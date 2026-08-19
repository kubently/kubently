#!/usr/bin/env python3
"""
Test script for unified single-port architecture.

This script verifies that both admin API and A2A functionality
work correctly through the single unified port.

Every URL below was checked against the routes the app actually registers
(``kubently/main.py`` for the API, ``kubently/modules/a2a/__init__.py`` for the
mounted A2A sub-app). The script used to assert URLs nobody had verified —
``GET /a2a/`` for the agent card (the JSON-RPC endpoint is a POST, so that was
always a 405), plus ``/a2a/health`` and ``/a2a/invoke``, which the A2A app has
never registered at all. Those checks reported failure whether or not A2A was
healthy, which is worse than not testing: a permanently red check is one nobody
reads.
"""

import os

import requests

# The A2A app registers exactly three things: the agent card at the current
# spec path, the same card at the 0.2.x path (kept for clients pinned to it),
# and JSON-RPC as a POST at the mount root.
AGENT_CARD_PATH = "/a2a/.well-known/agent-card.json"
LEGACY_AGENT_CARD_PATH = "/a2a/.well-known/agent.json"
A2A_JSONRPC_PATH = "/a2a/"


def test_main_api_health(base_url: str) -> bool:
    """Test main API health endpoint."""
    try:
        response = requests.get(f"{base_url}/health", timeout=5)
        if response.status_code == 200:
            print("✓ Main API health check passed")
            return True
        else:
            print(f"✗ Main API health check failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"✗ Main API health check error: {e}")
        return False


def test_a2a_agent_card(base_url: str) -> bool:
    """Test that the A2A agent card is served at both well-known paths.

    Unauthenticated on purpose: the auth wrapper in ``main.py`` is mounted with
    ``public_well_known=True`` because the card is a public discovery document.
    A 401 here means that exemption broke.
    """
    ok = True
    for path in (AGENT_CARD_PATH, LEGACY_AGENT_CARD_PATH):
        try:
            response = requests.get(f"{base_url}{path}", timeout=5)
            if response.status_code != 200:
                print(f"✗ A2A agent card failed at {path}: {response.status_code}")
                ok = False
                continue
            agent_card = response.json()
            if "Kubently" in agent_card.get("name", ""):
                print(f"✓ A2A agent card retrieved successfully from {path}")
            else:
                print(f"✗ A2A agent card invalid format at {path}: {agent_card}")
                ok = False
        except Exception as e:
            print(f"✗ A2A agent card error at {path}: {e}")
            ok = False
    return ok


def test_admin_clusters_endpoint(base_url: str, api_key: str) -> bool:
    """Test admin clusters endpoint."""
    try:
        headers = {"X-API-Key": api_key}
        response = requests.get(f"{base_url}/debug/clusters", headers=headers, timeout=5)
        if response.status_code == 200:
            clusters = response.json()
            print(f"✓ Admin clusters endpoint works: {clusters}")
            return True
        else:
            print(f"✗ Admin clusters endpoint failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"✗ Admin clusters endpoint error: {e}")
        return False


def test_a2a_jsonrpc_endpoint(base_url: str, api_key: str) -> bool:
    """Test that the A2A JSON-RPC endpoint is mounted, authenticated and answering.

    Deliberately sends an unknown method rather than a real ``message/send``:
    the point of this script is that the single port routes to A2A at all, and
    an unknown method proves the whole chain (mount -> API-key wrapper ->
    JSON-RPC handler) without spending an LLM round-trip. a2a-sdk answers it
    with HTTP 200 carrying a JSON-RPC error object, code -32601. A 404/405 here
    means the endpoint moved; a 401 means the API key is wrong.
    """
    try:
        headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        payload = {
            "jsonrpc": "2.0",
            "id": "unified-port-probe",
            "method": "kubently/does-not-exist",
            "params": {},
        }

        response = requests.post(
            f"{base_url}{A2A_JSONRPC_PATH}", headers=headers, json=payload, timeout=10
        )

        if response.status_code != 200:
            print(f"✗ A2A JSON-RPC endpoint failed: {response.status_code}")
            print(f"  Response: {response.text}")
            return False

        result = response.json()
        if result.get("jsonrpc") == "2.0" and result.get("error", {}).get("code") == -32601:
            print("✓ A2A JSON-RPC endpoint works")
            return True

        print(f"✗ A2A JSON-RPC endpoint returned an unexpected body: {result}")
        return False
    except Exception as e:
        print(f"✗ A2A JSON-RPC endpoint error: {e}")
        return False


def main():
    """Run all tests."""
    print("Testing Unified Single-Port Architecture")
    print("=" * 50)

    # Configuration. `test-api-key` is what deployment/scripts/kind-e2e.sh puts
    # in the kubently-api-keys secret; override for any other deployment.
    base_url = os.environ.get("KUBENTLY_URL", "http://localhost:8080")
    api_key = os.environ.get("KUBENTLY_API_KEY", "test-api-key")

    print(f"Testing against: {base_url}")
    print(f"Using API key: {api_key}")
    print()

    # Run tests
    tests = [
        ("Main API Health", lambda: test_main_api_health(base_url)),
        ("A2A Agent Card", lambda: test_a2a_agent_card(base_url)),
        ("Admin Clusters", lambda: test_admin_clusters_endpoint(base_url, api_key)),
        ("A2A JSON-RPC", lambda: test_a2a_jsonrpc_endpoint(base_url, api_key)),
    ]

    passed = 0
    total = len(tests)

    for test_name, test_func in tests:
        print(f"Running {test_name}...")
        try:
            if test_func():
                passed += 1
        except Exception as e:
            print(f"✗ {test_name} exception: {e}")
        print()

    print("=" * 50)
    print(f"Results: {passed}/{total} tests passed")

    if passed == total:
        print("🎉 All tests passed! Unified port architecture is working correctly.")
        return 0
    else:
        print("❌ Some tests failed. Check the output above for details.")
        return 1


if __name__ == "__main__":
    exit(main())
