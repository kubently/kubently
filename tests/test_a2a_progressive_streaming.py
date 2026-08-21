#!/usr/bin/env python3
"""#115: the stream must narrate the investigation, not replay it afterwards.

`agent.run()` used to await `ainvoke()` and yield exactly once, so a
`message/stream` subscriber saw: the task, a long silence held open only by
sse_starlette keepalives, the ENTIRE answer in one frame, and then — after the
answer they informed — the tool calls. The connection streamed; the content did
not.

Every test here asserts on the SEQUENCE, because a test that only checked the
final text would pass against that behaviour. The three properties that matter:

  * tool calls arrive BEFORE the answer they informed, in the order they ran,
  * the answer arrives as more than one frame,
  * a run that dies mid-stream still reaches a terminal `failed` status.

The tool-call event is also asserted twice over: once as the legacy
"🔧 Tool Call: name({json})" prose that every deployed consumer parses today,
and once as the typed `kubently/tool_call` metadata that replaces the need to
parse it. Both, on the same frame — that is the compatibility promise.
"""

import json
import re
import sys
from pathlib import Path

import pytest

pytest.importorskip("a2a")
pytest.importorskip("deepagents")  # agent_executor -> agent -> deepagents
pytest.importorskip("langchain_core")

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI  # noqa: E402
from langchain_core.language_models import BaseChatModel  # noqa: E402
from langchain_core.messages import AIMessage, AIMessageChunk  # noqa: E402
from langchain_core.outputs import (  # noqa: E402
    ChatGeneration,
    ChatGenerationChunk,
    ChatResult,
)
from starlette.testclient import TestClient  # noqa: E402

from kubently.modules.a2a import A2AModule  # noqa: E402
from kubently.modules.a2a.protocol_bindings.a2a_server.agent import (  # noqa: E402
    TOKEN_FRAME_MAX_CHARS,
    KubentlyAgent,
    _token_frames,
)
from kubently.modules.a2a.protocol_bindings.a2a_server.tool_call_interceptor import (  # noqa: E402
    ToolCallInterceptor,
)

@pytest.fixture(autouse=True)
def _restore_the_global_interceptor():
    """These tests swap in a private interceptor; put the real one back.

    The interceptor is a module-level singleton shared by every test in the
    session — leaving a fake in its place is a cross-file failure that looks
    like a flake."""
    import kubently.modules.a2a.protocol_bindings.a2a_server.agent as agent_module

    original = agent_module.get_tool_call_interceptor
    yield
    agent_module.get_tool_call_interceptor = original


ANSWER = "The checkout pod is crashing.\nRoot cause: the image tag does not exist."
NARRATION = "Let me look at the pods first.\n"
KUBECTL_ARGS = {"cluster_id": "prod-1", "command": "get pods -n checkout"}
KUBECTL_OUTPUT = "checkout-7d9  0/1  CrashLoopBackOff  5  2m"


# --------------------------------------------------------------- agent level


class _FakeGraph:
    """A compiled graph that emits a realistic LangGraph event stream.

    `ainvoke` is implemented too, and deliberately: against the pre-#115
    `run()` — which awaited it — these tests still execute end to end and fail
    on the ORDERING assertions rather than blowing up on a missing attribute.
    A test that only passes because the fake is the wrong shape proves nothing.
    """

    def __init__(self, interceptor, script):
        self.interceptor = interceptor
        self.script = script
        self.invocations = 0

    async def ainvoke(self, inputs, config=None):
        self.invocations += 1
        return {"messages": [AIMessage(content=ANSWER)]}

    def astream_events(self, inputs, config=None, version=None):
        self.invocations += 1
        return self.script(self.interceptor)


def _model_chunk(text: str) -> dict:
    return {
        "event": "on_chat_model_stream",
        "run_id": "llm",
        "data": {"chunk": AIMessageChunk(content=text)},
    }


