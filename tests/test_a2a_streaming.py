#!/usr/bin/env python3
"""Regression guard for #65: `message/stream` must never answer 200 with an empty body.

`message/stream` is served as SSE, so the 200 and the `text/event-stream` headers
are flushed *before* the agent executor runs. Any exception raised before the
first `enqueue_event()` therefore reaches the client as a 200 with a zero-length
body — no events, no error, nothing. `message/send` surfaces the same failure as
a JSON-RPC error, which is why only the streaming path looked broken.

These tests drive the real app `A2AModule.get_app()` builds, with the real
`KubentlyAgentExecutor`, mounted the way `kubently/main.py` mounts it, and assert
the stream always carries at least one `data:` frame and a terminal event.

Since #76 the app is assembled here rather than handed over by the SDK:
a2a-sdk 1.x deleted `A2AStarletteApplication`, so `A2AModule.get_app()` composes
`a2a.server.routes` itself. That is exactly why these tests go through
`A2AModule` instead of standing the routes up themselves — a test that
re-assembles the routes could pass while the app the server actually mounts is
broken (drop `enable_v0_3_compat` and every `message/stream` client gets
-32601 Method not found, with a hand-rolled test none the wiser).
"""

import os
import sys

import pytest

pytest.importorskip("a2a")
pytest.importorskip("deepagents")  # agent_executor -> agent -> deepagents

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from kubently.modules.a2a import A2AModule  # noqa: E402
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
    module = A2AModule(host="127.0.0.1", port=8080, external_url="http://localhost:8080/a2a/")
    # get_app() builds its own executor from _lazy_imports(); swap in the one
    # under test while leaving the rest of the real composition alone.
    module._lazy_imports = lambda: (lambda redis_client=None: executor)
    inner = module.get_app()
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


def test_the_0_3_wire_method_is_still_served():
    """`message/stream` must keep working across the 1.x bump (#76).

    a2a-sdk 1.x renamed the JSON-RPC methods (`message/stream` ->
    `SendStreamingMessage`) and only answers the old names when the endpoint is
    built with `enable_v0_3_compat`. The Kubently CLI, `docs/A2A_CONFIGURATION.md`
    and every deployed client send the old names, so an endpoint that answered
    -32601 to them would be exactly as broken as #65 was, one layer down.
    """
    response = _post_stream(_failing_executor(RuntimeError("boom")))
    _assert_usable_stream(response)
    assert "Method not found" not in response.text
    assert "-32601" not in response.text


def test_the_1_0_wire_method_is_served_too():
    """The card advertises protocol 1.0, so 1.0 has to actually answer.

    1.x negotiates on the `A2A-Version` header; with v0.3 compatibility on, a
    request without it is treated as 0.3. Both halves of the advertisement are
    checked so the card cannot claim a version the endpoint rejects.
    """
    with _client(_failing_executor(RuntimeError("boom"))) as client:
        response = client.post(
            "/a2a/",
            headers={
                "Accept": "text/event-stream",
                "Content-Type": "application/json",
                "A2A-Version": "1.0",
            },
            json={
                "jsonrpc": "2.0",
                "id": "n1",
                "method": "SendStreamingMessage",
                "params": {
                    "message": {
                        "messageId": "m1",
                        "role": "ROLE_USER",
                        "parts": [{"text": "say hi"}],
                    }
                },
            },
        )
    _assert_usable_stream(response)
    assert "TASK_STATE_FAILED" in response.text, "1.0 stream never reached a terminal state"


def test_agent_card_is_served_on_both_well_known_paths():
    """Discovery must not break for clients pinned to the 0.2.x path.

    1.x serves `/.well-known/agent-card.json` (the current spec); 0.2.x served
    `/.well-known/agent.json`. The card is a public contract, so both answer.
    """
    with _client(_failing_executor(RuntimeError("unused"))) as client:
        for path in ("/a2a/.well-known/agent-card.json", "/a2a/.well-known/agent.json"):
            response = client.get(path)
            assert response.status_code == 200, path
            card = response.json()
            assert card["name"] == "Kubently Kubernetes Debugger"
            # The pre-1.x top-level fields the CLI reads survive the bump.
            assert card["url"] == "http://localhost:8080/a2a/"
            assert card["protocolVersion"]
