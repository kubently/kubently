"""
MCP tool logic for Kubently.

The MCP surface is a single natural-language tool: the caller delegates a question and
Kubently's own troubleshooting agent answers — the same agent reached over A2A. This
mirrors A2A rather than exposing raw kubectl, so Kubently's reasoning loop stays in the
loop instead of being bypassed by the caller's LLM driving kubectl directly.

Kept free of the `mcp` SDK import so it stays unit-testable and the API boots even when
the SDK isn't installed.
"""

import uuid


async def ask_kubently(
    agent,
    query: str,
    cluster_id: str | None = None,
    conversation_id: str | None = None,
) -> dict:
    """Route a natural-language question through the Kubently agent and return its answer.

    `conversation_id` doubles as the agent's memory thread_id; omit it to start a fresh
    thread (a new id is generated and returned so the caller can continue the conversation).

    MCP tool calls are request/response, so the agent's stream is drained to its final
    message. `agent` is a `KubentlyAgent` (injected by the server so this stays SDK-free).
    """
    thread_id = conversation_id or str(uuid.uuid4())
    messages = [{"role": "user", "content": query}]

    answer = ""
    async for event in agent.run(messages, thread_id=thread_id, cluster_id=cluster_id):
        if event.get("type") in ("message", "error"):
            answer = event.get("content", answer)

    return {"answer": answer, "thread_id": thread_id}
