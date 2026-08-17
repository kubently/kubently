#!/usr/bin/env python3
"""
Tests for build_mcp_tools against a real in-process MCP server.

A FastMCP server (streamable-http, the same transport used for Grafana/Datadog
remote MCPs) runs on an ephemeral localhost port inside the test's event loop.
Covers the Track C2a contract: happy path with server-name prefixing and
untrusted framing, credential header pass-through, oversized-result truncation,
and degradation when the server is down (registration) or dies mid-flight
(per-call).
"""

import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kubently.modules.a2a.protocol_bindings.a2a_server.mcp_client import (
    MCPServerSpec,
    build_mcp_tools,
)


class FakeInterceptor:
    """Minimal stand-in for ToolCallInterceptor; records everything passed in."""

    def __init__(self):
        self.calls = []
        self.results = []

    async def record_tool_call(self, tool_name, args, thread_id=None):
        self.calls.append({"tool_name": tool_name, "args": args, "thread_id": thread_id})
        return f"call-{len(self.calls)}"

    async def record_tool_result(self, tool_call_id, result, error=None):
        self.results.append({"id": tool_call_id, "result": result, "error": error})


def _build_mock_app(captured_headers: list):
    """A FastMCP app with echo/big tools, wrapped to capture request headers."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("mock", streamable_http_path="/")

    @mcp.tool()
    def echo(text: str) -> str:
        """Echo the text back."""
        return f"echo:{text}"

    @mcp.tool()
    def big(n: int) -> str:
        """Return n characters."""
        return "x" * n

    inner = mcp.streamable_http_app()

    async def app(scope, receive, send):
        if scope.get("type") == "http":
            captured_headers.append(
                {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
            )
        await inner(scope, receive, send)

    # uvicorn drives the lifespan of the outer app; delegate it to FastMCP's
    # (its session manager must be started for streamable http to serve).
    async def with_lifespan(scope, receive, send):
        if scope.get("type") == "lifespan":
            await inner(scope, receive, send)
            return
        await app(scope, receive, send)

    return with_lifespan


@pytest.fixture
async def mock_server():
    """Start the mock MCP server on an ephemeral port; yields (url, headers_seen)."""
    import uvicorn

    captured: list = []
    config = uvicorn.Config(
        _build_mock_app(captured), host="127.0.0.1", port=0, log_level="error", lifespan="on"
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(0.025)
        assert server.started, "mock MCP server failed to start"
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}/", captured
    finally:
        server.should_exit = True
        await task


async def test_happy_path_prefix_framing_and_tracing(mock_server):
    url, _ = mock_server
    interceptor = FakeInterceptor()
    spec = MCPServerSpec(name="mock", url=url)
    tools = await build_mcp_tools([spec], interceptor, lambda: "thread-1")

    names = sorted(t.name for t in tools)
    assert names == ["mcp_mock_big", "mcp_mock_echo"]

    echo = next(t for t in tools if t.name == "mcp_mock_echo")
    # Descriptions are framed as untrusted third-party input.
    assert echo.description.startswith("[UNTRUSTED third-party tool from MCP server 'mock'")
    assert "Echo the text back." in echo.description

    result = await echo.ainvoke({"text": "hello"})
    assert "BEGIN UNTRUSTED MCP RESULT (server: mock, tool: echo)" in result
    assert "echo:hello" in result
    assert "END UNTRUSTED MCP RESULT" in result

    # Interceptor tracing is mandatory for every A2A tool.
    assert interceptor.calls[0]["tool_name"] == "mcp_mock_echo"
    assert interceptor.calls[0]["thread_id"] == "thread-1"
    assert interceptor.results[0]["error"] is None
    assert "echo:hello" in interceptor.results[0]["result"]


async def test_auth_headers_passed_through_but_never_echoed(mock_server, monkeypatch):
    url, captured = mock_server
    monkeypatch.setenv("MOCK_MCP_TOKEN", "super-secret-tok")
    spec = MCPServerSpec.from_dict(
        {
            "name": "mock",
            "url": url,
            "bearer_token_env": "MOCK_MCP_TOKEN",
            "headers": {"X-Extra": "plain"},
        }
    )
    interceptor = FakeInterceptor()
    tools = await build_mcp_tools([spec], interceptor, lambda: None)
    echo = next(t for t in tools if t.name == "mcp_mock_echo")
    result = await echo.ainvoke({"text": "hi"})

    auth_seen = [h.get("authorization") for h in captured if "authorization" in h]
    assert "Bearer super-secret-tok" in auth_seen
    assert any(h.get("x-extra") == "plain" for h in captured)
    # The credential must never surface in model-visible text or the trace.
    assert "super-secret-tok" not in result
    assert "super-secret-tok" not in json.dumps(interceptor.results)
    assert "super-secret-tok" not in echo.description


async def test_oversized_result_is_truncated_with_note(mock_server, monkeypatch):
    url, _ = mock_server
    monkeypatch.setenv("KUBENTLY_MCP_MAX_OUTPUT_CHARS", "500")
    interceptor = FakeInterceptor()
    tools = await build_mcp_tools([MCPServerSpec(name="mock", url=url)], interceptor, lambda: None)
    big = next(t for t in tools if t.name == "mcp_mock_big")

    result = await big.ainvoke({"n": 5000})
    assert "truncated at 500 chars" in result
    assert result.count("x") <= 520  # capped body, not 5000
    assert "END UNTRUSTED MCP RESULT" in result  # framing survives truncation


async def test_unreachable_server_degrades_to_no_tools(monkeypatch):
    monkeypatch.setenv("KUBENTLY_MCP_CONNECT_TIMEOUT", "3")
    interceptor = FakeInterceptor()
    # Port 9 (discard) on localhost: connection refused immediately.
    spec = MCPServerSpec(name="down", url="http://127.0.0.1:9/mcp")
    tools = await build_mcp_tools([spec], interceptor, lambda: None)
    assert tools == []


async def test_one_bad_server_does_not_sink_the_good_one(mock_server, monkeypatch):
    monkeypatch.setenv("KUBENTLY_MCP_CONNECT_TIMEOUT", "3")
    url, _ = mock_server
    interceptor = FakeInterceptor()
    tools = await build_mcp_tools(
        [
            MCPServerSpec(name="down", url="http://127.0.0.1:9/mcp"),
            MCPServerSpec(name="mock", url=url),
        ],
        interceptor,
        lambda: None,
    )
    assert sorted(t.name for t in tools) == ["mcp_mock_big", "mcp_mock_echo"]


async def test_server_dying_after_registration_returns_error_string(mock_server):
    url, _ = mock_server
    interceptor = FakeInterceptor()
    tools = await build_mcp_tools([MCPServerSpec(name="mock", url=url)], interceptor, lambda: None)
    echo = next(t for t in tools if t.name == "mcp_mock_echo")

    # Point the underlying connection at a dead port to simulate the server
    # going away between registration and the call.
    dead = MCPServerSpec(name="mock", url="http://127.0.0.1:9/mcp")
    result = await asyncio.wait_for(_rebind(tools, dead, interceptor), timeout=30)
    assert result.startswith("Error: MCP tool 'echo' on server 'mock' failed")
    assert "Continue with other tools" in result


async def _rebind(tools, dead_spec, interceptor):
    """Call a registered tool after its server has gone away.

    We can't easily kill the fixture server mid-test without tearing down the
    event loop plumbing, so rebuild the same wrapped tool against a dead URL —
    the wrapper's per-call error path is identical either way.
    """
    from kubently.modules.a2a.protocol_bindings.a2a_server.mcp_client import _wrap_tool

    echo = next(t for t in tools if t.name == "mcp_mock_echo")

    class _Underlying:
        name = "echo"
        description = "Echo the text back."
        args_schema = echo.args_schema

        @staticmethod
        async def coroutine(**kwargs):
            from langchain_mcp_adapters.tools import load_mcp_tools

            # Fresh session against the dead URL: raises a transport error.
            await load_mcp_tools(
                None,
                connection={
                    "transport": "streamable_http",
                    "url": dead_spec.url,
                    "timeout": 3,
                },
            )

    wrapped = _wrap_tool(_Underlying, dead_spec, interceptor, lambda: None)
    return await wrapped.ainvoke({"text": "hi"})
