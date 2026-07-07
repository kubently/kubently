#!/usr/bin/env python3
"""Unit tests for the Alertmanager webhook module (proactive diagnosis)."""

import os
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kubently.modules.webhook.alertmanager import (  # noqa: E402
    build_query,
    create_router,
    firing_alerts,
    format_slack_message,
)

PAYLOAD = {
    "status": "firing",
    "alerts": [
        {
            "status": "firing",
            "labels": {
                "alertname": "KubePodCrashLooping",
                "namespace": "payments",
                "pod": "api-5c9",
                "cluster": "prod-east",
            },
            "annotations": {"summary": "Pod payments/api-5c9 is crash looping"},
        },
        {"status": "resolved", "labels": {"alertname": "Done"}, "annotations": {}},
    ],
}


def test_firing_alerts_filters_resolved():
    alerts = firing_alerts(PAYLOAD)
    assert len(alerts) == 1
    assert alerts[0]["labels"]["alertname"] == "KubePodCrashLooping"


def test_firing_alerts_tolerates_garbage():
    assert firing_alerts({}) == []
    assert firing_alerts({"alerts": "nope"}) == []
    assert firing_alerts(None) == []


def test_build_query_includes_key_facts():
    q = build_query(PAYLOAD["alerts"][0])
    assert "KubePodCrashLooping" in q
    assert "payments" in q
    assert "prod-east" in q
    assert "crash looping" in q


def test_build_query_minimal_alert():
    q = build_query({"labels": {"alertname": "X"}, "annotations": {}})
    assert "X" in q


def test_format_slack_message():
    msg = format_slack_message(PAYLOAD["alerts"][0], "root cause: bad image")
    assert "KubePodCrashLooping" in msg["text"]
    assert "root cause: bad image" in msg["text"]


def make_app(monkeypatch, slack_url="http://slack.test/hook"):
    if slack_url:
        monkeypatch.setenv("SLACK_WEBHOOK_URL", slack_url)
    else:
        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    app = FastAPI()
    app.include_router(create_router(verify_api_key=lambda: ("ok", None)))
    return app


def test_webhook_503_when_slack_not_configured(monkeypatch):
    client = TestClient(make_app(monkeypatch, slack_url=None))
    assert client.post("/webhooks/alertmanager", json=PAYLOAD).status_code == 503


def test_webhook_accepts_firing_alerts(monkeypatch):
    import kubently.modules.webhook.alertmanager as am

    diagnosed = []

    async def fake_diag(agent_factory, alert, url):
        diagnosed.append(alert)

    monkeypatch.setattr(am, "_diagnose_and_post", fake_diag)
    client = TestClient(make_app(monkeypatch))
    resp = client.post("/webhooks/alertmanager", json=PAYLOAD)
    assert resp.status_code == 202
    assert resp.json() == {"accepted": 1}


def test_webhook_caps_alert_fanout(monkeypatch):
    import kubently.modules.webhook.alertmanager as am

    async def fake_diag(agent_factory, alert, url):
        pass

    monkeypatch.setattr(am, "_diagnose_and_post", fake_diag)
    many = {"alerts": [PAYLOAD["alerts"][0]] * 10}
    client = TestClient(make_app(monkeypatch))
    resp = client.post("/webhooks/alertmanager", json=many)
    assert resp.json() == {"accepted": am.MAX_ALERTS_PER_PAYLOAD}
