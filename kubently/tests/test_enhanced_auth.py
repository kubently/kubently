"""
Unit tests for enhanced authentication module with dual auth support.
"""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kubently.modules.auth.enhanced import EnhancedAuthModule


@pytest.fixture
def redis_mock():
    """Create a mock Redis client."""
    redis = AsyncMock()
    redis.lpush = AsyncMock()
    redis.ltrim = AsyncMock()
    return redis


@pytest.fixture
def token_validator_mock():
    """Create a mock TokenValidator."""
    validator = MagicMock()
    validator.validate_jwt_async = AsyncMock()
    return validator


@pytest.fixture
def base_auth_mock():
    """Create a mock base AuthModule."""
    auth = AsyncMock()
    auth.verify_api_key = AsyncMock()
    return auth


@pytest.fixture
def enhanced_auth(redis_mock, base_auth_mock, token_validator_mock):
    """Create an EnhancedAuthModule instance with mocks."""
    return EnhancedAuthModule(
        redis_client=redis_mock,
        base_auth_module=base_auth_mock,
        token_validator=token_validator_mock
    )


@pytest.mark.asyncio
async def test_verify_credentials_with_valid_jwt(enhanced_auth, token_validator_mock, redis_mock):
    """Test credential verification with valid JWT token."""
    # Mock successful JWT validation
    jwt_claims = {
        "sub": "user-123",
        "email": "test@example.com",
        "groups": ["developers"],
        "name": "Test User"
    }
    token_validator_mock.validate_jwt_async.return_value = (True, jwt_claims)

    bearer_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."

    is_valid, identity, auth_method = await enhanced_auth.verify_credentials(
        api_key=None,
        bearer_token=bearer_token
    )

    assert is_valid is True
    assert identity == "test@example.com"
    assert auth_method == "jwt"

    # Verify JWT validation was called
    token_validator_mock.validate_jwt_async.assert_called_once_with(bearer_token)

    # Verify audit log was created
    assert redis_mock.lpush.called
    call_args = redis_mock.lpush.call_args[0]
    assert call_args[0] == "auth:audit:enhanced"
    event = json.loads(call_args[1])
    assert event["type"] == "jwt_authenticated"
    assert event["data"]["user"] == "test@example.com"


@pytest.mark.asyncio
async def test_verify_credentials_jwt_without_email(enhanced_auth, token_validator_mock):
    """Test credential verification with JWT that has no email (uses sub)."""
    jwt_claims = {
        "sub": "user-123",
        "name": "Test User"
    }
    token_validator_mock.validate_jwt_async.return_value = (True, jwt_claims)

    bearer_token = "valid.jwt.token"

    is_valid, identity, auth_method = await enhanced_auth.verify_credentials(
        api_key=None,
        bearer_token=bearer_token
    )

    assert is_valid is True
    assert identity == "user-123"  # Should fall back to sub
    assert auth_method == "jwt"


@pytest.mark.asyncio
async def test_verify_credentials_with_invalid_jwt(enhanced_auth, token_validator_mock, base_auth_mock):
    """Test credential verification with invalid JWT."""
    # Mock failed JWT validation
    token_validator_mock.validate_jwt_async.return_value = (False, None)

    # No API key provided either
    base_auth_mock.verify_api_key.return_value = (False, None)

    bearer_token = "invalid.jwt.token"

    is_valid, identity, auth_method = await enhanced_auth.verify_credentials(
        api_key=None,
        bearer_token=bearer_token
    )

    assert is_valid is False
    assert identity is None
    assert auth_method is None


@pytest.mark.asyncio
async def test_verify_credentials_with_valid_api_key(enhanced_auth, base_auth_mock, token_validator_mock):
    """Test credential verification with valid API key."""
    # Mock successful API key validation
    base_auth_mock.verify_api_key.return_value = (True, "test-service")

    api_key = "test-api-key-123"

    is_valid, identity, auth_method = await enhanced_auth.verify_credentials(
        api_key=api_key,
        bearer_token=None
    )

    assert is_valid is True
    assert identity == "test-service"
    assert auth_method == "api_key"

    # Verify API key validation was called
    base_auth_mock.verify_api_key.assert_called_once_with(api_key)


@pytest.mark.asyncio
async def test_verify_credentials_with_invalid_api_key(enhanced_auth, base_auth_mock):
    """Test credential verification with invalid API key."""
    # Mock failed API key validation
    base_auth_mock.verify_api_key.return_value = (False, None)

    api_key = "invalid-key"

    is_valid, identity, auth_method = await enhanced_auth.verify_credentials(
        api_key=api_key,
        bearer_token=None
    )

    assert is_valid is False
    assert identity is None
    assert auth_method is None


@pytest.mark.asyncio
async def test_verify_credentials_jwt_preferred_over_api_key(
    enhanced_auth, token_validator_mock, base_auth_mock
):
    """Test that JWT is tried first when both JWT and API key are provided."""
    # Mock successful JWT validation
    jwt_claims = {"sub": "user-123", "email": "test@example.com"}
    token_validator_mock.validate_jwt_async.return_value = (True, jwt_claims)

    bearer_token = "valid.jwt.token"
    api_key = "valid-api-key"

    is_valid, identity, auth_method = await enhanced_auth.verify_credentials(
        api_key=api_key,
        bearer_token=bearer_token
    )

    # Should use JWT authentication
    assert is_valid is True
    assert identity == "test@example.com"
    assert auth_method == "jwt"

    # API key should not be checked since JWT succeeded
    base_auth_mock.verify_api_key.assert_not_called()


