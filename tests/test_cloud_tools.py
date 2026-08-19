#!/usr/bin/env python3
"""
Tests for the agent-side cloud tools (provider dispatch) and the cloud
capability passthrough (Track D1).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kubently.modules.a2a.protocol_bindings.a2a_server.cloud_tools import (
    build_changes_request,
    build_logs_request,
    build_metrics_request,
)
from kubently.modules.capability import ExecutorCapabilities
from kubently.modules.executor.cloud.operations import ALLOWED_CLOUD_OPERATIONS


class TestProviderDispatch:
    """The provider-agnostic tools must emit only allowlisted operations."""

    def test_aws_logs_without_group_discovers_groups_first(self):
        operation, params = build_logs_request("aws", "q", 60, None, 100)
        assert operation == "aws.logs.describe_log_groups"

    def test_aws_logs_with_group_runs_insights(self):
        operation, params = build_logs_request(
            "aws", "fields @message", 30, "/aws/eks/prod/cluster", 50
        )
        assert operation == "aws.logs.insights_query"
        assert params["log_group"] == "/aws/eks/prod/cluster"
        assert params["minutes"] == 30

    def test_aws_logs_empty_query_gets_default(self):
        _, params = build_logs_request("aws", "", 30, "g", 50)
        assert "fields @timestamp" in params["query"]

    def test_gcp_logs(self):
        operation, params = build_logs_request("gcp", 'severity>=ERROR', 30, None, 50)
        assert operation == "gcp.logging.list_entries"
        assert params["filter"] == 'severity>=ERROR'

    def test_aws_metrics_parses_namespace_and_metric(self):
        operation, params = build_metrics_request(
            "aws", "AWS/EC2:CPUUtilization", {"ClusterName": "p"}, "Average", 60, 300
        )
        assert operation == "aws.metrics.get_metric_data"
        assert params["namespace"] == "AWS/EC2"
        assert params["metric_name"] == "CPUUtilization"

    def test_aws_metrics_rejects_missing_namespace(self):
        with pytest.raises(ValueError):
            build_metrics_request("aws", "CPUUtilization", None, "Average", 60, 300)

    def test_gcp_metrics_builds_filter_from_dimensions(self):
        operation, params = build_metrics_request(
            "gcp",
            "kubernetes.io/container/cpu/core_usage_time",
            {"cluster_name": "prod"},
            "Average",
            60,
            300,
        )
        assert operation == "gcp.monitoring.list_time_series"
        assert 'metric.type="kubernetes.io/container/cpu/core_usage_time"' in params["filter"]
        assert 'resource.labels.cluster_name="prod"' in params["filter"]

    def test_changes_dispatch(self):
        aws_op, aws_params = build_changes_request("aws", 60, "sg-1", 50)
        gcp_op, gcp_params = build_changes_request("gcp", 60, "nginx", 50)
        assert aws_op == "aws.cloudtrail.lookup_events"
        assert aws_params["resource_name"] == "sg-1"
        assert gcp_op == "gcp.gke.audit_logs"
        assert gcp_params["resource_name"] == "nginx"

    def test_every_dispatched_operation_is_on_the_allowlist(self):
        emitted = [
            build_logs_request("aws", "q", 60, None, 100)[0],
            build_logs_request("aws", "q", 60, "g", 100)[0],
            build_logs_request("gcp", "q", 60, None, 100)[0],
            build_metrics_request("aws", "NS:M", None, "Average", 60, 300)[0],
            build_metrics_request("gcp", "m.type", None, "Average", 60, 300)[0],
            build_changes_request("aws", 60, None, 50)[0],
            build_changes_request("gcp", 60, None, 50)[0],
        ]
        for operation in emitted:
            assert operation in ALLOWED_CLOUD_OPERATIONS, operation


class TestCloudToolsRegistration:
    def test_disabled_via_env(self, monkeypatch):
        monkeypatch.setenv("KUBENTLY_CLOUD_TOOLS", "off")
        from kubently.modules.a2a.protocol_bindings.a2a_server.cloud_tools import (
            build_cloud_tools,
        )

        assert build_cloud_tools("http://x", lambda: "k", None, lambda: None) == []

    def test_enabled_by_default_builds_three_tools(self, monkeypatch):
        monkeypatch.delenv("KUBENTLY_CLOUD_TOOLS", raising=False)
        pytest.importorskip("langchain_core")
        from kubently.modules.a2a.protocol_bindings.a2a_server.cloud_tools import (
            build_cloud_tools,
        )

        tools = build_cloud_tools("http://x", lambda: "k", None, lambda: None)
        assert [t.name for t in tools] == [
            "query_cloud_logs",
            "query_cloud_metrics",
            "get_recent_cloud_changes",
        ]


class TestCloudToolsGate:
    """Registration gate (issue #90): cloud tools are offered to the model only
    when some registered executor advertises a cloud identity, the same rule
    LOKI_URL / PROMETHEUS_URL apply to their toolsets."""

    @pytest.fixture
    def redis(self):
        fakeredis = pytest.importorskip("fakeredis")
        return fakeredis.FakeAsyncRedis(decode_responses=True)

    async def _report(self, redis, cluster_id, cloud):
        from kubently.modules.capability import CapabilityModule

        await CapabilityModule(redis).store_capabilities(
            ExecutorCapabilities(
                cluster_id=cluster_id, mode="readOnly", allowed_verbs=["get"], cloud=cloud
            )
        )

    async def test_off_when_no_executor_reports_cloud(self, redis, monkeypatch):
        monkeypatch.delenv("KUBENTLY_CLOUD_TOOLS", raising=False)
        from kubently.modules.a2a.protocol_bindings.a2a_server.cloud_tools import (
            cloud_guidance,
            cloud_tools_enabled,
        )

        await self._report(redis, "kind", None)
        enabled = await cloud_tools_enabled(redis)
        assert enabled is False
        assert cloud_guidance(enabled) == ""

    async def test_on_when_an_executor_reports_cloud(self, redis, monkeypatch):
        monkeypatch.delenv("KUBENTLY_CLOUD_TOOLS", raising=False)
        from kubently.modules.a2a.protocol_bindings.a2a_server.cloud_tools import (
            cloud_guidance,
            cloud_tools_enabled,
        )

        await self._report(redis, "kind", None)
        await self._report(redis, "eks-prod", {"provider": "aws", "identity": "arn:..."})
        enabled = await cloud_tools_enabled(redis)
        assert enabled is True
        assert "query_cloud_metrics" in cloud_guidance(enabled)

    async def test_env_override_still_wins(self, redis, monkeypatch):
        monkeypatch.setenv("KUBENTLY_CLOUD_TOOLS", "off")
        from kubently.modules.a2a.protocol_bindings.a2a_server.cloud_tools import (
            cloud_tools_enabled,
        )

        await self._report(redis, "eks-prod", {"provider": "aws"})
        assert await cloud_tools_enabled(redis) is False

    def test_prompt_does_not_hardcode_cloud_guidance(self):
        """Guidance must arrive through {{cloud_guidance}}, so it disappears
        with the tools instead of describing tools that are not registered."""
        repo = os.path.join(os.path.dirname(__file__), "..")
        for path in (
            os.path.join(repo, "prompts", "system.prompt.yaml"),
            os.path.join(repo, "deployment", "helm", "kubently", "prompts", "system.prompt.yaml"),
        ):
            with open(path, encoding="utf-8") as f:
                content = f.read()
            assert "query_cloud_metrics" not in content, path
            assert "{{cloud_guidance}}" in content, path

    async def test_off_without_redis(self, monkeypatch):
        monkeypatch.delenv("KUBENTLY_CLOUD_TOOLS", raising=False)
        from kubently.modules.a2a.protocol_bindings.a2a_server.cloud_tools import (
            cloud_tools_enabled,
        )

        assert await cloud_tools_enabled(None) is False


class TestCapabilityCloudPassthrough:
    def test_round_trip_preserves_cloud_section(self):
        cloud = {
            "provider": "aws",
            "identity": {"provider": "aws", "account": "1", "principal": "arn:x"},
            "operations": ["aws.logs.insights_query"],
            "usable_families": {"logs": True},
            "checked_at": "2026-08-17T00:00:00+00:00",
        }
        capabilities = ExecutorCapabilities(
            cluster_id="c", mode="readOnly", allowed_verbs=["get"], cloud=cloud
        )
        restored = ExecutorCapabilities.from_dict(capabilities.to_dict())
        assert restored.cloud == cloud

    def test_cloud_defaults_to_none(self):
        capabilities = ExecutorCapabilities(
            cluster_id="c", mode="readOnly", allowed_verbs=["get"]
        )
        assert capabilities.cloud is None
        assert ExecutorCapabilities.from_dict(capabilities.to_dict()).cloud is None
