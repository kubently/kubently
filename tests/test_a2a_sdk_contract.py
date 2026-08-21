#!/usr/bin/env python3
"""
Contract tests pinning the a2a-sdk surface Kubently binds to.

Why this file exists: the A2A binding code (``kubently/modules/a2a/__init__.py``,
``agent_executor.py``, ``helpers.py``) is the only place Kubently touches
``a2a-sdk``, and until now none of it was exercised by a test — the sole
existing check (``test_tool_trace_thread_match.py``) reads ``agent_executor.py``
as *text* and regexes it, so it passes even when the module cannot be imported.
That made an a2a-sdk version bump completely unguarded.

``kubently/modules/a2a/__init__.py`` used to wrap its SDK imports in a bare
``except Exception`` that set ``A2A_AVAILABLE = False``: an SDK release that
moved or renamed a symbol did not crash the API, it silently started Kubently
with the entire A2A protocol surface missing (issue #97). A2A is now gated on
``KUBENTLY_A2A`` and an unimportable SDK raises ``A2AUnavailableError``; the
tests at the bottom of this file are what keep that from regressing.

Pinned against **a2a-sdk 1.x** since #76. The 1.x surface differs from 0.2.x in
ways this file has to encode: ``a2a.server.apps`` is gone (routes come from
``a2a.server.routes``), the ``new_*`` helpers moved to ``a2a.helpers`` with new
signatures, ``a2a.types`` is protobuf-generated (snake_case fields, ``TaskState``
enum members, no ``TextPart``), and ``TaskStatusUpdateEvent`` has no ``final``
flag — terminality is carried by the task state.

Contracts under guard:
- Every module path and symbol the codebase imports from ``a2a`` still resolves.
- ``A2A_AVAILABLE`` is True, i.e. the SDK did not silently disable A2A.
- An unimportable SDK is fatal when A2A is enabled, and returns None *only* when
  it was explicitly switched off.
- The helper functions produce real SDK event objects with the fields the
  protocol requires.
- ``KubentlyAgentExecutor.execute()`` emits the documented event sequence.
- The agent card builds and serialises.

These are deliberately shallow-but-broad: they assert the *shape* of the SDK
contract rather than agent behaviour, so they stay cheap and fail loudly on any
SDK bump that moves the ground underneath the bindings.
"""

import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# =============================================================================
# Import surface
# =============================================================================

# (module path, symbol) for every name the codebase imports from the SDK.
# Sourced by grepping `from a2a...` across kubently/.
SDK_IMPORT_SURFACE = [
    ("a2a.server.request_handlers", "DefaultRequestHandler"),
    ("a2a.server.routes", "create_agent_card_routes"),
    ("a2a.server.routes", "create_jsonrpc_routes"),
    ("a2a.server.tasks", "InMemoryTaskStore"),
    ("a2a.server.tasks", "PushNotificationSender"),
    ("a2a.server.agent_execution", "AgentExecutor"),
    ("a2a.server.agent_execution", "RequestContext"),
    ("a2a.server.events.event_queue", "EventQueue"),
    ("a2a.types", "AgentCapabilities"),
    ("a2a.types", "AgentCard"),
    ("a2a.types", "AgentInterface"),
    ("a2a.types", "AgentSkill"),
    ("a2a.types", "Artifact"),
    ("a2a.types", "Message"),
    ("a2a.types", "Part"),
    ("a2a.types", "Role"),
    ("a2a.types", "Task"),
    ("a2a.types", "TaskArtifactUpdateEvent"),
    ("a2a.types", "TaskState"),
    ("a2a.types", "TaskStatus"),
    ("a2a.types", "TaskStatusUpdateEvent"),
    ("a2a.utils.constants", "AGENT_CARD_WELL_KNOWN_PATH"),
    ("a2a.utils.constants", "PROTOCOL_VERSION_0_3"),
    ("a2a.utils.constants", "PROTOCOL_VERSION_CURRENT"),
    ("a2a.utils.constants", "TransportProtocol"),
    ("a2a.helpers", "new_task_from_user_message"),
    ("a2a.helpers", "new_text_artifact"),
    ("a2a.helpers", "new_text_message"),
    ("a2a.helpers", "new_text_part"),
]


