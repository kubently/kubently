"""Tests for A2A checkpointer backend selection and the plain-Redis saver.

The plain-Redis backend exists so conversation checkpointing works on Redis
servers without the RediSearch module (e.g. Upstash). These tests use
fakeredis, which — like Upstash — has no RediSearch, so they also prove the
saver needs only core Redis commands.
"""

import operator
import sys
from typing import Annotated, TypedDict

import pytest

pytest.importorskip("langgraph.checkpoint.base")
fakeredis = pytest.importorskip("fakeredis")

from langgraph.checkpoint.base import empty_checkpoint  # noqa: E402

from kubently.modules.a2a.protocol_bindings.a2a_server.checkpointer import (  # noqa: E402
    BACKEND_ENV,
    TTL_ENV,
    PlainRedisSaver,
    create_checkpointer,
)


@pytest.fixture
def redis_client():
    # decode_responses=True mirrors how kubently/main.py creates its client.
    return fakeredis.FakeAsyncRedis(decode_responses=True)


@pytest.fixture
def saver(redis_client):
    return PlainRedisSaver(redis_client, ttl_seconds=None)


def thread_config(thread_id="thread-1", checkpoint_id=None):
    cfg = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    if checkpoint_id:
        cfg["configurable"]["checkpoint_id"] = checkpoint_id
    return cfg


def make_checkpoint(checkpoint_id, values=None, version=None):
    # Channel versions are bumped by LangGraph on every write; blobs are keyed
    # by (channel, version), so distinct checkpoints carry distinct versions.
    cp = empty_checkpoint()
    cp["id"] = checkpoint_id
    cp["channel_values"] = values or {}
    cp["channel_versions"] = {k: (version or checkpoint_id) for k in (values or {})}
    return cp


# =============================================================================
# Backend selection
# =============================================================================


class TestBackendSelection:
    async def test_none_backend_disables_checkpointing(self, monkeypatch, redis_client):
        monkeypatch.setenv(BACKEND_ENV, "none")
        assert await create_checkpointer(redis_client) is None

    async def test_memory_backend(self, monkeypatch):
        monkeypatch.setenv(BACKEND_ENV, "memory")
        from langgraph.checkpoint.memory import InMemorySaver

        saver = await create_checkpointer(None)
        assert isinstance(saver, InMemorySaver)

    async def test_plain_redis_backend(self, monkeypatch, redis_client):
        monkeypatch.setenv(BACKEND_ENV, "plain-redis")
        saver = await create_checkpointer(redis_client)
        assert isinstance(saver, PlainRedisSaver)

    async def test_plain_redis_alias(self, monkeypatch, redis_client):
        monkeypatch.setenv(BACKEND_ENV, "redis-plain")
        saver = await create_checkpointer(redis_client)
        assert isinstance(saver, PlainRedisSaver)

    async def test_no_redis_client_degrades_to_none(self, monkeypatch):
        monkeypatch.delenv(BACKEND_ENV, raising=False)
        assert await create_checkpointer(None) is None
        monkeypatch.setenv(BACKEND_ENV, "plain-redis")
        assert await create_checkpointer(None) is None

    async def test_unknown_backend_raises(self, monkeypatch, redis_client):
        monkeypatch.setenv(BACKEND_ENV, "postgres")
        with pytest.raises(ValueError, match="postgres"):
            await create_checkpointer(redis_client)

    async def test_default_backend_is_redisearch(self, monkeypatch, redis_client):
        """Unset env var must select AsyncRedisSaver (unchanged default).

        fakeredis has no RediSearch, so setup() failing with an error proves
        the RediSearch path was actually taken; the agent catches this and
        degrades to no memory, matching today's Upstash behavior.
        """
        pytest.importorskip("langgraph.checkpoint.redis")
        monkeypatch.delenv(BACKEND_ENV, raising=False)
        with pytest.raises(Exception):
            await create_checkpointer(redis_client)

    async def test_ttl_env_parsing(self, monkeypatch, redis_client):
        monkeypatch.setenv(BACKEND_ENV, "plain-redis")
        monkeypatch.setenv(TTL_ENV, "3600")
        saver = await create_checkpointer(redis_client)
        assert saver._ttl == 3600

        monkeypatch.setenv(TTL_ENV, "0")
        saver = await create_checkpointer(redis_client)
        assert saver._ttl is None


