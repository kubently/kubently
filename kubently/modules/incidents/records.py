"""Incident record shape, extraction from concluded investigations, and the
per-caller namespace derivation shared with the conversation checkpointer."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

ENABLED_ENV = "KUBENTLY_INCIDENT_HISTORY"

# Namespace used when there is no authenticated caller (direct/local
# invocation). Matches the checkpointer's behaviour of falling back to the
# raw, un-prefixed thread id in single-tenant deployments.
LOCAL_NAMESPACE = "local"

# An RCA one-liner longer than this is a paragraph, not a summary.
MAX_FIELD_CHARS = 300
MAX_RESOURCES = 12
MAX_SYMPTOMS = 8

# Failure-mode vocabulary for symptom keywords. Substring-matched against the
# lowercased investigation text; ordering is stable so records are
# deterministic. Deliberately coarse — symptoms are a retrieval signal, not a
# diagnosis.
SYMPTOM_TERMS = (
    "crashloopbackoff",
    "oomkilled",
    "oom",
    "imagepullbackoff",
    "errimagepull",
    "createcontainerconfigerror",
    "failedscheduling",
    "unschedulable",
    "pending",
    "evicted",
    "not ready",
    "notready",
    "connection refused",
    "timed out",
    "timeout",
    "dns",
    "forbidden",
    "rbac",
    "unauthorized",
    "networkpolicy",
    "network policy",
    "no endpoints",
    "0 endpoints",
    "probe",
    "readiness",
    "liveness",
    "restart",
    "crash",
    "502",
    "503",
    "5xx",
    "quota",
    "disk pressure",
    "memory pressure",
    "pvc",
    "volume",
    "mount",
    "certificate",
    "tls",
    "selector",
    "port mismatch",
    "image pull",
    "backoff",
    "throttl",
    "misconfigur",
)

# Same token shape as the runbook matcher: keep the characters Kubernetes
# names use so resource names survive tokenization intact.
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")

# The agent's response template renders "- Root Cause: <one-liner>"; accept
# loose variants (markdown decoration, missing bullet, en-dash) because the
# model does not always follow the template exactly.
_ROOT_CAUSE_RE = re.compile(
    r"root\s*cause\s*[:\-–]\s*[*_`]*\s*(?P<cause>[^\n]+)", re.IGNORECASE
)

# "🔧 Fix:" heading (content on following lines) or inline "Fix:/Resolution:".
_RESOLUTION_RE = re.compile(
    r"(?:fix|resolution|remediation)\s*[:\-–]\s*[*_`]*\s*(?P<res>[^\n]*)",
    re.IGNORECASE,
)


def incidents_enabled() -> bool:
    """Feature kill-switch: default ON, KUBENTLY_INCIDENT_HISTORY=false disables."""
    raw = os.getenv(ENABLED_ENV, "true").strip().lower()
    return raw not in ("false", "0", "no", "off", "disabled")


def caller_namespace() -> str:
    """Namespace for the current caller's incident records.

    Uses the same derivation as the checkpointer's thread namespacing
    (_namespaced_thread_id in agent.py): a short hash of the authenticated
    caller's API key. Records written and read through this namespace can
    therefore never cross tenants — the same security boundary the
    conversation memory relies on. Unauthenticated/local invocation gets a
    fixed "local" namespace, preserving single-tenant behaviour.
    """
    try:
        from kubently.modules.auth.context import current_api_key

        key = current_api_key.get()
    except Exception:
        key = None
    if not key:
        return LOCAL_NAMESPACE
    return hashlib.sha256(key.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class IncidentRecord:
    """One concluded investigation, compressed to its retrievable essence."""

    id: str
    timestamp: str  # ISO-8601 UTC
    root_cause: str
    cluster_id: str | None = None
    resources: tuple = ()
    symptoms: tuple = ()
    resolution: str | None = None
    thread_id: str | None = None
    query: str | None = None  # the user question that started the investigation

    def to_json(self) -> str:
        data = asdict(self)
        data["resources"] = list(self.resources)
        data["symptoms"] = list(self.symptoms)
        return json.dumps(data, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> IncidentRecord:
        data = json.loads(raw)
        data["resources"] = tuple(data.get("resources") or ())
        data["symptoms"] = tuple(data.get("symptoms") or ())
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def date(self) -> str:
        """Human date (YYYY-MM-DD) for citations."""
        return self.timestamp[:10]


def _clean_line(text: str) -> str:
    """Strip markdown decoration and bound the length of an extracted field."""
    cleaned = text.strip().strip("*_`").strip()
    # Drop a trailing markdown emphasis remnant like "**" mid-strip left over.
    cleaned = re.sub(r"\s+", " ", cleaned)
    if len(cleaned) > MAX_FIELD_CHARS:
        cleaned = cleaned[: MAX_FIELD_CHARS - 1].rstrip() + "…"
    return cleaned


def extract_root_cause(text: str) -> str | None:
    """The root-cause one-liner from a final answer, or None when the answer
    contains no explicit root-cause statement (= no RCA, nothing to record)."""
    if not text:
        return None
    m = _ROOT_CAUSE_RE.search(text)
    if not m:
        return None
    cause = _clean_line(m.group("cause"))
    return cause or None


def extract_resolution(text: str) -> str | None:
    """First line of the fix/resolution section, when one is stated."""
    if not text:
        return None
    for m in _RESOLUTION_RE.finditer(text):
        inline = _clean_line(m.group("res"))
        if inline:
            return inline
        # Heading form ("🔧 Fix:\n1. do the thing"): take the next non-empty line.
        rest = text[m.end() :]
        for line in rest.splitlines():
            cleaned = _clean_line(line)
            if cleaned:
                return cleaned
    return None


def extract_symptoms(*texts: str) -> tuple:
    """Symptom keywords present in the given texts, vocabulary order."""
    lowered = " ".join(t.lower() for t in texts if t)
    found = []
    for term in SYMPTOM_TERMS:
        if term in lowered and term not in found:
            found.append(term)
        if len(found) >= MAX_SYMPTOMS:
            break
    return tuple(found)


def _flatten_text(content) -> str:
    """Coerce an LLM message content (str or content-block list) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "\n".join(parts)
    return str(content or "")


