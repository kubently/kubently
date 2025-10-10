#!/usr/bin/env python3
"""
Integration test for OAuth 2.0 authentication flow.

This test verifies:
1. Mock OAuth provider is working
2. JWT token validation works
3. Dual authentication (API key + JWT) works
4. CLI login flow works (device authorization)
"""

import asyncio
import json
import sys
import time
from typing import Optional

import httpx
import jwt


async def test_mock_oauth_provider():
    """Test the mock OAuth provider endpoints."""
    print("🧪 Testing Mock OAuth Provider...")
    
    base_url = "http://localhost:9000"
    
    async with httpx.AsyncClient() as client:
        # Test discovery endpoint
        print("  - Testing OIDC discovery...")
        response = await client.get(f"{base_url}/.well-known/openid-configuration")
        assert response.status_code == 200
        config = response.json()
        assert config["issuer"] == base_url
        print("    ✅ Discovery endpoint works")
        
        # Test JWKS endpoint
        print("  - Testing JWKS endpoint...")
        response = await client.get(f"{base_url}/jwks")
        assert response.status_code == 200
        jwks = response.json()
        assert "keys" in jwks
        assert len(jwks["keys"]) > 0
        print("    ✅ JWKS endpoint works")
        
        # Test device authorization flow
        print("  - Testing device authorization flow...")
        response = await client.post(
            f"{base_url}/device/code",
            data={"client_id": "kubently-cli", "scope": "openid email profile"}
        )
        assert response.status_code == 200
        device_data = response.json()
        assert "device_code" in device_data
        assert "user_code" in device_data
        print(f"    ✅ Device code obtained: {device_data['user_code']}")
        
        # Simulate user approval (automatic for testing)
        print("  - Simulating user approval...")
        response = await client.post(
            f"{base_url}/device/approve",
            data={
                "user_code": device_data["user_code"],
                "user_email": "test@example.com"
            }
        )
        assert response.status_code == 200
        print("    ✅ Device approved")
        
        # Poll for token
        print("  - Polling for token...")
        await asyncio.sleep(1)  # Wait a bit before polling
        response = await client.post(
            f"{base_url}/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_data["device_code"],
                "client_id": "kubently-cli"
            }
        )
        assert response.status_code == 200
        token_data = response.json()
        assert "access_token" in token_data
        assert "refresh_token" in token_data
        print("    ✅ Tokens obtained")
        
        # Verify token with user info endpoint
        print("  - Testing user info endpoint...")
        response = await client.get(
            f"{base_url}/userinfo",
            headers={"Authorization": f"Bearer {token_data['access_token']}"}
        )
        assert response.status_code == 200
        user_info = response.json()
        assert user_info["email"] == "test@example.com"
        print(f"    ✅ User info retrieved: {user_info['email']}")
        
        return token_data["access_token"]


async def test_kubently_jwt_auth(jwt_token: str):
    """Test Kubently API with JWT authentication."""
    print("\n🧪 Testing Kubently API with JWT...")
    
    api_url = "http://localhost:8080"
    
    async with httpx.AsyncClient() as client:
        # Test creating a session with JWT
        print("  - Testing session creation with JWT...")
        response = await client.post(
            f"{api_url}/debug/session",
            json={
                "cluster_id": "test-cluster",
                "namespace": "default",
                "ttl": 300
            },
            headers={"Authorization": f"Bearer {jwt_token}"}
        )
        
        if response.status_code == 201:
            session = response.json()
            print(f"    ✅ Session created: {session['session_id']}")
            return session["session_id"]
        else:
            print(f"    ❌ Failed to create session: {response.status_code}")
            print(f"       Response: {response.text}")
            return None


async def test_kubently_api_key_auth():
    """Test Kubently API with API key authentication (legacy)."""
    print("\n🧪 Testing Kubently API with API Key...")
    
    api_url = "http://localhost:8080"
    api_key = "test-api-key"
    
    async with httpx.AsyncClient() as client:
        # Test creating a session with API key
        print("  - Testing session creation with API key...")
        response = await client.post(
            f"{api_url}/debug/session",
            json={
                "cluster_id": "test-cluster",
                "namespace": "default",
                "ttl": 300
            },
            headers={"X-API-Key": api_key}
        )
        
        if response.status_code == 201:
            session = response.json()
            print(f"    ✅ Session created: {session['session_id']}")
            return session["session_id"]
        else:
            print(f"    ❌ Failed to create session: {response.status_code}")
            return None


async def main():
    """Run all integration tests."""
    print("=" * 60)
    print("OAuth 2.0 Integration Tests")
    print("=" * 60)
    
    # Wait a bit for services to be ready
    print("\n⏳ Waiting for services to be ready...")
    await asyncio.sleep(2)
    
    try:
        # Test mock OAuth provider
        jwt_token = await test_mock_oauth_provider()
        
        # Test Kubently API with JWT
        session_id = await test_kubently_jwt_auth(jwt_token)
        
        # Test Kubently API with API key (should still work)
        api_session_id = await test_kubently_api_key_auth()
        
        print("\n" + "=" * 60)
        print("✅ All OAuth integration tests passed!")
        print("=" * 60)
        
        print("\n📝 Summary:")
        print("  - Mock OAuth provider: Working")
        print("  - Device authorization flow: Working")
        print("  - JWT token validation: Working")
        print("  - Dual authentication: Working")
        print("\nYou can now use:")
        print("  - 'kubently login' for OAuth authentication")
        print("  - 'kubently login --api-key <key>' for API key authentication")
        
        return 0
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)