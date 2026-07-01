#!/usr/bin/env python3
"""
Tests for executor-side command whitelist enforcement.

These verify that SSEKubentlyExecutor._run_kubectl actually gates commands
through the DynamicCommandWhitelist before shelling out to kubectl — closing
the bypass where a direct POST to /debug/execute would otherwise run a write
verb (RBAC remains the ultimate backstop; this is defense-in-depth).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kubently.modules.executor.sse_executor import SSEKubentlyExecutor


@pytest.fixture
def executor(monkeypatch):
    """A real executor instance with required env vars set."""
    monkeypatch.setenv("KUBENTLY_API_URL", "http://localhost:8080")
    monkeypatch.setenv("CLUSTER_ID", "test-cluster")
    monkeypatch.setenv("KUBENTLY_TOKEN", "test-token")
    # Force default whitelist (file absent -> safe READ_ONLY defaults)
    monkeypatch.setenv("KUBENTLY_WHITELIST_CONFIG", "/nonexistent/whitelist.yaml")
    return SSEKubentlyExecutor()


def test_run_kubectl_blocks_write_verb(executor, monkeypatch):
    """A write verb must be blocked before kubectl is ever invoked."""
    ran = {"called": False}

    def fake_run(*args, **kwargs):
        ran["called"] = True
        raise AssertionError("subprocess.run should not be called for a blocked command")

    monkeypatch.setattr("subprocess.run", fake_run)

    result = executor._run_kubectl(["delete", "pod", "foo"])

    assert result["status"] == "BLOCKED"
    assert result["success"] is False
    assert ran["called"] is False


def test_run_kubectl_allows_read_verb(executor, monkeypatch):
    """A normal read command still runs kubectl."""

    class FakeProc:
        stdout = "pod/foo"
        stderr = ""
        returncode = 0

    monkeypatch.setattr("subprocess.run", lambda *a, **k: FakeProc())

    result = executor._run_kubectl(["get", "pods"])

    assert result["status"] == "SUCCESS"
    assert result["success"] is True
