#!/usr/bin/env python3
"""Unit tests for fleet fan-out (execute_kubectl_multi internals)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kubently.modules.a2a.protocol_bindings.a2a_server.fleet import (  # noqa: E402
    PER_CLUSTER_OUTPUT_CAP,
    build_execute_payload,
    format_fleet_results,
    format_section,
)


def test_build_execute_payload_basic():
    p = build_execute_payload("get pods -o wide", "default")
    assert p["command_type"] == "get"
    assert p["args"] == ["pods", "-o", "wide"]
    assert p["namespace"] is None
    assert p["timeout_seconds"] == 30


def test_build_execute_payload_namespace_flag_in_command_wins():
    p = build_execute_payload("get pods -n payments", "default")
    assert p["namespace"] == "payments"


def test_build_execute_payload_namespace_param():
    p = build_execute_payload("get pods", "payments")
    assert p["namespace"] == "payments"


def test_build_execute_payload_all_namespaces_adds_A():
    p = build_execute_payload("get pods", "all")
    assert "-A" in p["args"]
    assert p["namespace"] is None


def test_format_section_plain():
    s = format_section("prod-east", True, "pod-a Running\n")
    assert s == "=== cluster: prod-east ===\npod-a Running"


def test_format_section_error():
    s = format_section("prod-east", False, "HTTP 500: boom")
    assert s == "=== cluster: prod-east ===\nERROR: HTTP 500: boom"


def test_format_section_empty_collapses():
    assert format_section("prod-east", True, "  \n") == "=== cluster: prod-east === (no matching resources)"
    assert (
        format_section("prod-east", True, "No resources found in payments namespace.")
        == "=== cluster: prod-east === (no matching resources)"
    )


def test_format_section_truncates_at_cap():
    s = format_section("prod-east", True, "x" * (PER_CLUSTER_OUTPUT_CAP + 500))
    assert "[truncated — run execute_kubectl on prod-east for full output]" in s
    # header + capped body + truncation note; nowhere near the raw size
    assert len(s) < PER_CLUSTER_OUTPUT_CAP + 200


def test_format_fleet_results_joins_sections():
    out = format_fleet_results([("a", True, "ok"), ("b", False, "down")])
    assert "=== cluster: a ===\nok" in out
    assert "=== cluster: b ===\nERROR: down" in out


import json  # noqa: E402

import httpx  # noqa: E402

from kubently.modules.a2a.protocol_bindings.a2a_server.fleet import (  # noqa: E402
    MAX_FLEET_CLUSTERS,
    run_fleet_command,
)

API = "http://api.test"
KEY = "k"
PAYLOAD = {"command_type": "get", "args": ["pods"], "namespace": None, "timeout_seconds": 30}


def _mock_client(clusters, per_cluster):
    """MockTransport serving /debug/clusters and /debug/execute.

    per_cluster: cluster_id -> httpx.Response for its /debug/execute call.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/debug/clusters":
            return httpx.Response(200, json={"clusters": clusters})
        if request.url.path == "/debug/execute":
            cluster_id = json.loads(request.content)["cluster_id"]
            return per_cluster[cluster_id]
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_run_fleet_command_aggregates_named_clusters():
    client = _mock_client(
        ["a", "b"],
        {
            "a": httpx.Response(200, json={"output": "pod-a Running"}),
            "b": httpx.Response(200, json={"output": "pod-b CrashLoopBackOff"}),
        },
    )
    out = await run_fleet_command(API, KEY, ["a", "b"], PAYLOAD, client=client)
    assert "=== cluster: a ===\npod-a Running" in out
    assert "=== cluster: b ===\npod-b CrashLoopBackOff" in out


async def test_run_fleet_command_resolves_all():
    client = _mock_client(
        ["a", "b"],
        {
            "a": httpx.Response(200, json={"output": "x"}),
            "b": httpx.Response(200, json={"output": "y"}),
        },
    )
    out = await run_fleet_command(API, KEY, ["all"], PAYLOAD, client=client)
    assert "=== cluster: a ===" in out and "=== cluster: b ===" in out


async def test_run_fleet_command_error_isolation():
    client = _mock_client(
        ["a", "b"],
        {
            "a": httpx.Response(500, text="boom"),
            "b": httpx.Response(200, json={"output": "fine"}),
        },
    )
    out = await run_fleet_command(API, KEY, ["a", "b"], PAYLOAD, client=client)
    assert "=== cluster: a ===\nERROR: HTTP 500" in out
    assert "=== cluster: b ===\nfine" in out


async def test_run_fleet_command_cap():
    many = [f"c{i}" for i in range(MAX_FLEET_CLUSTERS + 1)]
    client = _mock_client(many, {})
    out = await run_fleet_command(API, KEY, many, PAYLOAD, client=client)
    assert "capped at" in out


async def test_run_fleet_command_no_clusters():
    client = _mock_client([], {})
    out = await run_fleet_command(API, KEY, ["all"], PAYLOAD, client=client)
    assert out == "No clusters are currently registered."
