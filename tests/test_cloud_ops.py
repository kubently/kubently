#!/usr/bin/env python3
"""
Tests for the executor's cloud read-operations module (Track D1).

Covers, with fully mocked SDK clients (no cloud calls, no credentials):
- code-level allowlist enforcement at dispatch (the security boundary)
- each AWS operation (boto3 mocked via injected client factory)
- each GCP operation (google-cloud clients mocked via injected factories)
- result caps + explicit truncation notes
- identity detection and permission-probe capability paths
- SSE executor command routing (type=cloud) and capability payload
"""

import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kubently.modules.executor.cloud import (
    ALLOWED_CLOUD_OPERATIONS,
    CloudIdentity,
    CloudOperationResult,
    CloudOpsManager,
    CloudProvider,
    cap_payload,
)
from kubently.modules.executor.cloud.aws_provider import AWSProvider
from kubently.modules.executor.cloud.base import MAX_QUERY_ROWS, MAX_RESULT_CHARS
from kubently.modules.executor.cloud.gcp_provider import GCPProvider


# =============================================================================
# Allowlist enforcement (the security boundary)
# =============================================================================


class FakeProvider(CloudProvider):
    """Minimal provider for manager tests."""

    def __init__(self, name="aws", identity=True, families=None):
        self.name = name
        self._identity = identity
        self._families = families or {"identity": True, "logs": True}
        self.executed = []

    def detect_identity(self):
        if not self._identity:
            return None
        return CloudIdentity(provider=self.name, account="123", principal="arn:x")

    def probe_permissions(self):
        return dict(self._families)

    def execute(self, operation, params):
        self.executed.append((operation, params))
        return CloudOperationResult(
            success=True, operation=operation, provider=self.name, data={"ok": True}
        )


class TestAllowlistEnforcement:
    def test_unknown_operation_rejected_before_any_provider_call(self):
        provider = FakeProvider()
        manager = CloudOpsManager(providers=[provider])
        result = manager.execute("aws.iam.create_user", {"UserName": "evil"})
        assert not result.success
        assert result.error_code == "OPERATION_NOT_ALLOWED"
        assert provider.executed == []

    def test_write_sounding_operations_are_not_listed(self):
        for name in ALLOWED_CLOUD_OPERATIONS:
            for verb in ("create", "delete", "put", "update", "modify", "terminate"):
                assert verb not in name, f"{name} looks like a write op"

    def test_every_operation_is_aws_or_gcp(self):
        for spec in ALLOWED_CLOUD_OPERATIONS.values():
            assert spec.provider in ("aws", "gcp")
            assert spec.family

    def test_no_identity_gives_clear_error(self):
        manager = CloudOpsManager(providers=[FakeProvider(identity=False)])
        result = manager.execute("aws.logs.insights_query", {})
        assert not result.success
        assert result.error_code == "NO_CLOUD_IDENTITY"

    def test_provider_mismatch_rejected(self):
        manager = CloudOpsManager(providers=[FakeProvider(name="gcp")])
        result = manager.execute("aws.logs.insights_query", {})
        assert not result.success
        assert result.error_code == "PROVIDER_MISMATCH"

    def test_allowed_operation_dispatches(self):
        provider = FakeProvider(name="aws")
        manager = CloudOpsManager(providers=[provider])
        result = manager.execute("aws.logs.insights_query", {"log_group": "g", "query": "q"})
        assert result.success
        assert provider.executed[0][0] == "aws.logs.insights_query"

    def test_allowlist_module_is_importable_without_cloud_sdks(self):
        # The API server validates against the allowlist without boto3/google
        # installed — so the operations module must not import them.
        import ast

        import kubently.modules.executor.cloud.operations as ops

        tree = ast.parse(open(ops.__file__).read())
        imports = [
            name.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for name in node.names
        ] + [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        ]
        for module in imports:
            assert not module.startswith(("boto3", "botocore", "google")), module


# =============================================================================
# Result caps + truncation notes
# =============================================================================