@pytest.mark.parametrize("module_path,symbol", SDK_IMPORT_SURFACE)
def test_sdk_symbol_still_exists(module_path, symbol):
    """Every symbol the bindings import must still resolve in the installed SDK.

    A bump that relocates one of these (1.x moved the ``new_*`` helpers to
    ``a2a.helpers`` and deleted ``a2a.server.apps`` outright) fails here with the
    exact missing name rather than surfacing as a silent runtime downgrade.
    """
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:  # pragma: no cover - only on an incompatible SDK
        pytest.fail(f"a2a-sdk no longer provides module '{module_path}': {exc}")

    assert hasattr(module, symbol), (
        f"a2a-sdk no longer provides '{module_path}.{symbol}'. "
        f"Kubently's A2A bindings import it; the bump needs a migration, not a version change."
    )


def test_a2a_is_available_not_silently_disabled():
    """A2A must be genuinely importable, not degraded to a no-op.

    ``kubently/modules/a2a/__init__.py`` catches *all* exceptions around its SDK
    imports and falls back to ``A2A_AVAILABLE = False``. Without this assertion
    an incompatible a2a-sdk ships as "A2A support disabled at import time" in a
    log line nobody reads, and the whole agent protocol quietly disappears.
    """
    a2a_module = importlib.import_module("kubently.modules.a2a")

    assert a2a_module.A2A_AVAILABLE is True, (
        "A2A support disabled at import time — the installed a2a-sdk is "
        "incompatible with kubently/modules/a2a/__init__.py. This does not raise "
        "on its own; it silently removes the A2A protocol surface."
    )


def test_agent_executor_module_imports():
    """The executor binds AgentExecutor/EventQueue/utils and must import cleanly."""
    module = importlib.import_module(
        "kubently.modules.a2a.protocol_bindings.a2a_server.agent_executor"
    )
    from a2a.server.agent_execution import AgentExecutor

    assert issubclass(module.KubentlyAgentExecutor, AgentExecutor), (
        "KubentlyAgentExecutor must remain a subclass of the SDK's AgentExecutor "
        "or DefaultRequestHandler will refuse it."
    )


def test_sdk_helper_signatures_are_stable():
    """The ``new_*`` helpers are called by keyword, so the names matter."""
    import inspect

    from a2a.helpers import new_task_from_user_message, new_text_artifact, new_text_message

    # agent_executor.py calls new_text_message(text, context_id=..., task_id=...).
    # 1.x replaced new_agent_text_message with this; the agent role is the default,
    # so a change of default would silently relabel every streamed chunk as a user
    # message.
    params = inspect.signature(new_text_message).parameters
    assert next(iter(params)) == "text"
    assert {"context_id", "task_id", "role"} <= set(params)
    from a2a.types import Role

    assert params["role"].default == Role.ROLE_AGENT, (
        "new_text_message no longer defaults to the agent role; agent_executor.py "
        "relies on that default for every streamed chunk."
    )

    # agent_executor.py calls new_text_artifact(name=..., description=..., text=...)
    params = set(inspect.signature(new_text_artifact).parameters)
    assert {"name", "text", "description"} <= params, (
        f"new_text_artifact no longer accepts name/text/description: {params}"
    )

    # agent_executor.py calls new_task_from_user_message(context.message)
    assert len(inspect.signature(new_task_from_user_message).parameters) == 1


# =============================================================================
# helpers.py — event construction against real SDK types
# =============================================================================


def _stream_ending_states():
    """The states a2a-sdk 1.x treats as ending a stream.

    ``TaskStatusUpdateEvent.final`` no longer exists. ``EventConsumer`` stops
    consuming when the task reaches one of these instead, and the v0.3
    compatibility layer re-derives the wire-level ``final: true`` from (a subset
    of) the same set. So a terminal task state *is* the "stream is over" signal
    now, and asserting on it is the 1.x equivalent of asserting ``final``.

    Mirrors ``a2a.server.events.event_consumer``; imported lazily so this module
    still collects when the SDK is missing.
    """
    from a2a.types import TaskState

    return {
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_CANCELED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_REJECTED,
        TaskState.TASK_STATE_UNSPECIFIED,
        TaskState.TASK_STATE_INPUT_REQUIRED,
    }


