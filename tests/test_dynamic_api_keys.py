#!/usr/bin/env python3
"""Dynamic (Redis-backed) API keys + caller-key forwarding.

A control plane can issue keys at runtime by writing api:key:{sha256(key)} =
identity to Redis; AuthModule.verify_api_key must accept them alongside the
static API_KEYS env keys. The auth wrapper must expose the caller's validated
key via the current_api_key contextvar so agent tools act as the caller.
"""

import hashlib
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kubently.modules.auth.auth import AuthModule  # noqa: E402
from kubently.modules.auth.context import current_api_key  # noqa: E402
from kubently.modules.mcp.server import add_api_key_auth  # noqa: E402


class FakeRedis:
    """Just enough async Redis for AuthModule: get + audit-log no-ops."""

    def __init__(self, data=None):
        self.data = data or {}

    async def get(self, key):
        return self.data.get(key)

    async def lpush(self, *a):
        pass

    async def ltrim(self, *a):
        pass


def _sha(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


@pytest.mark.asyncio
async def test_dynamic_key_accepted(monkeypatch):
    monkeypatch.setenv("API_KEYS", "static-key")
    redis = FakeRedis({f"api:key:{_sha('tenant-key-1')}": "tenant:abc"})
    auth = AuthModule(redis)

    ok, identity = await auth.verify_api_key("tenant-key-1")
    assert ok and identity == "tenant:abc"

    # Static env keys still work; unknown keys still fail.
    assert (await auth.verify_api_key("static-key"))[0]
    assert not (await auth.verify_api_key("nope"))[0]


@pytest.mark.asyncio
async def test_dynamic_key_without_redis(monkeypatch):
    monkeypatch.setenv("API_KEYS", "static-key")
    assert not (await AuthModule(None).verify_api_key("tenant-key-1"))[0]


@pytest.mark.asyncio
async def test_auth_wrapper_sets_current_api_key(monkeypatch):
    monkeypatch.setenv("API_KEYS", "good-key")
    seen = {}

    async def inner_app(scope, receive, send):
        seen["key"] = current_api_key.get()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    wrapped = add_api_key_auth(inner_app, AuthModule(FakeRedis()))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/a2a/",
        "headers": [(b"x-api-key", b"good-key")],
    }

    async def receive():
        return {"type": "http.request", "body": b""}

    sent = []

    async def send(msg):
        sent.append(msg)

    await wrapped(scope, receive, send)
    assert seen["key"] == "good-key"
    assert sent[0]["status"] == 200
