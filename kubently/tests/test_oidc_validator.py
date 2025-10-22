"""
Unit tests for OIDC validator module.
"""

import json
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest

from kubently.config.provider import OIDCConfig
from kubently.modules.auth.oidc_validator import OIDCValidator


@pytest.fixture
def oidc_config():
    """Create a test OIDC configuration."""
    return OIDCConfig(
        enabled=True,
        issuer="https://auth.example.com",
        client_id="test-client-id",
        jwks_uri="https://auth.example.com/jwks",
        token_endpoint="https://auth.example.com/token",
        device_endpoint="https://auth.example.com/device/code",
        audience="test-client-id",
        scopes=["openid", "email", "profile"]
    )


@pytest.fixture
def disabled_oidc_config():
    """Create a disabled OIDC configuration."""
    return OIDCConfig(
        enabled=False,
        issuer=None,
        client_id="test-client-id",
        jwks_uri=None,
        token_endpoint=None,
        device_endpoint=None,
        audience="test-client-id",
        scopes=["openid"]
    )


@pytest.fixture
def valid_jwt_claims():
    """Create valid JWT claims."""
    now = datetime.now(timezone.utc)
    return {
        "sub": "user-123",
        "email": "test@example.com",
        "name": "Test User",
        "iss": "https://auth.example.com",
        "aud": "test-client-id",
        "exp": int((now + timedelta(hours=1)).timestamp()),
        "iat": int(now.timestamp()),
        "groups": ["developers", "admins"],
        "roles": ["admin"]
    }


@pytest.fixture
def expired_jwt_claims():
    """Create expired JWT claims."""
    now = datetime.now(timezone.utc)
    return {
        "sub": "user-123",
        "email": "test@example.com",
        "iss": "https://auth.example.com",
        "aud": "test-client-id",
        "exp": int((now - timedelta(hours=1)).timestamp()),
        "iat": int((now - timedelta(hours=2)).timestamp())
    }


def create_test_jwt(claims: dict, secret: str = "test-secret") -> str:
    """Create a test JWT token."""
    return jwt.encode(claims, secret, algorithm="HS256")


@pytest.mark.asyncio
async def test_oidc_config_is_configured(oidc_config):
    """Test OIDC configuration validation."""
    assert oidc_config.is_configured is True


@pytest.mark.asyncio
async def test_oidc_config_not_configured_when_disabled(disabled_oidc_config):
    """Test OIDC configuration is not configured when disabled."""
    assert disabled_oidc_config.is_configured is False


@pytest.mark.asyncio
async def test_oidc_validator_initialization(oidc_config):
    """Test OIDCValidator initialization."""
    with patch('kubently.modules.auth.oidc_validator.PyJWKClient') as mock_jwk_client:
        validator = OIDCValidator(oidc_config)

        assert validator.issuer == "https://auth.example.com"
        assert validator.client_id == "test-client-id"
        assert validator.audience == "test-client-id"
        assert validator.jwks_uri == "https://auth.example.com/jwks"

        # Should initialize JWKS client when configured
        mock_jwk_client.assert_called_once()


@pytest.mark.asyncio
async def test_oidc_validator_disabled_config(disabled_oidc_config):
    """Test OIDCValidator with disabled configuration."""
    validator = OIDCValidator(disabled_oidc_config)

    # Should not initialize JWKS client when not configured
    assert validator.jwks_client is None


@pytest.mark.asyncio
async def test_validate_jwt_not_configured(disabled_oidc_config, valid_jwt_claims):
    """Test JWT validation when OIDC is not configured."""
    validator = OIDCValidator(disabled_oidc_config)
    token = create_test_jwt(valid_jwt_claims)

    is_valid, claims = await validator.validate_jwt_async(token)

    assert is_valid is False
    assert claims is None


