#!/usr/bin/env python3
"""Unit tests for the scheduled fleet health digest endpoint."""

import os
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kubently.modules.webhook import fleet_report
from kubently.modules.webhook.fleet_report import (
    FALLBACK_QUERY,
    create_router,
    format_slack_message,
    resolve_query,
    to_slack_mrkdwn,
)

CHART_PROMPT = os.path.join(os.path.dirname(__file__), "..", "prompts", "fleet_report.prompt.yaml")


def _client(monkeypatch, answer="prod-east: healthy", slack_calls=None):
    """App with the agent + Slack post stubbed out (no LLM, no network)."""

    async def fake_run_digest(agent_factory, query):
        return f"{answer} [q={query[:30]}]"

    async def fake_digest_and_post(agent_factory, query, slack_url):
        result = await fake_run_digest(agent_factory, query)
        if slack_calls is not None:
            slack_calls.append((slack_url, result))

    monkeypatch.setattr(fleet_report, "_run_digest", fake_run_digest)
    monkeypatch.setattr(fleet_report, "_digest_and_post", fake_digest_and_post)

    app = FastAPI()
    app.include_router(create_router(lambda: True))
    return TestClient(app)


# --- query resolution -------------------------------------------------------


def test_resolve_query_body_wins(monkeypatch):
    monkeypatch.setenv("KUBENTLY_FLEET_REPORT_PROMPT_FILE", CHART_PROMPT)
    assert resolve_query("just list crashing pods") == "just list crashing pods"


def test_resolve_query_blank_body_falls_through(monkeypatch):
    monkeypatch.setenv("KUBENTLY_FLEET_REPORT_PROMPT_FILE", CHART_PROMPT)
    q = resolve_query("   ")
    assert "Check the health of every registered cluster" in q


def test_resolve_query_reads_prompt_file(monkeypatch):
    monkeypatch.setenv("KUBENTLY_FLEET_REPORT_PROMPT_FILE", CHART_PROMPT)
    q = resolve_query(None)
    assert "one short section per cluster" in q
    assert q != FALLBACK_QUERY


def test_resolve_query_falls_back_when_no_file(monkeypatch, tmp_path):
    # Point every lookup path at nothing: get_prompt returns DEFAULT_PROMPT,
    # which is a system prompt, not a digest query.
    monkeypatch.setenv("KUBENTLY_FLEET_REPORT_PROMPT_FILE", str(tmp_path / "missing.yaml"))
    monkeypatch.setenv("KUBENTLY_PROMPT_FILE", str(tmp_path / "missing.yaml"))
    monkeypatch.chdir(tmp_path)
    assert resolve_query(None) == FALLBACK_QUERY


# --- Slack payload ----------------------------------------------------------


def test_format_slack_message():
    msg = format_slack_message("prod-east: healthy")
    assert msg["text"].startswith(":satellite: *Kubently fleet health digest*")
    assert "prod-east: healthy" in msg["text"]


# Verbatim from a real digest run — Slack renders every one of these as literal
# characters, so this is what the channel would actually have shown.
E2E_ANSWER = """---

**kind**

:warning: `digest-test/pod/broken-payments` — not ready, `CrashLoopBackOff`

---

**kind-exec-only** — healthy

See [the runbook](https://example.com/runbook) for next steps.
"""


def test_to_slack_mrkdwn_fixes_real_digest_output():
    out = to_slack_mrkdwn(E2E_ANSWER)
    assert "**" not in out, "double-asterisk bold renders literally in Slack"
    assert "*kind*" in out and "*kind-exec-only* — healthy" in out
    assert not any(line.strip() in ("---", "===") for line in out.splitlines())
    assert "<https://example.com/runbook|the runbook>" in out
    assert "[the runbook]" not in out
    # Removing a rule must not eat the blank line it sat between: that blank
    # line IS the section separator once the rule is gone.
    assert "\n\n*kind-exec-only*" in out, "cluster sections got glued together"


def test_to_slack_mrkdwn_converts_headings():
    assert to_slack_mrkdwn("## Fleet status") == "*Fleet status*"


# Verbatim from a real Alertmanager diagnosis posted to Slack. The `#` lines are
# shell comments, not headings: rewriting them to *bold* handed the reader
# commands that break when pasted, and the ```bash hint rendered as content.
E2E_DIAGNOSIS = """*Investigate:*
```bash
# Check if a postgres service/pod exists in the payments namespace
kubectl get svc,pod -n payments -l app=postgres
# Check DB connection config (env vars / secrets on the deployment)
kubectl describe deployment checkout-api -n payments
```
"""


def test_to_slack_mrkdwn_leaves_code_blocks_alone():
    out = to_slack_mrkdwn(E2E_DIAGNOSIS)
    assert "# Check if a postgres service/pod exists" in out, "shell comment was mangled"
    assert "# Check DB connection config" in out
    assert "*Check if" not in out, "heading rule fired inside a fenced block"
    assert "```bash" not in out, "Slack renders the language hint as content"
    assert "```\n# Check if" in out


def test_to_slack_mrkdwn_still_fixes_prose_around_code_blocks():
    """Protecting fences must not stop the prose either side being normalised."""
    out = to_slack_mrkdwn("**Root Cause:** boom\n\n```sh\n# keep me\n```\n\n## Next steps")
    assert "*Root Cause:*" in out and "**" not in out
    assert "# keep me" in out
    assert "*Next steps*" in out


def test_to_slack_mrkdwn_leaves_valid_mrkdwn_alone():
    """Must not mangle output that was already correct."""
    good = "*prod-east*\n• `ns/pod/api` — `CrashLoopBackOff`, 5 restarts\n\n*prod-west* — healthy"
    assert to_slack_mrkdwn(good) == good


# --- endpoint ---------------------------------------------------------------


def test_scheduled_post_acks_202_and_schedules_slack_post(monkeypatch):
    """202 without awaiting, and the Slack post really does get scheduled.

    The second assertion is what makes the dry-run test below meaningful: it
    proves this harness can observe a post at all, so `calls == []` there is
    evidence of dry-run behaviour and not just an inert background task.
    """
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.test/x")
    calls = []
    client = _client(monkeypatch, slack_calls=calls)
    resp = client.post("/webhooks/fleet-report")
    assert resp.status_code == 202
    assert resp.json() == {"accepted": True}
    assert calls and calls[0][0] == "https://hooks.slack.test/x"


def test_scheduled_post_requires_slack_url(monkeypatch):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    client = _client(monkeypatch)
    assert client.post("/webhooks/fleet-report").status_code == 503


def test_dry_run_returns_answer_and_posts_nothing(monkeypatch):
    """The one bug in this feature that would burn a real Slack channel."""
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.test/x")
    calls = []
    client = _client(monkeypatch, slack_calls=calls)
    resp = client.post("/webhooks/fleet-report", json={"dry_run": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["dryRun"] is True
    assert "prod-east: healthy" in body["answer"]
    assert calls == []


def test_dry_run_works_without_slack_url(monkeypatch):
    """A dry run never posts, so it must not require Slack to be configured."""
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    client = _client(monkeypatch)
    assert client.post("/webhooks/fleet-report", json={"dry_run": True}).status_code == 200


def test_dry_run_uses_body_query(monkeypatch):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    client = _client(monkeypatch)
    resp = client.post(
        "/webhooks/fleet-report", json={"dry_run": True, "query": "only cert expiry"}
    )
    assert "only cert expiry" in resp.json()["query"]


def test_non_object_body_rejected(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.test/x")
    client = _client(monkeypatch)
    assert client.post("/webhooks/fleet-report", json=["nope"]).status_code == 400