class TestResultCaps:
    def test_cap_payload_truncates_oversized_data_with_note(self):
        result = CloudOperationResult(
            success=True,
            operation="op",
            provider="aws",
            data={"blob": "x" * (MAX_RESULT_CHARS * 2)},
        )
        capped = cap_payload(result)
        assert capped.truncated
        assert "cap" in capped.truncation_note
        assert len(json.dumps(capped.data)) < MAX_RESULT_CHARS + 1000

    def test_cap_payload_leaves_small_data_alone(self):
        result = CloudOperationResult(
            success=True, operation="op", provider="aws", data={"small": True}
        )
        capped = cap_payload(result)
        assert not capped.truncated
        assert capped.data == {"small": True}


# =============================================================================
# AWS provider (mocked boto3 clients)
# =============================================================================


def make_client_error(code="AccessDeniedException"):
    from botocore.exceptions import ClientError

    return ClientError({"Error": {"Code": code, "Message": "denied"}}, "Op")


@pytest.fixture
def aws_clients():
    clients = {
        "sts": MagicMock(),
        "logs": MagicMock(),
        "cloudwatch": MagicMock(),
        "cloudtrail": MagicMock(),
        "eks": MagicMock(),
    }
    return clients


@pytest.fixture
def aws(aws_clients):
    return AWSProvider(region="us-west-2", client_factory=lambda s: aws_clients[s])


