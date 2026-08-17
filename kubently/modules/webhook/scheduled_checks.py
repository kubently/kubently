"""Named scheduled checks: cron-triggered agent investigations -> Slack.

The chart renders `scheduledChecks.checks` (name + cron schedule + optional
target clusters + a question prompt) into a ConfigMap mounted at
KUBENTLY_CHECKS_FILE, plus one CronJob per check that POSTs
`{"check": "<name>"}` here on its schedule. The agent runs the check's prompt
as a real investigation and the result goes to SLACK_WEBHOOK_URL.

Noise discipline: a check that PASSes posts nothing by default — the digest of
"everything is still fine" belongs to the fleet report, not to N cron checks.
`notifyOnPass` (global or per-check) opts back in. A FAIL — or an answer whose
verdict cannot be parsed — always posts, with the agent's evidence trail.

Same response contracts as fleet_report:
  - scheduled: ACK 202, investigation runs in the background
  - dry_run:   run synchronously, return verdict + whether it would have posted
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field

import httpx
import yaml
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

# Strong refs for fire-and-forget background tasks (see fleet_report.py).
_background: set[asyncio.Task] = set()

DEFAULT_CHECKS_FILE = "/etc/kubently/checks/checks.yaml"

# Check names become CronJob name suffixes and Slack headers: RFC-1123 label,
# kept short so "<release>-check-<name>" stays under the Job-name limit.
_NAME = re.compile(r"^[a-z0-9]([a-z0-9-]{0,28}[a-z0-9])?$")
_CLUSTER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
# Five whitespace-separated fields. Kubernetes' CronJob controller is the real
# parser; this only catches "someone pasted a 6-field or empty schedule" at
# config load instead of at silent-CronJob-never-fires time.
_CRON = re.compile(r"^\S+\s+\S+\s+\S+\s+\S+\s+\S+$")


@dataclass
class Check:
    name: str
    schedule: str
    prompt: str
    clusters: list = field(default_factory=list)
    notify_on_pass: bool = False
    suspend: bool = False


def validate_check(raw: dict, default_notify_on_pass: bool = False) -> Check:
    """One config entry -> Check. Raises ValueError with a pointed message."""
    if not isinstance(raw, dict):
        raise ValueError(f"check entry must be a mapping, got {type(raw).__name__}")

    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("check is missing 'name'")
    if not _NAME.match(name):
        raise ValueError(
            f"check name {name!r} must be lowercase alphanumeric/hyphens, max 30 chars "
            "(it becomes part of a CronJob name)"
        )

    schedule = str(raw.get("schedule") or "").strip()
    if not schedule:
        raise ValueError(f"check {name!r} is missing 'schedule'")
    if not _CRON.match(schedule):
        raise ValueError(f"check {name!r} schedule {schedule!r} is not a 5-field cron expression")

    prompt = str(raw.get("prompt") or "").strip()
    if not prompt:
        raise ValueError(f"check {name!r} is missing 'prompt'")

    clusters = raw.get("clusters") or []
    if not isinstance(clusters, list):
        raise ValueError(f"check {name!r} 'clusters' must be a list")
    clusters = [str(c).strip() for c in clusters if str(c).strip()]
    for c in clusters:
        if not _CLUSTER_ID.match(c):
            raise ValueError(f"check {name!r} has invalid cluster id {c!r}")

    notify = raw.get("notifyOnPass", raw.get("notify_on_pass"))
    return Check(
        name=name,
        schedule=schedule,
        prompt=prompt,
        clusters=clusters,
        notify_on_pass=default_notify_on_pass if notify is None else bool(notify),
        suspend=bool(raw.get("suspend", False)),
    )


def load_checks(path: str | None = None) -> dict[str, Check]:
    """Load and validate the checks file. Raises ValueError on any bad entry.

    All-or-nothing on purpose: a half-loaded config where the misspelled check
    silently vanishes is how a "certificate expiry" check stops running without
    anyone noticing. The CronJob POST for a check that failed validation gets a
    clear error instead.
    """
    path = path or os.environ.get("KUBENTLY_CHECKS_FILE") or DEFAULT_CHECKS_FILE
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError("checks file must be a mapping with a 'checks' list")
    default_notify = bool(data.get("notifyOnPass", False))
    entries = data.get("checks") or []
    if not isinstance(entries, list):
        raise ValueError("'checks' must be a list")
    checks: dict[str, Check] = {}
    for raw in entries:
        check = validate_check(raw, default_notify_on_pass=default_notify)
        if check.name in checks:
            raise ValueError(f"duplicate check name {check.name!r}")
        checks[check.name] = check
    return checks


def build_query(check: Check) -> str:
    parts = []
    if len(check.clusters) == 1:
        parts.append(f"Investigate cluster {check.clusters[0]} only.")
    elif check.clusters:
        parts.append("Investigate only these clusters: " + ", ".join(check.clusters) + ".")
    else:
        parts.append("Investigate every registered cluster.")
    parts.append(check.prompt)
    parts.append(VERDICT_INSTRUCTIONS)
    parts.append(SLACK_MRKDWN_INSTRUCTIONS)
    return "\n\n".join(parts)


def should_post(verdict: str, check: Check) -> bool:
    """Noise discipline: quiet on PASS unless opted in; FAIL and UNKNOWN post."""
    if verdict == verdicts.PASS:
        return check.notify_on_pass
    return True


def format_slack_message(check: Check, verdict: str, body: str) -> dict:
    from .fleet_report import to_slack_mrkdwn

    header = f"{verdict_emoji(verdict)} *Scheduled check `{check.name}`: {verdict_label(verdict)}*"
    return {"text": f"{header}\n\n{to_slack_mrkdwn(body)}"}


async def _run_check(agent_factory, check: Check, prompt_override: str | None = None) -> tuple:
    """Run the investigation. Returns (verdict, body)."""
    from kubently.modules.mcp import tools

    if prompt_override:
        check = Check(
            name=check.name,
            schedule=check.schedule,
            prompt=prompt_override,
            clusters=check.clusters,
            notify_on_pass=check.notify_on_pass,
        )
    cluster_id = check.clusters[0] if len(check.clusters) == 1 else None
    result = await tools.ask_kubently(agent_factory(), build_query(check), cluster_id, None)
    return parse_verdict(result["answer"])


async def _post_slack(slack_url: str, message: dict) -> None:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(slack_url, json=message)
        resp.raise_for_status()


async def _check_and_post(agent_factory, check: Check, slack_url: str) -> None:
    try:
        verdict, body = await _run_check(agent_factory, check)
        if not should_post(verdict, check):
            logger.info("Scheduled check %s passed; not posting (noise discipline)", check.name)
            return
        await _post_slack(slack_url, format_slack_message(check, verdict, body))
        logger.info("Posted scheduled check %s (%s) to Slack", check.name, verdict)
    except Exception:
        logger.exception("Scheduled check %s failed", check.name)


def _resolve_check(name: str) -> Check:
    """Load config and find the named check, mapping failures to HTTP errors."""
    try:
        checks = load_checks()
    except ValueError as e:
        raise HTTPException(500, f"checks config is invalid: {e}") from e
    check = checks.get(name)
    if check is None:
        known = ", ".join(sorted(checks)) or "(none configured)"
        raise HTTPException(404, f"unknown check {name!r}; configured checks: {known}")
    return check


def create_router(verify_api_key, redis_client=None) -> APIRouter:
    """Router factory. The agent (and its heavy langchain import) is only touched
    once a check actually runs, so the API boots without the a2a stack."""
    router = APIRouter()
    state: dict = {"agent": None}

    def _agent():
        if state["agent"] is None:
            from kubently.modules.a2a.protocol_bindings.a2a_server.agent import KubentlyAgent

            state["agent"] = KubentlyAgent(redis_client=redis_client)
        return state["agent"]

    @router.post("/webhooks/scheduled-check")
    async def scheduled_check(request: Request, auth=Depends(verify_api_key)):
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            raise HTTPException(400, "Body must be a JSON object")

        name = str(body.get("check") or "").strip()
        if not name:
            raise HTTPException(400, "'check' is required")
        check = _resolve_check(name)

        if body.get("dry_run"):
            # Synchronous: the caller is a human iterating on a check. Never
            # posts, so SLACK_WEBHOOK_URL is not required.
            verdict, answer = await _run_check(_agent, check, body.get("prompt"))
            return {
                "dryRun": True,
                "check": check.name,
                "verdict": verdict,
                "wouldPost": should_post(verdict, check),
                "answer": answer,
            }

        slack_url = os.environ.get("SLACK_WEBHOOK_URL")
        if not slack_url:
            raise HTTPException(503, "SLACK_WEBHOOK_URL is not configured")
        task = asyncio.create_task(_check_and_post(_agent, check, slack_url))
        _background.add(task)
        task.add_done_callback(_background.discard)
        return JSONResponse(status_code=202, content={"accepted": True, "check": check.name})

    return router