def extract_resources(tool_calls) -> tuple:
    """Resources touched during the investigation, from the tool-call trace.

    Best-effort: pulls namespaces and resource/pod/workload names out of the
    recorded tool-call args (the interceptor trace every tool must feed).
    Produces "namespace/name" strings where both are known, bare names
    otherwise.
    """
    resources: list = []

    def add(item: str | None) -> None:
        if not item:
            return
        item = str(item).strip()
        if not item or item in ("default", "all") or item in resources:
            return
        if len(resources) < MAX_RESOURCES:
            resources.append(item)

    for call in tool_calls or ():
        args = call.get("args") or {}
        ns = args.get("namespace")
        if ns in ("default", "all"):
            ns = None
        for key in ("resource_name", "pod_name"):
            name = args.get(key)
            if name:
                add(f"{ns}/{name}" if ns else str(name))
        parsed = args.get("parsed") or {}
        pns = parsed.get("namespace")
        if pns in ("default", "all"):
            pns = None
        name = parsed.get("name")
        if name:
            add(f"{pns or ns}/{name}" if (pns or ns) else str(name))
        selector = args.get("selector")
        if selector:
            add(f"{ns}/{selector}" if ns else str(selector))
        if ns:
            add(str(ns))
    return tuple(resources)


def extract_cluster(tool_calls, cluster_id: str | None = None) -> str | None:
    """The investigation's cluster: explicit context wins, else the cluster
    most tool calls targeted."""
    if cluster_id:
        return cluster_id
    counts: dict = {}
    for call in tool_calls or ():
        args = call.get("args") or {}
        cid = args.get("cluster_id")
        if cid:
            counts[str(cid)] = counts.get(str(cid), 0) + 1
    if not counts:
        return None
    return max(counts, key=counts.get)


def extract_incident(
    final_text,
    *,
    user_text: str = "",
    tool_calls=(),
    cluster_id: str | None = None,
    thread_id: str | None = None,
    now: datetime | None = None,
) -> IncidentRecord | None:
    """Build an incident record from a concluded investigation.

    Returns None when the final answer states no root cause — that is the
    "did this investigation conclude with an RCA?" detector: chit-chat,
    partial answers and clarifying questions produce no record.

    The record id is deterministic over (thread, root cause), so a multi-turn
    conversation that restates the same diagnosis updates one record instead
    of piling up near-duplicates.
    """
    text = _flatten_text(final_text)
    root_cause = extract_root_cause(text)
    if not root_cause:
        return None

    ts = (now or datetime.now(UTC)).isoformat()
    incident_id = hashlib.sha256(
        f"{thread_id or ''}|{root_cause.lower()}".encode()
    ).hexdigest()[:16]

    query = _clean_line(user_text) if user_text else None
    return IncidentRecord(
        id=incident_id,
        timestamp=ts,
        root_cause=root_cause,
        cluster_id=extract_cluster(tool_calls, cluster_id),
        resources=extract_resources(tool_calls),
        symptoms=extract_symptoms(user_text, text),
        resolution=extract_resolution(text),
        thread_id=thread_id,
        query=query,
    )


def tokens(text: str) -> set:
    """Tokenize search text the way the runbook matcher does (Kubernetes
    names keep their hyphens/dots)."""
    return set(_TOKEN_RE.findall((text or "").lower()))