def _make_task():
    """Build a minimal but valid SDK Task, the way new_task_from_user_message() would."""
    from a2a.helpers import new_text_part
    from a2a.types import Message, Role, Task, TaskState, TaskStatus

    return Task(
        id="task-1",
        context_id="ctx-1",
        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
        history=[
            Message(
                message_id="msg-1",
                role=Role.ROLE_USER,
                parts=[new_text_part("why is my pod crashlooping?")],
            )
        ],
    )


class TestUpdateTaskWithAgentResponse:
    """``update_task_with_agent_response`` mutates a Task in place."""

    def test_completed_response_appends_artifact(self):
        from a2a.types import TaskState
        from kubently.modules.a2a.protocol_bindings.a2a_server.helpers import (
            update_task_with_agent_response,
        )

        task = _make_task()
        update_task_with_agent_response(
            task, {"content": "pod is OOMKilled", "require_user_input": False}
        )

        assert task.status.state == TaskState.TASK_STATE_COMPLETED
        # Protobuf messages have no None: "no status message" is the field being
        # unset, which is what the 1.x port asserts instead of `is None`.
        assert not task.status.HasField("message")
        assert len(task.artifacts) == 1
        assert task.artifacts[0].parts[0].text == "pod is OOMKilled"
        # timestamp must be set for the protocol to order events
        assert task.status.HasField("timestamp")

    def test_input_required_response_sets_message_and_history(self):
        from a2a.types import Role, TaskState
        from kubently.modules.a2a.protocol_bindings.a2a_server.helpers import (
            update_task_with_agent_response,
        )

        task = _make_task()
        history_before = len(task.history)
        update_task_with_agent_response(
            task, {"content": "which namespace?", "require_user_input": True}
        )

        assert task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        assert task.status.HasField("message")
        assert task.status.message.role == Role.ROLE_AGENT
        assert task.status.message.parts[0].text == "which namespace?"
        # the clarifying question must land in history, or the next turn loses it
        assert len(task.history) == history_before + 1
        assert not task.artifacts


class TestProcessStreamingAgentResponse:
    """``process_streaming_agent_response`` maps agent chunks to SDK events."""

    def test_working_chunk_streams_without_ending(self):
        from a2a.types import TaskState
        from kubently.modules.a2a.protocol_bindings.a2a_server.helpers import (
            process_streaming_agent_response,
        )

        artifact_event, status_event = process_streaming_agent_response(
            _make_task(),
            {"content": "checking pods...", "is_task_complete": False, "require_user_input": False},
        )

        assert artifact_event is None, "intermediate chunks must not emit artifacts"
        assert status_event.status.state == TaskState.TASK_STATE_WORKING
        # 1.x dropped TaskStatusUpdateEvent.final; a non-terminal state is what
        # keeps the stream open, so that is what gets asserted.
        assert status_event.status.state not in _stream_ending_states()
        assert status_event.status.message.parts[0].text == "checking pods..."

    def test_completed_chunk_emits_final_artifact(self):
        from a2a.types import TaskState
        from kubently.modules.a2a.protocol_bindings.a2a_server.helpers import (
            process_streaming_agent_response,
        )

        task = _make_task()
        artifact_event, status_event = process_streaming_agent_response(
            task,
            {"content": "done: OOMKilled", "is_task_complete": True, "require_user_input": False},
        )

        assert artifact_event is not None
        assert artifact_event.task_id == task.id
        assert artifact_event.context_id == task.context_id
        assert artifact_event.last_chunk is True
        assert artifact_event.append is False
        assert artifact_event.artifact.parts[0].text == "done: OOMKilled"

        assert status_event.status.state == TaskState.TASK_STATE_COMPLETED
        assert status_event.status.state in _stream_ending_states(), (
            "completed stream must reach a terminal state or clients hang"
        )

    def test_input_required_chunk_ends_stream_without_artifact(self):
        from a2a.types import TaskState
        from kubently.modules.a2a.protocol_bindings.a2a_server.helpers import (
            process_streaming_agent_response,
        )

        artifact_event, status_event = process_streaming_agent_response(
            _make_task(),
            {"content": "which cluster?", "is_task_complete": False, "require_user_input": True},
        )

        assert artifact_event is None
        assert status_event.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        assert status_event.status.state in _stream_ending_states()