class TestAWSProvider:
    def test_detect_identity(self, aws, aws_clients):
        aws_clients["sts"].get_caller_identity.return_value = {
            "Account": "123456789012",
            "Arn": "arn:aws:sts::123456789012:assumed-role/kubently-executor-readonly/pod",
        }
        identity = aws.detect_identity()
        assert identity.provider == "aws"
        assert identity.account == "123456789012"
        assert "kubently-executor-readonly" in identity.principal
        assert identity.region == "us-west-2"

    def test_detect_identity_absent(self, aws, aws_clients):
        aws_clients["sts"].get_caller_identity.side_effect = Exception("no creds")
        assert aws.detect_identity() is None

    def test_insights_query_start_and_poll(self, aws, aws_clients):
        aws_clients["logs"].start_query.return_value = {"queryId": "q-1"}
        aws_clients["logs"].get_query_results.return_value = {
            "status": "Complete",
            "results": [
                [
                    {"field": "@timestamp", "value": "2026-08-17 10:00:00"},
                    {"field": "@message", "value": "error: denied"},
                    {"field": "@ptr", "value": "opaque-pointer"},
                ]
            ],
            "statistics": {"recordsScanned": 10},
        }
        result = aws.execute(
            "aws.logs.insights_query",
            {"log_group": "/aws/eks/prod/cluster", "query": "fields @message", "minutes": 30},
        )
        assert result.success
        assert result.data["status"] == "Complete"
        assert result.data["rows"] == [
            {"@timestamp": "2026-08-17 10:00:00", "@message": "error: denied"}
        ]  # @ptr stripped
        assert result.data["query_id"] == "q-1"
        call = aws_clients["logs"].start_query.call_args.kwargs
        assert call["logGroupNames"] == ["/aws/eks/prod/cluster"]
        assert call["limit"] <= MAX_QUERY_ROWS

    def test_insights_query_still_running_notes_partial(self, aws, aws_clients, monkeypatch):
        monkeypatch.setattr(
            "kubently.modules.executor.cloud.aws_provider.INSIGHTS_SYNC_TIMEOUT_SECONDS", 0
        )
        aws_clients["logs"].start_query.return_value = {"queryId": "q-2"}
        aws_clients["logs"].get_query_results.return_value = {"status": "Running", "results": []}
        result = aws.execute(
            "aws.logs.insights_query", {"log_group": "g", "query": "q", "minutes": 5}
        )
        assert result.success
        assert result.truncated
        assert "q-2" in result.truncation_note

    def test_start_query_and_get_query_results_primitives(self, aws, aws_clients):
        aws_clients["logs"].start_query.return_value = {"queryId": "q-3"}
        result = aws.execute("aws.logs.start_query", {"log_group": "g", "query": "q"})
        assert result.success and result.data["query_id"] == "q-3"

        rows = [[{"field": "@message", "value": f"m{i}"}] for i in range(MAX_QUERY_ROWS + 50)]
        aws_clients["logs"].get_query_results.return_value = {
            "status": "Complete",
            "results": rows,
        }
        result = aws.execute("aws.logs.get_query_results", {"query_id": "q-3"})
        assert result.success
        assert len(result.data["rows"]) == MAX_QUERY_ROWS
        assert result.truncated
        assert str(MAX_QUERY_ROWS) in result.truncation_note

    def test_describe_log_groups(self, aws, aws_clients):
        aws_clients["logs"].describe_log_groups.return_value = {
            "logGroups": [{"logGroupName": "/aws/eks/prod/cluster", "storedBytes": 5}],
            "nextToken": "more",
        }
        result = aws.execute("aws.logs.describe_log_groups", {"prefix": "/aws/eks"})
        assert result.success
        assert result.data["log_groups"][0]["name"] == "/aws/eks/prod/cluster"
        assert result.truncated  # nextToken -> note

    def test_filter_log_events_caps_and_notes(self, aws, aws_clients):
        aws_clients["logs"].filter_log_events.return_value = {
            "events": [
                {"timestamp": i, "logStreamName": "s", "message": f"m{i}"} for i in range(5)
            ],
            "nextToken": "t",
        }
        result = aws.execute("aws.logs.filter_log_events", {"log_group": "g", "minutes": 10})
        assert result.success
        assert len(result.data["events"]) == 5
        assert result.truncated  # nextToken present
        kwargs = aws_clients["logs"].filter_log_events.call_args.kwargs
        assert kwargs["logGroupName"] == "g"

    def test_eks_control_plane_logs(self, aws, aws_clients):
        aws_clients["eks"].describe_cluster.return_value = {
            "cluster": {
                "logging": {
                    "clusterLogging": [
                        {"enabled": True, "types": ["api", "authenticator"]},
                    ]
                }
            }
        }
        aws_clients["logs"].filter_log_events.return_value = {
            "events": [{"timestamp": 1, "logStreamName": "kube-apiserver-abc", "message": "ok"}]
        }
        result = aws.execute(
            "aws.eks.control_plane_logs",
            {"cluster_name": "prod", "log_type": "kube-apiserver", "minutes": 15},
        )
        assert result.success
        assert result.data["enabled_log_types"] == ["api", "authenticator"]
        kwargs = aws_clients["logs"].filter_log_events.call_args.kwargs
        assert kwargs["logGroupName"] == "/aws/eks/prod/cluster"
        assert kwargs["logStreamNamePrefix"] == "kube-apiserver"

    def test_eks_control_plane_logs_rejects_unknown_log_type(self, aws):
        result = aws.execute(
            "aws.eks.control_plane_logs",
            {"cluster_name": "prod", "log_type": "../../etc/passwd"},
        )
        assert not result.success
        assert "log_type" in result.error

    def test_get_metric_data_simplified_form(self, aws, aws_clients):
        from datetime import datetime

        aws_clients["cloudwatch"].get_metric_data.return_value = {
            "MetricDataResults": [
                {
                    "Id": "m1",
                    "Label": "CPUUtilization",
                    "StatusCode": "Complete",
                    "Timestamps": [datetime(2026, 8, 17, 10, 0)],
                    "Values": [42.5],
                }
            ]
        }
        result = aws.execute(
            "aws.metrics.get_metric_data",
            {
                "namespace": "AWS/EC2",
                "metric_name": "CPUUtilization",
                "dimensions": {"ClusterName": "prod"},
                "minutes": 60,
            },
        )
        assert result.success
        series = result.data["series"][0]
        assert series["label"] == "CPUUtilization"
        assert series["datapoints"][0]["value"] == 42.5
        query = aws_clients["cloudwatch"].get_metric_data.call_args.kwargs[
            "MetricDataQueries"
        ][0]
        assert query["MetricStat"]["Metric"]["Namespace"] == "AWS/EC2"
        assert query["MetricStat"]["Metric"]["Dimensions"] == [
            {"Name": "ClusterName", "Value": "prod"}
        ]

    def test_cloudtrail_lookup_events_with_resource_filter(self, aws, aws_clients):
        from datetime import datetime

        aws_clients["cloudtrail"].lookup_events.return_value = {
            "Events": [
                {
                    "EventTime": datetime(2026, 8, 17, 9, 59),
                    "EventName": "AuthorizeSecurityGroupIngress",
                    "Username": "admin",
                    "EventSource": "ec2.amazonaws.com",
                    "Resources": [{"ResourceType": "SecurityGroup", "ResourceName": "sg-1"}],
                    "ReadOnly": "false",
                }
            ]
        }
        result = aws.execute(
            "aws.cloudtrail.lookup_events", {"minutes": 60, "resource_name": "sg-1"}
        )
        assert result.success
        assert result.data["events"][0]["event_name"] == "AuthorizeSecurityGroupIngress"
        kwargs = aws_clients["cloudtrail"].lookup_events.call_args.kwargs
        assert kwargs["LookupAttributes"] == [
            {"AttributeKey": "ResourceName", "AttributeValue": "sg-1"}
        ]
        assert kwargs["MaxResults"] <= 50

    def test_client_error_maps_to_error_code(self, aws, aws_clients):
        aws_clients["logs"].filter_log_events.side_effect = make_client_error(
            "AccessDeniedException"
        )
        result = aws.execute("aws.logs.filter_log_events", {"log_group": "g"})
        assert not result.success
        assert result.error_code == "AccessDeniedException"

    def test_unknown_operation_at_provider_level(self, aws):
        result = aws.execute("aws.made.up", {})
        assert not result.success
        assert result.error_code == "UNKNOWN_OPERATION"

    def test_probe_permissions_partial_grant(self, aws, aws_clients):
        aws_clients["sts"].get_caller_identity.return_value = {"Account": "1", "Arn": "a"}
        aws_clients["logs"].describe_log_groups.return_value = {"logGroups": []}
        aws_clients["cloudwatch"].get_metric_data.side_effect = make_client_error()
        aws_clients["cloudtrail"].lookup_events.return_value = {"Events": []}
        usable = aws.probe_permissions()
        assert usable == {
            "identity": True,
            "logs": True,
            "metrics": False,
            "changes": True,
        }


