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
