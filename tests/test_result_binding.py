#!/usr/bin/env python3
"""An executor may only answer commands issued to its own cluster.

Executors run inside customer clusters, so the customer controls them. Before
this binding, any authenticated executor could POST /executor/results for ANY
command_id — injecting fabricated kubectl output into another tenant's in-flight
diagnosis (the agent would then report that as the other tenant's cluster state).
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from kubently.modules.queue.queue import QueueModule  # noqa: E402


class FakeRedis:
    def __init__(self):
        self.kv = {}

    async def setex(self, k, ttl, v):
        self.kv[k] = v

    async def get(self, k):
        return self.kv.get(k)


@pytest.mark.asyncio
async def test_issuing_cluster_accepted_others_rejected():
    q = QueueModule(FakeRedis())
    await q.bind_command("cmd-1", "tenant-a-cluster")

    assert await q.command_belongs_to("cmd-1", "tenant-a-cluster")
    # The attack: tenant B's executor answering tenant A's command.
    assert not await q.command_belongs_to("cmd-1", "tenant-b-cluster")


@pytest.mark.asyncio
async def test_unknown_or_expired_command_rejected():
    """Fails closed: an id we never issued (or whose binding expired) is denied,
    so a guessed id can't be used to smuggle a result in."""
    q = QueueModule(FakeRedis())
    assert not await q.command_belongs_to("never-issued", "any-cluster")


@pytest.mark.asyncio
async def test_binding_is_bytes_safe():
    """decode_responses=False clients hand back bytes; comparison must still hold."""
    class BytesRedis(FakeRedis):
        async def get(self, k):
            v = self.kv.get(k)
            return v.encode() if isinstance(v, str) else v

    q = QueueModule(BytesRedis())
    await q.bind_command("cmd-2", "cluster-x")
    assert await q.command_belongs_to("cmd-2", "cluster-x")
    assert not await q.command_belongs_to("cmd-2", "cluster-y")


def test_endpoint_enforces_the_binding():
    """The helper being correct is worthless if the endpoint never calls it.

    Reverting the check in post_result reintroduces the vulnerability while the
    unit tests above still pass — so assert the wiring explicitly.
    """
    from pathlib import Path

    main_src = (Path(__file__).parent.parent / "kubently/main.py").read_text()

    # /debug/execute must bind before publishing.
    assert "bind_command(" in main_src, "commands are never bound to a cluster"
    idx_bind = main_src.index("bind_command(")
    idx_publish = main_src.index("redis_client.publish(channel", idx_bind - 4000)
    assert idx_bind < idx_publish, "bind must happen before publish (else a race)"

    # /executor/results must reject foreign clusters.
    idx_results = main_src.index('@app.post("/executor/results")')
    idx_store = main_src.index("store_result(", idx_results)
    segment = main_src[idx_results:idx_store]
    assert "command_belongs_to(" in segment, (
        "post_result stores a result without verifying the command was issued to "
        "this executor's cluster — cross-tenant result injection"
    )
    assert "403" in segment