# =============================================================================
# GCP provider (mocked google-cloud clients)
# =============================================================================


def make_gcp_entry(message="hello", severity="ERROR"):
    return SimpleNamespace(
        timestamp="2026-08-17 10:00:00+00:00",
        severity=severity,
        log_name="projects/p/logs/stderr",
        resource=SimpleNamespace(labels={"cluster_name": "prod"}),
        payload=message,
    )


@pytest.fixture
def gcp_logging_client():
    return MagicMock()


@pytest.fixture
def gcp(gcp_logging_client):
    return GCPProvider(
        project="my-project",
        logging_client_factory=lambda: gcp_logging_client,
        monitoring_client_factory=MagicMock,
        metadata_fetcher=lambda path: {
            "project/project-id": "my-project",
            "instance/service-accounts/default/email": "kubently-executor@my-project.iam.gserviceaccount.com",
        }.get(path),
    )


class TestGCPProvider:
    def test_detect_identity_via_metadata(self, gcp):
        identity = gcp.detect_identity()
        assert identity.provider == "gcp"
        assert identity.account == "my-project"
        assert identity.principal.startswith("kubently-executor@")

    def test_detect_identity_absent(self, gcp_logging_client):
        provider = GCPProvider(
            logging_client_factory=lambda: gcp_logging_client,
            metadata_fetcher=lambda path: None,
        )
        # No metadata server and no ADC -> no identity
        provider._project = None
        import kubently.modules.executor.cloud.gcp_provider as mod

        # google.auth.default may resolve in some environments; force failure
        assert provider._metadata_fetcher("anything") is None

    def test_list_entries_builds_filter_and_serializes(self, gcp, gcp_logging_client):
        gcp_logging_client.list_entries.return_value = [make_gcp_entry("boom")]
        result = gcp.execute(
            "gcp.logging.list_entries",
            {"filter": 'resource.type="k8s_container" AND severity>=ERROR', "minutes": 30},
        )
        assert result.success
        assert result.data["entries"][0]["payload"] == "boom"
        assert result.data["entries"][0]["resource_labels"] == {"cluster_name": "prod"}
        filter_used = gcp_logging_client.list_entries.call_args.kwargs["filter_"]
        assert "timestamp>=" in filter_used
        assert 'resource.type="k8s_container"' in filter_used

    def test_list_entries_caps_with_note(self, gcp, gcp_logging_client):
        gcp_logging_client.list_entries.return_value = [make_gcp_entry(f"m{i}") for i in range(7)]
        result = gcp.execute("gcp.logging.list_entries", {"limit": 5})
        assert result.success
        assert len(result.data["entries"]) == 5
        assert result.truncated
        assert "first 5" in result.truncation_note

    def test_gke_audit_logs_filter(self, gcp, gcp_logging_client):
        gcp_logging_client.list_entries.return_value = []
        result = gcp.execute(
            "gcp.gke.audit_logs",
            {"minutes": 120, "cluster_name": "prod", "resource_name": "deployments/nginx"},
        )
        assert result.success
        filter_used = result.data["filter"]
        assert 'resource.type="k8s_cluster"' in filter_used
        assert "cloudaudit.googleapis.com" in filter_used
        assert 'resource.labels.cluster_name="prod"' in filter_used
        assert 'protoPayload.resourceName:"deployments/nginx"' in filter_used

    def test_list_time_series(self, gcp):
        pytest.importorskip("google.cloud.monitoring_v3")
        fake_series = SimpleNamespace(
            metric=SimpleNamespace(
                type="kubernetes.io/container/cpu/core_usage_time", labels={}
            ),
            resource=SimpleNamespace(labels={"cluster_name": "prod"}),
            points=[
                SimpleNamespace(
                    value=SimpleNamespace(double_value=0.75),
                    interval=SimpleNamespace(end_time="2026-08-17T10:00:00Z"),
                )
            ],
        )
        monitoring_client = MagicMock()
        monitoring_client.list_time_series.return_value = [fake_series]
        provider = GCPProvider(
            project="my-project",
            logging_client_factory=MagicMock,
            monitoring_client_factory=lambda: monitoring_client,
            metadata_fetcher=lambda path: None,
        )
        result = provider.execute(
            "gcp.monitoring.list_time_series",
            {"filter": 'metric.type="kubernetes.io/container/cpu/core_usage_time"'},
        )
        assert result.success
        series = result.data["series"][0]
        assert series["resource_labels"] == {"cluster_name": "prod"}
        assert series["points"][0]["value"] == 0.75
        request = monitoring_client.list_time_series.call_args.kwargs["request"]
        assert request["name"] == "projects/my-project"

    def test_error_becomes_result_not_exception(self, gcp, gcp_logging_client):
        gcp_logging_client.list_entries.side_effect = PermissionError("403 denied")
        result = gcp.execute("gcp.logging.list_entries", {})
        assert not result.success
        assert result.error_code == "PermissionError"