# =============================================================================
# Agent card
# =============================================================================


class TestAgentCardContract:
    """The agent card is the A2A discovery document — it must build and serialise."""

    def test_agent_card_builds_and_serialises(self):
        from a2a.server.request_handlers.response_helpers import agent_card_to_dict
        from a2a.utils.constants import (
            PROTOCOL_VERSION_0_3,
            PROTOCOL_VERSION_CURRENT,
            TransportProtocol,
        )
        from kubently.modules.a2a import A2AModule

        module = A2AModule(host="127.0.0.1", port=8000, external_url="https://example.test/a2a/")
        card = module.get_agent_card()

        assert card.name == "Kubently Kubernetes Debugger"
        assert card.capabilities.streaming is True

        # 1.x moved the endpoint off the card and onto per-transport interfaces.
        # Both protocol versions must be advertised because both are served
        # (get_app() enables the SDK's v0.3 compatibility on the same endpoint).
        interfaces = {i.protocol_version: i for i in card.supported_interfaces}
        assert set(interfaces) == {PROTOCOL_VERSION_CURRENT, PROTOCOL_VERSION_0_3}
        for interface in interfaces.values():
            assert interface.url == "https://example.test/a2a/"
            assert interface.protocol_binding == TransportProtocol.JSONRPC

        # The card is served as JSON over /.well-known — round-tripping it through
        # the SDK's own serialiser is the real contract.
        payload = agent_card_to_dict(card)
        assert payload["name"] == "Kubently Kubernetes Debugger"
        assert "text/plain" in payload["defaultInputModes"]
        assert [s["id"] for s in payload["skills"]] == [s.id for s in card.skills]

        # ...and the published JSON must still carry the pre-1.x top-level fields,
        # which the SDK derives from the 0.3 interface. Existing clients (the
        # Kubently CLI included) read `url`; dropping it breaks discovery for
        # every one of them.
        assert payload["url"] == "https://example.test/a2a/"
        assert payload["protocolVersion"] == PROTOCOL_VERSION_0_3
        assert payload["preferredTransport"] == TransportProtocol.JSONRPC

    def test_advertised_skills_follow_the_toolset_gating(self):
        """Skills are configuration-dependent, so assert the gates — not a count.

        A hardcoded number is the stale assumption issue #87 removed: it says
        nothing about whether the card matches the tools this deployment
        registers, and it breaks every time a toolset is added.
        """
        from kubently.modules.a2a import A2AModule
        from kubently.modules.a2a.protocol_bindings.a2a_server.cloud_tools import (
            cloud_tools_configured,
        )
        from kubently.modules.a2a.protocol_bindings.a2a_server.gitops import gitops_tools_enabled
        from kubently.modules.a2a.protocol_bindings.a2a_server.logsearch import loki_tool_enabled
        from kubently.modules.a2a.protocol_bindings.a2a_server.mcp_client import mcp_client_enabled
        from kubently.modules.a2a.protocol_bindings.a2a_server.prometheus import (
            prometheus_tool_enabled,
        )

        # redis_client is None here, so the incident store cannot be built and
        # agent.py would not register search_past_incidents.
        card = A2AModule(
            host="127.0.0.1", port=8000, external_url="https://example.test/a2a/"
        ).get_agent_card()
        ids = {skill.id for skill in card.skills}

        # Toolsets agent.py registers unconditionally.
        assert {"kubernetes-debug", "fleet-query", "pod-log-search", "change-correlation"} <= ids

        # Optional toolsets appear exactly when their tools would register.
        assert ("prometheus-metrics" in ids) is prometheus_tool_enabled()
        assert ("loki-log-search" in ids) is loki_tool_enabled()
        assert ("cloud-telemetry" in ids) is cloud_tools_configured()
        assert ("gitops-remediation" in ids) is gitops_tools_enabled()
        assert ("external-mcp-tools" in ids) is mcp_client_enabled()
        assert "incident-history" not in ids

        for skill in card.skills:
            assert skill.name and skill.description and skill.tags


