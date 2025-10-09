#!/usr/bin/env python3
"""
Direct API test for cluster-2.
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


def main():
    print("Testing cluster-2 directly via API")
    print("=" * 80)

    # List clusters
    print("\n1. Available clusters:")
    clusters = api_request("/debug/clusters")
    if clusters:
        for cluster in clusters.get("clusters", []):
            print(f"  - {cluster}")

    # Create session for cluster-2
    print("\n2. Creating session for cluster-2...")
    session_data = api_request("/debug/session", method="POST", data={"cluster_id": "cluster-2"})

    if not session_data:
        print("Failed to create session")
        return

    session_id = session_data.get("session_id")
    print(f"Session created: {session_id}")

    # Execute command in cluster-2
    print("\n3. Executing: kubectl get pods -A")
    cmd_data = api_request(
        "/debug/execute",
        method="POST",
        data={
            "cluster_id": "cluster-2",
            "command_type": "get",
            "args": ["pods", "-A"],
            "session_id": session_id,
        },
    )

    if cmd_data:
        print("Command result:")
        print("-" * 40)
        output = cmd_data.get("output", "")
        if output:
            print(output)
        else:
            print("No output received")
            print(f"Full response: {json.dumps(cmd_data, indent=2)}")

    # Try a simpler command
    print("\n4. Executing: kubectl get namespaces")
    cmd_data = api_request(
        "/debug/execute",
        method="POST",
        data={
            "cluster_id": "cluster-2",
            "command_type": "get",
            "args": ["namespaces"],
            "session_id": session_id,
        },
    )

    if cmd_data:
        print("Namespaces:")
        print("-" * 40)
        output = cmd_data.get("output", "")
        if output:
            print(output)

    # Close session
    print("\n5. Closing session...")
    api_request(f"/debug/session/{session_id}", method="DELETE")
    print("Session closed")


if __name__ == "__main__":
    main()