# =============================================================================
# PlainRedisSaver: checkpoint save/restore
# =============================================================================


class TestPlainRedisSaver:
    async def test_get_tuple_empty_thread_returns_none(self, saver):
        assert await saver.aget_tuple(thread_config("missing")) is None

    async def test_put_then_get_roundtrip(self, saver):
        cp = make_checkpoint("00000001", {"messages": ["hello"], "count": 1})
        await saver.aput(
            thread_config(), cp, {"source": "input", "step": -1}, cp["channel_versions"]
        )

        tup = await saver.aget_tuple(thread_config())
        assert tup is not None
        assert tup.checkpoint["id"] == "00000001"
        assert tup.checkpoint["channel_values"] == {"messages": ["hello"], "count": 1}
        assert tup.metadata["source"] == "input"
        assert tup.parent_config is None

    async def test_latest_checkpoint_wins(self, saver):
        for i in (1, 2, 3):
            cp = make_checkpoint(f"0000000{i}", {"count": i})
            await saver.aput(
                thread_config(checkpoint_id=f"0000000{i - 1}" if i > 1 else None),
                cp,
                {"source": "loop", "step": i},
                cp["channel_versions"],
            )

        tup = await saver.aget_tuple(thread_config())
        assert tup.checkpoint["id"] == "00000003"
        assert tup.checkpoint["channel_values"]["count"] == 3
        # Parent linkage preserved
        assert tup.parent_config["configurable"]["checkpoint_id"] == "00000002"

    async def test_get_specific_checkpoint_id(self, saver):
        for i in (1, 2):
            cp = make_checkpoint(f"0000000{i}", {"count": i})
            await saver.aput(thread_config(), cp, {"step": i}, cp["channel_versions"])

        tup = await saver.aget_tuple(thread_config(checkpoint_id="00000001"))
        assert tup.checkpoint["channel_values"]["count"] == 1

    async def test_threads_are_isolated(self, saver):
        cp_a = make_checkpoint("00000001", {"who": "a"})
        cp_b = make_checkpoint("00000001", {"who": "b"})
        await saver.aput(thread_config("thread-a"), cp_a, {}, cp_a["channel_versions"])
        await saver.aput(thread_config("thread-b"), cp_b, {}, cp_b["channel_versions"])

        assert (await saver.aget_tuple(thread_config("thread-a"))).checkpoint[
            "channel_values"
        ] == {"who": "a"}
        assert (await saver.aget_tuple(thread_config("thread-b"))).checkpoint[
            "channel_values"
        ] == {"who": "b"}

    async def test_restore_across_saver_instances(self, redis_client):
        """State survives a new saver instance (i.e. lives in Redis, not RAM)."""
        saver1 = PlainRedisSaver(redis_client, ttl_seconds=None)
        cp = make_checkpoint("00000001", {"messages": ["persisted"]})
        await saver1.aput(thread_config(), cp, {"step": 0}, cp["channel_versions"])

        saver2 = PlainRedisSaver(redis_client, ttl_seconds=None)
        tup = await saver2.aget_tuple(thread_config())
        assert tup.checkpoint["channel_values"] == {"messages": ["persisted"]}

    async def test_pending_writes_roundtrip(self, saver):
        cp = make_checkpoint("00000001", {"count": 0})
        await saver.aput(thread_config(), cp, {}, cp["channel_versions"])
        cfg = thread_config(checkpoint_id="00000001")
        await saver.aput_writes(cfg, [("count", 42), ("other", "x")], task_id="task-1")

        tup = await saver.aget_tuple(thread_config())
        assert ("task-1", "count", 42) in tup.pending_writes
        assert ("task-1", "other", "x") in tup.pending_writes

    async def test_alist_order_and_limit(self, saver):
        for i in (1, 2, 3):
            cp = make_checkpoint(f"0000000{i}", {"count": i})
            await saver.aput(thread_config(), cp, {"step": i}, cp["channel_versions"])

        ids = [t.checkpoint["id"] async for t in saver.alist(thread_config())]
        assert ids == ["00000003", "00000002", "00000001"]

        ids = [t.checkpoint["id"] async for t in saver.alist(thread_config(), limit=2)]
        assert ids == ["00000003", "00000002"]

        before = thread_config(checkpoint_id="00000003")
        ids = [t.checkpoint["id"] async for t in saver.alist(thread_config(), before=before)]
        assert ids == ["00000002", "00000001"]

    async def test_delete_thread(self, saver, redis_client):
        cp = make_checkpoint("00000001", {"count": 1})
        await saver.aput(thread_config(), cp, {}, cp["channel_versions"])
        await saver.aput_writes(
            thread_config(checkpoint_id="00000001"), [("count", 2)], task_id="t"
        )

        await saver.adelete_thread("thread-1")
        assert await saver.aget_tuple(thread_config()) is None
        assert await redis_client.keys("kubently:ckpt:*") == []

    async def test_ttl_applied_and_refreshed(self, redis_client):
        saver = PlainRedisSaver(redis_client, ttl_seconds=3600)
        cp = make_checkpoint("00000001", {"count": 1})
        await saver.aput(thread_config(), cp, {}, cp["channel_versions"])

        for key in await redis_client.keys("kubently:ckpt:*"):
            ttl = await redis_client.ttl(key)
            assert 0 < ttl <= 3600, f"{key} has no TTL"

        # A later checkpoint refreshes TTLs on the older keys too
        cp2 = make_checkpoint("00000002", {"count": 2})
        await saver.aput(thread_config(), cp2, {}, cp2["channel_versions"])
        ttl = await redis_client.ttl("kubently:ckpt:cp:thread-1::00000001")
        assert 0 < ttl <= 3600