# =============================================================================
# Executor event sequence
# =============================================================================


class _StubAgent:
    """Stands in for KubentlyAgent so the executor runs with no LLM or cluster."""

    SUPPORTED_CONTENT_TYPES = ["text/plain", "application/json"]

    def __init__(self, chunks):
        self._chunks = chunks
        self.received = None

    async def initialize(self):
        return None

    async def run(self, messages, thread_id=None, cluster_id=None):
        self.received = {
            "messages": messages,
            "thread_id": thread_id,
            "cluster_id": cluster_id,
        }
        for chunk in self._chunks:
            yield chunk


def _make_request_context(text: str, metadata: dict | None = None):
    """Build a RequestContext the way DefaultRequestHandler does for a new task."""
    from a2a.helpers import new_text_part
    from a2a.server.agent_execution import RequestContext
    from a2a.server.context import ServerCallContext
    from a2a.types import Message, Role, SendMessageRequest
    from google.protobuf.json_format import ParseDict

    message = Message(
        message_id="msg-1",
        role=Role.ROLE_USER,
        parts=[new_text_part(text)],
        context_id="ctx-1",
    )
    request = SendMessageRequest(message=message)
    if metadata is not None:
        # metadata carries the clusterId A2A extension the executor reads. In 1.x
        # it hangs off SendMessageRequest rather than MessageSendParams; the v0.3
        # compatibility layer copies `params.metadata` into it, so the CLI's
        # clusterId still lands here.
        ParseDict(metadata, request.metadata)
    return RequestContext(
        call_context=ServerCallContext(),
        request=request,
        task_id="task-1",
        context_id="ctx-1",
    )


def _event_queue():
    """A queue the test can drain.

    1.x made ``EventQueue`` an abstract producer-side interface owned by the
    request handler; ``EventQueueLegacy`` is the concrete implementation that
    still exposes an inspectable backing queue, which is what these tests need
    to look at the emitted event sequence directly.
    """
    from a2a.server.events.event_queue import EventQueueLegacy

    return EventQueueLegacy()


async def _drain(event_queue):
    """Pull every event currently sitting in the queue."""
    events = []
    queue = event_queue.queue if hasattr(event_queue, "queue") else None
    if queue is None:  # pragma: no cover - SDK internals changed
        pytest.fail("EventQueue no longer exposes an inspectable backing queue")
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


