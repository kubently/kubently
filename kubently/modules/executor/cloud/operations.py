"""
Operation allowlist for cloud read operations.

This is the code-level security boundary: an operation that is not listed here
cannot be executed, no matter what the executor is asked to do and no matter
how broad the pod's IAM role accidentally is. IAM is the customer's boundary;
this allowlist is Kubently's. Both must permit an operation for it to run.

This module is intentionally dependency-free (no boto3 / google-cloud imports)
so the central API server can import it for request validation without cloud
SDKs installed.

Every operation is read-only by construction — each maps to exactly one
provider API call (or a start/poll pair for Logs Insights) that retrieves
data. There is no generic "call any SDK method" escape hatch.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OperationSpec:
    """Describes a single whitelisted cloud read operation."""

    name: str  # e.g. "aws.logs.insights_query"
    provider: str  # "aws" or "gcp"
    family: str  # permission-probe family, e.g. "logs", "metrics", "changes"
    description: str
    required_permissions: tuple = field(default_factory=tuple)  # IAM perms needed


_SPECS = [
    # ------------------------------------------------------------------ AWS
    OperationSpec(
        name="aws.sts.get_caller_identity",
        provider="aws",
        family="identity",
        description="Return the AWS identity (account/ARN) the executor holds",
        required_permissions=(),  # GetCallerIdentity needs no permissions
    ),
    OperationSpec(
        name="aws.logs.insights_query",
        provider="aws",
        family="logs",
        description=(
            "Run a CloudWatch Logs Insights query synchronously (StartQuery + poll GetQueryResults)"
        ),
        required_permissions=("logs:StartQuery", "logs:GetQueryResults"),
    ),
    OperationSpec(
        name="aws.logs.start_query",
        provider="aws",
        family="logs",
        description="Start an async CloudWatch Logs Insights query, returns queryId",
        required_permissions=("logs:StartQuery",),
    ),
    OperationSpec(
        name="aws.logs.get_query_results",
        provider="aws",
        family="logs",
        description="Poll results of a CloudWatch Logs Insights query by queryId",
        required_permissions=("logs:GetQueryResults",),
    ),
    OperationSpec(
        name="aws.logs.describe_log_groups",
        provider="aws",
        family="logs",
        description="List CloudWatch log groups (find the right group to query)",
        required_permissions=("logs:DescribeLogGroups",),
    ),
    OperationSpec(
        name="aws.logs.filter_log_events",
        provider="aws",
        family="logs",
        description="Filter recent events from a CloudWatch log group (no Insights)",
        required_permissions=("logs:FilterLogEvents",),
    ),
    OperationSpec(
        name="aws.metrics.get_metric_data",
        provider="aws",
        family="metrics",
        description="Fetch CloudWatch metric time series via GetMetricData",
        required_permissions=("cloudwatch:GetMetricData",),
    ),
    OperationSpec(
        name="aws.eks.control_plane_logs",
        provider="aws",
        family="logs",
        description=(
            "Read EKS control-plane logs (api server, authenticator, audit, "
            "scheduler, controller manager) from /aws/eks/<cluster>/cluster"
        ),
        required_permissions=("logs:FilterLogEvents", "eks:DescribeCluster"),
    ),
    OperationSpec(
        name="aws.cloudtrail.lookup_events",
        provider="aws",
        family="changes",
        description="Look up recent CloudTrail management events (change correlation)",
        required_permissions=("cloudtrail:LookupEvents",),
    ),
    # ------------------------------------------------------------------ GCP
    OperationSpec(
        name="gcp.logging.list_entries",
        provider="gcp",
        family="logs",
        description="Query Cloud Logging entries with an advanced-log filter",
        required_permissions=("logging.logEntries.list",),
    ),
    OperationSpec(
        name="gcp.monitoring.list_time_series",
        provider="gcp",
        family="metrics",
        description="Fetch Cloud Monitoring time series (timeSeries.list)",
        required_permissions=("monitoring.timeSeries.list",),
    ),
    OperationSpec(
        name="gcp.gke.audit_logs",
        provider="gcp",
        family="changes",
        description=(
            "Read a slice of GKE audit logs (cloudaudit.googleapis.com activity "
            "log, resource.type=k8s_cluster) for change correlation"
        ),
        required_permissions=("logging.logEntries.list",),
    ),
]

ALLOWED_CLOUD_OPERATIONS: dict[str, OperationSpec] = {s.name: s for s in _SPECS}

# Family names used by permission probing; an operation is advertised as
# usable only when its family's probe call succeeded with the pod's identity.
OPERATION_FAMILIES = sorted({s.family for s in _SPECS})


def operations_for_provider(provider: str) -> list[OperationSpec]:
    """All whitelisted operations for one provider."""
    return [s for s in _SPECS if s.provider == provider]


def is_allowed(operation: str) -> bool:
    """True when the operation name is on the code-level allowlist."""
    return operation in ALLOWED_CLOUD_OPERATIONS
