#!/usr/bin/env python3
"""`message/stream` must actually stream — and must never return an empty body.

The A2A SDK flushes the SSE response headers (HTTP 200) *before* it pulls the
first event out of the executor, and its streaming path converts only
`ServerError` into a JSON-RPC error frame. So any other exception escaping
`KubentlyAgentExecutor.execute()` closes the connection with zero bytes: the
client sees "HTTP 200, 0 bytes" and is told nothing about what went wrong,
while the very same failure on `message/send` returns a readable JSON-RPC
error. That asymmetry shipped once (issue #65).

These tests pin both halves of the contract:
  * a stream is never empty — success or failure, events come out;
  * a long investigation emits progress *while* it runs, not one artifact at
    the end, so a caller (or a proxy with a response deadline) sees bytes flow.

Requires the optional `a2a` extra; skipped when the SDK isn't installed.
"""

import asyncio
import importlib
import json
import sys
import types
from pathlib import Path
from typing import ClassVar

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

pytest.importorskip("a2a", reason="a2a-sdk not installed (optional 'a2a' extra)")
pytest.importorskip("httpx")

AGENT_MOD = "kubently.modules.a2a.protocol_bindings.a2a_server.agent"


def _ensure_agent_module():
    """Make the executor importable without langchain/deepagents.

    The executor imports the LLM agent module at import time; the unit test env
    installs only the base dependencies, so stand in a stub when the real module
    can't be loaded. The stub is never exercised — every test injects its own
    fake agent instance.
    """
    if AGENT_MOD in sys.modules:
        return
    try:
        importlib.import_module(AGENT_MOD)
    except Exception:
        stub = types.ModuleType(AGENT_MOD)

        class KubentlyAgent:
            SUPPORTED_CONTENT_TYPES: ClassVar[list] = ["text/plain"]

            def __init__(self, redis_client=None):
                pass

        stub.KubentlyAgent = KubentlyAgent
        stub._namespaced_thread_id = lambda thread_id: thread_id
        sys.modules[AGENT_MOD] = stub


_ensure_agent_module()

from kubently.modules.a2a.protocol_bindings.a2a_server import agent_executor as ax  # noqa: E402
from kubently.modules.a2a.protocol_bindings.a2a_server.tool_call_interceptor import (  # noqa: E402
    get_tool_call_interceptor,
)


class FakeAgent:
    """Stands in for KubentlyAgent, mimicking its streaming shape.

    The real agent awaits a single `ainvoke()` and yields exactly one chunk at
    the very end — which is why the executor has to surface progress from the
    tool-call interceptor rather than from this generator.
    """

    def __init__(self, tool_calls=0, step_delay=0.0, init_error=None, run_error=None):
        self.tool_calls = tool_calls
        self.step_delay = step_delay
        self.init_error = init_error
        self.run_error = run_error

    async def initialize(self):
        if self.init_error:
            raise self.init_error

    async def run(self, messages, thread_id=None, cluster_id=None, context_id=None):
        if self.run_error:
            raise self.run_error
        interceptor = get_tool_call_interceptor()
        for i in range(self.tool_calls):
            await asyncio.sleep(self.step_delay)
            call_id = await interceptor.record_tool_call(
                "execute_kubectl", {"command": f"get pods -n ns{i}"}, thread_id
            )
            await asyncio.sleep(self.step_delay)
            await interceptor.record_tool_result(call_id, f"pod-{i} CrashLoopBackOff")
        yield {"type": "message", "content": "Final diagnosis: image pull failure."}


def build_app(agent):
    """Mount the A2A app the way kubently/main.py does (FastAPI mount)."""
    from a2a.server.apps import A2AStarletteApplication
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.server.tasks import InMemoryTaskStore
    from a2a.types import AgentCapabilities, AgentCard, AgentSkill
    from fastapi import FastAPI

    executor = ax.KubentlyAgentExecutor(redis_client=None)
    executor.agent = agent

    card = AgentCard(
        name="Kubently",
        description="test",
        url="http://test/a2a/",
        version="1.0.0",
        defaultInputModes=["text/plain"],
        defaultOutputModes=["text/plain"],
        capabilities=AgentCapabilities(streaming=True, pushNotifications=False),
        skills=[AgentSkill(id="k", name="k", description="k", tags=["k"])],
    )
    a2a_app = A2AStarletteApplication(
        agent_card=card,
        http_handler=DefaultRequestHandler(
            agent_executor=executor, task_store=InMemoryTaskStore()
        ),
    ).build()

    app = FastAPI()
    app.mount("/a2a", a2a_app)
    return app


async def stream(app, text="why are pods failing?"):
    """POST message/stream and return (elapsed_seconds, result) per SSE frame."""
    import httpx

    payload = {
        "jsonrpc": "2.0",
        "id": "s1",
        "method": "message/stream",
        "params": {
            "message": {
                "messageId": "m1",
                "role": "user",
                "parts": [{"partId": "p1", "text": text}],
            }
        },
    }

    frames = []
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        started = asyncio.get_running_loop().time()
        async with client.stream(
            "POST",
            "/a2a/",
            json=payload,
            headers={"Accept": "text/event-stream"},
        ) as response:
            assert response.status_code == 200
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    frames.append(
                        (
                            asyncio.get_running_loop().time() - started,
                            json.loads(line[5:]),
                        )
                    )
    return frames


