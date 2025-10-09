import asyncio
import json
import os
import sys
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, call

import pytest

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kubently.api.session import SessionModule


@pytest.fixture
def mock_redis():
    """Create a mock Redis client."""
    redis = AsyncMock()
    redis.setex = AsyncMock()
    redis.get = AsyncMock()
    redis.exists = AsyncMock()
    redis.delete = AsyncMock()
    redis.sadd = AsyncMock()
    redis.srem = AsyncMock()
    redis.smembers = AsyncMock()
    redis.expire = AsyncMock()
    redis.publish = AsyncMock()
    redis.lpush = AsyncMock()
    redis.ltrim = AsyncMock()
    return redis


@pytest.fixture
def session_module(mock_redis):
    """Create a SessionModule instance with mock Redis."""
    return SessionModule(mock_redis, default_ttl=300)


@pytest.mark.asyncio
async def test_create_session(session_module, mock_redis):
    """Test session creation with all features."""
    # Create session with A2A tracking
    session_id = await session_module.create_session(
        cluster_id="test-cluster",
        user_id="test-user",
        correlation_id="corr-123",
        service_identity="orchestrator",
    )

    # Verify UUID format
    assert len(session_id) == 36
    assert session_id.count("-") == 4

    # Verify Redis calls
    assert mock_redis.setex.call_count >= 3  # session, cluster:active, cluster:session

    # Verify session data was stored
    session_key_call = None
    for call_args in mock_redis.setex.call_args_list:
        if f"session:" in str(call_args[0][0]):
            session_key_call = call_args
            break

    assert session_key_call is not None
    session_data = json.loads(session_key_call[0][2])
    assert session_data["cluster_id"] == "test-cluster"
    assert session_data["user_id"] == "test-user"
    assert session_data["correlation_id"] == "corr-123"
    assert session_data["service_identity"] == "orchestrator"
    assert session_data["command_count"] == 0

    # Verify cluster was marked as active
    cluster_active_call = None
    for call_args in mock_redis.setex.call_args_list:
        if "cluster:active:test-cluster" in str(call_args[0][0]):
            cluster_active_call = call_args
            break

    assert cluster_active_call is not None
    assert cluster_active_call[0][1] == 300  # TTL
    assert cluster_active_call[0][2] == session_id

    # Verify session was added to active set
    mock_redis.sadd.assert_any_call("sessions:active", session_id)

    # Verify correlation indexing
    mock_redis.sadd.assert_any_call("correlation:corr-123:sessions", session_id)

    # Verify event was published
    assert mock_redis.publish.called


@pytest.mark.asyncio
async def test_create_session_without_optional_params(session_module, mock_redis):
    """Test session creation with minimal parameters."""
    session_id = await session_module.create_session(cluster_id="test-cluster")

    # Verify session was created
    assert len(session_id) == 36

    # Verify default values were set
    session_key_call = None
    for call_args in mock_redis.setex.call_args_list:
        if f"session:" in str(call_args[0][0]):
            session_key_call = call_args
            break

    session_data = json.loads(session_key_call[0][2])
    assert session_data["user_id"] == "anonymous"
    assert session_data["correlation_id"] is None
    assert session_data["service_identity"] is None


@pytest.mark.asyncio
async def test_get_session(session_module, mock_redis):
    """Test retrieving session data."""
    session_data = {
        "session_id": "test-session-id",
        "cluster_id": "test-cluster",
        "user_id": "test-user",
        "created_at": datetime.utcnow().isoformat(),
        "command_count": 5,
    }

    mock_redis.get.return_value = json.dumps(session_data)

    result = await session_module.get_session("test-session-id")

    assert result == session_data
    mock_redis.get.assert_called_once_with("session:test-session-id")


@pytest.mark.asyncio
async def test_get_session_not_found(session_module, mock_redis):
    """Test retrieving non-existent session."""
    mock_redis.get.return_value = None

    result = await session_module.get_session("non-existent")

    assert result is None
    mock_redis.get.assert_called_once_with("session:non-existent")


@pytest.mark.asyncio
async def test_is_cluster_active(session_module, mock_redis):
    """Test cluster active check (hot path)."""
    # Test active cluster
    mock_redis.exists.return_value = 1

    is_active = await session_module.is_cluster_active("test-cluster")

    assert is_active is True
    mock_redis.exists.assert_called_once_with("cluster:active:test-cluster")

    # Test inactive cluster
    mock_redis.reset_mock()
    mock_redis.exists.return_value = 0

    is_active = await session_module.is_cluster_active("inactive-cluster")

    assert is_active is False
    mock_redis.exists.assert_called_once_with("cluster:active:inactive-cluster")


@pytest.mark.asyncio
async def test_keep_alive(session_module, mock_redis):
    """Test session TTL extension."""
    session_data = {
        "session_id": "test-session-id",
        "cluster_id": "test-cluster",
        "user_id": "test-user",
        "correlation_id": "corr-123",
        "created_at": datetime.utcnow().isoformat(),
        "last_activity": datetime.utcnow().isoformat(),
        "command_count": 3,
        "ttl": 300,
    }

    mock_redis.get.return_value = json.dumps(session_data)

    result = await session_module.keep_alive("test-session-id")

    assert result is True

    # Verify session was updated
    assert mock_redis.setex.called
    updated_call = mock_redis.setex.call_args_list[0]
    updated_data = json.loads(updated_call[0][2])
    assert updated_data["command_count"] == 4
    assert updated_data["last_activity"] != session_data["last_activity"]

    # Verify TTL extensions
    mock_redis.expire.assert_any_call("cluster:active:test-cluster", 300)
    mock_redis.expire.assert_any_call("cluster:session:test-cluster", 300)
    mock_redis.expire.assert_any_call("correlation:corr-123:sessions", 300)


