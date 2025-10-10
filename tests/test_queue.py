import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from kubently.api.queue import QueueModule


@pytest_asyncio.fixture
async def redis_mock():
    """Create a mock Redis client"""
    redis = AsyncMock()

    redis.lpush = AsyncMock()
    redis.rpop = AsyncMock()
    redis.brpop = AsyncMock()
    redis.llen = AsyncMock()
    redis.setex = AsyncMock()
    redis.get = AsyncMock()
    redis.delete = AsyncMock()
    redis.expire = AsyncMock()
    redis.publish = AsyncMock()
    redis.incrby = AsyncMock()
    redis.ltrim = AsyncMock()

    return redis


@pytest_asyncio.fixture
async def queue_module(redis_mock):
    """Create a QueueModule instance with mocked Redis"""
    return QueueModule(redis_mock, max_commands_per_fetch=5)


@pytest.mark.asyncio
async def test_push_command_with_id(queue_module, redis_mock):
    """Test pushing a command that already has an ID"""
    command = {"id": "test-123", "args": ["get", "pods"]}

    command_id = await queue_module.push_command("cluster-1", command)

    assert command_id == "test-123"

    redis_mock.lpush.assert_called_once()
    call_args = redis_mock.lpush.call_args[0]
    assert call_args[0] == "queue:commands:cluster-1"

    pushed_command = json.loads(call_args[1])
    assert pushed_command["id"] == "test-123"
    assert pushed_command["cluster_id"] == "cluster-1"
    assert "queued_at" in pushed_command

    redis_mock.expire.assert_any_call("queue:commands:cluster-1", 300)

    redis_mock.setex.assert_called_once()
    tracking_args = redis_mock.setex.call_args[0]
    assert tracking_args[0] == "command:tracking:test-123"
    assert tracking_args[1] == 60


@pytest.mark.asyncio
async def test_push_command_without_id(queue_module, redis_mock):
    """Test pushing a command without an ID generates one"""
    command = {"args": ["get", "pods"]}

    command_id = await queue_module.push_command("cluster-1", command)

    assert command_id is not None
    assert len(command_id) == 36

    redis_mock.lpush.assert_called_once()
    pushed_data = redis_mock.lpush.call_args[0][1]
    pushed_command = json.loads(pushed_data)
    assert pushed_command["id"] == command_id


@pytest.mark.asyncio
async def test_pull_commands_blocking_with_result(queue_module, redis_mock):
    """Test blocking pull that receives a command"""
    command_data = {
        "id": "cmd-123",
        "args": ["get", "pods"],
        "queued_at": datetime.now(UTC).isoformat(),
    }

    redis_mock.brpop.return_value = ("queue:commands:cluster-1", json.dumps(command_data))

    commands = await queue_module.pull_commands("cluster-1", wait=5)

    assert len(commands) == 1
    assert commands[0]["id"] == "cmd-123"

    redis_mock.brpop.assert_called_once_with("queue:commands:cluster-1", timeout=5)

    redis_mock.incrby.assert_any_call("metrics:commands_delivered:cluster-1", 1)


@pytest.mark.asyncio
async def test_pull_commands_blocking_timeout(queue_module, redis_mock):
    """Test blocking pull that times out"""
    redis_mock.brpop.return_value = None

    commands = await queue_module.pull_commands("cluster-1", wait=1)

    assert commands == []
    redis_mock.brpop.assert_called_once_with("queue:commands:cluster-1", timeout=1)


@pytest.mark.asyncio
async def test_pull_commands_non_blocking(queue_module, redis_mock):
    """Test non-blocking pull of multiple commands"""
    commands_data = [
        {"id": "cmd-1", "args": ["get", "pods"], "queued_at": datetime.now(UTC).isoformat()},
        {"id": "cmd-2", "args": ["get", "services"], "queued_at": datetime.now(UTC).isoformat()},
        None,
    ]

    redis_mock.rpop.side_effect = [json.dumps(cmd) if cmd else None for cmd in commands_data]

    commands = await queue_module.pull_commands("cluster-1", wait=0)

    assert len(commands) == 2
    assert commands[0]["id"] == "cmd-1"
    assert commands[1]["id"] == "cmd-2"

    assert redis_mock.rpop.call_count == 3

    redis_mock.incrby.assert_any_call("metrics:commands_delivered:cluster-1", 2)


@pytest.mark.asyncio
async def test_store_result_success(queue_module, redis_mock):
    """Test storing a successful command result"""
    tracking_data = {"cluster_id": "cluster-1", "queued_at": datetime.now(UTC).isoformat()}
    redis_mock.get.return_value = json.dumps(tracking_data)

    result = {"success": True, "output": "pod listing..."}

    await queue_module.store_result("cmd-123", result)

    redis_mock.setex.assert_called_once()
    call_args = redis_mock.setex.call_args[0]
    assert call_args[0] == "result:cmd-123"
    assert call_args[1] == 60

    stored_result = json.loads(call_args[2])
    assert stored_result["success"] == True
    assert "stored_at" in stored_result

    redis_mock.publish.assert_called_once_with("result:ready:cmd-123", "1")

    redis_mock.incrby.assert_any_call("metrics:commands_succeeded:cluster-1", 1)


