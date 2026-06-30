#!/usr/bin/env python3
"""
The MCP endpoint must require the same API-key auth as the A2A endpoint the CLI uses.
Tests the ASGI auth wrapper directly: no key -> 401 (downstream never reached);
valid key -> downstream reached. Skipped when the `mcp` SDK isn't installed.
"""

import os
import sys

import pytest

pytest.importorskip("mcp")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from kubently.modules.mcp.server import add_api_key_auth


class FakeAuthModule:
    async def verify_api_key(self, api_key):
        if api_key == "good-key":
            return True, "tester"
        return False, None


def _wrapped_client():
    async def downstream(scope, receive, send):
        await PlainTextResponse("REACHED")(scope, receive, send)

    return TestClient(add_api_key_auth(downstream, FakeAuthModule()))


def test_mcp_rejects_missing_api_key():
    r = _wrapped_client().post("/", json={"jsonrpc": "2.0", "method": "ping", "id": 1})
    assert r.status_code == 401
    assert "REACHED" not in r.text


def test_mcp_rejects_invalid_api_key():
    r = _wrapped_client().post("/", headers={"X-API-Key": "wrong"}, json={})
    assert r.status_code == 401


def test_mcp_accepts_valid_api_key():
    r = _wrapped_client().post("/", headers={"X-API-Key": "good-key"}, json={})
    assert r.status_code == 200
    assert r.text == "REACHED"


def _mounted_card_client():
    """Mount the wrapper UNDER a prefix (like /a2a) so the test exercises the real
    root_path behavior — mounted sub-apps see the full path, not the stripped one."""
    from fastapi import FastAPI

    async def downstream(scope, receive, send):
        await PlainTextResponse("REACHED")(scope, receive, send)

    parent = FastAPI()
    parent.mount("/a2a", add_api_key_auth(downstream, FakeAuthModule(), public_well_known=True))
    return TestClient(parent)


def test_well_known_card_is_public_even_when_mounted_under_prefix():
    r = _mounted_card_client().get("/a2a/.well-known/agent.json")
    assert r.status_code == 200
    assert r.text == "REACHED"


def test_protected_path_still_requires_key_when_well_known_public():
    # a POST to the RPC root is not public even with public_well_known enabled
    r = _mounted_card_client().post("/a2a/", json={})
    assert r.status_code == 401
