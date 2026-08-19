"""
Primitives shared by all cloud providers: identity, results, and result caps.

Result capping lives here so every provider truncates identically and every
truncated payload carries an explicit note — the agent must never mistake a
capped result for the complete picture.
"""

import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Any

# Hard caps applied to every operation result, regardless of what the caller
# asked for. Providers also clamp per-call limits (rows/events/datapoints)
# before hitting the cloud API.
MAX_LOG_EVENTS = 100
MAX_QUERY_ROWS = 100
MAX_METRIC_DATAPOINTS = 500
MAX_CHANGE_EVENTS = 50
MAX_RESULT_CHARS = 40_000  # serialized payload cap


@dataclass
class CloudIdentity:
    """The cloud identity the executor pod currently holds."""

    provider: str  # "aws" or "gcp"
    account: str | None = None  # AWS account id / GCP project id
    principal: str | None = None  # AWS ARN / GCP service-account email
    region: str | None = None  # AWS region (GCP: unset)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class CloudOperationResult:
    """Standard result envelope for every cloud operation."""

    success: bool
    operation: str
    provider: str
    data: dict[str, Any] | None = None
    error: str | None = None
    error_code: str | None = None
    truncated: bool = False
    truncation_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        # Drop empty optional fields to keep result payloads small
        return {k: v for k, v in out.items() if v not in (None, False)} | {
            "success": self.success
        }


def cap_list(items: list, limit: int, what: str) -> tuple[list, str | None]:
    """Truncate a list to `limit`, returning a human-readable note if cut."""
    if len(items) <= limit:
        return items, None
    return (
        items[:limit],
        f"Result truncated: showing first {limit} of {len(items)} {what}. "
        f"Narrow the time range or filter to see the rest.",
    )


def cap_payload(result: CloudOperationResult) -> CloudOperationResult:
    """
    Enforce the serialized-size cap on a result's data payload.

    If the JSON-serialized data exceeds MAX_RESULT_CHARS, the payload is
    replaced with a truncated string representation plus an explicit note.
    """
    if result.data is None:
        return result

    serialized = json.dumps(result.data, default=str)
    if len(serialized) <= MAX_RESULT_CHARS:
        return result

    result.data = {
        "raw_truncated": serialized[:MAX_RESULT_CHARS],
    }
    result.truncated = True
    note = (
        f"Result exceeded the {MAX_RESULT_CHARS}-character cap and was cut mid-"
        f"payload. Use a narrower query (shorter time range, tighter filter, "
        f"fewer fields) to get complete data."
    )
    result.truncation_note = (
        f"{result.truncation_note} {note}" if result.truncation_note else note
    )
    return result


class CloudProvider(ABC):
    """
    Black box interface every cloud provider implements.

    Implementations own SDK clients and auth (ambient pod identity only —
    never explicit credentials). They are individually replaceable.
    """

    name: str  # "aws" or "gcp"

    @abstractmethod
    def detect_identity(self) -> CloudIdentity | None:
        """
        Return the identity the pod holds, or None when the provider's
        identity plumbing is absent (no IRSA/Pod Identity, no metadata server).
        Must be cheap and must never raise.
        """

    @abstractmethod
    def probe_permissions(self) -> dict[str, bool]:
        """
        Probe which operation families are usable with the held identity,
        via cheap read-only calls. Returns {family: usable}.
        """

    @abstractmethod
    def execute(self, operation: str, params: dict[str, Any]) -> CloudOperationResult:
        """Execute one whitelisted operation. Never raises; errors go in the result."""
