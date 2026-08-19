#!/usr/bin/env python3
"""The cluster registry is executor-owned (issue #89).

Session creation used to write `cluster:active:*` / `cluster:session:*`, and
`/debug/clusters` scanned those keys — so POSTing /debug/session with any
string put a phantom cluster into the fleet the agent sees via list_clusters.
Only an executor registration (`executor:token:*`) may define a cluster.
"""

import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

fakeredis = pytest.importorskip("fakeredis")

import kubently.main as main
from kubently.modules.api.models import CreateSessionRequest
from kubently.modules.session.session import SessionModule

AUTH = (True, "test-service")
REGISTERED = "prod-1"
PHANTOM = "totally-fake-cluster-xyz"


@pytest.fixture
def wired(monkeypatch):
    """main.py wired to a fake Redis holding one registered executor."""
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    monkeypatch.setattr(main, "redis_client", redis)
    monkeypatch.setattr(main, "session_module", SessionModule(redis, default_ttl=300))
    return redis


async def _create_session(cluster_id):
    return await main.create_session(
        CreateSessionRequest(cluster_id=cluster_id),
        auth_info=AUTH,
        x_correlation_id=None,
        x_service_identity=None,
    )


async def _clusters():
    return (await main.list_clusters(auth_info=AUTH))["clusters"]


async def test_executor_registration_flow_still_works(wired):
    """The legitimate path: executor registered -> session -> listed."""
    await wired.set(f"executor:token:{REGISTERED}", "tok")

    session = await _create_session(REGISTERED)
    assert session.cluster_id == REGISTERED

    assert await _clusters() == [REGISTERED]


async def test_session_for_unregistered_cluster_is_rejected(wired):
    await wired.set(f"executor:token:{REGISTERED}", "tok")

    with pytest.raises(HTTPException) as exc:
        await _create_session(PHANTOM)
    assert exc.value.status_code == 404
    assert REGISTERED in exc.value.detail  # same shape as /debug/execute's 404


async def test_session_creation_cannot_inject_a_cluster(wired):
    """The boundary: a session must never add an id to /debug/clusters."""
    await wired.set(f"executor:token:{REGISTERED}", "tok")

    with pytest.raises(HTTPException):
        await _create_session(PHANTOM)

    clusters = await _clusters()
    assert PHANTOM not in clusters
    assert clusters == [REGISTERED]


async def test_listing_ignores_session_written_keys(wired):
    """Belt and braces: even pre-existing session keys (written before this
    fix, or by any future session writer) are not part of the registry."""
    await wired.set(f"executor:token:{REGISTERED}", "tok")
    await wired.setex("cluster:session:namespace", 300, "some-session-uuid")
    await wired.setex("cluster:active:namespace", 300, "some-session-uuid")

    assert await _clusters() == [REGISTERED]


async def test_no_executors_means_no_clusters(wired):
    assert await _clusters() == []
