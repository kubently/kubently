#!/usr/bin/env python3
"""Incident history (Track P6c): extraction, persistence, isolation, retrieval.

The store is retrieval over compact summaries of concluded investigations —
explicitly NOT a learning system. These tests pin the properties that matter:

- extraction only fires when the answer states a root cause (that IS the
  "investigation concluded with an RCA" detector);
- records live in per-caller Redis namespaces derived exactly like the
  checkpointer's thread namespacing — one tenant's incidents must NEVER be
  visible to another (security boundary, not a convenience);
- TTL bounds record lifetime and the per-namespace cap evicts oldest-first;
- keyword scoring ranks by resource/cluster/symptom relevance;
- auto-surface only triggers on a strong match, never on vague similarity.
"""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

fakeredis = pytest.importorskip("fakeredis")

sys.path.insert(0, str(Path(__file__).parent.parent))

from kubently.modules.auth.context import current_api_key  # noqa: E402
from kubently.modules.incidents import (  # noqa: E402
    IncidentRecord,
    IncidentStore,
    build_surface_note,
    caller_namespace,
    extract_incident,
    format_search_results,
    incidents_enabled,
    score_incident,
)
from kubently.modules.incidents.store import (  # noqa: E402
    DEFAULT_SURFACE_MIN_SCORE,
    surface_min_score,
)

RCA_ANSWER = """📊 Summary:
- Root Cause: The payment-api deployment's JVM heap flag (-Xmx4g) exceeds the container memory limit (2Gi), so pods are OOMKilled on startup.
- Evidence: container status shows OOMKilled, restart count 14.

🔧 Fix:
Set -Xmx to 1536m or raise the memory limit.

✅ Verification:
kubectl get pods -n payments
"""

TOOL_TRACE = [
    {
        "tool_name": "execute_kubectl",
        "args": {
            "cluster_id": "prod-east",
            "command": "describe pod payment-api-7f9d8-x2v",
            "namespace": "payments",
            "parsed": {
                "verb": "describe",
                "resource": "pod",
                "name": "payment-api-7f9d8-x2v",
                "namespace": "payments",
            },
        },
    },
    {
        "tool_name": "search_pod_logs",
        "args": {
            "cluster_id": "prod-east",
            "namespace": "payments",
            "selector": "app=payment-api",
            "query": "OutOfMemoryError",
        },
    },
]


def make_record(**overrides) -> IncidentRecord:
    base = dict(
        id="abc123",
        timestamp=datetime.now(UTC).isoformat(),
        root_cause="JVM heap exceeds container memory limit; pods OOMKilled",
        cluster_id="prod-east",
        resources=("payments/payment-api-7f9d8-x2v", "payments"),
        symptoms=("crashloopbackoff", "oomkilled"),
        resolution="Lower -Xmx below the memory limit",
        thread_id="thread-1",
        query="payment-api crashlooping in payments",
    )
    base.update(overrides)
    return IncidentRecord(**base)


@pytest.fixture
def redis():
    return fakeredis.FakeAsyncRedis(decode_responses=True)


@pytest.fixture
def store(redis):
    return IncidentStore(redis, ttl_seconds=90 * 24 * 3600, max_per_namespace=200)


# -- extraction ---------------------------------------------------------------


