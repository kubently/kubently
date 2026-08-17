#!/usr/bin/env python3
"""
Tests for the executor-side multi-pod log search runner.

The runner composes ONLY whitelist-checked `get pods` / `logs` invocations
through the injected kubectl runner, filters logs locally so raw logs never
leave the executor, caps output at every level, and announces every cap that
fires in the output the model reads.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kubently.modules.executor.logsearch import (
    LogSearchRunner,
    build_matcher,
    filter_lines,
)
from kubently.modules.executor.sse_executor import SSEKubentlyExecutor


# -- matcher building --------------------------------------------------------


def test_substring_matcher_is_case_insensitive_by_default():
    m = build_matcher("Connection Refused")
    assert m("dial tcp: CONNECTION REFUSED")
    assert m("connection refused by peer")
    assert not m("connection accepted")


def test_substring_matcher_case_sensitive():
    m = build_matcher("Error", case_sensitive=True)
    assert m("Error: boom")
    assert not m("error: boom")


def test_regex_matcher_alternation():
    m = build_matcher("error|exception|timed? ?out", use_regex=True)
    assert m("java.lang.NullPointerException")
    assert m("request timed out")
    assert m("TIMEOUT waiting for upstream")
    assert not m("all good")


def test_invalid_regex_raises_readable_error():
    with pytest.raises(ValueError, match="Invalid regex"):
        build_matcher("([unclosed", use_regex=True)


def test_empty_and_oversized_queries_rejected():
    with pytest.raises(ValueError):
        build_matcher("")
    with pytest.raises(ValueError, match="too long"):
        build_matcher("x" * 1000)


# -- line filtering / caps ---------------------------------------------------


def test_filter_reports_total_matches_beyond_cap():
    lines = [f"error {i}" for i in range(20)]
    kept, total, capped = filter_lines(lines, build_matcher("error"), max_matches=5)
    assert total == 20
    assert capped is True
    assert len(kept) == 5


def test_filter_context_merges_overlaps_and_marks_gaps():
    lines = ["a", "b", "ERROR one", "c", "ERROR two", "d", "e", "f", "g", "ERROR three"]
    kept, total, capped = filter_lines(lines, build_matcher("error"), context_lines=1)
    assert total == 3
    assert capped is False
    # First two matches' context windows overlap (b..d contiguous); the third
    # is separated by a "..." gap marker.
    assert kept == ["b", "ERROR one", "c", "ERROR two", "d", "...", "g", "ERROR three"]


def test_filter_truncates_long_lines():
    lines = ["error " + "x" * 1000]
    kept, _, _ = filter_lines(lines, build_matcher("error"), max_line_chars=50)
    assert len(kept) == 1
    assert kept[0].endswith("[line truncated]")
    assert len(kept[0]) < 100


def test_filter_no_matches():
    kept, total, capped = filter_lines(["a", "b"], build_matcher("zzz"))
    assert (kept, total, capped) == ([], 0, False)


# -- runner end-to-end with a fake kubectl -----------------------------------


def pod_list(*pods):
    items = []
    for name, containers in pods:
        items.append(
            {
                "metadata": {"name": name},
                "spec": {"containers": [{"name": c} for c in containers]},
            }
        )
    return {"kind": "PodList", "items": items}


class FakeKubectl:
    """Records every argv list and returns canned pod-list/logs responses."""

    def __init__(self, pods, logs_by_target=None, logs_error=None):
        self.pods = pods
        self.logs_by_target = logs_by_target or {}
        self.logs_error = logs_error
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        if args[0] == "get":
            return {"success": True, "stdout": json.dumps(self.pods), "output": ""}
        assert args[0] == "logs", f"unexpected kubectl verb: {args[0]}"
        pod = args[1]
        container = next(a.split("=", 1)[1] for a in args if a.startswith("--container="))
        if self.logs_error:
            return {"success": False, "error": self.logs_error, "output": None}
        text = self.logs_by_target.get((pod, container), "")
        return {"success": True, "stdout": text, "output": text}


def run_search(kubectl, request=None, **runner_kwargs):
    runner = LogSearchRunner(kubectl_runner=kubectl, **runner_kwargs)
    base = {"namespace": "payments", "selector": "app=api", "query": "error"}
    base.update(request or {})
    return runner.run(base)


def test_search_finds_matches_across_pods_and_containers():
    kubectl = FakeKubectl(
        pod_list(("api-1", ["app", "sidecar"]), ("api-2", ["app"])),
        logs_by_target={
            ("api-1", "app"): "ok\nerror: db down\nok",
            ("api-1", "sidecar"): "all fine",
            ("api-2", "app"): "ERROR: db down again",
        },
    )
    result = run_search(kubectl)
    assert result["success"] is True
    out = result["output"]
    assert "=== api-1/app ===" in out
    assert "error: db down" in out
    assert "=== api-2/app ===" in out
    assert "2 matching line(s)" in out
    assert "No matches: api-1/sidecar" in out


def test_search_composes_only_get_and_logs_with_flag_equals_form():
    kubectl = FakeKubectl(pod_list(("api-1", ["app"])), logs_by_target={})
    run_search(
        kubectl,
        {"since": "30m", "previous": True, "tail_lines": 100, "container": "app"},
    )
    verbs = {c[0] for c in kubectl.calls}
    assert verbs == {"get", "logs"}
    logs_call = next(c for c in kubectl.calls if c[0] == "logs")
    assert "--namespace=payments" in logs_call
    assert "--container=app" in logs_call
    assert "--tail=100" in logs_call
    assert "--since=30m" in logs_call
    assert "--previous" in logs_call
    assert "--timestamps" in logs_call
    get_call = next(c for c in kubectl.calls if c[0] == "get")
    assert "--selector=app=api" in get_call


def test_since_time_takes_precedence_over_since():
    kubectl = FakeKubectl(pod_list(("api-1", ["app"])))
    run_search(kubectl, {"since": "1h", "since_time": "2026-08-17T00:00:00Z"})
    logs_call = next(c for c in kubectl.calls if c[0] == "logs")
    assert "--since-time=2026-08-17T00:00:00Z" in logs_call
    assert not any(a.startswith("--since=") for a in logs_call)


def test_pod_name_mode_fetches_single_pod():
    single = {"kind": "Pod", "metadata": {"name": "api-1"},
              "spec": {"containers": [{"name": "app"}]}}
    kubectl = FakeKubectl(single, logs_by_target={("api-1", "app"): "an error here"})
    result = run_search(kubectl, {"selector": None, "pod_name": "api-1"})
    assert result["success"] is True
    assert "api-1/app" in result["output"]
    get_call = next(c for c in kubectl.calls if c[0] == "get")
    assert get_call[2] == "api-1"


def test_requires_exactly_one_of_selector_and_pod_name():
    kubectl = FakeKubectl(pod_list())
    both = run_search(kubectl, {"pod_name": "api-1"})
    neither = run_search(kubectl, {"selector": None})
    assert both["success"] is False and "exactly one" in both["error"]
    assert neither["success"] is False and "exactly one" in neither["error"]
    assert kubectl.calls == []  # rejected before any kubectl call


def test_invalid_namespace_and_pod_name_rejected_before_kubectl():
    kubectl = FakeKubectl(pod_list())
    bad_ns = run_search(kubectl, {"namespace": "-oops"})
    assert bad_ns["success"] is False and "namespace" in bad_ns["error"]
    bad_pod = run_search(kubectl, {"selector": None, "pod_name": "--previous"})
    assert bad_pod["success"] is False and "pod name" in bad_pod["error"]
    assert kubectl.calls == []


def test_invalid_regex_surfaces_as_error():
    kubectl = FakeKubectl(pod_list(("api-1", ["app"])))
    result = run_search(kubectl, {"query": "([", "use_regex": True})
    assert result["success"] is False
    assert "Invalid regex" in result["error"]


def test_no_pods_matched_is_success_with_hint():
    kubectl = FakeKubectl(pod_list())
    result = run_search(kubectl)
    assert result["success"] is True
    assert "No pods" in result["output"]
    assert "--show-labels" in result["output"]


def test_pod_cap_limits_scan_and_notes():
    pods = pod_list(*[(f"api-{i}", ["app"]) for i in range(6)])
    kubectl = FakeKubectl(pods, logs_by_target={(f"api-{i}", "app"): "no hits" for i in range(6)})
    result = run_search(kubectl, max_pods=3)
    assert "matched 6 pods" in result["output"]
    assert "first 3" in result["output"]
    assert len([c for c in kubectl.calls if c[0] == "logs"]) == 3


def test_per_container_match_cap_notes_shown_vs_total():
    logs = "\n".join(f"error {i}" for i in range(30))
    kubectl = FakeKubectl(pod_list(("api-1", ["app"])), logs_by_target={("api-1", "app"): logs})
    result = run_search(kubectl, max_matches_per_container=5)
    assert "showing 5 of 30 matches" in result["output"]


def test_total_match_cap_stops_scanning_and_notes():
    logs = "\n".join(f"error {i}" for i in range(10))
    pods = pod_list(("api-1", ["app"]), ("api-2", ["app"]), ("api-3", ["app"]))
    kubectl = FakeKubectl(pods, logs_by_target={(f"api-{i}", "app"): logs for i in (1, 2, 3)})
    result = run_search(kubectl, max_total_matches=10)
    out = result["output"]
    assert "total match cap (10) reached" in out
    # third container never fetched
    assert len([c for c in kubectl.calls if c[0] == "logs"]) < 3


def test_output_char_cap_is_final_backstop():
    logs = "\n".join(f"error {'x' * 80} {i}" for i in range(50))
    kubectl = FakeKubectl(pod_list(("api-1", ["app"])), logs_by_target={("api-1", "app"): logs})
    result = run_search(kubectl, max_output_chars=800)
    assert len(result["output"]) < 1000
    assert "truncated at 800 chars" in result["output"]


def test_previous_without_prior_container_is_not_fatal():
    kubectl = FakeKubectl(
        pod_list(("api-1", ["app"])),
        logs_error='previous terminated container "app" in pod "api-1" not found',
    )
    result = run_search(kubectl, {"previous": True})
    assert result["success"] is True
    assert "no previous container" in result["output"]


def test_kubectl_error_reported_per_container_not_fatal():
    kubectl = FakeKubectl(pod_list(("api-1", ["app"])), logs_error="Blocked by whitelist: nope")
    result = run_search(kubectl)
    assert result["success"] is True
    assert "Errors:" in result["output"]
    assert "Blocked by whitelist" in result["output"]


def test_zero_matches_gives_hint():
    kubectl = FakeKubectl(pod_list(("api-1", ["app"])), logs_by_target={("api-1", "app"): "quiet"})
    result = run_search(kubectl)
    assert "0 matching line(s)" in result["output"]
    assert "previous=true" in result["output"]


# -- executor dispatch -------------------------------------------------------


@pytest.fixture
def executor(monkeypatch):
    monkeypatch.setenv("KUBENTLY_API_URL", "http://localhost:8080")
    monkeypatch.setenv("CLUSTER_ID", "test-cluster")
    monkeypatch.setenv("KUBENTLY_TOKEN", "test-token")
    monkeypatch.setenv("KUBENTLY_WHITELIST_CONFIG", "/nonexistent/whitelist.yaml")
    monkeypatch.delenv("LOKI_URL", raising=False)
    return SSEKubentlyExecutor()


def test_dispatch_default_tool_is_kubectl(executor, monkeypatch):
    monkeypatch.setattr(executor, "_run_kubectl", lambda args: {"success": True, "output": "ok"})
    result = executor._run_tool({"args": ["get", "pods"]})
    assert result["output"] == "ok"


def test_dispatch_routes_log_search_through_kubectl_runner(executor, monkeypatch):
    monkeypatch.setattr(
        executor,
        "_run_kubectl",
        lambda args: {"success": True, "stdout": json.dumps(pod_list()), "output": ""},
    )
    # Re-wire the runner's injected kubectl to the patched method
    executor._logsearch._kubectl = executor._run_kubectl
    result = executor._run_tool(
        {
            "tool": "log_search",
            "request": {"namespace": "default", "selector": "app=x", "query": "error"},
        }
    )
    assert result["success"] is True
    assert "No pods" in result["output"]


def test_dispatch_routes_loki_tool(executor):
    # No LOKI_URL set -> the runner answers "unavailable", proving the
    # envelope reached the loki runner and not kubectl.
    result = executor._run_tool({"tool": "loki", "request": {"query": "{app=\"x\"}"}})
    assert result["status"] == "UNAVAILABLE"


def test_dispatch_rejects_unknown_tool(executor):
    result = executor._run_tool({"tool": "curl", "request": {}})
    assert result["success"] is False
    assert result["status"] == "BLOCKED"
