#!/usr/bin/env python3
"""
Contract tests pinning the a2a-sdk surface Kubently binds to.

Why this file exists: the A2A binding code (``kubently/modules/a2a/__init__.py``,
``agent_executor.py``, ``helpers.py``) is the only place Kubently touches
``a2a-sdk``, and until now none of it was exercised by a test — the sole
existing check (``test_tool_trace_thread_match.py``) reads ``agent_executor.py``
as *text* and regexes it, so it passes even when the module cannot be imported.
That made an a2a-sdk version bump completely unguarded.

Worse, ``kubently/modules/a2a/__init__.py`` wraps its SDK imports in a bare
``except Exception`` that sets ``A2A_AVAILABLE = False``. An SDK release that
moves or renames a symbol therefore does not crash the API — it silently starts
Kubently with the entire A2A protocol surface missing. A dependency bump could
ship that regression with every test still green.

Contracts under guard:
- Every module path and symbol the codebase imports from ``a2a`` still resolves.
- ``A2A_AVAILABLE`` is True, i.e. the SDK did not silently disable A2A.
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
    ("a2a.server.apps", "A2AStarletteApplication"),
    ("a2a.server.request_handlers", "DefaultRequestHandler"),
    ("a2a.server.tasks", "InMemoryTaskStore"),
    ("a2a.server.tasks", "PushNotificationSender"),
    ("a2a.server.agent_execution", "AgentExecutor"),
    ("a2a.server.agent_execution", "RequestContext"),
    ("a2a.server.events.event_queue", "EventQueue"),
    ("a2a.types", "AgentCapabilities"),
    ("a2a.types", "AgentCard"),
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
    ("a2a.types", "TextPart"),
    ("a2a.utils", "new_agent_text_message"),
    ("a2a.utils", "new_task"),
    ("a2a.utils", "new_text_artifact"),
]


@pytest.mark.parametrize("module_path,symbol", SDK_IMPORT_SURFACE)
def test_sdk_symbol_still_exists(module_path, symbol):
    """Every symbol the bindings import must still resolve in the installed SDK.

    A bump that relocates one of these (a2a-sdk 1.x moves the ``new_*`` helpers
    to ``a2a.helpers`` and deletes ``a2a.server.apps`` outright) fails here with
    the exact missing name rather than surfacing as a silent runtime downgrade.
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
    """The ``new_*`` helpers are called positionally, so argument order matters."""
    import inspect

    from a2a.utils import new_agent_text_message, new_task, new_text_artifact

    # agent_executor.py calls new_agent_text_message(text, contextId, taskId)
    params = list(inspect.signature(new_agent_text_message).parameters)
    assert params[:3] == ["text", "context_id", "task_id"], (
        f"new_agent_text_message signature changed to {params}; agent_executor.py "
        f"passes these three positionally."
    )

    # agent_executor.py calls new_text_artifact(name=..., description=..., text=...)
    params = set(inspect.signature(new_text_artifact).parameters)
    assert {"name", "text", "description"} <= params, (
        f"new_text_artifact no longer accepts name/text/description: {params}"
    )

    # agent_executor.py calls new_task(context.message)
    assert len(inspect.signature(new_task).parameters) >= 1


# =============================================================================
# helpers.py — event construction against real SDK types
# =============================================================================


