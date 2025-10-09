#!/usr/bin/env python3
"""
Test A2A natural language queries against Kubently.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "kubently-cli"))

from kubently_pkg.a2a_client_simple import KubentlyA2AClient


def test_a2a_queries():
    """Test A2A with natural language queries about pods."""

    # Configuration
    api_url = "http://localhost:8080"
    api_key = "test-api-key"

    print("=" * 80)
    print("Testing Kubently A2A Natural Language Queries")
    print("=" * 80)

    # Initialize client
    with KubentlyA2AClient(api_url, api_key) as client:

        # Test 1: Ask about pods in cluster "kind"
        print("\n1. Query: Tell me about the pods in cluster kind")
        print("-" * 40)

        result = client.execute_command("kind", "Tell me about the pods running in cluster kind")

        if result["success"]:
            print("Response:")
            print(result["output"])
        else:
            print(f"Error: {result['error']}")

        # Test 2: Ask about pods in cluster "cluster-2"
        print("\n" + "=" * 80)
        print("2. Query: Tell me about the pods in cluster-2")
        print("-" * 40)

        result = client.execute_command(
            "cluster-2", "Tell me about the pods running in this cluster"
        )

        if result["success"]:
            print("Response:")
            print(result["output"])
        else:
            print(f"Error: {result['error']}")

        # Test 3: Ask about pods across all clusters
        print("\n" + "=" * 80)
        print("3. Query: Tell me about pods in all clusters")
        print("-" * 40)

        # Since A2A doesn't have built-in multi-cluster aggregation, we'll ask a general question
        result = client.execute_command(
            "kind", "List all available clusters and tell me about the pods in each"
        )

        if result["success"]:
            print("Response:")
            print(result["output"])
        else:
            print(f"Error: {result['error']}")

        # Test 4: More specific query
        print("\n" + "=" * 80)
        print("4. Query: Are there any failing pods in cluster kind?")
        print("-" * 40)

        result = client.execute_command("kind", "Are there any failing or crashlooping pods?")

        if result["success"]:
            print("Response:")
            print(result["output"])
        else:
            print(f"Error: {result['error']}")


if __name__ == "__main__":
    test_a2a_queries()
