"""
GCP provider: read-only cloud telemetry via google-cloud SDKs and Workload
Identity Federation.

Credentials come exclusively from the pod's ambient identity (GKE Workload
Identity via the metadata server) through Application Default Credentials —
this module never accepts, reads, or stores keys. Every public operation maps
to a single read-only GCP API call and is registered in the operations
allowlist; there is no generic SDK dispatch.
"""

import logging
import time
from datetime import UTC, datetime
from typing import Any, Callable, Optional

from .base import (
    MAX_CHANGE_EVENTS,
    MAX_LOG_EVENTS,
    MAX_METRIC_DATAPOINTS,
    CloudIdentity,
    CloudOperationResult,
    CloudProvider,
    cap_list,
    cap_payload,
)

logger = logging.getLogger("kubently-executor.cloud.gcp")

MAX_LOOKBACK_MINUTES = 7 * 24 * 60
METADATA_BASE = "http://metadata.google.internal/computeMetadata/v1"
METADATA_HEADERS = {"Metadata-Flavor": "Google"}
METADATA_TIMEOUT = 2  # seconds — fail fast off-GCP


def _time_range(params: dict[str, Any]) -> tuple[datetime, datetime]:
    """Resolve (start, end) datetimes from params (minutes lookback or explicit epoch)."""
    end_epoch = int(params.get("end_time") or time.time())
    if params.get("start_time"):
        start_epoch = int(params["start_time"])
    else:
        minutes = int(params.get("minutes") or 60)
        minutes = max(1, min(minutes, MAX_LOOKBACK_MINUTES))
        start_epoch = end_epoch - minutes * 60
    return (
        datetime.fromtimestamp(start_epoch, tz=UTC),
        datetime.fromtimestamp(end_epoch, tz=UTC),
    )


def _ts_filter(start: datetime, end: datetime) -> str:
    """Cloud Logging timestamp range clause."""
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return f'timestamp>="{start.strftime(fmt)}" AND timestamp<="{end.strftime(fmt)}"'


