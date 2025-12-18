"""
Shared pytest fixtures for Kubently tests.

This module provides common fixtures including:
- KubectlMocker: Mock kubectl subprocess calls with canned responses
- Redis mocks for session/queue tests
- FastAPI test client utilities
"""

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Pattern, Union
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =============================================================================
# Kubectl Mocking Infrastructure
# =============================================================================

@dataclass
class KubectlResponse:
    """Represents a mocked kubectl command response."""
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0

    def to_completed_process(self) -> MagicMock:
        """Convert to a subprocess.CompletedProcess-like mock."""
        result = MagicMock()
        result.stdout = self.stdout
        result.stderr = self.stderr
        result.returncode = self.returncode
        return result


@dataclass
class KubectlCall:
    """Record of a kubectl call made during testing."""
    command: List[str]
    full_command_str: str
    matched_pattern: Optional[str] = None
    response: Optional[KubectlResponse] = None


class KubectlMocker:
    """
    Mock kubectl subprocess calls with pattern-matched responses.

    This allows testing executor and agent behavior without a real
    Kubernetes cluster by intercepting subprocess.run calls.

    Usage:
        def test_pod_diagnosis(kubectl_mocker):
            kubectl_mocker.register("get pods", KubectlResponse(
                stdout="NAME  STATUS\\nmypod  CrashLoopBackOff"
            ))

            # Run code that calls kubectl
            result = executor._run_kubectl(["get", "pods"])

            # Verify the call was made
            assert kubectl_mocker.was_called_with("get pods")
    """

    def __init__(self):
        self._responses: List[tuple[Union[str, Pattern], KubectlResponse]] = []
        self._call_history: List[KubectlCall] = []
        self._default_response = KubectlResponse(
            stderr="Error: mock not configured for this command",
            returncode=1
        )
        self._passthrough_non_kubectl = True

    def register(
        self,
        pattern: Union[str, Pattern],
        response: KubectlResponse,
        priority: int = 0
    ) -> "KubectlMocker":
        """
        Register a response for commands matching the pattern.

        Args:
            pattern: String (substring match) or regex pattern
            response: KubectlResponse to return when matched
            priority: Higher priority patterns are checked first

        Returns:
            self for chaining
        """
        self._responses.append((pattern, response, priority))
        # Sort by priority (highest first)
        self._responses.sort(key=lambda x: x[2], reverse=True)
        return self

    def register_scenario(self, scenario_name: str) -> "KubectlMocker":
        """
        Register all responses for a named scenario.

        Scenarios are predefined sets of kubectl responses that simulate
        common Kubernetes issues.

        Args:
            scenario_name: One of the predefined scenario names

        Returns:
            self for chaining
        """
        from fixtures.kubectl_scenarios import SCENARIOS

        if scenario_name not in SCENARIOS:
            raise ValueError(
                f"Unknown scenario: {scenario_name}. "
                f"Available: {list(SCENARIOS.keys())}"
            )

        for pattern, response in SCENARIOS[scenario_name].items():
            self.register(pattern, response)

        return self

    def set_default_response(self, response: KubectlResponse) -> "KubectlMocker":
        """Set the default response for unmatched commands."""
        self._default_response = response
        return self

    def mock_run(
        self,
        cmd: List[str],
        capture_output: bool = True,
        text: bool = True,
        timeout: Optional[int] = None,
        **kwargs
    ) -> MagicMock:
        """
        Mock implementation of subprocess.run for kubectl commands.

        This method is used as a side_effect for patching subprocess.run.
        """
        cmd_str = " ".join(cmd)

        # Only intercept kubectl commands
        if cmd[0] != "kubectl":
            if self._passthrough_non_kubectl:
                return subprocess.run(
                    cmd,
                    capture_output=capture_output,
                    text=text,
                    timeout=timeout,
                    **kwargs
                )
            else:
                raise RuntimeError(f"Non-kubectl command blocked: {cmd_str}")

        # Find matching response
        kubectl_args = " ".join(cmd[1:])
        matched_pattern = None
        response = self._default_response

        for pattern, resp, _ in self._responses:
            if isinstance(pattern, str):
                if pattern in kubectl_args:
                    matched_pattern = pattern
                    response = resp
                    break
            else:  # Compiled regex
                if pattern.search(kubectl_args):
                    matched_pattern = pattern.pattern
                    response = resp
                    break

        # Record the call
        call = KubectlCall(
            command=cmd,
            full_command_str=cmd_str,
            matched_pattern=matched_pattern,
            response=response
        )
        self._call_history.append(call)

        return response.to_completed_process()

    @property
    def calls(self) -> List[KubectlCall]:
        """Get all kubectl calls made during the test."""
        return self._call_history

    @property
    def call_count(self) -> int:
        """Get the number of kubectl calls made."""
        return len(self._call_history)

    def was_called_with(self, pattern: str) -> bool:
        """Check if any call contained the given pattern."""
        return any(pattern in call.full_command_str for call in self._call_history)

    def get_calls_matching(self, pattern: str) -> List[KubectlCall]:
        """Get all calls containing the given pattern."""
        return [c for c in self._call_history if pattern in c.full_command_str]

    def reset(self):
        """Clear call history (but keep registered responses)."""
        self._call_history = []

    def clear(self):
        """Clear both responses and call history."""
        self._responses = []
        self._call_history = []


