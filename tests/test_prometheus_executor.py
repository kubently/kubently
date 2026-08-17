#!/usr/bin/env python3
"""
Tests for the executor-side Prometheus query runner.

The runner is the security boundary for the first non-kubectl evidence source:
it must only ever GET the two whitelisted query paths against the LOCALLY
configured base URL (never one supplied by the control plane), report cleanly
when unconfigured, and cap results before they leave the executor.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kubently.modules.executor.prometheus import (
    ALLOWED_QUERY_PATHS,
    PrometheusRunner,
    _thin_samples,
)
from kubently.modules.executor.sse_executor import SSEKubentlyExecutor


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def prom_success(result, result_type="vector"):
    return {"status": "success", "data": {"resultType": result_type, "result": result}}


@pytest.fixture
def capture_get(monkeypatch):
    """Capture requests.get calls and return a canned response."""
    calls = {}

    def install(response):
        def fake_get(url, params=None, timeout=None):
            calls["url"] = url
            calls["params"] = params
            calls["timeout"] = timeout
            return response

        monkeypatch.setattr("kubently.modules.executor.prometheus.requests.get", fake_get)
        return calls

    return install


# Availability


def test_unconfigured_runner_reports_unavailable_without_network(monkeypatch):
    monkeypatch.delenv("PROMETHEUS_URL", raising=False)

    def no_network(*a, **k):
        raise AssertionError("must not touch the network when unconfigured")

    monkeypatch.setattr("kubently.modules.executor.prometheus.requests.get", no_network)

    runner = PrometheusRunner()
    assert runner.available is False
    result = runner.run({"query_type": "instant", "query": "up"})
    assert result["success"] is False
    assert result["status"] == "UNAVAILABLE"
    assert "not configured" in result["error"]


# Path allowlist / request building


def test_instant_query_hits_only_the_query_path(capture_get):
    calls = capture_get(FakeResponse(prom_success([])))
    runner = PrometheusRunner(base_url="http://prom:9090")
    result = runner.run({"query_type": "instant", "query": "up", "time": "1700000000"})
    assert result["success"] is True
    assert calls["url"] == "http://prom:9090/api/v1/query"
    assert calls["params"] == {"query": "up", "time": "1700000000"}


def test_range_query_hits_only_the_query_range_path(capture_get):
    calls = capture_get(FakeResponse(prom_success([], "matrix")))
    runner = PrometheusRunner(base_url="http://prom:9090/")  # trailing slash normalized
    result = runner.run(
        {
            "query_type": "range",
            "query": "rate(x[5m])",
            "start": "1700000000",
            "end": "1700003600",
            "step": "60s",
        }
    )
    assert result["success"] is True
    assert calls["url"] == "http://prom:9090/api/v1/query_range"
    assert calls["params"]["step"] == "60s"


def test_unknown_query_type_is_rejected_without_network(monkeypatch):
    monkeypatch.setattr(
        "kubently.modules.executor.prometheus.requests.get",
        lambda *a, **k: pytest.fail("must not reach the network"),
    )
    runner = PrometheusRunner(base_url="http://prom:9090")
    result = runner.run({"query_type": "admin", "query": "up"})
    assert result["success"] is False
    assert "Unsupported query_type" in result["error"]


def test_range_query_requires_start_end_step():
    runner = PrometheusRunner(base_url="http://prom:9090")
    result = runner.run({"query_type": "range", "query": "up", "start": "1700000000"})
    assert result["success"] is False
    assert "end" in result["error"] and "step" in result["error"]


def test_allowlist_is_exactly_two_read_paths():
    assert ALLOWED_QUERY_PATHS == {
        "instant": "/api/v1/query",
        "range": "/api/v1/query_range",
    }


# Error surfacing


def test_promql_error_is_surfaced(capture_get):
    capture_get(
        FakeResponse(
            {"status": "error", "errorType": "bad_data", "error": "parse error at char 3"},
            status_code=400,
        )
    )
    runner = PrometheusRunner(base_url="http://prom:9090")
    result = runner.run({"query_type": "instant", "query": "up{"})
    assert result["success"] is False
    assert "bad_data" in result["error"]
    assert "parse error" in result["error"]


# Result capping


def _series(name, n_values=0):
    metric = {"__name__": "m", "pod": name}
    if n_values:
        return {"metric": metric, "values": [[i, str(i)] for i in range(n_values)]}
    return {"metric": metric, "value": [0, "1"]}


def test_series_cap_truncates_and_notes(capture_get):
    capture_get(FakeResponse(prom_success([_series(f"p{i}") for i in range(10)])))
    runner = PrometheusRunner(base_url="http://prom:9090", max_series=3)
    result = runner.run({"query_type": "instant", "query": "up"})
    payload = json.loads(result["output"])
    assert len(payload["result"]) == 3
    assert any("3 of 10 series" in n for n in payload["kubently_truncation"])


def test_sample_cap_downsamples_keeping_endpoints(capture_get):
    series = [_series("p0", n_values=100), _series("p1", n_values=100)]
    capture_get(FakeResponse(prom_success(series, "matrix")))
    runner = PrometheusRunner(base_url="http://prom:9090", max_samples=20)
    result = runner.run(
        {"query_type": "range", "query": "m", "start": "0", "end": "1", "step": "1s"}
    )
    payload = json.loads(result["output"])
    for s in payload["result"]:
        assert len(s["values"]) <= 11  # 20 // 2 series, +1 for endpoint inclusion
        assert s["values"][0][0] == 0  # first sample kept
        assert s["values"][-1][0] == 99  # last sample kept
    assert any("downsampled" in n for n in payload["kubently_truncation"])


def test_small_results_pass_through_untruncated(capture_get):
    capture_get(FakeResponse(prom_success([_series("p0", n_values=5)], "matrix")))
    runner = PrometheusRunner(base_url="http://prom:9090")
    result = runner.run(
        {"query_type": "range", "query": "m", "start": "0", "end": "1", "step": "1s"}
    )
    payload = json.loads(result["output"])
    assert len(payload["result"][0]["values"]) == 5
    assert "kubently_truncation" not in payload


def test_char_cap_is_final_backstop(capture_get):
    capture_get(FakeResponse(prom_success([_series(f"pod-{i}" * 10) for i in range(40)])))
    runner = PrometheusRunner(base_url="http://prom:9090", max_series=50, max_output_chars=500)
    result = runner.run({"query_type": "instant", "query": "up"})
    assert len(result["output"]) < 700
    assert "truncated at 500 chars" in result["output"]


def test_thin_samples_even_spacing():
    values = list(range(100))
    thinned = _thin_samples(values, 10)
    assert thinned[0] == 0 and thinned[-1] == 99
    assert len(thinned) <= 11
    assert _thin_samples([1, 2], 10) == [1, 2]
    assert _thin_samples(values, 1) == [99]


# Executor dispatch


@pytest.fixture
def executor(monkeypatch):
    monkeypatch.setenv("KUBENTLY_API_URL", "http://localhost:8080")
    monkeypatch.setenv("CLUSTER_ID", "test-cluster")
    monkeypatch.setenv("KUBENTLY_TOKEN", "test-token")
    monkeypatch.setenv("KUBENTLY_WHITELIST_CONFIG", "/nonexistent/whitelist.yaml")
    monkeypatch.delenv("PROMETHEUS_URL", raising=False)
    return SSEKubentlyExecutor()


def test_dispatch_default_tool_is_kubectl(executor, monkeypatch):
    monkeypatch.setattr(executor, "_run_kubectl", lambda args: {"success": True, "output": "ok"})
    result = executor._run_tool({"args": ["get", "pods"]})
    assert result["output"] == "ok"


def test_dispatch_routes_prometheus_tool(executor):
    # No PROMETHEUS_URL set -> the runner answers "unavailable", proving the
    # envelope reached the prometheus runner and not kubectl.
    result = executor._run_tool({"tool": "prometheus", "request": {"query": "up"}})
    assert result["status"] == "UNAVAILABLE"


def test_dispatch_rejects_unknown_tool(executor):
    result = executor._run_tool({"tool": "curl", "request": {}})
    assert result["success"] is False
    assert result["status"] == "BLOCKED"
