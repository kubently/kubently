#!/usr/bin/env python3
"""The response from /debug/execute reports a failure as a failure.

`CommandResult` carries `success: bool` and has no `status` field, so the
`result.get("status", ExecutionStatus.SUCCESS)` that built the response always
fell through to its default: every command came back `status: "success"`,
including one the executor's read-only RBAC refused. RBAC denial is the
expected failure mode for a read-only tool, so that default mislabelled
precisely the case a caller -- or the diagnostic agent, which reads this field
-- most needs to see.

#112 fixed the same defect in the audit path; this covers the response body,
which is what callers and the A2A agent actually consume. These drive the real
endpoint with the shape an executor posts, so they fail if the expression in
main.py regresses, not merely if a local copy of it does.
"""

import os
import sys
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

fakeredis = pytest.importorskip("fakeredis")

import kubently.main as main
from kubently.modules.api import ExecuteCommandRequest
from kubently.modules.api.models import CommandResult, ExecutionStatus

CLUSTER = "prod-a"
IDENTITY = "team-a"


@pytest.fixture
def wired(monkeypatch):
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    monkeypatch.setattr(main, "redis_client", redis)
    monkeypatch.setattr(main, "audit_module", None)
    monkeypatch.setattr(main, "session_module", AsyncMock())
    return redis


async def _execute(wired, monkeypatch, posted):
    await wired.set(f"executor:token:{CLUSTER}", "tok")
    queue = AsyncMock()
    queue.wait_for_result.return_value = posted
    monkeypatch.setattr(main, "queue_module", queue)
    return await main.execute_command(
        ExecuteCommandRequest(cluster_id=CLUSTER, command_type="get", args=["secrets"]),
        auth_info=(True, IDENTITY),
        x_correlation_id=None,
        x_request_timeout=5,
    )


def test_command_result_has_no_status_field():
    """The premise the bug rested on. If this fails, the fix can be simplified."""
    posted = CommandResult(
        command_id="c", success=True, execution_time_ms=1
    ).model_dump()
    assert "status" not in posted


async def test_a_denied_command_is_not_reported_as_success(wired, monkeypatch):
    denied = {"success": False, "error": "Error from server (Forbidden): secrets is forbidden"}
    response = await _execute(wired, monkeypatch, denied)
    assert response.status == ExecutionStatus.FAILURE
    assert "Forbidden" in response.error


async def test_a_successful_command_is_still_a_success(wired, monkeypatch):
    """Guards the regression of 'fix it by calling everything a failure'."""
    ok = {"success": True, "output": "NAME  READY\nweb-0  1/1"}
    response = await _execute(wired, monkeypatch, ok)
    assert response.status == ExecutionStatus.SUCCESS
    assert "web-0" in response.output


async def test_a_timeout_is_still_a_timeout(wired, monkeypatch):
    """The `if not result` branch above the fix must keep its own status."""
    response = await _execute(wired, monkeypatch, None)
    assert response.status == ExecutionStatus.TIMEOUT