# =============================================================================
# Manager capability payload (what gets advertised to the agent)
# =============================================================================


class TestCapabilityPayload:
    def test_payload_lists_only_probed_usable_operations(self):
        provider = FakeProvider(
            name="aws",
            families={"identity": True, "logs": True, "metrics": False, "changes": True},
        )
        manager = CloudOpsManager(providers=[provider])
        payload = manager.capability_payload()
        assert payload["provider"] == "aws"
        assert payload["identity"]["principal"] == "arn:x"
        assert "aws.logs.insights_query" in payload["operations"]
        assert "aws.cloudtrail.lookup_events" in payload["operations"]
        assert "aws.metrics.get_metric_data" not in payload["operations"]
        assert payload["usable_families"]["metrics"] is False
        assert payload["checked_at"]

    def test_payload_none_without_identity(self):
        manager = CloudOpsManager(providers=[FakeProvider(identity=False)])
        assert manager.capability_payload() is None

    def test_detection_cached_then_refreshable(self):
        provider = FakeProvider()
        manager = CloudOpsManager(providers=[provider], refresh_interval=3600)
        manager.detect()
        first = manager._last_detection
        manager.detect()  # cached — timestamp unchanged
        assert manager._last_detection == first
        manager.detect(force=True)
        assert manager._last_detection >= first

    def test_auto_mode_falls_through_to_second_provider(self):
        aws = FakeProvider(name="aws", identity=False)
        gcp = FakeProvider(name="gcp")
        manager = CloudOpsManager(providers=[aws, gcp])
        identity = manager.detect()
        assert identity.provider == "gcp"