@pytest.fixture
def kubectl_mocker():
    """
    Fixture that provides a KubectlMocker with subprocess.run patched.

    Usage:
        def test_something(kubectl_mocker):
            kubectl_mocker.register("get pods", KubectlResponse(stdout="..."))
            # Your test code that calls kubectl
            assert kubectl_mocker.was_called_with("get pods")
    """
    mocker = KubectlMocker()
    with patch("subprocess.run", side_effect=mocker.mock_run):
        yield mocker


@pytest.fixture
def kubectl_mocker_strict():
    """
    Strict kubectl mocker that fails on any unregistered command.

    Use this when you want to ensure all kubectl interactions are
    explicitly accounted for in your test.
    """
    mocker = KubectlMocker()
    mocker._default_response = KubectlResponse(
        stderr="STRICT MODE: No mock registered for this command",
        returncode=127
    )
    with patch("subprocess.run", side_effect=mocker.mock_run):
        yield mocker


# =============================================================================
# Redis Mocking Infrastructure
# =============================================================================

@pytest.fixture
def mock_redis():
    """Create a comprehensive mock Redis client for async operations."""
    redis = AsyncMock()

    # Basic operations
    redis.setex = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock()
    redis.delete = AsyncMock(return_value=1)
    redis.exists = AsyncMock(return_value=0)
    redis.keys = AsyncMock(return_value=[])
    redis.expire = AsyncMock()
    redis.ttl = AsyncMock(return_value=-2)

    # Set operations
    redis.sadd = AsyncMock()
    redis.srem = AsyncMock()
    redis.smembers = AsyncMock(return_value=set())
    redis.sismember = AsyncMock(return_value=False)

    # List operations
    redis.lpush = AsyncMock()
    redis.rpush = AsyncMock()
    redis.lpop = AsyncMock(return_value=None)
    redis.rpop = AsyncMock(return_value=None)
    redis.lrange = AsyncMock(return_value=[])
    redis.ltrim = AsyncMock()
    redis.llen = AsyncMock(return_value=0)

    # Hash operations
    redis.hset = AsyncMock()
    redis.hget = AsyncMock(return_value=None)
    redis.hgetall = AsyncMock(return_value={})
    redis.hdel = AsyncMock()

    # Pub/sub
    redis.publish = AsyncMock()
    redis.subscribe = AsyncMock()

    # Pipeline support
    pipeline = AsyncMock()
    pipeline.execute = AsyncMock(return_value=[])
    redis.pipeline = MagicMock(return_value=pipeline)

    return redis


@pytest.fixture
def mock_redis_with_data():
    """
    Redis mock with in-memory data storage for more realistic tests.

    This allows testing code that reads back what it writes.
    """
    storage = {}

    redis = AsyncMock()

    async def mock_set(key, value, *args, **kwargs):
        storage[key] = value
        return True

    async def mock_setex(key, ttl, value):
        storage[key] = value
        return True

    async def mock_get(key):
        return storage.get(key)

    async def mock_delete(*keys):
        count = 0
        for key in keys:
            if key in storage:
                del storage[key]
                count += 1
        return count

    async def mock_exists(*keys):
        return sum(1 for k in keys if k in storage)

    async def mock_keys(pattern):
        import fnmatch
        # Convert Redis glob to fnmatch
        pattern = pattern.replace("*", "*")
        return [k for k in storage.keys() if fnmatch.fnmatch(k, pattern)]

    redis.set = mock_set
    redis.setex = mock_setex
    redis.get = mock_get
    redis.delete = mock_delete
    redis.exists = mock_exists
    redis.keys = mock_keys
    redis._storage = storage  # Expose for test assertions

    return redis


# =============================================================================
# Test Markers Configuration
# =============================================================================

def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "kubectl_mock: Tests using mocked kubectl subprocess calls"
    )
    config.addinivalue_line(
        "markers", "integration: Integration tests requiring infrastructure"
    )
    config.addinivalue_line(
        "markers", "e2e: End-to-end tests requiring a real cluster"
    )
    config.addinivalue_line(
        "markers", "slow: Tests that take a long time to run"
    )