async def _one_tool_then_an_answer(interceptor):
    """Narrate, run one kubectl, then answer — the shape of a real diagnosis."""
    from kubently.modules.a2a.protocol_bindings.a2a_server.agent import current_thread_id

    thread_id = current_thread_id.get()

    yield {"event": "on_chain_start", "run_id": "root", "name": "kubently", "data": {}}
    yield {"event": "on_chat_model_start", "run_id": "llm", "data": {}}
    yield _model_chunk(NARRATION)

    call_id = await interceptor.record_tool_call(
        tool_name="execute_kubectl", args=KUBECTL_ARGS, thread_id=thread_id
    )
    yield {"event": "on_tool_start", "run_id": "tool", "name": "execute_kubectl", "data": {}}
    await interceptor.record_tool_result(call_id, KUBECTL_OUTPUT)
    yield {"event": "on_tool_end", "run_id": "tool", "name": "execute_kubectl", "data": {}}

    yield {"event": "on_chat_model_start", "run_id": "llm2", "data": {}}
    for piece in (ANSWER[:30], ANSWER[30:]):
        yield _model_chunk(piece)

    yield {
        "event": "on_chain_end",
        "run_id": "root",
        "data": {"output": {"messages": [AIMessage(content=ANSWER)]}},
    }


def _agent(graph, interceptor) -> KubentlyAgent:
    """A KubentlyAgent wired to a fake graph, skipping LLM/Redis setup."""
    agent = KubentlyAgent.__new__(KubentlyAgent)
    agent.redis_client = None
    agent.llm = None
    agent.tools = []
    agent.memory = None
    agent.runbooks = None
    agent.incidents = None
    agent.system_prompt = ""
    agent._injected_runbooks = {}
    agent._surfaced_incidents = {}
    agent.agent = graph

    async def _already_initialized():
        return None

    agent.initialize = _already_initialized

    import kubently.modules.a2a.protocol_bindings.a2a_server.agent as agent_module

    agent_module.get_tool_call_interceptor = lambda: interceptor
    return agent


async def _run(script, thread_id: str):
    """(chunks, graph) for one turn against a fake graph."""
    interceptor = ToolCallInterceptor()
    graph = _FakeGraph(interceptor, script)
    agent = _agent(graph, interceptor)
    chunks = [
        chunk
        async for chunk in agent.run([{"role": "user", "content": "why is checkout down?"}],
                                     thread_id=thread_id)
    ]
    return chunks, graph


@pytest.mark.asyncio
async def test_a_tool_call_arrives_before_the_answer_it_informed():
    """The headline #115 regression: ordering, not content."""
    chunks, _ = await _run(_one_tool_then_an_answer, "ctx-order")
    kinds = [c["type"] for c in chunks]

    assert "tool_call" in kinds, (
        "no tool_call chunk: the run yielded only the finished answer, which is "
        "exactly the pre-#115 behaviour"
    )
    tool_at = kinds.index("tool_call")

    # The answer's own frames, not the narration that preceded the tool call.
    answer_at = next(
        i
        for i, c in enumerate(chunks)
        if c["type"] == "token" and c["content"].startswith(ANSWER[:10])
    )
    assert tool_at < answer_at, "the tool call was streamed after the answer it informed"

    # And the terminal message is last, so nothing trails the answer.
    assert kinds[-1] == "message"
    assert chunks[-1]["content"] == ANSWER


@pytest.mark.asyncio
async def test_the_answer_arrives_as_more_than_one_frame():
    chunks, _ = await _run(_one_tool_then_an_answer, "ctx-frames")
    tokens = [c["content"] for c in chunks if c["type"] == "token"]
    assert len(tokens) > 1, (
        f"the answer arrived in {len(tokens)} frame(s); ainvoke() yields exactly one"
    )
    # Consumers join consecutive `working` texts with "\n" (see stream.ts).
    # That join must reconstruct the model's output character for character.
    assert "\n".join(tokens) == NARRATION.rstrip("\n") + "\n" + ANSWER


@pytest.mark.asyncio
async def test_the_graph_runs_exactly_once():
    """Streaming must not turn one diagnosis into several runs (billing)."""
    _, graph = await _run(_one_tool_then_an_answer, "ctx-billing")
    assert graph.invocations == 1


@pytest.mark.asyncio
async def test_the_tool_call_carries_typed_fields_and_the_legacy_text():
    chunks, _ = await _run(_one_tool_then_an_answer, "ctx-shape")
    call = next(c for c in chunks if c["type"] == "tool_call")

    event = call["tool_call"]
    assert event["schema"] == "kubently.tool_call/v1"
    assert event["tool"] == "execute_kubectl"
    assert event["args"] == KUBECTL_ARGS
    assert event["outcome"] == "ok"
    assert KUBECTL_OUTPUT in event["result"]

    # The prose every deployed consumer parses today is still there, verbatim.
    assert call["content"].startswith("🔧 Tool Call: execute_kubectl(")
    assert "✅ Result:" in call["content"]


