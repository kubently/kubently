"""Shared verdict plumbing for proactive investigations.

Deployment verifications and scheduled checks both end the same way: the agent's
free-text answer has to become a machine-readable pass/fail so the caller can
decide whether (and how loudly) to post to Slack. The contract is a marker line
the prompt asks for — `VERDICT: PASS` or `VERDICT: FAIL` first — and a parser
that treats anything else as "unknown". Unknown is deliberately not pass:
a verdict we cannot read must never suppress a notification.
"""

from __future__ import annotations

import re

PASS = "pass"
FAIL = "fail"
UNKNOWN = "unknown"

# Appended to every proactive prompt. Kept in one place so the two features
# cannot drift into different marker dialects.
VERDICT_INSTRUCTIONS = (
    "Begin your reply with exactly one line reading `VERDICT: PASS` or "
    "`VERDICT: FAIL` (nothing else on that line), then a blank line, then your "
    "findings. PASS means everything you checked is healthy. FAIL means you "
    "found a problem or could not complete the investigation — when in doubt, "
    "FAIL. After the verdict line, show your evidence: what you checked and "
    "what you saw, one line per finding."
)

# Same Slack-formatting contract the alertmanager prompt uses; prompt compliance
# is probabilistic, so to_slack_mrkdwn still normalises the answer on the way out.
SLACK_MRKDWN_INSTRUCTIONS = (
    "Be concise; this will be posted to Slack. Format as Slack mrkdwn, which "
    "is NOT markdown: bold is *single asterisk* (never **double**), there are "
    "no `#` headings, no `---` rules and no tables. Code fences take no "
    "language hint — use ``` alone, not ```bash."
)

# The marker line, tolerantly: optional leading bullet/bold decoration, any
# case, optional trailing punctuation. Models decorate despite instructions,
# and a decorated marker is still a readable verdict.
_MARKER = re.compile(
    r"^\s*[*_`•\-]*\s*verdict\s*[:\-]\s*[*_`]*\s*(pass|fail)(?:ed)?\s*[*_`.!]*\s*$",
    re.IGNORECASE,
)

# How many leading lines to scan for the marker. The instruction says "first
# line", but a preamble sentence before it is a common failure mode and the
# marker is unambiguous wherever it appears near the top.
_SCAN_LINES = 5


def parse_verdict(answer: str) -> tuple[str, str]:
    """Extract (verdict, body) from an agent answer.

    Returns one of PASS/FAIL/UNKNOWN plus the answer with the marker line
    removed (verbatim answer when no marker was found). UNKNOWN is the
    fail-safe: the caller should treat it like a failure for posting purposes.
    """
    lines = (answer or "").splitlines()
    for i, line in enumerate(lines[:_SCAN_LINES]):
        m = _MARKER.match(line)
        if m:
            body = "\n".join(lines[:i] + lines[i + 1 :]).strip()
            return (PASS if m.group(1).lower() == "pass" else FAIL), body
    return UNKNOWN, (answer or "").strip()


def verdict_emoji(verdict: str) -> str:
    return {PASS: ":white_check_mark:", FAIL: ":x:"}.get(verdict, ":warning:")


def verdict_label(verdict: str) -> str:
    return {PASS: "PASS", FAIL: "FAIL"}.get(verdict, "UNKNOWN")
