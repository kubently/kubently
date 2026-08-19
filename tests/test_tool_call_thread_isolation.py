#!/usr/bin/env python3
"""Concurrent requests must not steal each other's tool-call thread id.

KubentlyAgent is ONE shared instance serving every A2A request. The thread id
that tool calls are recorded under used to live on `self`, so with two turns in
flight the second one's assignment clobbered the first's: request A's tool call
was recorded under B's thread id, and therefore streamed into B's SSE as a
"🔧 Tool Call" event carrying A's command args and kubectl output. Issue #63.

The interleaving here is forced with events, not sleeps, so both tests are
deterministic. `test_shared_instance_attribute_races` pins the failure mode
against the old carrier (proving this harness actually detects the bug), and
`test_contextvar_isolates_concurrent_requests` shows the shipped carrier — a
ContextVar, which is per-task — does not have it.
"""

import asyncio
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from kubently.modules.a2a.protocol_bindings.a2a_server.agent import (  # noqa: E402
    _namespaced_thread_id,
    current_thread_id,
)
from kubently.modules.a2a.protocol_bindings.a2a_server.tool_call_interceptor import (  # noqa: E402
    ToolCallInterceptor,
)
from kubently.modules.auth.context import current_api_key  # noqa: E402

AGENT_SRC = (
    Path(__file__).parent.parent
    / "kubently/modules/a2a/protocol_bindings/a2a_server/agent.py"
).read_text()


async def _two_interleaved_turns(store, load):
    """Two A2A turns in flight at once, sharing one agent.

    Both callers pick the same client-supplied contextId (the realistic
    collision) but authenticate as different tenants, so each turn's namespaced
    thread id is distinct. Turn B stores its thread id *between* turn A storing
    its own and turn A recording a tool call — the exact window that races.

    `store`/`load` are the carrier under test: how run() saves the thread id and
    how a tool reads it back.

    Returns (interceptor, (a_own, a_observed), (b_own, b_observed)).
    """
    interceptor = ToolCallInterceptor()
    a_stored = asyncio.Event()
    b_stored = asyncio.Event()

    async def turn_a():
        current_api_key.set("tenant-a-key")  # set by the auth layer per request
        own = _namespaced_thread_id("shared-context")  # what run() derives
        store(own)
        a_stored.set()
        await b_stored.wait()  # the other turn is now also in flight
        observed = load()  # what a tool reads just before recording its call
        await interceptor.record_tool_call(
            "execute_kubectl", {"command": "get secrets"}, observed
        )
        return own, observed

    async def turn_b():
        current_api_key.set("tenant-b-key")
        await a_stored.wait()
        own = _namespaced_thread_id("shared-context")
        store(own)
        b_stored.set()
        observed = load()
        await interceptor.record_tool_call(
            "execute_kubectl", {"command": "get pods"}, observed
        )
        return own, observed

    # gather() wraps each coroutine in its own Task, which is what gives each
    # one an independent copy of the context — same as one asyncio task per
    # inbound A2A request.
    a_result, b_result = await asyncio.gather(turn_a(), turn_b())
    return interceptor, a_result, b_result


@pytest.mark.asyncio
async def test_contextvar_isolates_concurrent_requests():
    """Each in-flight turn's tool call carries its OWN thread id."""
    interceptor, (a_own, a_seen), (b_own, b_seen) = await _two_interleaved_turns(
        current_thread_id.set, current_thread_id.get
    )

    assert a_own != b_own, "the two tenants must namespace the shared contextId apart"
    assert a_seen == a_own, f"turn A recorded under {a_seen}, not its own {a_own}"
    assert b_seen == b_own, f"turn B recorded under {b_seen}, not its own {b_own}"

    # End-to-end through the real interceptor: each thread sees only its own
    # call, so neither caller's SSE stream can carry the other's kubectl output.
    a_calls = await interceptor.get_tool_calls_for_thread(a_own)
    b_calls = await interceptor.get_tool_calls_for_thread(b_own)
    assert [c["args"]["command"] for c in a_calls] == ["get secrets"]
    assert [c["args"]["command"] for c in b_calls] == ["get pods"]


@pytest.mark.asyncio
async def test_shared_instance_attribute_races():
    """The pre-fix carrier, under the identical interleaving, misattributes.

    Not a test of shipped behaviour — it is the control that proves the harness
    above would fail if the thread id ever moved back onto the shared instance.
    """
    shared = types.SimpleNamespace(thread_id=None)
    interceptor, (a_own, a_seen), (b_own, b_seen) = await _two_interleaved_turns(
        lambda tid: setattr(shared, "thread_id", tid), lambda: shared.thread_id
    )

    assert a_seen == b_own != a_own, "expected A's tool call to land on B's thread"
    leaked = await interceptor.get_tool_calls_for_thread(b_own)
    assert {c["args"]["command"] for c in leaked} == {"get secrets", "get pods"}, (
        "B's thread should have picked up both turns' tool calls"
    )
    assert await interceptor.get_tool_calls_for_thread(a_own) == []


def test_thread_id_is_not_instance_state():
    """Regression guard: the id lives in the ContextVar, never back on self."""
    assert "_current_thread_id" not in AGENT_SRC, (
        "the tool-call thread id is back on the shared agent instance — "
        "concurrent requests will cross-attribute tool calls again"
    )
    assert "current_thread_id.set(thread_id)" in AGENT_SRC