@pytest.mark.asyncio
async def test_a_failed_tool_is_reported_as_an_error_not_a_success():
    """#113 in the streaming path: outcome comes from whether the tool reported
    an error, never from a `status` key inside the tool's own payload."""

    async def script(interceptor):
        from kubently.modules.a2a.protocol_bindings.a2a_server.agent import current_thread_id

        yield {"event": "on_chain_start", "run_id": "root", "data": {}}
        call_id = await interceptor.record_tool_call(
            tool_name="execute_kubectl",
            args={"cluster_id": "prod-1", "command": "delete pods --all"},
            thread_id=current_thread_id.get(),
        )
        # The executor denies the command: no result, an error.
        await interceptor.record_tool_result(call_id, None, "Command not allowed: delete")
        yield {"event": "on_tool_end", "run_id": "tool", "data": {}}
        yield {
            "event": "on_chain_end",
            "run_id": "root",
            "data": {"output": {"messages": [AIMessage(content="I cannot delete pods.")]}},
        }

    chunks, _ = await _run(script, "ctx-denied")
    call = next(c for c in chunks if c["type"] == "tool_call")
    assert call["tool_call"]["outcome"] == "error"
    assert "not allowed" in call["tool_call"]["error"]
    assert "result" not in call["tool_call"]
    assert "❌ Error:" in call["content"]


@pytest.mark.asyncio
async def test_a_run_that_dies_mid_stream_still_reports_what_it_ran():
    """The commands that DID run are worth showing, and the turn must end."""

    async def script(interceptor):
        from kubently.modules.a2a.protocol_bindings.a2a_server.agent import current_thread_id

        yield {"event": "on_chain_start", "run_id": "root", "data": {}}
        yield _model_chunk("Looking at the pods.\n")
        call_id = await interceptor.record_tool_call(
            tool_name="execute_kubectl", args=KUBECTL_ARGS, thread_id=current_thread_id.get()
        )
        await interceptor.record_tool_result(call_id, KUBECTL_OUTPUT)
        yield {"event": "on_tool_end", "run_id": "tool", "data": {}}
        raise RuntimeError("the model connection dropped")

    chunks, _ = await _run(script, "ctx-dies")
    kinds = [c["type"] for c in chunks]
    assert "token" in kinds
    assert "tool_call" in kinds
    assert kinds[-1] == "error", "a dead run must terminate, not trail off"
    assert "the model connection dropped" in chunks[-1]["content"]


@pytest.mark.asyncio
async def test_tool_calls_from_another_caller_are_never_streamed_here():
    """The interceptor is a shared buffer; thread matching is what isolates it."""

    async def script(interceptor):
        from kubently.modules.a2a.protocol_bindings.a2a_server.agent import current_thread_id

        yield {"event": "on_chain_start", "run_id": "root", "data": {}}
        await interceptor.record_tool_call(
            tool_name="execute_kubectl",
            args={"cluster_id": "someone-elses", "command": "get secrets -A"},
            thread_id="a-different-caller:ctx-leak",
        )
        mine = await interceptor.record_tool_call(
            tool_name="list_clusters", args={}, thread_id=current_thread_id.get()
        )
        await interceptor.record_tool_result(mine, "prod-1")
        yield {"event": "on_tool_end", "run_id": "tool", "data": {}}
        yield {
            "event": "on_chain_end",
            "run_id": "root",
            "data": {"output": {"messages": [AIMessage(content="prod-1")]}},
        }

    chunks, _ = await _run(script, "ctx-leak")
    tools = [c["tool_call"]["tool"] for c in chunks if c["type"] == "tool_call"]
    assert tools == ["list_clusters"]
    assert "someone-elses" not in json.dumps(chunks)


