#!/usr/bin/env python3
"""Unit tests for the shared proactive-verdict parsing."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kubently.modules.webhook.verdict import (
    FAIL,
    PASS,
    UNKNOWN,
    parse_verdict,
    verdict_emoji,
    verdict_label,
)


def test_parse_pass():
    verdict, body = parse_verdict("VERDICT: PASS\n\nAll 3 pods Ready, no warnings.")
    assert verdict == PASS
    assert body == "All 3 pods Ready, no warnings."
    assert "VERDICT" not in body


def test_parse_fail():
    verdict, body = parse_verdict("VERDICT: FAIL\n\n• `api-7f9` CrashLoopBackOff")
    assert verdict == FAIL
    assert "CrashLoopBackOff" in body


def test_parse_is_case_insensitive():
    assert parse_verdict("verdict: pass\nok")[0] == PASS
    assert parse_verdict("Verdict: Fail\nbad")[0] == FAIL


def test_parse_tolerates_model_decoration():
    """Models bold/bullet the marker despite instructions; still a verdict."""
    assert parse_verdict("*VERDICT: PASS*\n\nfine")[0] == PASS
    assert parse_verdict("**VERDICT: FAIL**\n\nbad")[0] == FAIL
    assert parse_verdict("- VERDICT: PASS")[0] == PASS


def test_parse_tolerates_short_preamble():
    text = "Here is my assessment.\nVERDICT: FAIL\n\npod is down"
    verdict, body = parse_verdict(text)
    assert verdict == FAIL
    assert "Here is my assessment." in body
    assert "VERDICT" not in body


def test_missing_marker_is_unknown_not_pass():
    """Fail-safe: an unreadable verdict must never suppress a notification."""
    verdict, body = parse_verdict("Everything looks great, no problems at all!")
    assert verdict == UNKNOWN
    assert body == "Everything looks great, no problems at all!"


def test_marker_deep_in_body_is_not_a_verdict():
    """A verdict quoted mid-explanation (beyond the scan window) doesn't count."""
    text = "\n".join(["line"] * 10 + ["VERDICT: PASS"])
    assert parse_verdict(text)[0] == UNKNOWN


def test_empty_answer_is_unknown():
    assert parse_verdict("")[0] == UNKNOWN
    assert parse_verdict(None)[0] == UNKNOWN


def test_prose_containing_the_word_verdict_is_not_a_marker():
    assert parse_verdict("The verdict: passing this to the team.")[0] == UNKNOWN


def test_emoji_and_label():
    assert verdict_emoji(PASS) == ":white_check_mark:"
    assert verdict_emoji(FAIL) == ":x:"
    assert verdict_emoji(UNKNOWN) == ":warning:"
    assert verdict_label(PASS) == "PASS"
    assert verdict_label(FAIL) == "FAIL"
    assert verdict_label(UNKNOWN) == "UNKNOWN"
