import asyncio
import json
import logging
import os
import re
import traceback
from typing import Any, Dict, Optional

import httpx
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import TaskArtifactUpdateEvent, TaskState, TaskStatus, TaskStatusUpdateEvent
from a2a.utils import new_agent_text_message, new_task, new_text_artifact
from typing_extensions import override

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
        print("=== EXECUTE CALLED ===")  # Debug print to confirm execution
        logger.info("=== KubentlyAgentExecutor.execute() CALLED ===")
        
        # Ensure agent is initialized in the correct event loop
        await self.initialize()
        
        query = context.get_user_input()
        task = context.current_task
        
        # Use context_id from message like mas-agent-atlassian does
        contextId = context.message.context_id if context.message else None
        
        # Debug logging to track context ID
        logger.info(f"Debug context IDs:")
        logger.info(f"  - context.context_id: {context.context_id}")
        logger.info(f"  - context.message.context_id: {contextId}")
        logger.info(f"  - task.contextId: {task.contextId if task else None}")
        
        logger.info(f"Using contextId: {contextId}")

        if not context.message:
            raise Exception("No message provided")

        if not task:
            task = new_task(context.message)
            await event_queue.enqueue_event(task)

        # Let the agent handle all queries including cluster discovery
        # The agent's memory and prompt will maintain context properly

        # Check for direct kubectl command short-circuit
        direct_result = await self._try_direct_kubectl(query, contextId)
        if direct_result:
            # Emit final artifact and completion
            await event_queue.enqueue_event(
                TaskArtifactUpdateEvent(
                    append=False,
                    contextId=task.contextId,
                    taskId=task.id,
                    lastChunk=True,
                    artifact=new_text_artifact(
                        name="debug_result",
                        description="Kubernetes debugging analysis and findings",
                        text=direct_result,
                    ),
                )
            )
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    status=TaskStatus(state=TaskState.completed),
                    final=True,
                    contextId=task.contextId,
                    taskId=task.id,
                )
            )
            return

        # Build messages - trust the agent's memory and system prompt to handle context
        messages = [{"role": "user", "content": query}]

        # Stream results from the agent with error handling
        full_response = []
        last_tool_check_time = None
        
        # Import the interceptor
        from .tool_call_interceptor import get_tool_call_interceptor
        interceptor = get_tool_call_interceptor()
        
        try:
            logger.info(f"Starting agent execution for query: {query[:100]}")
            chunk_count = 0
            async for chunk in self.agent.run(messages, thread_id=contextId):
                chunk_count += 1
                # Chunk is a dict, extract content for logging
                chunk_str = str(chunk)[:100] if chunk else 'EMPTY'
                logger.info(f"Received chunk {chunk_count}: {chunk_str}")
                # Extract content from chunk dict
                chunk_content = chunk.get("content", "") if isinstance(chunk, dict) else str(chunk)
                full_response.append(chunk_content)

                # Send streaming update
                await event_queue.enqueue_event(
                    TaskStatusUpdateEvent(
                        status=TaskStatus(
                            state=TaskState.working,
                            message=new_agent_text_message(
                                chunk_content,
                                task.contextId,
                                task.id,
                            ),
                        ),
                        final=False,
                        contextId=task.contextId,
                        taskId=task.id,
                    )
                )
                
                # Check for new tool calls periodically
                from datetime import datetime
                current_time = datetime.now().isoformat()
                if last_tool_check_time is None or last_tool_check_time < current_time:
                    # Get tool calls since last check
                    tool_calls = await interceptor.get_tool_calls_for_thread(
                        contextId, 
                        since_timestamp=last_tool_check_time
                    )
                    
                    # Emit tool call events
                    for tool_call in tool_calls:
                        # Create a custom event for tool calls
                        # Since A2A doesn't have a specific tool call event, we'll use TaskStatusUpdateEvent
                        # with metadata in the message
                        tool_message = f"🔧 Tool Call: {tool_call['tool_name']}({json.dumps(tool_call.get('args', {}), indent=2)})"
                        if tool_call.get('status') == 'completed' and tool_call.get('result'):
                            tool_message += f"\n✅ Result: {tool_call['result'][:500]}..."
                        elif tool_call.get('error'):
                            tool_message += f"\n❌ Error: {tool_call['error']}"
                            
                        await event_queue.enqueue_event(
                            TaskStatusUpdateEvent(
                                status=TaskStatus(
                                    state=TaskState.working,
                                    message=new_agent_text_message(
                                        tool_message,
                                        task.contextId,
                                        task.id,
                                    ),
                                ),
                                final=False,
                                contextId=task.contextId,
                                taskId=task.id,
                            )
                        )
                    
                    last_tool_check_time = current_time

            # Send final result
            final_response = "\n".join(full_response)
            logger.info(f"Agent execution completed. Chunks: {chunk_count}, Response: '{final_response[:200]}'")
            
            # Emit any remaining tool calls
            final_tool_calls = await interceptor.get_tool_calls_for_thread(
                contextId, 
                since_timestamp=last_tool_check_time
            )
            for tool_call in final_tool_calls:
                tool_message = f"🔧 Tool Call: {tool_call['tool_name']}({json.dumps(tool_call.get('args', {}), indent=2)})"
                if tool_call.get('status') == 'completed' and tool_call.get('result'):
                    tool_message += f"\n✅ Result: {tool_call['result'][:500]}..."
                elif tool_call.get('error'):
                    tool_message += f"\n❌ Error: {tool_call['error']}"
                    
                await event_queue.enqueue_event(
                    TaskStatusUpdateEvent(
                        status=TaskStatus(
                            state=TaskState.working,
                            message=new_agent_text_message(
                                tool_message,
                                task.contextId,
                                task.id,
                            ),
                        ),
                        final=False,
                        contextId=task.contextId,
                        taskId=task.id,
                    )
                )
        except Exception as e:
            error_msg = f"Agent execution failed: {str(e)}\n{traceback.format_exc()}"
            logger.error(error_msg)
            final_response = f"I encountered an error while processing your request: {str(e)}"
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

        # Mark task as complete
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                status=TaskStatus(state=TaskState.completed),
                final=True,
                contextId=task.contextId,
                taskId=task.id,
            )
        )

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