@pytest.mark.asyncio
async def test_store_result_failure(queue_module, redis_mock):
    """Test storing a failed command result"""
    tracking_data = {"cluster_id": "cluster-1", "queued_at": datetime.now(UTC).isoformat()}
    redis_mock.get.return_value = json.dumps(tracking_data)

    result = {"success": False, "error": "Command failed"}

    await queue_module.store_result("cmd-123", result)

    redis_mock.incrby.assert_any_call("metrics:commands_failed:cluster-1", 1)


@pytest.mark.asyncio
async def test_wait_for_result_immediate(queue_module, redis_mock):
    """Test waiting for a result that's already available"""
    result_data = {"success": True, "output": "Done"}
    redis_mock.get.return_value = json.dumps(result_data)

    result = await queue_module.wait_for_result("cmd-123", timeout=5)

    assert result == result_data
    redis_mock.get.assert_called_once_with("result:cmd-123")


@pytest.mark.asyncio
async def test_wait_for_result_polling(queue_module, redis_mock):
    """Test waiting for a result that appears after polling"""
    result_data = {"success": True, "output": "Done"}

    redis_mock.get.side_effect = [None, None, json.dumps(result_data)]

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await queue_module.wait_for_result("cmd-123", timeout=2)

    assert result == result_data
    assert redis_mock.get.call_count == 3


@pytest.mark.asyncio
async def test_wait_for_result_timeout(queue_module, redis_mock):
    """Test waiting for a result that times out"""
    redis_mock.get.return_value = None

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await queue_module.wait_for_result("cmd-123", timeout=0.5)

    assert result is None
    redis_mock.incrby.assert_any_call("metrics:commands_timeout:global", 1)


@pytest.mark.asyncio
async def test_get_queue_depth(queue_module, redis_mock):
    """Test getting the queue depth"""
    redis_mock.llen.return_value = 5

    depth = await queue_module.get_queue_depth("cluster-1")

    assert depth == 5
    redis_mock.llen.assert_called_once_with("queue:commands:cluster-1")


@pytest.mark.asyncio
async def test_clear_queue(queue_module, redis_mock):
    """Test clearing the queue"""
    redis_mock.llen.return_value = 3

    count = await queue_module.clear_queue("cluster-1")

    assert count == 3
    redis_mock.llen.assert_called_once_with("queue:commands:cluster-1")
    redis_mock.delete.assert_called_once_with("queue:commands:cluster-1")


@pytest.mark.asyncio
async def test_record_latency(queue_module, redis_mock):
    """Test recording command delivery latency"""
    queued_at = datetime.now(UTC)
    command = {"id": "cmd-123", "queued_at": queued_at.isoformat()}

    await queue_module._record_latency(command)

    redis_mock.lpush.assert_called_once()
    call_args = redis_mock.lpush.call_args[0]
    assert call_args[0] == "metrics:delivery_latency"

    latency = call_args[1]
    assert isinstance(latency, float)
    assert latency >= 0

    redis_mock.ltrim.assert_called_once_with("metrics:delivery_latency", 0, 999)


@pytest.mark.asyncio
async def test_increment_metric(queue_module, redis_mock):
    """Test incrementing metrics"""
    await queue_module._increment_metric("test_metric", "cluster-1", 5)

    redis_mock.incrby.assert_called_once_with("metrics:test_metric:cluster-1", 5)
    redis_mock.expire.assert_any_call("metrics:test_metric:cluster-1", 86400)


@pytest.mark.asyncio
async def test_concurrent_push_commands(queue_module, redis_mock):
    """Test concurrent command pushing"""
    commands = [
        {"args": ["get", "pods"]},
        {"args": ["get", "services"]},
        {"args": ["get", "deployments"]},
    ]

    tasks = [queue_module.push_command("cluster-1", cmd) for cmd in commands]

    command_ids = await asyncio.gather(*tasks)

    assert len(command_ids) == 3
    assert all(cmd_id for cmd_id in command_ids)
    assert redis_mock.lpush.call_count == 3


@pytest.mark.asyncio
async def test_pull_commands_max_fetch_limit(queue_module, redis_mock):
    """Test that pull_commands respects max_commands_per_fetch"""
    queue_module.max_commands_per_fetch = 2

    commands_data = [
        {"id": f"cmd-{i}", "queued_at": datetime.now(UTC).isoformat()} for i in range(5)
    ]

    redis_mock.rpop.side_effect = [json.dumps(cmd) for cmd in commands_data]

    commands = await queue_module.pull_commands("cluster-1", wait=0)

    assert len(commands) == 2
    assert redis_mock.rpop.call_count == 2
