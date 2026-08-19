"""Read-only Prometheus query runner for the Kubently executor.

The control plane cannot reach an in-cluster Prometheus, so metric queries
travel the same outbound channel as kubectl commands: API -> Redis pub/sub ->
executor SSE -> result POST. This module is the executor-side terminus of that
path.

Security model (mirrors the kubectl whitelist philosophy — the executor, not
the caller, decides what it will execute):

- The base URL comes ONLY from local executor configuration (PROMETHEUS_URL).
  The control plane never supplies a URL, so a compromised API key cannot turn
  the executor into an SSRF proxy.
- Only two fixed, read-only HTTP API paths are reachable: /api/v1/query and
  /api/v1/query_range. GET only. Nothing else is constructible from a request.
- When PROMETHEUS_URL is unset the runner reports "unavailable" without making
  any network call.

Results are capped (series count, total samples, output characters) before
they leave the executor, so an unbounded PromQL result never floods Redis, the
API, or the model's context. Truncation is always announced in the output so
the model knows it is looking at a partial result.

Deliberately import-light (stdlib + requests) to match sse_executor.py.
"""

import json
import logging
import os

import requests

logger = logging.getLogger("kubently-prometheus")

# The complete surface area: query_type -> API path. GET only, nothing else.
ALLOWED_QUERY_PATHS = {
    "instant": "/api/v1/query",
    "range": "/api/v1/query_range",
}

DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_MAX_SERIES = 50
DEFAULT_MAX_SAMPLES = 2000
DEFAULT_MAX_OUTPUT_CHARS = 20000

UNAVAILABLE_MESSAGE = (
    "Prometheus is not configured on this cluster's executor "
    "(PROMETHEUS_URL is unset). Metrics are unavailable here; "
    "continue the investigation with kubectl."
)


def _thin_samples(values: list, keep: int) -> list:
    """Keep `keep` evenly spaced samples, always including first and last.

    Preserves the shape of a trend (the reason the model asked for a range
    query) instead of returning only the oldest window.
    """
    if keep <= 0:
        return []
    if len(values) <= keep:
        return values
    if keep == 1:
        return [values[-1]]
    step = (len(values) - 1) / (keep - 1)
    indices = {round(i * step) for i in range(keep)}
    indices.add(len(values) - 1)
    return [values[i] for i in sorted(indices)]


class PrometheusRunner:
    """Executes validated instant/range queries against a locally configured
    Prometheus and returns results in the executor's standard result shape."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: int | None = None,
        max_series: int | None = None,
        max_samples: int | None = None,
        max_output_chars: int | None = None,
    ):
        self.base_url = (
            (base_url if base_url is not None else os.environ.get("PROMETHEUS_URL", ""))
            .strip()
            .rstrip("/")
        )
        self.timeout = timeout or int(
            os.environ.get("PROMETHEUS_TIMEOUT", str(DEFAULT_TIMEOUT_SECONDS))
        )
        self.max_series = max_series or int(
            os.environ.get("PROMETHEUS_MAX_SERIES", str(DEFAULT_MAX_SERIES))
        )
        self.max_samples = max_samples or int(
            os.environ.get("PROMETHEUS_MAX_SAMPLES", str(DEFAULT_MAX_SAMPLES))
        )
        self.max_output_chars = max_output_chars or int(
            os.environ.get("PROMETHEUS_MAX_OUTPUT_CHARS", str(DEFAULT_MAX_OUTPUT_CHARS))
        )

    @property
    def available(self) -> bool:
        return bool(self.base_url)

    def run(self, request: dict) -> dict:
        """Run one query request and return the executor result shape:
        {"success", "output", "error", "status", "return_code"}."""
        if not self.available:
            return self._error(UNAVAILABLE_MESSAGE, status="UNAVAILABLE")

        query_type = request.get("query_type", "instant")
        path = ALLOWED_QUERY_PATHS.get(query_type)
        if path is None:
            return self._error(
                f"Unsupported query_type '{query_type}'. "
                f"Allowed: {', '.join(sorted(ALLOWED_QUERY_PATHS))}."
            )

        query = request.get("query")
        if not query or not isinstance(query, str):
            return self._error("Missing PromQL 'query' string.")

        params = {"query": query}
        if query_type == "range":
            missing = [k for k in ("start", "end", "step") if not request.get(k)]
            if missing:
                return self._error(
                    f"Range query requires start, end and step (missing: {', '.join(missing)})."
                )
            params["start"] = str(request["start"])
            params["end"] = str(request["end"])
            params["step"] = str(request["step"])
        elif request.get("time"):
            params["time"] = str(request["time"])

        try:
            response = requests.get(
                f"{self.base_url}{path}",
                params=params,
                timeout=self.timeout,
            )
        except requests.exceptions.Timeout:
            return self._error(
                f"Prometheus query timed out after {self.timeout}s. "
                "Narrow the query (shorter range, larger step, tighter selectors).",
                status="TIMEOUT",
            )
        except requests.exceptions.RequestException as e:
            return self._error(f"Could not reach Prometheus at {self.base_url}: {e}")

        try:
            body = response.json()
        except ValueError:
            return self._error(
                f"Prometheus returned non-JSON (HTTP {response.status_code}): {response.text[:200]}"
            )

        if response.status_code != 200 or body.get("status") != "success":
            # Prometheus puts PromQL errors in errorType/error with HTTP 400.
            detail = body.get("error") or f"HTTP {response.status_code}"
            error_type = body.get("errorType")
            prefix = (
                f"Prometheus query failed ({error_type}): "
                if error_type
                else "Prometheus query failed: "
            )
            return self._error(prefix + str(detail))

        output = self._format_capped(body.get("data") or {})
        return {
            "success": True,
            "output": output,
            "error": None,
            "status": "SUCCESS",
            "return_code": 0,
        }

    def _format_capped(self, data: dict) -> str:
        """Serialize the result, capping series count and total samples.

        Truncation notes are embedded in the payload the model reads, so a
        partial result is never mistaken for the whole picture.
        """
        result_type = data.get("resultType")
        result = data.get("result")
        notes = []

        if isinstance(result, list):
            total_series = len(result)
            if total_series > self.max_series:
                result = result[: self.max_series]
                notes.append(
                    f"showing {self.max_series} of {total_series} series — "
                    "aggregate (sum/avg by (...)) or use topk() to reduce cardinality"
                )

            # Matrix results carry per-series "values"; spread the sample
            # budget across the kept series and thin evenly to keep the trend.
            series_with_values = [s for s in result if isinstance(s, dict) and "values" in s]
            total_samples = sum(len(s.get("values") or []) for s in series_with_values)
            if series_with_values and total_samples > self.max_samples:
                per_series = max(2, self.max_samples // len(series_with_values))
                for s in series_with_values:
                    s["values"] = _thin_samples(s.get("values") or [], per_series)
                notes.append(
                    f"downsampled from {total_samples} to "
                    f"~{self.max_samples} samples (evenly spaced, endpoints kept) — "
                    "use a larger step or shorter range for full resolution"
                )

        payload = {"resultType": result_type, "result": result}
        if notes:
            payload["kubently_truncation"] = notes
        output = json.dumps(payload, separators=(",", ":"))

        if len(output) > self.max_output_chars:
            output = output[: self.max_output_chars] + (
                f"\n[truncated at {self.max_output_chars} chars — result too large even "
                "after series/sample caps; aggregate the query further]"
            )
        return output

    @staticmethod
    def _error(message: str, status: str = "FAILED") -> dict:
        return {
            "success": False,
            "output": None,
            "error": message,
            "status": status,
            "return_code": -1,
        }
