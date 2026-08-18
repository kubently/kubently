import hashlib
import json
import logging
import os
import uuid
from collections.abc import AsyncIterable
from datetime import UTC, datetime

import httpx
from deepagents import create_deep_agent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables.config import RunnableConfig
from .checkpointer import create_checkpointer
from .fleet import cap_output
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
        log_data["timestamp"] = datetime.datetime.now(datetime.UTC).isoformat()

        # Log as formatted JSON
        logger.info(json.dumps(log_data, indent=2, default=str))


_POSTHOG_CLIENT = None  # module-level singleton, see _posthog_llm_callbacks


def _user_message_text(messages: list[dict]) -> str:
    """Concatenated text of the user messages in an incoming turn.

    This is what runbook matching runs against; it handles the same
    multi-part content shape the LangChain conversion below does.
    """
    parts = []
    for msg in messages:
        if msg.get("role", "user") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if p.get("type") == "text")
        if content:
            parts.append(str(content))
    return " ".join(parts)


def _namespaced_thread_id(thread_id: str | None) -> str | None:
    """Bind a conversation thread to the authenticated caller.

    The A2A contextId is client-supplied and is used directly as the LangGraph
    checkpointer's thread namespace, so without this two callers who pick the
    same contextId share conversation memory. Prefixing with a hash of the
    caller's API key keeps threads private per caller while staying stable
    across that caller's turns (which is what makes multi-turn memory work).

    Returns thread_id unchanged when there is no authenticated caller (direct
    or local invocation), preserving existing single-tenant behaviour.
    """
    if not thread_id:
        return thread_id
    try:
        from kubently.modules.auth.context import current_api_key

        key = current_api_key.get()
    except Exception:
        key = None
    if not key:
        return thread_id
    caller = hashlib.sha256(key.encode()).hexdigest()[:16]
    return f"{caller}:{thread_id}"


def _posthog_llm_callbacks():
    """PostHog LLM observability, opt-in via POSTHOG_API_KEY.

    Returns a LangChain callback list that reports each generation
    (model, tokens, cost, latency) to PostHog, or [] when unset. The import is
    guarded so an absent/older posthog SDK degrades to no-op rather than breaking
    the agent. Spike scope: fleet-level capture; per-tenant distinct_id (from the
    caller's identity) is the follow-up.
    """
    key = os.getenv("POSTHOG_API_KEY")
    if not key:
        return []

    global _POSTHOG_CLIENT
    if _POSTHOG_CLIENT is None:
        try:
            from posthog import Posthog
        except Exception as e:  # SDK missing/too old — never fail the agent for telemetry
            logger.warning(f"PostHog LLM observability requested but unavailable: {e}")
            return []
        # Singleton: the client owns a background flush thread and connection
        # pool, and initialize() runs per agent construction — a client per call
        # would leak both.
        _POSTHOG_CLIENT = Posthog(
            key, host=os.getenv("POSTHOG_HOST", "https://us.i.posthog.com")
        )

    try:
        from posthog.ai.langchain import CallbackHandler
    except Exception as e:
        logger.warning(f"PostHog LangChain callback unavailable: {e}")
        return []
    return [CallbackHandler(client=_POSTHOG_CLIENT)]


# Dangerous kubectl verbs that require explicit permission
DANGEROUS_VERBS = {
    "delete", "create", "apply", "patch", "edit", "scale",
    "cordon", "drain", "uncordon", "taint", "label", "annotate",
    "set", "autoscale", "rollout", "expose", "run"
}

# Partially read-only verbs: allowed only with these read-only subcommands
# (mirrors the executor whitelist, which enforces the same restriction).
READ_ONLY_SUBCOMMANDS = {
    "rollout": {"history", "status"},
}

