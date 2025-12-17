"""
Integration tests for executor using mocked kubectl subprocess calls.

These tests verify that the executor correctly interprets kubectl output
and produces appropriate responses, without requiring a real Kubernetes cluster.

Usage:
    pytest tests/integration/test_executor_mocked.py -v
    pytest tests/integration/test_executor_mocked.py -v -k crashloop
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add kubently root to path for imports
KUBENTLY_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(KUBENTLY_ROOT))

# Import from conftest (pytest auto-loads conftest.py fixtures)
# KubectlResponse is defined in our tests/conftest.py
from conftest import KubectlResponse
from fixtures.kubectl_scenarios import (
    SCENARIOS,
    get_scenario_names,
)


# =============================================================================
# Test Helpers
# =============================================================================

class KubectlRunner:
    """
    Standalone kubectl runner that mimics the executor's _run_kubectl method.

    This allows testing kubectl execution behavior without importing the full
    executor module with all its dependencies (sseclient, httpx, etc.).

    The implementation mirrors kubently/modules/executor/sse_executor.py:_run_kubectl
    """

    def _run_kubectl(self, args: list) -> dict:
        """
        Execute kubectl command.

        Args:
            args: kubectl command arguments

        Returns:
            Result dictionary with output and status
        """
        import subprocess

        try:
            # Prepend kubectl to args
            cmd = ["kubectl"] + args

            # Execute command
            process = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,  # 30 second timeout
            )

            # Combine stdout and stderr for output
            output = process.stdout
            if process.stderr:
                output += "\n" + process.stderr

            return {
                "success": process.returncode == 0,
                "output": output.strip(),
                "stderr": process.stderr,
                "status": "SUCCESS" if process.returncode == 0 else "FAILED",
                "return_code": process.returncode,
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "output": "",
                "stderr": "Command timed out",
                "status": "TIMEOUT",
                "return_code": -1,
            }
        except Exception as e:
            return {
                "success": False,
                "output": "",
                "stderr": str(e),
                "status": "ERROR",
                "return_code": -1,
            }


def create_executor_with_mocks():
    """
    Create a KubectlRunner instance for testing.

    This returns a lightweight object that has the same _run_kubectl interface
    as the real executor, but without all the SSE/HTTP dependencies.
    """
    return KubectlRunner()


# =============================================================================
# Basic Executor Tests
# =============================================================================

class TestExecutorKubectlExecution:
    """Test executor's kubectl command execution."""

    @pytest.mark.kubectl_mock
    def test_successful_get_pods(self, kubectl_mocker):
        """Test successful kubectl get pods execution."""
        kubectl_mocker.register("get pods", KubectlResponse(
            stdout="NAME    READY   STATUS    RESTARTS   AGE\napp-1   1/1     Running   0          1h",
            returncode=0
        ))

        executor = create_executor_with_mocks()
        result = executor._run_kubectl(["get", "pods"])

        assert result["success"] is True
        assert "app-1" in result["output"]
        assert result["return_code"] == 0
        assert kubectl_mocker.was_called_with("get pods")

    @pytest.mark.kubectl_mock
    def test_failed_kubectl_command(self, kubectl_mocker):
        """Test handling of failed kubectl commands."""
        kubectl_mocker.register("get nonexistent", KubectlResponse(
            stderr='error: the server doesn\'t have a resource type "nonexistent"',
            returncode=1
        ))

        executor = create_executor_with_mocks()
        result = executor._run_kubectl(["get", "nonexistent"])

        assert result["success"] is False
        assert result["return_code"] == 1

    @pytest.mark.kubectl_mock
    def test_kubectl_with_namespace(self, kubectl_mocker):
        """Test kubectl commands with namespace flag."""
        kubectl_mocker.register("-n kube-system get pods", KubectlResponse(
            stdout="NAME                       READY   STATUS    RESTARTS   AGE\ncoredns-abc123   1/1     Running   0          30d",
            returncode=0
        ))

        executor = create_executor_with_mocks()
        result = executor._run_kubectl(["-n", "kube-system", "get", "pods"])

        assert result["success"] is True
        assert "coredns" in result["output"]

    @pytest.mark.kubectl_mock
    def test_call_history_tracking(self, kubectl_mocker):
        """Test that all kubectl calls are tracked."""
        kubectl_mocker.register("get", KubectlResponse(stdout="ok"))

        executor = create_executor_with_mocks()
        executor._run_kubectl(["get", "pods"])
        executor._run_kubectl(["get", "services"])
        executor._run_kubectl(["get", "deployments"])

        assert kubectl_mocker.call_count == 3
        assert kubectl_mocker.was_called_with("get pods")
        assert kubectl_mocker.was_called_with("get services")
        assert kubectl_mocker.was_called_with("get deployments")


