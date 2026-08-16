import asyncio
import contextlib
import json
import logging
import os
import re
import traceback
from typing import Any, Dict, Optional, Set, Tuple

import httpx
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import TaskArtifactUpdateEvent, TaskState, TaskStatus, TaskStatusUpdateEvent
from a2a.utils import new_agent_text_message, new_task, new_text_artifact
from typing_extensions import override

# Always use real agent when LLM is configured
from kubently.modules.a2a.protocol_bindings.a2a_server.agent import KubentlyAgent

logger = logging.getLogger(__name__)

# How often (seconds) to sweep the tool-call interceptor for new activity while the
# agent is thinking. The agent's own run() yields a single chunk at the very end, so
# without this sweep a multi-minute diagnosis emits nothing between the initial task
# event and the final artifact.
TOOL_POLL_INTERVAL = float(os.getenv("A2A_TOOL_POLL_INTERVAL", "1.0"))


class KubentlyAgentExecutor(AgentExecutor):
    """Kubently Kubernetes Debugging AgentExecutor."""

    def __init__(self, redis_client=None):
        self.redis_client = redis_client
        self.agent = KubentlyAgent(redis_client=redis_client)
        # Removed discovery patterns - agent's memory and system prompt handle context now
        # Track active sessions per context
        self._active_sessions = {}  # contextId -> {cluster_id: session_id}
        self._initialized = False
        logger.info("KubentlyAgentExecutor initialized")

    async def initialize(self):
        """Initialize the agent and its dependencies."""
        logger.info(f"KubentlyAgentExecutor.initialize() called, _initialized={self._initialized}")
        if not self._initialized:
            logger.info("Initializing KubentlyAgent...")
            await self.agent.initialize()
            self._initialized = True
            logger.info("KubentlyAgent initialization complete")
        else:
            logger.info("KubentlyAgentExecutor already initialized, skipping")

    @override
    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """Run a diagnosis, emitting task events as it goes.

        Two invariants make `message/stream` usable, and both are load-bearing:

        1. The task event is enqueued FIRST, before any work that can be slow or
           can fail. The SSE response headers (HTTP 200) are flushed by the SDK
           before this coroutine is ever awaited, so an exception raised before
           the first event produces a 200 with a zero-byte body — the connection
           just closes and the client is told nothing.
        2. NOTHING escapes this method. The SDK's streaming path only converts
           `ServerError` into a JSON-RPC error frame (see
           JSONRPCHandler.on_message_send_stream); any other exception propagates
           out of the SSE generator and, again, ends the stream with an empty
           body. Failures are reported as a `failed` task instead.
        """
        logger.info("=== KubentlyAgentExecutor.execute() CALLED ===")

        task = context.current_task
        try:
            if not context.message:
                raise ValueError("No message provided")

            if not task:
                task = new_task(context.message)
                # Emit immediately: this is the client's first byte, and it must
                # not be gated on agent initialization or the LLM round-trip.
                await event_queue.enqueue_event(task)

            final_response = await self._diagnose(context, event_queue, task)
            state = TaskState.completed
        except Exception as e:
            logger.error(f"Agent execution failed: {e}\n{traceback.format_exc()}")
            final_response = f"I encountered an error while processing your request: {e!s}"
            state = TaskState.failed
            if task is None:
                # Nothing to hang task events off (malformed request). A bare
                # agent message is still a terminal event, so the client gets a
                # real response rather than an empty stream.
                await event_queue.enqueue_event(new_agent_text_message(final_response))
                return

        await event_queue.enqueue_event(
            TaskArtifactUpdateEvent(
                append=False,
                contextId=task.contextId,
                taskId=task.id,
                lastChunk=True,
                artifact=new_text_artifact(
                    name="debug_result",
                    description="Kubernetes debugging analysis and findings",
                    text=final_response,
                ),
            )
        )

        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                status=TaskStatus(state=state),
                final=True,
                contextId=task.contextId,
                taskId=task.id,
            )
        )

    async def _diagnose(
        self,
        context: RequestContext,
        event_queue: EventQueue,
        task: Any,
    ) -> str:
        """Run the agent for this request and return its final text."""
        # Ensure agent is initialized in the correct event loop
        await self.initialize()

        query = context.get_user_input()

        # Use context_id from message like mas-agent-atlassian does. Fall back to
        # the task's contextId so the id the agent memoizes under is never None —
        # a None thread_id makes the agent invent a random one, which the
        # interceptor lookup below could then never match.
        contextId = (context.message.context_id if context.message else None) or task.contextId

        # Extract cluster ID from A2A metadata extension
        cluster_id = context.metadata.get("clusterId") if context.metadata else None

        logger.info(
            f"Using contextId: {contextId}, cluster_id: {cluster_id}, "
            f"task.contextId: {task.contextId}"
        )

        # Tool calls are recorded by the agent under the CALLER-NAMESPACED thread
        # id (see agent._namespaced_thread_id), so the interceptor must be queried
        # with the same value — matching is exact equality. Querying the raw
        # contextId silently returns [] and the "🔧 Tool Call" stream events
        # (which test-automation parses) vanish.
        from .agent import _namespaced_thread_id

        traceThreadId = _namespaced_thread_id(contextId)

        # Check for direct kubectl command short-circuit
        direct_result = await self._try_direct_kubectl(query, contextId)
        if direct_result:
            return direct_result

        # Build messages - trust the agent's memory and system prompt to handle context
        messages = [{"role": "user", "content": query}]

        # Sweep the interceptor for tool activity while the agent works, so the
        # client sees the investigation happening instead of waiting minutes for a
        # single artifact. `emitted` dedupes across the poller and the final flush;
        # seeding it with what is already buffered keeps an earlier turn's calls
        # (the interceptor buffer is per-thread, not per-turn) out of this stream.
        emitted: Set[Tuple[str, str]] = await self._tool_call_keys(traceThreadId)
        poller = asyncio.create_task(
            self._poll_tool_calls(event_queue, task, traceThreadId, emitted)
        )

        full_response = []
        chunk_count = 0
        try:
            logger.info(
                f"Starting agent execution for query: {query[:100]}, cluster_id: {cluster_id}"
            )
            async for chunk in self.agent.run(messages, thread_id=contextId, cluster_id=cluster_id):
                chunk_count += 1
                logger.info(f"Received chunk {chunk_count}: {str(chunk)[:100] if chunk else 'EMPTY'}")
                chunk_content = chunk.get("content", "") if isinstance(chunk, dict) else str(chunk)
                full_response.append(chunk_content)

                # Flush pending tool activity before the narrative chunk so the
                # transcript reads in the order things happened.
                await self._emit_tool_calls(event_queue, task, traceThreadId, emitted)
                await self._emit_working(event_queue, task, chunk_content)
        finally:
            poller.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await poller

        # Emit any tool calls that landed (or completed) after the last sweep
        await self._emit_tool_calls(event_queue, task, traceThreadId, emitted)

        final_response = "\n".join(full_response)
        logger.info(
            f"Agent execution completed. Chunks: {chunk_count}, "
            f"Response: '{final_response[:200]}'"
        )
        return final_response

    async def _emit_working(self, event_queue: EventQueue, task: Any, text: str) -> None:
        """Emit a non-final `working` status update carrying `text`."""
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                status=TaskStatus(
                    state=TaskState.working,
                    message=new_agent_text_message(text, task.contextId, task.id),
                ),
                final=False,
                contextId=task.contextId,
                taskId=task.id,
            )
        )

    async def _poll_tool_calls(
        self,
        event_queue: EventQueue,
        task: Any,
        thread_id: Optional[str],
        emitted: Set[Tuple[str, str]],
    ) -> None:
        """Emit tool activity as it is recorded, until cancelled.

        Runs alongside the agent because `agent.run()` is a single long await that
        yields only once, at the end — this loop is what makes the stream
        incremental rather than a silent wait followed by one artifact.
        """
        while True:
            await asyncio.sleep(TOOL_POLL_INTERVAL)
            try:
                await self._emit_tool_calls(event_queue, task, thread_id, emitted)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # never let progress reporting kill the stream
                logger.warning(f"Tool call progress sweep failed: {e}")

    async def _emit_tool_calls(
        self,
        event_queue: EventQueue,
        task: Any,
        thread_id: Optional[str],
        emitted: Set[Tuple[str, str]],
    ) -> None:
        """Emit any tool calls for `thread_id` not already emitted at this status.

        Keyed by (call id, status) so a call surfaces twice: once when it starts
        (so the client sees the step immediately) and once when it resolves (with
        the output). Only the resolved event uses the "🔧 Tool Call:" prefix that
        test-automation counts, so per-call counts stay accurate.
        """
        if not thread_id:
            return

        from .tool_call_interceptor import get_tool_call_interceptor

        interceptor = get_tool_call_interceptor()
        for tool_call in await interceptor.get_tool_calls_for_thread(thread_id):
            key = (tool_call.get("id", ""), tool_call.get("status", ""))
            if key in emitted:
                continue
            emitted.add(key)
            await self._emit_working(event_queue, task, self._format_tool_call(tool_call))

    async def _tool_call_keys(self, thread_id: Optional[str]) -> Set[Tuple[str, str]]:
        """(call id, status) pairs already buffered for `thread_id`."""
        if not thread_id:
            return set()

        from .tool_call_interceptor import get_tool_call_interceptor

        calls = await get_tool_call_interceptor().get_tool_calls_for_thread(thread_id)
        return {(c.get("id", ""), c.get("status", "")) for c in calls}

    @staticmethod
    def _format_tool_call(tool_call: Dict[str, Any]) -> str:
        """Render a tool call for the stream.

        The "🔧 Tool Call: name(args)" shape is parsed by test-automation, so it
        marks exactly one completed call. In-flight calls get a distinct prefix.
        """
        args = json.dumps(tool_call.get("args", {}), indent=2)
        name = tool_call.get("tool_name")
        if tool_call.get("status") == "started":
            return f"⏳ Running: {name}({args})"

        message = f"🔧 Tool Call: {name}({args})"
        if tool_call.get("status") == "completed" and tool_call.get("result"):
            message += f"\n✅ Result: {tool_call['result'][:500]}..."
        elif tool_call.get("error"):
            message += f"\n❌ Error: {tool_call['error']}"
        return message

    @override
    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise Exception("cancel not supported")

    # Removed _is_discovery_intent, _maybe_prefetch_discovery, _fetch_clusters_from_redis,
    # and _fetch_clusters_preface methods - agent's memory handles context now

    async def _fetch_clusters_list(self) -> list[str] | None:
        # Get API key for internal service-to-service calls using auth module utility
        from kubently.modules.auth import AuthModule
        api_key = AuthModule.extract_first_api_key()

        candidates = [
            os.getenv("KUBENTLY_API_URL", "http://localhost:8080"),
            "http://localhost:8080",
        ]
        for base in candidates:
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        f"{base}/debug/clusters", headers={"X-Api-Key": api_key}, timeout=5
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        return data.get("clusters", [])
            except Exception:
                continue
        # No clusters available if API is unreachable
        return []

    async def _try_direct_kubectl(self, query: str, context_id: str) -> Optional[str]:
        """
        Try to execute kubectl commands directly when cluster is explicit.
        Returns the formatted result if successful, None otherwise.
        """
        text = query.strip().lower()

        # Pattern matching for explicit cluster kubectl commands
        # Examples: "show pods in kind cluster", "get pods from kubently", "kind cluster pods"
        patterns = [
            r"(?:show|get|list)\s+(\w+)\s+(?:in|from|on)\s+(\w+)\s+cluster",
            r"(\w+)\s+cluster\s+(pods|deployments?|services?|nodes|namespaces)",
            r"cluster\s+(\w+)\s+(pods|deployments?|services?|nodes|namespaces)",
        ]

        resource = None
        cluster_id = None

        # Try to extract resource and cluster from the query
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                if len(match.groups()) == 2:
                    # Determine which group is resource vs cluster
                    g1, g2 = match.groups()
                    if g1 in [
                        "pods",
                        "pod",
                        "deployments",
                        "deployment",
                        "services",
                        "service",
                        "nodes",
                        "node",
                        "namespaces",
                        "namespace",
                    ]:
                        resource = g1
                        cluster_id = g2
                    else:
                        cluster_id = g1
                        resource = g2
                break

        # Also check for simple "pods in <cluster>" pattern
        if not cluster_id:
            simple_match = re.search(
                r"(pods|deployments?|services?|nodes|namespaces)\s+(?:in|from|on)\s+(\w+)", text
            )
            if simple_match:
                resource = simple_match.group(1)
                cluster_id = simple_match.group(2)

        if not cluster_id or not resource:
            return None

        # Normalize resource name
        resource_map = {
            "pod": "pods",
            "pods": "pods",
            "deployment": "deployments",
            "deployments": "deployments",
            "service": "services",
            "services": "services",
            "node": "nodes",
            "nodes": "nodes",
            "namespace": "namespaces",
            "namespaces": "namespaces",
        }
        resource = resource_map.get(resource, resource)

        # Execute the command directly via API
        try:
            # Get or create session for this cluster
            session_id = await self._ensure_session(cluster_id, context_id)
            if not session_id:
                return None

            # Execute kubectl command
            result = await self._execute_kubectl_direct(
                session_id, cluster_id, "get", [resource, "-A"]
            )
            if result and result.get("status") == "success":
                output = result.get("output", "")
                return f"Cluster: {cluster_id}\n\n{output}"
        except Exception as e:
            # If direct execution fails, fall back to LLM
            return None

        return None

    async def _ensure_session(self, cluster_id: str, context_id: str) -> Optional[str]:
        """Ensure a session exists for the given cluster and context."""
        # Check if we already have a session for this cluster in this context
        if context_id in self._active_sessions:
            if cluster_id in self._active_sessions[context_id]:
                return self._active_sessions[context_id][cluster_id]

        # Create a new session
        from kubently.modules.auth import AuthModule
        api_key = AuthModule.extract_first_api_key()
        api_url = os.getenv("KUBENTLY_API_URL", "http://localhost:8080")

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{api_url}/debug/session",
                    json={
                        "cluster_id": cluster_id,
                        "correlation_id": context_id,
                    },
                    headers={"X-Api-Key": api_key},
                    timeout=5,
                )
                if resp.status_code == 201:
                    data = resp.json()
                    session_id = data.get("session_id")

                    # Cache the session
                    if context_id not in self._active_sessions:
                        self._active_sessions[context_id] = {}
                    self._active_sessions[context_id][cluster_id] = session_id

                    return session_id
        except Exception:
            pass

        return None

    async def _execute_kubectl_direct(
        self, session_id: str, cluster_id: str, command_type: str, args: list
    ) -> Optional[Dict[str, Any]]:
        """Execute kubectl command directly via API."""
        from kubently.modules.auth import AuthModule
        api_key = AuthModule.extract_first_api_key()
        api_url = os.getenv("KUBENTLY_API_URL", "http://localhost:8080")

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{api_url}/debug/execute",
                    json={
                        "session_id": session_id,
                        "cluster_id": cluster_id,
                        "command_type": command_type,
                        "args": args,
                        "timeout_seconds": 10,
                    },
                    headers={"X-Api-Key": api_key},
                    timeout=15,
                )
                if resp.status_code == 200:
                    return resp.json()
        except Exception:
            pass

        return None
