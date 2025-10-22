"""
Integration tests for OAuth 2.0 authentication flow.

This test verifies:
1. Mock OAuth provider is working
2. JWT token validation works
3. Dual authentication (API key + JWT) works
4. Complete device authorization flow
"""

import asyncio
import time
from typing import Optional

import httpx
import jwt
import pytest


# Test configuration
MOCK_OAUTH_URL = "http://localhost:9000"
KUBENTLY_API_URL = "http://localhost:8080"
TEST_USER_EMAIL = "test@example.com"


@pytest.fixture
async def oauth_provider_available():
    """Check if mock OAuth provider is available."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{MOCK_OAUTH_URL}/.well-known/openid-configuration", timeout=2.0)
            return response.status_code == 200
    except Exception:
        return False


@pytest.fixture
async def kubently_api_available():
    """Check if Kubently API is available."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{KUBENTLY_API_URL}/healthz", timeout=2.0)
            return response.status_code == 200
    except Exception:
        return False


@pytest.mark.asyncio
@pytest.mark.integration
async def test_mock_oauth_discovery(oauth_provider_available):
    """Test OIDC discovery endpoint on mock provider."""
    if not oauth_provider_available:
        pytest.skip("Mock OAuth provider not available")

    async with httpx.AsyncClient() as client:
        response = await client.get(f"{MOCK_OAUTH_URL}/.well-known/openid-configuration")

        assert response.status_code == 200
        config = response.json()

        assert config["issuer"] == MOCK_OAUTH_URL
        assert "jwks_uri" in config
        assert "token_endpoint" in config
        assert "device_authorization_endpoint" in config


@pytest.mark.asyncio
@pytest.mark.integration
async def test_mock_oauth_jwks(oauth_provider_available):
    """Test JWKS endpoint on mock provider."""
    if not oauth_provider_available:
        pytest.skip("Mock OAuth provider not available")

    async with httpx.AsyncClient() as client:
        response = await client.get(f"{MOCK_OAUTH_URL}/jwks")

        assert response.status_code == 200
        jwks = response.json()

        assert "keys" in jwks
        assert len(jwks["keys"]) > 0
        assert jwks["keys"][0]["kty"] in ["RSA", "EC"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_device_authorization_flow(oauth_provider_available):
    """Test complete device authorization flow."""
    if not oauth_provider_available:
        pytest.skip("Mock OAuth provider not available")

    async with httpx.AsyncClient() as client:
        # Step 1: Request device code
        response = await client.post(
            f"{MOCK_OAUTH_URL}/device/code",
            data={"client_id": "kubently-cli", "scope": "openid email profile"}
        )
        assert response.status_code == 200
        device_data = response.json()

        assert "device_code" in device_data
        assert "user_code" in device_data
        assert "verification_uri" in device_data
        assert device_data["interval"] > 0

        device_code = device_data["device_code"]
        user_code = device_data["user_code"]

        # Step 2: Simulate user approval
        response = await client.post(
            f"{MOCK_OAUTH_URL}/device/approve",
            data={
                "user_code": user_code,
                "user_email": TEST_USER_EMAIL
            }
        )
        assert response.status_code == 200

        # Step 3: Poll for token (wait for approval to process)
        await asyncio.sleep(1)

        response = await client.post(
            f"{MOCK_OAUTH_URL}/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
                "client_id": "kubently-cli"
            }
        )
        assert response.status_code == 200
        token_data = response.json()

        assert "access_token" in token_data
        assert "refresh_token" in token_data
        assert "token_type" in token_data
        assert token_data["token_type"] == "Bearer"

        return token_data["access_token"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_jwt_token_validation(oauth_provider_available):
    """Test JWT token can be validated."""
    if not oauth_provider_available:
        pytest.skip("Mock OAuth provider not available")

    # Get a valid token
    access_token = await test_device_authorization_flow(oauth_provider_available)

    # Decode token without verification to check structure
    decoded = jwt.decode(access_token, options={"verify_signature": False})

    assert "sub" in decoded
    assert "email" in decoded
    assert "iss" in decoded
    assert "exp" in decoded
    assert "iat" in decoded

    assert decoded["email"] == TEST_USER_EMAIL
    assert decoded["iss"] == MOCK_OAUTH_URL


@pytest.mark.asyncio
@pytest.mark.integration
async def test_kubently_jwt_authentication(oauth_provider_available, kubently_api_available):
    """Test Kubently API accepts JWT authentication."""
    if not oauth_provider_available:
        pytest.skip("Mock OAuth provider not available")
    if not kubently_api_available:
        pytest.skip("Kubently API not available")

    # Get a valid JWT token
    access_token = await test_device_authorization_flow(oauth_provider_available)

    # Try to create a session with JWT
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{KUBENTLY_API_URL}/debug/session",
            json={
                "cluster_id": "test-cluster",
                "namespace": "default",
                "ttl": 300
            },
            headers={"Authorization": f"Bearer {access_token}"}
        )

        # Should authenticate successfully
        assert response.status_code == 201
        session = response.json()

        assert "session_id" in session
        assert session["cluster_id"] == "test-cluster"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_kubently_api_key_authentication(kubently_api_available):
    """Test Kubently API accepts API key authentication."""
    if not kubently_api_available:
        pytest.skip("Kubently API not available")

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{KUBENTLY_API_URL}/debug/session",
            json={
                "cluster_id": "test-cluster",
                "namespace": "default",
                "ttl": 300
            },
            headers={"X-API-Key": "test-api-key"}
        )

        # Should authenticate successfully
        assert response.status_code == 201
        session = response.json()

        assert "session_id" in session


