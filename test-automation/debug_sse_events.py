#!/usr/bin/env python3
"""Debug script to see raw SSE events from kubently"""

import asyncio
import json
import httpx
import uuid

async def debug_sse():
    """Send a request and print all SSE events"""
    base_url = "http://localhost:8080/a2a"
    api_key = "test-api-key"
    query = "In cluster kind, there's an issue with a pod in namespace test-debug. Please investigate."
    
    async with httpx.AsyncClient(
        base_url=base_url,
        headers={"X-API-Key": api_key},
        timeout=30
    ) as client:
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "message/stream",
            "params": {
                "message": {
                    "messageId": str(uuid.uuid4()),
                    "role": "user",
                    "parts": [{
                        "partId": "p1",
                        "text": query
                    }]
                }
            }
        }
        
        print("Sending request...")
        async with client.stream("POST", "/", json=request) as response:
            print(f"Response status: {response.status_code}")
            
            event_count = 0
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    event_count += 1
                    try:
                        data = json.loads(line[6:])
                        print(f"\n=== Event {event_count} ===")
                        print(json.dumps(data, indent=2))
                        
                        # Check for tool-related fields
                        if "result" in data:
                            result = data["result"]
                            if "kind" in result:
                                print(f"KIND: {result['kind']}")
                            
                            # Look for tool-related content
                            for key in result:
                                if "tool" in key.lower():
                                    print(f"FOUND TOOL FIELD: {key} = {result[key]}")
                                    
                    except json.JSONDecodeError:
                        print(f"Failed to parse: {line}")

if __name__ == "__main__":
    asyncio.run(debug_sse())