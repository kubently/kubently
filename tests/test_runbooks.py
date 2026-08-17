#!/usr/bin/env python3
"""Tests for operator runbook ingestion (kubently.modules.runbooks).

Covers the three behaviours that matter in production:
1. Matching — alert name globs, namespace/workload selectors, topic tags,
   and the ranking between them.
2. Size capping — best match wins over concatenating everything; a single
   oversized runbook is truncated, not dropped.
3. Injection formatting — operator framing, citation instruction, and the
   runbook name/source so the RCA can cite it.
"""

import os
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kubently.modules.runbooks import RunbookStore, build_runbook_context
from kubently.modules.runbooks.store import (
    FRAMING,
    TRUNCATION_NOTE,
    Runbook,
    parse_runbook,
    score_runbook,
)


def make_runbook(**overrides):
    defaults = dict(
        name="Payments CrashLoopBackOff",
        source="payments-crashloop.md",
        body="1. Check recent deploys first.\n2. Compare heap flag to memory limit.",
        alerts=("KubePodCrashLooping",),
        namespaces=("payments", "payments-*"),
        workloads=("payment-api*",),
        topics=("crashloop", "payment service"),
    )
    defaults.update(overrides)
    return Runbook(**defaults)


def write_runbook(directory, filename, name, body="Step 1. Look at events.", match_yaml=None):
    match_yaml = match_yaml if match_yaml is not None else '  alerts: ["KubePodCrashLooping"]'
    text = f"---\nname: {name}\nmatch:\n{match_yaml}\n---\n{body}\n"
    path = directory / filename
    path.write_text(text, encoding="utf-8")
    return path


# =========================================================================
# Frontmatter parsing
# =========================================================================


class TestParsing:
    def test_parses_full_frontmatter(self):
        text = textwrap.dedent(
            """\
            ---
            name: DB failover
            match:
              alerts: ["PostgresDown", "Replica*Lag"]
              namespaces: ["db"]
              workloads: ["postgres-*"]
              topics: ["failover", "replication lag"]
            ---
            # Steps
            1. Do not restart the primary.
            """
        )
        rb = parse_runbook(text, "db-failover.md")
        assert rb.name == "DB failover"
        assert rb.source == "db-failover.md"
        assert rb.alerts == ("PostgresDown", "Replica*Lag")
        assert rb.namespaces == ("db",)
        assert rb.workloads == ("postgres-*",)
        assert rb.topics == ("failover", "replication lag")
        assert rb.body.startswith("# Steps")

    def test_name_falls_back_to_filename(self):
        text = "---\nmatch:\n  topics: [dns]\n---\nCheck CoreDNS."
        rb = parse_runbook(text, "dns-issues.md")
        assert rb.name == "dns-issues"

    def test_scalar_criteria_accepted_as_single_entry(self):
        # Hand-written YAML: `alerts: KubePodCrashLooping` (no list) must work.
        text = "---\nname: X\nmatch:\n  alerts: KubePodCrashLooping\n---\nBody."
        rb = parse_runbook(text, "x.md")
        assert rb.alerts == ("KubePodCrashLooping",)

    def test_no_frontmatter_is_skipped(self):
        assert parse_runbook("Just a markdown file.", "plain.md") is None

    def test_invalid_yaml_is_skipped(self):
        assert parse_runbook("---\nname: [unclosed\n---\nBody.", "bad.md") is None

    def test_empty_body_is_skipped(self):
        assert parse_runbook("---\nname: X\nmatch:\n  topics: [a]\n---\n", "empty.md") is None

    def test_non_mapping_match_is_skipped(self):
        assert parse_runbook("---\nname: X\nmatch: [a, b]\n---\nBody.", "bad.md") is None


# =========================================================================
# Matching: alert patterns, selectors, tag relevance
# =========================================================================