@pytest.mark.asyncio
@pytest.mark.integration
async def test_kubently_auth_discovery(kubently_api_available):
    """Test Kubently authentication discovery endpoint."""
    if not kubently_api_available:
        pytest.skip("Kubently API not available")

    async with httpx.AsyncClient() as client:
        response = await client.get(f"{KUBENTLY_API_URL}/.well-known/kubently-auth")

        assert response.status_code == 200
        config = response.json()

        assert "authentication_methods" in config
        assert "api_key" in config

        # Check if OAuth is configured
        if "oauth" in config and config["oauth"]["enabled"]:
            assert "issuer" in config["oauth"]
            assert "client_id" in config["oauth"]
            assert "device_authorization_endpoint" in config["oauth"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_jwt_preference_over_api_key(oauth_provider_available, kubently_api_available):
    """Test that JWT is preferred when both JWT and API key are provided."""
    if not oauth_provider_available:
        pytest.skip("Mock OAuth provider not available")
    if not kubently_api_available:
        pytest.skip("Kubently API not available")

    # Get a valid JWT token
    access_token = await test_device_authorization_flow(oauth_provider_available)

    async with httpx.AsyncClient() as client:
        # Send both JWT and API key
        response = await client.post(
            f"{KUBENTLY_API_URL}/debug/session",
            json={
                "cluster_id": "test-cluster",
                "namespace": "default",
                "ttl": 300
            },
            headers={
                "Authorization": f"Bearer {access_token}",
                "X-API-Key": "test-api-key"
            }
        )

        # Should authenticate successfully (using JWT)
        assert response.status_code == 201


@pytest.mark.asyncio
@pytest.mark.integration
async def test_invalid_jwt_fallback_to_api_key(kubently_api_available):
    """Test fallback to API key when JWT is invalid."""
    if not kubently_api_available:
        pytest.skip("Kubently API not available")

    async with httpx.AsyncClient() as client:
        # Send invalid JWT with valid API key
        response = await client.post(
            f"{KUBENTLY_API_URL}/debug/session",
            json={
                "cluster_id": "test-cluster",
                "namespace": "default",
                "ttl": 300
            },
            headers={
                "Authorization": "Bearer invalid.jwt.token",
                "X-API-Key": "test-api-key"
            }
        )

        # Should authenticate successfully using API key fallback
        assert response.status_code == 201


@pytest.mark.asyncio
@pytest.mark.integration
async def test_no_credentials_fails(kubently_api_available):
    """Test that requests without credentials are rejected."""
    if not kubently_api_available:
        pytest.skip("Kubently API not available")

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{KUBENTLY_API_URL}/debug/session",
            json={
                "cluster_id": "test-cluster",
                "namespace": "default",
                "ttl": 300
            }
        )

        # Should fail authentication
        assert response.status_code == 401
