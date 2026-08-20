#!/usr/bin/env python3
"""The audit trail is readable, exportable, scoped, and read-only (issue #55).

`auth:audit` has always existed but nothing surfaced it, so "what did the
agent run, where, and when" meant reading Redis by hand. These tests cover the
three properties that make surfacing it safe rather than just convenient:

- a command executed through /debug/execute turns into an entry carrying
  cluster, timestamp and outcome, and that entry survives a JSON round-trip;
- a caller reads only their own identity's entries -- never another
  identity's, by any combination of filters, and never an unattributed one;
- the read path does not write. Not a trim, not a TTL, not a delete.
"""

import json
import os
import sys
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

fakeredis = pytest.importorskip("fakeredis")

import kubently.main as main
from kubently.modules.audit import AUDIT_KEY, AuditModule

TEAM_A = "team-a"
TEAM_B = "team-b"
CLUSTER_A = "prod-a"
CLUSTER_B = "prod-b"


@pytest.fixture
def redis():
    return fakeredis.FakeAsyncRedis(decode_responses=True)


@pytest.fixture
def audit(redis):
    return AuditModule(redis)


@pytest.fixture
def wired(redis, audit, monkeypatch):
    """main.py wired to a fake Redis with the audit module installed."""
    monkeypatch.setattr(main, "redis_client", redis)
    monkeypatch.setattr(main, "audit_module", audit)
    return redis


async def _record(audit, identity, cluster, **kwargs):
    await audit.record_command(
        service_identity=identity,
        cluster_id=cluster,
        command_id=kwargs.pop("command_id", "cmd-1"),
        args=kwargs.pop("args", ["get", "pods"]),
        **kwargs,
    )


# ---------------------------------------------------------------- round-trip


async def test_recorded_command_comes_back_with_cluster_time_and_outcome(audit):
    """The acceptance criterion: a run command is visible with its context."""
    await _record(
        audit,
        TEAM_A,
        CLUSTER_A,
        args=["get", "pods", "-n", "kube-system"],
        session_id="sess-42",
        outcome="success",
    )

    entries = await audit.query(identity=TEAM_A)

    assert len(entries) == 1
    entry = entries[0]
    assert entry["cluster_id"] == CLUSTER_A
    assert entry["session_id"] == "sess-42"
    assert entry["command"] == "get pods -n kube-system"
    assert entry["outcome"] == "success"
    assert entry["type"] == "command_executed"
    # A timestamp that does not parse is not a timestamp.
    assert datetime.fromisoformat(entry["timestamp"]).tzinfo is not None


async def test_failure_outcome_and_truncated_error_are_recorded(audit):
    await _record(
        audit, TEAM_A, CLUSTER_A, outcome="failure", error="Error from server (Forbidden): " + "x" * 500
    )

    (entry,) = await audit.query(identity=TEAM_A)
    assert entry["outcome"] == "failure"
    assert entry["error"].startswith("Error from server (Forbidden):")
    # Errors are the one field an executor can echo unbounded text into.
    assert len(entry["error"]) == 200


async def test_command_output_is_never_stored(audit, redis):
    """Surfacing the trail must not widen what the trail holds."""
    await _record(audit, TEAM_A, CLUSTER_A, outcome="success")

    raw = await redis.lrange(AUDIT_KEY, 0, -1)
    stored = json.loads(raw[0])
    assert "output" not in stored["data"]
    assert "output" not in stored


async def test_json_export_round_trips(audit):
    """What the CLI writes with --output json must parse back identically."""
    await _record(audit, TEAM_A, CLUSTER_A, session_id="sess-1", outcome="success")
    await _record(audit, TEAM_A, CLUSTER_B, command_id="cmd-2", outcome="failure")

    entries = await audit.query(identity=TEAM_A)
    assert json.loads(json.dumps(entries)) == entries


# ------------------------------------------------------------------ filters


async def test_filters_by_cluster_and_session(audit):
    await _record(audit, TEAM_A, CLUSTER_A, command_id="a", session_id="s1")
    await _record(audit, TEAM_A, CLUSTER_B, command_id="b", session_id="s2")

    assert [e["command_id"] for e in await audit.query(identity=TEAM_A, cluster_id=CLUSTER_A)] == [
        "a"
    ]
    assert [e["command_id"] for e in await audit.query(identity=TEAM_A, session_id="s2")] == ["b"]


async def test_filters_by_time_range(audit, redis):
    await _record(audit, TEAM_A, CLUSTER_A)

    future = datetime.now(UTC) + timedelta(hours=1)
    past = datetime.now(UTC) - timedelta(hours=1)

    assert await audit.query(identity=TEAM_A, since=past) != []
    assert await audit.query(identity=TEAM_A, since=future) == []
    assert await audit.query(identity=TEAM_A, until=past) == []
    assert await audit.query(identity=TEAM_A, until=future) != []


