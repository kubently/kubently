#!/usr/bin/env python3
"""
Test multi-cluster commands.
"""

import json
import time
import urllib.error
import urllib.request


def api_request(endpoint, method="GET", data=None):
    """Make a request to the Kubently API."""
    url = f"http://localhost:8080{endpoint}"

    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode("utf-8") if data else None,
        headers={"Content-Type": "application/json", "X-API-Key": "test-api-key"},
        method=method,
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        print(f"HTTP {e.code}: {error_body}")
        return None
    except Exception as e:
        print(f"Error: {e}")
        return None


def count_pods_in_output(output):
    """Count pods in kubectl output."""
    if not output:
        return 0
    lines = output.strip().split("\n")
    # Skip header line if present
    if lines and "NAMESPACE" in lines[0]:
        lines = lines[1:]
    return len([l for l in lines if l.strip()])


def main():
    print("=" * 80)
    print("Multi-Cluster Pod Comparison Test")
    print("=" * 80)

    # List clusters
    print("\n1. Available clusters:")
    clusters_resp = api_request("/debug/clusters")
    if not clusters_resp:
        print("Failed to get clusters")
        return

    clusters = clusters_resp.get("clusters", [])
    for cluster in sorted(clusters):
        print(f"  - {cluster}")

    # Test clusters we're interested in
    test_clusters = ["kind", "test-cluster"]
    cluster_pods = {}

    for cluster_id in test_clusters:
        print(f"\n2. Testing cluster: {cluster_id}")
        print("-" * 40)

        # Create session
        session_data = api_request("/debug/session", method="POST", data={"cluster_id": cluster_id})

        if not session_data:
            print(f"Failed to create session for {cluster_id}")
            continue

        session_id = session_data.get("session_id")
        print(f"Session created: {session_id}")

        # Execute kubectl get pods -A
        cmd_data = api_request(
            "/debug/execute",
            method="POST",
            data={
                "cluster_id": cluster_id,
                "command_type": "get",
                "args": ["pods", "-A"],
                "session_id": session_id,
            },
        )

        if cmd_data and cmd_data.get("output"):
            output = cmd_data["output"]
            pod_count = count_pods_in_output(output)
            cluster_pods[cluster_id] = pod_count

            print(f"Pods in {cluster_id}: {pod_count}")

            # Show first few lines of output
            lines = output.strip().split("\n")[:5]
            print("\nSample output:")
            for line in lines:
                print(f"  {line}")
            if len(output.strip().split("\n")) > 5:
                print(f"  ... ({pod_count - 4} more pods)")
        else:
            print(f"Failed to get pods for {cluster_id}")
            cluster_pods[cluster_id] = 0

        # Close session
        api_request(f"/debug/session/{session_id}", method="DELETE")

    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY: Pod Count by Cluster")
    print("-" * 40)

    for cluster_id, count in cluster_pods.items():
        print(f"  {cluster_id:15} : {count:3} pods")

    # Verify they're different
    if len(set(cluster_pods.values())) > 1:
        print("\n✅ SUCCESS: Clusters have different pod counts (separate clusters confirmed)")
    else:
        print("\n⚠️  WARNING: Clusters have the same pod count")


if __name__ == "__main__":
    main()
