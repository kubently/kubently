"""Incident history: past diagnoses as searchable institutional memory.

When an investigation concludes with a root cause, a compact summary record
(timestamp, cluster, resources involved, symptom keywords, root-cause
one-liner, resolution when stated) is persisted to Redis. Later
investigations can ask "have we seen this before?" — via the
search_past_incidents agent tool, or via the auto-surface note injected when
a new investigation strongly matches a past record.

This is retrieval over stored summaries, deliberately NOT a learning system:
records are plain data the agent reads and must verify against fresh
evidence, never weights or behavioral adjustments.

Records are namespaced per authenticated caller with the same derivation the
conversation checkpointer uses for thread ids, and every Redis key embeds the
namespace: in multi-tenant deployments one tenant's incidents are never
visible to another. That isolation is a security boundary, not a convenience.

Black box interface: IncidentStore is the storage/retrieval entry point;
extract_incident builds records from a concluded investigation. The v1 store
scores with keyword/resource/cluster overlap — an embedding-backed store can
replace it behind the same search()/best_match() interface without touching
the agent.
"""

from .records import (
    IncidentRecord,
    caller_namespace,
    extract_incident,
    incidents_enabled,
)
from .store import (
    IncidentStore,
    build_surface_note,
    format_search_results,
    score_incident,
)

__all__ = [
    "IncidentRecord",
    "IncidentStore",
    "build_surface_note",
    "caller_namespace",
    "extract_incident",
    "format_search_results",
    "incidents_enabled",
    "score_incident",
]
