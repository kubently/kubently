#!/usr/bin/env python3
"""
Tests for the agent-side log-search tool plumbing.

Two availability contracts are the point here:
- search_pod_logs is always available (kubectl is all it needs), so its
  guidance lives statically in the externalized prompt YAML.
- query_loki exists ONLY when LOKI_URL is set, and the prompt's Loki guidance
  is injected through the {{loki_guidance}} variable on the same switch — the
  model must never be told about a tool it cannot call.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kubently.modules.a2a.protocol_bindings.a2a_server.logsearch import (
    LOKI_PROMPT_SECTION,
    build_log_search_payload,
    build_loki_payload,
    loki_guidance,
    loki_tool_enabled,
)
from kubently.modules.config import get_prompt

REPO = os.path.join(os.path.dirname(__file__), "..")
ROOT_PROMPT = os.path.join(REPO, "prompts", "system.prompt.yaml")
CHART_PROMPT = os.path.join(
    REPO, "deployment", "helm", "kubently", "prompts", "system.prompt.yaml"
)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# Availability gating (Loki)


def test_loki_disabled_when_env_unset_or_blank(monkeypatch):
    monkeypatch.delenv("LOKI_URL", raising=False)
    assert loki_tool_enabled() is False
    assert loki_guidance() == ""

    monkeypatch.setenv("LOKI_URL", "   ")
    assert loki_tool_enabled() is False
    assert loki_guidance() == ""


def test_loki_enabled_when_env_set(monkeypatch):
    monkeypatch.setenv("LOKI_URL", "http://loki:3100")
    assert loki_tool_enabled() is True
    assert loki_guidance() == LOKI_PROMPT_SECTION


# Payload building


def test_build_log_search_payload_defaults():
    payload = build_log_search_payload("payments", "error", selector="app=api")
    assert payload["namespace"] == "payments"
    assert payload["selector"] == "app=api"
    assert payload["pod_name"] is None
    assert payload["since"] == "1h"  # bounded by default
    assert payload["previous"] is False


def test_build_log_search_payload_previous_and_regex():
    payload = build_log_search_payload(
        "payments",
        "error|panic",
        pod_name="api-1",
        use_regex=True,
        previous=True,
        context_lines=3,
        since=None,
        since_time="2026-08-17T00:00:00Z",
    )
    assert payload["use_regex"] is True
    assert payload["previous"] is True
    assert payload["context_lines"] == 3
    assert payload["since"] is None
    assert payload["since_time"] == "2026-08-17T00:00:00Z"


def test_build_loki_payload():
    payload = build_loki_payload('{app="x"} |= "error"', start="1700000000", limit=50)
    assert payload["query"] == '{app="x"} |= "error"'
    assert payload["start"] == "1700000000"
    assert payload["limit"] == 50
    assert payload["direction"] == "backward"


# Prompt: static log-search guidance + Loki injection hook


def test_prompt_files_carry_log_search_guidance_and_loki_hook():
    for path in (ROOT_PROMPT, CHART_PROMPT):
        content = _read(path)
        assert "search_pod_logs" in content, f"{path} lost the log-search guidance"
        assert "{{loki_guidance}}" in content, f"{path} lost the loki hook"


def test_prompt_copies_are_identical():
    assert _read(ROOT_PROMPT) == _read(CHART_PROMPT)


def test_prompt_renders_loki_section_when_enabled(monkeypatch):
    monkeypatch.setenv("KUBENTLY_A2A_PROMPT_FILE", ROOT_PROMPT)
    monkeypatch.setenv("LOKI_URL", "http://loki:3100")
    prompt = get_prompt(role="a2a", variables={"loki_guidance": loki_guidance()})
    assert "query_loki" in prompt
    assert "LogQL" in prompt
    assert "{{loki_guidance}}" not in prompt


def test_prompt_never_mentions_loki_when_disabled(monkeypatch):
    monkeypatch.setenv("KUBENTLY_A2A_PROMPT_FILE", ROOT_PROMPT)
    monkeypatch.delenv("LOKI_URL", raising=False)
    prompt = get_prompt(role="a2a", variables={"loki_guidance": loki_guidance()})
    assert "query_loki" not in prompt
    assert "Loki" not in prompt
    assert "{{loki_guidance}}" not in prompt
    # the always-available tool keeps its guidance either way
    assert "search_pod_logs" in prompt


def test_prompt_default_keeps_placeholder_out_even_without_variables(monkeypatch):
    """Older callers that don't pass variables still get a clean prompt: the
    declared default ('') must swallow the placeholder."""
    monkeypatch.setenv("KUBENTLY_A2A_PROMPT_FILE", ROOT_PROMPT)
    prompt = get_prompt(role="a2a")
    assert "{{loki_guidance}}" not in prompt
    assert "query_loki" not in prompt


def test_guidance_covers_the_required_topics():
    """Track C1b asks for specific guidance: when to search logs (errors after
    a deploy, correlating restarts, upstream failures), narrowing first, and
    preferring Loki when present."""
    prompt_text = _read(ROOT_PROMPT).lower()
    for topic in ("deploy", "restart", "upstream", "selector", "narrow"):
        assert topic in prompt_text, f"log-search guidance lost its '{topic}' coverage"

    loki_text = LOKI_PROMPT_SECTION.lower()
    for topic in ("prefer", "restarted", "label selector", "count_over_time"):
        assert topic in loki_text, f"loki guidance lost its '{topic}' coverage"
