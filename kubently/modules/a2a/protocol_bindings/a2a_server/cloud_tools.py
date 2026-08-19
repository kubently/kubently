"""
Provider-agnostic cloud telemetry tools for the Kubently agent.

The three tools here (query_cloud_logs, query_cloud_metrics,
get_recent_cloud_changes) dispatch to whichever cloud provider the target
cluster's executor reports holding a workload identity for. The executor —
not the agent, not this API — talks to the cloud, using a customer-controlled
read-only role; results come back over the existing outbound-only channel.

Kept out of agent.py so the agent-toolset area stays minimal and additive
(several sibling branches land there concurrently).
"""

import logging
import os
from collections.abc import Callable
from typing import Any

import httpx

logger = logging.getLogger(__name__)

CLOUD_UNAVAILABLE_MSG = (
    "Cluster '{cluster_id}' has no cloud telemetry access: its executor did not "
    "report a cloud identity. Cloud tools only work when the executor pod holds "
    "a read-only workload identity (EKS Pod Identity/IRSA or GKE Workload "
    "Identity) and cloud mode is enabled — see docs/CLOUD_TELEMETRY.md. "
    "Continue the investigation with kubectl-based tools."
)


def cloud_tools_configured() -> bool:
    """Whether this deployment has cloud telemetry switched on at all.

    The configuration-level half of the gate, and the only half that can be
    answered synchronously. The agent card (kubently/modules/a2a/skills.py) is
    built once when the A2A app is mounted, before any executor has connected
    or reported a capability, so it can only assert the operator's intent —
    asking the fleet there would advertise "no cloud" on every deployment.
    Tool registration additionally requires a live cloud identity; see
    cloud_tools_enabled() below.
    """
    return os.getenv("KUBENTLY_CLOUD_TOOLS", "auto").lower() != "off"


