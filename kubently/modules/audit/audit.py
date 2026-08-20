"""
Audit module.

The audit trail lives in a single Redis list, `auth:audit`, which
`AuthModule._log_event` has always written authentication events to. This
module adds the one event type an operator actually asks for -- "what command
did the agent run, where, and did it work" -- and provides the only read path
over the list.

Two properties are load-bearing and are covered by tests/test_audit.py:

1. **Read-only.** `query()` issues exactly one `LRANGE`. Nothing in the read
   path writes, deletes, trims or re-expires the key. The HTTP surface built
   on it is a single GET.

2. **Identity-scoped.** Every entry carries the `service_identity` that caused
   it, and `query()` returns only the entries belonging to the caller's own
   identity. An entry with no identity on it is *dropped*, never shared: the
   filter fails closed, so a new event type that forgets to stamp an identity
   becomes invisible rather than becoming everybody's.

   Identity is the strongest scope Kubently can currently enforce. There is no
   tenant -> cluster ownership model anywhere in the codebase (every API key
   may target every registered cluster), so "your clusters" is not yet a
   question the data can answer; "the commands you ran" is. See docs/AUDIT.md.

Command *output* is deliberately not recorded. The audit trail answers what
was run, against what, by whom, and whether it succeeded -- reproducing
kubectl output into a 10,000-entry list that every holder of the issuing API
key can read would widen the blast radius of the audit log well past its
purpose.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# The list AuthModule has always written to. Shared deliberately: one audit
# trail, one retention rule, one thing to back up.
AUDIT_KEY = "auth:audit"

# Mirrors the `ltrim(AUDIT_KEY, 0, 9999)` AuthModule applies on every write.
# The list is capped by count, not by time, and carries no TTL -- see the
# retention section of docs/AUDIT.md.
AUDIT_MAX_ENTRIES = 10000

# Errors are kept because "it ran and was denied" is the outcome operators
# care about, but they are truncated: an error body is the one field an
# executor can echo unbounded attacker-influenced text into.
MAX_ERROR_CHARS = 200

COMMAND_EVENT = "command_executed"


def _decode(value: Any) -> str | None:
    """Redis clients here are configured either way; tolerate both."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value
    return None


def _parse_timestamp(value: Any) -> datetime | None:
    text = _decode(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    # Entries written before the timezone-aware `datetime.now(UTC)` switch are
    # naive; treat them as UTC rather than crashing the comparison below.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _identity_of(event: dict) -> str | None:
    """The identity an entry belongs to, or None if it is unattributed."""
    data = event.get("data")
    if not isinstance(data, dict):
        return None
    identity = data.get("service_identity")
    if isinstance(identity, str) and identity:
        return identity
    return None


class AuditModule:
    """Records command events and reads the audit trail back, scoped."""

    def __init__(self, redis_client):
        self.redis = redis_client

    async def record_command(
        self,
        *,
        service_identity: str | None,
        cluster_id: str,
        command_id: str,
        args: list[str] | None = None,
        session_id: str | None = None,
        outcome: str = "unknown",
        error: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        """
        Record one executed command.

        Never raises: an audit write must not be able to fail a command that
        already ran. A dropped entry is logged loudly instead.
        """
        now = datetime.now(UTC).isoformat()
        event = {
            "type": COMMAND_EVENT,
            "data": {
                "service_identity": service_identity,
                "cluster_id": cluster_id,
                "session_id": session_id,
                "command_id": command_id,
                # Joined for display; the args themselves are the audit record.
                "command": " ".join(args or []),
                "outcome": outcome,
                "error": (error or "")[:MAX_ERROR_CHARS] or None,
                "timestamp": now,
            },
            "correlation_id": correlation_id,
            "timestamp": now,
        }

        try:
            await self.redis.lpush(AUDIT_KEY, json.dumps(event))
            await self.redis.ltrim(AUDIT_KEY, 0, AUDIT_MAX_ENTRIES - 1)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to record audit entry for command %s: %s", command_id, exc)

    async def query(
        self,
        *,
        identity: str | None,
        event_type: str | None = None,
        cluster_id: str | None = None,
        session_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """
        Read audit entries belonging to `identity`, newest first.

        Read-only: one LRANGE, no writes. Returns [] for an absent identity
        rather than falling back to "everything" -- the failure mode of a
        scoped read must be no data, not all data.
        """
        if not identity:
            return []

        raw_entries = await self.redis.lrange(AUDIT_KEY, 0, AUDIT_MAX_ENTRIES - 1)

        results: list[dict] = []
        for raw in raw_entries:
            text = _decode(raw)
            if not text:
                continue
            try:
                event = json.loads(text)
            except (ValueError, TypeError):
                # AuthModule's docs once showed `str(event)` rather than
                # json.dumps; skip anything unparseable instead of failing the
                # whole read.
                continue
            if not isinstance(event, dict):
                continue

            # === SCOPE GATE ===
            # The only line that decides who sees what. Everything below is
            # display filtering and must never be able to widen this.
            if _identity_of(event) != identity:
                continue
            # === END SCOPE GATE ===

            data = event.get("data") or {}
            if event_type and event.get("type") != event_type:
                continue
            if cluster_id and data.get("cluster_id") != cluster_id:
                continue
            if session_id and data.get("session_id") != session_id:
                continue

            timestamp = _parse_timestamp(event.get("timestamp")) or _parse_timestamp(
                data.get("timestamp")
            )
            if since and (timestamp is None or timestamp < since):
                continue
            if until and (timestamp is None or timestamp > until):
                continue

            results.append(
                {
                    "timestamp": _decode(event.get("timestamp")),
                    "type": _decode(event.get("type")) or "unknown",
                    "service_identity": _identity_of(event),
                    "cluster_id": data.get("cluster_id"),
                    "session_id": data.get("session_id"),
                    "command_id": data.get("command_id"),
                    "command": data.get("command"),
                    "outcome": data.get("outcome"),
                    "error": data.get("error"),
                    "correlation_id": _decode(event.get("correlation_id")),
                }
            )

            if len(results) >= limit:
                break

        return results