class TestAgentExecutorEventSequence:
    """``execute()`` must emit the event sequence A2A clients depend on."""

    @pytest.mark.asyncio
    async def test_execute_emits_task_then_stream_then_final_artifact(self):
        from a2a.types import (
            Task,
            TaskArtifactUpdateEvent,
            TaskState,
            TaskStatusUpdateEvent,
        )
        from kubently.modules.a2a.protocol_bindings.a2a_server.agent_executor import (
            KubentlyAgentExecutor,
        )

        executor = KubentlyAgentExecutor.__new__(KubentlyAgentExecutor)
        executor.redis_client = None
        executor.agent = _StubAgent([{"content": "looking at pods"}, {"content": "found it"}])
        executor._active_sessions = {}
        executor._initialized = True

        context = _make_request_context("summarise cluster health")
        event_queue = _event_queue()

        await executor.execute(context, event_queue)
        events = await _drain(event_queue)

        # 1. the new Task must be published first, or the client has nothing to attach to
        assert isinstance(events[0], Task), f"first event was {type(events[0]).__name__}"

        # 2. every streamed chunk reaches the client as a non-terminal working update
        working = [
            e
            for e in events
            if isinstance(e, TaskStatusUpdateEvent)
            and e.status.state == TaskState.TASK_STATE_WORKING
        ]
        streamed = [e.status.message.parts[0].text for e in working]
        assert "looking at pods" in streamed
        assert "found it" in streamed
        assert all(e.status.state not in _stream_ending_states() for e in working)

        # 3. exactly one final artifact carrying the joined response
        artifacts = [e for e in events if isinstance(e, TaskArtifactUpdateEvent)]
        assert len(artifacts) == 1
        assert artifacts[0].last_chunk is True
        assert artifacts[0].artifact.parts[0].text == "looking at pods\nfound it"

        # 4. the stream terminates on a terminal state, or clients hang forever.
        #    (1.x has no `final` flag; the state is the signal — see
        #    _stream_ending_states.)
        assert isinstance(events[-1], TaskStatusUpdateEvent)
        assert events[-1].status.state == TaskState.TASK_STATE_COMPLETED
        assert events[-1].status.state in _stream_ending_states()

    @pytest.mark.asyncio
    async def test_cluster_id_from_metadata_reaches_the_agent(self):
        """clusterId arrives as an A2A metadata extension and must be forwarded."""
        from kubently.modules.a2a.protocol_bindings.a2a_server.agent_executor import (
            KubentlyAgentExecutor,
        )

        executor = KubentlyAgentExecutor.__new__(KubentlyAgentExecutor)
        executor.redis_client = None
        executor.agent = _StubAgent([{"content": "ok"}])
        executor._active_sessions = {}
        executor._initialized = True

        context = _make_request_context("summarise cluster health", metadata={"clusterId": "prod"})
        await executor.execute(context, _event_queue())

        assert executor.agent.received["cluster_id"] == "prod"
        assert executor.agent.received["thread_id"] == "ctx-1"
        assert executor.agent.received["messages"] == [
            {"role": "user", "content": "summarise cluster health"}
        ]

    @pytest.mark.asyncio
    async def test_agent_failure_still_closes_the_stream(self):
        """An agent crash must still yield a final artifact + a TERMINAL status.

        Without this the client sees a half-open stream and waits forever, which
        is a worse failure mode than an error message.

        The terminal state is `failed`, not `completed` (#115): a crashed run
        used to end with `completed` carrying "I encountered an error…" in the
        artifact, which a subscriber cannot tell apart from a real answer.
        """
        from a2a.types import TaskArtifactUpdateEvent, TaskState, TaskStatusUpdateEvent
        from kubently.modules.a2a.protocol_bindings.a2a_server.agent_executor import (
            KubentlyAgentExecutor,
        )

        class _ExplodingAgent(_StubAgent):
            async def run(self, messages, thread_id=None, cluster_id=None):
                raise RuntimeError("llm unreachable")
                yield  # pragma: no cover - makes this an async generator

        executor = KubentlyAgentExecutor.__new__(KubentlyAgentExecutor)
        executor.redis_client = None
        executor.agent = _ExplodingAgent([])
        executor._active_sessions = {}
        executor._initialized = True

        event_queue = _event_queue()
        await executor.execute(_make_request_context("summarise cluster health"), event_queue)
        events = await _drain(event_queue)

        artifacts = [e for e in events if isinstance(e, TaskArtifactUpdateEvent)]
        assert len(artifacts) == 1
        assert "llm unreachable" in artifacts[0].artifact.parts[0].text

        assert isinstance(events[-1], TaskStatusUpdateEvent)
        assert events[-1].status.state == TaskState.TASK_STATE_FAILED
        assert events[-1].status.state in _stream_ending_states()
        # The reason is on the terminal status, where a client looks for it.
        assert "llm unreachable" in events[-1].status.message.parts[0].text


# =============================================================================
# Availability gating (issue #97)
# =============================================================================


class _RejectA2AImports:
    """Meta-path finder that makes every ``a2a`` import fail.

    Stands in for an incompatible SDK — a2a-sdk 1.x deletes ``a2a.server.apps``
    outright — without needing to install one.
    """

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "a2a" or fullname.startswith("a2a."):
            raise ImportError(f"simulated incompatible a2a-sdk: cannot import {fullname}")
        return None