class TestExtraction:
    def test_full_extraction_from_rca_answer(self):
        rec = extract_incident(
            RCA_ANSWER,
            user_text="payment-api pods in payments are in CrashLoopBackOff",
            tool_calls=TOOL_TRACE,
            thread_id="t1",
        )
        assert rec is not None
        assert rec.root_cause.startswith("The payment-api deployment's JVM heap flag")
        assert rec.cluster_id == "prod-east"  # inferred from the tool trace
        assert "payments/payment-api-7f9d8-x2v" in rec.resources
        assert "payments" in rec.resources
        assert "crashloopbackoff" in rec.symptoms
        assert "oomkilled" in rec.symptoms
        assert rec.resolution.startswith("Set -Xmx to 1536m")
        assert rec.thread_id == "t1"

    def test_no_root_cause_means_no_record(self):
        """The root-cause line is the 'RCA produced' detector: answers without
        one (clarifying questions, healthy-cluster reports) record nothing."""
        for answer in (
            "Everything looks healthy — all pods are Running.",
            "Which cluster would you like me to look at?",
            "",
            None,
        ):
            assert extract_incident(answer, user_text="check payments") is None

    def test_explicit_cluster_context_wins_over_trace(self):
        rec = extract_incident(RCA_ANSWER, tool_calls=TOOL_TRACE, cluster_id="staging-eu")
        assert rec.cluster_id == "staging-eu"

    def test_deterministic_id_dedupes_restated_diagnosis(self):
        """Same thread restating the same root cause updates one record
        rather than creating near-duplicates."""
        a = extract_incident(RCA_ANSWER, thread_id="t1")
        b = extract_incident(RCA_ANSWER, thread_id="t1")
        c = extract_incident(RCA_ANSWER, thread_id="t2")
        assert a.id == b.id
        assert a.id != c.id

    def test_content_block_list_answers_are_handled(self):
        """Anthropic answers can arrive as content-block lists, not strings."""
        blocks = [{"type": "text", "text": RCA_ANSWER}]
        rec = extract_incident(blocks)
        assert rec is not None and "JVM heap" in rec.root_cause

    def test_long_root_cause_is_bounded(self):
        rec = extract_incident("Root Cause: " + "x" * 2000)
        assert rec is not None
        assert len(rec.root_cause) <= 300

    def test_json_round_trip(self):
        rec = make_record()
        again = IncidentRecord.from_json(rec.to_json())
        assert again == rec

    def test_kill_switch_env(self, monkeypatch):
        monkeypatch.delenv("KUBENTLY_INCIDENT_HISTORY", raising=False)
        assert incidents_enabled() is True  # default ON
        for off in ("false", "0", "off", "disabled", "FALSE"):
            monkeypatch.setenv("KUBENTLY_INCIDENT_HISTORY", off)
            assert incidents_enabled() is False
        monkeypatch.setenv("KUBENTLY_INCIDENT_HISTORY", "true")
        assert incidents_enabled() is True


# -- namespace derivation -----------------------------------------------------


class TestCallerNamespace:
    def test_matches_checkpointer_thread_derivation(self):
        """Incident namespaces MUST use the same caller derivation as the
        checkpointer's thread namespacing — same key, same prefix, so the
        isolation boundaries line up exactly."""
        import hashlib

        token = current_api_key.set("tenant-a-key")
        try:
            assert caller_namespace() == hashlib.sha256(b"tenant-a-key").hexdigest()[:16]
        finally:
            current_api_key.reset(token)

    def test_different_callers_different_namespaces(self):
        token = current_api_key.set("tenant-a-key")
        a = caller_namespace()
        current_api_key.reset(token)
        token = current_api_key.set("tenant-b-key")
        b = caller_namespace()
        current_api_key.reset(token)
        assert a != b

    def test_key_not_leaked(self):
        token = current_api_key.set("super-secret-key")
        ns = caller_namespace()
        current_api_key.reset(token)
        assert "super-secret-key" not in ns

    def test_unauthenticated_gets_local(self):
        assert current_api_key.get() is None
        assert caller_namespace() == "local"


# -- persistence & isolation --------------------------------------------------