@pytest.mark.asyncio
async def test_verify_credentials_fallback_to_api_key(
    enhanced_auth, token_validator_mock, base_auth_mock
):
    """Test fallback to API key when JWT validation fails."""
    # Mock failed JWT validation
    token_validator_mock.validate_jwt_async.return_value = (False, None)

    # Mock successful API key validation
    base_auth_mock.verify_api_key.return_value = (True, "service")

    bearer_token = "invalid.jwt.token"
    api_key = "valid-api-key"

    is_valid, identity, auth_method = await enhanced_auth.verify_credentials(
        api_key=api_key,
        bearer_token=bearer_token
    )

    # Should fall back to API key authentication
    assert is_valid is True
    assert identity == "service"
    assert auth_method == "api_key"


@pytest.mark.asyncio
async def test_verify_credentials_no_credentials(enhanced_auth):
    """Test credential verification with no credentials provided."""
    is_valid, identity, auth_method = await enhanced_auth.verify_credentials(
        api_key=None,
        bearer_token=None
    )

    assert is_valid is False
    assert identity is None
    assert auth_method is None


@pytest.mark.asyncio
async def test_get_user_permissions_jwt(enhanced_auth):
    """Test getting permissions for JWT-authenticated user."""
    permissions = await enhanced_auth.get_user_permissions(
        identity="test@example.com",
        auth_method="jwt"
    )

    assert permissions["clusters"] == ["*"]
    assert "debug" in permissions["operations"]
    assert "execute" in permissions["operations"]
    assert permissions["admin"] is False


@pytest.mark.asyncio
async def test_get_user_permissions_api_key(enhanced_auth):
    """Test getting permissions for API key-authenticated service."""
    permissions = await enhanced_auth.get_user_permissions(
        identity="service-name",
        auth_method="api_key"
    )

    assert permissions["clusters"] == ["*"]
    assert permissions["operations"] == ["*"]
    assert permissions["admin"] is True


@pytest.mark.asyncio
async def test_get_auth_stats(enhanced_auth, token_validator_mock, base_auth_mock):
    """Test authentication statistics tracking."""
    # Perform some authentications
    jwt_claims = {"sub": "user-123", "email": "test@example.com"}
    token_validator_mock.validate_jwt_async.return_value = (True, jwt_claims)

    # 2 JWT authentications
    await enhanced_auth.verify_credentials(bearer_token="jwt1")
    await enhanced_auth.verify_credentials(bearer_token="jwt2")

    # 1 API key authentication
    base_auth_mock.verify_api_key.return_value = (True, "service")
    await enhanced_auth.verify_credentials(api_key="key1")

    # 1 failed authentication
    token_validator_mock.validate_jwt_async.return_value = (False, None)
    base_auth_mock.verify_api_key.return_value = (False, None)
    await enhanced_auth.verify_credentials(bearer_token="invalid")

    # Get stats
    stats = await enhanced_auth.get_auth_stats()

    assert stats["stats"]["jwt"] == 2
    assert stats["stats"]["api_key"] == 1
    assert stats["stats"]["failed"] == 1
    assert "timestamp" in stats


@pytest.mark.asyncio
async def test_audit_logging_format(enhanced_auth, token_validator_mock, redis_mock):
    """Test audit log format is correct."""
    jwt_claims = {
        "sub": "user-123",
        "email": "test@example.com",
        "groups": ["developers", "admins"]
    }
    token_validator_mock.validate_jwt_async.return_value = (True, jwt_claims)

    await enhanced_auth.verify_credentials(bearer_token="valid.jwt.token")

    # Verify audit log format
    assert redis_mock.lpush.called
    call_args = redis_mock.lpush.call_args[0]

    event = json.loads(call_args[1])
    assert "type" in event
    assert "data" in event
    assert "timestamp" in event
    assert event["data"]["user"] == "test@example.com"
    assert event["data"]["sub"] == "user-123"
    assert event["data"]["groups"] == ["developers", "admins"]

    # Verify audit log is trimmed
    redis_mock.ltrim.assert_called_once_with("auth:audit:enhanced", 0, 9999)


@pytest.mark.asyncio
async def test_audit_logging_without_redis(token_validator_mock, base_auth_mock):
    """Test authentication works even without Redis (no audit logging)."""
    # Create enhanced auth without Redis
    enhanced_auth = EnhancedAuthModule(
        redis_client=None,
        base_auth_module=base_auth_mock,
        token_validator=token_validator_mock
    )

    jwt_claims = {"sub": "user-123", "email": "test@example.com"}
    token_validator_mock.validate_jwt_async.return_value = (True, jwt_claims)

    # Should work without Redis
    is_valid, identity, auth_method = await enhanced_auth.verify_credentials(
        bearer_token="valid.jwt.token"
    )

    assert is_valid is True
    assert identity == "test@example.com"
    assert auth_method == "jwt"
