# Proactive Diagnosis (Roadmap Phase 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Alertmanager fires a webhook at Kubently; Kubently diagnoses the alert with its own agent and posts the result to a Slack incoming webhook — "the bot diagnosed the alert before I opened my laptop."

**Architecture:** New black-box module `kubently/modules/webhook/` with an APIRouter. `POST /webhooks/alertmanager` (X-API-Key auth via existing `verify_api_key`) returns 202 immediately and diagnoses in a background task, reusing the exact agent entry point the MCP module uses (`kubently.modules.mcp.tools.ask_kubently` with a lazily-built `KubentlyAgent`). The answer is POSTed to `SLACK_WEBHOOK_URL` with httpx (already a direct dependency). Pure functions (alert parsing, query building, Slack formatting) are unit-tested; the endpoint is tested with a mocked agent; live E2E uses a mock Slack pod on the kind cluster.

**Tech Stack:** FastAPI APIRouter, httpx, pytest (existing tests/ layout), Helm `api.env` passthrough for `SLACK_WEBHOOK_URL`.

**Key repo facts** (verified 2026-07-07):
- `kubently/main.py` has `verify_api_key` dependency (line ~214) used by debug endpoints; lazy-import pattern for agent-dependent features (MCP mounts only when the SDK imports).
- `kubently/modules/mcp/tools.py: ask_kubently(agent, query, cluster_id, conversation_id)` → `{"answer": md, "thread_id": id}`.
- `KubentlyAgent(redis_client=...)` built lazily in `kubently/modules/mcp/server.py`.
- Alertmanager webhook payload: `{"status": "firing"|"resolved", "alerts": [{"status", "labels": {alertname, namespace, pod, cluster?...}, "annotations": {summary?, description?}, ...}], ...}`.

---

### Task 1: Webhook module (TDD)

**Files:**
- Create: `kubently/modules/webhook/__init__.py`
- Create: `kubently/modules/webhook/alertmanager.py`
- Test: `tests/test_webhook.py`

- [ ] **Step 1: Failing tests for the pure functions**

`tests/test_webhook.py`:

```python
from kubently.modules.webhook.alertmanager import (
    build_query,
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
```

Run: `python3 -m pytest tests/test_webhook.py -q` → expect import error (module missing).

- [ ] **Step 2: Implement pure functions + router**

`kubently/modules/webhook/__init__.py`:

```python
from .alertmanager import create_router  # noqa: F401
```

`kubently/modules/webhook/alertmanager.py`:

```python
"""Alertmanager webhook -> Kubently diagnosis -> Slack incoming webhook.

Proactive mode: Alertmanager POSTs its standard webhook payload here; each firing
alert is diagnosed by the same agent that serves A2A/MCP, and the answer is posted
to SLACK_WEBHOOK_URL. The endpoint ACKs 202 immediately; diagnosis runs in the
background (it can take minutes).
"""

import asyncio
import logging
import os

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

logger = logging.getLogger(__name__)

MAX_ALERTS_PER_PAYLOAD = 3  # ponytail: cap fan-out per webhook; raise if users batch more


def firing_alerts(payload: dict) -> list[dict]:
    alerts = payload.get("alerts") if isinstance(payload, dict) else None
    if not isinstance(alerts, list):
        return []
    return [a for a in alerts if isinstance(a, dict) and a.get("status") == "firing"]


def build_query(alert: dict) -> str:
    labels = alert.get("labels", {})
    ann = alert.get("annotations", {})
    parts = [f"Alert '{labels.get('alertname', 'unknown')}' is firing"]
    if labels.get("cluster"):
        parts.append(f"in cluster {labels['cluster']}")
    if labels.get("namespace"):
        parts.append(f"in namespace {labels['namespace']}")
    if labels.get("pod"):
        parts.append(f"on pod {labels['pod']}")
    summary = ann.get("summary") or ann.get("description") or ""
    lead = " ".join(parts) + (f": {summary}" if summary else "")
    return (
        f"{lead}. Diagnose the root cause and suggest a fix. "
        "Be concise; this will be posted to Slack."
    )


def format_slack_message(alert: dict, answer: str) -> dict:
    name = alert.get("labels", {}).get("alertname", "alert")
    return {"text": f":rotating_light: *Kubently diagnosis for `{name}`*\n\n{answer}"}


async def _diagnose_and_post(agent, alert: dict, slack_url: str) -> None:
    from kubently.modules.mcp import tools

    try:
        result = await tools.ask_kubently(agent, build_query(alert), None, None)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(slack_url, json=format_slack_message(alert, result["answer"]))
            resp.raise_for_status()
        logger.info("Posted diagnosis for %s to Slack", alert.get("labels", {}).get("alertname"))
    except Exception:
        logger.exception("Proactive diagnosis failed for alert %s", alert.get("labels", {}))


def create_router(verify_api_key, redis_client=None) -> APIRouter:
    """Router factory; agent built lazily on first alert (langchain import is heavy)."""
    router = APIRouter()
    state: dict = {"agent": None}

    def _agent():
        if state["agent"] is None:
            from kubently.modules.a2a.protocol_bindings.a2a_server.agent import KubentlyAgent

            state["agent"] = KubentlyAgent(redis_client=redis_client)
        return state["agent"]

    @router.post("/webhooks/alertmanager", status_code=202)
    async def alertmanager_webhook(request: Request, auth=Depends(verify_api_key)):
        slack_url = os.environ.get("SLACK_WEBHOOK_URL")
        if not slack_url:
            raise HTTPException(503, "SLACK_WEBHOOK_URL is not configured")
        payload = await request.json()
        alerts = firing_alerts(payload)
        for alert in alerts[:MAX_ALERTS_PER_PAYLOAD]:
            asyncio.create_task(_diagnose_and_post(_agent(), alert, slack_url))
        if len(alerts) > MAX_ALERTS_PER_PAYLOAD:
            logger.warning(
                "Alertmanager payload had %d firing alerts; diagnosing first %d",
                len(alerts),
                MAX_ALERTS_PER_PAYLOAD,
            )
        return {"accepted": min(len(alerts), MAX_ALERTS_PER_PAYLOAD)}

    return router
```