async def test_newest_first_and_limit_applies(audit):
    for i in range(5):
        await _record(audit, TEAM_A, CLUSTER_A, command_id=f"cmd-{i}")

    entries = await audit.query(identity=TEAM_A, limit=2)
    assert [e["command_id"] for e in entries] == ["cmd-4", "cmd-3"]


# -------------------------------------------------------- scope isolation


async def test_cross_scope_read_returns_nothing(audit):
    """The non-negotiable: one identity can never read another's entries."""
    await _record(audit, TEAM_A, CLUSTER_A, command_id="a-secret", args=["get", "secrets"])
    await _record(audit, TEAM_B, CLUSTER_B, command_id="b-own")

    b_entries = await audit.query(identity=TEAM_B)

    assert [e["command_id"] for e in b_entries] == ["b-own"]
    assert all(e["service_identity"] == TEAM_B for e in b_entries)
    assert "a-secret" not in json.dumps(b_entries)


async def test_cross_scope_read_cannot_be_widened_by_filters(audit):
    """Naming somebody else's cluster/session yields empty, not their data."""
    await _record(audit, TEAM_A, CLUSTER_A, command_id="a-secret", session_id="a-sess")

    # Team B explicitly targets team A's cluster and session.
    assert await audit.query(identity=TEAM_B, cluster_id=CLUSTER_A) == []
    assert await audit.query(identity=TEAM_B, session_id="a-sess") == []
    assert await audit.query(identity=TEAM_B, limit=1000) == []


