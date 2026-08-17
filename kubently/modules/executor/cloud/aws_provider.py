"""
AWS provider: read-only cloud telemetry via boto3 and ambient pod identity.

Credentials come exclusively from the pod's environment (EKS Pod Identity or
IRSA) through boto3's default credential chain — this module never accepts,
reads, or stores keys. Every public operation maps to a single read-only AWS
API call (Logs Insights being a start/poll pair) and is registered in the
operations allowlist; there is no generic SDK dispatch.
"""

import logging
import time
from typing import Any, Callable, Optional

from .base import (
    MAX_CHANGE_EVENTS,
    MAX_LOG_EVENTS,
    MAX_METRIC_DATAPOINTS,
    MAX_QUERY_ROWS,
    CloudIdentity,
    CloudOperationResult,
    CloudProvider,
    cap_list,
    cap_payload,
)

logger = logging.getLogger("kubently-executor.cloud.aws")

# Lookback clamp: 1 minute to 7 days
MAX_LOOKBACK_MINUTES = 7 * 24 * 60
# Synchronous Insights queries poll for at most this long before returning
# whatever is available (with the queryId so the caller can poll again).
INSIGHTS_SYNC_TIMEOUT_SECONDS = 25
EKS_LOG_TYPES = (
    "kube-apiserver",
    "kube-apiserver-audit",
    "authenticator",
    "kube-controller-manager",
    "kube-scheduler",
    "cloud-controller-manager",
)


def _time_range(params: dict[str, Any]) -> tuple[int, int]:
    """Resolve (start, end) epoch seconds from params (minutes lookback or explicit)."""
    end = int(params.get("end_time") or time.time())
    if params.get("start_time"):
        start = int(params["start_time"])
    else:
        minutes = int(params.get("minutes") or 60)
        minutes = max(1, min(minutes, MAX_LOOKBACK_MINUTES))
        start = end - minutes * 60
    return start, end


