#!/usr/bin/env python3
"""Regression guard for #65: `message/stream` must never answer 200 with an empty body.

`message/stream` is served as SSE, so the 200 and the `text/event-stream` headers
are flushed *before* the agent executor runs. Any exception raised before the
first `enqueue_event()` therefore reaches the client as a 200 with a zero-length
body — no events, no error, nothing. `message/send` surfaces the same failure as
a JSON-RPC error, which is why only the streaming path looked broken.

These tests drive the real `A2AStarletteApplication` + `KubentlyAgentExecutor`
mounted the way `kubently/main.py` mounts them, and assert the stream always
carries at least one `data:` frame and a terminal event.
"""

import os
import sys

import pytest

pytest.importorskip("a2a")
pytest.importorskip("deepagents")  # agent_executor -> agent -> deepagents

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from a2a.server.apps import A2AStarletteApplication  # noqa: E402
from a2a.server.request_handlers import DefaultRequestHandler  # noqa: E402
from a2a.server.tasks import InMemoryTaskStore  # noqa: E402
from a2a.types import AgentCapabilities, AgentCard, AgentSkill  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from kubently.modules.a2a.protocol_bindings.a2a_server.agent_executor import (  # noqa: E402
    KubentlyAgentExecutor,
)

STREAM_REQUEST = {
    "jsonrpc": "2.0",
    "id": "s1",
    "method": "message/stream",
    "params": {
        "message": {
            "messageId": "m1",
            "role": "user",
            "parts": [{"partId": "p1", "text": "say hi"}],
        }
    },
}


def _client(executor):
    card = AgentCard(
        name="Kubently Kubernetes Debugger",
        description="test card",
        url="http://localhost:8080/a2a/",
        version="1.0.0",
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
        capabilities=AgentCapabilities(streaming=True, pushNotifications=False),
        skills=[AgentSkill(id="k", name="k", description="d", tags=["k8s"])],
    )
    handler = DefaultRequestHandler(
        agent_executor=executor, task_store=InMemoryTaskStore()
    )
    inner = A2AStarletteApplication(agent_card=card, http_handler=handler).build()
    # Mounted under /a2a exactly like kubently/main.py does.
    outer = FastAPI()
    outer.mount("/a2a", inner)
    # raise_server_exceptions=False so we see what the wire sees, not the traceback.
    return TestClient(outer, raise_server_exceptions=False)


def _post_stream(executor):
    with _client(executor) as client:
        return client.post(
            "/a2a/",
            headers={"Accept": "text/event-stream", "Content-Type": "application/json"},
            json=STREAM_REQUEST,
        )


def _assert_usable_stream(response):
    """The invariant #65 violated: a 200 must carry actual SSE events."""
    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")
    assert len(response.content) > 0, "message/stream returned 200 with an EMPTY body (#65)"
    assert "data:" in response.text, "no SSE data frames in the stream"


def _failing_executor(exc: Exception) -> KubentlyAgentExecutor:
    executor = KubentlyAgentExecutor(redis_client=None)

    async def boom():
        raise exc

    executor.initialize = boom  # simulate e.g. an unconfigured/unreachable LLM
    return executor


def test_stream_is_not_empty_when_agent_startup_fails():
    """The exact #65 signature: the executor blows up before enqueuing anything."""
    response = _post_stream(
        _failing_executor(ValueError("Unsupported LLM_PROVIDER ''"))
    )
    _assert_usable_stream(response)
    assert "Unsupported LLM_PROVIDER" in response.text
    assert '"final":true' in response.text, "stream never reached a terminal event"


def test_stream_reports_the_failure_as_a_task_artifact():
    """A failed run still produces an artifact, so CLI/dashboard clients show something."""
    response = _post_stream(_failing_executor(RuntimeError("redis is down")))
    _assert_usable_stream(response)
    assert "redis is down" in response.text
    assert "debug_result" in response.text
