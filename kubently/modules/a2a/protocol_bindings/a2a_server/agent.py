import json
import logging
import os
import uuid
from collections.abc import AsyncIterable
from typing import Any, List, Dict
from datetime import datetime, timezone

import httpx
from cnoe_agent_utils import LLMFactory
from langchain_core.messages import AIMessage, ToolMessage, SystemMessage, HumanMessage
from langchain_core.runnables.config import RunnableConfig
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field

from .tool_call_interceptor import get_tool_call_interceptor

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


def debug_print(message: str, banner: bool = True):
    if os.getenv("A2A_SERVER_DEBUG", "false").lower() == "true":
        if banner:
            print("=" * 80)
        print(f"DEBUG: {message}")
        if banner:
            print("=" * 80)


def structured_log(log_data: dict, thread_id: str = None):
    """Log structured data when A2A_SERVER_DEBUG is enabled.
    
    Args:
        log_data: Dictionary containing log data
        thread_id: Optional thread ID to include in the log
    """
    if os.getenv("A2A_SERVER_DEBUG", "false").lower() == "true":
        # Add thread ID if provided
        if thread_id:
            log_data["thread_id"] = thread_id
        
        # Add timestamp
        import datetime
        log_data["timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        
        # Log as formatted JSON
        logger.info(json.dumps(log_data, indent=2, default=str))


# Dangerous kubectl verbs that require explicit permission
DANGEROUS_VERBS = {
    "delete", "create", "apply", "patch", "edit", "scale",
    "cordon", "drain", "uncordon", "taint", "label", "annotate",
    "set", "autoscale", "rollout", "expose", "run"
}

def validate_kubectl_command(command: str, allow_write: bool = False) -> bool:
    """Validate kubectl command for safety."""
    parts = command.split()
    if not parts:
        return False

    verb = parts[0]

    # Check dangerous verbs
    if verb in DANGEROUS_VERBS and not allow_write:
        raise ValueError(
            f"Dangerous verb '{verb}' blocked. This tool is read-only. "
            f"Use commands like: get, describe, logs, exec, port-forward"
        )

    return True

def parse_kubectl_command(command: str) -> dict:
    """Parse kubectl command for structured logging."""
    parts = command.split()
    result = {
        "verb": parts[0] if parts else None,
        "resource": None,
        "name": None,
        "namespace": "default",
        "flags": []
    }

    # Basic parsing logic
    if len(parts) > 1:
        result["resource"] = parts[1]
    if len(parts) > 2 and not parts[2].startswith("-"):
        result["name"] = parts[2]

    # Extract namespace
    if "-n" in parts:
        idx = parts.index("-n")
        if idx + 1 < len(parts):
            result["namespace"] = parts[idx + 1]
    elif "--namespace" in parts:
        idx = parts.index("--namespace")
        if idx + 1 < len(parts):
            result["namespace"] = parts[idx + 1]

    # Extract flags
    result["flags"] = [p for p in parts if p.startswith("-")]

    return result


class KubentlyAgent:
    """Kubernetes Debugging Agent - Enhanced with thorough investigation."""

    SUPPORTED_CONTENT_TYPES = ["text/plain", "application/json"]

    def __init__(self, redis_client=None):
        """Initialize the Kubently agent."""
        self.redis_client = redis_client
        self.llm = None
        self.tools = []
        self.agent = None
        # Memory will be initialized in async context
        self.memory = None
        self._initialized = False
        self.system_prompt = None
        # Investigation tracking
        self.investigation_steps = []
        self.min_investigation_steps = 4  # Minimum steps for thoroughness
        self._current_thread_id = None

    async def track_investigation_step(self, command: str, purpose: str, findings: str):
        """Track each investigation step for thoroughness."""
        self.investigation_steps.append({
            "command": command,
            "purpose": purpose,
            "findings": findings,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

        # Log structured data for analysis
        structured_log({
            "investigation_step": len(self.investigation_steps),
            "command": command,
            "purpose": purpose
        }, thread_id=self._current_thread_id)

    def should_continue_investigation(self, steps_taken: int) -> bool:
        """Encourage continued investigation."""
        if steps_taken < self.min_investigation_steps:
            return True

        # Check if recent findings suggest more investigation needed
        recent_findings = self.investigation_steps[-2:] if len(self.investigation_steps) >= 2 else []

        # Continue if recent steps revealed new questions
        for step in recent_findings:
            if any(keyword in step["findings"].lower() for keyword in
                   ["unclear", "need to check", "verify", "confirm", "strange", "unexpected"]):
                return True

        return False

    async def initialize(self):
        """Initialize the agent with LLM and tools."""
        if self._initialized and self.memory is not None:
            return

        if self._initialized and self.memory is None:
            logger.info("Agent initialized but memory failed previously, retrying memory setup...")

        # Initialize memory in async context
        memory_initialized = False
        try:
            import redis.asyncio as redis_async

            if self.redis_client:
                # Test Redis connection first
                await self.redis_client.ping()
                self.memory = AsyncRedisSaver(redis_client=self.redis_client)
                # Setup is required to initialize indices
                await self.memory.setup()
                logger.info("AsyncRedisSaver initialized and setup successfully")
                memory_initialized = True
        except Exception as e:
            logger.warning(f"Failed to initialize AsyncRedisSaver: {e}")
            logger.warning("Continuing without memory persistence")
            self.memory = None

        # Initialize LLM using factory
        llm_factory = LLMFactory()
        self.llm = llm_factory.get_llm()

        # Load system prompt from configuration
        from kubently.modules.config import get_prompt

        self.system_prompt = get_prompt(role="a2a")

        # Initialize tools for kubectl operations
        await self._initialize_tools()

        # Create the single ReAct agent with externalized system prompt
        # Only use checkpointer if we have a Redis connection for centralized state
        self.agent = create_react_agent(
            self.llm,
            tools=self.tools,
            checkpointer=self.memory if self.memory else None,
            prompt=self.system_prompt,
        )

        self._initialized = True
        logger.info("KubentlyAgent initialized successfully with enhanced investigation")

    async def _initialize_tools(self):
        """Initialize kubectl tools."""
        # Get API URL from environment - use internal service for tool calls
        api_url = os.getenv("KUBENTLY_API_URL", "http://localhost:8080")

        # Use the auth module utility to extract API key (handles service:key format)
        from kubently.modules.auth import AuthModule
        api_key = AuthModule.extract_first_api_key()

        # Create tool functions for kubectl operations
        from langchain_core.tools import tool

        # Get the tool call interceptor
        interceptor = get_tool_call_interceptor()
        
        @tool
        async def list_clusters() -> str:
            """List all available Kubernetes clusters.
            
            Use this tool when the user doesn't specify a cluster to get a list of available options.
            
            Returns:
                List of available cluster IDs
            """
            debug_print("list_clusters called")
            
            # Record tool call
            tool_call_id = await interceptor.record_tool_call(
                tool_name="list_clusters",
                args={},
                thread_id=getattr(self, '_current_thread_id', None)
            )
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                try:
                    response = await client.get(
                        f"{api_url}/debug/clusters",
                        headers={"X-Api-Key": api_key},
                    )
                    if response.status_code == 200:
                        result = response.json()
                        logger.info(f"list_clusters API response: {result}")
                        clusters = result.get("clusters", [])
                        logger.info(f"Available clusters: {clusters}")
                        debug_print(f"Available clusters: {clusters}")
                        if clusters:
                            output = f"Available clusters: {', '.join(clusters)}. Please specify which cluster you want to use."
                        else:
                            output = "No clusters are currently available."
                        await interceptor.record_tool_result(tool_call_id, output)
                        return output
                    else:
                        error_msg = f"Error listing clusters: HTTP {response.status_code}"
                        await interceptor.record_tool_result(tool_call_id, None, error_msg)
                        return error_msg
                except Exception as e:
                    error_msg = f"Error listing clusters: {str(e)}"
                    await interceptor.record_tool_result(tool_call_id, None, error_msg)
                    return error_msg

        @tool
        async def execute_kubectl(
            cluster_id: str,
            command: str,
            namespace: str = "default",
            extra_args: list[str] | None = None
        ) -> str:
            """Execute any kubectl command for thorough Kubernetes investigation.

            This is your primary tool for all Kubernetes operations. Use it liberally
            to explore, investigate, and verify. You have access to all kubectl commands
            and flags.

            PREFERRED OUTPUT FORMATS:
            - Use "-o json" for structured data that's easier to parse and analyze
            - Use "-o yaml" when you need full configuration details
            - Use "-o wide" for quick overview with more columns
            - Default output is good for human-readable summaries

            Examples:
            - JSON output (PREFERRED): "get pod pod-name -o json"
            - Get all pods as JSON: "get pods -o json"
            - With namespace: "get pods -n kube-system -o json"
            - Wide format: "get pods -o wide"
            - Logs: "logs pod-name --tail=50"
            - Describe: "describe pod pod-name"
            - Events as JSON: "get events --sort-by='.lastTimestamp' -o json"
            - Custom columns: "get pods -o custom-columns=NAME:.metadata.name,STATUS:.status.phase"
            - With labels: "get pods --show-labels"
            - Selectors: "get pods -l app=nginx -o json"
            - Endpoints: "get endpoints service-name -o json"

            IMPORTANT: Use multiple commands to build complete understanding.
            Don't assume - verify everything with additional commands.
            Prefer JSON output (-o json) for better parsing and analysis.

            Common investigation patterns:
            - kubectl get <resource> -n <namespace> -o json (for structured data)
            - kubectl describe <resource> <name> -n <namespace> (for detailed events)
            - kubectl get events -n <namespace> --sort-by='.lastTimestamp' -o json
            - kubectl logs <pod> -n <namespace> --tail=50
            - kubectl get endpoints <service> -n <namespace> -o json
            - kubectl get <resource> -o yaml -n <namespace> (for full config)

            Args:
                cluster_id: Target cluster
                command: Full kubectl command (verb, resource, flags)
                namespace: Default namespace (used if -n not in command)
                extra_args: Additional safe arguments

            Returns:
                Command output (stdout + stderr)
            """
            # Validate command safety
            try:
                validate_kubectl_command(command, allow_write=False)
            except ValueError as e:
                return str(e)

            # Parse the command for structured logging
            cmd_info = parse_kubectl_command(command)

            # Build the command parts
            command_parts = command.split()

            # Extract the verb and rest of the command
            if not command_parts:
                return "Error: Empty kubectl command"

            verb = command_parts[0]

            # Handle namespace if not specified in command
            if "-n" not in command_parts and "--namespace" not in command_parts and namespace != "default":
                # Add namespace unless it's already specified
                if namespace == "all":
                    command_parts.append("-A")
                else:
                    command_parts.extend(["-n", namespace])

            debug_print(
                f"execute_kubectl called: cluster_id={cluster_id}, command={' '.join(command_parts)}, namespace={namespace}"
            )

            # Record tool call with parsed info
            tool_call_id = await interceptor.record_tool_call(
                tool_name="execute_kubectl",
                args={
                    "cluster_id": cluster_id,
                    "command": command,
                    "namespace": namespace,
                    "extra_args": extra_args,
                    "parsed": cmd_info
                },
                thread_id=getattr(self, '_current_thread_id', None)
            )

            # Track investigation step
            await self.track_investigation_step(
                command=' '.join(command_parts),
                purpose=f"Execute: {verb} {cmd_info.get('resource', '')}",
                findings="Pending"
            )

            async with httpx.AsyncClient(timeout=30.0) as client:
                try:
                    # Prepare the API payload
                    # Command is the verb, rest are args
                    if len(command_parts) > 1:
                        args = command_parts[1:]
                    else:
                        args = []

                    # Fix namespace handling
                    actual_namespace = None
                    if "-n" in command_parts:
                        idx = command_parts.index("-n")
                        if idx + 1 < len(command_parts):
                            actual_namespace = command_parts[idx + 1]
                    elif "--namespace" in command_parts:
                        idx = command_parts.index("--namespace")
                        if idx + 1 < len(command_parts):
                            actual_namespace = command_parts[idx + 1]
                    elif namespace != "all":
                        actual_namespace = namespace

                    payload = {
                        "cluster_id": cluster_id,
                        "command_type": verb,
                        "args": args,
                        "namespace": actual_namespace,
                        "timeout_seconds": 30,  # Increased timeout for thorough investigation
                    }
                    if extra_args:
                        payload["extra_args"] = extra_args
                    debug_print(f"Sending API request: {payload}")

                    response = await client.post(
                        f"{api_url}/debug/execute",
                        headers={"X-Api-Key": api_key},
                        json=payload,
                    )

                    debug_print(
                        f"API response: status={response.status_code}, text={response.text[:200]}..."
                    )
                    if response.status_code == 200:
                        result = response.json()
                        output = result.get("output", "")
                        debug_print(f"Tool successful: {output[:100]}...")

                        # Update investigation tracking with findings
                        if self.investigation_steps:
                            self.investigation_steps[-1]["findings"] = output[:200] if output else "No output"

                        await interceptor.record_tool_result(tool_call_id, output)
                        return output
                    else:
                        error_msg = f"Error: HTTP {response.status_code}: {response.text}"
                        debug_print(f"Tool failed: {error_msg}")
                        await interceptor.record_tool_result(tool_call_id, None, error_msg)
                        return error_msg

                except Exception as e:
                    error_msg = f"Error executing command: {str(e)}"
                    await interceptor.record_tool_result(tool_call_id, None, error_msg)
                    return error_msg

        @tool
        async def todo_write(todos: List[Dict[str, str]]) -> str:
            """Manage debugging workflow tasks to track systematic investigation progress.

            This tool helps you plan and track your debugging steps, ensuring thorough
            investigation and giving visibility into your progress.

            Args:
                todos: List of todo items with fields:
                    - content: Task description (e.g., "Check pod status")
                    - activeForm: Present continuous form (e.g., "Checking pod status")
                    - status: "pending", "in_progress", or "completed"

            Example:
                [
                    {"content": "Check pod events", "activeForm": "Checking pod events", "status": "in_progress"},
                    {"content": "Examine pod logs", "activeForm": "Examining pod logs", "status": "pending"},
                    {"content": "Verify service endpoints", "activeForm": "Verifying service endpoints", "status": "pending"}
                ]

            Returns:
                Confirmation message with progress summary
            """
            from .todo_manager import TodoManager

            # Record tool call
            tool_call_id = await interceptor.record_tool_call(
                tool_name="todo_write",
                args={"todos": todos},
                thread_id=getattr(self, '_current_thread_id', None)
            )

            try:
                # Get or create todo manager for this thread
                thread_id = getattr(self, '_current_thread_id', 'default')
                if not hasattr(self, '_todo_managers'):
                    self._todo_managers = {}

                if thread_id not in self._todo_managers:
                    self._todo_managers[thread_id] = TodoManager(thread_id)

                manager = self._todo_managers[thread_id]

                # Clear existing todos and add new ones
                manager.todos.clear()
                manager._todo_counter = 0

                for todo_dict in todos:
                    manager.add_todo(
                        content=todo_dict.get("content", ""),
                        activeForm=todo_dict.get("activeForm", todo_dict.get("content", "")),
                        status=todo_dict.get("status", "pending")
                    )

                # Get progress summary
                summary = manager.get_progress_summary()
                formatted_list = manager.format_for_display()

                result = f"✅ Todo list updated successfully\n\n{formatted_list}"

                await interceptor.record_tool_result(tool_call_id, result)
                return result

            except Exception as e:
                error_msg = f"Error updating todo list: {str(e)}"
                await interceptor.record_tool_result(tool_call_id, None, error_msg)
                return error_msg

        # Include all tools
        self.tools = [list_clusters, execute_kubectl, todo_write]
        logger.info(f"Initialized {len(self.tools)} tools")

    async def run(
        self,
        messages: list[dict],
        thread_id: str | None = None,
        context_id: str | None = None,
    ) -> AsyncIterable[dict]:
        """Run the agent and stream responses."""
        await self.initialize()

        # Store thread ID for tool call tracking
        self._current_thread_id = thread_id

        # Convert messages to LangChain format
        lc_messages = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if isinstance(content, list):
                # Handle multi-part messages
                text_parts = [
                    p.get("text", "") for p in content if p.get("type") == "text"
                ]
                content = " ".join(text_parts)

            if role == "user":
                lc_messages.append(HumanMessage(content=content))
            elif role == "assistant":
                lc_messages.append(AIMessage(content=content))
            elif role == "system":
                lc_messages.append(SystemMessage(content=content))

        # Use thread_id for memory if available
        actual_thread_id = thread_id or str(uuid.uuid4())
        logger.info(f"Agent.run called with thread_id: {actual_thread_id}, memory enabled: {self.memory is not None}")

        # Implement conversation history management to prevent token overflow
        # Maximum number of recent messages to keep (including tool calls)
        # This prevents the "prompt is too long" error after /clear
        # Note: Each message can include large tool outputs (kubectl JSON, logs, etc.)
        # which can be 10K+ tokens each, so we keep this conservative
        MAX_MESSAGES = 10  # Keep last 10 messages (approximately 3-5 conversation turns with tools)

        # If we have a checkpointer, we need to manage history size
        # The checkpointer automatically loads ALL history, which can exceed token limits
        if self.memory:
            try:
                # Get the current checkpoint state
                checkpoint_tuple = await self.memory.aget_tuple({"configurable": {"thread_id": actual_thread_id}})

                if checkpoint_tuple and checkpoint_tuple.checkpoint:
                    # Extract messages from checkpoint
                    channel_values = checkpoint_tuple.checkpoint.get("channel_values", {})
                    existing_messages = channel_values.get("messages", [])

                    message_count = len(existing_messages)
                    logger.info(f"Thread {actual_thread_id} has {message_count} messages in history")

                    # If history is too long, delete the checkpoint to start fresh
                    # This is the most reliable way to prevent token overflow
                    if message_count > MAX_MESSAGES:
                        logger.warning(f"History too long ({message_count} messages), clearing checkpoint for thread {actual_thread_id}")

                        # Delete all checkpoints for this thread to start fresh
                        # This simulates what /clear should do
                        redis_key_pattern = f"{actual_thread_id}*"
                        keys_deleted = 0

                        # Use scan_iter to find and delete all keys for this thread
                        async for key in self.redis_client.scan_iter(match=redis_key_pattern):
                            await self.redis_client.delete(key)
                            keys_deleted += 1

                        logger.info(f"Cleared {keys_deleted} checkpoint keys for thread {actual_thread_id}")

            except Exception as e:
                logger.warning(f"Failed to manage conversation history: {e}")
                # Continue anyway - better to have long history than crash

        config = RunnableConfig(
            configurable={"thread_id": actual_thread_id},
            recursion_limit=25,  # Standard recursion limit for single agent
        )

        # Log the prompt being sent
        structured_log({
            "event": "llm_prompt",
            "messages": [{"role": m.__class__.__name__, "content": m.content[:200] if hasattr(m, 'content') else str(m)[:200]} for m in lc_messages],
            "thread_id": actual_thread_id,
            "message_count": len(lc_messages)
        })

        try:
            # Run the single agent
            result = await self.agent.ainvoke(
                {"messages": lc_messages},
                config=config
            )
            
            # Extract the final message
            final_messages = result.get("messages", [])
            if final_messages:
                last_message = final_messages[-1]
                
                # Handle response
                if isinstance(last_message, AIMessage):
                    response_text = last_message.content
                    
                    # Check for empty response (happens with various LLM providers)
                    if not response_text or not response_text.strip():
                        # Log the issue for debugging
                        logger.warning("LLM returned empty response after tool execution")

                        # Provide a clear, honest message that doesn't mislead
                        response_text = (
                            "⚠️ No model summary available. "
                            "The diagnostic tools have been executed - please review the tool outputs above for findings.\n\n"
                            "Tool executions completed:\n"
                        )

                        # Add a simple list of what tools were executed (not trying to interpret results)
                        tool_summary = []
                        for msg in final_messages[-10:]:  # Look at recent messages only
                            if hasattr(msg, 'content'):
                                content = str(msg.content)
                                if "kubectl" in content and "✅" in content:
                                    # Extract just the kubectl command that was run
                                    if "execute_kubectl" in content:
                                        tool_summary.append("• Executed kubectl commands")
                                        break

                        if tool_summary:
                            response_text += "\n".join(tool_summary)

                        # Add a note about checking raw outputs
                        response_text += "\n\nPlease review the raw command outputs above to understand the issue."
                    
                    yield {
                        "type": "message",
                        "content": response_text,
                        "metadata": {"thread_id": actual_thread_id}
                    }
                else:
                    # Fallback response
                    yield {
                        "type": "message", 
                        "content": "I can help you debug Kubernetes issues. Please specify which cluster you want to examine, or I can list the available clusters for you.",
                        "metadata": {"thread_id": actual_thread_id}
                    }
            else:
                # No messages returned
                yield {
                    "type": "message",
                    "content": "I can help you debug Kubernetes issues. Please specify which cluster you want to examine, or I can list the available clusters for you.",
                    "metadata": {"thread_id": actual_thread_id}
                }
                
        except Exception as e:
            logger.error(f"Error in agent.run: {e}", exc_info=True)
            yield {
                "type": "error",
                "content": f"I encountered an error while processing your request: {str(e)}",
                "metadata": {"thread_id": actual_thread_id}
            }