def texts(frames):
    """Every text part carried by status-update / artifact-update frames."""
    out = []
    for _, frame in frames:
        result = frame.get("result", {})
        if result.get("kind") == "status-update":
            message = result.get("status", {}).get("message") or {}
            out += [p.get("text", "") for p in message.get("parts", [])]
        elif result.get("kind") == "artifact-update":
            out += [p.get("text", "") for p in result["artifact"].get("parts", [])]
    return out


def kinds(frames):
    return [frame.get("result", {}).get("kind") for _, frame in frames]


class RecordingQueue:
    """Stands in for the SDK's EventQueue, capturing what the executor emits."""

    def __init__(self, on_text=None):
        self.events = []
        self.texts = []
        self._on_text = on_text

    async def enqueue_event(self, event):
        self.events.append(event)
        message = getattr(getattr(event, "status", None), "message", None)
        for part in getattr(message, "parts", []) or []:
            text = getattr(part.root, "text", None)
            if text is None:
                continue
            self.texts.append(text)
            if self._on_text:
                self._on_text(text)

    @property
    def final_state(self):
        status = getattr(self.events[-1], "status", None)
        return getattr(getattr(status, "state", None), "value", None)


def make_context(text):
    """A RequestContext as the SDK would build it for a fresh message."""
    from a2a.server.agent_execution import RequestContext
    from a2a.types import Message, MessageSendParams, Part, Role, TextPart

    message = Message(
        messageId="m1",
        role=Role.user,
        parts=[Part(root=TextPart(text=text))],
    )
    return RequestContext(request=MessageSendParams(message=message))


async def test_stream_is_never_empty_on_success():
    frames = await stream(build_app(FakeAgent()))

    assert frames, "message/stream returned no SSE events at all"
    assert kinds(frames)[0] == "task", "the task event must be the client's first byte"
    assert "artifact-update" in kinds(frames), "the final artifact never arrived"
    assert frames[-1][1]["result"]["status"]["state"] == "completed"
    assert any("image pull failure" in t for t in texts(frames))


async def test_stream_reports_failures_instead_of_returning_an_empty_body():
    """A crash inside execute() must become a `failed` task, not zero bytes.

    Pre-fix this returned HTTP 200 with an empty body, because the exception
    escaped execute() after the SSE headers had already been flushed.
    """
    agent = FakeAgent(init_error=ValueError("Unsupported LLM_PROVIDER ''"))
    frames = await stream(build_app(agent))

    assert frames, "a failing agent produced an empty stream (issue #65)"
    assert kinds(frames)[0] == "task"
    assert frames[-1][1]["result"]["status"]["state"] == "failed"
    assert any("Unsupported LLM_PROVIDER" in t for t in texts(frames)), (
        "the failure reason must reach the client, not just the server log"
    )


async def test_stream_survives_an_agent_that_blows_up_mid_run():
    agent = FakeAgent(run_error=RuntimeError("recursion limit reached"))
    frames = await stream(build_app(agent))

    assert frames
    assert frames[-1][1]["result"]["status"]["state"] == "failed"
    assert any("recursion limit reached" in t for t in texts(frames))


async def test_progress_is_emitted_while_the_agent_is_still_working(monkeypatch):
    """Tool activity must reach the client *during* the investigation.

    `agent.run()` yields once, at the end, so without the interceptor sweep the
    stream is silent for the whole diagnosis (measured at 166s in issue #65) and
    a proxy with a response deadline kills it.

    Asserted at the executor rather than over HTTP so it's deterministic: the
    agent parks until the executor has emitted the tool call it just recorded.
    If emission waited for run() to finish, that wait can never be satisfied and
    the agent raises instead of hanging the suite.
    """
    monkeypatch.setattr(ax, "TOOL_POLL_INTERVAL", 0.01)

    emitted_during_run = asyncio.Event()
    queue = RecordingQueue(on_text=lambda t: emitted_during_run.set() if "kubectl" in t else None)

    class ParkingAgent:
        async def initialize(self):
            pass

        async def run(self, messages, thread_id=None, cluster_id=None, context_id=None):
            await get_tool_call_interceptor().record_tool_call(
                "execute_kubectl", {"command": "get pods"}, thread_id
            )
            # Blocks until the executor streams that call out mid-run.
            await asyncio.wait_for(emitted_during_run.wait(), timeout=5)
            yield {"type": "message", "content": "done"}

    executor = ax.KubentlyAgentExecutor(redis_client=None)
    executor.agent = ParkingAgent()
    await executor.execute(make_context("why are pods failing?"), queue)

    assert emitted_during_run.is_set(), (
        "no tool-call event was emitted while the agent was still running — the "
        "stream only produces output after the whole diagnosis finishes"
    )
    assert any("kubectl" in t for t in queue.texts)
    assert queue.final_state == "completed", queue.texts


async def test_completed_tool_calls_are_reported_exactly_once():
    """test-automation counts one '🔧 Tool Call:' per executed tool.

    In-flight calls use a distinct prefix so the running/finished pair doesn't
    double the count.
    """
    frames = await stream(build_app(FakeAgent(tool_calls=2)))

    assert sum(t.count("🔧 Tool Call:") for t in texts(frames)) == 2
    assert any("✅ Result:" in t for t in texts(frames))
