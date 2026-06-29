#!/usr/bin/env python3
"""
Tests for the MCP server tool adapters (kubently.modules.mcp.tools).

These adapters expose Kubently's multi-cluster troubleshooting over MCP by calling
the same central API endpoints the A2A agent uses (/debug/clusters, /debug/execute).
The logic under test is the request shape and response parsing; HTTP is faked.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kubently.modules.mcp import tools


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class FakeAsyncClient:
    """Minimal async-context httpx.AsyncClient stand-in that records calls."""

    def __init__(self, get_response=None, post_response=None, recorder=None):
        self._get_response = get_response
        self._post_response = post_response
        self._rec = recorder if recorder is not None else {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None):
        self._rec["get_url"] = url
        self._rec["get_headers"] = headers
        return self._get_response

    async def post(self, url, headers=None, json=None):
        self._rec["post_url"] = url
        self._rec["post_headers"] = headers
        self._rec["post_json"] = json
        return self._post_response


@pytest.mark.asyncio
async def test_list_clusters_returns_ids(monkeypatch):
    rec = {}
    monkeypatch.setattr(
        tools.httpx,
        "AsyncClient",
        lambda *a, **k: FakeAsyncClient(
            get_response=FakeResponse(200, {"clusters": ["prod", "staging"]}), recorder=rec
        ),
    )

    result = await tools.list_clusters("http://api:8080", "key123")

    assert result == ["prod", "staging"]
    assert rec["get_url"] == "http://api:8080/debug/clusters"
    assert rec["get_headers"]["X-Api-Key"] == "key123"


@pytest.mark.asyncio
async def test_execute_kubectl_posts_verb_and_args(monkeypatch):
    rec = {}
    monkeypatch.setattr(
        tools.httpx,
        "AsyncClient",
        lambda *a, **k: FakeAsyncClient(
            post_response=FakeResponse(200, {"output": "pod/foo Running"}), recorder=rec
        ),
    )

    result = await tools.execute_kubectl(
        "http://api:8080", "key123", "prod", "get pods", namespace="kube-system"
    )

    assert result == "pod/foo Running"
    assert rec["post_url"] == "http://api:8080/debug/execute"
    body = rec["post_json"]
    assert body["cluster_id"] == "prod"
    assert body["command_type"] == "get"
    assert body["args"] == ["pods"]
    assert body["namespace"] == "kube-system"


@pytest.mark.asyncio
async def test_execute_kubectl_surfaces_http_error(monkeypatch):
    monkeypatch.setattr(
        tools.httpx,
        "AsyncClient",
        lambda *a, **k: FakeAsyncClient(post_response=FakeResponse(404, {"detail": "no cluster"})),
    )

    result = await tools.execute_kubectl("http://api:8080", "key123", "ghost", "get pods")

    assert "404" in result