class TestMatching:
    def test_alert_name_exact_match(self):
        rb = make_runbook()
        text = "Alert 'KubePodCrashLooping' is firing in cluster prod"
        assert score_runbook(rb, text) >= 100

    def test_alert_name_glob_match(self):
        rb = make_runbook(alerts=("Payments*",))
        assert score_runbook(rb, "Alert 'PaymentsHighLatency' is firing") >= 100

    def test_alert_match_is_case_insensitive(self):
        rb = make_runbook()
        assert score_runbook(rb, "alert kubepodcrashlooping fired") >= 100

    def test_namespace_selector_match(self):
        rb = make_runbook(alerts=(), workloads=(), topics=())
        assert score_runbook(rb, "pods failing in namespace payments") > 0

    def test_namespace_glob_selector(self):
        rb = make_runbook(alerts=(), workloads=(), topics=())
        assert score_runbook(rb, "pods failing in namespace payments-eu") > 0

    def test_workload_glob_matches_derived_pod_name(self):
        rb = make_runbook(alerts=(), namespaces=(), topics=())
        assert score_runbook(rb, "why is payment-api-7f9d8b5c-x2vqp restarting?") > 0

    def test_topic_tag_relevance(self):
        rb = make_runbook(alerts=(), namespaces=(), workloads=())
        assert score_runbook(rb, "the payment service seems down, some crashloop") > 0

    def test_multiword_topic_matches_as_phrase(self):
        rb = make_runbook(alerts=(), namespaces=(), workloads=(), topics=("connection refused",))
        assert score_runbook(rb, "getting connection refused from the backend") > 0
        assert score_runbook(rb, "connection was refused") == 0

    def test_no_match_scores_zero(self):
        rb = make_runbook()
        assert score_runbook(rb, "why is coredns slow in kube-system?") == 0

    def test_alert_hit_outranks_topic_pile(self):
        by_alert = make_runbook(name="A", alerts=("KubePodCrashLooping",), namespaces=(),
                                workloads=(), topics=())
        by_topics = make_runbook(name="B", alerts=(), namespaces=(), workloads=(),
                                 topics=("pod", "firing", "alert", "crash"))
        text = "Alert 'KubePodCrashLooping' is firing: pod crash"
        assert score_runbook(by_alert, text) > score_runbook(by_topics, text)


class TestStoreSelect:
    def test_select_orders_best_match_first(self, tmp_path):
        write_runbook(tmp_path, "generic.md", "Generic crashloop",
                      match_yaml='  topics: ["crashloop"]')
        write_runbook(tmp_path, "specific.md", "Payments crashloop",
                      match_yaml='  alerts: ["KubePodCrashLooping"]\n  topics: ["crashloop"]')
        store = RunbookStore(directory=str(tmp_path), reload_seconds=0)
        matches = store.select("Alert 'KubePodCrashLooping' is firing: crashloop in payments")
        assert [r.name for r in matches] == ["Payments crashloop", "Generic crashloop"]

    def test_select_empty_text_matches_nothing(self, tmp_path):
        write_runbook(tmp_path, "a.md", "A")
        store = RunbookStore(directory=str(tmp_path), reload_seconds=0)
        assert store.select("") == []
        assert store.select("   ") == []

    def test_select_unrelated_text_matches_nothing(self, tmp_path):
        write_runbook(tmp_path, "a.md", "A")
        store = RunbookStore(directory=str(tmp_path), reload_seconds=0)
        assert store.select("how many nodes does the cluster have?") == []


# =========================================================================
# Size capping
# =========================================================================


class TestSizeCapping:
    def test_prefers_best_match_over_concatenating_everything(self):
        best = make_runbook(name="Best", body="B" * 3000)
        second = make_runbook(name="Second", source="second.md", body="S" * 3000)
        context = build_runbook_context([best, second], max_chars=4000)
        assert "Runbook: Best" in context
        assert "Runbook: Second" not in context
        assert len(context) <= 4000

    def test_multiple_runbooks_fit_when_budget_allows(self):
        a = make_runbook(name="A", body="a" * 100)
        b = make_runbook(name="B", source="b.md", body="b" * 100)
        context = build_runbook_context([a, b], max_chars=8000)
        assert "Runbook: A" in context and "Runbook: B" in context

    def test_oversized_best_match_is_truncated_not_dropped(self):
        big = make_runbook(name="Huge", body="X" * 20000)
        context = build_runbook_context([big], max_chars=2000)
        assert context is not None
        assert len(context) <= 2000
        assert "Runbook: Huge" in context
        assert context.endswith(TRUNCATION_NOTE)

    def test_no_matches_yields_none(self):
        assert build_runbook_context([], max_chars=8000) is None

    def test_store_env_cap_is_respected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KUBENTLY_RUNBOOKS_MAX_CHARS", "1500")
        write_runbook(tmp_path, "big.md", "Big", body="Z" * 5000)
        store = RunbookStore(directory=str(tmp_path), reload_seconds=0)
        context = store.build_context("Alert 'KubePodCrashLooping' firing")
        assert context is not None and len(context) <= 1500


