#!/usr/bin/env python3
"""
Simple A2A test client for protocol-compliant testing.
Uses httpx directly to implement the A2A protocol.
"""

import asyncio
import json
import sys
import uuid
from typing import Optional

try:
    import httpx
except ImportError:
    print("Error: httpx not installed. Install with: pip install httpx")
    sys.exit(1)


async def send_a2a_message(
    base_url: str,
    api_key: str,
    query: str,
    timeout: int = 30
) -> dict:
    """Send a message via A2A protocol and collect the response."""
    
    # Ensure URL ends with /a2a
    if not base_url.endswith('/a2a'):
        base_url = base_url.rstrip('/') + '/a2a'
    
    # Create HTTP client
    async with httpx.AsyncClient(
        base_url=base_url,
        headers={"X-API-Key": api_key},
        timeout=timeout
    ) as client:
        # Create A2A message
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
        
        # Collect response
        result = {
            "status": "success",
            "context_id": None,
            "final_text": "",
            "all_responses": [],
            "artifacts": [],
            "tool_calls": [],
            "thinking_steps": []
        }
        
        try:
            # Send message and process streaming response
            async with client.stream("POST", "/", json=request) as response:
                if response.status_code != 200:
                    result["status"] = "error"
                    result["error"] = f"HTTP {response.status_code}"
                    return result
                
                # Process SSE stream
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                            if "result" in data:
                                event_result = data["result"]
                                
                                # Get context ID from first response
                                if not result["context_id"] and "contextId" in event_result:
                                    result["context_id"] = event_result["contextId"]
                                
                                # Check for text in status updates
                                if event_result.get("kind") == "status-update":
                                    message = event_result.get("status", {}).get("message", {})
                                    if message and "parts" in message:
                                        for part in message["parts"]:
                                            if part.get("kind") == "text":
                                                text = part.get("text", "")
                                                if text:
                                                    # Check if this is a tool call message
                                                    if "🔧 Tool Call:" in text:
                                                        result["tool_calls"].append({
                                                            "type": "embedded",
                                                            "content": text,
                                                            "timestamp": event_result.get("timestamp", "")
                                                        })
                                                    else:
                                                        result["final_text"] = text
                                                        result["all_responses"].append({
                                                            "type": "message",
                                                            "text": text
                                                        })
                                
                                # Check for artifact updates
                                elif event_result.get("kind") == "artifact-update":
                                    artifact = event_result.get("artifact", {})
                                    if "parts" in artifact:
                                        for part in artifact["parts"]:
                                            if part.get("kind") == "text":
                                                text = part.get("text", "")
                                                if text:
                                                    result["final_text"] = text
                                                    result["artifacts"].append({
                                                        "type": "artifact",
                                                        "text": text
                                                    })
                                
                                # Check for tool calls
                                elif event_result.get("kind") == "tool-call":
                                    tool_call = event_result.get("toolCall", {})
                                    result["tool_calls"].append({
                                        "timestamp": event_result.get("timestamp", ""),
                                        "tool": tool_call.get("tool", "unknown"),
                                        "args": tool_call.get("parameters", {}),
                                        "result": None  # Will be updated when tool-response is received
                                    })
                                
                                # Check for tool responses
                                elif event_result.get("kind") == "tool-response":
                                    tool_response = event_result.get("toolResponse", {})
                                    # Update the last tool call with the response
                                    if result["tool_calls"]:
                                        result["tool_calls"][-1]["result"] = tool_response.get("content", "")
                                
                                # Check for thinking steps
                                elif event_result.get("kind") == "thinking":
                                    thinking = event_result.get("thinking", {})
                                    if "content" in thinking:
                                        result["thinking_steps"].append(thinking["content"])
                                
                                # Check if completed
                                if event_result.get("kind") == "status-update" and event_result.get("final"):
                                    if event_result.get("status", {}).get("state") == "completed":
                                        break
                        except json.JSONDecodeError:
                            pass
                            
        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)
    
    return result


async def main():
    """Main entry point for command-line usage."""
    if len(sys.argv) != 4:
        print("Usage: python a2a_test_client.py <base_url> <api_key> <query>")
        sys.exit(1)
    
    base_url = sys.argv[1]
    api_key = sys.argv[2]
    query = sys.argv[3]
    
    result = await send_a2a_message(base_url, api_key, query)
    
    # Output JSON result
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())