def _make_task():
    """Build a minimal but valid SDK Task, the way new_task() would."""
    from a2a.types import Message, Part, Role, Task, TaskState, TaskStatus, TextPart

    return Task(
        id="task-1",
        contextId="ctx-1",
        status=TaskStatus(state=TaskState.submitted),
        history=[
            Message(
                messageId="msg-1",
                role=Role.user,
                parts=[Part(root=TextPart(text="why is my pod crashlooping?"))],
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

        assert task.status.state == TaskState.completed
        assert task.status.message is None
        assert len(task.artifacts) == 1
        assert task.artifacts[0].parts[0].root.text == "pod is OOMKilled"
        # timestamp must be set for the protocol to order events
        assert task.status.timestamp

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

        assert task.status.state == TaskState.input_required
        assert task.status.message is not None
        assert task.status.message.role == Role.agent
        assert task.status.message.parts[0].root.text == "which namespace?"
        # the clarifying question must land in history, or the next turn loses it
        assert len(task.history) == history_before + 1
        assert task.artifacts is None or task.artifacts == []


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
        assert status_event.status.state == TaskState.working
        assert status_event.final is False
        assert status_event.status.message.parts[0].root.text == "checking pods..."

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
        assert artifact_event.taskId == task.id
        assert artifact_event.contextId == task.contextId
        assert artifact_event.lastChunk is True
        assert artifact_event.append is False
        assert artifact_event.artifact.parts[0].root.text == "done: OOMKilled"

        assert status_event.status.state == TaskState.completed
        assert status_event.final is True, "completed stream must set final=True or clients hang"

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
        assert status_event.status.state == TaskState.input_required
        assert status_event.final is True


# =============================================================================
# Agent card
# =============================================================================


class TestAgentCardContract:
    """The agent card is the A2A discovery document — it must build and serialise."""

    def test_agent_card_builds_and_serialises(self):
        from kubently.modules.a2a import A2AModule

        module = A2AModule(host="127.0.0.1", port=8000, external_url="https://example.test/a2a/")
        card = module.get_agent_card()

        assert card.name == "Kubently Kubernetes Debugger"
        assert card.url == "https://example.test/a2a/"
        assert card.capabilities.streaming is True

        # The card is served as JSON over /.well-known — round-tripping it is the
        # real contract, and pydantic model changes in the SDK break it here.
        payload = card.model_dump(mode="json", exclude_none=True)
        assert payload["name"] == "Kubently Kubernetes Debugger"
        assert "text/plain" in payload["defaultInputModes"]
        assert [s["id"] for s in payload["skills"]] == [s.id for s in card.skills]

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
    from a2a.server.agent_execution import RequestContext
    from a2a.types import Message, MessageSendParams, Part, Role, TextPart

    message = Message(
        messageId="msg-1",
        role=Role.user,
        parts=[Part(root=TextPart(text=text))],
        contextId="ctx-1",
    )
    context = RequestContext(
        request=MessageSendParams(message=message),
        task_id="task-1",
        context_id="ctx-1",
    )
    if metadata is not None:
        # metadata carries the clusterId A2A extension the executor reads
        context._params.metadata = metadata
    return context


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
        from a2a.server.events.event_queue import EventQueue
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
        event_queue = EventQueue()

        await executor.execute(context, event_queue)
        events = await _drain(event_queue)

        # 1. the new Task must be published first, or the client has nothing to attach to
        assert isinstance(events[0], Task), f"first event was {type(events[0]).__name__}"

        # 2. every streamed chunk reaches the client as a non-final working update
        working = [
            e
            for e in events
            if isinstance(e, TaskStatusUpdateEvent) and e.status.state == TaskState.working
        ]
        streamed = [e.status.message.parts[0].root.text for e in working]
        assert "looking at pods" in streamed
        assert "found it" in streamed
        assert all(e.final is False for e in working)

        # 3. exactly one final artifact carrying the joined response
        artifacts = [e for e in events if isinstance(e, TaskArtifactUpdateEvent)]
        assert len(artifacts) == 1
        assert artifacts[0].lastChunk is True
        assert artifacts[0].artifact.parts[0].root.text == "looking at pods\nfound it"

        # 4. the stream terminates with final=True/completed, or clients hang forever
        assert isinstance(events[-1], TaskStatusUpdateEvent)
        assert events[-1].status.state == TaskState.completed
        assert events[-1].final is True

    @pytest.mark.asyncio
    async def test_cluster_id_from_metadata_reaches_the_agent(self):
        """clusterId arrives as an A2A metadata extension and must be forwarded."""
        from a2a.server.events.event_queue import EventQueue
        from kubently.modules.a2a.protocol_bindings.a2a_server.agent_executor import (
            KubentlyAgentExecutor,
        )

        executor = KubentlyAgentExecutor.__new__(KubentlyAgentExecutor)
        executor.redis_client = None
        executor.agent = _StubAgent([{"content": "ok"}])
        executor._active_sessions = {}
        executor._initialized = True

        context = _make_request_context("summarise cluster health", metadata={"clusterId": "prod"})
        await executor.execute(context, EventQueue())

        assert executor.agent.received["cluster_id"] == "prod"
        assert executor.agent.received["thread_id"] == "ctx-1"
        assert executor.agent.received["messages"] == [
            {"role": "user", "content": "summarise cluster health"}
        ]

    @pytest.mark.asyncio
    async def test_agent_failure_still_closes_the_stream(self):
        """An agent crash must still yield a final artifact + completed status.

        Without this the client sees a half-open stream and waits forever, which
        is a worse failure mode than an error message.
        """
        from a2a.server.events.event_queue import EventQueue
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

        event_queue = EventQueue()
        await executor.execute(_make_request_context("summarise cluster health"), event_queue)
        events = await _drain(event_queue)

        artifacts = [e for e in events if isinstance(e, TaskArtifactUpdateEvent)]
        assert len(artifacts) == 1
        assert "llm unreachable" in artifacts[0].artifact.parts[0].root.text

        assert isinstance(events[-1], TaskStatusUpdateEvent)
        assert events[-1].status.state == TaskState.completed
        assert events[-1].final is True
