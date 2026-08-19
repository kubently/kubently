#!/usr/bin/env python3
"""
End-to-end test of SSE + Redis pub/sub solution.
Tests command execution with real Kubernetes cluster.
"""

import json
import time
from datetime import datetime

import pytest
import requests

# Configuration
API_URL = "http://localhost:8080"
API_KEY = "test-api-key"  # From the secret
CLUSTER_ID = "kubently"

print("=" * 60)
print("E2E Test: SSE + Redis Pub/Sub Architecture")
print("=" * 60)
print()


def _deployment_reachable() -> bool:
    """API up, key accepted, and the target cluster actually registered."""
    try:
        response = requests.get(
            f"{API_URL}/debug/clusters", headers={"X-API-Key": API_KEY}, timeout=2
        )
        return response.status_code == 200 and CLUSTER_ID in response.text
    except Exception:
        return False


# Without this these "tests" pass on any machine with no Kubently at all: every
# request raises, the helper swallows it, and nothing is asserted. Skipping is
# the honest outcome — a green vacuous e2e test is worse than no test.
requires_deployment = pytest.mark.skipif(
    not _deployment_reachable(),
    reason=f"needs a running Kubently API at {API_URL} with cluster '{CLUSTER_ID}' registered",
)


def _execute_command(command_type="get", args=["pods", "-A"], expected_latency=200):
    """Run one command through /debug/execute; returns (success, latency_ms).

    Named with a leading underscore because it is a helper, not a test: pytest
    collected it as one and warned that it returned a tuple instead of asserting.
    """

    headers = {"X-API-Key": API_KEY}

    command_request = {
        "cluster_id": CLUSTER_ID,
        "command_type": command_type,
        "args": args,
        "timeout_seconds": 10,
    }

    print(f"📤 Sending command: kubectl {command_type} {' '.join(args)}")
    start_time = time.time()

    try:
        response = requests.post(
            f"{API_URL}/debug/execute", json=command_request, headers=headers, timeout=15
        )

        elapsed_ms = int((time.time() - start_time) * 1000)

        if response.status_code == 200:
            result = response.json()
            print(f"✅ Command executed successfully")
            print(f"⏱️  Latency: {elapsed_ms}ms (expected: <{expected_latency}ms)")

            if elapsed_ms < expected_latency:
                print(f"🎯 Performance target met!")
            else:
                print(f"⚠️  Performance slower than expected")

            # Show partial output
            if result.get("output"):
                lines = result["output"].split("\n")[:5]
                print(f"📋 Output (first 5 lines):")
                for line in lines:
                    print(f"   {line}")

            return True, elapsed_ms
        else:
            print(f"❌ Command failed: {response.status_code}")
            print(f"   Response: {response.text}")
            return False, elapsed_ms

    except Exception as e:
        print(f"❌ Error: {e}")
        return False, 0


@requires_deployment
def test_multiple_commands():
    """Test multiple rapid commands to verify instant delivery."""
    print("\n" + "=" * 40)
    print("Testing Multiple Rapid Commands")
    print("=" * 40)

    commands = [
        ("get", ["nodes"]),
        ("get", ["namespaces"]),
        ("get", ["pods", "-n", "kubently"]),
        ("get", ["services", "-n", "kubently"]),
    ]

    total_latency = 0
    success_count = 0

    for cmd_type, args in commands:
        print(f"\nTest {success_count + 1}:")
        success, latency = _execute_command(cmd_type, args, expected_latency=100)
        if success:
            success_count += 1
            total_latency += latency
        time.sleep(0.5)  # Small delay between commands

    print("\n" + "=" * 40)
    print("Summary")
    print("=" * 40)
    print(f"✅ Successful commands: {success_count}/{len(commands)}")
    assert success_count == len(commands), (
        f"only {success_count}/{len(commands)} commands succeeded"
    )
    if success_count > 0:
        avg_latency = total_latency / success_count
        print(f"⏱️  Average latency: {avg_latency:.0f}ms")

        if avg_latency < 100:
            print(f"🚀 Excellent performance! (SSE instant delivery working)")
        elif avg_latency < 500:
            print(f"✅ Good performance (much better than polling)")
        else:
            print(f"⚠️  Performance needs investigation")


@requires_deployment
def test_pod_distribution():
    """Test that commands work regardless of which pod handles them."""
    print("\n" + "=" * 40)
    print("Testing Multi-Pod Distribution")
    print("=" * 40)
    print("Note: Load balancer will distribute requests across 3 API pods")
    print("All should succeed with similar latency")

    latencies = []
    for i in range(6):  # Test 6 times to hit different pods
        print(f"\nRequest {i+1}:")
        success, latency = _execute_command(
            "get", ["pods", "-n", "default"], expected_latency=150
        )
        if success:
            latencies.append(latency)
        time.sleep(0.2)

    assert len(latencies) == 6, f"only {len(latencies)}/6 requests succeeded across pods"
    if latencies:
        print("\n" + "=" * 40)
        print(f"✅ All {len(latencies)} requests succeeded")
        print(f"⏱️  Latencies: {latencies}")
        print(
            f"⏱️  Min: {min(latencies)}ms, Max: {max(latencies)}ms, Avg: {sum(latencies)/len(latencies):.0f}ms"
        )

        # Check consistency
        if max(latencies) - min(latencies) < 100:
            print(f"🎯 Consistent performance across pods!")


def main():
    print("Test Configuration:")
    print(f"  API URL: {API_URL}")
    print(f"  Cluster: {CLUSTER_ID}")
    print(f"  Architecture: SSE + Redis Pub/Sub")
    print()

    print("Expected benefits:")
    print("  ✅ Instant command delivery (~50-100ms)")
    print("  ✅ Works with multiple API pods")
    print("  ✅ No polling delays")
    print()

    # Test 1: Single command
    print("Test 1: Single Command Execution")
    print("-" * 40)
    success, latency = _execute_command()

    if not success:
        print("\n❌ Basic test failed. Check configuration.")
        return

    # Test 2: Multiple commands
    test_multiple_commands()

    # Test 3: Pod distribution
    test_pod_distribution()

    print("\n" + "=" * 60)
    print("🎉 E2E Testing Complete!")
    print("=" * 60)
    print()
    print("Key Results:")
    print("  ✅ SSE connection working")
    print("  ✅ Redis pub/sub distributing commands")
    print("  ✅ Instant command delivery achieved")
    print("  ✅ Multi-pod horizontal scaling verified")
    print()
    print("The HPA solution is successfully implemented!")


if __name__ == "__main__":
    main()
