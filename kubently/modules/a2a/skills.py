"""Agent-card skills, derived from the same gating that registers tools.

The agent card is a public contract: other agents read it to decide whether to
route a question to Kubently at all. So every optional toolset here is gated on
exactly the predicate that decides whether its tools get registered in
`agent.py::_initialize_tools` — the card gains a skill when a toolset is
switched on and loses it when off, with no second source of truth to drift.

Import-light on purpose (stdlib plus the sibling gating modules), so the
coverage test can check that every registered tool is advertised without
pulling the langchain/a2a stack.
"""

from __future__ import annotations

# Every skill lists the tools it advertises. `enabled` is None for the toolsets
# that are always registered, otherwise the name of the gating predicate that
# agent.py checks before registering them (resolved lazily in _is_enabled).
SKILLS: list[dict] = [
    {
        "id": "kubernetes-debug",
        "name": "Kubernetes Debugging",
        "description": (
            "Execute read-only kubectl commands across registered clusters and read "
            "resource state and Kubernetes events. Troubleshoot pods, services, "
            "deployments, and other resources."
        ),
        "tags": ["kubernetes", "k8s", "debugging", "kubectl", "troubleshooting", "events"],
        "examples": [
            "Show me all failing pods",
            "Debug crashlooping pod",
            "What events fired for deployment checkout-api?",
        ],
        "tools": ["list_clusters", "execute_kubectl", "get_events_for_resource"],
        "enabled": None,
    },
    {
        "id": "fleet-query",
        "name": "Multi-Cluster Fleet Queries",
        "description": (
            "Run one read-only query across many registered clusters at once and "
            "report the fleet-wide answer, including which clusters could not be reached."
        ),
        "tags": ["kubernetes", "fleet", "multi-cluster", "inventory", "audit"],
        "examples": [
            "Which clusters are still running image tag v1.4?",
            "Show unhealthy pods across every cluster",
        ],
        "tools": ["execute_kubectl_multi"],
        "enabled": None,
    },
    {
        "id": "pod-log-search",
        "name": "Pod Log Search",
        "description": (
            "Search the current logs of a workload's pods for errors, panics and "
            "stack traces, filtered server-side rather than dumped whole."
        ),
        "tags": ["logs", "kubernetes", "errors", "search"],
        "examples": [
            "Search the api pods' logs for panics",
            "Find OOM messages in the checkout logs",
        ],
        "tools": ["search_pod_logs"],
        "enabled": None,
    },
    {
        "id": "change-correlation",
        "name": "Change Correlation",
        "description": (
            "Answer 'what changed?' — correlate an incident with recent rollouts, "
            "ReplicaSet revisions, Helm release history, ArgoCD syncs and events on "
            "one chronological timeline."
        ),
        "tags": ["changes", "rollout", "helm", "argocd", "gitops", "correlation"],
        "examples": [
            "What changed in namespace payments in the last hour?",
            "Did anything roll out just before this started failing?",
        ],
        "tools": ["get_recent_changes"],
        "enabled": None,
    },
    {
        "id": "incident-history",
        "name": "Past Incident Recall",
        "description": (
            "Search previously diagnosed incidents for a similar symptom and reuse "
            "the earlier diagnosis. Namespace-isolated to the calling tenant."
        ),
        "tags": ["incidents", "history", "postmortem", "similar-issues"],
        "examples": [
            "Have we seen this CrashLoopBackOff before?",
            "What was the fix last time the ingress 503'd?",
        ],
        "tools": ["search_past_incidents"],
        "enabled": "incidents",
    },
    {
        "id": "loki-log-search",
        "name": "Loki Log Search",
        "description": (
            "Run bounded read-only LogQL queries against a cluster's Loki — logs "
            "that survive pod restarts and reach further back than pod logs do."
        ),
        "tags": ["logs", "loki", "logql", "observability"],
        "examples": [
            "How many error lines did the api emit per pod in the last hour?",
            "Search Loki for 'connection refused' in namespace payments",
        ],
        "tools": ["query_loki"],
        "enabled": "loki",
    },
    {
        "id": "prometheus-metrics",
        "name": "Prometheus Metrics",
        "description": (
            "Run read-only PromQL (instant and range) against a cluster's Prometheus "
            "to check error rates, latency, saturation and restarts, and compare a "
            "window against the period before a change."
        ),
        "tags": ["metrics", "prometheus", "promql", "latency", "saturation"],
        "examples": [
            "What is the 5xx rate for the api service right now?",
            "Compare checkout latency to the 30 minutes before the deploy",
        ],
        "tools": ["query_prometheus"],
        "enabled": "prometheus",
    },
    {
        "id": "cloud-telemetry",
        "name": "Cloud Telemetry",
        "description": (
            "Query the cloud provider behind a cluster — provider logs, provider "
            "metrics and recent cloud-side changes (audit events) — through the "
            "executor's read-only workload identity, where one is granted."
        ),
        "tags": ["cloud", "aws", "gcp", "audit-logs", "cloudwatch", "telemetry"],
        "examples": [
            "Any node group changes in the last hour?",
            "Show cloud load balancer errors for this cluster",
        ],
        "tools": ["query_cloud_logs", "query_cloud_metrics", "get_recent_cloud_changes"],
        "enabled": "cloud",
    },
    {
        "id": "gitops-remediation",
        "name": "GitOps Fix Proposals",
        "description": (
            "Read the manifest behind a live resource and open a pull request that "
            "proposes the fix. Never writes to the cluster — the change lands as a "
            "reviewable PR against the configured GitOps repository."
        ),
        "tags": ["gitops", "remediation", "pull-request", "github", "gitlab"],
        "examples": [
            "Propose a PR raising the memory limit for the api deployment",
            "Show me the manifest that produced this deployment",
        ],
        "tools": ["get_manifest_file", "propose_fix_pr"],
        "enabled": "gitops",
    },
    {
        "id": "external-mcp-tools",
        "name": "External MCP Tools",
        "description": (
            "Additional operator-configured tools from external MCP servers, "
            "registered under an mcp_<server>_<tool> prefix and used alongside the "
            "native Kubernetes tools."
        ),
        "tags": ["mcp", "integrations", "external-tools"],
        "examples": ["Use the connected observability tools to investigate this alert"],
        # Tool names are discovered from the server at startup, so none are static.
        "tools": [],
        "enabled": "mcp",
    },
]


