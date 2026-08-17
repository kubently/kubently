#!/usr/bin/env python3
"""Unit tests for the deployment verification webhook."""

import asyncio
import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kubently.modules.webhook import verify_deployment as vd
from kubently.modules.webhook.verify_deployment import (
    DONE,
    FAILED,
    PENDING,
    SETTLE_COMPLETE,
    SETTLE_FAILED,
    SETTLE_TIMEOUT,
    VerifyRequest,
    build_query,
    classify_rollout,
    create_router,
    format_slack_message,
    parse_request,
    parse_watch_output,
)

# --- trigger parsing --------------------------------------------------------


def test_parse_minimal_request():
    req = parse_request({"cluster": "prod-east", "workload": "checkout-api"})
    assert req.cluster == "prod-east"
    assert req.workload == "checkout-api"
    assert req.kind == "deployment"
    assert req.namespace == "default"
    assert req.dry_run is False


def test_parse_kind_prefix_forms():
    assert parse_request({"cluster": "c", "workload": "deploy/api"}).kind == "deployment"
    assert parse_request({"cluster": "c", "workload": "deployment/api"}).kind == "deployment"
    assert parse_request({"cluster": "c", "workload": "sts/db"}).kind == "statefulset"
    assert parse_request({"cluster": "c", "workload": "ds/agent"}).kind == "daemonset"
    req = parse_request({"cluster": "c", "workload": "statefulsets/db"})
    assert (req.kind, req.workload) == ("statefulset", "db")


def test_parse_explicit_kind_field():
    req = parse_request({"cluster": "c", "workload": "db", "kind": "statefulset"})
    assert req.kind == "statefulset"


def test_parse_kind_contradiction_rejected():
    with pytest.raises(ValueError, match="contradicts"):
        parse_request({"cluster": "c", "workload": "deploy/api", "kind": "daemonset"})


def test_parse_agreeing_kind_and_prefix_ok():
    req = parse_request({"cluster": "c", "workload": "deploy/api", "kind": "deployment"})
    assert req.workload == "api"


def test_parse_missing_cluster():
    with pytest.raises(ValueError, match="'cluster' is required"):
        parse_request({"workload": "api"})


def test_parse_missing_workload():
    with pytest.raises(ValueError, match="'workload' is required"):
        parse_request({"cluster": "c"})


def test_parse_bad_kind():
    with pytest.raises(ValueError, match="'kind' must be one of"):
        parse_request({"cluster": "c", "workload": "cronjob/x"})


def test_parse_rejects_shell_metacharacters():
    """Workload/namespace go into kubectl args; only DNS-1123 names pass."""
    for bad in ("api;rm -rf /", "api pod", "API", "a/b/c", "-api"):
        with pytest.raises(ValueError):
            parse_request({"cluster": "c", "workload": bad})
    with pytest.raises(ValueError, match="namespace"):
        parse_request({"cluster": "c", "workload": "api", "namespace": "ns$(x)"})
    with pytest.raises(ValueError, match="cluster"):
        parse_request({"cluster": "c luster", "workload": "api"})


def test_parse_timeout_clamped():
    lo = parse_request({"cluster": "c", "workload": "api", "timeout_seconds": 5})
    hi = parse_request({"cluster": "c", "workload": "api", "timeout_seconds": 999999})
    assert lo.timeout_seconds == vd.MIN_TIMEOUT_SECONDS
    assert hi.timeout_seconds == vd.MAX_TIMEOUT_SECONDS
    with pytest.raises(ValueError, match="timeout_seconds"):
        parse_request({"cluster": "c", "workload": "api", "timeout_seconds": "soon"})


def test_parse_context_truncated():
    req = parse_request({"cluster": "c", "workload": "api", "context": "x" * 500})
    assert len(req.context) == vd.MAX_CONTEXT_CHARS


def test_parse_non_dict_body():
    with pytest.raises(ValueError, match="JSON object"):
        parse_request(["nope"])


# --- rollout classification -------------------------------------------------


def test_classify_rollout_done():
    assert classify_rollout('deployment "api" successfully rolled out', None) == DONE


def test_classify_rollout_pending():
    out = 'Waiting for deployment "api" rollout to finish: 1 of 3 updated replicas are available...'
    assert classify_rollout(out, None) == PENDING
    assert classify_rollout("", None) == PENDING


def test_classify_rollout_failed():
    assert classify_rollout(None, 'deployments.apps "api" not found') == FAILED
    assert classify_rollout('error: deployment "api" exceeded its progress deadline', None) == FAILED