def test_token_frames_reconstruct_exactly_under_a_newline_join():
    """The property the frame cutter exists for, over awkward inputs."""
    for text in (
        "one line",
        "two\nlines",
        "trailing\n",
        "\nleading",
        "blank\n\nline",
        "a" * 1000,
        "bullet one\n- bullet two\n- bullet three\n",
    ):
        frames: list[str] = []
        buffer = ""
        # Feed it a character at a time: the worst case for a buffering cutter.
        for char in text:
            new, buffer = _token_frames(buffer + char)
            frames.extend(new)
        new, buffer = _token_frames(buffer, force=True)
        frames.extend(new)
        assert buffer == ""
        joined = "\n".join(frames)
        if "\n" in text or len(text) <= TOKEN_FRAME_MAX_CHARS:
            # Exact, modulo the one trailing newline a final frame cannot carry.
            expected = text[:-1] if text.endswith("\n") else text
            assert joined == expected, text
        else:
            # A line longer than any consumer wants to wait for is cut anyway;
            # the join adds newlines but never adds or drops a character.
            assert joined.replace("\n", "") == text


# ------------------------------------------------- the real LangGraph plumbing


class _ScriptedModel(BaseChatModel):
    """A chat model that streams a scripted tool call, then a scripted answer.

    Everything else in the graph is real: `create_deep_agent` builds the actual
    compiled graph (with deepagents' planning and filesystem middleware), and
    LangGraph's own `astream_events` produces the events run() consumes. Only
    the network calls — the model and the cluster — are scripted.
    """

    turn: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _next_message(self) -> AIMessage:
        self.turn += 1
        if self.turn == 1:
            return AIMessage(
                content=NARRATION,
                tool_calls=[{"name": "peek", "args": {"cluster_id": "prod-1"}, "id": "call_1"}],
            )
        return AIMessage(content=ANSWER)

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        message = self._next_message()
        text = message.content or ""
        for i in range(0, len(text), 5):
            piece = text[i : i + 5]
            chunk = ChatGenerationChunk(message=AIMessageChunk(content=piece))
            if run_manager:
                run_manager.on_llm_new_token(piece, chunk=chunk)
            yield chunk
        if message.tool_calls:
            yield ChatGenerationChunk(
                message=AIMessageChunk(content="", tool_calls=message.tool_calls)
            )

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._next_message())])

    @property
    def _identifying_params(self):
        return {}


@pytest.mark.asyncio
async def test_a_real_graph_streams_tokens_and_tools_in_order():
    """End to end over the actual deepagents graph, not a scripted event list.

    This is the test that would have caught "astream_events compiles but never
    emits token events through deepagents' middleware", which no amount of
    faking the event stream can tell you.
    """
    from deepagents import create_deep_agent
    from langchain_core.tools import tool

    from kubently.modules.a2a.protocol_bindings.a2a_server.agent import current_thread_id

    interceptor = ToolCallInterceptor()

    @tool
    async def peek(cluster_id: str) -> str:
        """Look at a cluster."""
        # Exactly what the real tools do: record the call, then its result.
        call_id = await interceptor.record_tool_call(
            tool_name="peek", args={"cluster_id": cluster_id}, thread_id=current_thread_id.get()
        )
        await interceptor.record_tool_result(call_id, KUBECTL_OUTPUT)
        return KUBECTL_OUTPUT

    graph = create_deep_agent(_ScriptedModel(), [peek], system_prompt="debug clusters")
    agent = _agent(graph, interceptor)

    chunks = [
        chunk
        async for chunk in agent.run(
            [{"role": "user", "content": "why is checkout down?"}], thread_id="ctx-real"
        )
    ]
    kinds = [c["type"] for c in chunks]

    assert kinds.count("token") > 1, "the real graph produced no incremental text"
    assert "tool_call" in kinds
    call = next(c for c in chunks if c["type"] == "tool_call")
    assert call["tool_call"]["tool"] == "peek"
    assert call["tool_call"]["outcome"] == "ok"

    tool_at = kinds.index("tool_call")
    answer_at = next(
        i
        for i, c in enumerate(chunks)
        if c["type"] == "token" and c["content"].startswith(ANSWER[:10])
    )
    assert tool_at < answer_at

    assert kinds[-1] == "message"
    assert chunks[-1]["content"] == ANSWER
    assert chunks[-1]["metadata"]["streamed"] is True

    # And the frames still rebuild the model's output exactly.
    tokens = [c["content"] for c in chunks if c["type"] == "token"]
    assert "\n".join(tokens) == NARRATION.rstrip("\n") + "\n" + ANSWER


# ----------------------------------------------------------------- SSE level