# =============================================================================
# Scenario-Based Tests
# =============================================================================

class TestCrashLoopBackOffScenario:
    """Test executor behavior with CrashLoopBackOff scenario."""

    @pytest.mark.kubectl_mock
    def test_identifies_crashloop_pods(self, kubectl_mocker):
        """Test that executor can identify pods in CrashLoopBackOff."""
        kubectl_mocker.register_scenario("crashloopbackoff")

        executor = create_executor_with_mocks()
        result = executor._run_kubectl(["get", "pods"])

        assert result["success"] is True
        assert "CrashLoopBackOff" in result["output"]
        assert "crashloop-app" in result["output"]

    @pytest.mark.kubectl_mock
    def test_can_retrieve_crashloop_logs(self, kubectl_mocker):
        """Test that executor can get logs from crashing pod."""
        kubectl_mocker.register_scenario("crashloopbackoff")

        executor = create_executor_with_mocks()
        result = executor._run_kubectl(["logs", "crashloop-app-7d4f5b6c8d"])

        assert result["success"] is True
        assert "Connection refused" in result["output"]
        assert "database" in result["output"].lower()

    @pytest.mark.kubectl_mock
    def test_describe_shows_restart_count(self, kubectl_mocker):
        """Test that describe shows high restart count."""
        kubectl_mocker.register_scenario("crashloopbackoff")

        executor = create_executor_with_mocks()
        result = executor._run_kubectl(["describe", "pod", "crashloop-app"])

        assert result["success"] is True
        assert "Restart Count:  5" in result["output"]
        assert "BackOff" in result["output"]


class TestImagePullBackOffScenario:
    """Test executor behavior with ImagePullBackOff scenario."""

    @pytest.mark.kubectl_mock
    def test_identifies_imagepull_failure(self, kubectl_mocker):
        """Test identification of ImagePullBackOff issues."""
        kubectl_mocker.register_scenario("imagepullbackoff")

        executor = create_executor_with_mocks()
        result = executor._run_kubectl(["get", "pods"])

        assert result["success"] is True
        assert "ImagePullBackOff" in result["output"]

    @pytest.mark.kubectl_mock
    def test_describe_shows_image_error(self, kubectl_mocker):
        """Test that describe reveals image pull error details."""
        kubectl_mocker.register_scenario("imagepullbackoff")

        executor = create_executor_with_mocks()
        result = executor._run_kubectl(["describe", "pod", "imagepull-app"])

        assert result["success"] is True
        assert "myregistry.io/myapp:v999" in result["output"]
        assert "not found" in result["output"]


class TestOOMKilledScenario:
    """Test executor behavior with OOMKilled scenario."""

    @pytest.mark.kubectl_mock
    def test_identifies_oom_killed(self, kubectl_mocker):
        """Test identification of OOMKilled pods."""
        kubectl_mocker.register_scenario("oomkilled")

        executor = create_executor_with_mocks()
        result = executor._run_kubectl(["get", "pods"])

        assert result["success"] is True
        assert "OOMKilled" in result["output"]

    @pytest.mark.kubectl_mock
    def test_describe_shows_memory_limits(self, kubectl_mocker):
        """Test that describe shows memory limits."""
        kubectl_mocker.register_scenario("oomkilled")

        executor = create_executor_with_mocks()
        result = executor._run_kubectl(["describe", "pod", "oom-app"])

        assert result["success"] is True
        assert "memory:  64Mi" in result["output"]
        assert "Exit Code:    137" in result["output"]