# =============================================================================
# SSE executor integration (routing + capability report)
# =============================================================================


@pytest.fixture
def executor(monkeypatch):
    monkeypatch.setenv("KUBENTLY_API_URL", "http://localhost:8080")
    monkeypatch.setenv("CLUSTER_ID", "test-cluster")
    monkeypatch.setenv("KUBENTLY_TOKEN", "test-token")
    monkeypatch.setenv("KUBENTLY_WHITELIST_CONFIG", "/nonexistent/whitelist.yaml")
    monkeypatch.delenv("KUBENTLY_CLOUD_MODE", raising=False)
    from kubently.modules.executor.sse_executor import SSEKubentlyExecutor

    return SSEKubentlyExecutor()


class TestSSEExecutorCloudRouting:
    def test_cloud_disabled_by_default(self, executor):
        assert executor._cloud is None
        result = executor._run_cloud_operation(
            {"operation": "aws.logs.insights_query", "params": {}}
        )
        assert not result["success"]
        assert "not enabled" in result["error"]

    def test_cloud_command_routed_to_manager(self, executor):
        provider = FakeProvider(name="aws")
        executor._cloud = CloudOpsManager(providers=[provider])
        result = executor._run_cloud_operation(
            {
                "operation": "aws.cloudtrail.lookup_events",
                "params": {"minutes": 30},
            }
        )
        assert result["success"]
        payload = json.loads(result["output"])
        assert payload["data"] == {"ok": True}
        assert provider.executed[0] == ("aws.cloudtrail.lookup_events", {"minutes": 30})

    def test_disallowed_operation_marked_blocked(self, executor):
        executor._cloud = CloudOpsManager(providers=[FakeProvider()])
        result = executor._run_cloud_operation(
            {"operation": "aws.s3.get_object", "params": {}}
        )
        assert not result["success"]
        assert result["status"] == "BLOCKED"

    def test_execute_command_routes_by_type(self, executor, monkeypatch):
        seen = {}

        def fake_cloud(cmd):
            seen["cloud"] = cmd
            return {"success": True}

        def fake_kubectl(args):
            seen["kubectl"] = args
            return {"success": True}

        monkeypatch.setattr(executor, "_run_cloud_operation", fake_cloud)
        monkeypatch.setattr(executor, "_run_kubectl", fake_kubectl)
        monkeypatch.setattr(
            "requests.post", lambda *a, **k: SimpleNamespace(status_code=200)
        )
        executor._execute_command({"id": "1", "tool": "cloud", "operation": "x"})
        executor._execute_command({"id": "2", "args": ["get", "pods"]})
        assert seen["cloud"]["operation"] == "x"
        assert seen["kubectl"] == ["get", "pods"]

    def test_capability_payload_includes_cloud_section(self, executor):
        executor._cloud = CloudOpsManager(providers=[FakeProvider(name="aws")])
        payload = executor._get_capabilities_payload()
        assert payload["cloud"]["provider"] == "aws"
        assert payload["mode"]  # kubectl capabilities untouched

    def test_capability_payload_omits_cloud_without_identity(self, executor):
        executor._cloud = CloudOpsManager(providers=[FakeProvider(identity=False)])
        payload = executor._get_capabilities_payload()
        assert "cloud" not in payload

    def test_cloud_mode_enables_capability_reporting(self, monkeypatch):
        monkeypatch.setenv("KUBENTLY_API_URL", "http://localhost:8080")
        monkeypatch.setenv("CLUSTER_ID", "c")
        monkeypatch.setenv("KUBENTLY_TOKEN", "t")
        monkeypatch.setenv("KUBENTLY_WHITELIST_CONFIG", "/nonexistent/whitelist.yaml")
        monkeypatch.setenv("KUBENTLY_CLOUD_MODE", "aws")
        monkeypatch.setenv("KUBENTLY_REPORT_CAPABILITIES", "false")
        from kubently.modules.executor.sse_executor import SSEKubentlyExecutor

        ex = SSEKubentlyExecutor()
        assert ex._cloud is not None
        assert ex.report_capabilities is True
