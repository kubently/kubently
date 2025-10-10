import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import List, Optional


class QueueModule:
    def __init__(self, redis_client, max_commands_per_fetch: int = 10):
        """
        Initialize queue module.

        Args:
            redis_client: Async Redis client
            max_commands_per_fetch: Max commands to return per fetch
        """
        self.redis = redis_client
        self.max_commands_per_fetch = max_commands_per_fetch

    async def push_command(self, cluster_id: str, command: dict) -> str:
        """
        Add command to cluster's queue.

        Args:
            cluster_id: Target cluster
            command: Command dict with at least 'id' and 'args'

        Returns:
            Command ID

        Logic:
        1. Ensure command has ID
        2. Add timestamp
        3. Push to cluster queue (LPUSH for FIFO with RPOP)
        4. Set expiration on queue
        """
        if "id" not in command:
            command["id"] = str(uuid.uuid4())

        command["queued_at"] = datetime.now(UTC).isoformat()
        command["cluster_id"] = cluster_id

        queue_key = f"queue:commands:{cluster_id}"

        await self.redis.lpush(queue_key, json.dumps(command))

        await self.redis.expire(queue_key, 300)

        tracking_key = f"command:tracking:{command['id']}"
        await self.redis.setex(
            tracking_key,
            60,
            json.dumps({"cluster_id": cluster_id, "queued_at": command["queued_at"]}),
        )

        await self._increment_metric("commands_queued", cluster_id)

        return command["id"]

    async def pull_commands(self, cluster_id: str, wait: int = 0) -> List[dict]:
        """
        Pull commands from queue with optional blocking.

        THIS IS THE KEY METHOD FOR LOW LATENCY.

        Args:
            cluster_id: Cluster identifier
            wait: Seconds to wait for commands (0 = non-blocking)

        Returns:
            List of commands (empty if none available)

        Logic for blocking mode (wait > 0):
        1. Use BRPOP for blocking right pop
        2. Returns immediately when command available
        3. Returns None after timeout

        Logic for non-blocking mode (wait = 0):
        1. Use RPOP to get multiple commands
        2. Return immediately
        """
        queue_key = f"queue:commands:{cluster_id}"
        commands = []

        if wait > 0:
            try:
                result = await self.redis.brpop(queue_key, timeout=wait)

                if result:
                    command = json.loads(result[1])
                    commands.append(command)

                    await self._record_latency(command)

            except asyncio.TimeoutError:
                pass

        else:
            for _ in range(self.max_commands_per_fetch):
                cmd_json = await self.redis.rpop(queue_key)
                if not cmd_json:
                    break

                command = json.loads(cmd_json)
                commands.append(command)

                await self._record_latency(command)

        if commands:
            await self._increment_metric("commands_delivered", cluster_id, len(commands))

        return commands

    async def store_result(self, command_id: str, result: dict) -> None:
        """
        Store command execution result.

        Args:
            command_id: Command identifier
            result: Execution result dict

        Logic:
        1. Store result with TTL
        2. Publish notification for waiters
        3. Update metrics
        """
        result["stored_at"] = datetime.now(UTC).isoformat()

        # Convert any datetime objects to ISO format strings
        for key, value in result.items():
            if hasattr(value, "isoformat"):
                result[key] = value.isoformat()

        result_key = f"result:{command_id}"
        await self.redis.setex(result_key, 60, json.dumps(result))

        channel = f"result:ready:{command_id}"
        await self.redis.publish(channel, "1")

        tracking_key = f"command:tracking:{command_id}"
        tracking = await self.redis.get(tracking_key)
        if tracking:
            tracking_data = json.loads(tracking)
            cluster_id = tracking_data.get("cluster_id")

            if result.get("success"):
                await self._increment_metric("commands_succeeded", cluster_id)
            else:
                await self._increment_metric("commands_failed", cluster_id)

    async def wait_for_result(self, command_id: str, timeout: int = 10) -> Optional[dict]:
        """
        Wait for command result (blocking).

        Used by API to wait synchronously for command completion.

        Args:
            command_id: Command identifier
            timeout: Max seconds to wait

        Returns:
            Result dict or None if timeout

        Logic:
        1. Check if result already exists
        2. If not, poll with exponential backoff
        3. Could be optimized with pub/sub
        """
        result_key = f"result:{command_id}"

        result = await self.redis.get(result_key)
        if result:
            return json.loads(result)

        elapsed = 0
        poll_interval = 0.1

        while elapsed < timeout:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

            result = await self.redis.get(result_key)
            if result:
                return json.loads(result)

            poll_interval = min(poll_interval * 1.5, 1.0)

        await self._increment_metric("commands_timeout", "global")
        return None

    async def get_queue_depth(self, cluster_id: str) -> int:
        """
        Get number of pending commands.

        Args:
            cluster_id: Cluster identifier

        Returns:
            Number of commands in queue
        """
        queue_key = f"queue:commands:{cluster_id}"
        return await self.redis.llen(queue_key)

    async def clear_queue(self, cluster_id: str) -> int:
        """
        Clear all pending commands for cluster.

        Used for emergency stop or cleanup.

        Args:
            cluster_id: Cluster identifier

        Returns:
            Number of commands cleared
        """
        queue_key = f"queue:commands:{cluster_id}"

        count = await self.redis.llen(queue_key)

        await self.redis.delete(queue_key)

        return count

    async def _record_latency(self, command: dict):
        """Record command delivery latency"""
        if "queued_at" in command:
            queued_at = datetime.fromisoformat(command["queued_at"])
            latency_ms = (datetime.now(UTC) - queued_at).total_seconds() * 1000

            await self.redis.lpush("metrics:delivery_latency", latency_ms)
            await self.redis.ltrim("metrics:delivery_latency", 0, 999)

    async def _increment_metric(self, metric: str, cluster_id: str, count: int = 1):
        """Increment counter metric"""
        key = f"metrics:{metric}:{cluster_id}"
        await self.redis.incrby(key, count)

        await self.redis.expire(key, 86400)