class TestPendingResourcesScenario:
    """Test executor behavior with resource-constrained pending pods."""

    @pytest.mark.kubectl_mock
    def test_identifies_pending_pod(self, kubectl_mocker):
        """Test identification of pending pods."""
        kubectl_mocker.register_scenario("pending_resources")

        executor = create_executor_with_mocks()
        result = executor._run_kubectl(["get", "pods"])

        assert result["success"] is True
        assert "Pending" in result["output"]

    @pytest.mark.kubectl_mock
    def test_describe_shows_scheduling_failure(self, kubectl_mocker):
        """Test that describe reveals scheduling failure reason."""
        kubectl_mocker.register_scenario("pending_resources")

        executor = create_executor_with_mocks()
        result = executor._run_kubectl(["describe", "pod", "pending-app"])

        assert result["success"] is True
        assert "Insufficient cpu" in result["output"]
        assert "Insufficient memory" in result["output"]

    @pytest.mark.kubectl_mock
    def test_node_describe_shows_capacity(self, kubectl_mocker):
        """Test that node describe shows available resources."""
        kubectl_mocker.register_scenario("pending_resources")

        executor = create_executor_with_mocks()
        result = executor._run_kubectl(["describe", "nodes"])

        assert result["success"] is True
        assert "cpu:" in result["output"]
        assert "memory:" in result["output"]


class TestServiceSelectorMismatchScenario:
    """Test executor behavior with service selector mismatch."""

    @pytest.mark.kubectl_mock
    def test_service_has_no_endpoints(self, kubectl_mocker):
        """Test detection of service with no endpoints."""
        kubectl_mocker.register_scenario("service_selector_mismatch")

        executor = create_executor_with_mocks()
        result = executor._run_kubectl(["get", "endpoints", "webapp"])

        assert result["success"] is True
        assert "<none>" in result["output"]

    @pytest.mark.kubectl_mock
    def test_service_selector_visible(self, kubectl_mocker):
        """Test that service selector is visible in describe."""
        kubectl_mocker.register_scenario("service_selector_mismatch")

        executor = create_executor_with_mocks()
        result = executor._run_kubectl(["describe", "svc", "webapp"])

        assert result["success"] is True
        assert "Selector:" in result["output"]
        assert "version=v1" in result["output"]

    @pytest.mark.kubectl_mock
    def test_pod_labels_visible(self, kubectl_mocker):
        """Test that pod labels show version mismatch."""
        kubectl_mocker.register_scenario("service_selector_mismatch")

        # Register more specific pattern with higher priority
        kubectl_mocker.register(
            "get pods --show-labels",
            KubectlResponse(
                stdout="""NAME                    READY   STATUS    RESTARTS   AGE   LABELS
webapp-abc123def456    1/1     Running   0          5m    app=webapp,version=v2"""
            ),
            priority=10  # Higher priority than scenario defaults
        )

        executor = create_executor_with_mocks()
        result = executor._run_kubectl(["get", "pods", "--show-labels"])

        assert result["success"] is True
        assert "version=v2" in result["output"]  # Pod has v2, service expects v1


# =============================================================================
# Parameterized Scenario Tests
# =============================================================================

@pytest.mark.kubectl_mock
@pytest.mark.parametrize("scenario_name", get_scenario_names())
def test_scenario_loads_correctly(kubectl_mocker, scenario_name):
    """Test that all scenarios can be loaded without errors."""
    kubectl_mocker.register_scenario(scenario_name)

    executor = create_executor_with_mocks()
    result = executor._run_kubectl(["get", "pods"])

    # All scenarios should have valid get pods response
    assert result["success"] is True
    assert len(result["output"]) > 0


@pytest.mark.kubectl_mock
@pytest.mark.parametrize("scenario_name,expected_status", [
    ("crashloopbackoff", "CrashLoopBackOff"),
    ("imagepullbackoff", "ImagePullBackOff"),
    ("oomkilled", "OOMKilled"),
    ("pending_resources", "Pending"),
    ("healthy", "Running"),
])
def test_scenario_status_detection(kubectl_mocker, scenario_name, expected_status):
    """Test that scenarios produce expected pod statuses."""
    kubectl_mocker.register_scenario(scenario_name)

    executor = create_executor_with_mocks()
    result = executor._run_kubectl(["get", "pods"])

    assert expected_status in result["output"]


# =============================================================================
# Advanced Mock Patterns
# =============================================================================