Run: `python3 -m pytest tests/test_webhook.py -q` → all pass.

- [ ] **Step 3: Endpoint test with mocked agent + Slack**

Append to `tests/test_webhook.py` (follow the style of tests/test_mcp_server.py for app/dependency fakes):

```python
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kubently.modules.webhook.alertmanager import create_router


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
    # Stub the diagnosis coroutine so no agent/langchain import happens
    import kubently.modules.webhook.alertmanager as am

    calls = []

    async def fake_diag(agent, alert, url):
        calls.append(alert)

    monkeypatch.setattr(am, "_diagnose_and_post", fake_diag)
    monkeypatch.setattr(am, "KubentlyAgent", object, raising=False)
    client = TestClient(make_app(monkeypatch))
    # also stub the lazy agent builder path by pre-seeding state via a no-op:
    resp = client.post("/webhooks/alertmanager", json=PAYLOAD)
    assert resp.status_code == 202
    assert resp.json() == {"accepted": 1}
```

Note: the lazy `_agent()` still imports KubentlyAgent in the accept test — if that import
is heavy/unavailable in the unit-test env, refactor so `_agent()` is only called inside
`_diagnose_and_post` (pass a callable), keeping the endpoint import-free. Adjust to
whatever the existing test env supports; tests/test_mcp_server.py shows what's importable.

Run: `python3 -m pytest tests/test_webhook.py -q` → all pass.

- [ ] **Step 4: Commit**

```bash
git add kubently/modules/webhook tests/test_webhook.py
git commit -m "feat: alertmanager webhook -> agent diagnosis -> slack (proactive mode)"
```

---

### Task 2: Wire into main.py + Helm/docs

**Files:**
- Modify: `kubently/main.py` (include router in lifespan/startup near the MCP mount, same lazy pattern)
- Modify: `deployment/helm/kubently/values.yaml` (comment documenting `api.env.SLACK_WEBHOOK_URL`)
- Modify: `README.md` (short "Proactive diagnosis" section), `CHANGELOG.md`

- [ ] **Step 1: main.py wiring** — after the MCP mount block, add:

```python
    # Proactive mode: Alertmanager webhook -> diagnosis -> Slack (needs the agent stack).
    try:
        from kubently.modules.webhook import create_router

        app.include_router(create_router(verify_api_key, redis_client=redis_client))
        logger.info("Alertmanager webhook mounted at /webhooks/alertmanager")
    except ImportError as e:
        logger.info(f"Webhook module not mounted: {e}")
```

Place inside the same startup path where `verify_api_key` and `redis_client` are in scope; match the MCP block's error-handling style. Verify with `python3 -c "import kubently.main"` if that's how other changes are smoke-checked (or run the existing unit suite).

- [ ] **Step 2: values.yaml + docs**

In the `api.env` block of values.yaml add a comment:

```yaml
    # SLACK_WEBHOOK_URL: "https://hooks.slack.com/services/..."  # enables /webhooks/alertmanager -> Slack diagnosis
```

README section (after the MCP section):