def validate_kubectl_command(command: str, allow_write: bool = False) -> bool:
    """Validate kubectl command for safety."""
    parts = command.split()
    if not parts:
        return False

    verb = parts[0]

    # Check dangerous verbs
    if verb in DANGEROUS_VERBS and not allow_write:
        read_only_subs = READ_ONLY_SUBCOMMANDS.get(verb)
        subcommand = next((p for p in parts[1:] if not p.startswith("-")), None)
        if not (read_only_subs and subcommand in read_only_subs):
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
        # True when checkpointing is intentionally off (backend "none" or no
        # Redis client), so initialize() doesn't retry on every request.
        self._memory_disabled = False
        self._initialized = False
        self.system_prompt = None
        # Operator runbooks (kubently.modules.runbooks): matched per
        # investigation and injected as context. None until initialize().
        self.runbooks = None
        # thread_id -> set of runbook names already injected into that thread,
        # so multi-turn conversations don't accumulate duplicate copies.
        # Best-effort (in-memory): a restart re-injects once, which is harmless.
        self._injected_runbooks: dict[str, set] = {}
        # Incident history (kubently.modules.incidents): past diagnoses become
        # searchable institutional memory. None until initialize() (or when
        # disabled / no Redis).
        self.incidents = None
        # thread_id -> incident ids already auto-surfaced into that thread
        # (same dedup pattern as _injected_runbooks).
        self._surfaced_incidents: dict[str, set] = {}
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
            "timestamp": datetime.now(UTC).isoformat()
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
        if self._initialized and (self.memory is not None or self._memory_disabled):
            return

        if self._initialized and self.memory is None:
            logger.info("Agent initialized but memory failed previously, retrying memory setup...")

        # Initialize memory in async context. Backend selection (RediSearch,
        # plain Redis, in-memory, none) lives in checkpointer.create_checkpointer.
        try:
            self.memory = await create_checkpointer(self.redis_client)
            if self.memory is None:
                self._memory_disabled = True
        except Exception as e:
            logger.warning(f"Failed to initialize checkpointer: {e}")
            logger.warning("Continuing without memory persistence")
            self.memory = None

        # Initialize LLM with context management support for Anthropic models
        # https://docs.claude.com/en/docs/build-with-claude/context-editing#how-it-works
        llm_provider = os.getenv("LLM_PROVIDER", "").lower()
        enable_context_management = os.getenv("ANTHROPIC_CONTEXT_CLEARING", "true").lower() == "true"

        # PostHog LLM observability (optional): when POSTHOG_API_KEY is set, every
        # generation reports model/tokens/cost/latency to PostHog. Attached to the
        # model, so it flows through deepagents without touching the graph.
        posthog_cbs = _posthog_llm_callbacks()

        if "anthropic" in llm_provider or "claude" in llm_provider:
            # For Anthropic models, use direct initialization to enable context management
            if enable_context_management:
                from langchain_anthropic import ChatAnthropic

                # Determine model from environment or use default
                model_name = os.getenv("ANTHROPIC_MODEL_NAME", "claude-sonnet-4-6")

                self.llm = ChatAnthropic(
                    model=model_name,
                    max_tokens=4096,
                    betas=["context-management-2025-06-27"],
                    context_management={
                        "edits": [{"type": "clear_tool_uses_20250919"}]
                    },
                    callbacks=posthog_cbs,
                )
                logger.info(f"Anthropic Claude initialized with context management: {model_name}")
                logger.info("Context management will automatically clear tool results to prevent context overflow")
            else:
                # Anthropic without context-clearing: plain ChatAnthropic.
                from langchain_anthropic import ChatAnthropic

                model_name = os.getenv("ANTHROPIC_MODEL_NAME", "claude-sonnet-4-6")
                self.llm = ChatAnthropic(model=model_name, max_tokens=4096, callbacks=posthog_cbs)
                logger.info(f"Anthropic Claude initialized without context management: {model_name}")
        elif "openai" in llm_provider or "azure" in llm_provider:
            from langchain_openai import ChatOpenAI

            # Cap completion tokens (parity with the Anthropic branch's 4096).
            # Matters for OpenAI-compatible brokers like OpenRouter, which
            # reserve max_tokens against the account balance per request.
            self.llm = ChatOpenAI(
                model=os.getenv("OPENAI_MODEL_NAME", "gpt-4o"),
                max_tokens=int(os.getenv("OPENAI_MAX_TOKENS", "4096")),
                callbacks=posthog_cbs,
            )
            logger.info(f"OpenAI initialized: {llm_provider}")
        elif "google" in llm_provider or "gemini" in llm_provider:
            from langchain_google_genai import ChatGoogleGenerativeAI

            self.llm = ChatGoogleGenerativeAI(model=os.getenv("GOOGLE_MODEL_NAME", "gemini-2.0-flash"), callbacks=posthog_cbs)
            logger.info(f"Google Gemini initialized: {llm_provider}")
        else:
            raise ValueError(
                f"Unsupported LLM_PROVIDER '{llm_provider}'. Set LLM_PROVIDER to one of: "
                "anthropic-claude, openai, google-gemini."
            )

        # Load system prompt from configuration. Loki and metrics guidance
        # are injected only when the matching tool (query_loki /
        # query_prometheus) is registered, so the prompt never references a
        # tool the model cannot call.
        from kubently.modules.config import get_prompt

        from .gitops import gitops_guidance
        from .logsearch import loki_guidance
        from .mcp_client import mcp_guidance
        from .prometheus import metrics_guidance

        self.system_prompt = get_prompt(
            role="a2a",
            variables={
                "loki_guidance": loki_guidance(),
                "metrics_guidance": metrics_guidance(),
                "gitops_guidance": gitops_guidance(),
                "mcp_guidance": mcp_guidance(),
            },
        )

        # Operator runbooks: directory (KUBENTLY_RUNBOOKS_DIR, ConfigMap-mounted
        # in Helm deployments) of markdown files matched per investigation and
        # injected as context. Missing directory just means an empty store.
        from kubently.modules.runbooks import RunbookStore

        self.runbooks = RunbookStore()

        # Incident history: retrieval over compact summaries of concluded
        # investigations, stored in Redis per caller namespace. Default on;
        # KUBENTLY_INCIDENT_HISTORY=false or a missing Redis client turns it
        # off (the agent then just has no institutional memory, as before).
        from kubently.modules.incidents import IncidentStore, incidents_enabled

        if incidents_enabled() and self.redis_client is not None:
            try:
                self.incidents = IncidentStore(self.redis_client)
                logger.info("Incident history enabled (search_past_incidents + auto-surface)")
            except Exception as e:
                logger.warning(f"Incident history unavailable: {e}")
                self.incidents = None
        else:
            self.incidents = None

        # Initialize tools for kubectl operations
        await self._initialize_tools()

        # Build a deep agent (deepagents 0.6.x). Beyond a plain ReAct loop this gives
        # the model a built-in planning tool (write_todos via TodoListMiddleware), a
        # virtual filesystem, and sub-agent support — better suited to multi-step
        # Kubernetes debugging. Returns a CompiledStateGraph, so the existing
        # `self.agent.ainvoke({"messages": ...}, config)` call in run() is unchanged.
        # Only attach the Redis checkpointer if we have a connection for shared state.
        self.agent = create_deep_agent(
            self.llm,
            self.tools,
            system_prompt=self.system_prompt,
            checkpointer=self.memory if self.memory else None,
        )

        self._initialized = True
        logger.info("KubentlyAgent initialized successfully with enhanced investigation")

    async def _initialize_tools(self):
        """Initialize kubectl tools."""
        # Get API URL from environment - use internal service for tool calls
        api_url = os.getenv("KUBENTLY_API_URL", "http://localhost:8080")

        # Use the auth module utility to extract API key (handles service:key format)
        from kubently.modules.auth import AuthModule
        from kubently.modules.auth.context import current_api_key

        internal_api_key = AuthModule.extract_first_api_key()

        def api_key() -> str:
            # Per-call: prefer the caller's key (set by the auth wrapper at the
            # A2A/MCP mount) so tool calls execute with the caller's privileges;
            # fall back to the internal service key (direct/local invocation).
            return current_api_key.get() or internal_api_key

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
                        headers={"X-Api-Key": api_key()},
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
                    error_msg = f"Error listing clusters: {e!s}"
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

            TOKEN EFFICIENCY FIRST:
            - Minimize output tokens by using targeted kubectl flags
            - Use "--field-selector" for genuine field lookups (e.g., "status.phase=Pending"
              for scheduling problems, "involvedObject.name=<pod>" for events)
            - NEVER use "--field-selector status.phase!=Running" to find broken pods: a
              CrashLoopBackOff pod reports phase=Running, so that filter hides it
            - Use "-o custom-columns" to retrieve only needed fields
            - Use "-o wide" for quick overview with essential columns
            - Use "describe" instead of "-o json" for comprehensive resource details
            - ONLY use "-o json" when you need to parse specific nested fields programmatically
            - Default output is usually sufficient and most token-efficient

            TOKEN-EFFICIENT EXAMPLES:
            - Find problematic pods: "get pods -A -o wide" (READY shows 0/1, STATUS shows
              CrashLoopBackOff/ImagePullBackOff/Error — catches every failure mode)
            - Custom columns: "get pods -o custom-columns=NAME:.metadata.name,READY:.status.containerStatuses[*].ready,RESTARTS:.status.containerStatuses[*].restartCount,REASON:.status.containerStatuses[*].state.waiting.reason"
            - Wide format: "get pods -o wide"
            - Describe (comprehensive): "describe pod pod-name"
            - Events (default output): "get events --sort-by='.lastTimestamp'"
            - With selectors: "get pods -l app=nginx"
            - Logs: "logs pod-name --tail=50"
            - Field extraction: "get pod pod-name -o jsonpath='{.status.phase}'"

            AVOID THESE (token-heavy):
            - ❌ "get pods -A -o json" (dumps full config for every pod - thousands of tokens!)
            - ❌ "get pods -o yaml" (verbose YAML for all pods)
            - ❌ "get events -o json" (JSON adds unnecessary overhead)

            IMPORTANT: Use multiple targeted commands to build complete understanding.
            Don't assume - verify everything with additional commands.

            Common investigation patterns:
            - kubectl get <resource> -n <namespace> --field-selector <filter>
            - kubectl describe <resource> <name> -n <namespace> (comprehensive, efficient)
            - kubectl get events -n <namespace> --sort-by='.lastTimestamp'
            - kubectl logs <pod> -n <namespace> --tail=50
            - kubectl get endpoints <service> -n <namespace>
            - kubectl get <resource> -o wide -n <namespace>

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
                        headers={"X-Api-Key": api_key()},
                        json=payload,
                    )

                    debug_print(
                        f"API response: status={response.status_code}, text={response.text[:200]}..."
                    )
                    if response.status_code == 200:
                        result = response.json()
                        # HTTP 200 does not mean the command ran: an unreachable
                        # executor returns 200 with status=timeout, output=null.
                        # Surface that as an error instead of empty output, which
                        # the agent would otherwise read as "nothing is wrong".
                        exec_status, exec_error = result.get("status"), result.get("error")
                        if exec_error or (exec_status and exec_status != "success"):
                            error_msg = cap_output(
                                f"Error: {exec_error or f'command status: {exec_status}'}"
                            )
                            await interceptor.record_tool_result(tool_call_id, None, error_msg)
                            return error_msg
                        # Cap before anything downstream sees it: the model, the
                        # interceptor trace and the investigation log all get the
                        # same bounded text the agent actually reasons over.
                        output = cap_output(result.get("output") or "")
                        debug_print(f"Tool successful: {output[:100]}...")

                        # Update investigation tracking with findings
                        if self.investigation_steps:
                            self.investigation_steps[-1]["findings"] = output[:200] if output else "No output"

                        await interceptor.record_tool_result(tool_call_id, output)
                        return output
                    else:
                        error_msg = cap_output(f"Error: HTTP {response.status_code}: {response.text}")
                        debug_print(f"Tool failed: {error_msg}")
                        await interceptor.record_tool_result(tool_call_id, None, error_msg)
                        return error_msg

                except Exception as e:
                    error_msg = f"Error executing command: {e!s}"
                    await interceptor.record_tool_result(tool_call_id, None, error_msg)
                    return error_msg

        from kubently.modules.a2a.protocol_bindings.a2a_server.fleet import (
            build_execute_payload,
            run_fleet_command,
        )

        @tool
        async def execute_kubectl_multi(
            cluster_ids: list[str],
            command: str,
            namespace: str = "default",
        ) -> str:
            """Run one read-only kubectl command across MANY clusters in parallel.

            Use this for fleet-wide questions ("across all clusters", "which clusters
            have X"). Pass ["all"] to target every registered cluster (capped at 10).
            Results are grouped per cluster; empty results collapse to one line and
            long outputs are truncated — drill into a specific cluster with
            execute_kubectl when you need full output.

            Keep fleet commands filtered and token-efficient (e.g. "get pods -A -o wide").
            Do NOT sweep for failures with "--field-selector status.phase!=Running":
            CrashLoopBackOff pods report phase=Running, so entire clusters come back
            looking healthy while their pods crash on a loop.

            Args:
                cluster_ids: Target clusters, or ["all"] for every registered cluster
                command: Full kubectl command (verb, resource, flags) — read-only only
                namespace: Namespace to scope to ("all" adds -A)

            Returns:
                Aggregated output, one "=== cluster: <id> ===" section per cluster
            """
            try:
                validate_kubectl_command(command, allow_write=False)
            except ValueError as e:
                return str(e)

            debug_print(
                f"execute_kubectl_multi called: cluster_ids={cluster_ids}, command={command}"
            )
            tool_call_id = await interceptor.record_tool_call(
                tool_name="execute_kubectl_multi",
                args={"cluster_ids": cluster_ids, "command": command, "namespace": namespace},
                thread_id=getattr(self, "_current_thread_id", None),
            )
            try:
                output = await run_fleet_command(
                    api_url, api_key(), cluster_ids, build_execute_payload(command, namespace)
                )
                await interceptor.record_tool_result(tool_call_id, output)
                return output
            except Exception as e:
                error_msg = f"Error executing fleet command: {e!s}"
                await interceptor.record_tool_result(tool_call_id, None, error_msg)
                return error_msg

        from kubently.modules.a2a.protocol_bindings.a2a_server import changes as changes_mod

        async def _call_debug(client, path: str, payload: dict) -> tuple:
            """POST one /debug/* request; returns (output, error) — never raises."""
            try:
                response = await client.post(
                    f"{api_url}{path}", headers={"X-Api-Key": api_key()}, json=payload
                )
                if response.status_code != 200:
                    return None, f"HTTP {response.status_code}: {response.text[:200]}"
                result = response.json()
                exec_status, exec_error = result.get("status"), result.get("error")
                if exec_error or (exec_status and exec_status != "success"):
                    return None, exec_error or f"status: {exec_status}"
                return result.get("output") or "", None
            except Exception as e:
                return None, str(e)

        @tool
        async def get_recent_changes(
            cluster_id: str,
            namespace: str,
            resource_name: str | None = None,
            resource_type: str = "deployment",
            window: str = "24h",
        ) -> str:
            """Build a timeline of recent changes for a resource or namespace.

            THE FIRST QUESTION of any sudden-failure investigation is "what
            changed?" — call this BEFORE deep-diving into symptoms. It
            aggregates, for the given time window, every change source in one
            pass:
            - Deployment rollouts (ReplicaSet revisions with timestamps and
              images, rollout history change-causes)
            - Helm release history (installs/upgrades/rollbacks, when enabled
              on the cluster's executor)
            - ArgoCD sync history (when configured)
            - Kubernetes events, Normal AND Warning — Normal events
              (ScalingReplicaSet, Killing, new-image Pulled) are the record of
              a change; Warning events are its consequences

            Then CORRELATE: compare change timestamps against the first-error
            timestamp and name the correlated change explicitly in your RCA
            ("the OOMKills began 90 seconds after helm revision 42 deployed").

            Args:
                cluster_id: Target cluster (same IDs as execute_kubectl)
                namespace: Namespace to inspect
                resource_name: Optional workload to scope to (Deployment /
                    StatefulSet / DaemonSet name — pass the owning workload,
                    not a pod). Omit for a namespace-wide change sweep.
                resource_type: Kind of resource_name (default "deployment")
                window: Look-back window like "30m", "6h", "24h" (default), "2d"

            Returns:
                Chronological changes timeline (oldest first), with
                per-source availability notes.
            """
            debug_print(
                f"get_recent_changes called: cluster_id={cluster_id}, namespace={namespace}, "
                f"resource={resource_type}/{resource_name}, window={window}"
            )
            tool_call_id = await interceptor.record_tool_call(
                tool_name="get_recent_changes",
                args={
                    "cluster_id": cluster_id,
                    "namespace": namespace,
                    "resource_name": resource_name,
                    "resource_type": resource_type,
                    "window": window,
                },
                thread_id=getattr(self, "_current_thread_id", None),
            )

            try:
                window_delta = changes_mod.parse_window(window)
                resource_type_normalized = resource_type.strip().lower().rstrip("s") or "deployment"
                entries: list = []
                unavailable: dict = {}

                async with httpx.AsyncClient(timeout=45.0) as client:
                    # 1. Workload metadata: helm/argocd ownership + revisions.
                    workloads_json, err = await _call_debug(
                        client,
                        "/debug/execute",
                        {
                            "cluster_id": cluster_id,
                            "command_type": "get",
                            "args": ["deployments,statefulsets,daemonsets", "-o", "json"],
                            "namespace": namespace,
                            "timeout_seconds": 30,
                        },
                    )
                    workloads = changes_mod.extract_workloads(workloads_json) if not err else []
                    if err:
                        unavailable["workload metadata"] = err

                    scoped = [w for w in workloads if w.name == resource_name] if resource_name else workloads
                    if resource_name and not scoped:
                        unavailable["workload scope"] = (
                            f"'{resource_name}' not found among deployments/statefulsets/"
                            f"daemonsets in {namespace}; showing name-matched events only"
                        )

                    # 2. ReplicaSet revisions: the dated record of rollouts.
                    rs_json, err = await _call_debug(
                        client,
                        "/debug/execute",
                        {
                            "cluster_id": cluster_id,
                            "command_type": "get",
                            "args": ["replicasets", "-o", "json"],
                            "namespace": namespace,
                            "timeout_seconds": 30,
                        },
                    )
                    if not err:
                        entries.extend(
                            changes_mod.replicaset_changes(
                                rs_json,
                                deployment=resource_name
                                if resource_name and resource_type_normalized == "deployment"
                                else None,
                            )
                        )

                    # 3. Rollout history change-causes for the scoped workload.
                    if resource_name and resource_type_normalized in (
                        "deployment",
                        "statefulset",
                        "daemonset",
                    ):
                        history_text, err = await _call_debug(
                            client,
                            "/debug/execute",
                            {
                                "cluster_id": cluster_id,
                                "command_type": "rollout",
                                "args": ["history", f"{resource_type_normalized}/{resource_name}"],
                                "namespace": namespace,
                                "timeout_seconds": 30,
                            },
                        )
                        if not err:
                            entries.extend(
                                changes_mod.parse_rollout_history(
                                    history_text, f"{resource_type_normalized}/{resource_name}"
                                )
                            )

                    # 4. Events (Normal + Warning), scoped by ownership-chain prefix.
                    events_json, err = await _call_debug(
                        client,
                        "/debug/execute",
                        {
                            "cluster_id": cluster_id,
                            "command_type": "get",
                            "args": ["events", "-o", "json"],
                            "namespace": namespace,
                            "timeout_seconds": 30,
                        },
                    )
                    if not err:
                        entries.extend(
                            changes_mod.event_changes(
                                events_json,
                                name_prefixes=[resource_name] if resource_name else None,
                            )
                        )
                    else:
                        unavailable["events"] = err

                    # 5. Helm history for the owning releases (deduped, capped).
                    releases = []
                    for w in scoped:
                        if w.helm_release:
                            key = (w.helm_release, w.helm_namespace or namespace)
                            if key not in releases:
                                releases.append(key)
                    for release, release_ns in releases[:3]:
                        history_json, err = await _call_debug(
                            client,
                            "/debug/helm",
                            {
                                "cluster_id": cluster_id,
                                "subcommand": "history",
                                "release_name": release,
                                "namespace": release_ns,
                                "max": 10,
                            },
                        )
                        if err:
                            unavailable["helm history"] = err
                            break  # one clear note beats three identical ones
                        entries.extend(changes_mod.helm_history_changes(history_json, release))

                    # 6. ArgoCD sync history (only when configured — otherwise
                    # this source is silently absent).
                    if changes_mod.argocd_enabled():
                        apps = []
                        for w in scoped:
                            if w.argocd_app and w.argocd_app not in apps:
                                apps.append(w.argocd_app)
                        for app_name in apps[:3]:
                            app_json, err = await _call_debug(
                                client,
                                "/debug/argocd",
                                {
                                    "cluster_id": cluster_id,
                                    "operation": "get_app",
                                    "app_name": app_name,
                                },
                            )
                            if err:
                                unavailable["argocd"] = err
                                break
                            entries.extend(changes_mod.argocd_changes(app_json))

                scope = (
                    f"{namespace}/{resource_type_normalized}/{resource_name}"
                    if resource_name
                    else f"namespace {namespace}"
                )
                output = cap_output(
                    changes_mod.build_timeline(
                        entries, window_delta, scope, sources_unavailable=unavailable
                    )
                )
                await interceptor.record_tool_result(tool_call_id, output)
                return output
            except Exception as e:
                error_msg = f"Error building changes timeline: {e!s}"
                await interceptor.record_tool_result(tool_call_id, None, error_msg)
                return error_msg

        @tool
        async def get_events_for_resource(
            cluster_id: str,
            resource_name: str,
            namespace: str,
            window: str = "6h",
        ) -> str:
            """Get all Kubernetes events for a resource AND its children.

            Prefix-matches the ownership chain: for Deployment "api" this
            includes events on ReplicaSet "api-<hash>" and Pods
            "api-<hash>-<id>", so one call shows the full picture (scaling,
            image pulls, kills, probe failures, scheduling problems).

            Prefer this over raw `kubectl get events` when investigating one
            resource — it is already filtered, deduplicated by name, and
            chronologically sorted (oldest first). Includes Normal events,
            which record changes (ScalingReplicaSet, Pulled, Killing), not
            just Warnings. Note events expire (default ~1h TTL), so an empty
            result does not prove nothing happened.

            Args:
                cluster_id: Target cluster (same IDs as execute_kubectl)
                resource_name: Resource whose events to fetch (any kind)
                namespace: Namespace of the resource
                window: Look-back window like "30m", "6h" (default), "24h"

            Returns:
                Chronological event timeline for the resource and its children.
            """
            debug_print(
                f"get_events_for_resource called: cluster_id={cluster_id}, "
                f"resource_name={resource_name}, namespace={namespace}"
            )
            tool_call_id = await interceptor.record_tool_call(
                tool_name="get_events_for_resource",
                args={
                    "cluster_id": cluster_id,
                    "resource_name": resource_name,
                    "namespace": namespace,
                    "window": window,
                },
                thread_id=getattr(self, "_current_thread_id", None),
            )

            try:
                async with httpx.AsyncClient(timeout=45.0) as client:
                    events_json, err = await _call_debug(
                        client,
                        "/debug/execute",
                        {
                            "cluster_id": cluster_id,
                            "command_type": "get",
                            "args": ["events", "-o", "json"],
                            "namespace": namespace,
                            "timeout_seconds": 30,
                        },
                    )
                if err:
                    error_msg = cap_output(f"Error fetching events: {err}")
                    await interceptor.record_tool_result(tool_call_id, None, error_msg)
                    return error_msg

                entries = changes_mod.event_changes(events_json, name_prefixes=[resource_name])
                output = cap_output(
                    changes_mod.build_timeline(
                        entries,
                        changes_mod.parse_window(window),
                        f"events for {namespace}/{resource_name} (+children)",
                    )
                )
                await interceptor.record_tool_result(tool_call_id, output)
                return output
            except Exception as e:
                error_msg = f"Error fetching events: {e!s}"
                await interceptor.record_tool_result(tool_call_id, None, error_msg)
                return error_msg
        from kubently.modules.a2a.protocol_bindings.a2a_server.logsearch import (
            build_log_search_payload,
            build_loki_payload,
            loki_tool_enabled,
        )

        async def _post_tool_request(
            endpoint: str, payload: dict, tool_call_id, error_label: str, timeout: float = 75.0
        ) -> str:
            """POST a tool payload to the API, normalize errors, cap and record
            the result. Shared by search_pod_logs and query_loki."""
            async with httpx.AsyncClient(timeout=timeout) as client:
                try:
                    response = await client.post(
                        f"{api_url}{endpoint}",
                        headers={"X-Api-Key": api_key()},
                        json=payload,
                    )
                    if response.status_code != 200:
                        error_msg = cap_output(
                            f"Error: HTTP {response.status_code}: {response.text}"
                        )
                        await interceptor.record_tool_result(tool_call_id, None, error_msg)
                        return error_msg

                    result = response.json()
                    exec_status, exec_error = result.get("status"), result.get("error")
                    if exec_error or (exec_status and exec_status != "success"):
                        error_msg = cap_output(
                            f"Error: {exec_error or f'{error_label} status: {exec_status}'}"
                        )
                        await interceptor.record_tool_result(tool_call_id, None, error_msg)
                        return error_msg

                    # The executor already caps matches/lines; this is the
                    # context-budget backstop shared with kubectl results.
                    output = cap_output(result.get("output") or "")
                    await interceptor.record_tool_result(tool_call_id, output)
                    return output
                except Exception as e:
                    error_msg = f"Error running {error_label}: {e!s}"
                    await interceptor.record_tool_result(tool_call_id, None, error_msg)
                    return error_msg

        @tool
        async def search_pod_logs(
            cluster_id: str,
            namespace: str,
            query: str,
            selector: str | None = None,
            pod_name: str | None = None,
            container: str | None = None,
            use_regex: bool = False,
            case_sensitive: bool = False,
            since: str | None = "1h",
            since_time: str | None = None,
            tail_lines: int = 2000,
            previous: bool = False,
            context_lines: int = 0,
        ) -> str:
            """Search logs across ALL pods matching a selector in one call.

            Use this instead of dumping logs pod-by-pod when you need to find
            WHICH pods/containers logged something: errors after a deploy,
            correlating messages across replicas, tracing an upstream failure
            through a workload. Logs are filtered on the cluster's executor,
            so only matching lines (already capped) come back — far cheaper
            than kubectl logs on each pod.

            NARROW FIRST: identify the namespace and a label selector (e.g.
            via "get pods -n <ns> --show-labels"), and keep a time bound
            (since defaults to "1h"). Provide exactly one of `selector` or
            `pod_name`.

            Query matching:
            - Default: case-insensitive substring
            - use_regex=True for alternatives, e.g. "error|exception|timed? ?out"
            - context_lines=2-3 to capture stack traces around each match

            For crash investigations set previous=True to search the
            pre-restart logs of restarted containers.

            Results are capped (pods scanned, matches per container, total
            matches, output size) and every truncation is noted in the output.
            When a cap fires, narrow the query/selector/time window instead of
            retrying the same search.

            Args:
                cluster_id: Target cluster (same IDs as execute_kubectl)
                namespace: Namespace to search in
                query: Substring or regex to search for
                selector: Label selector (e.g. "app=api"); or use pod_name
                pod_name: Single pod name; or use selector
                container: Restrict to one container name
                use_regex: Treat query as a regular expression
                case_sensitive: Match case-sensitively
                since: Relative time window like "30m", "1h" (default "1h")
                since_time: Absolute RFC3339 lower bound (overrides since)
                tail_lines: Max lines fetched per container (default 2000)
                previous: Search previous (pre-restart) container logs
                context_lines: Lines of context around each match (0-10)

            Returns:
                Per-container sections of matching lines with a summary
                header, no-match list, and explicit truncation notes.
            """
            debug_print(
                f"search_pod_logs called: cluster_id={cluster_id}, namespace={namespace}, "
                f"selector={selector}, pod_name={pod_name}, query={query}"
            )
            tool_call_id = await interceptor.record_tool_call(
                tool_name="search_pod_logs",
                args={
                    "cluster_id": cluster_id,
                    "namespace": namespace,
                    "query": query,
                    "selector": selector,
                    "pod_name": pod_name,
                    "container": container,
                    "use_regex": use_regex,
                    "case_sensitive": case_sensitive,
                    "since": since,
                    "since_time": since_time,
                    "tail_lines": tail_lines,
                    "previous": previous,
                    "context_lines": context_lines,
                },
                thread_id=getattr(self, "_current_thread_id", None),
            )
            payload = build_log_search_payload(
                namespace=namespace,
                query=query,
                selector=selector,
                pod_name=pod_name,
                container=container,
                use_regex=use_regex,
                case_sensitive=case_sensitive,
                since=since,
                since_time=since_time,
                tail_lines=tail_lines,
                previous=previous,
                context_lines=context_lines,
            )
            payload["cluster_id"] = cluster_id
            return await _post_tool_request(
                "/debug/logs/search", payload, tool_call_id, "log search"
            )

        # Note: planning/todo tracking is now provided natively by deepagents
        # (write_todos via TodoListMiddleware), so the previous hand-rolled
        # todo_write tool + TodoManager were removed.
        self.tools = [
            list_clusters,
            execute_kubectl,
            execute_kubectl_multi,
            search_pod_logs,
            get_recent_changes,
            get_events_for_resource,
        ]

        if self.incidents is not None:
            from kubently.modules.incidents import caller_namespace, format_search_results

            @tool
            async def search_past_incidents(
                query: str,
                cluster_id: str | None = None,
                limit: int = 5,
            ) -> str:
                """Search this deployment's history of past diagnosed incidents.

                Institutional memory: every investigation that concluded with a
                root cause left a compact record (date, cluster, resources,
                symptom keywords, root-cause one-liner, resolution when
                stated). Use this to answer "have we seen this before?" —
                when current symptoms feel like a recurrence, when the user
                asks about past issues, or before concluding a novel root
                cause for a familiar-looking failure.

                Matching is keyword-based: mention the failing resource names,
                namespace, and symptom words (e.g. "checkout-api payments
                CrashLoopBackOff OOMKilled") for the best results. Pass an
                empty query to list the most recent incidents.

                IMPORTANT: results are summaries of PAST states, not evidence
                about the current cluster. Verify against fresh kubectl/log
                evidence before relying on one. If a past incident materially
                informs your diagnosis, cite it in your root-cause summary
                (e.g. "same root cause as the 2026-07-03 incident").

                Args:
                    query: Free-text search (resource names, namespaces,
                        symptoms, root-cause words). Empty = newest first.
                    cluster_id: Optionally boost incidents from this cluster
                    limit: Max results (default 5)

                Returns:
                    Matching incidents (best first) with date, cluster,
                    resources, root cause and resolution, or a clear
                    "no matching past incidents" note.
                """
                debug_print(
                    f"search_past_incidents called: query={query!r}, "
                    f"cluster_id={cluster_id}, limit={limit}"
                )
                tool_call_id = await interceptor.record_tool_call(
                    tool_name="search_past_incidents",
                    args={"query": query, "cluster_id": cluster_id, "limit": limit},
                    thread_id=getattr(self, "_current_thread_id", None),
                )
                try:
                    # Same per-caller namespace derivation as conversation
                    # memory: a caller can only ever search their own records.
                    results = await self.incidents.search(
                        caller_namespace(),
                        query=query,
                        cluster_id=cluster_id,
                        limit=max(1, min(int(limit), 20)),
                    )
                    output = cap_output(format_search_results(results))
                    await interceptor.record_tool_result(tool_call_id, output)
                    return output
                except Exception as e:
                    error_msg = f"Error searching past incidents: {e!s}"
                    await interceptor.record_tool_result(tool_call_id, None, error_msg)
                    return error_msg

            self.tools.append(search_past_incidents)
            logger.info("Incident history search tool registered")

        if loki_tool_enabled():

            @tool
            async def query_loki(
                cluster_id: str,
                query: str,
                start: str | None = None,
                end: str | None = None,
                limit: int = 100,
                direction: str = "backward",
            ) -> str:
                """Run a read-only LogQL range query against a cluster's Loki.

                PREFER this over search_pod_logs when logs must survive pod
                restarts/deletions, when searching across many workloads at
                once, or when the window is further back than pods' current
                logs reach. The query executes inside the target cluster (via
                its executor), so use the in-cluster view of Loki.

                Write BOUNDED LogQL — results are line-capped with a note when
                truncated:
                - Always start from a label selector: {namespace="x", app="api"}
                - Add line filters: |= "error" (substring), |~ "regex", != / !~ to exclude
                - Keep ranges short; Loki defaults to the last hour when
                  start/end are omitted
                - Count first when volume is unknown:
                  sum by (pod) (count_over_time({namespace="x"} |= "error" [1h]))

                Args:
                    cluster_id: Target cluster (same IDs as execute_kubectl)
                    query: LogQL expression
                    start: Range start (RFC3339 or unix seconds; default -1h)
                    end: Range end (RFC3339 or unix seconds; default now)
                    limit: Max log lines returned (default 100)
                    direction: "backward" (newest first, default) or "forward"

                Returns:
                    Per-stream sections of timestamped log lines (or compact
                    JSON for metric-style queries), with truncation notes, or
                    an error message (e.g. Loki not configured on that cluster
                    — use search_pod_logs there instead).
                """
                debug_print(
                    f"query_loki called: cluster_id={cluster_id}, query={query}, "
                    f"start={start}, end={end}, limit={limit}"
                )
                tool_call_id = await interceptor.record_tool_call(
                    tool_name="query_loki",
                    args={
                        "cluster_id": cluster_id,
                        "query": query,
                        "start": start,
                        "end": end,
                        "limit": limit,
                        "direction": direction,
                    },
                    thread_id=getattr(self, "_current_thread_id", None),
                )
                payload = build_loki_payload(
                    query, start=start, end=end, limit=limit, direction=direction
                )
                payload["cluster_id"] = cluster_id
                return await _post_tool_request(
                    "/debug/loki", payload, tool_call_id, "Loki query", timeout=45.0
                )

            self.tools.append(query_loki)
            logger.info("Loki log search tool registered (LOKI_URL is set)")

        # Cloud telemetry tools (query_cloud_logs, query_cloud_metrics,
        # get_recent_cloud_changes). They dispatch to whichever provider the
        # target executor reports a workload identity for, and refuse per-call
        # when a cluster's executor reports none.
        from kubently.modules.a2a.protocol_bindings.a2a_server.cloud_tools import (
            build_cloud_tools,
        )

        self.tools.extend(
            build_cloud_tools(
                api_url,
                api_key,
                interceptor,
                lambda: getattr(self, "_current_thread_id", None),
            )
        )

        from kubently.modules.a2a.protocol_bindings.a2a_server.prometheus import (
            build_prometheus_payload,
            prometheus_tool_enabled,
        )

        if prometheus_tool_enabled():

            @tool
            async def query_prometheus(
                cluster_id: str,
                query: str,
                query_type: str = "instant",
                start: str | None = None,
                end: str | None = None,
                step: str | None = None,
                time: str | None = None,
            ) -> str:
                """Run a read-only PromQL query against a cluster's Prometheus.

                Use metrics when kubectl can't answer the question: latency and
                error rates, CPU/memory saturation trends, OOM pressure, restart
                frequency over time, capacity headroom. The query executes inside
                the target cluster (via its executor), so use the in-cluster view
                of Prometheus.

                Query types:
                - "instant" (default): current value. Optionally set `time`
                  (RFC3339 or unix seconds) to evaluate at a point in the past.
                - "range": values over a window. Requires `start`, `end` (RFC3339
                  or unix seconds) and `step` (e.g. "60s", "5m"). Keep windows
                  short (30m-2h) and steps coarse — results are capped.

                EFFICIENT PromQL (results are capped; truncation is noted in the
                output when it happens):
                - Always filter by labels (namespace, pod, container) — never
                  query a bare metric name with no selector
                - Aggregate: sum/avg by (pod) (rate(metric{...}[5m]))
                - Rank with topk(5, ...) instead of returning everything
                - Use rate()/increase() over counters

                Args:
                    cluster_id: Target cluster (same IDs as execute_kubectl)
                    query: PromQL expression
                    query_type: "instant" or "range"
                    start: Range start (RFC3339 or unix seconds)
                    end: Range end (RFC3339 or unix seconds)
                    step: Range resolution (e.g. "60s", "5m")
                    time: Optional evaluation time for instant queries

                Returns:
                    Compact JSON result ({"resultType": ..., "result": [...]}),
                    with a "kubently_truncation" note when caps were applied,
                    or an error message (e.g. Prometheus not configured on that
                    cluster — fall back to kubectl evidence in that case).
                """
                debug_print(
                    f"query_prometheus called: cluster_id={cluster_id}, "
                    f"query_type={query_type}, query={query}"
                )
                tool_call_id = await interceptor.record_tool_call(
                    tool_name="query_prometheus",
                    args={
                        "cluster_id": cluster_id,
                        "query": query,
                        "query_type": query_type,
                        "start": start,
                        "end": end,
                        "step": step,
                        "time": time,
                    },
                    thread_id=getattr(self, "_current_thread_id", None),
                )

                payload = build_prometheus_payload(
                    query, query_type=query_type, start=start, end=end, step=step, time=time
                )
                payload["cluster_id"] = cluster_id

                async with httpx.AsyncClient(timeout=45.0) as client:
                    try:
                        response = await client.post(
                            f"{api_url}/debug/prometheus",
                            headers={"X-Api-Key": api_key()},
                            json=payload,
                        )
                        if response.status_code != 200:
                            error_msg = cap_output(
                                f"Error: HTTP {response.status_code}: {response.text}"
                            )
                            await interceptor.record_tool_result(tool_call_id, None, error_msg)
                            return error_msg

                        result = response.json()
                        exec_status, exec_error = result.get("status"), result.get("error")
                        if exec_error or (exec_status and exec_status != "success"):
                            error_msg = cap_output(
                                f"Error: {exec_error or f'query status: {exec_status}'}"
                            )
                            await interceptor.record_tool_result(tool_call_id, None, error_msg)
                            return error_msg

                        # Executor already caps series/samples; this is the
                        # context-budget backstop shared with kubectl results.
                        output = cap_output(result.get("output") or "")
                        await interceptor.record_tool_result(tool_call_id, output)
                        return output
                    except Exception as e:
                        error_msg = f"Error executing Prometheus query: {e!s}"
                        await interceptor.record_tool_result(tool_call_id, None, error_msg)
                        return error_msg

            self.tools.append(query_prometheus)
            logger.info("Prometheus metrics tool registered (PROMETHEUS_URL is set)")

        # GitOps PR remediation tools (get_manifest_file, propose_fix_pr).
        # Registered only when a Git remediation target is fully configured
        # (provider + repo + token) — default OFF. The matching prompt
        # guidance flips on the same switch via {{gitops_guidance}} above.
        from kubently.modules.a2a.protocol_bindings.a2a_server.gitops_tools import (
            build_gitops_tools,
        )

        self.tools.extend(
            build_gitops_tools(
                interceptor,
                lambda: getattr(self, "_current_thread_id", None),
            )
        )

        # External MCP servers (operator-configured): tools register with an
        # mcp_<server>_ prefix; an unreachable server contributes nothing and
        # the investigation proceeds with native tools (see mcp_client.py for
        # the untrusted-input framing and result caps).
        from kubently.modules.a2a.protocol_bindings.a2a_server.mcp_client import (
            build_mcp_tools,
            load_static_servers,
        )

        static_mcp_specs = load_static_servers()
        if static_mcp_specs:
            mcp_tools = await build_mcp_tools(
                static_mcp_specs,
                interceptor,
                lambda: getattr(self, "_current_thread_id", None),
            )
            self.tools.extend(mcp_tools)
            logger.info(
                f"Registered {len(mcp_tools)} external MCP tool(s) from "
                f"{len(static_mcp_specs)} configured server(s)"
            )

        logger.info(f"Initialized {len(self.tools)} tools")

    async def run(
        self,
        messages: list[dict],
        thread_id: str | None = None,
        context_id: str | None = None,
        cluster_id: str | None = None,
        mcp_servers: list | None = None,
    ) -> AsyncIterable[dict]:
        """Run the agent and stream responses.

        Args:
            messages: User messages to process
            thread_id: Thread ID for memory/conversation tracking
            context_id: Context ID for the A2A protocol
            cluster_id: Target cluster ID from CLI (if specified)
            mcp_servers: Optional per-request external MCP servers — a list of
                mcp_client.MCPServerSpec (or dicts of the same shape). The
                extension point for an embedding service: tools from these
                servers exist only for THIS invocation, and any credentials in
                the specs are used for the call and never stored by the agent.
                Unreachable servers degrade to "tools unavailable".
        """
        await self.initialize()

        # SECURITY: thread_id is the checkpointer's namespace, and it comes from
        # the A2A message's client-supplied contextId. Un-namespaced, any caller
        # could resume another caller's conversation — replaying their questions,
        # kubectl output and cluster internals — by guessing/reusing a contextId.
        # Bind it to the authenticated caller so threads can never collide across
        # tenants. Falls back to the raw id for unauthenticated/local invocation.
        thread_id = _namespaced_thread_id(thread_id)

        # Store thread ID for tool call tracking
        self._current_thread_id = thread_id

        # This turn's user text: matched against runbooks and past incidents,
        # and kept as the "query" on any incident record this turn produces.
        # Captured before any context injection below (MCP tool notes,
        # runbooks, incident notes) so matching sees only what the user said.
        query_text = _user_message_text(messages)

        # Per-request MCP servers (embedding-service seam): build their tools
        # for this invocation only and run a per-request agent that sees the
        # base toolset plus the injected ones. The specs (and the credentials
        # inside them) live only for the duration of this call — nothing is
        # stored on the agent. Failure to reach a server just means its tools
        # are absent; the investigation proceeds either way.
        run_agent = self.agent
        if mcp_servers:
            from .mcp_client import MCPServerSpec, build_mcp_tools, per_request_note

            try:
                specs = [
                    s if isinstance(s, MCPServerSpec) else MCPServerSpec.from_dict(s)
                    for s in mcp_servers
                ]
            except ValueError as e:
                logger.warning(f"Invalid per-request MCP server spec, ignoring all: {e}")
                specs = []
            request_tools = await build_mcp_tools(
                specs,
                get_tool_call_interceptor(),
                lambda: getattr(self, "_current_thread_id", None),
            )
            if request_tools:
                from deepagents import create_deep_agent

                run_agent = create_deep_agent(
                    self.llm,
                    [*self.tools, *request_tools],
                    system_prompt=self.system_prompt,
                    checkpointer=self.memory if self.memory else None,
                )
                # Announce the extra tools as a user-role message (system
                # messages mid-thread break the checkpointer — see the cluster
                # context injection below for the full rationale).
                messages = [
                    {"role": "user", "content": per_request_note([t.name for t in request_tools])},
                    *messages,
                ]
                logger.info(
                    f"Per-request MCP tools active for this invocation: "
                    f"{[t.name for t in request_tools]}"
                )

        # Operator runbooks: match against this turn's user text (covers chat
        # questions, alert-derived queries and A2A calls — they all arrive
        # here as text) and inject the best match(es) as context. Injected
        # with role "user" for the same checkpointer reason as cluster
        # context below, and deduped per thread so multi-turn conversations
        # don't accumulate copies.
        if self.runbooks is not None:
            matched = self.runbooks.select(query_text)
            dedup_key = thread_id or ""
            already = self._injected_runbooks.get(dedup_key, set())
            fresh = [r for r in matched if r.name not in already]
            if fresh:
                from kubently.modules.runbooks import build_runbook_context

                runbook_context = build_runbook_context(fresh, self.runbooks.max_chars)
                if runbook_context:
                    injected = [r.name for r in fresh if f"Runbook: {r.name} " in runbook_context]
                    logger.info(f"Injecting runbook(s) into investigation: {injected}")
                    structured_log(
                        {"event": "runbooks_injected", "runbooks": injected},
                        thread_id=thread_id,
                    )
                    messages = [{"role": "user", "content": runbook_context}] + messages
                    # Cap the dedup map so long-lived processes don't grow it
                    # unboundedly across threads.
                    if len(self._injected_runbooks) > 1024:
                        self._injected_runbooks.pop(next(iter(self._injected_runbooks)))
                    self._injected_runbooks.setdefault(dedup_key, set()).update(injected)

        # Incident history auto-surface: when this investigation strongly
        # matches a past diagnosed incident in the caller's namespace, inject
        # a one-line "similar past incident" note framed as context to verify,
        # never as a conclusion. Role "user" for the same checkpointer reason
        # as runbooks/cluster context; deduped per thread; the thread's own
        # records are excluded so turn 2 doesn't surface turn 1's diagnosis.
        # Any failure here is logged and skipped — surfacing must never break
        # an investigation.
        if self.incidents is not None and query_text.strip():
            try:
                from kubently.modules.incidents import build_surface_note, caller_namespace

                # Dedup only applies within a real conversation thread: a
                # one-shot request (no thread) is a fresh conversation every
                # time, so nothing to dedup against.
                already = self._surfaced_incidents.get(thread_id, set()) if thread_id else set()
                match = await self.incidents.best_match(
                    caller_namespace(),
                    query_text,
                    cluster_id=cluster_id,
                    exclude_thread_id=thread_id,
                    exclude_ids=already,
                )
                if match:
                    score, past = match
                    logger.info(
                        f"Auto-surfacing past incident {past.id} (score={score})"
                    )
                    structured_log(
                        {"event": "incident_surfaced", "incident_id": past.id, "score": score},
                        thread_id=thread_id,
                    )
                    messages = [{"role": "user", "content": build_surface_note(past)}] + messages
                    if thread_id:
                        if len(self._surfaced_incidents) > 1024:
                            self._surfaced_incidents.pop(next(iter(self._surfaced_incidents)))
                        self._surfaced_incidents.setdefault(thread_id, set()).add(past.id)
            except Exception as e:
                logger.warning(f"Incident auto-surface failed (continuing without): {e}")

        # If cluster_id is specified, inject context at the start.
        # Must be role "user", not "system": the checkpointer appends each turn's
        # input to the thread history, so a per-turn system message lands mid-
        # conversation on turn 2+ and langchain-anthropic rejects it with
        # "Received multiple non-consecutive system messages".
        if cluster_id:
            logger.info(f"Cluster context provided: {cluster_id}")
            cluster_context = {
                "role": "user",
                "content": f"IMPORTANT CONTEXT: The user has selected cluster '{cluster_id}' for this session. "
                           f"Use this cluster_id in all execute_kubectl calls unless the user explicitly "
                           f"requests a different cluster. Do NOT ask which cluster to use - it has been specified."
            }
            messages = [cluster_context] + messages

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

        # Note: For Anthropic models with context management enabled, tool results are automatically
        # cleared server-side to prevent context overflow. No manual intervention needed.

        config = RunnableConfig(
            configurable={"thread_id": actual_thread_id},
            # deepagents adds middleware nodes (planning, filesystem) that consume
            # extra graph steps per tool call, so 25 only allowed ~10-12 kubectl calls
            # and complex scenarios (e.g. cross-namespace RBAC) hit the limit mid-answer.
            recursion_limit=50,
        )

        # Log the prompt being sent
        structured_log({
            "event": "llm_prompt",
            "messages": [{"role": m.__class__.__name__, "content": m.content[:200] if hasattr(m, 'content') else str(m)[:200]} for m in lc_messages],
            "thread_id": actual_thread_id,
            "message_count": len(lc_messages)
        })

        try:
            # Run the single agent (per-request variant when MCP servers were injected)
            result = await run_agent.ainvoke(
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

                    # Incident history: if this answer states a root cause,
                    # persist a compact record to the caller's namespace so
                    # future investigations can find it. Extraction returning
                    # None means no RCA was produced — nothing to record.
                    # Best-effort: a storage failure must never break the
                    # user-facing response.
                    if self.incidents is not None:
                        try:
                            from kubently.modules.incidents import (
                                caller_namespace,
                                extract_incident,
                            )

                            trace = await get_tool_call_interceptor().get_tool_calls_for_thread(
                                actual_thread_id
                            )
                            incident = extract_incident(
                                response_text,
                                user_text=query_text,
                                tool_calls=trace,
                                cluster_id=cluster_id,
                                thread_id=actual_thread_id,
                            )
                            if incident:
                                await self.incidents.record(caller_namespace(), incident)
                                logger.info(f"Recorded incident {incident.id}")
                                structured_log(
                                    {"event": "incident_recorded", "incident_id": incident.id},
                                    thread_id=actual_thread_id,
                                )
                        except Exception as e:
                            logger.warning(f"Incident recording failed (response unaffected): {e}")

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
                "content": f"I encountered an error while processing your request: {e!s}",
                "metadata": {"thread_id": actual_thread_id}
            }
