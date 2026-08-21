import logging
import os
import re
import traceback
from typing import Any, override

import httpx
from a2a.helpers import new_task_from_user_message, new_text_artifact, new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import TaskArtifactUpdateEvent, TaskState, TaskStatus, TaskStatusUpdateEvent

# Always use real agent when LLM is configured
from kubently.modules.a2a.protocol_bindings.a2a_server.agent import KubentlyAgent

logger = logging.getLogger(__name__)


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
        """Run the agent, guaranteeing the event stream is never left empty.

        `message/stream` is served as SSE: the 200 and the `text/event-stream`
        headers are flushed before this coroutine runs. Anything that raised in
        here *before* the first `enqueue_event()` therefore reached the client
        as a 200 with a zero-length body — no events, no error, nothing (#65).
        `message/send` surfaced the very same failure as a JSON-RPC error,
        which is why only the streaming path looked broken.

        So: publish the task first, then run everything else under a guard that
        turns any failure into a visible error artifact and a terminal status
        event.
        """
        logger.info("=== KubentlyAgentExecutor.execute() CALLED ===")

        if not context.message:
            raise Exception("No message provided")

        task = context.current_task
        if not task:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)

        try:
            await self._execute(context, event_queue, task)
        except Exception as e:
            logger.exception("A2A execute() failed before the task reached a terminal state")
            await self._emit_failure(event_queue, task, e)

    async def _emit_failure(self, event_queue: EventQueue, task, error: Exception) -> None:
        """End a stream that failed outside the agent loop with a visible error."""
        message = f"Kubently could not run this request: {error}"
        try:
            await event_queue.enqueue_event(
                TaskArtifactUpdateEvent(
                    append=False,
                    context_id=task.context_id,
                    task_id=task.id,
                    last_chunk=True,
                    artifact=new_text_artifact(
                        name="debug_result",
                        description="Kubernetes debugging analysis and findings",
                        text=message,
                    ),
                )
            )
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    status=TaskStatus(
                        state=TaskState.TASK_STATE_FAILED,
                        message=new_text_message(
                            message, context_id=task.context_id, task_id=task.id
                        ),
                    ),
                    context_id=task.context_id,
                    task_id=task.id,
                )
            )
        except Exception:
            logger.exception("Failed to emit the terminal error event")

    async def _execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
        task,
    ) -> None:
        # Ensure agent is initialized in the correct event loop
        await self.initialize()

        query = context.get_user_input()

        # Use context_id from message like mas-agent-atlassian does
        contextId = context.message.context_id if context.message else None

        # Extract cluster ID from A2A metadata extension
        cluster_id = context.metadata.get("clusterId") if context.metadata else None

        # Debug logging to track context ID and cluster
        logger.info("Debug context IDs:")
        logger.info(f"  - context.context_id: {context.context_id}")
        logger.info(f"  - context.message.context_id: {contextId}")
        logger.info(f"  - task.context_id: {task.context_id if task else None}")
        logger.info(f"  - cluster_id (from metadata): {cluster_id}")

        logger.info(f"Using contextId: {contextId}, cluster_id: {cluster_id}")

        # Tool-call events are no longer polled here. They are drained inside
        # agent.run(), under the CALLER-NAMESPACED thread id it already holds
        # (agent._namespaced_thread_id), and yielded as `tool_call` chunks.
        # That is what makes them arrive BEFORE the answer they informed (#115),
        # and it removes the split-brain that made #63's silent-empty-result
        # possible: recording and querying now use one variable in one file.

        # Let the agent handle all queries including cluster discovery
        # The agent's memory and prompt will maintain context properly

        # Check for direct kubectl command short-circuit
        direct_result = await self._try_direct_kubectl(query, contextId)
        if direct_result:
            # Emit final artifact and completion
            await event_queue.enqueue_event(
                TaskArtifactUpdateEvent(
                    append=False,
                    context_id=task.context_id,
                    task_id=task.id,
                    last_chunk=True,
                    artifact=new_text_artifact(
                        name="debug_result",
                        description="Kubernetes debugging analysis and findings",
                        text=direct_result,
                    ),
                )
            )
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
                    context_id=task.context_id,
                    task_id=task.id,
                )
            )
            return

        # Build messages - trust the agent's memory and system prompt to handle context
        messages = [{"role": "user", "content": query}]

        # Stream results from the agent with error handling
        full_response = []
        # Set when the agent reports a failure. A run that dies must reach a
        # terminal `failed` status, not a `completed` one carrying an apology
        # as if it were an answer — a subscriber cannot tell those apart.
        failure: str | None = None

        try:
            logger.info(
                f"Starting agent execution for query: {query[:100]}, cluster_id: {cluster_id}"
            )
            chunk_count = 0
            async for chunk in self.agent.run(messages, thread_id=contextId, cluster_id=cluster_id):
                chunk_count += 1
                if not isinstance(chunk, dict):
                    chunk = {"type": "message", "content": str(chunk)}
                chunk_type = chunk.get("type") or "message"
                chunk_content = chunk.get("content") or ""

                if chunk_type == "token":
                    # A fragment of the answer as the model produces it. It is
                    # NOT accumulated into full_response: the agent sends the
                    # whole answer once more as a `message` chunk, and that is
                    # what the artifact is built from.
                    await self._emit_working(
                        event_queue, task, chunk_content, {"kubently/event": "token"}
                    )
                    continue

                if chunk_type == "tool_call":
                    # Two renderings of the same call: the legacy prose (which
                    # is what every deployed consumer parses today) on the text
                    # part, and the typed event in metadata for consumers that
                    # would rather read fields than regexes (#115).
                    logger.info(f"Tool call chunk {chunk_count}: {chunk_content[:120]}")
                    await self._emit_working(
                        event_queue,
                        task,
                        chunk_content,
                        {
                            "kubently/event": "tool_call",
                            "kubently/tool_call": chunk.get("tool_call") or {},
                        },
                    )
                    continue

                logger.info(f"Received chunk {chunk_count} ({chunk_type}): {chunk_content[:100]}")
                full_response.append(chunk_content)
                if chunk_type == "error":
                    failure = chunk_content

                # An answer already delivered as token frames is not repeated
                # here — it goes out once more only as the final artifact.
                if not (chunk.get("metadata") or {}).get("streamed"):
                    await self._emit_working(
                        event_queue, task, chunk_content, {"kubently/event": chunk_type}
                    )

            # Send final result
            final_response = "\n".join(full_response)
            logger.info(
                f"Agent execution completed. Chunks: {chunk_count}, Response: '{final_response[:200]}'"
            )
        except Exception as e:
            error_msg = f"Agent execution failed: {e!s}\n{traceback.format_exc()}"
            logger.error(error_msg)
            final_response = f"I encountered an error while processing your request: {e!s}"
            failure = final_response

        await event_queue.enqueue_event(
            TaskArtifactUpdateEvent(
                append=False,
                context_id=task.context_id,
                task_id=task.id,
                last_chunk=True,
                artifact=new_text_artifact(
                    name="debug_result",
                    description="Kubernetes debugging analysis and findings",
                    text=final_response,
                ),
            )
        )

        # Terminal state. a2a-sdk 1.x dropped TaskStatusUpdateEvent.final: the
        # stream ends when a terminal TaskState is emitted, and the v0.3
        # compatibility layer re-derives `final: true` from that for old
        # clients. So a terminal state here is the only thing keeping clients
        # from hanging — which is why the failure path emits one too, rather
        # than reporting a dead run as `completed`.
        if failure:
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    status=TaskStatus(
                        state=TaskState.TASK_STATE_FAILED,
                        message=new_text_message(
                            failure, context_id=task.context_id, task_id=task.id
                        ),
                    ),
                    context_id=task.context_id,
                    task_id=task.id,
                )
            )
            return

        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
                context_id=task.context_id,
                task_id=task.id,
            )
        )

    async def _emit_working(
        self,
        event_queue: EventQueue,
        task,
        text: str,
        metadata: dict | None = None,
    ) -> None:
        """One `working` status carrying text and, optionally, a typed event.

        A2A has no tool-call event type, so structured events ride in the
        `metadata` Struct on both the status update and its message (consumers
        read one or the other depending on which layer they parse). The text
        part is unchanged from what this executor always wrote, so nothing that
        reads only text notices the addition.
        """
        if not text:
            # An empty text part is a frame a consumer has to skip anyway, and
            # one that a "\n"-joining consumer would turn into a stray newline.
            return
        message = new_text_message(text, context_id=task.context_id, task_id=task.id)
        if metadata:
            message.metadata.update(metadata)
        event = TaskStatusUpdateEvent(
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING, message=message),
            context_id=task.context_id,
            task_id=task.id,
        )
        if metadata:
            event.metadata.update(metadata)
        await event_queue.enqueue_event(event)

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

    async def _try_direct_kubectl(self, query: str, context_id: str) -> str | None:
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
        except Exception:
            # If direct execution fails, fall back to LLM
            return None

        return None

    async def _ensure_session(self, cluster_id: str, context_id: str) -> str | None:
        """Ensure a session exists for the given cluster and context."""
        # Check if we already have a session for this cluster in this context
        if context_id in self._active_sessions and cluster_id in self._active_sessions[context_id]:
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
    ) -> dict[str, Any] | None:
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