class TestA2AAvailabilityIsFatal:
    """A missing or incompatible SDK must not be allowed to start the app quietly.

    Before #97 the only consequence of an SDK that would not import was
    ``A2A_AVAILABLE = False`` plus an INFO line, and ``create_a2a_server()``
    returning None. Whether A2A exists is now an explicit setting
    (``KUBENTLY_A2A``), and "expected but unavailable" raises.
    """

    def test_incompatible_sdk_raises_instead_of_degrading(self, monkeypatch):
        """Reload the module against an unimportable SDK: it must refuse to build."""
        import kubently.modules.a2a as a2a_module

        monkeypatch.delenv("KUBENTLY_A2A", raising=False)
        finder = _RejectA2AImports()
        cached = {
            name: mod
            for name, mod in sys.modules.items()
            if name == "a2a" or name.startswith("a2a.")
        }
        try:
            for name in cached:
                del sys.modules[name]
            sys.meta_path.insert(0, finder)
            broken = importlib.reload(a2a_module)

            assert broken.A2A_AVAILABLE is False, "the simulated SDK should not have imported"
            assert broken.A2A_IMPORT_ERROR is not None, (
                "the ImportError must be kept, not discarded: it is the only "
                "description of why A2A is unavailable"
            )

            error = None
            try:
                broken.create_a2a_server(external_url="https://example.test/a2a/")
            except Exception as exc:  # the type is asserted below
                error = exc

            assert error is not None, (
                "an unimportable a2a-sdk did not raise: create_a2a_server() handed back "
                "a value, so the API would start with the whole A2A surface missing"
            )
            assert type(error).__name__ == "A2AUnavailableError", type(error).__name__
            # The operator has to be able to see the cause, and the fix.
            assert "simulated incompatible a2a-sdk" in str(error)
            assert "KUBENTLY_A2A" in str(error)
            assert error.__cause__ is broken.A2A_IMPORT_ERROR
        finally:
            sys.meta_path.remove(finder)
            sys.modules.update(cached)
            importlib.reload(a2a_module)

    def test_explicitly_disabled_returns_none_without_raising(self, monkeypatch):
        """``KUBENTLY_A2A=off`` is the only way to get a None server back."""
        import kubently.modules.a2a as a2a_module

        monkeypatch.setenv("KUBENTLY_A2A", "off")
        assert a2a_module.a2a_enabled() is False
        assert a2a_module.create_a2a_server(external_url="https://example.test/a2a/") is None

    def test_enabled_by_default(self, monkeypatch):
        """No setting means A2A is expected: main.py mounts it and cannot skip it."""
        import kubently.modules.a2a as a2a_module

        monkeypatch.delenv("KUBENTLY_A2A", raising=False)
        assert a2a_module.a2a_enabled() is True
        assert a2a_module.create_a2a_server(external_url="https://example.test/a2a/") is not None

    def test_unavailable_sdk_is_tolerated_only_when_switched_off(self, monkeypatch):
        """Disabled + missing SDK is a deliberate configuration, not a failure."""
        import kubently.modules.a2a as a2a_module

        monkeypatch.setattr(a2a_module, "A2A_AVAILABLE", False)
        monkeypatch.setattr(
            a2a_module, "A2A_IMPORT_ERROR", ImportError("no module named 'a2a.server.routes'")
        )

        monkeypatch.setenv("KUBENTLY_A2A", "off")
        assert a2a_module.create_a2a_server(external_url="https://example.test/a2a/") is None

        monkeypatch.setenv("KUBENTLY_A2A", "on")
        with pytest.raises(a2a_module.A2AUnavailableError):
            a2a_module.create_a2a_server(external_url="https://example.test/a2a/")

    def test_main_startup_cannot_continue_without_a2a_when_enabled(self):
        """main.py must not treat a missing A2A server as recoverable.

        The mount block is the last place the failure could be swallowed, so
        assert on the compiled source of ``lifespan``: with A2A enabled there is
        no path from "no server" to a running app.
        """
        import inspect

        import kubently.main as main

        source = inspect.getsource(main.lifespan)
        mount_block = source.split("a2a_server = create_a2a_server")[1]
        assert "elif a2a_enabled():" in mount_block, (
            "main.py must distinguish 'A2A explicitly off' from 'A2A missing'; "
            "without that check a None server silently starts an API with no /a2a/"
        )
        assert "raise RuntimeError" in mount_block.split("else:")[0]
