"""
Unit tests for Kubently data models.
"""

import os
import sys
from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.models import (
    AgentCommand,
    Command,
    CommandResult,
    CommandType,
    CreateSessionRequest,
    ExecuteCommandRequest,
    ExecutionStatus,
    KubentlyConfig,
    Session,
    SessionStatus,
    validate_cluster_id,
    validate_kubectl_args,
)


class TestCreateSessionRequest:
    """Test session creation request model."""

    def test_valid_session_request(self):
        """Test creating a valid session request."""
        request = CreateSessionRequest(
            cluster_id="production-cluster-1", user_id="user123", ttl_seconds=300
        )
        assert request.cluster_id == "production-cluster-1"
        assert request.user_id == "user123"
        assert request.ttl_seconds == 300

    def test_single_char_cluster_id(self):
        """Test that single character cluster IDs are valid."""
        request = CreateSessionRequest(cluster_id="a")
        assert request.cluster_id == "a"

    def test_invalid_cluster_id_format(self):
        """Test that invalid cluster IDs are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            CreateSessionRequest(cluster_id="Production-Cluster")
        assert "string does not match regex" in str(exc_info.value)

        with pytest.raises(ValidationError) as exc_info:
            CreateSessionRequest(cluster_id="-invalid")
        assert "string does not match regex" in str(exc_info.value)

        with pytest.raises(ValidationError) as exc_info:
            CreateSessionRequest(cluster_id="invalid-")
        assert "string does not match regex" in str(exc_info.value)

    def test_ttl_bounds(self):
        """Test TTL validation bounds."""
        # Valid TTL
        request = CreateSessionRequest(cluster_id="test", ttl_seconds=60)
        assert request.ttl_seconds == 60

        request = CreateSessionRequest(cluster_id="test", ttl_seconds=3600)
        assert request.ttl_seconds == 3600

        # Invalid TTL - too small
        with pytest.raises(ValidationError) as exc_info:
            CreateSessionRequest(cluster_id="test", ttl_seconds=59)
        assert "greater than or equal to 60" in str(exc_info.value)

        # Invalid TTL - too large
        with pytest.raises(ValidationError) as exc_info:
            CreateSessionRequest(cluster_id="test", ttl_seconds=3601)
        assert "less than or equal to 3600" in str(exc_info.value)

    def test_optional_fields(self):
        """Test optional fields have correct defaults."""
        request = CreateSessionRequest(cluster_id="test")
        assert request.user_id is None
        assert request.correlation_id is None
        assert request.service_identity is None
        assert request.ttl_seconds == 300  # Default


class TestExecuteCommandRequest:
    """Test command execution request model."""

    def test_valid_command_request(self):
        """Test creating a valid command request."""
        request = ExecuteCommandRequest(
            cluster_id="test", args=["pods"], command_type=CommandType.GET
        )
        assert request.cluster_id == "test"
        assert request.args == ["pods"]
        assert request.command_type == CommandType.GET
        assert request.namespace == "default"
        assert request.timeout_seconds == 10

    def test_forbidden_verbs_rejected(self):
        """Test that dangerous verbs are rejected."""
        forbidden = ["delete", "apply", "create", "patch", "edit", "replace", "scale"]

        for verb in forbidden:
            with pytest.raises(ValidationError) as exc_info:
                ExecuteCommandRequest(cluster_id="test", args=[verb, "pod", "test-pod"])
            assert f"Forbidden argument: {verb}" in str(exc_info.value)

    def test_forbidden_flags_in_args(self):
        """Test that dangerous flags are rejected."""
        dangerous_args = [
            ["get", "pods", "--token=secret"],
            ["get", "pods", "--DELETE"],  # Case insensitive check
            ["describe", "pod", "test", "delete"],  # Forbidden word in args
        ]

        for args in dangerous_args:
            with pytest.raises(ValidationError) as exc_info:
                ExecuteCommandRequest(cluster_id="test", args=args)
            assert "Forbidden argument" in str(exc_info.value)

    def test_args_length_validation(self):
        """Test args length constraints."""
        # Valid - 1 arg minimum
        request = ExecuteCommandRequest(cluster_id="test", args=["pods"])
        assert len(request.args) == 1

        # Valid - 20 args maximum
        many_args = ["get"] + [f"arg{i}" for i in range(19)]
        request = ExecuteCommandRequest(cluster_id="test", args=many_args)
        assert len(request.args) == 20

        # Invalid - empty args
        with pytest.raises(ValidationError) as exc_info:
            ExecuteCommandRequest(cluster_id="test", args=[])
        assert "at least 1 item" in str(exc_info.value)

        # Invalid - too many args
        too_many = ["get"] + [f"arg{i}" for i in range(20)]
        with pytest.raises(ValidationError) as exc_info:
            ExecuteCommandRequest(cluster_id="test", args=too_many)
        assert "at most 20 items" in str(exc_info.value)

    def test_timeout_bounds(self):
        """Test timeout validation."""
        # Valid timeouts
        request = ExecuteCommandRequest(cluster_id="test", args=["pods"], timeout_seconds=1)
        assert request.timeout_seconds == 1

        request = ExecuteCommandRequest(cluster_id="test", args=["pods"], timeout_seconds=30)
        assert request.timeout_seconds == 30

        # Invalid - too small
        with pytest.raises(ValidationError):
            ExecuteCommandRequest(cluster_id="test", args=["pods"], timeout_seconds=0)

        # Invalid - too large
        with pytest.raises(ValidationError):
            ExecuteCommandRequest(cluster_id="test", args=["pods"], timeout_seconds=31)


class TestSession:
    """Test session model."""

    def test_session_expiration(self):
        """Test session expiration calculation."""
        session = Session(session_id="test-session", cluster_id="test", ttl_seconds=300)

        # Should expire 300 seconds after last activity
        expected_expiry = session.last_activity + timedelta(seconds=300)
        assert abs((session.expires_at - expected_expiry).total_seconds()) < 1

        # Should not be expired immediately
        assert not session.is_expired

    def test_session_defaults(self):
        """Test session default values."""
        session = Session(session_id="test-session", cluster_id="test")

        assert session.status == SessionStatus.ACTIVE
        assert session.command_count == 0
        assert session.ttl_seconds == 300
        assert session.user_id is None
        assert session.correlation_id is None
        assert session.service_identity is None


class TestValidationHelpers:
    """Test validation helper functions."""

    def test_validate_cluster_id(self):
        """Test cluster ID validation."""
        # Valid cluster IDs
        valid_ids = ["production-cluster-1", "test", "k8s-prod-us-west-2", "a", "cluster123"]

        for cluster_id in valid_ids:
            assert validate_cluster_id(cluster_id) == cluster_id

        # Invalid cluster IDs
        invalid_ids = [
            "Production-Cluster",  # Uppercase
            "-invalid",  # Starts with hyphen
            "invalid-",  # Ends with hyphen
            "invalid_cluster",  # Underscore
            "invalid.cluster",  # Dot
            "",  # Empty
        ]

        for cluster_id in invalid_ids:
            with pytest.raises(ValueError) as exc_info:
                validate_cluster_id(cluster_id)
            assert "Cluster ID must be" in str(exc_info.value)

    def test_validate_kubectl_args(self):
        """Test kubectl argument validation."""
        # Valid args
        valid_args = [
            ["get", "pods"],
            ["describe", "pod", "test-pod"],
            ["logs", "test-pod", "-f"],
            ["top", "nodes"],
            ["events", "--all-namespaces"],
        ]

        for args in valid_args:
            assert validate_kubectl_args(args) == args

        # Invalid - forbidden verbs
        forbidden_verbs = ["delete", "apply", "create", "patch", "edit", "exec"]
        for verb in forbidden_verbs:
            with pytest.raises(ValueError) as exc_info:
                validate_kubectl_args([verb, "pod", "test"])
            assert f"Forbidden verb: {verb}" in str(exc_info.value)

        # Invalid - forbidden flags
        forbidden_flags = [
            ["get", "pods", "--token=secret"],
            ["get", "pods", "--kubeconfig=/path/to/config"],
            ["get", "pods", "--username=admin"],
        ]

        for args in forbidden_flags:
            with pytest.raises(ValueError) as exc_info:
                validate_kubectl_args(args)
            assert "Forbidden flag" in str(exc_info.value)


class TestCommand:
    """Test internal command model."""

    def test_command_defaults(self):
        """Test command default values."""
        cmd = Command(id="cmd-123", cluster_id="test", command_type=CommandType.GET, args=["pods"])

        assert cmd.namespace == "default"
        assert cmd.timeout_seconds == 10
        assert cmd.priority == 1
        assert cmd.session_id is None
        assert isinstance(cmd.queued_at, datetime)

    def test_command_priority_bounds(self):
        """Test command priority validation."""
        # Valid priorities
        cmd = Command(
            id="cmd-123", cluster_id="test", command_type=CommandType.GET, args=["pods"], priority=0
        )
        assert cmd.priority == 0

        cmd = Command(
            id="cmd-123",
            cluster_id="test",
            command_type=CommandType.GET,
            args=["pods"],
            priority=100,
        )
        assert cmd.priority == 100

        # Invalid priorities
        with pytest.raises(ValidationError):
            Command(
                id="cmd-123",
                cluster_id="test",
                command_type=CommandType.GET,
                args=["pods"],
                priority=-1,
            )

        with pytest.raises(ValidationError):
            Command(
                id="cmd-123",
                cluster_id="test",
                command_type=CommandType.GET,
                args=["pods"],
                priority=101,
            )


class TestCommandResult:
    """Test command result model."""

    def test_result_creation(self):
        """Test creating command results."""
        result = CommandResult(
            command_id="cmd-123", success=True, output="pod/test-pod", execution_time_ms=250
        )

        assert result.command_id == "cmd-123"
        assert result.success is True
        assert result.output == "pod/test-pod"
        assert result.error is None
        assert result.exit_code is None
        assert result.execution_time_ms == 250
        assert isinstance(result.executed_at, datetime)

    def test_failed_result(self):
        """Test creating a failed command result."""
        result = CommandResult(
            command_id="cmd-123",
            success=False,
            error="Pod not found",
            exit_code=1,
            execution_time_ms=150,
        )

        assert result.success is False
        assert result.output is None
        assert result.error == "Pod not found"
        assert result.exit_code == 1


class TestAgentCommand:
    """Test agent command model."""

    def test_agent_command(self):
        """Test agent command creation."""
        cmd = AgentCommand(id="cmd-123", args=["get", "pods"], timeout=15)

        assert cmd.id == "cmd-123"
        assert cmd.args == ["get", "pods"]
        assert cmd.timeout == 15
        assert cmd.session_id is None

    def test_agent_command_defaults(self):
        """Test agent command defaults."""
        cmd = AgentCommand(id="cmd-123", args=["get", "pods"])

        assert cmd.timeout == 10  # Default timeout


class TestKubentlyConfig:
    """Test configuration model."""

    def test_default_config(self):
        """Test default configuration values."""
        config = KubentlyConfig()

        # Redis defaults
        assert config.redis_url == "redis://localhost:6379"
        assert config.redis_max_connections == 50

        # API defaults
        assert config.api_host == "0.0.0.0"
        assert config.api_port == 8080
        assert config.api_workers == 4

        # Security defaults
        assert config.api_keys == []
        assert config.agent_tokens == {}

        # Timeout defaults
        assert config.command_timeout == 10
        assert config.session_ttl == 300
        assert config.result_ttl == 60

        # Performance defaults
        assert config.max_commands_per_fetch == 10
        assert config.long_poll_timeout == 30

    def test_config_validation(self):
        """Test configuration validation."""
        # Valid config
        config = KubentlyConfig(api_port=3000, redis_max_connections=100, api_workers=8)
        assert config.api_port == 3000
        assert config.redis_max_connections == 100
        assert config.api_workers == 8

        # Invalid port
        with pytest.raises(ValidationError):
            KubentlyConfig(api_port=70000)

        # Invalid worker count
        with pytest.raises(ValidationError):
            KubentlyConfig(api_workers=101)

        # Invalid connection count
        with pytest.raises(ValidationError):
            KubentlyConfig(redis_max_connections=1001)


class TestEnums:
    """Test enum definitions."""

    def test_execution_status(self):
        """Test execution status enum."""
        assert ExecutionStatus.PENDING.value == "pending"
        assert ExecutionStatus.RUNNING.value == "running"
        assert ExecutionStatus.SUCCESS.value == "success"
        assert ExecutionStatus.FAILURE.value == "failure"
        assert ExecutionStatus.TIMEOUT.value == "timeout"
        assert ExecutionStatus.CANCELLED.value == "cancelled"

    def test_session_status(self):
        """Test session status enum."""
        assert SessionStatus.ACTIVE.value == "active"
        assert SessionStatus.IDLE.value == "idle"
        assert SessionStatus.EXPIRED.value == "expired"
        assert SessionStatus.ENDED.value == "ended"

    def test_command_type(self):
        """Test command type enum."""
        assert CommandType.GET.value == "get"
        assert CommandType.DESCRIBE.value == "describe"
        assert CommandType.LOGS.value == "logs"
        assert CommandType.TOP.value == "top"
        assert CommandType.EVENTS.value == "events"
        assert CommandType.VERSION.value == "version"
        assert CommandType.API_RESOURCES.value == "api-resources"
        assert CommandType.API_VERSIONS.value == "api-versions"
        assert CommandType.EXPLAIN.value == "explain"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