class TestPersistence:
    async def test_record_and_search_round_trip(self, store):
        rec = make_record()
        await store.record("ns-a", rec)
        results = await store.search("ns-a", "payment-api oomkilled")
        assert len(results) == 1
        score, found = results[0]
        assert found == rec
        assert score > 0

    async def test_namespace_isolation_is_absolute(self, store):
        """CRITICAL: tenant A's incidents must never be visible to tenant B —
        not via search, not via empty-query listing, not via count."""
        secret = make_record(root_cause="tenant-a secret database credentials leaked")
        await store.record("tenant-a", secret)

        assert await store.search("tenant-b", "database credentials leaked") == []
        assert await store.search("tenant-b", "") == []
        assert await store.count("tenant-b") == 0
        assert await store.best_match("tenant-b", "database credentials leaked") is None
        # And the record IS there for its owner.
        assert await store.count("tenant-a") == 1
        assert len(await store.search("tenant-a", "database credentials")) == 1

    async def test_all_keys_embed_the_namespace(self, store, redis):
        """Defense in depth: every Redis key carries the namespace, so there
        is no shared key any cross-tenant read could go through."""
        await store.record("tenant-a", make_record())
        keys = [k async for k in redis.scan_iter("*")]
        assert keys, "expected keys to be written"
        assert all(":tenant-a" in k or ":tenant-a:" in k for k in keys), keys

    async def test_record_ttl_applied(self, redis):
        store = IncidentStore(redis, ttl_seconds=3600, max_per_namespace=10)
        rec = make_record()
        await store.record("ns", rec)
        ttl = await redis.ttl(f"kubently:incidents:rec:ns:{rec.id}")
        assert 0 < ttl <= 3600
        idx_ttl = await redis.ttl("kubently:incidents:idx:ns")
        assert 0 < idx_ttl <= 3600

    async def test_zero_ttl_disables_expiry(self, redis):
        store = IncidentStore(redis, ttl_seconds=0, max_per_namespace=10)
        rec = make_record()
        await store.record("ns", rec)
        assert await redis.ttl(f"kubently:incidents:rec:ns:{rec.id}") == -1

    async def test_ttl_env_default(self, monkeypatch, redis):
        monkeypatch.delenv("KUBENTLY_INCIDENT_TTL_SECONDS", raising=False)
        store = IncidentStore(redis)
        assert store._ttl == 90 * 24 * 3600
        monkeypatch.setenv("KUBENTLY_INCIDENT_TTL_SECONDS", "3600")
        assert IncidentStore(redis)._ttl == 3600
        monkeypatch.setenv("KUBENTLY_INCIDENT_TTL_SECONDS", "0")
        assert IncidentStore(redis)._ttl is None

    async def test_expired_record_pruned_from_index_lazily(self, store, redis):
        """A rec key that TTL-expired while its index entry survived must be
        skipped and cleaned up, not returned as garbage."""
        rec = make_record()
        await store.record("ns", rec)
        await redis.delete(f"kubently:incidents:rec:ns:{rec.id}")  # simulate expiry
        assert await store.search("ns", "payment-api") == []
        assert await redis.zcard("kubently:incidents:idx:ns") == 0

    async def test_per_namespace_cap_evicts_oldest(self, redis):
        store = IncidentStore(redis, ttl_seconds=0, max_per_namespace=3)
        base = datetime.now(UTC)
        for i in range(5):
            await store.record(
                "ns",
                make_record(
                    id=f"rec-{i}",
                    timestamp=(base + timedelta(minutes=i)).isoformat(),
                    thread_id=f"t-{i}",
                ),
            )
        assert await store.count("ns") == 3
        remaining = {r.id for r in await store.load_recent("ns")}
        assert remaining == {"rec-2", "rec-3", "rec-4"}
        # Evicted records' keys are actually deleted, not just de-indexed.
        assert await redis.get("kubently:incidents:rec:ns:rec-0") is None

    async def test_cap_is_per_namespace(self, redis):
        store = IncidentStore(redis, ttl_seconds=0, max_per_namespace=2)
        for ns in ("a", "b"):
            for i in range(2):
                await store.record(ns, make_record(id=f"{ns}-{i}", thread_id=f"{ns}-t{i}"))
        assert await store.count("a") == 2
        assert await store.count("b") == 2

    async def test_rewrite_same_id_updates_not_duplicates(self, store):
        rec = make_record()
        await store.record("ns", rec)
        updated = make_record(resolution="a better fix")
        await store.record("ns", updated)
        assert await store.count("ns") == 1
        results = await store.search("ns", "payment-api")
        assert results[0][1].resolution == "a better fix"


# -- scoring & retrieval ------------------------------------------------------


