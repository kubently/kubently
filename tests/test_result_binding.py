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