class TestCustomMockPatterns:
    """Test advanced mocking patterns."""

    @pytest.mark.kubectl_mock
    def test_regex_pattern_matching(self, kubectl_mocker):
        """Test regex pattern matching for flexible mocking."""
        import re

        kubectl_mocker.register(
            re.compile(r"get pods -n \w+"),
            KubectlResponse(stdout="NAME  STATUS\nregex-pod  Running")
        )

        executor = create_executor_with_mocks()

        # Should match any namespace
        result1 = executor._run_kubectl(["get", "pods", "-n", "default"])
        result2 = executor._run_kubectl(["get", "pods", "-n", "kube-system"])

        assert "regex-pod" in result1["output"]
        assert "regex-pod" in result2["output"]

    @pytest.mark.kubectl_mock
    def test_strict_mode_catches_unregistered(self, kubectl_mocker_strict):
        """Test strict mode fails on unregistered commands."""
        executor = create_executor_with_mocks()
        result = executor._run_kubectl(["get", "unregistered-resource"])

        assert result["success"] is False
        assert result["return_code"] == 127

    @pytest.mark.kubectl_mock
    def test_multiple_scenarios_combined(self, kubectl_mocker):
        """Test combining responses from multiple scenarios."""
        # Register specific responses from different scenarios
        from fixtures.kubectl_scenarios import CRASHLOOPBACKOFF, OOMKILLED

        kubectl_mocker.register("crashloop", CRASHLOOPBACKOFF["describe pod crashloop"])
        kubectl_mocker.register("oom", OOMKILLED["describe pod oom"])

        executor = create_executor_with_mocks()

        # Can query both
        result1 = executor._run_kubectl(["describe", "pod", "crashloop-app"])
        result2 = executor._run_kubectl(["describe", "pod", "oom-app"])

        assert "CrashLoopBackOff" in result1["output"]
        assert "OOMKilled" in result2["output"]

    @pytest.mark.kubectl_mock
    def test_sequential_responses(self, kubectl_mocker):
        """Test simulating state changes across multiple calls."""
        # First call: pod is pending
        kubectl_mocker.register("get pods", KubectlResponse(
            stdout="NAME    READY   STATUS    RESTARTS   AGE\napp-1   0/1     Pending   0          1m"
        ))

        executor = create_executor_with_mocks()
        result1 = executor._run_kubectl(["get", "pods"])
        assert "Pending" in result1["output"]

        # Clear and re-register with new state
        kubectl_mocker.clear()
        kubectl_mocker.register("get pods", KubectlResponse(
            stdout="NAME    READY   STATUS    RESTARTS   AGE\napp-1   1/1     Running   0          2m"
        ))

        result2 = executor._run_kubectl(["get", "pods"])
        assert "Running" in result2["output"]


# =============================================================================
# Command Parsing Tests
# =============================================================================

class TestCommandParsing:
    """Test that commands are correctly parsed and tracked."""

    @pytest.mark.kubectl_mock
    def test_tracks_full_command(self, kubectl_mocker):
        """Test that full command strings are tracked."""
        kubectl_mocker.register("get", KubectlResponse(stdout="ok"))

        executor = create_executor_with_mocks()
        executor._run_kubectl(["get", "pods", "-n", "production", "-o", "wide"])

        calls = kubectl_mocker.get_calls_matching("get pods")
        assert len(calls) == 1
        assert "-n" in calls[0].full_command_str
        assert "production" in calls[0].full_command_str
        assert "-o wide" in calls[0].full_command_str

    @pytest.mark.kubectl_mock
    def test_distinguishes_similar_commands(self, kubectl_mocker):
        """Test that similar commands are tracked separately."""
        kubectl_mocker.register("get pods", KubectlResponse(stdout="pods"))
        kubectl_mocker.register("get services", KubectlResponse(stdout="services"))

        executor = create_executor_with_mocks()
        executor._run_kubectl(["get", "pods"])
        executor._run_kubectl(["get", "services"])

        pod_calls = kubectl_mocker.get_calls_matching("get pods")
        svc_calls = kubectl_mocker.get_calls_matching("get services")

        assert len(pod_calls) == 1
        assert len(svc_calls) == 1
