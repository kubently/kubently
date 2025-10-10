#!/usr/bin/env python3
"""Debug script to capture raw SSE events from Kubently"""
import httpx
import json
import asyncio
import uuid

async def debug_sse():
    client = httpx.AsyncClient(
        base_url="http://localhost:8080/a2a",
        headers={
            "X-API-Key": "test-api-key",
            "Content-Type": "application/json",
        },
        timeout=120.0,
    )
    
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "message/stream",
        "params": {
            "message": {
                "messageId": str(uuid.uuid4()),
                "role": "user",
                "parts": [{"partId": "p1", "text": "In cluster kind, run kubectl get pods -A and show me the output"}]
            }
        }
    }
    
    print("Sending request...")
    print(json.dumps(request, indent=2))
    print("\n" + "="*50 + "\n")
    
    async with client.stream("POST", "/", json=request) as response:
        print(f"Response status: {response.status_code}")
        print("\nRaw SSE events:")
        print("-"*50)
        
        event_count = 0
        async for line in response.aiter_lines():
            if line.strip():
                print(f"Event {event_count}: {line}")
                
                # Parse and pretty print data events
                if line.startswith("data: "):
                    try:
                        data = json.loads(line[6:])
                        print("  Parsed:")
                        print(f"    Kind: {data.get('result', {}).get('kind', 'unknown')}")
                        if 'artifact' in data.get('result', {}):
                            artifact = data['result']['artifact']
                            print(f"    Artifact ID: {artifact.get('artifactId', 'unknown')}")
                            print(f"    Parts: {len(artifact.get('parts', []))}")
                            for i, part in enumerate(artifact.get('parts', [])):
                                print(f"      Part {i}: kind={part.get('kind', 'unknown')}, length={len(str(part.get('text', '')))} chars")
                                if part.get('kind') == 'tool-use':
                                    print(f"        Tool: {part.get('name', 'unknown')}")
                                    print(f"        Input: {json.dumps(part.get('input', {}), indent=10)}")
                    except json.JSONDecodeError:
                        print("  (Failed to parse JSON)")
                
                print()
                event_count += 1
                
                # Stop after 20 events to avoid too much output
                if event_count > 20:
                    print("(Stopping after 20 events...)")
                    break

if __name__ == "__main__":
    asyncio.run(debug_sse())