def test_classify_transport_error_is_pending():
    """A single executor blip must not end the settle-watch."""
    assert classify_rollout(None, "HTTP 408: timeout waiting for executor") == PENDING


# --- settle watch -----------------------------------------------------------


def _req(**kw):
    defaults = dict(cluster="c", workload="api", timeout_seconds=600)
    defaults.update(kw)
    return VerifyRequest(**defaults)


def _run_settle(monkeypatch, observations, timeout_seconds=600):
    """Drive wait_for_settle over a scripted sequence of (output, error)."""
    monkeypatch.setenv("API_KEYS", "test-service:test-key")
    seq = iter(observations)

    async def fake_status(req, api_url, api_key):
        try:
            return next(seq)
        except StopIteration:
            return "", None  # pending forever

    monkeypatch.setattr(vd, "_rollout_status_once", fake_status)
    return asyncio.run(vd.wait_for_settle(_req(timeout_seconds=timeout_seconds), poll_seconds=0))


def test_settle_completes(monkeypatch):
    outcome, last = _run_settle(
        monkeypatch,
        [
            ("Waiting for deployment rollout: 1 of 3", None),
            ('deployment "api" successfully rolled out', None),
        ],
    )
    assert outcome == SETTLE_COMPLETE
    assert "successfully rolled out" in last


def test_settle_fails_on_terminal_error(monkeypatch):
    outcome, last = _run_settle(monkeypatch, [(None, 'deployments.apps "api" not found')])
    assert outcome == SETTLE_FAILED
    assert "not found" in last


def test_settle_times_out(monkeypatch):
    outcome, _ = _run_settle(monkeypatch, [("waiting...", None)], timeout_seconds=0)
    assert outcome == SETTLE_TIMEOUT


def test_settle_tolerates_transient_errors_then_completes(monkeypatch):
    outcome, _ = _run_settle(
        monkeypatch,
        [
            (None, "HTTP 503: executor busy"),
            (None, "HTTP 503: executor busy"),
            ('deployment "api" successfully rolled out', None),
        ],
    )
    assert outcome == SETTLE_COMPLETE


def test_settle_gives_up_after_consecutive_errors(monkeypatch):
    outcome, last = _run_settle(monkeypatch, [(None, "HTTP 503: boom")] * 5)
    assert outcome == SETTLE_FAILED
    assert "could not observe rollout status" in last


# --- query + verdict interplay ----------------------------------------------


def test_build_query_mentions_target_and_verdict_contract():
    q = build_query(_req(namespace="payments", context="v1.2.3"), SETTLE_COMPLETE, "rolled out")
    assert "deployment/api in namespace payments on cluster c" in q
    assert "VERDICT: PASS" in q and "VERDICT: FAIL" in q
    assert "v1.2.3" in q
    assert "Prometheus" in q  # metrics regression step is in the brief


def test_build_query_unsettled_rollout_changes_the_brief():
    q = build_query(_req(), SETTLE_TIMEOUT, "1 of 3 available")
    assert "did NOT settle" in q
    assert "1 of 3 available" in q


def test_agent_pass_cannot_overrule_unsettled_rollout(monkeypatch):
    """PASS from 'pods look fine' while the rollout is stuck is a false all-clear."""

    async def fake_settle(req, poll_seconds=vd.POLL_SECONDS):
        return SETTLE_TIMEOUT, "stuck at 1 of 3"

    async def fake_ask(agent, query, cluster_id, conversation_id):
        return {"answer": "VERDICT: PASS\n\npods look fine", "thread_id": "t"}

    monkeypatch.setattr(vd, "wait_for_settle", fake_settle)
    from kubently.modules.mcp import tools

    monkeypatch.setattr(tools, "ask_kubently", fake_ask)
    verdict, body = asyncio.run(vd._run_verification(lambda: object(), _req()))
    assert verdict == "fail"
    assert "rollout did not settle" in body


# --- verdict formatting -----------------------------------------------------


def test_format_slack_message_pass():
    msg = format_slack_message(_req(context="v2"), "pass", "All pods Ready.")
    assert msg["text"].startswith(":white_check_mark: *Deploy verification PASS*")
    assert "`deployment/api`" in msg["text"]
    assert "(v2)" in msg["text"]
    assert "All pods Ready." in msg["text"]


def test_format_slack_message_fail_normalises_mrkdwn():
    msg = format_slack_message(_req(), "fail", "**Root cause:** bad image")
    assert msg["text"].startswith(":x: *Deploy verification FAIL*")
    assert "**" not in msg["text"]