@pytest.mark.asyncio
async def test_keep_alive_session_not_found(session_module, mock_redis):
    """Test keep_alive for non-existent session."""
    mock_redis.get.return_value = None

    result = await session_module.keep_alive("non-existent")

    assert result is False
    mock_redis.setex.assert_not_called()


@pytest.mark.asyncio
async def test_end_session(session_module, mock_redis):
    """Test ending a session."""
    session_data = {
        "session_id": "test-session-id",
        "cluster_id": "test-cluster",
        "correlation_id": "corr-123",
        "service_identity": "orchestrator",
        "user_id": "test-user",
    }

    mock_redis.get.return_value = json.dumps(session_data)

    await session_module.end_session("test-session-id")

    # Verify all related keys were deleted
    expected_keys = [
        "session:test-session-id",
        "cluster:active:test-cluster",
        "cluster:session:test-cluster",
    ]
    mock_redis.delete.assert_called_once_with(*expected_keys)

    # Verify removal from active set
    mock_redis.srem.assert_any_call("sessions:active", "test-session-id")

    # Verify removal from correlation index
    mock_redis.srem.assert_any_call("correlation:corr-123:sessions", "test-session-id")

    # Verify event was published
    assert mock_redis.publish.called


@pytest.mark.asyncio
async def test_end_session_not_found(session_module, mock_redis):
    """Test ending a non-existent session."""
    mock_redis.get.return_value = None

    await session_module.end_session("non-existent")

    # Should not attempt to delete anything
    mock_redis.delete.assert_not_called()
    mock_redis.srem.assert_not_called()


@pytest.mark.asyncio
async def test_get_active_sessions(session_module, mock_redis):
    """Test retrieving all active sessions."""
    # Setup mock data
    session_ids = ["session-1", "session-2", "session-3"]
    mock_redis.smembers.return_value = session_ids

    session_data_1 = {"session_id": "session-1", "cluster_id": "cluster-1"}
    session_data_2 = {"session_id": "session-2", "cluster_id": "cluster-2"}

    # session-3 is stale (no data)
    mock_redis.get.side_effect = [
        json.dumps(session_data_1),
        json.dumps(session_data_2),
        None,  # session-3 is stale
    ]

    result = await session_module.get_active_sessions()

    assert len(result) == 2
    assert result[0] == session_data_1
    assert result[1] == session_data_2

    # Verify stale session was cleaned up
    mock_redis.srem.assert_called_once_with("sessions:active", "session-3")


@pytest.mark.asyncio
async def test_get_sessions_by_correlation(session_module, mock_redis):
    """Test retrieving sessions by correlation ID."""
    # Setup mock data
    session_ids = ["session-1", "session-2", "session-3"]
    mock_redis.smembers.return_value = session_ids

    session_data_1 = {
        "session_id": "session-1",
        "cluster_id": "cluster-1",
        "correlation_id": "corr-123",
    }
    session_data_2 = {
        "session_id": "session-2",
        "cluster_id": "cluster-2",
        "correlation_id": "corr-123",
    }

    # session-3 is stale
    mock_redis.get.side_effect = [json.dumps(session_data_1), json.dumps(session_data_2), None]

    result = await session_module.get_sessions_by_correlation("corr-123")

    assert len(result) == 2
    assert result[0]["correlation_id"] == "corr-123"
    assert result[1]["correlation_id"] == "corr-123"

    # Verify correlation key was queried
    mock_redis.smembers.assert_called_once_with("correlation:corr-123:sessions")

    # Verify stale session was cleaned up
    mock_redis.srem.assert_called_once_with("correlation:corr-123:sessions", "session-3")


@pytest.mark.asyncio
async def test_cleanup_expired(session_module, mock_redis):
    """Test cleanup of expired sessions."""
    # Setup mock data
    session_ids = ["session-1", "session-2", "session-3", "session-4"]
    mock_redis.smembers.return_value = session_ids

    # session-1 and session-3 are expired
    mock_redis.exists.side_effect = [
        0,  # session-1 expired
        1,  # session-2 active
        0,  # session-3 expired
        1,  # session-4 active
    ]

    cleaned_count = await session_module.cleanup_expired()

    assert cleaned_count == 2

    # Verify expired sessions were removed
    calls = mock_redis.srem.call_args_list
    assert len(calls) == 2
    assert calls[0] == call("sessions:active", "session-1")
    assert calls[1] == call("sessions:active", "session-3")


@pytest.mark.asyncio
async def test_publish_event(session_module, mock_redis):
    """Test event publishing mechanism."""
    event_data = {"session_id": "test-session", "cluster_id": "test-cluster"}

    await session_module._publish_event("test.event", event_data)

    # Verify event was published to pub/sub
    assert mock_redis.publish.called
    publish_call = mock_redis.publish.call_args
    assert publish_call[0][0] == "events:session"

    event = json.loads(publish_call[0][1])
    assert event["type"] == "test.event"
    assert event["data"] == event_data
    assert "timestamp" in event

    # Verify event was stored in history
    assert mock_redis.lpush.called
    assert mock_redis.ltrim.called_with("session:events", 0, 999)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
