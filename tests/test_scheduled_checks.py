#!/usr/bin/env python3
"""Unit tests for the named scheduled checks endpoint + config validation."""

import asyncio
import os
import sys
import textwrap

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kubently.modules.webhook import scheduled_checks as sc
from kubently.modules.webhook.scheduled_checks import (
    Check,
    build_query,
    create_router,
    format_slack_message,
    load_checks,
    should_post,
    validate_check,
)

GOOD = {
    "name": "cert-expiry",
    "schedule": "0 8 * * 1",
    "prompt": "Check TLS secrets for certificates expiring within 21 days.",
}


# --- config validation ------------------------------------------------------


def test_validate_minimal_check():
    check = validate_check(GOOD)
    assert check.name == "cert-expiry"
    assert check.schedule == "0 8 * * 1"
    assert check.clusters == []
    assert check.notify_on_pass is False
    assert check.suspend is False


def test_validate_full_check():
    check = validate_check(
        {**GOOD, "clusters": ["prod-east", "prod-west"], "notifyOnPass": True, "suspend": True}
    )
    assert check.clusters == ["prod-east", "prod-west"]
    assert check.notify_on_pass is True
    assert check.suspend is True


def test_validate_notify_on_pass_inherits_global_default():
    assert validate_check(GOOD, default_notify_on_pass=True).notify_on_pass is True
    # explicit per-check value beats the global default
    assert (
        validate_check({**GOOD, "notifyOnPass": False}, default_notify_on_pass=True).notify_on_pass
        is False
    )


def test_validate_missing_name():
    with pytest.raises(ValueError, match="missing 'name'"):
        validate_check({k: v for k, v in GOOD.items() if k != "name"})


def test_validate_bad_names():
    for bad in ("Has-Caps", "under_score", "-leading", "x" * 40, "sp ace"):
        with pytest.raises(ValueError, match="name"):
            validate_check({**GOOD, "name": bad})


def test_validate_missing_schedule():
    with pytest.raises(ValueError, match="missing 'schedule'"):
        validate_check({k: v for k, v in GOOD.items() if k != "schedule"})


def test_validate_bad_cron():
    for bad in ("hourly", "0 8 * *", "0 8 * * 1 2026"):
        with pytest.raises(ValueError, match="cron"):
            validate_check({**GOOD, "schedule": bad})


def test_validate_missing_prompt():
    with pytest.raises(ValueError, match="missing 'prompt'"):
        validate_check({**GOOD, "prompt": "   "})


def test_validate_bad_clusters():
    with pytest.raises(ValueError, match="must be a list"):
        validate_check({**GOOD, "clusters": "prod-east"})
    with pytest.raises(ValueError, match="invalid cluster id"):
        validate_check({**GOOD, "clusters": ["ok", "bad cluster"]})


def test_validate_non_mapping_entry():
    with pytest.raises(ValueError, match="mapping"):
        validate_check(["not", "a", "dict"])


def _write_checks(tmp_path, content):
    path = tmp_path / "checks.yaml"
    path.write_text(textwrap.dedent(content))
    return str(path)


def test_load_checks_file(tmp_path):
    path = _write_checks(
        tmp_path,
        """
        notifyOnPass: false
        checks:
          - name: cert-expiry
            schedule: "0 8 * * 1"
            prompt: Check certs.
          - name: pvc-usage
            schedule: "0 */6 * * *"
            clusters: [prod-east]
            notifyOnPass: true
            prompt: Check PVC usage above 85%.
        """,
    )
    checks = load_checks(path)
    assert set(checks) == {"cert-expiry", "pvc-usage"}
    assert checks["pvc-usage"].notify_on_pass is True
    assert checks["pvc-usage"].clusters == ["prod-east"]


def test_load_checks_missing_file_is_empty(tmp_path):
    assert load_checks(str(tmp_path / "nope.yaml")) == {}


