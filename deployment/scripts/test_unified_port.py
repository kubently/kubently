#!/usr/bin/env python3
"""
Test script for unified single-port architecture.

This script verifies that both admin API and A2A functionality
work correctly through the single unified port.
"""

import json
import time
from typing import Any, Dict

import requests


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


def test_a2a_health(base_url: str) -> bool:
    """Test A2A health endpoint."""
    try:
        response = requests.get(f"{base_url}/a2a/health", timeout=5)
        if response.status_code == 200:
            print("✓ A2A health check passed")
            return True
        else:
            print(f"✗ A2A health check failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"✗ A2A health check error: {e}")
        return False


def test_a2a_agent_card(base_url: str) -> bool:
    """Test A2A agent card endpoint."""
    try:
        response = requests.get(f"{base_url}/a2a/", timeout=5)
        if response.status_code == 200:
            agent_card = response.json()
            if "name" in agent_card and "Kubently" in agent_card["name"]:
                print("✓ A2A agent card retrieved successfully")
                return True
            else:
                print(f"✗ A2A agent card invalid format: {agent_card}")
                return False
        else:
            print(f"✗ A2A agent card failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"✗ A2A agent card error: {e}")
        return False


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


def test_a2a_invoke_endpoint(base_url: str, api_key: str) -> bool:
    """Test A2A invoke endpoint."""
    try:
        headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        payload = {
            "messages": [{"role": "user", "content": "List available clusters"}],
            "tools": [],
            "tool_choice": "auto",
        }

        response = requests.post(
            f"{base_url}/a2a/invoke", headers=headers, json=payload, timeout=10
        )

        if response.status_code == 200:
            result = response.json()
            print("✓ A2A invoke endpoint works")
            return True
        else:
            print(f"✗ A2A invoke endpoint failed: {response.status_code}")
            print(f"  Response: {response.text}")
            return False
    except Exception as e:
        print(f"✗ A2A invoke endpoint error: {e}")
        return False


def main():
    """Run all tests."""
    print("Testing Unified Single-Port Architecture")
    print("=" * 50)

    # Configuration
    base_url = "http://localhost:8080"
    api_key = "test-key-1"  # Default test API key from docker-compose

    print(f"Testing against: {base_url}")
    print(f"Using API key: {api_key}")
    print()

    # Run tests
    tests = [
        ("Main API Health", lambda: test_main_api_health(base_url)),
        ("A2A Health", lambda: test_a2a_health(base_url)),
        ("A2A Agent Card", lambda: test_a2a_agent_card(base_url)),
        ("Admin Clusters", lambda: test_admin_clusters_endpoint(base_url, api_key)),
        ("A2A Invoke", lambda: test_a2a_invoke_endpoint(base_url, api_key)),
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
