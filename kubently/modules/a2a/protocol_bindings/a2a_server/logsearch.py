"""Agent-side plumbing for the log-search toolset.

Deliberately import-light (stdlib only) so unit tests can import it without
pulling the langchain/a2a stack that agent.py requires.

Two tools with different availability contracts:

- search_pod_logs is ALWAYS registered: it only needs the executor's kubectl
  runner, which every cluster has. Its guidance lives directly in the
  externalized system prompt YAML.
- query_loki is registered ONLY when LOKI_URL is set in the A2A server's
  environment, and the matching guidance is injected into the system prompt
  through the {{loki_guidance}} prompt variable at the same time. When unset,
  neither exists, so the model is never told about a tool it cannot call.

Note the A2A server never dials LOKI_URL itself — queries execute on the
target cluster's executor against the executor's own locally configured URL.
On the control plane the variable only switches the tool on.
"""

import os

LOKI_URL_ENV = "LOKI_URL"


def loki_tool_enabled() -> bool:
    """Whether the query_loki tool should be registered."""
    return bool(os.getenv(LOKI_URL_ENV, "").strip())


def build_log_search_payload(
    namespace: str,
    query: str,
    selector: str | None = None,
    pod_name: str | None = None,
    container: str | None = None,
    use_regex: bool = False,
    case_sensitive: bool = False,
    since: str | None = "1h",
    since_time: str | None = None,
    tail_lines: int = 2000,
    previous: bool = False,
    context_lines: int = 0,
    timeout_seconds: int = 60,
) -> dict:
    """Build the /debug/logs/search request body (minus cluster_id)."""
    return {
        "namespace": namespace,
        "query": query,
        "selector": selector,
        "pod_name": pod_name,
        "container": container,
        "use_regex": use_regex,
        "case_sensitive": case_sensitive,
        "since": since,
        "since_time": since_time,
        "tail_lines": tail_lines,
        "previous": previous,
        "context_lines": context_lines,
        "timeout_seconds": timeout_seconds,
    }


def build_loki_payload(
    query: str,
    start: str | None = None,
    end: str | None = None,
    limit: int = 100,
    direction: str = "backward",
    timeout_seconds: int = 30,
) -> dict:
    """Build the /debug/loki request body (minus cluster_id)."""
    return {
        "query": query,
        "start": start,
        "end": end,
        "limit": limit,
        "direction": direction,
        "timeout_seconds": timeout_seconds,
    }


# Injected into the system prompt (via the {{loki_guidance}} variable) only
# when the tool is registered, so an unconfigured deployment's prompt never
# references Loki.
LOKI_PROMPT_SECTION = """\
### Loki (preferred when available)

You also have a query_loki tool (LogQL range queries). PREFER Loki over
search_pod_logs when the question spans history or many workloads: Loki keeps
logs from pods that have restarted, been rescheduled or deleted, and searches a
whole namespace in one query instead of pod-by-pod fetches. Fall back to
search_pod_logs for the freshest lines or when a Loki query errors.

Write BOUNDED LogQL — results are line-capped with a note when truncated:
- Always start from a label selector: `{namespace="payments", app="api"}`
- Add line filters to narrow: `{namespace="x"} |= "error"` (substring),
  `|~ "timeout|refused"` (regex), `!=` / `!~` to exclude noise
- Keep time ranges short (default is the last hour; pass start/end RFC3339 to widen)
- Default direction is backward (newest first) — right for incident triage
- Count before dumping when volume is unknown:
  `sum by (pod) (count_over_time({namespace="x"} |= "error" [1h]))`, then fetch
  lines only for the offenders

If query_loki reports Loki is not configured for a cluster, use search_pod_logs
on that cluster instead — do not retry Loki there.
"""


def loki_guidance() -> str:
    """The prompt section to inject — empty when the tool is not registered."""
    return LOKI_PROMPT_SECTION if loki_tool_enabled() else ""
