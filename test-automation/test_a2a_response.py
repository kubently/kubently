#!/usr/bin/env python3
"""
Test script to examine A2A response structure
"""

import httpx
import json
import sys

def test_a2a_response():
    """Send a test query and print the full response structure."""
    
    # Create request
    request = {
        "jsonrpc": "2.0",
        "id": "test-123",
        "method": "message/send",
        "params": {
            "message": {
                "messageId": "msg-123",
                "role": "user",
                "parts": [{"text": "What pods are running in cluster kind namespace default?"}]
            }
        }
    }
    
    # Send request
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                "http://localhost:8080/a2a/",
                json=request,
                headers={
                    "X-API-Key": "test-api-key",
                    "Content-Type": "application/json"
                }
            )
            
            print(f"Status Code: {response.status_code}")
            print(f"Response Headers: {dict(response.headers)}")
            print("\n" + "="*60)
            print("RAW RESPONSE:")
            print("="*60)
            print(response.text[:2000])  # First 2000 chars
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    print("\n" + "="*60)
                    print("PARSED JSON STRUCTURE:")
                    print("="*60)
                    print(json.dumps(data, indent=2)[:3000])  # First 3000 chars
                    
                    # Check for common fields
                    print("\n" + "="*60)
                    print("KEY FIELDS ANALYSIS:")
                    print("="*60)
                    
                    if "result" in data:
                        result = data["result"]
                        print(f"Has 'result': Yes")
                        print(f"Result keys: {list(result.keys())}")
                        
                        if "artifacts" in result:
                            print(f"Has 'artifacts': Yes ({len(result['artifacts'])} items)")
                        
                        if "metadata" in result:
                            print(f"Has 'metadata': Yes")
                            print(f"Metadata keys: {list(result['metadata'].keys())}")
                        
                        if "tool_calls" in result:
                            print(f"Has 'tool_calls': Yes ({len(result['tool_calls'])} calls)")
                        
                        # Check nested locations
                        for key in result:
                            if isinstance(result[key], dict):
                                if "tool_calls" in result[key]:
                                    print(f"Found 'tool_calls' in result['{key}']")
                                if "toolCalls" in result[key]:
                                    print(f"Found 'toolCalls' in result['{key}']")
                    
                except json.JSONDecodeError as e:
                    print(f"Failed to parse JSON: {e}")
            
    except httpx.ConnectError:
        print("ERROR: Cannot connect to http://localhost:8080")
        print("Make sure kubently is running and port-forwarded:")
        print("  kubectl port-forward svc/kubently-api 8080:80 -n kubently")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)

if __name__ == "__main__":
    test_a2a_response()