```markdown
### Proactive diagnosis (Alertmanager → Slack)

Set `api.env.SLACK_WEBHOOK_URL` to a Slack incoming-webhook URL and point
Alertmanager at Kubently:

​```yaml
receivers:
  - name: kubently
    webhook_configs:
      - url: https://<your-kubently-host>/webhooks/alertmanager
        http_config:
          authorization:
            type: X-API-Key   # sent as the X-API-Key header
            credentials: <your-api-key>
​```

Each firing alert gets diagnosed by the agent and the result lands in Slack.
```

**Verify the Alertmanager auth shape**: Alertmanager's `http_config.authorization` sets an `Authorization: <type> <credentials>` header, NOT arbitrary headers. If `verify_api_key` only reads `X-API-Key`, either (a) note in docs that Alertmanager ≥0.25 supports `webhook_configs.http_config.headers` custom headers if available, or (b) extend `verify_api_key` usage in the router to also accept `Authorization: Bearer <key>`. Check `verify_api_key`'s implementation in main.py and pick the smallest correct option; update the README snippet to match reality.

CHANGELOG: add an entry under today's Unreleased section.

- [ ] **Step 3: Commit**

```bash
git add kubently/main.py deployment/helm/kubently/values.yaml README.md CHANGELOG.md
git commit -m "feat: mount alertmanager webhook; helm/docs for proactive mode"
```

---

### Task 3: Live E2E on kind with a mock Slack

No files (scratch pod on the existing `kind-kubently` cluster).

- [ ] **Step 1: Mock Slack sink**

```bash
kubectl --context kind-kubently -n kubently run mock-slack --image=python:3.12-alpine --restart=Never -- \
  python3 -c "
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        print('SLACK-POST:', body.decode()[:2000], flush=True)
        self.send_response(200); self.end_headers(); self.wfile.write(b'ok')
HTTPServer(('', 8080), H).serve_forever()
"
kubectl --context kind-kubently -n kubently expose pod mock-slack --port 8080
```

- [ ] **Step 2: Rebuild API image with the webhook, set env, roll**

Rebuild/load the API image the way `deployment/scripts/kind-e2e.sh` does (BUILD=true path), then:

```bash
kubectl --context kind-kubently -n kubently set env deploy/kubently-api SLACK_WEBHOOK_URL=http://mock-slack:8080
kubectl --context kind-kubently -n kubently rollout status deploy/kubently-api --timeout=150s
```

(Or run the full `ANTHROPIC_API_KEY=... ./deployment/scripts/kind-e2e.sh` then `set env` — whichever is less fiddly.)

- [ ] **Step 3: Fire a fake alert and watch the sink**

```bash
kubectl --context kind-kubently -n kubently port-forward svc/kubently-api 8080:8080 &
KEY=$(kubectl --context kind-kubently -n kubently get secret kubently-api-keys -o jsonpath='{.data.keys}' | base64 -d | head -1)
curl -s -X POST http://localhost:8080/webhooks/alertmanager \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"status":"firing","alerts":[{"status":"firing","labels":{"alertname":"TestAlwaysFiring","cluster":"kind","namespace":"kube-system"},"annotations":{"summary":"synthetic test alert for e2e"}}]}'
# expect {"accepted":1}; then within ~1-2 min:
kubectl --context kind-kubently -n kubently logs pod/mock-slack | grep -c "SLACK-POST"
```

Expected: `{"accepted": 1}` immediately; mock-slack log shows one SLACK-POST containing "Kubently diagnosis" and real diagnostic content.

- [ ] **Step 4: Cleanup**

```bash
kubectl --context kind-kubently -n kubently delete pod/mock-slack svc/mock-slack
pkill -f "port-forward.*kubently-api 8080"
```

---

### Task 4: Ship everything (single PR #37)

- [ ] Push branch; confirm PR #37 includes phase-2 + fixes + phase-3.
- [ ] Merge PR #37 (user-authorized: "fix everything in 37 and keep going").
- [ ] Confirm `release-chart.yml` run on main succeeds; `curl https://kubently.github.io/kubently/index.yaml` lists kubently 1.0.0.
- [ ] Tag `cli-v2.3.0` on the merge commit and push the tag → `publish-npm.yml` publishes; confirm `npm view @kubently/cli version` → 2.3.0.
- [ ] Final public-path check: fresh kind cluster, `npx -y @kubently/cli@2.3.0 install --yes --no-chat` with NO `--chart` (published chart + published images + published CLI).
- [ ] Cleanup, report.

## Self-Review Notes
- Alertmanager auth header shape flagged as a verification step (Task 2 Step 2) rather than assumed.
- Agent import kept lazy everywhere so the API still boots without the `a2a` extra.
- Deliberately skipped: dedup/rate-limiting of repeat alerts (`ponytail:` cap comment marks the ceiling — add Redis-keyed dedup when someone's Slack gets noisy), resolved-alert notifications, Slack blocks formatting.
