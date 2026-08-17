#!/usr/bin/env python3
"""Incident history wired through KubentlyAgent.run().

Module behaviour is covered in test_incidents.py; these tests pin the agent
integration itself: a concluded RCA is persisted, a later similar
investigation gets the auto-surface note (deduped per thread, never the
thread's own record), the search_past_incidents tool is registered exactly
when the feature is on, and the kill switch removes all of it.

The LLM/graph is stubbed: run() drives a fake compiled graph that returns a
canned final answer, so no model call and no checkpointer is involved.
"""

import sys
from pathlib import Path

import pytest

fakeredis = pytest.importorskip("fakeredis")
pytest.importorskip("langchain_core")
pytest.importorskip("deepagents")

sys.path.insert(0, str(Path(__file__).parent.parent))

from langchain_core.messages import AIMessage  # noqa: E402

from kubently.modules.a2a.protocol_bindings.a2a_server.agent import KubentlyAgent  # noqa: E402
from kubently.modules.incidents import IncidentStore  # noqa: E402

RCA_ANSWER = (
    "📊 Summary:\n"
    "- Root Cause: checkout-api readiness probe points at port 8081 but the "
    "container listens on 8080, so the service has no endpoints.\n"
    "🔧 Fix:\nAlign the probe port with the container port.\n"
)


class FakeGraph:
    """Stands in for the compiled deepagents graph."""

    def __init__(self, answer: str):
        self.answer = answer
        self.invocations = []

    async def ainvoke(self, payload, config=None):
        self.invocations.append(payload)
        return {"messages": [AIMessage(content=self.answer)]}


def make_agent(redis, answer=RCA_ANSWER, incidents=True) -> KubentlyAgent:
    agent = KubentlyAgent(redis_client=redis)
    # Short-circuit initialize(): stub everything it would build.
    agent._initialized = True
    agent._memory_disabled = True
    agent.memory = None
    agent.runbooks = None
    agent.incidents = IncidentStore(redis) if incidents else None
    agent.agent = FakeGraph(answer)
    return agent


async def drain(agent, messages, **kwargs):
    return [item async for item in agent.run(messages, **kwargs)]


@pytest.fixture
def redis():
    return fakeredis.FakeAsyncRedis(decode_responses=True)


async def test_rca_answer_is_recorded(redis):
    agent = make_agent(redis)
    out = await drain(
        agent,
        [{"role": "user", "content": "why does checkout-api have no endpoints?"}],
        thread_id="t1",
        cluster_id="prod-east",
    )
    assert out and out[0]["type"] == "message"
    # The concluded RCA left a record in the (unauthenticated → local) namespace.
    records = await agent.incidents.load_recent("local")
    assert len(records) == 1
    rec = records[0]
    assert "readiness probe" in rec.root_cause
    assert rec.cluster_id == "prod-east"
    assert rec.thread_id == "t1"


async def test_non_rca_answer_records_nothing(redis):
    agent = make_agent(redis, answer="All pods are healthy — nothing to fix.")
    await drain(agent, [{"role": "user", "content": "any problems?"}], thread_id="t1")
    assert await agent.incidents.count("local") == 0


async def test_similar_investigation_gets_surface_note(redis):
    # First conversation concludes with an RCA.
    first = make_agent(redis)
    await drain(
        first,
        [{"role": "user", "content": "checkout-api has no endpoints"}],
        thread_id="t1",
        cluster_id="prod-east",
    )
    # A NEW conversation with matching symptoms gets the note injected.
    second = make_agent(redis)
    await drain(
        second,
        [{"role": "user", "content": "checkout-api service has no endpoints again"}],
        thread_id="t2",
        cluster_id="prod-east",
    )
    sent = second.agent.invocations[0]["messages"]
    injected = [m.content for m in sent if "SIMILAR PAST INCIDENT" in str(m.content)]
    assert injected, "expected the auto-surface note in the model input"
    assert "readiness probe" in injected[0]
    assert "verify" in injected[0].lower()


async def test_own_thread_never_surfaces_its_own_record(redis):
    agent = make_agent(redis)
    msgs = [{"role": "user", "content": "checkout-api has no endpoints"}]
    await drain(agent, msgs, thread_id="t1", cluster_id="prod-east")
    # Turn 2 of the SAME thread: its own turn-1 diagnosis must not echo back.
    await drain(agent, msgs, thread_id="t1", cluster_id="prod-east")
    sent = agent.agent.invocations[1]["messages"]
    assert not any("SIMILAR PAST INCIDENT" in str(m.content) for m in sent)


async def test_surface_note_deduped_within_thread(redis):
    seeder = make_agent(redis)
    await drain(
        seeder,
        [{"role": "user", "content": "checkout-api has no endpoints"}],
        thread_id="t1",
        cluster_id="prod-east",
    )
    other = make_agent(redis)
    msgs = [{"role": "user", "content": "checkout-api service has no endpoints"}]
    await drain(other, msgs, thread_id="t2", cluster_id="prod-east")
    await drain(other, msgs, thread_id="t2", cluster_id="prod-east")
    second_turn = other.agent.invocations[1]["messages"]
    assert not any("SIMILAR PAST INCIDENT" in str(m.content) for m in second_turn)


async def test_unrelated_investigation_gets_no_note(redis):
    seeder = make_agent(redis)
    await drain(
        seeder,
        [{"role": "user", "content": "checkout-api has no endpoints"}],
        thread_id="t1",
        cluster_id="prod-east",
    )
    other = make_agent(redis)
    await drain(
        other,
        [{"role": "user", "content": "how many nodes does staging-eu have?"}],
        thread_id="t2",
    )
    sent = other.agent.invocations[0]["messages"]
    assert not any("SIMILAR PAST INCIDENT" in str(m.content) for m in sent)


async def test_kill_switch_disables_everything(redis, monkeypatch):
    monkeypatch.setenv("KUBENTLY_INCIDENT_HISTORY", "false")
    agent = make_agent(redis, incidents=False)
    await drain(
        agent,
        [{"role": "user", "content": "checkout-api has no endpoints"}],
        thread_id="t1",
    )
    # Nothing recorded anywhere.
    assert [k async for k in redis.scan_iter("kubently:incidents:*")] == []


def test_search_tool_registration_is_gated():
    """search_past_incidents registers exactly when the store exists; the
    baseline toolset is otherwise untouched (additive change only)."""
    src = Path(
        "kubently/modules/a2a/protocol_bindings/a2a_server/agent.py"
    ).read_text()
    assert "async def search_past_incidents(" in src
    assert "if self.incidents is not None:" in src
    # Tool tracing rule from CLAUDE.md: the tool must record calls + results.
    tool_body = src.split("async def search_past_incidents(")[1].split("self.tools.append")[0]
    assert "record_tool_call" in tool_body
    assert "record_tool_result" in tool_body
