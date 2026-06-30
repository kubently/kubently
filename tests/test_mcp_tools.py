#!/usr/bin/env python3
"""
Tests for the MCP ask tool logic (kubently.modules.mcp.tools.ask_kubently).

The tool routes a natural-language query through the Kubently agent and drains its
stream to the final answer. The agent is faked; the logic under test is the drain
(last message wins, errors surface) and the conversation_id -> thread_id mapping.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kubently.modules.mcp import tools


class FakeAgent:
    """Records run() args and yields a scripted sequence of agent events."""

    def __init__(self, events):
        self._events = events
        self.calls = []

    async def run(self, messages, thread_id=None, cluster_id=None):
        self.calls.append(
            {"messages": messages, "thread_id": thread_id, "cluster_id": cluster_id}
        )
        for event in self._events:
            yield event


@pytest.mark.asyncio
async def test_ask_drains_to_final_message_and_reuses_conversation_id():
    agent = FakeAgent(
        [
            {"type": "message", "content": "investigating...", "metadata": {}},
            {"type": "message", "content": "final answer", "metadata": {}},
        ]
    )

    result = await tools.ask_kubently(
        agent, "why crashlooping?", cluster_id="prod", conversation_id="conv-1"
    )

    assert result == {"answer": "final answer", "thread_id": "conv-1"}
    # conversation_id is passed through as the agent's memory thread_id, with cluster context.
    assert agent.calls[0]["thread_id"] == "conv-1"
    assert agent.calls[0]["cluster_id"] == "prod"


@pytest.mark.asyncio
async def test_ask_generates_thread_id_when_omitted():
    agent = FakeAgent([{"type": "message", "content": "ok", "metadata": {}}])

    result = await tools.ask_kubently(agent, "list clusters")

    assert result["answer"] == "ok"
    assert result["thread_id"]  # generated, non-empty, and handed back to the caller
    assert agent.calls[0]["thread_id"] == result["thread_id"]


@pytest.mark.asyncio
async def test_ask_surfaces_error_event():
    agent = FakeAgent([{"type": "error", "content": "boom", "metadata": {}}])

    result = await tools.ask_kubently(agent, "break", conversation_id="c")

    assert result == {"answer": "boom", "thread_id": "c"}
