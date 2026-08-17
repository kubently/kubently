"""Redis-backed incident record store with keyword retrieval.

Key layout (prefix "kubently:incidents"; {ns} is the caller namespace from
records.caller_namespace — the SAME per-caller derivation the conversation
checkpointer uses for thread ids, so record isolation follows the identical
security boundary):

- {p}:rec:{ns}:{id}   string: JSON-serialized IncidentRecord, TTL-bound
- {p}:idx:{ns}        zset: incident ids scored by record epoch time

Every key embeds the namespace and every operation takes an explicit
namespace, so no read or write can ever range across namespaces. Only core
Redis commands are used (SET/ZADD/ZRANGE/MGET) — no RediSearch, no SCAN —
matching the plain-redis checkpointer's managed-Redis discipline.

Retrieval is v1 keyword scoring: cluster match > resource-name overlap >
symptom overlap > root-cause token overlap, newest-first tiebreak. The
search()/best_match() interface is the seam where an embedding-backed store
would slot in later (same inputs: namespace + free text + optional cluster;
same outputs: scored records) — swap the class, keep the agent untouched.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

from .records import IncidentRecord, tokens

logger = logging.getLogger(__name__)

TTL_ENV = "KUBENTLY_INCIDENT_TTL_SECONDS"
CAP_ENV = "KUBENTLY_INCIDENT_MAX_PER_NAMESPACE"
SURFACE_MIN_SCORE_ENV = "KUBENTLY_INCIDENT_SURFACE_MIN_SCORE"

# ~90 days: long enough for "have we seen this before?" to span quarters,
# short enough that stale cluster state ages out. 0 disables expiry.
DEFAULT_TTL_SECONDS = 90 * 24 * 3600
DEFAULT_MAX_PER_NAMESPACE = 200

# Score weights. A resource-name hit is the strongest single retrieval signal
# (specific object recurring), then the cluster, then coarse symptom words.
RESOURCE_WEIGHT = 25
CLUSTER_WEIGHT = 20
SYMPTOM_WEIGHT = 15
TEXT_WEIGHT = 5
# Per-signal contribution caps so one dimension can't drown the others.
MAX_RESOURCE_HITS = 2
MAX_SYMPTOM_HITS = 3
MAX_TEXT_HITS = 5

# Auto-surface only on a strong match: roughly "a specific resource plus a
# matching symptom" (25+15) or "same cluster plus symptoms". Below this the
# note would be noise injected into every vaguely similar investigation.
DEFAULT_SURFACE_MIN_SCORE = 40

# Newest records considered per search. Bounds MGET fan-out; with the
# per-namespace cap at its default this covers the whole namespace.
SEARCH_SCAN_LIMIT = 500

# Generic words that would otherwise inflate root-cause text overlap.
_STOPWORDS = frozenset(
    "the a an is are was were to of in on for with and or not no by at it its "
    "this that pod pods container containers due caused causing because from "
    "has have had be been being as".split()
)


def _s(value: Any) -> str:
    """Normalize a Redis reply to str (client may or may not decode responses)."""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


def _resolve_ttl() -> int | None:
    raw = os.getenv(TTL_ENV, "").strip()
    if not raw:
        return DEFAULT_TTL_SECONDS
    ttl = int(raw)
    return ttl if ttl > 0 else None


def _resolve_cap() -> int:
    raw = os.getenv(CAP_ENV, "").strip()
    if not raw:
        return DEFAULT_MAX_PER_NAMESPACE
    cap = int(raw)
    return cap if cap > 0 else DEFAULT_MAX_PER_NAMESPACE


def surface_min_score() -> int:
    raw = os.getenv(SURFACE_MIN_SCORE_ENV, "").strip()
    if not raw:
        return DEFAULT_SURFACE_MIN_SCORE
    return int(raw)


def score_incident(
    record: IncidentRecord,
    query_text: str,
    cluster_id: str | None = None,
) -> int:
    """Relevance of one past incident to a new investigation. 0 = unrelated."""
    query_tokens = tokens(query_text)
    lowered = (query_text or "").lower()
    score = 0

    # Cluster: explicit context match, or the cluster named in the query text.
    if record.cluster_id:
        rc = record.cluster_id.lower()
        if (cluster_id and rc == cluster_id.lower()) or rc in query_tokens:
            score += CLUSTER_WEIGHT

    # Resources: split "namespace/name" so either half can match. Prefix
    # matching (min 4 chars) lets a workload name in the query match stored
    # pod names that carry ReplicaSet/pod hash suffixes, and vice versa.
    resource_tokens = set()
    for res in record.resources:
        for part in str(res).lower().split("/"):
            if part:
                resource_tokens.add(part)
    resource_hits = 0
    for rt in resource_tokens:
        if rt in query_tokens or any(
            len(qt) >= 4 and (rt.startswith(qt) or qt.startswith(rt))
            for qt in query_tokens
        ):
            resource_hits += 1
    score += RESOURCE_WEIGHT * min(resource_hits, MAX_RESOURCE_HITS)

    # Symptoms: substring match, same as extraction.
    symptom_hits = sum(1 for s in record.symptoms if s in lowered)
    score += SYMPTOM_WEIGHT * min(symptom_hits, MAX_SYMPTOM_HITS)

    # Root cause + original question: token overlap minus stopwords.
    cause_tokens = (tokens(record.root_cause) | tokens(record.query or "")) - _STOPWORDS
    score += TEXT_WEIGHT * min(
        len(cause_tokens & (query_tokens - _STOPWORDS)), MAX_TEXT_HITS
    )
    return score


class IncidentStore:
    """Per-namespace incident persistence and retrieval on core Redis."""

    def __init__(
        self,
        redis_client: Any,
        *,
        prefix: str = "kubently:incidents",
        ttl_seconds: int | None = None,
        max_per_namespace: int | None = None,
    ) -> None:
        self._redis = redis_client
        self._prefix = prefix
        self._ttl = ttl_seconds if ttl_seconds is not None else _resolve_ttl()
        if self._ttl is not None and self._ttl <= 0:
            self._ttl = None
        self._cap = max_per_namespace if max_per_namespace else _resolve_cap()

    # -- keys ----------------------------------------------------------------

    def _rec_key(self, namespace: str, incident_id: str) -> str:
        return f"{self._prefix}:rec:{namespace}:{incident_id}"

    def _idx_key(self, namespace: str) -> str:
        return f"{self._prefix}:idx:{namespace}"

    # -- write ---------------------------------------------------------------

    async def record(self, namespace: str, record: IncidentRecord) -> None:
        """Persist one record; enforce the per-namespace cap (oldest evicted)."""
        try:
            epoch = datetime.fromisoformat(record.timestamp).timestamp()
        except ValueError:
            epoch = time.time()

        idx_key = self._idx_key(namespace)
        pipe = self._redis.pipeline(transaction=True)
        if self._ttl:
            pipe.set(self._rec_key(namespace, record.id), record.to_json(), ex=self._ttl)
            # The index outlives any single record by design: refresh it to
            # the newest record's TTL so it expires with its last record.
            pipe.zadd(idx_key, {record.id: epoch})
            pipe.expire(idx_key, self._ttl)
        else:
            pipe.set(self._rec_key(namespace, record.id), record.to_json())
            pipe.zadd(idx_key, {record.id: epoch})
        await pipe.execute()

        # Cap enforcement: evict oldest beyond the namespace budget.
        count = await self._redis.zcard(idx_key)
        excess = count - self._cap
        if excess > 0:
            oldest = [_s(i) for i in await self._redis.zrange(idx_key, 0, excess - 1)]
            if oldest:
                pipe = self._redis.pipeline(transaction=True)
                pipe.delete(*[self._rec_key(namespace, i) for i in oldest])
                pipe.zrem(idx_key, *oldest)
                await pipe.execute()

    # -- read ----------------------------------------------------------------

    async def load_recent(self, namespace: str, limit: int = SEARCH_SCAN_LIMIT) -> list:
        """Newest records in one namespace. Index entries whose record has
        TTL-expired are pruned lazily here."""
        idx_key = self._idx_key(namespace)
        ids = [_s(i) for i in await self._redis.zrevrange(idx_key, 0, limit - 1)]
        if not ids:
            return []
        raws = await self._redis.mget([self._rec_key(namespace, i) for i in ids])
        records: list = []
        expired: list = []
        for incident_id, raw in zip(ids, raws):
            if raw is None:
                expired.append(incident_id)
                continue
            try:
                records.append(IncidentRecord.from_json(_s(raw)))
            except Exception:
                expired.append(incident_id)
        if expired:
            await self._redis.zrem(idx_key, *expired)
        return records

    async def count(self, namespace: str) -> int:
        return await self._redis.zcard(self._idx_key(namespace))

    async def search(
        self,
        namespace: str,
        query: str = "",
        cluster_id: str | None = None,
        limit: int = 5,
        min_score: int = 1,
        exclude_thread_id: str | None = None,
    ) -> list:
        """Scored keyword search over one namespace's records.

        Returns [(score, IncidentRecord)] best-first (recency breaks ties).
        With no query text, returns the newest records (score 0) so "list
        recent incidents" works too.
        """
        records = await self.load_recent(namespace)
        if exclude_thread_id:
            records = [r for r in records if r.thread_id != exclude_thread_id]
        if not (query or "").strip() and not cluster_id:
            return [(0, r) for r in records[:limit]]
        scored = [(score_incident(r, query, cluster_id), r) for r in records]
        matched = [(s, r) for s, r in scored if s >= min_score]
        # load_recent is newest-first; stable sort keeps that order on ties.
        matched.sort(key=lambda sr: -sr[0])
        return matched[:limit]

    async def best_match(
        self,
        namespace: str,
        query: str,
        cluster_id: str | None = None,
        exclude_thread_id: str | None = None,
        exclude_ids=(),
    ):
        """The strongest past-incident match, or None below the surface
        threshold. This is the auto-surface gate: weak matches stay silent."""
        results = await self.search(
            namespace,
            query,
            cluster_id=cluster_id,
            limit=len(exclude_ids) + 1 if exclude_ids else 1,
            min_score=surface_min_score(),
            exclude_thread_id=exclude_thread_id,
        )
        for score, record in results:
            if record.id not in exclude_ids:
                return score, record
        return None


# -- presentation ------------------------------------------------------------

RESULTS_FRAMING = (
    "Past incidents are summaries of previous diagnoses in this deployment — "
    "context, not evidence. Verify against the current cluster state before "
    "relying on one; if a past incident materially informs your diagnosis, "
    "cite it in your root-cause summary (e.g. \"same root cause as the "
    "{date} incident\")."
)


def _format_record(score: int, record: IncidentRecord) -> str:
    lines = [f"- [{record.date()}] root cause: {record.root_cause}"]
    details = []
    if record.cluster_id:
        details.append(f"cluster: {record.cluster_id}")
    if record.resources:
        details.append(f"resources: {', '.join(record.resources[:6])}")
    if record.symptoms:
        details.append(f"symptoms: {', '.join(record.symptoms)}")
    if details:
        lines.append("  " + "; ".join(details))
    if record.resolution:
        lines.append(f"  resolution: {record.resolution}")
    return "\n".join(lines)


def format_search_results(scored: list) -> str:
    """Tool output for search_past_incidents."""
    if not scored:
        return (
            "No matching past incidents found in this deployment's incident "
            "history. This does not mean the problem is new — history only "
            "covers investigations that concluded here with a root cause."
        )
    parts = [f"Found {len(scored)} matching past incident(s), best match first:\n"]
    parts.extend(_format_record(s, r) for s, r in scored)
    parts.append("\n" + RESULTS_FRAMING)
    return "\n".join(parts)


def build_surface_note(record: IncidentRecord) -> str:
    """The one-line context note injected when a new investigation strongly
    matches a past incident."""
    resolution = f" Resolution then: {record.resolution}." if record.resolution else ""
    return (
        f"SIMILAR PAST INCIDENT ({record.date()}): {record.root_cause}.{resolution} "
        "This past diagnosis may or may not apply now — verify with fresh "
        "evidence rather than assuming. If it materially informs your "
        "diagnosis, cite it (e.g. \"same root cause as the "
        f"{record.date()} incident\"). Use the search_past_incidents tool "
        "for more history."
    )
