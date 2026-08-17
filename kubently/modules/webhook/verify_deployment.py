"""Deployment verification webhook -> rollout settle-watch -> agent verdict -> Slack.

A CI pipeline (or a human with curl) POSTs the workload it just deployed; the
endpoint ACKs 202, waits for the rollout to settle by polling
`kubectl rollout status` through the executor channel, then has the agent run a
real post-deploy investigation (pods, events, logs, metrics vs the pre-deploy
window where Prometheus is configured) and posts a PASS/FAIL verdict with the
evidence to SLACK_WEBHOOK_URL. Verifications always post — a deploy is an event
someone is actively watching, so silence is not a signal here (contrast with
scheduled checks, which stay quiet on pass).

Optionally the API can notice deploys itself: workloads labelled
`kubently.io/verify=enabled` are polled for generation changes (see
start_annotation_watch) and every observed change triggers the same verification.

Two response contracts, chosen by the caller (same split as fleet_report):
  - default: ACK 202, verification runs in the background (settle + investigation
    take minutes; nobody holds a connection that long)
  - dry_run: run synchronously, return the verdict to the caller, post nothing
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from . import verdict as verdicts
from .verdict import (
    SLACK_MRKDWN_INSTRUCTIONS,
    VERDICT_INSTRUCTIONS,
    parse_verdict,
    verdict_emoji,
    verdict_label,
)

logger = logging.getLogger(__name__)

# asyncio holds only weak references to tasks, so a fire-and-forget task can be
# garbage-collected mid-flight. A verification runs for minutes; without a strong
# ref the symptom is "the Slack message just never arrives", with nothing logged.
_background: set[asyncio.Task] = set()

KINDS = ("deployment", "statefulset", "daemonset")

# Workload/namespace names go into kubectl args and the agent prompt, so they
# are validated as DNS-1123 names rather than passed through as free text.
_DNS1123 = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
# Cluster ids are Kubently's own (executor:token:{id}); slightly wider charset.
_CLUSTER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

DEFAULT_TIMEOUT_SECONDS = 600
MIN_TIMEOUT_SECONDS = 60
MAX_TIMEOUT_SECONDS = 1800
MAX_CONTEXT_CHARS = 200

# Label (not annotation) drives the optional deploy watch: labels are the only
# metadata kubectl can select server-side, and the watch sweeps whole clusters.
WATCH_LABEL = "kubently.io/verify=enabled"


@dataclass
class VerifyRequest:
    cluster: str
    workload: str
    kind: str = "deployment"
    namespace: str = "default"
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    context: str = ""  # free text for the Slack header, e.g. an image tag
    dry_run: bool = False
    query: str = ""  # extra caller instructions appended to the investigation


def _resolve_kind(body: dict, workload: str) -> tuple[str, str]:
    """(kind, bare workload name) from the `kind` field and/or `kind/name` prefix.

    An explicit `kind` field that contradicts the prefix is an error rather
    than a silent pick.
    """
    prefix_kind = None
    if "/" in workload:
        prefix_kind, _, workload = workload.partition("/")
        # normalise the plural/abbreviated forms kubectl accepts
        prefix_kind = prefix_kind.strip().lower()
        prefix_kind = {
            "deploy": "deployment",
            "deployments": "deployment",
            "sts": "statefulset",
            "statefulsets": "statefulset",
            "ds": "daemonset",
            "daemonsets": "daemonset",
        }.get(prefix_kind, prefix_kind)

    kind = str(body.get("kind") or "").strip().lower()
    if kind and prefix_kind and kind != prefix_kind:
        raise ValueError(f"'kind' ({kind}) contradicts workload prefix ({prefix_kind})")
    kind = kind or prefix_kind or "deployment"
    if kind not in KINDS:
        raise ValueError(f"'kind' must be one of {', '.join(KINDS)}; got {kind!r}")
    return kind, workload


def parse_request(body: dict) -> VerifyRequest:
    """Validate the trigger payload. Raises ValueError with a caller-facing message.

    `workload` accepts either a bare name or a `kind/name` prefix (the form
    kubectl prints).
    """
    if not isinstance(body, dict):
        raise ValueError("Body must be a JSON object")

    cluster = str(body.get("cluster") or "").strip()
    if not cluster:
        raise ValueError("'cluster' is required")
    if not _CLUSTER_ID.match(cluster):
        raise ValueError(f"'cluster' is not a valid cluster id: {cluster!r}")

    workload = str(body.get("workload") or "").strip()
    if not workload:
        raise ValueError("'workload' is required")
    kind, workload = _resolve_kind(body, workload)

    if not _DNS1123.match(workload):
        raise ValueError(f"'workload' is not a valid resource name: {workload!r}")

    namespace = str(body.get("namespace") or "default").strip()
    if not _DNS1123.match(namespace):
        raise ValueError(f"'namespace' is not a valid namespace name: {namespace!r}")

    raw_timeout = body.get("timeout_seconds", _default_timeout())
    try:
        timeout_seconds = int(raw_timeout)
    except (TypeError, ValueError) as e:
        raise ValueError(f"'timeout_seconds' must be an integer; got {raw_timeout!r}") from e
    timeout_seconds = max(MIN_TIMEOUT_SECONDS, min(MAX_TIMEOUT_SECONDS, timeout_seconds))

    context = str(body.get("context") or "").strip()[:MAX_CONTEXT_CHARS]
    query = str(body.get("query") or "").strip()

    return VerifyRequest(
        cluster=cluster,
        workload=workload,
        kind=kind,
        namespace=namespace,
        timeout_seconds=timeout_seconds,
        context=context,
        dry_run=bool(body.get("dry_run")),
        query=query,
    )


def _default_timeout() -> int:
    try:
        return int(os.environ.get("KUBENTLY_VERIFY_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


# --- settle watch -----------------------------------------------------------
#
# `kubectl rollout status --watch=false` prints the current state and exits, so
# the executor is never blocked holding a watch (COMMAND_TIMEOUT is 30s). The
# API polls it until the rollout reports done, fails its progress deadline, or
# our own deadline passes. The agent investigation runs in every case — a
# rollout that never settled is exactly when the evidence matters — but the
# settle outcome is put in the prompt so the verdict reflects it.

SETTLE_COMPLETE = "complete"
SETTLE_FAILED = "failed"
SETTLE_TIMEOUT = "timeout"

POLL_SECONDS = 15
_MAX_CONSECUTIVE_ERRORS = 3

DONE = "done"
FAILED = "failed"
PENDING = "pending"


def classify_rollout(output: str | None, error: str | None) -> str:
    """One `rollout status --watch=false` observation -> done/failed/pending."""
    text = (output or "").strip()
    if "successfully rolled out" in text or re.search(r"roll out complete", text):
        return DONE
    lowered = f"{text} {(error or '')}".lower()
    if "not found" in lowered or "progress deadline" in lowered:
        return FAILED
    if error:
        # Transport/executor errors are retried by the caller, which stops
        # after a few in a row; a single blip is still "pending".
        return PENDING
    return PENDING


async def _rollout_status_once(req: VerifyRequest, api_url: str, api_key: str) -> tuple:
    """Returns (output, error) from one rollout-status observation."""
    payload = {
        "cluster_id": req.cluster,
        "command_type": "rollout",
        "args": ["status", f"{req.kind}/{req.workload}", "--watch=false"],
        "namespace": req.namespace,
        "timeout_seconds": 30,
    }
    async with httpx.AsyncClient(timeout=40.0) as client:
        resp = await client.post(
            f"{api_url}/debug/execute", headers={"X-Api-Key": api_key}, json=payload
        )
    if resp.status_code != 200:
        return None, f"HTTP {resp.status_code}: {resp.text[:200]}"
    result = resp.json()
    if result.get("error") or (result.get("status") and result["status"] != "success"):
        return result.get("output"), result.get("error") or f"status: {result.get('status')}"
    return result.get("output") or "", None


async def wait_for_settle(req: VerifyRequest, poll_seconds: int = POLL_SECONDS) -> tuple[str, str]:
    """Poll rollout status until it settles. Returns (outcome, last observation)."""
    from kubently.modules.auth import AuthModule

    api_url = os.getenv("KUBENTLY_API_URL", "http://localhost:8080")
    api_key = AuthModule.extract_first_api_key()

    deadline = asyncio.get_running_loop().time() + req.timeout_seconds
    consecutive_errors = 0
    last = "no rollout status observed"
    while True:
        try:
            output, error = await _rollout_status_once(req, api_url, api_key)
        except Exception as e:  # network to our own API; transient
            output, error = None, str(e)
        state = classify_rollout(output, error)
        if output or error:
            last = (output or error or "").strip()
        if state == DONE:
            return SETTLE_COMPLETE, last
        if state == FAILED:
            return SETTLE_FAILED, last
        consecutive_errors = consecutive_errors + 1 if error else 0
        if consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
            return SETTLE_FAILED, f"could not observe rollout status: {last}"
        if asyncio.get_running_loop().time() + poll_seconds > deadline:
            return SETTLE_TIMEOUT, last
        await asyncio.sleep(poll_seconds)


# --- investigation ----------------------------------------------------------


def build_query(req: VerifyRequest, settle_outcome: str, settle_detail: str) -> str:
    """The post-deploy investigation the agent actually runs."""
    target = f"{req.kind}/{req.workload} in namespace {req.namespace} on cluster {req.cluster}"
    if settle_outcome == SETTLE_COMPLETE:
        lead = f"The rollout of {target} just completed ({settle_detail})."
    elif settle_outcome == SETTLE_TIMEOUT:
        lead = (
            f"The rollout of {target} did NOT settle within {req.timeout_seconds}s. "
            f"Last observed status: {settle_detail}. Find out why it is stuck."
        )
    else:
        lead = (
            f"The rollout of {target} failed to complete. "
            f"Last observed status: {settle_detail}. Find out what went wrong."
        )
    parts = [
        lead,
        "Verify this deployment is actually healthy — do not take the rollout "
        "status at face value:",
        "1. Are all its pods Ready, with no restarts or waiting states "
        "(CrashLoopBackOff, ImagePullBackOff, CreateContainerConfigError)?",
        "2. Any Warning events for the workload or its pods since the rollout?",
        "3. Any errors, panics or stack traces in the new pods' logs? Use the "
        "log search tools if available.",
        "4. If a Prometheus tool is available, compare the workload's error "
        "rate, latency and restart metrics for the last 15 minutes against the "
        "30 minutes before the rollout; call out regressions.",
        "5. If a recent-changes tool is available, confirm what changed in "
        "this rollout and mention it in the evidence.",
        "Skip any numbered step whose tool is not available — do not guess at "
        "data you cannot fetch.",
    ]
    if req.context:
        parts.append(f"Deploy context from the caller: {req.context}")
    if req.query:
        parts.append(f"Additional caller instructions: {req.query}")
    parts.append(VERDICT_INSTRUCTIONS)
    parts.append(SLACK_MRKDWN_INSTRUCTIONS)
    return "\n\n".join(parts)


def format_slack_message(req: VerifyRequest, verdict: str, body: str) -> dict:
    from .fleet_report import to_slack_mrkdwn

    header = (
        f"{verdict_emoji(verdict)} *Deploy verification {verdict_label(verdict)}* — "
        f"`{req.kind}/{req.workload}` in `{req.namespace}` on `{req.cluster}`"
    )
    if req.context:
        header += f" ({req.context})"
    return {"text": f"{header}\n\n{to_slack_mrkdwn(body)}"}


async def _run_verification(agent_factory, req: VerifyRequest) -> tuple[str, str]:
    """Settle-watch + agent investigation. Returns (verdict, body)."""
    from kubently.modules.mcp import tools

    settle_outcome, settle_detail = await wait_for_settle(req)
    query = build_query(req, settle_outcome, settle_detail)
    result = await tools.ask_kubently(agent_factory(), query, req.cluster, None)
    verdict, body = parse_verdict(result["answer"])
    # The agent's verdict cannot overrule an unsettled rollout: a PASS from
    # "pods look fine" while the rollout is stuck would be a false all-clear.
    if settle_outcome != SETTLE_COMPLETE and verdict == verdicts.PASS:
        verdict = verdicts.FAIL
        body = f"(rollout did not settle: {settle_detail})\n\n{body}"
    return verdict, body


async def _post_slack(slack_url: str, message: dict) -> None:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(slack_url, json=message)
        resp.raise_for_status()


async def _verify_and_post(agent_factory, req: VerifyRequest, slack_url: str) -> None:
    try:
        verdict, body = await _run_verification(agent_factory, req)
        await _post_slack(slack_url, format_slack_message(req, verdict, body))
        logger.info(
            "Posted deploy verification (%s) for %s/%s to Slack",
            verdict,
            req.cluster,
            req.workload,
        )
    except Exception:
        logger.exception("Deploy verification failed for %s/%s", req.cluster, req.workload)


# --- optional deploy watch --------------------------------------------------
#
# Poll registered clusters for labelled workloads whose .metadata.generation
# moved, and fire the same verification. Generation only changes on spec
# changes (not scale-status churn), which is exactly "a deploy happened".
# First sight of a workload records a baseline and does not verify — enabling
# the label on an old deployment must not trigger a verification storm.

_WATCH_JSONPATH = (
    r'{range .items[*]}{.metadata.namespace}{" "}{.metadata.name}{" "}'
    r'{.metadata.generation}{"\n"}{end}'
)


def parse_watch_output(output: str) -> list[tuple[str, str, int]]:
    """Parse the jsonpath sweep output into (namespace, name, generation)."""
    rows = []
    for line in (output or "").splitlines():
        parts = line.split()
        if len(parts) != 3:
            continue
        ns, name, gen = parts
        try:
            rows.append((ns, name, int(gen)))
        except ValueError:
            continue
    return rows


async def _sweep_cluster(cluster: str, kind: str, api_url: str, api_key: str) -> list:
    payload = {
        "cluster_id": cluster,
        "command_type": "get",
        "args": [f"{kind}s", "-A", "-l", WATCH_LABEL, "-o", f"jsonpath={_WATCH_JSONPATH}"],
        "namespace": None,
        "timeout_seconds": 30,
    }
    async with httpx.AsyncClient(timeout=40.0) as client:
        resp = await client.post(
            f"{api_url}/debug/execute", headers={"X-Api-Key": api_key}, json=payload
        )
    if resp.status_code != 200:
        return []
    result = resp.json()
    if result.get("error") or (result.get("status") and result["status"] != "success"):
        return []
    return parse_watch_output(result.get("output") or "")


async def _watch_clusters(redis_client) -> list[str]:
    configured = [
        c.strip()
        for c in os.environ.get("KUBENTLY_VERIFY_WATCH_CLUSTERS", "").split(",")
        if c.strip()
    ]
    if configured:
        return configured
    if redis_client is None:
        return []
    keys = await redis_client.keys("executor:token:*")
    out = []
    for key in keys:
        raw = key.decode() if isinstance(key, bytes) else key
        out.append(raw.replace("executor:token:", ""))
    return sorted(out)


async def _watch_loop(agent_factory, redis_client, interval: int, spawn) -> None:
    """spawn(req) schedules a verification; injected so tests can observe it."""
    from kubently.modules.auth import AuthModule

    api_url = os.getenv("KUBENTLY_API_URL", "http://localhost:8080")
    seen: dict[tuple, int] = {}
    while True:
        try:
            api_key = AuthModule.extract_first_api_key()
            for cluster in await _watch_clusters(redis_client):
                for kind in KINDS:
                    for ns, name, gen in await _sweep_cluster(cluster, kind, api_url, api_key):
                        key = (cluster, kind, ns, name)
                        prev = seen.get(key)
                        seen[key] = gen
                        if prev is not None and gen > prev:
                            logger.info(
                                "Deploy watch: %s %s/%s on %s generation %d -> %d; verifying",
                                kind,
                                ns,
                                name,
                                cluster,
                                prev,
                                gen,
                            )
                            spawn(
                                VerifyRequest(
                                    cluster=cluster,
                                    workload=name,
                                    kind=kind,
                                    namespace=ns,
                                    timeout_seconds=_default_timeout(),
                                    context=f"detected via {WATCH_LABEL} label",
                                )
                            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Deploy watch sweep failed; retrying next interval")
        await asyncio.sleep(interval)


def start_annotation_watch(agent_factory, redis_client) -> asyncio.Task | None:
    """Start the label-driven deploy watch if configured (returns the task).

    Enabled by KUBENTLY_VERIFY_WATCH_SECONDS > 0. Requires SLACK_WEBHOOK_URL —
    a watch-triggered verification has no caller to return a dry run to.
    """
    try:
        interval = int(os.environ.get("KUBENTLY_VERIFY_WATCH_SECONDS", "0"))
    except ValueError:
        interval = 0
    if interval <= 0:
        return None
    slack_url = os.environ.get("SLACK_WEBHOOK_URL")
    if not slack_url:
        logger.warning("Deploy watch enabled but SLACK_WEBHOOK_URL is not set; watch disabled")
        return None

    def spawn(req: VerifyRequest) -> None:
        task = asyncio.create_task(_verify_and_post(agent_factory, req, slack_url))
        _background.add(task)
        task.add_done_callback(_background.discard)

    task = asyncio.create_task(_watch_loop(agent_factory, redis_client, max(interval, 15), spawn))
    _background.add(task)
    task.add_done_callback(_background.discard)
    logger.info("Deploy watch started (every %ds, label %s)", max(interval, 15), WATCH_LABEL)
    return task


# --- router -----------------------------------------------------------------


def create_router(verify_api_key, redis_client=None) -> APIRouter:
    """Router factory. The agent (and its heavy langchain import) is only touched
    once a verification actually runs, so the API boots without the a2a stack."""
    router = APIRouter()
    state: dict = {"agent": None}

    def _agent():
        if state["agent"] is None:
            from kubently.modules.a2a.protocol_bindings.a2a_server.agent import KubentlyAgent

            state["agent"] = KubentlyAgent(redis_client=redis_client)
        return state["agent"]

    start_annotation_watch(_agent, redis_client)

    @router.post("/webhooks/verify-deployment")
    async def verify_deployment(request: Request, auth=Depends(verify_api_key)):
        try:
            body = await request.json()
        except Exception:
            body = {}
        try:
            req = parse_request(body)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

        if req.dry_run:
            # Synchronous: the caller wants to read the verdict. No
            # SLACK_WEBHOOK_URL requirement — a dry run never posts.
            verdict, answer_body = await _run_verification(_agent, req)
            return {
                "dryRun": True,
                "cluster": req.cluster,
                "workload": f"{req.kind}/{req.workload}",
                "namespace": req.namespace,
                "verdict": verdict,
                "answer": answer_body,
            }

        slack_url = os.environ.get("SLACK_WEBHOOK_URL")
        if not slack_url:
            raise HTTPException(503, "SLACK_WEBHOOK_URL is not configured")
        task = asyncio.create_task(_verify_and_post(_agent, req, slack_url))
        _background.add(task)
        task.add_done_callback(_background.discard)
        return JSONResponse(
            status_code=202,
            content={
                "accepted": True,
                "cluster": req.cluster,
                "workload": f"{req.kind}/{req.workload}",
                "namespace": req.namespace,
                "timeoutSeconds": req.timeout_seconds,
            },
        )

    return router
