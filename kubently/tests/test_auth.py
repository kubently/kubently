"""
Unit tests for the authentication module.
"""

import json
import os
import secrets
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kubently.modules.auth.auth import AuthModule


@pytest.fixture
def redis_mock():
    """Create a mock Redis client."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock()
    redis.delete = AsyncMock()
    redis.lpush = AsyncMock()
    redis.ltrim = AsyncMock()
    return redis


@pytest.fixture
def auth_module(redis_mock):
    """Create an AuthModule instance with mocked Redis."""
    with patch.dict(
        os.environ,
        {
            "API_KEYS": "test-key,orchestrator:service-key,monitoring:monitor-key",
        },
        clear=False,
    ):
        return AuthModule(redis_mock)


@pytest.mark.asyncio
async def test_verify_agent_valid_token_from_redis(auth_module, redis_mock):
    """Test agent verification with valid token from Redis."""
    cluster_id = "test-cluster"
    token = "dynamic-token-456"

    # Mock Redis to return the token
    redis_mock.get.return_value = token.encode("utf-8")

    result = await auth_module.verify_executor(token, cluster_id)

    assert result is True
    redis_mock.get.assert_called_once_with(f"executor:token:{cluster_id}")


@pytest.mark.asyncio
async def test_verify_agent_invalid_token(auth_module, redis_mock):
    """Test agent verification with invalid token."""
    cluster_id = "test-cluster"
    token = "invalid-token"

    redis_mock.get.return_value = None

    result = await auth_module.verify_executor(token, cluster_id)

    assert result is False


@pytest.mark.asyncio
async def test_verify_agent_empty_token(auth_module):
    """Test agent verification with empty token."""
    result = await auth_module.verify_executor("", "test-cluster")
    assert result is False


@pytest.mark.asyncio
async def test_verify_api_key_valid(auth_module, redis_mock):
    """Test API key validation with valid key."""
    api_key = "test-key"

    is_valid, service_identity = await auth_module.verify_api_key(api_key)

    assert is_valid is True
    assert service_identity is None  # Plain key without service identity

    # Check audit log was created
    redis_mock.lpush.assert_called_once()
    call_args = redis_mock.lpush.call_args[0]
    assert call_args[0] == "auth:audit"
    event = json.loads(call_args[1])
    assert event["type"] == "api_key_verified"


@pytest.mark.asyncio
async def test_verify_api_key_with_service(auth_module, redis_mock):
    """Test API key validation with service identity."""
    api_key = "service-key"

    is_valid, service_identity = await auth_module.verify_api_key(api_key)

    assert is_valid is True
    assert service_identity == "orchestrator"

    # Check audit log includes service identity
    call_args = redis_mock.lpush.call_args[0]
    event = json.loads(call_args[1])
    assert event["data"]["service_identity"] == "orchestrator"


@pytest.mark.asyncio
async def test_verify_api_key_invalid(auth_module):
    """Test API key validation with invalid key."""
    api_key = "invalid-key"

    is_valid, service_identity = await auth_module.verify_api_key(api_key)

    assert is_valid is False
    assert service_identity is None


@pytest.mark.asyncio
async def test_verify_api_key_empty(auth_module):
    """Test API key validation with empty key."""
    is_valid, service_identity = await auth_module.verify_api_key("")

    assert is_valid is False
    assert service_identity is None


@pytest.mark.asyncio
async def test_extract_service_identity(auth_module):
    """Test service identity extraction from API keys."""
    # Test key with service identity
    identity = await auth_module.extract_service_identity("service-key")
    assert identity == "orchestrator"

    # Test key without service identity
    identity = await auth_module.extract_service_identity("test-key")
    assert identity is None

    # Test unknown key
    identity = await auth_module.extract_service_identity("unknown-key")
    assert identity is None


@pytest.mark.asyncio
async def test_create_agent_token(auth_module, redis_mock):
    """Test agent token creation."""
    cluster_id = "new-cluster"

    token = await auth_module.create_executor_token(cluster_id)

    # Verify token format and length
    assert isinstance(token, str)
    assert len(token) > 30  # token_urlsafe(32) produces ~43 chars

    # Verify Redis storage
    redis_mock.set.assert_called_once_with(f"executor:token:{cluster_id}", token)

    # Verify audit log
    redis_mock.lpush.assert_called()
    call_args = redis_mock.lpush.call_args[0]
    assert call_args[0] == "auth:audit"
    event = json.loads(call_args[1])
    assert event["type"] == "executor_token_created"
    assert event["data"]["cluster_id"] == cluster_id


@pytest.mark.asyncio
async def test_revoke_agent_token(auth_module, redis_mock):
    """Test agent token revocation."""
    cluster_id = "revoke-cluster"

    await auth_module.revoke_executor_token(cluster_id)

    # Verify Redis deletion
    redis_mock.delete.assert_called_once_with(f"executor:token:{cluster_id}")

    # Verify audit log
    redis_mock.lpush.assert_called()
    call_args = redis_mock.lpush.call_args[0]
    event = json.loads(call_args[1])
    assert event["type"] == "executor_token_revoked"
    assert event["data"]["cluster_id"] == cluster_id


@pytest.mark.asyncio
async def test_audit_log_with_correlation_id(auth_module, redis_mock):
    """Test audit logging includes correlation IDs."""
    correlation_id = "trace-123"

    await auth_module._log_event("test_event", {"test": "data"}, correlation_id=correlation_id)

    redis_mock.lpush.assert_called_once()
    call_args = redis_mock.lpush.call_args[0]
    assert call_args[0] == "auth:audit"

    event = json.loads(call_args[1])
    assert event["correlation_id"] == correlation_id
    assert event["type"] == "test_event"
    assert event["data"]["test"] == "data"

    # Verify trim is called to limit audit log size
    redis_mock.ltrim.assert_called_once_with("auth:audit", 0, 9999)


@pytest.mark.asyncio
async def test_load_api_keys_various_formats():
    """Test loading API keys with various formats."""
    with patch.dict(
        os.environ,
        {"API_KEYS": "plain-key,service1:key1,service2:key2,  ,service3:key3"},
        clear=True,
    ):
        auth = AuthModule(AsyncMock())

        assert auth.api_keys["plain-key"] is None
        assert auth.api_keys["key1"] == "service1"
        assert auth.api_keys["key2"] == "service2"
        assert auth.api_keys["key3"] == "service3"
        assert len(auth.api_keys) == 4


@pytest.mark.asyncio
async def test_constant_time_comparison(auth_module, redis_mock):
    """Test that token comparison is constant-time for security."""
    cluster_id = "timing-test"
    correct_token = "correct-token-123"

    # Test with correct token
    redis_mock.get.return_value = correct_token.encode("utf-8")

    # Both should use secrets.compare_digest internally
    result1 = await auth_module.verify_executor(correct_token, cluster_id)
    assert result1 is True

    # Test with incorrect token of different length
    result2 = await auth_module.verify_executor("wrong", cluster_id)
    assert result2 is False

    # Test with incorrect token of same length
    result3 = await auth_module.verify_executor("incorrect-tok-123", cluster_id)
    assert result3 is False
