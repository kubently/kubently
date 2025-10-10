#!/usr/bin/env python3
"""
Simple A2A test using only standard library.
"""

import json
import urllib.error
import urllib.request
import uuid


def send_a2a_message(message_text, context_id=None):
    """Send a message to A2A server using JSON-RPC."""

    url = "http://localhost:8080/a2a/"

    # Create JSON-RPC request
    request_data = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "message/send",
        "params": {
            "message": {
                "messageId": str(uuid.uuid4()),
                "role": "user",
                "parts": [{"text": message_text}],
            },
            "contextId": context_id or str(uuid.uuid4()),
        },
    }

    # Prepare request
    req = urllib.request.Request(
        url,
        data=json.dumps(request_data).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-API-Key": "test-api-key"},
    )

    try:
        # Send request
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
            return result
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        return {"error": f"HTTP {e.code}: {error_body}"}
    except Exception as e:
        return {"error": str(e)}


def extract_text_from_response(response):
    """Extract text from A2A response."""
    if "error" in response:
        return f"Error: {response['error']}"

    if "result" in response:
        result = response["result"]

        # Try to extract from artifacts
        if isinstance(result, dict) and "artifacts" in result:
            artifacts = result["artifacts"]
            if isinstance(artifacts, list) and artifacts:
                artifact = artifacts[0]
                if "parts" in artifact and artifact["parts"]:
                    part = artifact["parts"][0]
                    if "text" in part:
                        return part["text"]
                    elif "root" in part and "text" in part["root"]:
                        return part["root"]["text"]

        # Try to extract from status message
        if isinstance(result, dict) and "status" in result:
            status = result["status"]
            if "message" in status and status["message"]:
                msg = status["message"]
                if "parts" in msg and msg["parts"]:
                    part = msg["parts"][0]
                    if "text" in part:
                        return part["text"]
                    elif "root" in part and "text" in part["root"]:
                        return part["root"]["text"]

        # Return raw result if can't extract
        return json.dumps(result, indent=2)

    return json.dumps(response, indent=2)


def main():
    """Run A2A tests."""
    print("=" * 80)
    print("Testing Kubently A2A Natural Language Queries")
    print("=" * 80)

    # Keep same context for conversation
    context_id = str(uuid.uuid4())

    # Test 1: Ask about pods in cluster "kind"
    print("\n1. Query: Tell me about the pods in cluster kind")
    print("-" * 40)

    response = send_a2a_message(
        "Tell me about the pods running in cluster kind (all namespaces)", context_id
    )
    print("Response:")
    print(extract_text_from_response(response))

    # Test 2: Ask about pods in cluster "cluster-2"
    print("\n" + "=" * 80)
    print("2. Query: Tell me about the pods in cluster-2")
    print("-" * 40)

    response = send_a2a_message(
        "Now tell me about the pods running in cluster cluster-2", context_id
    )
    print("Response:")
    print(extract_text_from_response(response))

    # Test 3: Ask about all clusters
    print("\n" + "=" * 80)
    print("3. Query: Tell me about pods in all clusters")
    print("-" * 40)

    response = send_a2a_message(
        "What clusters are available and how many pods are in each?", context_id
    )
    print("Response:")
    print(extract_text_from_response(response))

    # Test 4: Ask about failing pods
    print("\n" + "=" * 80)
    print("4. Query: Are there any failing pods?")
    print("-" * 40)

    response = send_a2a_message(
        "Are there any failing or crashlooping pods in cluster kind?", context_id
    )
    print("Response:")
    print(extract_text_from_response(response))


if __name__ == "__main__":
    main()