class GCPProvider(CloudProvider):
    """GCP implementation of the CloudProvider black box."""

    name = "gcp"

    def __init__(
        self,
        project: Optional[str] = None,
        logging_client_factory: Optional[Callable[[], Any]] = None,
        monitoring_client_factory: Optional[Callable[[], Any]] = None,
        metadata_fetcher: Optional[Callable[[str], Optional[str]]] = None,
    ):
        """
        Args:
            project: GCP project id override (defaults to the metadata server /
                ADC project).
            *_client_factory / metadata_fetcher: injectable for tests.
        """
        self._project = project
        self._logging_client = None
        self._monitoring_client = None
        self._logging_client_factory = logging_client_factory or self._default_logging
        self._monitoring_client_factory = (
            monitoring_client_factory or self._default_monitoring
        )
        self._metadata_fetcher = metadata_fetcher or self._fetch_metadata

        self._handlers: dict[str, Callable[[dict], CloudOperationResult]] = {
            "gcp.logging.list_entries": self._op_list_entries,
            "gcp.monitoring.list_time_series": self._op_list_time_series,
            "gcp.gke.audit_logs": self._op_gke_audit_logs,
        }

    # ------------------------------------------------------------ plumbing

    @staticmethod
    def _fetch_metadata(path: str) -> Optional[str]:
        """Read one value from the GCE/GKE metadata server; None off-GCP."""
        import requests

        try:
            resp = requests.get(
                f"{METADATA_BASE}/{path}",
                headers=METADATA_HEADERS,
                timeout=METADATA_TIMEOUT,
            )
            if resp.status_code == 200:
                return resp.text.strip()
        except Exception:
            pass
        return None

    def _default_logging(self):
        from google.cloud import logging as gcp_logging  # deferred import

        return gcp_logging.Client(project=self.project)

    def _default_monitoring(self):
        from google.cloud import monitoring_v3  # deferred import

        return monitoring_v3.MetricServiceClient()

    @property
    def project(self) -> Optional[str]:
        if not self._project:
            self._project = self._metadata_fetcher("project/project-id")
        if not self._project:
            try:
                import google.auth

                _, self._project = google.auth.default()
            except Exception:
                pass
        return self._project

    def _logging(self):
        if self._logging_client is None:
            self._logging_client = self._logging_client_factory()
        return self._logging_client

    def _monitoring(self):
        if self._monitoring_client is None:
            self._monitoring_client = self._monitoring_client_factory()
        return self._monitoring_client

    def _run(
        self, operation: str, params: dict[str, Any], fn: Callable[[], dict]
    ) -> CloudOperationResult:
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
            return CloudOperationResult(
                success=False,
                operation=operation,
                provider=self.name,
                error=str(e),
                error_code=type(e).__name__,
            )

    # ----------------------------------------------------------- interface

    def detect_identity(self) -> Optional[CloudIdentity]:
        email = self._metadata_fetcher("instance/service-accounts/default/email")
        project = self.project
        if not email and not project:
            logger.info("No usable GCP identity (no metadata server, no ADC)")
            return None
        return CloudIdentity(provider="gcp", account=project, principal=email)

    def probe_permissions(self) -> dict[str, bool]:
        """Probe operation families with cheap read-only calls."""
        usable = {"identity": self.detect_identity() is not None}

        try:
            entries = self._logging().list_entries(
                filter_=_ts_filter(*_time_range({"minutes": 5})),
                page_size=1,
                max_results=1,
            )
            next(iter(entries), None)
            usable["logs"] = True
        except Exception as e:
            logger.info(f"GCP permission probe failed for family 'logs': {e}")
            usable["logs"] = False

        # GKE audit logs use the same logging.logEntries.list permission
        usable["changes"] = usable["logs"]

        try:
            self._list_time_series(
                {
                    "filter": 'metric.type="kubernetes.io/container/cpu/core_usage_time"',
                    "minutes": 5,
                }
            )
            usable["metrics"] = True
        except Exception as e:
            logger.info(f"GCP permission probe failed for family 'metrics': {e}")
            usable["metrics"] = False

        return usable

    def execute(self, operation: str, params: dict[str, Any]) -> CloudOperationResult:
        handler = self._handlers.get(operation)
        if handler is None:
            return CloudOperationResult(
                success=False,
                operation=operation,
                provider=self.name,
                error=f"Operation '{operation}' is not implemented by the GCP provider",
                error_code="UNKNOWN_OPERATION",
            )
        return handler(params or {})

    # ---------------------------------------------------------- operations

    def _serialize_entry(self, entry: Any) -> dict[str, Any]:
        payload = getattr(entry, "payload", None)
        if payload is not None and not isinstance(payload, (str, dict, list)):
            payload = str(payload)
        resource = getattr(entry, "resource", None)
        return {
            "timestamp": str(getattr(entry, "timestamp", "")),
            "severity": getattr(entry, "severity", None),
            "log_name": getattr(entry, "log_name", None),
            "resource_labels": dict(getattr(resource, "labels", {}) or {}),
            "payload": payload,
        }

    def _list_entries(self, filter_: str, limit: int) -> tuple[list, Optional[str]]:
        limit = min(limit, MAX_LOG_EVENTS)
        # Fetch one extra entry to detect (and report) truncation
        iterator = self._logging().list_entries(
            filter_=filter_,
            order_by="timestamp desc",
            page_size=min(limit + 1, 1000),
            max_results=limit + 1,
        )
        entries = [self._serialize_entry(e) for e in iterator]
        return cap_list(entries, limit, "log entries")

    def _op_list_entries(self, params: dict) -> CloudOperationResult:
        def fn():
            start, end = _time_range(params)
            clauses = [_ts_filter(start, end)]
            if params.get("filter"):
                clauses.append(f'({params["filter"]})')
            filter_ = " AND ".join(clauses)
            limit = min(int(params.get("limit") or MAX_LOG_EVENTS), MAX_LOG_EVENTS)
            entries, note = self._list_entries(filter_, limit)
            data = {"project": self.project, "filter": filter_, "entries": entries}
            if note:
                data["_truncation_note"] = note
            return data

        return self._run("gcp.logging.list_entries", params, fn)

    def _list_time_series(self, params: dict) -> dict:
        from google.cloud import monitoring_v3  # deferred import

        start, end = _time_range(params)
        project = params.get("project") or self.project
        if not project:
            raise ValueError("No GCP project available (set executor.cloud.gcpProject)")

        interval = monitoring_v3.TimeInterval(
            {
                "start_time": {"seconds": int(start.timestamp())},
                "end_time": {"seconds": int(end.timestamp())},
            }
        )
        request = {
            "name": f"projects/{project}",
            "filter": params["filter"],
            "interval": interval,
            "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
        }
        results = self._monitoring().list_time_series(request=request)

        series = []
        note = None
        for ts in results:
            if len(series) >= 20:
                note = "More than 20 time series matched; showing first 20. Tighten the filter."
                break
            points = []
            for p in getattr(ts, "points", []):
                value = getattr(p, "value", None)
                # TypedValue is a proto oneof; WhichOneof tells us which member
                # is actually set (0.0 is a legitimate value, so no attr-scanning)
                v = None
                if hasattr(value, "WhichOneof"):
                    try:
                        which = value.WhichOneof("value")
                        if which:
                            v = getattr(value, which)
                    except Exception:
                        v = getattr(value, "double_value", None)
                elif value is not None:
                    for attr in ("double_value", "int64_value", "bool_value"):
                        if hasattr(value, attr):
                            v = getattr(value, attr)
                            break
                end_time = getattr(getattr(p, "interval", None), "end_time", None)
                points.append({"timestamp": str(end_time), "value": v})
            points, p_note = cap_list(points, MAX_METRIC_DATAPOINTS, "datapoints")
            note = note or p_note
            metric = getattr(ts, "metric", None)
            resource = getattr(ts, "resource", None)
            series.append(
                {
                    "metric_type": getattr(metric, "type", None),
                    "metric_labels": dict(getattr(metric, "labels", {}) or {}),
                    "resource_labels": dict(getattr(resource, "labels", {}) or {}),
                    "points": points,
                }
            )
        data = {"project": project, "series": series}
        if note:
            data["_truncation_note"] = note
        return data

    def _op_list_time_series(self, params: dict) -> CloudOperationResult:
        return self._run(
            "gcp.monitoring.list_time_series", params, lambda: self._list_time_series(params)
        )

    def _op_gke_audit_logs(self, params: dict) -> CloudOperationResult:
        def fn():
            start, end = _time_range(params)
            project = params.get("project") or self.project
            clauses = [
                _ts_filter(start, end),
                'resource.type="k8s_cluster"',
                f'logName="projects/{project}/logs/cloudaudit.googleapis.com%2Factivity"',
            ]
            if params.get("cluster_name"):
                clauses.append(
                    f'resource.labels.cluster_name="{params["cluster_name"]}"'
                )
            if params.get("resource_name"):
                clauses.append(
                    f'protoPayload.resourceName:"{params["resource_name"]}"'
                )
            filter_ = " AND ".join(clauses)
            limit = min(int(params.get("limit") or MAX_CHANGE_EVENTS), MAX_CHANGE_EVENTS)
            entries, note = self._list_entries(filter_, limit)
            data = {"project": project, "filter": filter_, "entries": entries}
            if note:
                data["_truncation_note"] = note
            return data

        return self._run("gcp.gke.audit_logs", params, fn)