@pytest.mark.asyncio
async def test_validate_jwt_with_bearer_prefix(oidc_config, valid_jwt_claims):
    """Test JWT validation strips Bearer prefix."""
    with patch('kubently.modules.auth.oidc_validator.PyJWKClient'):
        validator = OIDCValidator(oidc_config)

        # Mock the JWKS client to avoid actual verification
        mock_signing_key = MagicMock()
        mock_signing_key.key = "test-key"
        validator.jwks_client = MagicMock()
        validator.jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key

        token = create_test_jwt(valid_jwt_claims)
        bearer_token = f"Bearer {token}"

        # Mock jwt.decode to return valid claims
        with patch('jwt.decode', return_value=valid_jwt_claims):
            is_valid, claims = await validator.validate_jwt_async(bearer_token)

            assert is_valid is True
            assert claims is not None
            assert claims["email"] == "test@example.com"


@pytest.mark.asyncio
async def test_validate_jwt_expired_token(oidc_config, expired_jwt_claims):
    """Test JWT validation with expired token."""
    with patch('kubently.modules.auth.oidc_validator.PyJWKClient'):
        validator = OIDCValidator(oidc_config)

        mock_signing_key = MagicMock()
        mock_signing_key.key = "test-key"
        validator.jwks_client = MagicMock()
        validator.jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key

        token = create_test_jwt(expired_jwt_claims)

        # Mock jwt.decode to raise ExpiredSignatureError
        with patch('jwt.decode', side_effect=jwt.ExpiredSignatureError()):
            is_valid, claims = await validator.validate_jwt_async(token)

            assert is_valid is False
            assert claims is None


@pytest.mark.asyncio
async def test_validate_jwt_invalid_audience(oidc_config, valid_jwt_claims):
    """Test JWT validation with invalid audience."""
    with patch('kubently.modules.auth.oidc_validator.PyJWKClient'):
        validator = OIDCValidator(oidc_config)

        mock_signing_key = MagicMock()
        mock_signing_key.key = "test-key"
        validator.jwks_client = MagicMock()
        validator.jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key

        token = create_test_jwt(valid_jwt_claims)

        # Mock jwt.decode to raise InvalidAudienceError
        with patch('jwt.decode', side_effect=jwt.InvalidAudienceError()):
            is_valid, claims = await validator.validate_jwt_async(token)

            assert is_valid is False
            assert claims is None


@pytest.mark.asyncio
async def test_validate_jwt_invalid_issuer(oidc_config, valid_jwt_claims):
    """Test JWT validation with invalid issuer."""
    with patch('kubently.modules.auth.oidc_validator.PyJWKClient'):
        validator = OIDCValidator(oidc_config)

        mock_signing_key = MagicMock()
        mock_signing_key.key = "test-key"
        validator.jwks_client = MagicMock()
        validator.jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key

        token = create_test_jwt(valid_jwt_claims)

        # Mock jwt.decode to raise InvalidIssuerError
        with patch('jwt.decode', side_effect=jwt.InvalidIssuerError()):
            is_valid, claims = await validator.validate_jwt_async(token)

            assert is_valid is False
            assert claims is None


@pytest.mark.asyncio
async def test_validate_jwt_caching(oidc_config, valid_jwt_claims):
    """Test JWT validation caches results."""
    with patch('kubently.modules.auth.oidc_validator.PyJWKClient'):
        validator = OIDCValidator(oidc_config)

        mock_signing_key = MagicMock()
        mock_signing_key.key = "test-key"
        validator.jwks_client = MagicMock()
        validator.jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key

        token = create_test_jwt(valid_jwt_claims)

        # First validation - should call jwt.decode
        with patch('jwt.decode', return_value=valid_jwt_claims) as mock_decode:
            is_valid1, claims1 = await validator.validate_jwt_async(token)
            assert is_valid1 is True
            assert mock_decode.call_count == 1

            # Second validation - should use cache
            is_valid2, claims2 = await validator.validate_jwt_async(token)
            assert is_valid2 is True
            assert claims2 == claims1
            # Should not call decode again
            assert mock_decode.call_count == 1