STREAM_REQUEST = {
    "jsonrpc": "2.0",
    "id": "s1",
    "method": "message/stream",
    "params": {
        "message": {
            "messageId": "m1",
            "role": "user",
            "contextId": "ctx-sse",
            "parts": [{"partId": "p1", "text": "why is checkout down?"}],
        }
    },
}

TOOL_TEXT = (
    "🔧 Tool Call: execute_kubectl(" + json.dumps(KUBECTL_ARGS, indent=2) + ")"
    "\n✅ Result: " + KUBECTL_OUTPUT + "..."
)

TOOL_EVENT = {
    "schema": "kubently.tool_call/v1",
    "id": "tc_1",
    "tool": "execute_kubectl",
    "args": KUBECTL_ARGS,
    "outcome": "ok",
    "result": KUBECTL_OUTPUT,
}


class _ScriptedAgent:
    """Stands in for KubentlyAgent at the executor boundary."""

    def __init__(self, chunks, explode: bool = False):
        self.chunks = chunks
        self.explode = explode

    async def initialize(self):
        return None

    async def run(self, messages, thread_id=None, cluster_id=None, mcp_servers=None):
        for chunk in self.chunks:
            yield chunk
        if self.explode:
            raise RuntimeError("the model connection dropped")


def _executor(agent):
    from kubently.modules.a2a.protocol_bindings.a2a_server.agent_executor import (
        KubentlyAgentExecutor,
    )

    executor = KubentlyAgentExecutor(redis_client=None)
    executor.agent = agent
    executor._initialized = True
    return executor


def _post(executor):
    module = A2AModule(host="127.0.0.1", port=8080, external_url="http://localhost:8080/a2a/")
    module._lazy_imports = lambda: (lambda redis_client=None: executor)
    outer = FastAPI()
    outer.mount("/a2a", module.get_app())
    with TestClient(outer, raise_server_exceptions=False) as client:
        return client.post(
            "/a2a/",
            headers={"Accept": "text/event-stream", "Content-Type": "application/json"},
            json=STREAM_REQUEST,
        )


def _parts_text(container) -> str:
    """Concatenated text of a Message's or Artifact's parts."""
    parts = (container or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts if p.get("kind") == "text")


def _state(frame) -> str:
    """`completed` / `TASK_STATE_COMPLETED` — normalise like a real client."""
    raw = ((frame.get("result") or {}).get("status") or {}).get("state") or ""
    return str(raw).lower().replace("task_state_", "")


def _frames(response) -> list[dict]:
    """The `data:` payloads of the SSE body, parsed, in order.

    Events are separated by a blank line, and sse_starlette writes CRLF — a
    reader that splits on "\n\n" alone sees one giant block and no events.
    """
    out = []
    for block in re.split(r"\r?\n\r?\n", response.text):
        data = "\n".join(
            line[len("data:") :].strip() for line in block.splitlines() if line.startswith("data:")
        )
        if not data:
            continue
        try:
            out.append(json.loads(data))
        except json.JSONDecodeError:
            continue
    return out


DIAGNOSIS = [
    {"type": "token", "content": "Let me look at the pods first."},
    {"type": "tool_call", "content": TOOL_TEXT, "tool_call": TOOL_EVENT},
    {"type": "token", "content": "The checkout pod is crashing."},
    {"type": "token", "content": "Root cause: the image tag does not exist."},
    {"type": "message", "content": ANSWER, "metadata": {"streamed": True}},
]


def test_the_wire_carries_the_tool_call_before_the_answer():
    response = _post(_executor(_ScriptedAgent(DIAGNOSIS)))
    assert response.status_code == 200
    frames = _frames(response)

    texts = []
    for frame in frames:
        result = frame.get("result", {})
        if result.get("kind") != "status-update":
            continue
        texts.append(_parts_text(result.get("status", {}).get("message")))

    tool_at = next(i for i, t in enumerate(texts) if "Tool Call:" in t)
    answer_at = next(i for i, t in enumerate(texts) if "checkout pod is crashing" in t)
    assert tool_at < answer_at, "the tool call reached the wire after the answer"
    assert len([t for t in texts if t.strip()]) > 2, "the answer arrived in one frame"


