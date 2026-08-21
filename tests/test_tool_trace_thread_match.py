#!/usr/bin/env python3
"""Tool-call tracing: the recording side and the query side must agree.

Tool calls are recorded under the CALLER-NAMESPACED thread id. The interceptor
matches thread_id by EXACT equality, so if the side that records and the side
that queries disagree, the lookup silently returns [] — no error, just
permanently missing tool visibility. That regression shipped once (the agent
namespaced, the A2A executor still queried the raw contextId).

Since #115 both halves live in `agent.run()`: it namespaces the id, stores it
on `current_thread_id` for the tools to record under, and drains the
interceptor with that same variable as the graph streams. So the guard is now
that the query keeps using that variable — an executor that starts polling the
interceptor again on its own is how the two sides drift apart a second time.
"""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

A2A = Path(__file__).parent.parent / "kubently/modules/a2a/protocol_bindings/a2a_server"
AGENT_SRC = (A2A / "agent.py").read_text()
EXECUTOR_SRC = (A2A / "agent_executor.py").read_text()


def test_the_query_uses_the_same_variable_the_recording_side_was_given():
    """Every get_tool_calls_for_thread call must use the namespaced id, never
    the raw client-supplied contextId."""
    calls = re.findall(
        r"get_tool_calls_for_thread\(\s*([A-Za-z_][A-Za-z0-9_]*)", AGENT_SRC
    )
    assert calls, "expected interceptor queries in agent.run()"
    for arg in calls:
        assert arg == "thread_id", (
            f"the interceptor is queried with {arg!r}, but tool calls are recorded "
            "under `thread_id` (the namespaced one) — the lookup will silently "
            "return [] and tool visibility disappears"
        )


def test_the_executor_does_not_poll_the_interceptor_behind_the_agents_back():
    """A second query site is how the two halves drifted apart the first time.

    The executor renders what `agent.run()` yields; it must not go looking for
    tool calls itself, which would need its own copy of the namespacing rule
    (and would reintroduce tool calls arriving after the answer, #115)."""
    assert "get_tool_calls_for_thread" not in EXECUTOR_SRC
    assert "🔧 Tool Call:" not in EXECUTOR_SRC, (
        "the executor is formatting tool calls again; the text lives in agent.py "
        "next to the structured event so the two cannot disagree"
    )


def test_agent_records_under_namespaced_id():
    """The recording side still namespaces (the other half of the contract)."""
    assert "thread_id = _namespaced_thread_id(thread_id)" in AGENT_SRC
    idx_ns = AGENT_SRC.index("thread_id = _namespaced_thread_id(thread_id)")
    idx_store = AGENT_SRC.index("current_thread_id.set(thread_id)", idx_ns)
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