def _is_enabled(gate: str, has_redis: bool) -> bool:
    """Evaluate one gate using the very predicate agent.py registers tools on."""
    if gate == "incidents":
        from kubently.modules.incidents.records import incidents_enabled

        # agent.py also requires a Redis client for the incident store.
        return incidents_enabled() and has_redis
    if gate == "loki":
        from .protocol_bindings.a2a_server.logsearch import loki_tool_enabled

        return loki_tool_enabled()
    if gate == "prometheus":
        from .protocol_bindings.a2a_server.prometheus import prometheus_tool_enabled

        return prometheus_tool_enabled()
    if gate == "cloud":
        from .protocol_bindings.a2a_server.cloud_tools import cloud_tools_enabled

        return cloud_tools_enabled()
    if gate == "gitops":
        from .protocol_bindings.a2a_server.gitops import gitops_tools_enabled

        return gitops_tools_enabled()
    if gate == "mcp":
        from .protocol_bindings.a2a_server.mcp_client import mcp_client_enabled

        return mcp_client_enabled()
    raise ValueError(f"Unknown skill gate: {gate}")


def build_skills(has_redis: bool = True) -> list[dict]:
    """The skills this deployment actually has, in agent-card field order."""
    return [
        {k: v for k, v in skill.items() if k not in ("tools", "enabled")}
        for skill in SKILLS
        if skill["enabled"] is None or _is_enabled(skill["enabled"], has_redis)
    ]


def advertised_tools() -> set[str]:
    """Every tool name claimed by a skill (regardless of whether it is on)."""
    return {name for skill in SKILLS for name in skill["tools"]}