class AWSProvider(CloudProvider):
    """AWS implementation of the CloudProvider black box."""

    name = "aws"

    def __init__(
        self,
        region: Optional[str] = None,
        client_factory: Optional[Callable[[str], Any]] = None,
    ):
        """
        Args:
            region: AWS region override (defaults to the SDK's resolution:
                AWS_REGION / AWS_DEFAULT_REGION / IMDS).
            client_factory: injectable factory(service_name) -> client, used
                by tests to supply mocked clients. Defaults to boto3.
        """
        self._region = region
        self._clients: dict[str, Any] = {}
        if client_factory is not None:
            self._client_factory = client_factory
        else:
            self._client_factory = self._boto3_factory

        # operation name -> handler (the executable side of the allowlist)
        self._handlers: dict[str, Callable[[dict], CloudOperationResult]] = {
            "aws.sts.get_caller_identity": self._op_get_caller_identity,
            "aws.logs.insights_query": self._op_insights_query,
            "aws.logs.start_query": self._op_start_query,
            "aws.logs.get_query_results": self._op_get_query_results,
            "aws.logs.describe_log_groups": self._op_describe_log_groups,
            "aws.logs.filter_log_events": self._op_filter_log_events,
            "aws.metrics.get_metric_data": self._op_get_metric_data,
            "aws.eks.control_plane_logs": self._op_eks_control_plane_logs,
            "aws.cloudtrail.lookup_events": self._op_cloudtrail_lookup_events,
        }

    # ------------------------------------------------------------ plumbing

    def _boto3_factory(self, service: str):
        import boto3  # deferred: executor may run without cloud SDKs installed

        kwargs = {"region_name": self._region} if self._region else {}
        return boto3.client(service, **kwargs)

    def _client(self, service: str):
        if service not in self._clients:
            self._clients[service] = self._client_factory(service)
        return self._clients[service]

    def _run(
        self, operation: str, params: dict[str, Any], fn: Callable[[], dict]
    ) -> CloudOperationResult:
        """Execute fn, translating SDK errors into the standard result envelope."""
        try:
            data = fn()
            result = CloudOperationResult(
                success=True, operation=operation, provider=self.name, data=data
            )
            note = data.pop("_truncation_note", None) if isinstance(data, dict) else None
            if note:
                result.truncated = True
                result.truncation_note = note
            return cap_payload(result)
        except Exception as e:
            code = None
            # botocore ClientError carries a structured error code
            response = getattr(e, "response", None)
            if isinstance(response, dict):
                code = response.get("Error", {}).get("Code")
            return CloudOperationResult(
                success=False,
                operation=operation,
                provider=self.name,
                error=str(e),
                error_code=code,
            )

    # ----------------------------------------------------------- interface

    def detect_identity(self) -> Optional[CloudIdentity]:
        try:
            resp = self._client("sts").get_caller_identity()
            region = self._region
            if not region:
                try:
                    import boto3

                    region = boto3.Session().region_name
                except Exception:
                    region = None
            return CloudIdentity(
                provider="aws",
                account=resp.get("Account"),
                principal=resp.get("Arn"),
                region=region,
            )
        except Exception as e:
            logger.info(f"No usable AWS identity: {e}")
            return None

    def probe_permissions(self) -> dict[str, bool]:
        """
        Cheap read-only probe per operation family, using the same permissions
        the real operations need — so the advertised capabilities match what
        will actually work.
        """
        probes = {
            "identity": lambda: self._client("sts").get_caller_identity(),
            "logs": lambda: self._client("logs").describe_log_groups(limit=1),
            "metrics": lambda: self._client("cloudwatch").get_metric_data(
                MetricDataQueries=[
                    {
                        "Id": "kubently_probe",
                        "MetricStat": {
                            "Metric": {
                                "Namespace": "AWS/EC2",
                                "MetricName": "CPUUtilization",
                            },
                            "Period": 300,
                            "Stat": "Average",
                        },
                    }
                ],
                StartTime=time.time() - 600,
                EndTime=time.time(),
            ),
            "changes": lambda: self._client("cloudtrail").lookup_events(MaxResults=1),
        }
        usable = {}
        for family, probe in probes.items():
            try:
                probe()
                usable[family] = True
            except Exception as e:
                logger.info(f"AWS permission probe failed for family '{family}': {e}")
                usable[family] = False
        return usable

    def execute(self, operation: str, params: dict[str, Any]) -> CloudOperationResult:
        handler = self._handlers.get(operation)
        if handler is None:
            return CloudOperationResult(
                success=False,
                operation=operation,
                provider=self.name,
                error=f"Operation '{operation}' is not implemented by the AWS provider",
                error_code="UNKNOWN_OPERATION",
            )
        return handler(params or {})

    # ---------------------------------------------------------- operations

    def _op_get_caller_identity(self, params: dict) -> CloudOperationResult:
        def fn():
            resp = self._client("sts").get_caller_identity()
            return {"account": resp.get("Account"), "arn": resp.get("Arn")}

        return self._run("aws.sts.get_caller_identity", params, fn)

    def _op_start_query(self, params: dict) -> CloudOperationResult:
        def fn():
            start, end = _time_range(params)
            log_groups = params.get("log_groups") or [params["log_group"]]
            resp = self._client("logs").start_query(
                logGroupNames=log_groups[:5],
                startTime=start,
                endTime=end,
                queryString=params["query"],
                limit=min(int(params.get("limit") or MAX_QUERY_ROWS), MAX_QUERY_ROWS),
            )
            return {"query_id": resp["queryId"]}

        return self._run("aws.logs.start_query", params, fn)

    def _get_query_results(self, query_id: str) -> dict:
        resp = self._client("logs").get_query_results(queryId=query_id)
        rows = [
            {f["field"]: f["value"] for f in row if f.get("field") != "@ptr"}
            for row in resp.get("results", [])
        ]
        rows, note = cap_list(rows, MAX_QUERY_ROWS, "result rows")
        data = {
            "status": resp.get("status"),
            "rows": rows,
            "statistics": resp.get("statistics"),
        }
        if note:
            data["_truncation_note"] = note
        return data

    def _op_get_query_results(self, params: dict) -> CloudOperationResult:
        return self._run(
            "aws.logs.get_query_results",
            params,
            lambda: self._get_query_results(params["query_id"]),
        )

    def _op_insights_query(self, params: dict) -> CloudOperationResult:
        def fn():
            start, end = _time_range(params)
            log_groups = params.get("log_groups") or [params["log_group"]]
            client = self._client("logs")
            query_id = client.start_query(
                logGroupNames=log_groups[:5],
                startTime=start,
                endTime=end,
                queryString=params["query"],
                limit=min(int(params.get("limit") or MAX_QUERY_ROWS), MAX_QUERY_ROWS),
            )["queryId"]

            deadline = time.time() + INSIGHTS_SYNC_TIMEOUT_SECONDS
            data = {"status": "Running", "rows": []}
            while time.time() < deadline:
                data = self._get_query_results(query_id)
                if data.get("status") in ("Complete", "Failed", "Cancelled", "Timeout"):
                    break
                time.sleep(1)
            data["query_id"] = query_id
            if data.get("status") == "Running":
                data["_truncation_note"] = (
                    f"Query still running after {INSIGHTS_SYNC_TIMEOUT_SECONDS}s; "
                    f"partial results shown. Poll aws.logs.get_query_results with "
                    f"query_id '{query_id}' for the rest."
                )
            return data

        return self._run("aws.logs.insights_query", params, fn)

    def _op_describe_log_groups(self, params: dict) -> CloudOperationResult:
        def fn():
            kwargs = {"limit": min(int(params.get("limit") or 50), 50)}
            if params.get("prefix"):
                kwargs["logGroupNamePrefix"] = params["prefix"]
            resp = self._client("logs").describe_log_groups(**kwargs)
            groups = [
                {
                    "name": g.get("logGroupName"),
                    "stored_bytes": g.get("storedBytes"),
                    "retention_days": g.get("retentionInDays"),
                }
                for g in resp.get("logGroups", [])
            ]
            data = {"log_groups": groups}
            if resp.get("nextToken"):
                data["_truncation_note"] = (
                    "More log groups exist; refine with a name prefix."
                )
            return data

        return self._run("aws.logs.describe_log_groups", params, fn)

    def _filter_log_events(
        self, log_group: str, params: dict, stream_prefix: Optional[str] = None
    ) -> dict:
        start, end = _time_range(params)
        limit = min(int(params.get("limit") or MAX_LOG_EVENTS), MAX_LOG_EVENTS)
        kwargs = {
            "logGroupName": log_group,
            "startTime": start * 1000,
            "endTime": end * 1000,
            "limit": limit,
        }
        if params.get("filter_pattern"):
            kwargs["filterPattern"] = params["filter_pattern"]
        if stream_prefix:
            kwargs["logStreamNamePrefix"] = stream_prefix
        resp = self._client("logs").filter_log_events(**kwargs)
        events = [
            {
                "timestamp": e.get("timestamp"),
                "stream": e.get("logStreamName"),
                "message": e.get("message"),
            }
            for e in resp.get("events", [])
        ]
        events, note = cap_list(events, MAX_LOG_EVENTS, "log events")
        data = {"log_group": log_group, "events": events}
        if resp.get("nextToken") and not note:
            note = (
                f"More events match; showing first {len(events)}. Narrow the "
                f"time range or filter pattern to see the rest."
            )
        if note:
            data["_truncation_note"] = note
        return data

    def _op_filter_log_events(self, params: dict) -> CloudOperationResult:
        return self._run(
            "aws.logs.filter_log_events",
            params,
            lambda: self._filter_log_events(
                params["log_group"], params, params.get("stream_prefix")
            ),
        )

    def _op_eks_control_plane_logs(self, params: dict) -> CloudOperationResult:
        def fn():
            cluster = params["cluster_name"]
            log_type = params.get("log_type") or "kube-apiserver"
            if log_type not in EKS_LOG_TYPES:
                raise ValueError(
                    f"log_type must be one of {EKS_LOG_TYPES}, got '{log_type}'"
                )

            # Best-effort: report which control-plane log types are enabled so
            # an empty result is distinguishable from "logging is off".
            enabled_types = None
            try:
                desc = self._client("eks").describe_cluster(name=cluster)
                for entry in (
                    desc.get("cluster", {}).get("logging", {}).get("clusterLogging", [])
                ):
                    if entry.get("enabled"):
                        enabled_types = entry.get("types", [])
                        break
            except Exception as e:
                logger.info(f"eks:DescribeCluster unavailable ({e}); skipping status")

            data = self._filter_log_events(
                f"/aws/eks/{cluster}/cluster", params, stream_prefix=log_type
            )
            data["cluster"] = cluster
            data["log_type"] = log_type
            if enabled_types is not None:
                data["enabled_log_types"] = enabled_types
            return data

        return self._run("aws.eks.control_plane_logs", params, fn)

    def _op_get_metric_data(self, params: dict) -> CloudOperationResult:
        def fn():
            start, end = _time_range(params)
            queries = params.get("metric_data_queries")
            if not queries:
                # Simplified single-metric form
                dimensions = [
                    {"Name": k, "Value": v}
                    for k, v in (params.get("dimensions") or {}).items()
                ]
                queries = [
                    {
                        "Id": "m1",
                        "MetricStat": {
                            "Metric": {
                                "Namespace": params["namespace"],
                                "MetricName": params["metric_name"],
                                "Dimensions": dimensions,
                            },
                            "Period": int(params.get("period") or 300),
                            "Stat": params.get("stat") or "Average",
                        },
                    }
                ]
            resp = self._client("cloudwatch").get_metric_data(
                MetricDataQueries=queries[:10],
                StartTime=start,
                EndTime=end,
                MaxDatapoints=MAX_METRIC_DATAPOINTS,
            )
            series = []
            note = None
            for r in resp.get("MetricDataResults", []):
                points = list(zip(r.get("Timestamps", []), r.get("Values", [])))
                points, series_note = cap_list(
                    points, MAX_METRIC_DATAPOINTS, "datapoints"
                )
                note = note or series_note
                series.append(
                    {
                        "id": r.get("Id"),
                        "label": r.get("Label"),
                        "status": r.get("StatusCode"),
                        "datapoints": [
                            {"timestamp": str(t), "value": v} for t, v in points
                        ],
                    }
                )
            data = {"series": series}
            if note:
                data["_truncation_note"] = note
            return data

        return self._run("aws.metrics.get_metric_data", params, fn)

    def _op_cloudtrail_lookup_events(self, params: dict) -> CloudOperationResult:
        def fn():
            start, end = _time_range(params)
            limit = min(int(params.get("limit") or MAX_CHANGE_EVENTS), MAX_CHANGE_EVENTS)
            kwargs = {
                "StartTime": start,
                "EndTime": end,
                "MaxResults": min(limit, 50),  # API max is 50
            }
            # Optional single lookup attribute (CloudTrail allows one)
            for key, attr in (
                ("resource_name", "ResourceName"),
                ("event_name", "EventName"),
                ("username", "Username"),
            ):
                if params.get(key):
                    kwargs["LookupAttributes"] = [
                        {"AttributeKey": attr, "AttributeValue": params[key]}
                    ]
                    break
            resp = self._client("cloudtrail").lookup_events(**kwargs)
            events = []
            for e in resp.get("Events", []):
                events.append(
                    {
                        "event_time": str(e.get("EventTime")),
                        "event_name": e.get("EventName"),
                        "username": e.get("Username"),
                        "event_source": e.get("EventSource"),
                        "resources": [
                            {
                                "type": r.get("ResourceType"),
                                "name": r.get("ResourceName"),
                            }
                            for r in e.get("Resources", [])
                        ],
                        "read_only": e.get("ReadOnly"),
                    }
                )
            events, note = cap_list(events, MAX_CHANGE_EVENTS, "CloudTrail events")
            data = {"events": events}
            if resp.get("NextToken") and not note:
                note = (
                    f"More events match; showing first {len(events)}. Narrow the "
                    f"time range or add a resource/event filter."
                )
            if note:
                data["_truncation_note"] = note
            return data

        return self._run("aws.cloudtrail.lookup_events", params, fn)
