#!/usr/bin/env python3
"""
Light wiring test for the FastMCP server. Skipped when the `mcp` SDK isn't installed
(it lives in the optional `a2a` extra), so the core unit suite stays green everywhere.
"""

import asyncio
import os
import sys

import pytest

pytest.importorskip("mcp")
# build_mcp_server now constructs Kubently's troubleshooting agent, which lives in the
# same optional `a2a` extra as the mcp SDK; skip when that stack isn't installed.
pytest.importorskip("langgraph.checkpoint.redis")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_build_mcp_server_registers_expected_tools(monkeypatch):
    monkeypatch.setenv("API_KEYS", "test:abc123")
    from kubently.modules.mcp.server import build_mcp_server

    server = build_mcp_server()
    server.streamable_http_app()  # session manager / tool manager initialized here

    names = sorted(tool.name for tool in asyncio.run(server.list_tools()))
    assert names == ["ask_kubently"]
