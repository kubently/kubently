"""Scheduled fleet health digest -> Slack incoming webhook.

A CronJob (or a human with curl) POSTs here; the agent sweeps every registered
cluster via its fleet fan-out tool and the digest is posted to SLACK_WEBHOOK_URL.

Two response contracts on one endpoint, chosen by the caller:
  - scheduled: ACK 202 immediately, diagnosis runs in the background (a sweep
    takes minutes; a CronJob holding the connection that long would time out)
  - dry_run:   run synchronously, return the digest to the caller, post nothing
"""

import asyncio
import logging
import os

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# asyncio holds only weak references to tasks, so a fire-and-forget task can be
# garbage-collected mid-flight. A digest runs for minutes; without a strong ref
# the symptom is "the Slack message just never arrives", with nothing in the log.
_background: set[asyncio.Task] = set()

# Used only if the prompt file is missing/unparseable; get_prompt falls back to
# its own generic system prompt otherwise, which is not a digest query.
FALLBACK_QUERY = (
    "Check the health of every registered cluster. Report pods that are not "
    "Running or Succeeded and recent Warning events. Format for Slack: one short "
    "section per cluster, healthy clusters get a single line. If the whole fleet "
    "is healthy, say so in one line and stop."
)


def resolve_query(body_query: str | None = None) -> str:
    """Digest query, most specific source first.

    body `query` (per-call, for iterating) > fleet_report.prompt.yaml (mounted by
    the chart, populated from fleetReport.query) > built-in fallback.
    """
    if body_query and body_query.strip():
        return body_query.strip()
    try:
        from kubently.modules.config import get_prompt
        from kubently.modules.config.prompts import DEFAULT_PROMPT

        prompt = get_prompt(role="fleet_report", default_filename="fleet_report.prompt.yaml")
    except Exception:
        logger.exception("Fleet report prompt load failed; using built-in query")
        return FALLBACK_QUERY
    # get_prompt returns DEFAULT_PROMPT when it finds no file. That is a generic
    # system prompt, not a digest query, so treat it as "no prompt configured".
    if not prompt or prompt == DEFAULT_PROMPT:
        logger.warning("No fleet_report prompt file found; using built-in digest query")
        return FALLBACK_QUERY
    return prompt


def format_slack_message(answer: str) -> dict:
    return {"text": f":satellite: *Kubently fleet health digest*\n\n{answer}"}


async def _run_digest(agent_factory, query: str) -> str:
    from kubently.modules.mcp import tools

    result = await tools.ask_kubently(agent_factory(), query, None, None)
    return result["answer"]


async def _digest_and_post(agent_factory, query: str, slack_url: str) -> None:
    try:
        answer = await _run_digest(agent_factory, query)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(slack_url, json=format_slack_message(answer))
            resp.raise_for_status()
        logger.info("Posted fleet health digest to Slack")
    except Exception:
        logger.exception("Fleet health digest failed")


def create_router(verify_api_key, redis_client=None) -> APIRouter:
    """Router factory. The agent (and its heavy langchain import) is only touched
    once a digest actually runs, so the API boots without the a2a stack."""
    router = APIRouter()
    state: dict = {"agent": None}

    def _agent():
        if state["agent"] is None:
            from kubently.modules.a2a.protocol_bindings.a2a_server.agent import KubentlyAgent

            state["agent"] = KubentlyAgent(redis_client=redis_client)
        return state["agent"]

    @router.post("/webhooks/fleet-report")
    async def fleet_report(request: Request, auth=Depends(verify_api_key)):
        try:
            body = await request.json()
        except Exception:  # no body at all is the normal scheduled case
            body = {}
        if not isinstance(body, dict):
            raise HTTPException(400, "Body must be a JSON object")

        query = resolve_query(body.get("query"))

        if body.get("dry_run"):
            # Synchronous: the caller is a human who wants to read the digest.
            # No SLACK_WEBHOOK_URL requirement — a dry run never posts.
            answer = await _run_digest(_agent, query)
            return {"dryRun": True, "query": query, "answer": answer}

        slack_url = os.environ.get("SLACK_WEBHOOK_URL")
        if not slack_url:
            raise HTTPException(503, "SLACK_WEBHOOK_URL is not configured")
        task = asyncio.create_task(_digest_and_post(_agent, query, slack_url))
        _background.add(task)
        task.add_done_callback(_background.discard)
        return JSONResponse(status_code=202, content={"accepted": True})

    return router