class TestScoring:
    def test_resource_and_symptom_outrank_vague_text(self):
        rec = make_record()
        strong = score_incident(rec, "payment-api CrashLoopBackOff in payments", "prod-east")
        vague = score_incident(rec, "the container memory something")
        assert strong > vague

    def test_unrelated_query_scores_zero(self):
        rec = make_record()
        assert score_incident(rec, "ingress 404 on marketing site in eu-cluster") == 0

    def test_workload_name_matches_hashed_pod_name(self):
        """Query says 'payment-api'; the stored resource is the pod
        payment-api-7f9d8-x2v. Prefix matching must connect them."""
        rec = make_record()
        assert score_incident(rec, "payment-api failing again") > 0

    def test_cluster_match_contributes(self):
        rec = make_record()
        with_cluster = score_incident(rec, "oomkilled", "prod-east")
        without = score_incident(rec, "oomkilled", "other-cluster")
        assert with_cluster > without

    async def test_search_ranks_best_first(self, store):
        await store.record("ns", make_record())
        await store.record(
            "ns",
            make_record(
                id="dns-1",
                thread_id="t-dns",
                root_cause="CoreDNS ConfigMap typo broke cluster DNS",
                cluster_id="prod-east",
                resources=("kube-system/coredns",),
                symptoms=("dns", "timeout"),
                resolution=None,
            ),
        )
        results = await store.search("ns", "coredns dns lookups timing out")
        assert results[0][1].id == "dns-1"

    async def test_empty_query_lists_newest_first(self, store):
        base = datetime.now(UTC)
        for i in range(3):
            await store.record(
                "ns",
                make_record(
                    id=f"r{i}",
                    thread_id=f"t{i}",
                    timestamp=(base + timedelta(minutes=i)).isoformat(),
                ),
            )
        results = await store.search("ns", "")
        assert [r.id for _, r in results] == ["r2", "r1", "r0"]

    async def test_search_limit(self, store):
        for i in range(10):
            await store.record("ns", make_record(id=f"r{i}", thread_id=f"t{i}"))
        assert len(await store.search("ns", "payment-api", limit=3)) == 3


# -- auto-surface -------------------------------------------------------------


class TestAutoSurface:
    async def test_strong_match_surfaces(self, store):
        await store.record("ns", make_record())
        match = await store.best_match(
            "ns", "payment-api pods CrashLoopBackOff in payments", cluster_id="prod-east"
        )
        assert match is not None
        score, rec = match
        assert score >= DEFAULT_SURFACE_MIN_SCORE
        assert rec.id == "abc123"

    async def test_weak_match_stays_silent(self, store):
        """Vague topical similarity (a couple of shared generic words) must
        not inject notes into every investigation."""
        await store.record("ns", make_record())
        assert await store.best_match("ns", "why is the memory usage graph flat") is None

    async def test_own_thread_excluded(self, store):
        """Turn 2 of a conversation must not surface turn 1's own diagnosis
        back at itself."""
        await store.record("ns", make_record(thread_id="thread-1"))
        match = await store.best_match(
            "ns",
            "payment-api pods CrashLoopBackOff in payments",
            cluster_id="prod-east",
            exclude_thread_id="thread-1",
        )
        assert match is None

    async def test_exclude_ids_skips_already_surfaced(self, store):
        await store.record("ns", make_record())
        match = await store.best_match(
            "ns",
            "payment-api pods CrashLoopBackOff in payments",
            cluster_id="prod-east",
            exclude_ids={"abc123"},
        )
        assert match is None

    def test_threshold_env_override(self, monkeypatch):
        monkeypatch.delenv("KUBENTLY_INCIDENT_SURFACE_MIN_SCORE", raising=False)
        assert surface_min_score() == DEFAULT_SURFACE_MIN_SCORE
        monkeypatch.setenv("KUBENTLY_INCIDENT_SURFACE_MIN_SCORE", "75")
        assert surface_min_score() == 75

    def test_surface_note_frames_verification_and_citation(self):
        note = build_surface_note(make_record())
        assert "SIMILAR PAST INCIDENT" in note
        assert make_record().date() in note
        assert "verify" in note.lower()
        assert "cite" in note.lower()
        # The one-liner, not a transcript dump.
        assert len(note) < 800


# -- presentation -------------------------------------------------------------


class TestFormatting:
    def test_results_include_citation_guidance(self):
        text = format_search_results([(65, make_record())])
        assert "root cause" in text.lower()
        assert "cite" in text.lower()
        assert "prod-east" in text
        assert "Lower -Xmx" in text

    def test_empty_results_are_explicit(self):
        text = format_search_results([])
        assert "No matching past incidents" in text
