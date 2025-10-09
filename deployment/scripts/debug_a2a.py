#!/usr/bin/env python3
"""Simple A2A debug client using JSON-RPC."""

import json
import uuid

import httpx


def test_a2a():
    try:
        # Test the A2A JSON-RPC endpoint
        with httpx.Client() as client:
            # JSON-RPC 2.0 request for A2A
            rpc_request = {
                "jsonrpc": "2.0",
                "id": str(uuid.uuid4()),
                "method": "message/send",  # Standard A2A method
                "params": {
                    "message": {
                        "messageId": str(uuid.uuid4()),
                        "role": "user",
                        "parts": [{"type": "text", "text": "what clusters do you have access to?"}],
                    }
                },
            }

            print(f"Sending JSON-RPC request to A2A server...")
            print(f"Payload: {json.dumps(rpc_request, indent=2)}")

            response = client.post(
                "http://localhost:8000/",
                headers={"Content-Type": "application/json", "User-Agent": "Debug-Client/1.0.0"},
                json=rpc_request,
                timeout=60.0,
            )

            print(f"Response status: {response.status_code}")
            print(f"Response headers: {dict(response.headers)}")
            print(f"Response body: {response.text}")

            if response.status_code == 200:
                result = response.json()
                print(f"\nParsed response: {json.dumps(result, indent=2)}")
            else:
                print(f"Error: {response.status_code} - {response.text}")

    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    test_a2a()