def test_format_slack_message_unknown_is_warning():
    msg = format_slack_message(_req(), "unknown", "no marker")
    assert msg["text"].startswith(":warning: *Deploy verification UNKNOWN*")


# --- endpoint ---------------------------------------------------------------


def _client(monkeypatch, verdict="pass", body="ok", posts=None):
    async def fake_run_verification(agent_factory, req):
        return verdict, body

    async def fake_verify_and_post(agent_factory, req, slack_url):
        if posts is not None:
            posts.append((slack_url, req))

    monkeypatch.setattr(vd, "_run_verification", fake_run_verification)
    monkeypatch.setattr(vd, "_verify_and_post", fake_verify_and_post)
    monkeypatch.delenv("KUBENTLY_VERIFY_WATCH_SECONDS", raising=False)

    app = FastAPI()
    app.include_router(create_router(lambda: True))
    return TestClient(app)


def test_endpoint_acks_202_and_schedules_post(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.test/x")
    posts = []
    client = _client(monkeypatch, posts=posts)
    resp = client.post(
        "/webhooks/verify-deployment",
        json={"cluster": "prod", "workload": "deploy/api", "namespace": "shop"},
    )
    assert resp.status_code == 202
    assert resp.json()["workload"] == "deployment/api"
    assert posts and posts[0][0] == "https://hooks.slack.test/x"
    assert posts[0][1].namespace == "shop"


def test_endpoint_requires_slack_url(monkeypatch):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    client = _client(monkeypatch)
    resp = client.post("/webhooks/verify-deployment", json={"cluster": "c", "workload": "api"})
    assert resp.status_code == 503


def test_endpoint_rejects_bad_payload(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.test/x")
    client = _client(monkeypatch)
    resp = client.post("/webhooks/verify-deployment", json={"workload": "api"})
    assert resp.status_code == 400
    assert "cluster" in resp.json()["detail"]


def test_endpoint_dry_run_returns_verdict_and_posts_nothing(monkeypatch):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    posts = []
    client = _client(monkeypatch, verdict="fail", body="pod down", posts=posts)
    resp = client.post(
        "/webhooks/verify-deployment",
        json={"cluster": "c", "workload": "api", "dry_run": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["dryRun"] is True
    assert data["verdict"] == "fail"
    assert data["answer"] == "pod down"
    assert posts == []


# --- deploy watch -----------------------------------------------------------


def test_parse_watch_output():
    out = "shop api 4\nshop worker 2\n\nbad line here extra\npayments db notanint\n"
    assert parse_watch_output(out) == [("shop", "api", 4), ("shop", "worker", 2)]
    assert parse_watch_output("") == []


def test_watch_fires_only_on_generation_change(monkeypatch):
    """First sweep records baselines (no storm on label-enable); a bumped
    generation on a later sweep triggers exactly one verification."""
    monkeypatch.setenv("API_KEYS", "test-service:test-key")
    sweeps = [
        [("shop", "api", 4)],  # baseline
        [("shop", "api", 4)],  # unchanged
        [("shop", "api", 5)],  # deploy happened
    ]
    calls = {"n": 0}
    done = asyncio.Event()

    async def fake_sweep(cluster, kind, api_url, api_key):
        if kind != "deployment":
            return []
        if calls["n"] >= len(sweeps):
            done.set()
            return sweeps[-1]
        result = sweeps[calls["n"]]
        calls["n"] += 1
        return result

    async def fake_clusters(redis_client):
        return ["prod"]

    monkeypatch.setattr(vd, "_sweep_cluster", fake_sweep)
    monkeypatch.setattr(vd, "_watch_clusters", fake_clusters)

    spawned = []

    async def run():
        task = asyncio.create_task(
            vd._watch_loop(lambda: object(), None, 0, spawned.append)
        )
        await asyncio.wait_for(done.wait(), timeout=5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())
    assert len(spawned) == 1
    req = spawned[0]
    assert (req.cluster, req.kind, req.namespace, req.workload) == (
        "prod",
        "deployment",
        "shop",
        "api",
    )


def test_watch_not_started_without_config(monkeypatch):
    monkeypatch.delenv("KUBENTLY_VERIFY_WATCH_SECONDS", raising=False)
    assert vd.start_annotation_watch(lambda: object(), None) is None


def test_watch_not_started_without_slack(monkeypatch):
    monkeypatch.setenv("KUBENTLY_VERIFY_WATCH_SECONDS", "60")
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    assert vd.start_annotation_watch(lambda: object(), None) is None
