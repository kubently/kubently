#!/usr/bin/env python3
"""
Tests for the agent-side Prometheus tool plumbing.

The availability contract is the point: PROMETHEUS_URL unset means the tool is
not registered AND the prompt says nothing about metrics — the model must never
be told about a tool it cannot call. Both switches flip on the same env var, so
these tests guard that they cannot drift apart.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kubently.modules.a2a.protocol_bindings.a2a_server.prometheus import (
    METRICS_PROMPT_SECTION,
    build_prometheus_payload,
    metrics_guidance,
    prometheus_tool_enabled,
)
from kubently.modules.config import get_prompt

REPO = os.path.join(os.path.dirname(__file__), "..")
ROOT_PROMPT = os.path.join(REPO, "prompts", "system.prompt.yaml")
CHART_PROMPT = os.path.join(
    REPO, "deployment", "helm", "kubently", "prompts", "system.prompt.yaml"
)


# Availability gating


def test_disabled_when_env_unset_or_blank(monkeypatch):
    monkeypatch.delenv("PROMETHEUS_URL", raising=False)
    assert prometheus_tool_enabled() is False
    assert metrics_guidance() == ""

    monkeypatch.setenv("PROMETHEUS_URL", "   ")
    assert prometheus_tool_enabled() is False
    assert metrics_guidance() == ""


def test_enabled_when_env_set(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "http://prom:9090")
    assert prometheus_tool_enabled() is True
    assert metrics_guidance() == METRICS_PROMPT_SECTION


# Payload building


def test_build_instant_payload():
    payload = build_prometheus_payload("up", time="1700000000")
    assert payload["query"] == "up"
    assert payload["query_type"] == "instant"
    assert payload["time"] == "1700000000"
    assert payload["start"] is None


def test_build_range_payload():
    payload = build_prometheus_payload(
        "rate(x[5m])", query_type="range", start="0", end="1", step="60s"
    )
    assert payload["query_type"] == "range"
    assert (payload["start"], payload["end"], payload["step"]) == ("0", "1", "60s")


# Prompt injection: the shipped prompt must carry the {{metrics_guidance}}
# hook, and get_prompt must render it in when enabled / to nothing when not.


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_prompt_files_declare_the_metrics_variable():
    for path in (ROOT_PROMPT, CHART_PROMPT):
        content = _read(path)
        assert "{{metrics_guidance}}" in content, f"{path} lost the metrics hook"
        assert "metrics_guidance" in content


def test_prompt_renders_metrics_section_when_enabled(monkeypatch):
    monkeypatch.setenv("KUBENTLY_A2A_PROMPT_FILE", ROOT_PROMPT)
    monkeypatch.setenv("PROMETHEUS_URL", "http://prom:9090")
    prompt = get_prompt(role="a2a", variables={"metrics_guidance": metrics_guidance()})
    assert "## Metrics (Prometheus)" in prompt
    assert "query_prometheus" in prompt
    assert "{{metrics_guidance}}" not in prompt


def test_prompt_never_mentions_metrics_when_disabled(monkeypatch):
    monkeypatch.setenv("KUBENTLY_A2A_PROMPT_FILE", ROOT_PROMPT)
    monkeypatch.delenv("PROMETHEUS_URL", raising=False)
    prompt = get_prompt(role="a2a", variables={"metrics_guidance": metrics_guidance()})
    assert "query_prometheus" not in prompt
    assert "Prometheus" not in prompt
    assert "{{metrics_guidance}}" not in prompt


def test_prompt_default_keeps_placeholder_out_even_without_variables(monkeypatch):
    """Older callers that don't pass variables still get a clean prompt: the
    declared default ('') must swallow the placeholder."""
    monkeypatch.setenv("KUBENTLY_A2A_PROMPT_FILE", ROOT_PROMPT)
    prompt = get_prompt(role="a2a")
    assert "{{metrics_guidance}}" not in prompt
    assert "query_prometheus" not in prompt


def test_guidance_covers_the_required_topics():
    """Track C1a asks for specific guidance: when to reach for metrics and how
    to write efficient, size-bounded PromQL."""
    text = METRICS_PROMPT_SECTION.lower()
    for topic in ("latency", "saturation", "oom", "restart", "topk", "rate(", "filter"):
        assert topic in text, f"metrics guidance lost its '{topic}' coverage"
