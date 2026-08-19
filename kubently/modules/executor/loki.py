"""Read-only Loki (LogQL) query runner for the Kubently executor.

Loki queries ride the same outbound channel as kubectl commands: API -> Redis
pub/sub -> executor SSE -> result POST. This module is the executor-side
terminus of that path, mirroring the Prometheus runner's security model:

- The base URL comes ONLY from local executor configuration (LOKI_URL). The
  control plane never supplies a URL, so a compromised API key cannot turn the
  executor into an SSRF proxy.
- Exactly one fixed, read-only HTTP API path is reachable:
  /loki/api/v1/query_range. GET only. Nothing else is constructible from a
  request.
- When LOKI_URL is unset the runner reports "unavailable" without making any
  network call.

Results are capped (line count via the clamped `limit` parameter, per-line
characters, total output characters) before they leave the executor, and every
truncation is announced in the output the model reads.

Deliberately import-light (stdlib + requests) to match sse_executor.py.
"""

import json
import logging
import os
from datetime import UTC, datetime

import requests

logger = logging.getLogger("kubently-loki")

QUERY_RANGE_PATH = "/loki/api/v1/query_range"

DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_LIMIT = 100
DEFAULT_MAX_LINES = 500
DEFAULT_MAX_LINE_CHARS = 500
DEFAULT_MAX_OUTPUT_CHARS = 20000

UNAVAILABLE_MESSAGE = (
    "Loki is not configured on this cluster's executor (LOKI_URL is unset). "
    "Aggregated log search is unavailable here; use search_pod_logs or kubectl "
    "logs instead."
)


def format_loki_timestamp(ns_timestamp: str) -> str:
    """Render Loki's nanosecond-epoch string as RFC3339 (falls back to raw)."""
    try:
        seconds = int(ns_timestamp) / 1_000_000_000
        return (
            datetime.fromtimestamp(seconds, tz=UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
    except (ValueError, TypeError, OSError, OverflowError):
        return str(ns_timestamp)


def format_streams(
    result: list,
    max_lines: int,
    max_line_chars: int,
) -> tuple[str, list]:
    """Flatten Loki streams into per-stream sections of timestamped lines.

    Lines within each stream keep Loki's returned order; caps are applied
    across all streams with a note when they fire. Returns (text, notes).
    """
    notes = []
    total_lines = sum(len(s.get("values") or []) for s in result)
    shown = 0
    sections = []
    for stream in result:
        if shown >= max_lines:
            break
        labels = stream.get("stream") or {}
        label_str = ", ".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
        lines = []
        for ts, line in stream.get("values") or []:
            if shown >= max_lines:
                break
            if len(line) > max_line_chars:
                line = line[:max_line_chars] + " [line truncated]"
            lines.append(f"{format_loki_timestamp(ts)} {line}")
            shown += 1
        if lines:
            sections.append(f"=== {{{label_str}}} ===\n" + "\n".join(lines))
    if shown < total_lines:
        notes.append(
            f"showing {shown} of {total_lines} log lines — narrow the LogQL "
            "selector, add a line filter (|= / |~), or shrink the time range"
        )
    return "\n\n".join(sections), notes


class LokiRunner:
    """Executes validated LogQL range queries against a locally configured Loki
    and returns results in the executor's standard result shape."""

    def __init__(
        self,
        base_url: str | None = None,
        tenant_id: str | None = None,
        timeout: int | None = None,
        max_lines: int | None = None,
        max_line_chars: int | None = None,
        max_output_chars: int | None = None,
    ):
        self.base_url = (
            (base_url if base_url is not None else os.environ.get("LOKI_URL", ""))
            .strip()
            .rstrip("/")
        )
        self.tenant_id = (
            tenant_id if tenant_id is not None else os.environ.get("LOKI_TENANT_ID", "")
        ).strip()
        self.timeout = timeout or int(os.environ.get("LOKI_TIMEOUT", str(DEFAULT_TIMEOUT_SECONDS)))
        self.max_lines = max_lines or int(os.environ.get("LOKI_MAX_LINES", str(DEFAULT_MAX_LINES)))
        self.max_line_chars = max_line_chars or int(
            os.environ.get("LOKI_MAX_LINE_CHARS", str(DEFAULT_MAX_LINE_CHARS))
        )
        self.max_output_chars = max_output_chars or int(
            os.environ.get("LOKI_MAX_OUTPUT_CHARS", str(DEFAULT_MAX_OUTPUT_CHARS))
        )

    @property
    def available(self) -> bool:
        return bool(self.base_url)

    def run(self, request: dict) -> dict:
        """Run one LogQL range query and return the executor result shape:
        {"success", "output", "error", "status", "return_code"}."""
        if not self.available:
            return self._error(UNAVAILABLE_MESSAGE, status="UNAVAILABLE")

        query = request.get("query")
        if not query or not isinstance(query, str):
            return self._error("Missing LogQL 'query' string.")

        limit = min(int(request.get("limit") or DEFAULT_LIMIT), self.max_lines)
        direction = request.get("direction") or "backward"
        if direction not in ("backward", "forward"):
            return self._error(
                f"Invalid direction '{direction}': use 'backward' (newest first) or 'forward'."
            )

        params = {"query": query, "limit": limit, "direction": direction}
        # Loki defaults to the last hour when start/end are omitted.
        for key in ("start", "end"):
            if request.get(key):
                params[key] = str(request[key])

        headers = {}
        if self.tenant_id:
            headers["X-Scope-OrgID"] = self.tenant_id

        try:
            response = requests.get(
                f"{self.base_url}{QUERY_RANGE_PATH}",
                params=params,
                headers=headers,
                timeout=self.timeout,
            )
        except requests.exceptions.Timeout:
            return self._error(
                f"Loki query timed out after {self.timeout}s. Narrow the query "
                "(shorter range, tighter label selector, lower limit).",
                status="TIMEOUT",
            )
        except requests.exceptions.RequestException as e:
            return self._error(f"Could not reach Loki at {self.base_url}: {e}")

        try:
            body = response.json()
        except ValueError:
            return self._error(
                f"Loki returned non-JSON (HTTP {response.status_code}): {response.text[:200]}"
            )

        if response.status_code != 200 or body.get("status") != "success":
            detail = body.get("error") or body.get("message") or f"HTTP {response.status_code}"
            return self._error(f"Loki query failed: {detail}")

        output = self._format_capped(body.get("data") or {})
        return {
            "success": True,
            "output": output,
            "error": None,
            "status": "SUCCESS",
            "return_code": 0,
        }

    def _format_capped(self, data: dict) -> str:
        result_type = data.get("resultType")
        result = data.get("result") or []

        if result_type == "streams":
            text, notes = format_streams(result, self.max_lines, self.max_line_chars)
            if not text:
                text = (
                    "No log lines matched. Widen the time range, loosen the label "
                    "selector, or drop/adjust line filters."
                )
            for note in notes:
                text += f"\n[{note}]"
        else:
            # Metric-style LogQL (rate/count_over_time/...) returns matrix or
            # vector data; pass it through compactly.
            text = json.dumps({"resultType": result_type, "result": result}, separators=(",", ":"))

        if len(text) > self.max_output_chars:
            text = text[: self.max_output_chars] + (
                f"\n[truncated at {self.max_output_chars} chars — narrow the query "
                "or lower the limit]"
            )
        return text

    @staticmethod
    def _error(message: str, status: str = "FAILED") -> dict:
        return {
            "success": False,
            "output": None,
            "error": message,
            "status": status,
            "return_code": -1,
        }
