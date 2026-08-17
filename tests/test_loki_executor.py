#!/usr/bin/env python3
"""
Tests for the executor-side Loki query runner.

Same security contract as the Prometheus runner: only ever GET the single
whitelisted query_range path against the LOCALLY configured base URL (never
one supplied by the control plane), report cleanly when unconfigured, and cap
results before they leave the executor.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kubently.modules.executor.loki import (
    QUERY_RANGE_PATH,
    LokiRunner,
    format_loki_timestamp,
    format_streams,
)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def loki_streams(*streams):
    return {
        "status": "success",
        "data": {"resultType": "streams", "result": list(streams)},
    }


def stream(labels, *lines, start_ns=1700000000_000000000):
    return {
        "stream": labels,
        "values": [[str(start_ns + i), line] for i, line in enumerate(lines)],
    }


@pytest.fixture
def capture_get(monkeypatch):
    """Capture requests.get calls and return a canned response."""
    calls = {}

    def install(response):
        def fake_get(url, params=None, headers=None, timeout=None):
            calls["url"] = url
            calls["params"] = params
            calls["headers"] = headers
            calls["timeout"] = timeout
            return response

        monkeypatch.setattr("kubently.modules.executor.loki.requests.get", fake_get)
        return calls

    return install


# Availability


def test_unconfigured_runner_reports_unavailable_without_network(monkeypatch):
    monkeypatch.delenv("LOKI_URL", raising=False)

    def no_network(*a, **k):
        raise AssertionError("must not touch the network when unconfigured")

    monkeypatch.setattr("kubently.modules.executor.loki.requests.get", no_network)

    runner = LokiRunner()
    assert runner.available is False
    result = runner.run({"query": '{app="x"}'})
    assert result["success"] is False
    assert result["status"] == "UNAVAILABLE"
    assert "not configured" in result["error"]
    assert "search_pod_logs" in result["error"]  # points the model at the fallback


# Path allowlist / request building


def test_query_hits_only_the_query_range_path(capture_get):
    calls = capture_get(FakeResponse(loki_streams()))
    runner = LokiRunner(base_url="http://loki:3100/")  # trailing slash normalized
    result = runner.run(
        {"query": '{app="x"} |= "error"', "start": "1700000000", "end": "1700003600"}
    )
    assert result["success"] is True
    assert calls["url"] == "http://loki:3100" + QUERY_RANGE_PATH
    assert calls["params"]["query"] == '{app="x"} |= "error"'
    assert calls["params"]["start"] == "1700000000"
    assert calls["params"]["direction"] == "backward"


def test_start_end_omitted_lets_loki_default_the_window(capture_get):
    calls = capture_get(FakeResponse(loki_streams()))
    LokiRunner(base_url="http://loki:3100").run({"query": '{app="x"}'})
    assert "start" not in calls["params"] and "end" not in calls["params"]


def test_limit_is_clamped_to_max_lines(capture_get):
    calls = capture_get(FakeResponse(loki_streams()))
    runner = LokiRunner(base_url="http://loki:3100", max_lines=50)
    runner.run({"query": '{app="x"}', "limit": 5000})
    assert calls["params"]["limit"] == 50


def test_invalid_direction_rejected_without_network(monkeypatch):
    monkeypatch.setattr(
        "kubently.modules.executor.loki.requests.get",
        lambda *a, **k: pytest.fail("must not reach the network"),
    )
    result = LokiRunner(base_url="http://loki:3100").run(
        {"query": '{app="x"}', "direction": "sideways"}
    )
    assert result["success"] is False
    assert "direction" in result["error"]


def test_missing_query_rejected():
    result = LokiRunner(base_url="http://loki:3100").run({})
    assert result["success"] is False
    assert "query" in result["error"]


def test_tenant_header_sent_only_when_configured(capture_get):
    calls = capture_get(FakeResponse(loki_streams()))
    LokiRunner(base_url="http://loki:3100", tenant_id="team-a").run({"query": '{app="x"}'})
    assert calls["headers"] == {"X-Scope-OrgID": "team-a"}

    calls = capture_get(FakeResponse(loki_streams()))
    LokiRunner(base_url="http://loki:3100", tenant_id="").run({"query": '{app="x"}'})
    assert calls["headers"] == {}


# Error surfacing


def test_logql_error_is_surfaced(capture_get):
    capture_get(FakeResponse({"status": "error", "error": "parse error at line 1"}, 400))
    result = LokiRunner(base_url="http://loki:3100").run({"query": "{bad"})
    assert result["success"] is False
    assert "parse error" in result["error"]


# Result formatting / capping


def test_streams_formatted_with_labels_and_rfc3339_timestamps(capture_get):
    capture_get(
        FakeResponse(
            loki_streams(
                stream({"pod": "api-1", "namespace": "x"}, "error: db down"),
            )
        )
    )
    result = LokiRunner(base_url="http://loki:3100").run({"query": '{app="x"}'})
    out = result["output"]
    assert '=== {namespace="x", pod="api-1"} ===' in out
    assert "error: db down" in out
    assert "2023-11-14T" in out  # 1700000000s rendered as RFC3339


def test_line_cap_across_streams_notes_truncation(capture_get):
    capture_get(
        FakeResponse(
            loki_streams(
                stream({"pod": "a"}, *[f"err {i}" for i in range(8)]),
                stream({"pod": "b"}, *[f"err {i}" for i in range(8)]),
            )
        )
    )
    runner = LokiRunner(base_url="http://loki:3100", max_lines=10)
    out = runner.run({"query": '{app="x"}'})["output"]
    assert "showing 10 of 16 log lines" in out


def test_long_lines_truncated():
    text, _ = format_streams(
        [stream({"pod": "a"}, "e" * 1000)], max_lines=10, max_line_chars=50
    )
    assert "[line truncated]" in text


def test_empty_result_gives_hint(capture_get):
    capture_get(FakeResponse(loki_streams()))
    out = LokiRunner(base_url="http://loki:3100").run({"query": '{app="x"}'})["output"]
    assert "No log lines matched" in out


def test_metric_style_result_passes_through_as_json(capture_get):
    capture_get(
        FakeResponse(
            {
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [{"metric": {"pod": "a"}, "values": [[1700000000, "3"]]}],
                },
            }
        )
    )
    out = LokiRunner(base_url="http://loki:3100").run(
        {"query": 'sum by (pod) (count_over_time({app="x"} |= "error" [1h]))'}
    )["output"]
    payload = json.loads(out)
    assert payload["resultType"] == "matrix"


def test_char_cap_is_final_backstop(capture_get):
    capture_get(
        FakeResponse(
            loki_streams(stream({"pod": "a"}, *[f"line {i} " + "x" * 100 for i in range(50)]))
        )
    )
    runner = LokiRunner(base_url="http://loki:3100", max_output_chars=500)
    out = runner.run({"query": '{app="x"}'})["output"]
    assert len(out) < 700
    assert "truncated at 500 chars" in out


def test_timestamp_formatter_falls_back_on_garbage():
    assert format_loki_timestamp("not-a-number") == "not-a-number"
    assert format_loki_timestamp("1700000000000000000").startswith("2023-11-14T")