# =========================================================================
# Injection formatting
# =========================================================================


class TestInjectionFormatting:
    def test_context_carries_operator_framing(self):
        context = build_runbook_context([make_runbook()])
        assert context.startswith(FRAMING)
        assert "operator's documented procedure" in context
        assert "note the deviation" in context

    def test_context_instructs_citation_by_name(self):
        context = build_runbook_context([make_runbook()])
        assert "cite it by name" in context

    def test_block_names_runbook_and_source_file(self):
        context = build_runbook_context([make_runbook()])
        assert "--- Runbook: Payments CrashLoopBackOff (from payments-crashloop.md) ---" in context
        assert "Compare heap flag" in context


# =========================================================================
# Store loading and reload
# =========================================================================


class TestStoreLoading:
    def test_missing_directory_is_empty_store(self, tmp_path):
        store = RunbookStore(directory=str(tmp_path / "nope"), reload_seconds=0)
        assert store.runbooks == []
        assert store.select("Alert 'KubePodCrashLooping' firing") == []

    def test_bad_file_does_not_break_good_files(self, tmp_path):
        (tmp_path / "broken.md").write_text("no frontmatter here", encoding="utf-8")
        write_runbook(tmp_path, "good.md", "Good")
        store = RunbookStore(directory=str(tmp_path), reload_seconds=0)
        assert [r.name for r in store.runbooks] == ["Good"]

    def test_non_markdown_files_ignored(self, tmp_path):
        (tmp_path / "notes.txt").write_text("---\nname: X\n---\nBody", encoding="utf-8")
        store = RunbookStore(directory=str(tmp_path), reload_seconds=0)
        assert store.runbooks == []

    def test_reload_picks_up_new_file(self, tmp_path):
        write_runbook(tmp_path, "a.md", "A")
        store = RunbookStore(directory=str(tmp_path), reload_seconds=0)
        assert len(store.runbooks) == 1
        write_runbook(tmp_path, "b.md", "B")
        assert sorted(r.name for r in store.runbooks) == ["A", "B"]

    def test_reload_interval_throttles_rescan(self, tmp_path):
        write_runbook(tmp_path, "a.md", "A")
        store = RunbookStore(directory=str(tmp_path), reload_seconds=3600)
        write_runbook(tmp_path, "b.md", "B")
        # Within the interval the old snapshot is served.
        assert [r.name for r in store.runbooks] == ["A"]

    def test_env_var_directory(self, tmp_path, monkeypatch):
        write_runbook(tmp_path, "a.md", "A")
        monkeypatch.setenv("KUBENTLY_RUNBOOKS_DIR", str(tmp_path))
        store = RunbookStore(reload_seconds=0)
        assert [r.name for r in store.runbooks] == ["A"]


# =========================================================================
# Agent-side helpers
# =========================================================================


def _load_user_message_text():
    """Import just the helper without pulling in the heavy a2a/langchain stack
    (same extraction convention as test_thread_namespacing)."""
    from pathlib import Path

    path = (Path(__file__).parent.parent / "kubently/modules/a2a/protocol_bindings"
            / "a2a_server/agent.py")
    src = path.read_text()
    start = src.index("def _user_message_text")
    end = src.index("def _namespaced_thread_id")
    ns: dict = {}
    exec(src[start:end], ns)
    return ns["_user_message_text"]


class TestUserMessageText:
    def test_extracts_plain_and_multipart_user_text(self):
        _user_message_text = _load_user_message_text()

        messages = [
            {"role": "user", "content": "Alert 'KubePodCrashLooping' firing"},
            {"role": "assistant", "content": "not this"},
            {"role": "user", "content": [{"type": "text", "text": "in payments"},
                                         {"type": "image", "url": "ignored"}]},
        ]
        text = _user_message_text(messages)
        assert "KubePodCrashLooping" in text
        assert "in payments" in text
        assert "not this" not in text
