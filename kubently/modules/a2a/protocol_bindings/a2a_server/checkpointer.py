"""Checkpointer backend selection for the A2A server.

The A2A agent uses a LangGraph checkpointer for cross-request conversation
memory. Historically the only backend was AsyncRedisSaver from
langgraph-checkpoint-redis, which requires the RediSearch module (FT.CREATE /
FT.SEARCH). Managed Redis offerings such as Upstash don't ship RediSearch, so
this module adds selectable backends:

- ``redisearch`` (default): AsyncRedisSaver, unchanged from previous behavior.
  Requires a Redis server with the RediSearch module.
- ``plain-redis``: PlainRedisSaver (defined here), which uses only core Redis
  commands (HSET/ZADD/EXPIRE) and works on any Redis, including Upstash.
- ``memory``: LangGraph's InMemorySaver. Per-process only; for local dev/tests.
- ``none``: explicitly disable checkpointing. Single-request diagnoses still
  work; multi-turn memory is off.

Selection is via the KUBENTLY_CHECKPOINTER_BACKEND environment variable. If a
backend fails to initialize the agent degrades gracefully to no memory, same
as before.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from collections.abc import AsyncIterator, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)

logger = logging.getLogger(__name__)

BACKEND_ENV = "KUBENTLY_CHECKPOINTER_BACKEND"
TTL_ENV = "KUBENTLY_CHECKPOINT_TTL_SECONDS"

DEFAULT_BACKEND = "redisearch"
# Applies to the plain-redis backend only. 0 disables expiry. The default
# bounds key growth on managed Redis; the TTL is refreshed on every
# checkpoint write, so active conversations never expire mid-flight.
DEFAULT_TTL_SECONDS = 7 * 24 * 3600

# Canonical backend names, with accepted aliases.
_BACKEND_ALIASES = {
    "redisearch": "redisearch",
    "redis": "redisearch",
    "plain-redis": "plain-redis",
    "redis-plain": "plain-redis",
    "plain": "plain-redis",
    "memory": "memory",
    "in-memory": "memory",
    "none": "none",
    "disabled": "none",
    "off": "none",
}


def _b64e(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64d(data: str) -> bytes:
    return base64.b64decode(data.encode("ascii"))


def _s(value: Any) -> str:
    """Normalize a Redis reply to str (client may or may not decode responses)."""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


class PlainRedisSaver(BaseCheckpointSaver[str]):
    """Async LangGraph checkpoint saver using only core Redis commands.

    No RediSearch required: checkpoints are stored in hashes, ordered per
    thread by a sorted set of checkpoint IDs (uuid6 IDs sort chronologically).
    All binary payloads are base64-encoded so the saver works with clients
    created with decode_responses=True (as Kubently's is).

    Key layout (prefix configurable, default "kubently:ckpt"):
    - {p}:cp:{thread}:{ns}:{id}        hash: serialized checkpoint + metadata + parent id
    - {p}:idx:{thread}:{ns}            zset (score 0): checkpoint ids, lex order = time order
    - {p}:blob:{thread}:{ns}:{ch}:{v}  hash: one channel value at one version
    - {p}:wr:{thread}:{ns}:{id}        hash: pending writes for a checkpoint
    - {p}:threadkeys:{thread}          set: every key created for the thread
      (used for TTL refresh and thread deletion without SCAN)

    Async-only: the A2A server always drives graphs with ainvoke/astream, so
    the sync BaseCheckpointSaver methods keep their default NotImplementedError.
    """

    def __init__(
        self,
        redis_client: Any,
        *,
        prefix: str = "kubently:ckpt",
        ttl_seconds: int | None = DEFAULT_TTL_SECONDS,
        serde: Any = None,
    ) -> None:
        super().__init__(serde=serde)
        self._redis = redis_client
        self._prefix = prefix
        self._ttl = ttl_seconds if ttl_seconds and ttl_seconds > 0 else None

    # -- key helpers ---------------------------------------------------------

    def _cp_key(self, thread_id: str, ns: str, checkpoint_id: str) -> str:
        return f"{self._prefix}:cp:{thread_id}:{ns}:{checkpoint_id}"

    def _idx_key(self, thread_id: str, ns: str) -> str:
        return f"{self._prefix}:idx:{thread_id}:{ns}"

    def _blob_key(self, thread_id: str, ns: str, channel: str, version: Any) -> str:
        return f"{self._prefix}:blob:{thread_id}:{ns}:{channel}:{version}"

    def _writes_key(self, thread_id: str, ns: str, checkpoint_id: str) -> str:
        return f"{self._prefix}:wr:{thread_id}:{ns}:{checkpoint_id}"

    def _threadkeys_key(self, thread_id: str) -> str:
        return f"{self._prefix}:threadkeys:{thread_id}"

    # -- version generation --------------------------------------------------

    def get_next_version(self, current: str | int | float | None, channel: None = None) -> str:
        if current is None:
            current_v = 0
        elif isinstance(current, int):
            current_v = current
        else:
            current_v = int(str(current).split(".")[0])
        return f"{current_v + 1:032}"

    # -- write path ----------------------------------------------------------

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        thread_id = config["configurable"]["thread_id"]
        ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = checkpoint["id"]
        parent_id = config["configurable"].get("checkpoint_id")

        c = dict(checkpoint)
        channel_values: dict[str, Any] = c.pop("channel_values", {})

        cp_type, cp_data = self.serde.dumps_typed(c)
        md_type, md_data = self.serde.dumps_typed(get_checkpoint_metadata(config, metadata))

        cp_key = self._cp_key(thread_id, ns, checkpoint_id)
        idx_key = self._idx_key(thread_id, ns)
        wr_key = self._writes_key(thread_id, ns, checkpoint_id)
        tk_key = self._threadkeys_key(thread_id)

        pipe = self._redis.pipeline(transaction=True)
        pipe.hset(
            cp_key,
            mapping={
                "checkpoint_type": cp_type,
                "checkpoint": _b64e(cp_data),
                "metadata_type": md_type,
                "metadata": _b64e(md_data),
                "parent_id": parent_id or "",
            },
        )
        pipe.zadd(idx_key, {checkpoint_id: 0})

        touched = [cp_key, idx_key, wr_key]
        for channel, version in new_versions.items():
            blob_key = self._blob_key(thread_id, ns, channel, version)
            if channel in channel_values:
                blob_type, blob_data = self.serde.dumps_typed(channel_values[channel])
            else:
                blob_type, blob_data = "empty", b""
            pipe.hset(blob_key, mapping={"type": blob_type, "data": _b64e(blob_data)})
            touched.append(blob_key)

        pipe.sadd(tk_key, *touched)
        if self._ttl:
            # Refresh the whole thread's TTL on activity so an active
            # conversation never loses older checkpoints mid-conversation.
            for key in [*touched, tk_key]:
                pipe.expire(key, self._ttl)
        await pipe.execute()

        if self._ttl:
            # Keys created by earlier turns (older checkpoints/blobs) also get
            # their TTL refreshed, off the hot path of the pipeline above.
            existing = await self._redis.smembers(tk_key)
            stale = [_s(k) for k in existing if _s(k) not in set(touched)]
            if stale:
                refresh = self._redis.pipeline(transaction=False)
                for key in stale:
                    refresh.expire(key, self._ttl)
                await refresh.execute()

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id = config["configurable"]["thread_id"]
        ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"]["checkpoint_id"]
        wr_key = self._writes_key(thread_id, ns, checkpoint_id)
        tk_key = self._threadkeys_key(thread_id)

        pipe = self._redis.pipeline(transaction=True)
        for idx, (channel, value) in enumerate(writes):
            write_idx = WRITES_IDX_MAP.get(channel, idx)
            field = f"{task_id}\x1f{write_idx}"
            value_type, value_data = self.serde.dumps_typed(value)
            payload = json.dumps(
                {
                    "task_id": task_id,
                    "idx": write_idx,
                    "channel": channel,
                    "type": value_type,
                    "data": _b64e(value_data),
                    "task_path": task_path,
                }
            )
            if write_idx >= 0:
                # Regular writes are idempotent per (task, idx): first wins.
                pipe.hsetnx(wr_key, field, payload)
            else:
                # Special channels (error/interrupt/...) always overwrite.
                pipe.hset(wr_key, field, payload)
        pipe.sadd(tk_key, wr_key)
        if self._ttl:
            pipe.expire(wr_key, self._ttl)
            pipe.expire(tk_key, self._ttl)
        await pipe.execute()

    # -- read path -----------------------------------------------------------

    async def _load_pending_writes(
        self, thread_id: str, ns: str, checkpoint_id: str
    ) -> list[tuple[str, str, Any]]:
        raw = await self._redis.hgetall(self._writes_key(thread_id, ns, checkpoint_id))
        entries = []
        for payload in raw.values():
            entry = json.loads(_s(payload))
            entries.append(entry)
        entries.sort(key=lambda e: (e["task_id"], e["idx"]))
        return [
            (e["task_id"], e["channel"], self.serde.loads_typed((e["type"], _b64d(e["data"]))))
            for e in entries
        ]

    async def _load_channel_values(
        self, thread_id: str, ns: str, versions: ChannelVersions
    ) -> dict[str, Any]:
        if not versions:
            return {}
        channels = list(versions.items())
        pipe = self._redis.pipeline(transaction=False)
        for channel, version in channels:
            pipe.hgetall(self._blob_key(thread_id, ns, channel, version))
        blobs = await pipe.execute()
        values: dict[str, Any] = {}
        for (channel, _version), blob in zip(channels, blobs, strict=False):
            if not blob:
                continue
            blob = {_s(k): _s(v) for k, v in blob.items()}
            if blob.get("type") == "empty":
                continue
            values[channel] = self.serde.loads_typed((blob["type"], _b64d(blob["data"])))
        return values

    async def _load_tuple(
        self, thread_id: str, ns: str, checkpoint_id: str
    ) -> CheckpointTuple | None:
        raw = await self._redis.hgetall(self._cp_key(thread_id, ns, checkpoint_id))
        if not raw:
            return None
        raw = {_s(k): _s(v) for k, v in raw.items()}
        checkpoint: Checkpoint = self.serde.loads_typed(
            (raw["checkpoint_type"], _b64d(raw["checkpoint"]))
        )
        metadata: CheckpointMetadata = self.serde.loads_typed(
            (raw["metadata_type"], _b64d(raw["metadata"]))
        )
        checkpoint = {
            **checkpoint,
            "channel_values": await self._load_channel_values(
                thread_id, ns, checkpoint.get("channel_versions", {})
            ),
        }
        parent_id = raw.get("parent_id") or None
        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": ns,
                    "checkpoint_id": checkpoint_id,
                }
            },
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=(
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": ns,
                        "checkpoint_id": parent_id,
                    }
                }
                if parent_id
                else None
            ),
            pending_writes=await self._load_pending_writes(thread_id, ns, checkpoint_id),
        )

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id = config["configurable"]["thread_id"]
        ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = get_checkpoint_id(config)
        if not checkpoint_id:
            latest = await self._redis.zrange(self._idx_key(thread_id, ns), -1, -1)
            if not latest:
                return None
            checkpoint_id = _s(latest[0])
        return await self._load_tuple(thread_id, ns, checkpoint_id)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        if config is None:
            # Listing across all threads requires a key scan; the A2A server
            # never lists without a thread, so this is intentionally
            # unsupported to stay SCAN-free on managed Redis.
            raise NotImplementedError("PlainRedisSaver.alist requires a thread_id in config")
        thread_id = config["configurable"]["thread_id"]
        ns = config["configurable"].get("checkpoint_ns", "")
        config_checkpoint_id = get_checkpoint_id(config)
        before_id = get_checkpoint_id(before) if before else None

        ids = [_s(i) for i in await self._redis.zrange(self._idx_key(thread_id, ns), 0, -1)]
        remaining = limit
        for checkpoint_id in reversed(ids):
            if config_checkpoint_id and checkpoint_id != config_checkpoint_id:
                continue
            if before_id and checkpoint_id >= before_id:
                continue
            tup = await self._load_tuple(thread_id, ns, checkpoint_id)
            if tup is None:
                continue
            if filter and not all(tup.metadata.get(k) == v for k, v in filter.items()):
                continue
            if remaining is not None:
                if remaining <= 0:
                    break
                remaining -= 1
            yield tup

    async def adelete_thread(self, thread_id: str) -> None:
        tk_key = self._threadkeys_key(thread_id)
        keys = [_s(k) for k in await self._redis.smembers(tk_key)]
        if keys:
            await self._redis.delete(*keys)
        await self._redis.delete(tk_key)


def _resolve_backend() -> str:
    raw = os.getenv(BACKEND_ENV, DEFAULT_BACKEND).strip().lower()
    backend = _BACKEND_ALIASES.get(raw)
    if backend is None:
        raise ValueError(
            f"Unknown {BACKEND_ENV}={raw!r}. "
            f"Valid values: redisearch (default), plain-redis, memory, none"
        )
    return backend


def _resolve_ttl() -> int | None:
    raw = os.getenv(TTL_ENV, "").strip()
    if not raw:
        return DEFAULT_TTL_SECONDS
    ttl = int(raw)
    return ttl if ttl > 0 else None


async def create_checkpointer(redis_client: Any) -> BaseCheckpointSaver | None:
    """Build the configured checkpointer backend, or None if unavailable.

    Returns None (rather than raising) when checkpointing is disabled or no
    Redis client exists — the agent then runs without cross-request memory,
    exactly as before. Backend initialization errors propagate so the caller
    can log and degrade.
    """
    backend = _resolve_backend()

    if backend == "none":
        logger.info("Checkpointer explicitly disabled (%s=none)", BACKEND_ENV)
        return None

    if backend == "memory":
        from langgraph.checkpoint.memory import InMemorySaver

        logger.info("Using in-memory checkpointer (per-process, dev/test only)")
        return InMemorySaver()

    if redis_client is None:
        logger.info("No Redis client available; running without conversation memory")
        return None

    await redis_client.ping()

    if backend == "plain-redis":
        saver = PlainRedisSaver(redis_client, ttl_seconds=_resolve_ttl())
        logger.info("Using plain-Redis checkpointer (no RediSearch required, ttl=%s)", saver._ttl)
        return saver

    # Default: RediSearch-backed saver, unchanged from previous behavior.
    from langgraph.checkpoint.redis.aio import AsyncRedisSaver

    saver = AsyncRedisSaver(redis_client=redis_client)
    await saver.setup()
    logger.info("Using RediSearch checkpointer (AsyncRedisSaver)")
    return saver
