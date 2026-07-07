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

MAX_ALERTS_PER_PAYLOAD = 3  # ponytail: cap fan-out per webhook; add Redis-keyed dedup if Slack gets noisy


def firing_alerts(payload) -> list[dict]:
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


async def _diagnose_and_post(agent_factory, alert: dict, slack_url: str) -> None:
    from kubently.modules.mcp import tools

    try:
        result = await tools.ask_kubently(agent_factory(), build_query(alert), None, None)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(slack_url, json=format_slack_message(alert, result["answer"]))
            resp.raise_for_status()
        logger.info("Posted diagnosis for %s to Slack", alert.get("labels", {}).get("alertname"))
    except Exception:
        logger.exception("Proactive diagnosis failed for alert %s", alert.get("labels", {}))


def create_router(verify_api_key, redis_client=None) -> APIRouter:
    """Router factory. The agent (and its heavy langchain import) is only touched
    inside the background task, so the API boots and ACKs without the a2a stack."""
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
            asyncio.create_task(_diagnose_and_post(_agent, alert, slack_url))
        if len(alerts) > MAX_ALERTS_PER_PAYLOAD:
            logger.warning(
                "Alertmanager payload had %d firing alerts; diagnosing first %d",
                len(alerts),
                MAX_ALERTS_PER_PAYLOAD,
            )
        return {"accepted": min(len(alerts), MAX_ALERTS_PER_PAYLOAD)}

    return router
