#!/usr/bin/env python3
"""
Test script to verify A2A authentication is working correctly.

Usage:
    python test_a2a_auth.py [API_URL] [API_KEY]
    
Examples:
    python test_a2a_auth.py  # Uses defaults
    python test_a2a_auth.py http://localhost:8080 test-api-key
"""

import asyncio
import httpx
import json
import sys
import os


async def test_a2a_auth(api_url: str, valid_api_key: str):
    """Test A2A authentication with various scenarios."""
    
    print(f"Testing A2A authentication at {api_url}")
    print("=" * 60)
    
    # Test 1: Request without API key (should fail)
    print("\n1. Testing request WITHOUT API key...")
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{api_url}/",
                json={
                    "jsonrpc": "2.0",
                    "id": "1",
                    "method": "message/send",
                    "params": {
                        "message": {
                            "messageId": "test-1",
                            "role": "user",
                            "parts": [{"text": "list clusters"}],
                            "contextId": "test-session-1"
                        }
                    }
                },
                timeout=10.0
            )
            print(f"  Status: {response.status_code}")
            if response.status_code == 401:
                print("  ✅ Correctly rejected (401 Unauthorized)")
            else:
                print(f"  ❌ Should have been rejected but got: {response.status_code}")
            print(f"  Response: {response.text[:200]}")
        except httpx.ConnectError as e:
            print(f"  ⚠️  Server not reachable: {e}")
            return
        except Exception as e:
            print(f"  ❌ Error: {e}")
    
    # Test 2: Request with invalid API key (should fail)
    print("\n2. Testing request with INVALID API key...")
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{api_url}/",
                headers={"X-API-Key": "invalid-key-12345"},
                json={
                    "jsonrpc": "2.0",
                    "id": "2",
                    "method": "message/send",
                    "params": {
                        "message": {
                            "messageId": "test-2",
                            "role": "user",
                            "parts": [{"text": "list clusters"}],
                            "contextId": "test-session-2"
                        }
                    }
                },
                timeout=10.0
            )
            print(f"  Status: {response.status_code}")
            if response.status_code == 401:
                print("  ✅ Correctly rejected (401 Unauthorized)")
            else:
                print(f"  ❌ Should have been rejected but got: {response.status_code}")
            print(f"  Response: {response.text[:200]}")
        except Exception as e:
            print(f"  ❌ Error: {e}")
    
    # Test 3: Request with valid API key (should succeed)
    print(f"\n3. Testing request with VALID API key...")
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{api_url}/",
                headers={"X-API-Key": valid_api_key},
                json={
                    "jsonrpc": "2.0",
                    "id": "3",
                    "method": "message/send",
                    "params": {
                        "message": {
                            "messageId": "test-3",
                            "role": "user",
                            "parts": [{"text": "list clusters"}],
                            "contextId": "test-session-3"
                        }
                    }
                },
                timeout=30.0  # Longer timeout for actual processing
            )
            print(f"  Status: {response.status_code}")
            if response.status_code == 200:
                print("  ✅ Successfully authenticated and processed")
                result = response.json()
                if "result" in result:
                    print("  Response contains result field ✅")
                elif "error" in result:
                    print(f"  Response contains error: {result['error']}")
            else:
                print(f"  ❌ Unexpected status code: {response.status_code}")
            print(f"  Response preview: {response.text[:300]}")
        except Exception as e:
            print(f"  ❌ Error: {e}")
    
    # Test 4: Check agent card endpoint (should be accessible without auth)
    print("\n4. Testing agent card endpoint (should not require auth)...")
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(f"{api_url}/", timeout=10.0)
            print(f"  Status: {response.status_code}")
            if response.status_code == 200:
                print("  ✅ Agent card accessible without auth")
                data = response.json()
                if "name" in data:
                    print(f"  Agent: {data.get('name')}")
            else:
                print(f"  ⚠️  Unexpected status: {response.status_code}")
        except Exception as e:
            print(f"  ❌ Error: {e}")
    
    print("\n" + "=" * 60)
    print("Authentication testing complete!")


if __name__ == "__main__":
    # Get API URL and key from arguments or environment
    api_url = sys.argv[1] if len(sys.argv) > 1 else os.getenv("KUBENTLY_API_URL", "http://localhost:8080")
    api_key = sys.argv[2] if len(sys.argv) > 2 else os.getenv("KUBENTLY_API_KEY", "test-api-key")
    
    # Run the tests
    asyncio.run(test_a2a_auth(api_url, api_key))