async def get_cloud_capability(
    api_url: str, api_key: str, cluster_id: str
) -> dict[str, Any] | None:
    """The cluster executor's reported cloud capability, or None."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{api_url}/api/v1/clusters/{cluster_id}/capabilities",
                headers={"X-Api-Key": api_key},
            )
            if response.status_code != 200:
                return None
            capabilities = response.json().get("capabilities") or {}
            return capabilities.get("cloud") or None
    except Exception as e:
        logger.warning(f"Cloud capability lookup failed for {cluster_id}: {e}")
        return None


async def run_cloud_operation(
    api_url: str,
    api_key: str,
    cluster_id: str,
    operation: str,
    params: dict[str, Any],
) -> str:
    """POST one whitelisted operation to /cloud/execute, return output text."""
    async with httpx.AsyncClient(timeout=70.0) as client:
        response = await client.post(
            f"{api_url}/cloud/execute",
            headers={"X-Api-Key": api_key},
            json={"cluster_id": cluster_id, "operation": operation, "params": params},
        )
        if response.status_code != 200:
            return f"Error: HTTP {response.status_code}: {response.text[:500]}"
        result = response.json()
        if result.get("error"):
            return f"Error: {result['error']}"
        return result.get("output") or "No output returned."


# --------------------------------------------------------------------------
# Provider dispatch: translate the provider-agnostic tool arguments into a
# (operation, params) pair for whichever provider the executor reported.
# --------------------------------------------------------------------------


def build_logs_request(
    provider: str,
    query: str,
    minutes: int,
    log_group: str | None,
    limit: int,
) -> tuple[str, dict[str, Any]]:
    if provider == "aws":
        if not log_group:
            # Let the agent discover group names first
            return "aws.logs.describe_log_groups", {"limit": 50}
        return "aws.logs.insights_query", {
            "log_group": log_group,
            "query": query
            or "fields @timestamp, @message | sort @timestamp desc | limit 50",
            "minutes": minutes,
            "limit": limit,
        }
    # gcp: query is a Cloud Logging advanced filter (may be empty for "everything")
    return "gcp.logging.list_entries", {
        "filter": query,
        "minutes": minutes,
        "limit": limit,
    }


def build_metrics_request(
    provider: str,
    metric: str,
    dimensions: dict[str, str] | None,
    stat: str,
    minutes: int,
    period_seconds: int,
) -> tuple[str, dict[str, Any]]:
    if provider == "aws":
        # metric format "Namespace:MetricName", e.g. "AWS/EKS:cluster_failed_node_count"
        namespace, _, metric_name = metric.rpartition(":")
        if not namespace:
            raise ValueError(
                "AWS metric must be 'Namespace:MetricName', e.g. "
                "'AWS/EC2:CPUUtilization' or 'ContainerInsights:node_cpu_utilization'"
            )
        return "aws.metrics.get_metric_data", {
            "namespace": namespace,
            "metric_name": metric_name,
            "dimensions": dimensions or {},
            "stat": stat,
            "period": period_seconds,
            "minutes": minutes,
        }
    # gcp: metric is the full metric type, dimensions become resource label filters
    clauses = [f'metric.type="{metric}"']
    for key, value in (dimensions or {}).items():
        clauses.append(f'resource.labels.{key}="{value}"')
    return "gcp.monitoring.list_time_series", {
        "filter": " AND ".join(clauses),
        "minutes": minutes,
    }


def build_changes_request(
    provider: str,
    minutes: int,
    resource_name: str | None,
    limit: int,
) -> tuple[str, dict[str, Any]]:
    if provider == "aws":
        params: dict[str, Any] = {"minutes": minutes, "limit": limit}
        if resource_name:
            params["resource_name"] = resource_name
        return "aws.cloudtrail.lookup_events", params
    params = {"minutes": minutes, "limit": limit}
    if resource_name:
        params["resource_name"] = resource_name
    return "gcp.gke.audit_logs", params


# --------------------------------------------------------------------------
# Registration gate (mirrors LOKI_URL / PROMETHEUS_URL): when nothing in the
# fleet can serve these tools they are not registered and the prompt section
# below is omitted, so the model is never told about a tool it cannot call.
# The per-call capability check stays, for mixed fleets where only some
# executors hold a cloud identity.
# --------------------------------------------------------------------------


async def cloud_tools_enabled(redis_client) -> bool:
    """Whether the cloud tools should be registered for this agent run.

    Configured on *and* some registered executor advertises a cloud identity.
    Evaluated at agent initialisation, which is late enough for the fleet's
    capability reports to be present.
    """
    if not cloud_tools_configured():
        logger.info("Cloud tools disabled via KUBENTLY_CLOUD_TOOLS=off")
        return False
    if redis_client is None:
        return False
    try:
        from kubently.modules.capability import CapabilityModule

        reports = await CapabilityModule(redis_client).list_all_capabilities()
        return any(report.cloud for report in reports)
    except Exception as e:
        logger.warning(f"Cloud capability scan failed; cloud tools stay off: {e}")
        return False


# Injected into the system prompt (via the {{cloud_guidance}} variable) only
# when the tools are registered.
CLOUD_PROMPT_SECTION = """\
## Cloud Telemetry

Some clusters' executors hold a read-only cloud identity (AWS or GCP). For those clusters you can use query_cloud_logs, query_cloud_metrics, and get_recent_cloud_changes. The tools tell you when a cluster has no cloud access — do not retry them there; continue with kubectl.

Prefer CLOUD evidence over cluster evidence when the cause likely lives outside the cluster:
- IAM/permission errors (AccessDenied, 403 from cloud APIs, image pull auth failures against ECR/GCR/AR): check cloud logs and recent IAM changes, not just pod logs.
- Throttling/quota symptoms (RequestLimitExceeded, 429s, rate exceeded): cloud metrics and API logs show the source.
- Managed-service failures (RDS/CloudSQL, load balancers, node pools): the service's cloud logs/metrics, not kubectl, hold the answer.
- Control-plane issues (API server errors, webhook timeouts, authentication failures): EKS control-plane logs / GKE audit logs — kubectl cannot see the control plane's own logs.
- "It broke suddenly and nothing changed in-cluster": get_recent_cloud_changes correlates CloudTrail/GKE audit events (IAM edits, security-group changes, node-pool operations) with the incident window.