def test_the_wire_carries_the_typed_tool_event_alongside_the_text():
    response = _post(_executor(_ScriptedAgent(DIAGNOSIS)))
    frame = next(
        f
        for f in _frames(response)
        if "kubently/tool_call" in json.dumps(f.get("result", {}))
    )
    result = frame["result"]

    # On the status update itself...
    assert result["metadata"]["kubently/event"] == "tool_call"
    event = result["metadata"]["kubently/tool_call"]
    assert event["tool"] == "execute_kubectl"
    assert event["outcome"] == "ok"
    assert event["args"]["command"] == KUBECTL_ARGS["command"]
    # ...and on the message, for consumers that only parse that layer.
    assert result["status"]["message"]["metadata"]["kubently/tool_call"]["tool"] == (
        "execute_kubectl"
    )
    # The old text protocol is on the SAME frame — nothing had to be replaced.
    assert _parts_text(result["status"]["message"]).startswith(
        "🔧 Tool Call: execute_kubectl("
    )


def test_the_answer_is_not_repeated_once_it_has_been_streamed():
    """`streamed` on the final message keeps the artifact authoritative without
    re-sending the whole answer as one more `working` status."""
    response = _post(_executor(_ScriptedAgent(DIAGNOSIS)))
    frames = _frames(response)

    working = [
        f
        for f in frames
        if f.get("result", {}).get("kind") == "status-update"
        and _state(f) == "working"
    ]
    whole_answer = [
        f for f in working if ANSWER in _parts_text(f["result"]["status"].get("message"))
    ]
    assert not whole_answer, "the finished answer was replayed as a working status"
    # ...and yet every line of it did reach the client, as separate frames.
    assert "\n".join(_parts_text(f["result"]["status"].get("message")) for f in working).endswith(
        ANSWER
    )

    artifact = next(f for f in frames if f.get("result", {}).get("kind") == "artifact-update")
    assert _parts_text(artifact["result"]["artifact"]) == ANSWER
    assert _state(frames[-1]) == "completed"


def test_a_run_that_dies_mid_stream_reaches_a_terminal_failed_status():
    """Never leave the subscription open on a half-told story."""
    agent = _ScriptedAgent(
        [
            {"type": "token", "content": "Let me look at the pods first."},
            {"type": "tool_call", "content": TOOL_TEXT, "tool_call": TOOL_EVENT},
        ],
        explode=True,
    )
    response = _post(_executor(agent))
    assert response.status_code == 200
    frames = _frames(response)

    assert any("Tool Call:" in json.dumps(f) for f in frames), (
        "the work that did happen was lost on the failure path"
    )
    terminal = frames[-1]["result"]
    assert terminal["kind"] == "status-update"
    assert _state(frames[-1]) == "failed", (
        "a dead run reported itself as completed; a subscriber cannot tell that "
        "from a real answer"
    )
    assert terminal["final"] is True
    assert "the model connection dropped" in json.dumps(terminal)


def test_an_agent_error_chunk_also_terminates_as_failed():
    """agent.run() reports an in-graph failure as an `error` chunk, not an
    exception. It has to end the task the same way."""
    agent = _ScriptedAgent(
        [
            {"type": "token", "content": "Checking."},
            {
                "type": "error",
                "content": "I encountered an error while processing your request: boom",
            },
        ]
    )
    frames = _frames(_post(_executor(agent)))
    assert _state(frames[-1]) == "failed"
    assert "boom" in json.dumps(frames[-1])


def test_the_streaming_path_makes_exactly_one_agent_run():
    """One A2A request is one diagnosis: kubently-cloud meters `message/stream`
    identically to `message/send`, one unit per request."""
    calls = []

    class _Counting(_ScriptedAgent):
        async def run(self, messages, thread_id=None, cluster_id=None, mcp_servers=None):
            calls.append(thread_id)
            async for chunk in super().run(messages, thread_id, cluster_id, mcp_servers):
                yield chunk

    _post(_executor(_Counting(DIAGNOSIS)))
    assert len(calls) == 1


def test_the_agent_no_longer_batches_the_run():
    """Source guard: `run()` must not go back to a single awaited invoke."""
    source = (
        Path(__file__).parent.parent
        / "kubently/modules/a2a/protocol_bindings/a2a_server/agent.py"
    ).read_text()
    assert "astream_events" in source
    assert "run_agent.ainvoke(" not in source, (
        "run() is awaiting the graph again — every consumer is back to a silent "
        "gap and one lump (#115)"
    )