def test_load_checks_duplicate_names(tmp_path):
    path = _write_checks(
        tmp_path,
        """
        checks:
          - {name: dup, schedule: "0 8 * * 1", prompt: a}
          - {name: dup, schedule: "0 9 * * 1", prompt: b}
        """,
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_checks(path)


def test_load_checks_rejects_any_bad_entry(tmp_path):
    """All-or-nothing: a misspelled check must not silently vanish."""
    path = _write_checks(
        tmp_path,
        """
        checks:
          - {name: good, schedule: "0 8 * * 1", prompt: fine}
          - {name: bad, prompt: no schedule here}
        """,
    )
    with pytest.raises(ValueError, match="'bad'"):
        load_checks(path)


def test_load_checks_env_var(tmp_path, monkeypatch):
    path = _write_checks(tmp_path, 'checks:\n  - {name: x, schedule: "0 8 * * 1", prompt: p}\n')
    monkeypatch.setenv("KUBENTLY_CHECKS_FILE", path)
    assert set(load_checks()) == {"x"}


# --- query + noise discipline -----------------------------------------------


def _check(**kw):
    defaults = dict(name="cert-expiry", schedule="0 8 * * 1", prompt="Check certs.")
    defaults.update(kw)
    return Check(**defaults)


def test_build_query_targets_clusters():
    assert "Investigate every registered cluster." in build_query(_check())
    assert "Investigate cluster prod-east only." in build_query(_check(clusters=["prod-east"]))
    q = build_query(_check(clusters=["a", "b"]))
    assert "Investigate only these clusters: a, b." in q


def test_build_query_carries_prompt_and_verdict_contract():
    q = build_query(_check())
    assert "Check certs." in q
    assert "VERDICT: PASS" in q and "VERDICT: FAIL" in q
    assert "Slack mrkdwn" in q


def test_should_post_matrix():
    quiet = _check()
    loud = _check(notify_on_pass=True)
    assert should_post("pass", quiet) is False  # the noise discipline
    assert should_post("pass", loud) is True
    assert should_post("fail", quiet) is True
    assert should_post("fail", loud) is True
    assert should_post("unknown", quiet) is True  # unreadable verdict never mutes


def test_format_slack_message():
    msg = format_slack_message(_check(), "fail", "**3 certs** expire in 5 days")
    assert msg["text"].startswith(":x: *Scheduled check `cert-expiry`: FAIL*")
    assert "**" not in msg["text"]  # mrkdwn normalised


# --- endpoint ---------------------------------------------------------------


def _client(monkeypatch, tmp_path, verdict="pass", body="fine", posts=None):
    path = _write_checks(
        tmp_path,
        """
        checks:
          - name: cert-expiry
            schedule: "0 8 * * 1"
            prompt: Check certs.
          - name: loud-check
            schedule: "0 9 * * 1"
            notifyOnPass: true
            prompt: Check stuff loudly.
        """,
    )
    monkeypatch.setenv("KUBENTLY_CHECKS_FILE", path)

    async def fake_run_check(agent_factory, check, prompt_override=None):
        return verdict, f"{body} [prompt={(prompt_override or check.prompt)[:60]}]"

    async def fake_post_slack(slack_url, message):
        if posts is not None:
            posts.append((slack_url, message))

    monkeypatch.setattr(sc, "_run_check", fake_run_check)
    monkeypatch.setattr(sc, "_post_slack", fake_post_slack)

    app = FastAPI()
    app.include_router(create_router(lambda: True))
    return TestClient(app)


def test_endpoint_unknown_check_404(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    resp = client.post("/webhooks/scheduled-check", json={"check": "nope"})
    assert resp.status_code == 404
    assert "cert-expiry" in resp.json()["detail"]  # tells you what IS configured


def test_endpoint_missing_check_field_400(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    assert client.post("/webhooks/scheduled-check", json={}).status_code == 400


def test_endpoint_invalid_config_500(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    bad = tmp_path / "bad-checks.yaml"
    bad.write_text("checks:\n  - {name: x}\n")
    monkeypatch.setenv("KUBENTLY_CHECKS_FILE", str(bad))
    resp = client.post("/webhooks/scheduled-check", json={"check": "x"})
    assert resp.status_code == 500
    assert "invalid" in resp.json()["detail"]


def test_endpoint_pass_posts_nothing(monkeypatch, tmp_path):
    """The noise discipline, end to end: green check -> silent channel."""
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.test/x")
    posts = []
    client = _client(monkeypatch, tmp_path, verdict="pass", posts=posts)
    resp = client.post("/webhooks/scheduled-check", json={"check": "cert-expiry"})
    assert resp.status_code == 202
    assert posts == []


def test_endpoint_pass_posts_when_opted_in(monkeypatch, tmp_path):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.test/x")
    posts = []
    client = _client(monkeypatch, tmp_path, verdict="pass", posts=posts)
    resp = client.post("/webhooks/scheduled-check", json={"check": "loud-check"})
    assert resp.status_code == 202
    assert len(posts) == 1
    assert "PASS" in posts[0][1]["text"]


def test_endpoint_fail_posts_with_evidence(monkeypatch, tmp_path):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.test/x")
    posts = []
    client = _client(monkeypatch, tmp_path, verdict="fail", body="3 certs expiring", posts=posts)
    resp = client.post("/webhooks/scheduled-check", json={"check": "cert-expiry"})
    assert resp.status_code == 202
    assert len(posts) == 1
    assert "FAIL" in posts[0][1]["text"]
    assert "3 certs expiring" in posts[0][1]["text"]


def test_endpoint_unknown_verdict_posts(monkeypatch, tmp_path):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.test/x")
    posts = []
    client = _client(monkeypatch, tmp_path, verdict="unknown", posts=posts)
    client.post("/webhooks/scheduled-check", json={"check": "cert-expiry"})
    assert len(posts) == 1


def test_endpoint_requires_slack_url(monkeypatch, tmp_path):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    client = _client(monkeypatch, tmp_path)
    assert client.post("/webhooks/scheduled-check", json={"check": "cert-expiry"}).status_code == 503


def test_endpoint_dry_run_reports_would_post(monkeypatch, tmp_path):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    posts = []
    client = _client(monkeypatch, tmp_path, verdict="pass", posts=posts)
    resp = client.post("/webhooks/scheduled-check", json={"check": "cert-expiry", "dry_run": True})
    assert resp.status_code == 200
    data = resp.json()
    assert data["dryRun"] is True
    assert data["verdict"] == "pass"
    assert data["wouldPost"] is False  # quiet check: a pass would not have posted
    assert posts == []


def test_endpoint_dry_run_prompt_override(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    resp = client.post(
        "/webhooks/scheduled-check",
        json={"check": "cert-expiry", "dry_run": True, "prompt": "only check kube-system"},
    )
    assert "only check kube-system" in resp.json()["answer"]
