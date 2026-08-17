"""Agent-side plumbing for the Prometheus metrics tool.

Deliberately import-light (stdlib only) so unit tests can import it without
pulling the langchain/a2a stack that agent.py requires.

Availability contract: the query_prometheus tool is registered ONLY when
PROMETHEUS_URL is set in the A2A server's environment, and the matching
metrics guidance is injected into the system prompt through the
{{metrics_guidance}} prompt variable at the same time. When unset, neither
exists, so the model is never told about a tool it cannot call.

Note the A2A server never dials PROMETHEUS_URL itself — queries execute on the
target cluster's executor against the executor's own locally configured URL.
On the control plane the variable only switches the tool on.
"""

import os

PROMETHEUS_URL_ENV = "PROMETHEUS_URL"


def prometheus_tool_enabled() -> bool:
    """Whether the query_prometheus tool should be registered."""
    return bool(os.getenv(PROMETHEUS_URL_ENV, "").strip())


def build_prometheus_payload(
    query: str,
    query_type: str = "instant",
    start: str | None = None,
    end: str | None = None,
    step: str | None = None,
    time: str | None = None,
    timeout_seconds: int = 30,
) -> dict:
    """Build the /debug/prometheus request body (minus cluster_id)."""
    return {
        "query": query,
        "query_type": query_type,
        "start": start,
        "end": end,
        "step": step,
        "time": time,
        "timeout_seconds": timeout_seconds,
    }


# Injected into the system prompt (via the {{metrics_guidance}} variable) only
# when the tool is registered, so an unconfigured deployment's prompt never
# references metrics.
METRICS_PROMPT_SECTION = """\
## Metrics (Prometheus)

You have a query_prometheus tool for PromQL queries against each cluster's Prometheus.
Metrics answer questions kubectl cannot: trends and rates over time.

REACH FOR METRICS WHEN the question involves:
- Latency / error rates ("is the service slow?", "are requests failing?")
- Resource saturation and trends (CPU throttling, memory growth toward OOM, disk filling)
- Restarts or OOMKills OVER TIME (kubectl shows the current count; metrics show when and how often)
- Capacity questions ("will this node run out of memory?")
- Confirming a root cause with data (e.g. memory climbing to the limit before each restart)

Use instant queries for "right now" values; range queries for trends. Keep range
windows short (30m-2h) and steps coarse (60s+) unless you need finer detail.

WRITE EFFICIENT PromQL — results are capped (series and samples are truncated
with a note when exceeded), so unfiltered queries waste your budget:
- ALWAYS filter by labels: `container_memory_working_set_bytes{namespace="x", pod=~"api-.*"}`
- Aggregate before returning: `sum by (pod) (rate(container_cpu_usage_seconds_total{namespace="x"}[5m]))`
- Use topk() to rank offenders: `topk(5, rate(kube_pod_container_status_restarts_total[1h]))`
- rate()/increase() over counters, never raw counter values
- NEVER query a bare metric name with no selector — that can return thousands of series

Useful patterns:
- Restart trend: `increase(kube_pod_container_status_restarts_total{namespace="x"}[1h])`
- OOM pressure: `container_memory_working_set_bytes / on(pod,container) kube_pod_container_resource_limits{resource="memory"}`
- CPU throttling: `rate(container_cpu_cfs_throttled_periods_total{namespace="x"}[5m])`
- P99 latency (histograms): `histogram_quantile(0.99, sum by (le) (rate(http_request_duration_seconds_bucket[5m])))`

If the tool reports Prometheus is not configured for a cluster, continue with
kubectl evidence — do not retry the metrics tool on that cluster.
"""


def metrics_guidance() -> str:
    """The prompt section to inject — empty when the tool is not registered."""
    return METRICS_PROMPT_SECTION if prometheus_tool_enabled() else ""