# =============================================================================
# End-to-end: a real LangGraph graph checkpointed on plain Redis
# =============================================================================


class TestGraphIntegration:
    async def test_multi_turn_state_accumulates(self, redis_client):
        """Two ainvoke calls on the same thread share state via plain Redis —
        the cross-request memory scenario that RediSearch-less Redis must support."""
        from langgraph.graph import END, START, StateGraph

        class State(TypedDict):
            values: Annotated[list, operator.add]

        builder = StateGraph(State)
        builder.add_node("node", lambda state: {"values": ["tick"]})
        builder.add_edge(START, "node")
        builder.add_edge("node", END)

        saver = PlainRedisSaver(redis_client, ttl_seconds=None)
        graph = builder.compile(checkpointer=saver)
        cfg = {"configurable": {"thread_id": "conversation-1"}}

        r1 = await graph.ainvoke({"values": ["turn1"]}, cfg)
        assert r1["values"] == ["turn1", "tick"]

        # Second request: prior state must be restored from Redis
        r2 = await graph.ainvoke({"values": ["turn2"]}, cfg)
        assert r2["values"] == ["turn1", "tick", "turn2", "tick"]

        # A different thread starts clean
        other = await graph.ainvoke(
            {"values": ["fresh"]}, {"configurable": {"thread_id": "conversation-2"}}
        )
        assert other["values"] == ["fresh", "tick"]

    async def test_state_survives_process_restart_simulation(self, redis_client):
        """Recompile the graph with a fresh saver over the same Redis —
        equivalent to an API pod restart mid-conversation."""
        from langgraph.graph import END, START, StateGraph

        class State(TypedDict):
            values: Annotated[list, operator.add]

        def build(saver):
            builder = StateGraph(State)
            builder.add_node("node", lambda state: {"values": ["tick"]})
            builder.add_edge(START, "node")
            builder.add_edge("node", END)
            return builder.compile(checkpointer=saver)

        cfg = {"configurable": {"thread_id": "conversation-1"}}
        graph1 = build(PlainRedisSaver(redis_client, ttl_seconds=None))
        await graph1.ainvoke({"values": ["before"]}, cfg)

        graph2 = build(PlainRedisSaver(redis_client, ttl_seconds=None))
        r = await graph2.ainvoke({"values": ["after"]}, cfg)
        assert r["values"] == ["before", "tick", "after", "tick"]