@pytest.mark.asyncio
async def test_validate_jwt_cache_expiration(oidc_config, valid_jwt_claims):
    """Test JWT validation cache expires correctly."""
    with patch('kubently.modules.auth.oidc_validator.PyJWKClient'):
        validator = OIDCValidator(oidc_config)
        validator.cache_ttl = 1  # 1 second cache

        mock_signing_key = MagicMock()
        mock_signing_key.key = "test-key"
        validator.jwks_client = MagicMock()
        validator.jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key

        token = create_test_jwt(valid_jwt_claims)

        # First validation
        with patch('jwt.decode', return_value=valid_jwt_claims) as mock_decode:
            is_valid1, _ = await validator.validate_jwt_async(token)
            assert is_valid1 is True
            assert mock_decode.call_count == 1

            # Wait for cache to expire
            time.sleep(1.1)

            # Second validation - cache expired, should decode again
            is_valid2, _ = await validator.validate_jwt_async(token)
            assert is_valid2 is True
            assert mock_decode.call_count == 2


@pytest.mark.asyncio
async def test_extract_user_info(oidc_config, valid_jwt_claims):
    """Test extracting user info from JWT claims."""
    with patch('kubently.modules.auth.oidc_validator.PyJWKClient'):
        validator = OIDCValidator(oidc_config)

        user_info = validator.extract_user_info(valid_jwt_claims)

        assert user_info["sub"] == "user-123"
        assert user_info["email"] == "test@example.com"
        assert user_info["name"] == "Test User"
        assert user_info["groups"] == ["developers", "admins"]
        assert user_info["roles"] == ["admin"]
        assert user_info["iss"] == "https://auth.example.com"
        assert user_info["aud"] == "test-client-id"


@pytest.mark.asyncio
async def test_extract_user_info_minimal_claims(oidc_config):
    """Test extracting user info from minimal JWT claims."""
    with patch('kubently.modules.auth.oidc_validator.PyJWKClient'):
        validator = OIDCValidator(oidc_config)

        minimal_claims = {
            "sub": "user-123",
            "iss": "https://auth.example.com"
        }

        user_info = validator.extract_user_info(minimal_claims)

        assert user_info["sub"] == "user-123"
        assert user_info["email"] is None
        assert user_info["name"] is None
        assert user_info["groups"] == []
        assert user_info["roles"] == []


@pytest.mark.asyncio
async def test_validate_jwt_invalid_token(oidc_config):
    """Test JWT validation with invalid token."""
    with patch('kubently.modules.auth.oidc_validator.PyJWKClient'):
        validator = OIDCValidator(oidc_config)

        mock_signing_key = MagicMock()
        mock_signing_key.key = "test-key"
        validator.jwks_client = MagicMock()
        validator.jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key

        # Mock jwt.decode to raise InvalidTokenError
        with patch('jwt.decode', side_effect=jwt.InvalidTokenError()):
            is_valid, claims = await validator.validate_jwt_async("invalid.token.here")

            assert is_valid is False
            assert claims is None


@pytest.mark.asyncio
async def test_validate_jwt_unexpected_error(oidc_config, valid_jwt_claims):
    """Test JWT validation handles unexpected errors gracefully."""
    with patch('kubently.modules.auth.oidc_validator.PyJWKClient'):
        validator = OIDCValidator(oidc_config)

        mock_signing_key = MagicMock()
        mock_signing_key.key = "test-key"
        validator.jwks_client = MagicMock()
        validator.jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key

        token = create_test_jwt(valid_jwt_claims)

        # Mock jwt.decode to raise unexpected error
        with patch('jwt.decode', side_effect=Exception("Unexpected error")):
            is_valid, claims = await validator.validate_jwt_async(token)

            assert is_valid is False
            assert claims is None
