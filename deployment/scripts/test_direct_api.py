#!/usr/bin/env python3
"""Test direct API with session creation."""

import json
import os
import time
from datetime import datetime

import requests

API_URL = "http://localhost:8080"  # NodePort mapping
API_KEY = os.getenv("KUBENTLY_API_KEY", "test-api-key")


def test_direct_api():
    """Test the API with session creation and kubectl execution."""

    headers = {"X-API-Key": API_KEY, "Content-Type": "application/json"}

    print(f"[{datetime.now()}] Starting direct API test...")
    print(f"API URL: {API_URL}")
    print(f"API Key: {API_KEY[:10]}...")

    # 1. List available clusters
    print("\n1. Listing available clusters...")
    response = requests.get(f"{API_URL}/debug/clusters", headers=headers)
    print(f"Status: {response.status_code}")

    if response.status_code == 200:
        clusters_data = response.json()
        print(f"Clusters response: {json.dumps(clusters_data, indent=2)}")

        if clusters_data.get("clusters"):
            cluster_id = clusters_data["clusters"][0]
            print(f"\nUsing cluster: {cluster_id}")
        else:
            print("No clusters available!")
            return
    else:
        print(f"Error: {response.text}")
        return

    # 2. Create a debugging session
    print(f"\n2. Creating session for cluster {cluster_id}...")
    session_data = {"cluster_id": cluster_id, "correlation_id": f"test-{int(time.time())}"}

    response = requests.post(f"{API_URL}/debug/session", json=session_data, headers=headers)
    print(f"Status: {response.status_code}")

    if response.status_code == 201:
        session = response.json()
        session_id = session["session_id"]
        print(f"Session created: {session_id}")
        print(f"Full response: {json.dumps(session, indent=2)}")
    else:
        print(f"Error: {response.text}")
        return

    # 3. Execute kubectl command
    print(f"\n3. Executing kubectl command in session {session_id}...")
    command_data = {
        "session_id": session_id,
        "cluster_id": cluster_id,
        "command_type": "get",
        "args": ["pods", "-A"],
        "timeout_seconds": 30,
    }

    response = requests.post(f"{API_URL}/debug/execute", json=command_data, headers=headers)
    print(f"Status: {response.status_code}")

    if response.status_code == 200:
        result = response.json()
        print(f"Command executed successfully!")
        print(f"Command ID: {result.get('command_id')}")
        print(f"Status: {result.get('status')}")
        print(f"\nOutput:\n{result.get('output', '')[:500]}...")  # First 500 chars
        if result.get("error"):
            print(f"\nError:\n{result.get('error')}")
    else:
        print(f"Error: {response.text}")
        return

    # 4. Try another command
    print(f"\n4. Executing another kubectl command...")
    command_data = {
        "session_id": session_id,
        "cluster_id": cluster_id,
        "command_type": "get",
        "args": ["nodes"],
        "timeout_seconds": 30,
    }

    response = requests.post(f"{API_URL}/debug/execute", json=command_data, headers=headers)
    print(f"Status: {response.status_code}")

    if response.status_code == 200:
        result = response.json()
        print(f"Command executed successfully!")
        print(f"\nOutput:\n{result.get('output', '')}")
    else:
        print(f"Error: {response.text}")

    # 5. Close the session
    print(f"\n5. Closing session {session_id}...")
    response = requests.delete(f"{API_URL}/debug/session/{session_id}", headers=headers)
    print(f"Status: {response.status_code}")

    if response.status_code == 204:
        print("Session closed successfully!")
    else:
        print(f"Error: {response.text}")

    print(f"\n[{datetime.now()}] Test completed!")


if __name__ == "__main__":
    test_direct_api()