async def test_unattributed_entries_are_never_returned(audit, redis):
    """The filter fails closed: no identity means nobody, not everybody.

    AuthModule writes `executor_token_created`/`executor_token_revoked` with a
    cluster_id and no service_identity. Those must not become readable by the
    first caller who asks.
    """
    await redis.lpush(
        AUDIT_KEY,
        json.dumps(
            {
                "type": "executor_token_created",
                "data": {"cluster_id": CLUSTER_A, "timestamp": datetime.now(UTC).isoformat()},
                "correlation_id": None,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        ),
    )

    assert await audit.query(identity=TEAM_A) == []
    assert await audit.query(identity=TEAM_B) == []
    assert await audit.query(identity=None) == []
    assert await audit.query(identity="") == []


async def test_existing_auth_events_stay_scoped_to_their_own_identity(audit, redis):
    """AuthModule's api_key_verified entries follow the same rule."""
    await redis.lpush(
        AUDIT_KEY,
        json.dumps(
            {
                "type": "api_key_verified",
                "data": {"service_identity": TEAM_A, "timestamp": datetime.now(UTC).isoformat()},
                "correlation_id": None,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        ),
    )

    assert [e["type"] for e in await audit.query(identity=TEAM_A)] == ["api_key_verified"]
    assert await audit.query(identity=TEAM_B) == []


async def test_unparseable_entries_are_skipped_not_fatal(audit, redis):
    """Old docs showed `str(event)` rather than json.dumps; don't die on it."""
    await redis.lpush(AUDIT_KEY, "{not json at all")
    await _record(audit, TEAM_A, CLUSTER_A)

    assert len(await audit.query(identity=TEAM_A)) == 1


# ------------------------------------------------------------- read-only


async def test_query_only_reads(audit, redis, monkeypatch):
    """No write command may appear on the read path."""
    await _record(audit, TEAM_A, CLUSTER_A)

    forbidden = ["lpush", "rpush", "ltrim", "delete", "expire", "set", "setex", "persist", "lrem"]
    calls = []
    for name in forbidden:

        def deny(*args, _name=name, **kwargs):
            calls.append(_name)
            raise AssertionError(f"read path called {_name}")

        monkeypatch.setattr(redis, name, deny)

    assert len(await audit.query(identity=TEAM_A)) == 1
    assert calls == []


# ------------------------------------------------- /debug/execute wiring


async def test_executing_a_command_records_an_audit_entry(wired, audit, monkeypatch):
    """The end-to-end premise: running a command puts it in the trail.

    Nothing recorded command execution before this change -- `auth:audit` held
    only authentication events -- so `kubently audit` had nothing to show. This
    is the test that keeps the writer wired to the endpoint.
    """
    from unittest.mock import AsyncMock

    from kubently.modules.api import ExecuteCommandRequest

    await wired.set(f"executor:token:{CLUSTER_A}", "tok")

    queue = AsyncMock()
    queue.wait_for_result.return_value = {
        "success": True,
        "output": "NAME  READY\nweb-0  1/1",
    }
    monkeypatch.setattr(main, "queue_module", queue)
    monkeypatch.setattr(main, "session_module", AsyncMock())

    await main.execute_command(
        ExecuteCommandRequest(cluster_id=CLUSTER_A, command_type="get", args=["pods"]),
        auth_info=(True, TEAM_A),
        x_correlation_id="corr-9",
        x_request_timeout=5,
    )

    (entry,) = await audit.query(identity=TEAM_A)
    assert entry["cluster_id"] == CLUSTER_A
    # The full argv handed to the executor, not the request as typed: the
    # defaulted `-n default` is part of what actually ran against the cluster.
    assert entry["command"] == "get pods -n default"
    assert entry["outcome"] == "success"
    assert entry["correlation_id"] == "corr-9"
    assert entry["timestamp"]
    # The output came back to the caller but must not reach the trail.
    assert "web-0" not in json.dumps(entry)


async def test_a_denied_command_is_recorded_as_a_failure(wired, audit, monkeypatch):
    """A Forbidden kubectl call must not be filed as a success.

    The executor posts a CommandResult, which has a `success` field and no
    `status` field -- so deriving the outcome from `status` silently records
    every denied command as having succeeded. Caught by running a real session
    against a real executor, not by reading the model.
    """
    from unittest.mock import AsyncMock

    from kubently.modules.api import ExecuteCommandRequest

    await wired.set(f"executor:token:{CLUSTER_A}", "tok")

    queue = AsyncMock()
    queue.wait_for_result.return_value = {
        "success": False,
        "error": "Error from server (Forbidden): secrets is forbidden",
    }
    monkeypatch.setattr(main, "queue_module", queue)
    monkeypatch.setattr(main, "session_module", AsyncMock())

    await main.execute_command(
        ExecuteCommandRequest(cluster_id=CLUSTER_A, command_type="get", args=["secrets"]),
        auth_info=(True, TEAM_A),
        x_correlation_id=None,
        x_request_timeout=5,
    )

    (entry,) = await audit.query(identity=TEAM_A)
    assert entry["outcome"] == "failure"
    assert "Forbidden" in entry["error"]


async def test_a_timed_out_command_is_still_recorded(wired, audit, monkeypatch):
    """A command that ran and hung is exactly what an operator hunts for."""
    from unittest.mock import AsyncMock

    from kubently.modules.api import ExecuteCommandRequest

    await wired.set(f"executor:token:{CLUSTER_A}", "tok")

    queue = AsyncMock()
    queue.wait_for_result.return_value = None
    monkeypatch.setattr(main, "queue_module", queue)
    monkeypatch.setattr(main, "session_module", AsyncMock())

    await main.execute_command(
        ExecuteCommandRequest(cluster_id=CLUSTER_A, command_type="get", args=["pods"]),
        auth_info=(True, TEAM_A),
        x_correlation_id=None,
        x_request_timeout=5,
    )

    (entry,) = await audit.query(identity=TEAM_A)
    assert entry["outcome"] == "timeout"


# --------------------------------------------------------- HTTP endpoint
#
# Driven through the real app so the registered route, its query-parameter
# parsing and the auth dependency are all exercised -- calling the handler
# directly would leave FastAPI's Query() defaults unresolved and would not
# prove which HTTP methods the path actually answers.


def _client(identity):
    """TestClient for the real app, authenticated as `identity`."""
    main.app.dependency_overrides[main.verify_api_key] = lambda: (True, identity)
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    main.app.dependency_overrides.clear()


async def test_endpoint_returns_only_the_callers_entries(wired, audit):
    await _record(audit, TEAM_A, CLUSTER_A, command_id="a-secret")
    await _record(audit, TEAM_B, CLUSTER_B, command_id="b-own")

    body = _client(TEAM_B).get("/audit").json()

    assert body["service_identity"] == TEAM_B
    assert body["count"] == 1
    assert body["entries"][0]["command_id"] == "b-own"
    assert "a-secret" not in json.dumps(body)


async def test_endpoint_cross_scope_read_fails(wired, audit):
    """Team B asks the API for team A's cluster and gets nothing back."""
    await _record(audit, TEAM_A, CLUSTER_A, command_id="a-secret", session_id="a-sess")

    client = _client(TEAM_B)

    for params in ({"cluster_id": CLUSTER_A}, {"session_id": "a-sess"}, {"limit": 1000}):
        response = client.get("/audit", params=params)
        assert response.status_code == 200
        assert response.json()["count"] == 0, params


async def test_endpoint_refuses_a_key_with_no_identity(wired):
    """An unscopeable caller is refused, not shown everything."""
    assert _client(None).get("/audit").status_code == 403


async def test_endpoint_rejects_unparseable_time(wired):
    assert _client(TEAM_A).get("/audit", params={"since": "last tuesday"}).status_code == 400


async def test_endpoint_is_read_only(wired, audit):
    """No mutating method is routed at /audit."""
    await _record(audit, TEAM_A, CLUSTER_A)
    client = _client(TEAM_A)

    for method in ("post", "put", "patch", "delete"):
        response = getattr(client, method)("/audit")
        assert response.status_code == 405, f"{method.upper()} /audit was routed"
