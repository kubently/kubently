#!/usr/bin/env python3
"""
Test script for the authentication middleware module.

This demonstrates the black box nature of the middleware - 
it can be tested independently without the rest of the system.
"""

import asyncio
from typing import Tuple, Optional
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient


# Mock auth validator for testing
async def mock_validator(key: str) -> Tuple[bool, Optional[str]]:
    """Mock API key validator for testing."""
    valid_keys = {
        "test-key-123": "test-service",
        "admin-key": "admin-service"
    }
    if key in valid_keys:
        return True, valid_keys[key]
    return False, None


def test_auth_middleware():
    """Test the authentication middleware independently."""
    from kubently.modules.middleware import AuthMiddleware
    
    # Create test app
    app = FastAPI()
    
    # Create middleware with mock validator
    auth_middleware = AuthMiddleware(
        auth_validator=mock_validator,
        skip_paths={"/health": ["GET"], "/": ["GET"]},
        error_format="json",
        log_attempts=False  # Disable logging for tests
    )
    
    # Register middleware
    @app.middleware("http")
    async def add_auth(request: Request, call_next):
        return await auth_middleware(request, call_next)
    
    # Add test endpoints
    @app.get("/health")
    def health():
        return {"status": "ok"}
    
    @app.get("/")
    def root():
        return {"message": "public endpoint"}
    
    @app.get("/protected")
    def protected():
        return {"data": "secret"}
    
    # Create test client
    client = TestClient(app)
    
    # Test cases
    print("Testing Authentication Middleware")
    print("=" * 50)
    
    # Test 1: Public endpoint (should work without auth)
    print("\n1. Testing public endpoint /health...")
    response = client.get("/health")
    assert response.status_code == 200
    print(f"   ✅ Status: {response.status_code}")
    
    # Test 2: Protected endpoint without auth (should fail)
    print("\n2. Testing protected endpoint without auth...")
    response = client.get("/protected")
    assert response.status_code == 401
    assert "Authentication required" in response.json()["error"]
    print(f"   ✅ Correctly rejected: {response.status_code}")
    
    # Test 3: Protected endpoint with invalid key (should fail)
    print("\n3. Testing protected endpoint with invalid key...")
    response = client.get("/protected", headers={"X-API-Key": "invalid"})
    assert response.status_code == 401
    assert "Invalid API key" in response.json()["error"]
    print(f"   ✅ Correctly rejected: {response.status_code}")
    
    # Test 4: Protected endpoint with valid key (should work)
    print("\n4. Testing protected endpoint with valid key...")
    response = client.get("/protected", headers={"X-API-Key": "test-key-123"})
    assert response.status_code == 200
    assert response.json() == {"data": "secret"}
    print(f"   ✅ Successfully authenticated: {response.status_code}")
    
    print("\n" + "=" * 50)
    print("All tests passed! ✅")


def test_jsonrpc_format():
    """Test JSON-RPC error format."""
    from kubently.modules.middleware import AuthMiddleware
    
    app = FastAPI()
    
    # Create middleware with JSON-RPC format
    auth_middleware = AuthMiddleware(
        auth_validator=mock_validator,
        error_format="jsonrpc",
        log_attempts=False
    )
    
    @app.middleware("http")
    async def add_auth(request: Request, call_next):
        return await auth_middleware(request, call_next)
    
    @app.post("/rpc")
    def rpc_endpoint():
        return {"result": "success"}
    
    client = TestClient(app)
    
    print("\nTesting JSON-RPC Error Format")
    print("=" * 50)
    
    response = client.post("/rpc")
    assert response.status_code == 401
    error_data = response.json()
    assert "jsonrpc" in error_data
    assert error_data["jsonrpc"] == "2.0"
    assert "error" in error_data
    assert error_data["error"]["code"] == -32700
    print(f"✅ JSON-RPC error format: {error_data}")


if __name__ == "__main__":
    test_auth_middleware()
    test_jsonrpc_format()
    print("\n✨ Middleware module is working correctly!")