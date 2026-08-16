#!/usr/bin/env python3
"""Tool-call tracing: the recording side and the query side must agree.

The agent records tool calls under the caller-namespaced thread id; the A2A
executor queries the interceptor for them to emit "🔧 Tool Call" stream events,
which test-automation parses. The interceptor matches thread_id by EXACT
equality, so if one side namespaces and the other doesn't, the lookup silently
returns [] — no error, just permanently missing tool visibility.

That regression shipped once (agent namespaced, executor still queried the raw
contextId). These tests pin both halves.
"""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

A2A = Path(__file__).parent.parent / "kubently/modules/a2a/protocol_bindings/a2a_server"
AGENT_SRC = (A2A / "agent.py").read_text()
EXECUTOR_SRC = (A2A / "agent_executor.py").read_text()


def test_executor_queries_with_namespaced_thread_id():
    """Every get_tool_calls_for_thread call must use the namespaced id, never
    the raw client-supplied contextId."""
    calls = re.findall(
        r"get_tool_calls_for_thread\(\s*([A-Za-z_][A-Za-z0-9_]*)", EXECUTOR_SRC
    )
    assert calls, "expected interceptor queries in agent_executor"
    for arg in calls:
        assert arg != "contextId", (
            "agent_executor queries the interceptor with the raw contextId, but the "
            "agent records under the namespaced thread id — tool call events will "
            "silently never be emitted"
        )
        assert arg == "traceThreadId", f"unexpected thread id argument: {arg}"


def test_executor_derives_trace_id_from_the_same_helper():
    """Both sides must derive the namespace from one function, so they can't drift."""
    assert "_namespaced_thread_id" in EXECUTOR_SRC
    assert "traceThreadId = _namespaced_thread_id(contextId)" in EXECUTOR_SRC


def test_agent_records_under_namespaced_id():
    """The recording side still namespaces (the other half of the contract)."""
    assert "thread_id = _namespaced_thread_id(thread_id)" in AGENT_SRC
    idx_ns = AGENT_SRC.index("thread_id = _namespaced_thread_id(thread_id)")
    idx_store = AGENT_SRC.index("self._current_thread_id = thread_id", idx_ns)
    assert idx_store > idx_ns, "namespacing must happen before the id is stored"


@pytest.mark.asyncio
async def test_recorded_call_is_retrievable_by_the_queried_id():
    """End-to-end on the interceptor itself: a call recorded under a namespaced
    id is found when queried with that same id, and NOT with the raw one."""
    from kubently.modules.a2a.protocol_bindings.a2a_server.tool_call_interceptor import (
        get_tool_call_interceptor,
    )

    interceptor = get_tool_call_interceptor()
    raw = "ctx-abc"
    namespaced = f"deadbeefdeadbeef:{raw}"

    await interceptor.record_tool_call(
        tool_name="execute_kubectl", args={"command": "get pods"}, thread_id=namespaced
    )

    found = await interceptor.get_tool_calls_for_thread(namespaced)
    assert any(c["tool_name"] == "execute_kubectl" for c in found)

    missed = await interceptor.get_tool_calls_for_thread(raw)
    assert not any(c["tool_name"] == "execute_kubectl" for c in missed), (
        "querying the raw contextId must not find namespaced records — this is "
        "exactly the silent-empty-result failure mode"
    )