Prefer CLUSTER evidence (kubectl) for anything about workload state: pod status, events, container logs of live pods, manifests, scheduling. Start with kubectl; reach for cloud tools when in-cluster evidence dead-ends or points outward.
"""


def cloud_guidance(enabled: bool) -> str:
    """The prompt section to inject — empty when the tools are not registered."""
    return CLOUD_PROMPT_SECTION if enabled else ""


# --------------------------------------------------------------------------
# Tool construction
# --------------------------------------------------------------------------


def build_cloud_tools(
    api_url: str,
    api_key_getter: Callable[[], str],
    interceptor: Any,
    thread_id_getter: Callable[[], str | None],
) -> list:
    """
    Build the three provider-agnostic cloud tools.

    Call only when cloud_tools_enabled() said so. Returns [] when disabled
    via KUBENTLY_CLOUD_TOOLS=off. Each tool still checks — per target cluster,
    per call — that the executor actually reports a cloud identity, because
    executors join/leave and identities get granted/revoked at runtime.
    """
    if not cloud_tools_configured():
        logger.info("Cloud tools disabled via KUBENTLY_CLOUD_TOOLS=off")
        return []

    from langchain_core.tools import tool

    async def _dispatch(
        tool_name: str,
        cluster_id: str,
        args: dict[str, Any],
        request_builder: Callable[[str], tuple[str, dict[str, Any]]],
    ) -> str:
        """Shared capability-check + interceptor-traced dispatch."""
        tool_call_id = await interceptor.record_tool_call(
            tool_name=tool_name,
            args={"cluster_id": cluster_id, **args},
            thread_id=thread_id_getter(),
        )
        try:
            cloud = await get_cloud_capability(api_url, api_key_getter(), cluster_id)
            if not cloud:
                output = CLOUD_UNAVAILABLE_MSG.format(cluster_id=cluster_id)
                await interceptor.record_tool_result(tool_call_id, output)
                return output

            provider = cloud.get("provider", "")
            try:
                operation, params = request_builder(provider)
            except ValueError as e:
                output = str(e)
                await interceptor.record_tool_result(tool_call_id, None, output)
                return output

            if operation not in (cloud.get("operations") or []):
                output = (
                    f"The executor for '{cluster_id}' holds a {provider} identity "
                    f"but operation '{operation}' is not usable with it (its IAM "
                    f"role lacks the permission, or the operation family failed "
                    f"the permission probe). Usable operations: "
                    f"{cloud.get('operations')}"
                )
                await interceptor.record_tool_result(tool_call_id, output)
                return output

            output = await run_cloud_operation(
                api_url, api_key_getter(), cluster_id, operation, params
            )
            await interceptor.record_tool_result(tool_call_id, output)
            return output
        except Exception as e:
            error_msg = f"Error executing {tool_name}: {e!s}"
            await interceptor.record_tool_result(tool_call_id, None, error_msg)
            return error_msg

    @tool
    async def query_cloud_logs(
        cluster_id: str,
        query: str = "",
        minutes: int = 60,
        log_group: str = "",
        limit: int = 100,
    ) -> str:
        """Query cloud provider logs (CloudWatch Logs Insights / GCP Cloud Logging) for a cluster.

        Use this when cloud-side evidence beats cluster-side evidence: control
        plane behavior, logs of pods that no longer exist, managed-service
        errors, or anything kubectl cannot see. Only works when the cluster's
        executor reports a cloud identity.

        The query language depends on the provider the executor reports:
        - AWS: CloudWatch Logs Insights syntax, e.g.
          "fields @timestamp, @message | filter @message like /error/ | sort @timestamp desc | limit 50".
          A log_group is required; call this tool WITHOUT log_group first to
          list available log groups (EKS control plane logs live in
          "/aws/eks/<cluster-name>/cluster").
        - GCP: Cloud Logging advanced filter, e.g.
          'resource.type="k8s_container" AND severity>=ERROR'. log_group is ignored.

        Args:
            cluster_id: Target cluster
            query: Provider-native log query/filter (see above)
            minutes: Lookback window in minutes (default 60, max 10080)
            log_group: AWS only — CloudWatch log group name
            limit: Max entries to return (hard-capped server-side)

        Returns:
            JSON with matching log entries; truncation is explicitly noted.
        """
        return await _dispatch(
            "query_cloud_logs",
            cluster_id,
            {"query": query, "minutes": minutes, "log_group": log_group, "limit": limit},
            lambda provider: build_logs_request(
                provider, query, minutes, log_group or None, limit
            ),
        )

    @tool
    async def query_cloud_metrics(
        cluster_id: str,
        metric: str,
        dimensions: dict[str, str] | None = None,
        stat: str = "Average",
        minutes: int = 60,
        period_seconds: int = 300,
    ) -> str:
        """Query cloud provider metrics (CloudWatch / GCP Cloud Monitoring) for a cluster.

        Use this for evidence kubectl cannot show: node-level pressure before
        eviction, managed-service throttling, control-plane API latency,
        load balancer error rates. Only works when the cluster's executor
        reports a cloud identity.

        Metric naming depends on the provider the executor reports:
        - AWS: "Namespace:MetricName", e.g. "AWS/EC2:CPUUtilization",
          "ContainerInsights:node_cpu_utilization", "AWS/EKS:cluster_failed_node_count".
          dimensions example: {"ClusterName": "prod-eks"}
        - GCP: full metric type, e.g. "kubernetes.io/container/cpu/core_usage_time".
          dimensions filter resource labels, e.g. {"cluster_name": "prod-gke"}

        Args:
            cluster_id: Target cluster
            metric: Metric identifier (provider-native, see above)
            dimensions: Dimension/label filters
            stat: AWS statistic (Average, Sum, Maximum, Minimum, p99...)
            minutes: Lookback window in minutes (default 60)
            period_seconds: Datapoint granularity (AWS, default 300)

        Returns:
            JSON time series; truncation is explicitly noted.
        """
        return await _dispatch(
            "query_cloud_metrics",
            cluster_id,
            {
                "metric": metric,
                "dimensions": dimensions,
                "stat": stat,
                "minutes": minutes,
                "period_seconds": period_seconds,
            },
            lambda provider: build_metrics_request(
                provider, metric, dimensions, stat, minutes, period_seconds
            ),
        )

    @tool
    async def get_recent_cloud_changes(
        cluster_id: str,
        minutes: int = 60,
        resource_name: str = "",
        limit: int = 50,
    ) -> str:
        """List recent cloud-level changes (CloudTrail / GKE audit logs) near a cluster.

        Use this for change correlation: "what changed right before this broke?"
        Surfaces IAM/security-group/node-pool/cluster-config changes made
        outside Kubernetes that kubectl can never see. Only works when the
        cluster's executor reports a cloud identity.

        Args:
            cluster_id: Target cluster
            minutes: Lookback window in minutes (default 60, max 10080)
            resource_name: Optional filter to changes touching one resource
                (AWS: CloudTrail resource name; GCP: audit resourceName substring)
            limit: Max events (hard-capped at 50)

        Returns:
            JSON list of change events (who, what, when); truncation noted.
        """
        return await _dispatch(
            "get_recent_cloud_changes",
            cluster_id,
            {"minutes": minutes, "resource_name": resource_name, "limit": limit},
            lambda provider: build_changes_request(
                provider, minutes, resource_name or None, limit
            ),
        )

    return [query_cloud_logs, query_cloud_metrics, get_recent_cloud_changes]
