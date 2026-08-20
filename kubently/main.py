#!/usr/bin/env python3
"""
Kubently - Main Entry Point

This is the thin orchestration layer that:
1. Loads configuration
2. Initializes modules
3. Runs the API and A2A servers

All business logic is in the modules, following black box principles.
"""

import asyncio
import json
import logging
import logging.config as log_config
import uuid
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as redis
import uvicorn
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sse_starlette.sse import EventSourceResponse

from kubently.config.provider import ConfigProvider, EnvConfigProvider
from kubently.logging_config import get_logging_config
from kubently.modules.a2a import a2a_enabled, create_a2a_server
from kubently.modules.api import (
    ArgoCDQueryRequest,
    AuditResponse,
    CommandResponse,
    CommandResult,
    CreateSessionRequest,
    ExecuteCommandRequest,
    ExecutionStatus,
    HelmCommandRequest,
    LogSearchRequest,
    LokiQueryRequest,
    PrometheusQueryRequest,
    SessionResponse,
    SessionStatus,
)
from kubently.modules.api.oidc_discovery import create_discovery_router
from kubently.modules.audit import AuditModule
from kubently.modules.auth.factory import AuthFactory
from kubently.modules.auth.service import AuthenticationService
from kubently.modules.capability import CapabilityModule, ExecutorCapabilities

# Import modules through their black box interfaces
from kubently.modules.config import get_config
from kubently.modules.queue import QueueModule
from kubently.modules.session import SessionModule

# Get configuration
config = get_config()

# Configure logging with health check suppression
log_config.dictConfig(get_logging_config())
logger = logging.getLogger(__name__)

# Configuration provider (centralized config access)
config_provider: ConfigProvider = EnvConfigProvider()

# Module instances (initialized at startup)
auth_service: AuthenticationService | None = None
session_module: SessionModule | None = None
queue_module: QueueModule | None = None
capability_module: CapabilityModule | None = None
audit_module: AuditModule | None = None
redis_client: redis.Redis | None = None
a2a_server = None  # A2A server instance
a2a_app = None  # A2A FastAPI sub-application
pubsub_connections = {}  # Active SSE connections for agents


# Pydantic Models
class CreateTokenRequest(BaseModel):
    """Request model for creating executor tokens with optional custom token."""

    token: str | None = Field(
        None,
        min_length=32,
        max_length=128,
        description="Custom token (32-128 chars). If not provided, a secure token will be auto-generated.",
    )

    @field_validator("token")
    @classmethod
    def validate_token_format(cls, v: str | None) -> str | None:
        """Validate token is alphanumeric, hyphens, or underscores only."""
        # Allow alphanumeric, hyphens, underscores (common in tokens)
        if v is not None and not all(c.isalnum() or c in "-_" for c in v):
            raise ValueError(
                "Token must contain only alphanumeric characters, hyphens, or underscores"
            )
        return v


async def get_redis_client() -> redis.Redis:
    """Create Redis client from configuration."""
    # Build basic Redis URL without password (password passed separately)
    redis_url = (
        f"redis://{config.get('redis_host')}:{config.get('redis_port')}/{config.get('redis_db')}"
    )

    # Get password if configured
    redis_password = config.get("redis_password")

    return await redis.from_url(
        redis_url,
        password=redis_password,  # Pass password as parameter to avoid URL encoding issues
        encoding="utf-8",
        decode_responses=True,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage application lifecycle - initialize and cleanup resources.
    """
    global auth_service, session_module, queue_module, capability_module, redis_client, a2a_server
    global audit_module

    # Startup
    logger.info("Starting Kubently API  ...")

    # Initialize Redis connection
    redis_client = await get_redis_client()

    # Build authentication service via factory (dependency injection)
    auth_service = AuthFactory.build(config_provider, redis_client)
    logger.info("Authentication service initialized via factory")
    session_module = SessionModule(redis_client, default_ttl=config.get("session_ttl"))
    queue_module = QueueModule(
        redis_client, max_commands_per_fetch=config.get("max_commands_per_fetch")
    )
    capability_module = CapabilityModule(
        redis_client, default_ttl=config.get("capability_ttl", 3600)
    )
    audit_module = AuditModule(redis_client)

    # Mount A2A server (core functionality)
    # Get external URL for A2A (for agent card)
    # `or`, not a get() default: the key is always present (set to None when
    # A2A_EXTERNAL_URL is unset), so a get() default never applied and the card
    # fell through to advertising the bind address.
    a2a_external_url = (
        config.get("a2a_external_url") or f"http://localhost:{config.get('port', 8080)}/a2a/"
    )
    a2a_server = create_a2a_server(
        host="0.0.0.0",
        port=config.get("port", 8080),  # Use main API port since A2A is mounted
        external_url=a2a_external_url,
        redis_client=redis_client,
    )
    if a2a_server:
        # A2A module provides its own mount configuration (black box interface)
        mount_path, a2a_app = a2a_server.get_mount_config()
        # Enforce API-key auth at the mount via an explicit ASGI wrapper. The A2A SDK's
        # own add_middleware() doesn't run once the app is mounted (lazy middleware stack),
        # which left /a2a/ open — this wrapper always runs. The agent card stays public.
        from kubently.modules.auth import AuthModule
        from kubently.modules.mcp.server import add_api_key_auth

        authed_a2a = add_api_key_auth(a2a_app, AuthModule(redis_client), public_well_known=True)
        app.mount(mount_path, authed_a2a)
        logger.info(f"A2A server mounted at {mount_path} (API-key auth enforced at mount)")
    elif a2a_enabled():
        # Defensive: create_a2a_server() raises rather than returning None when
        # A2A is expected, so reaching here means that contract was broken.
        # Never continue — a running API with no /a2a/ is the failure mode #97
        # is about.
        logger.error("A2A is enabled but no server was created - this is a critical failure")
        raise RuntimeError("A2A server initialization failed while A2A is enabled")
    else:
        logger.warning(
            "A2A is disabled via KUBENTLY_A2A=off - /a2a/ is not mounted and the agent "
            "protocol surface is unavailable"
        )

    # Mount MCP server (optional - only if the `mcp` SDK is installed).
    # Exposes Kubently's troubleshooting agent as a single natural-language MCP tool.
    mcp_stack = None
    try:
        from kubently.modules.auth import AuthModule
        from kubently.modules.mcp.server import add_api_key_auth, build_mcp_server

        mcp_server = build_mcp_server(redis_client=redis_client)
        mcp_app = mcp_server.streamable_http_app()  # must run before accessing session_manager
        # Require the same API-key auth as the A2A endpoint (the CLI's X-API-Key).
        authed_mcp = add_api_key_auth(mcp_app, AuthModule(redis_client))
        app.mount("/mcp", authed_mcp)
        # Starlette doesn't run a mounted sub-app's lifespan, so start the MCP session
        # manager ourselves and keep it alive for the process lifetime.
        mcp_stack = AsyncExitStack()
        await mcp_stack.enter_async_context(mcp_server.session_manager.run())
        logger.info("MCP server mounted at /mcp")
    except ImportError:
        logger.info("mcp package not installed; MCP server not mounted")
    except Exception as e:
        logger.warning(f"Failed to mount MCP server: {e}")

    # Proactive mode: Alertmanager webhook -> agent diagnosis -> Slack incoming webhook.
    # Auth accepts X-API-Key or Authorization Bearer (verify_dual_auth); the agent stack
    # is imported lazily inside the background task, so mounting is cheap.
    try:
        from kubently.modules.webhook import create_router as create_webhook_router

        app.include_router(create_webhook_router(verify_dual_auth, redis_client=redis_client))
        logger.info("Alertmanager webhook mounted at /webhooks/alertmanager")
    except Exception as e:
        logger.warning(f"Failed to mount alertmanager webhook: {e}")

    # Proactive mode: scheduled fleet health digest -> Slack. Same lazy-agent
    # pattern; triggered by the chart's CronJob or by hand (dry_run to preview).
    try:
        from kubently.modules.webhook import create_fleet_report_router

        app.include_router(create_fleet_report_router(verify_dual_auth, redis_client=redis_client))
        logger.info("Fleet report endpoint mounted at /webhooks/fleet-report")
    except Exception as e:
        logger.warning(f"Failed to mount fleet report endpoint: {e}")

    # Proactive mode: deployment verification -> Slack verdict. Same lazy-agent
    # pattern; triggered by CI webhooks, curl, or the optional label-driven
    # deploy watch (KUBENTLY_VERIFY_WATCH_SECONDS).
    try:
        from kubently.modules.webhook import create_verify_deployment_router

        app.include_router(
            create_verify_deployment_router(verify_dual_auth, redis_client=redis_client)
        )
        logger.info("Deployment verification endpoint mounted at /webhooks/verify-deployment")
    except Exception as e:
        logger.warning(f"Failed to mount deployment verification endpoint: {e}")

    # Proactive mode: named scheduled checks -> Slack (quiet on pass). Same
    # lazy-agent pattern; triggered by the chart's per-check CronJobs.
    try:
        from kubently.modules.webhook import create_scheduled_checks_router

        app.include_router(
            create_scheduled_checks_router(verify_dual_auth, redis_client=redis_client)
        )
        logger.info("Scheduled checks endpoint mounted at /webhooks/scheduled-check")
    except Exception as e:
        logger.warning(f"Failed to mount scheduled checks endpoint: {e}")

    logger.info("Kubently API started successfully")

    yield

    # Shutdown
    logger.info("Shutting down Kubently API...")

    if mcp_stack:
        await mcp_stack.aclose()
    if redis_client:
        await redis_client.close()
    logger.info("Kubently API shutdown complete")


# Create FastAPI application
app = FastAPI(
    title="Kubently API",
    description="Kubently - Troubleshooting Kubernetes Agentically",
    version="1.0.0",
    lifespan=lifespan,
)

# Include OIDC discovery routes (no authentication required)
# Add OIDC discovery router
discovery_router = create_discovery_router(config_provider)
app.include_router(discovery_router, tags=["auth"])


# Dependency injection helpers
async def verify_api_key(
    x_api_key: str | None = Header(None, description="API key for authentication"),
) -> tuple[bool, str | None]:
    """Verify API key and return service identity."""
    if not auth_service:
        raise HTTPException(503, "Service not initialized")

    # Missing credentials are an authentication failure, not a malformed
    # request: a required Header(...) would answer 422 and leak the parameter
    # name, which is not what docs/AUTH_DISCOVERY.md advertises.
    if not x_api_key:
        raise HTTPException(401, "Missing API key", headers={"WWW-Authenticate": "ApiKey"})

    result = await auth_service.authenticate(api_key=x_api_key, authorization=None)

    if not result.ok:
        raise HTTPException(401, "Invalid API key")

    service_identity = result.identity
    is_valid = True
    if not is_valid:
        raise HTTPException(401, "Invalid API key")

    return is_valid, service_identity


async def verify_dual_auth(
    x_api_key: str | None = Header(None, description="API key for authentication"),
    authorization: str | None = Header(None, description="Bearer token for authentication"),
) -> tuple[str, str]:
    """
    Verify either API key or JWT Bearer token using authentication service.

    Returns:
        Tuple of (identity, auth_method)
    """
    if not auth_service:
        raise HTTPException(503, "Service not initialized")

    # Use authentication service facade
    result = await auth_service.authenticate(api_key=x_api_key, authorization=authorization)

    if not result.ok:
        raise HTTPException(status_code=401, detail=f"Authentication failed: {result.error}")

    return result.identity, result.method


async def verify_executor_auth(
    authorization: str = Header(..., description="Bearer token"),
    x_cluster_id: str = Header(..., description="Cluster identifier"),
) -> str:
    """Verify executor authentication."""
    if not redis_client:
        raise HTTPException(503, "Service not initialized")

    token = authorization.replace("Bearer ", "")

    # Check if token matches for this cluster
    stored_token = await redis_client.get(f"executor:token:{x_cluster_id}")
    if not stored_token or stored_token != token:
        raise HTTPException(401, "Invalid executor credentials")

    return x_cluster_id


# Executor Endpoints


@app.get("/executor/stream")
async def executor_stream(cluster_id: str = Depends(verify_executor_auth)):
    """
    SSE endpoint for real-time command streaming to executors.

    Executors connect to this endpoint and receive commands via Server-Sent Events.
    This eliminates polling and provides instant command delivery.

    Returns:
        SSE stream of commands
        401: Unauthorized
    """
    if not redis_client:
        raise HTTPException(503, "Service not initialized")

    logger.info(f"Executor {cluster_id} connecting via SSE")

    # Mark cluster as active when executor connects
    # Use SET with NX EX for first creation (won't overwrite if exists)
    cluster_active_key = f"cluster:active:{cluster_id}"
    try:
        await redis_client.set(cluster_active_key, "1", nx=True, ex=90)
    except Exception as e:
        logger.warning(f"Failed to set cluster active key for {cluster_id}: {e}")

    async def event_generator() -> AsyncGenerator:
        """Generate SSE events from Redis pub/sub."""
        # Create a separate Redis connection for pub/sub
        pubsub = redis_client.pubsub()
        channel = f"executor-commands:{cluster_id}"
        keepalive_interval = 30  # Send keepalive every 30 seconds

        try:
            # Subscribe to executor's command channel
            await pubsub.subscribe(channel)
            logger.info(f"Subscribed to channel: {channel}")

            # Send initial keepalive
            yield {
                "event": "connected",
                "data": json.dumps({"status": "connected", "cluster_id": cluster_id}),
            }

            # Listen for commands with timeout-based keepalives
            while True:
                # Wait efficiently for a message for up to keepalive_interval seconds
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=keepalive_interval
                )

                if message and message["type"] == "message":
                    # Command received - send it to the client
                    command_data = message["data"]
                    if isinstance(command_data, str):
                        logger.info(f"Sending command to executor {cluster_id}")
                        yield {"event": "command", "data": command_data}
                else:
                    # No message within timeout - send keepalive and renew TTL
                    try:
                        await redis_client.expire(cluster_active_key, 90)
                    except Exception as e:
                        logger.warning(f"Failed to renew cluster TTL for {cluster_id}: {e}")

                    yield {
                        "event": "keepalive",
                        "data": json.dumps({"timestamp": asyncio.get_running_loop().time()}),
                    }

        except asyncio.CancelledError:
            logger.info(f"Executor {cluster_id} disconnecting")
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()
            # Do NOT delete cluster:active key - let TTL handle cleanup naturally
            # This prevents false "inactive" state if multiple executors are connected
            logger.info(
                f"Executor {cluster_id} disconnected (cluster will expire via TTL if no other executors)"
            )

    return EventSourceResponse(event_generator())


@app.post("/executor/results")
async def post_result(payload: CommandResult, cluster_id: str = Depends(verify_executor_auth)):
    """
    Endpoint for executors to submit command results.

    Returns:
        200: Result accepted
        401: Unauthorized
    """
    if not queue_module:
        raise HTTPException(503, "Service not initialized")

    # An executor may only answer commands issued to ITS cluster. Executors are
    # operated by the customer whose cluster they run in, so without this check
    # any authenticated executor could submit a result for another tenant's
    # in-flight command and inject fabricated kubectl output into their
    # diagnosis. Unknown/expired command ids are rejected.
    if not await queue_module.command_belongs_to(payload.command_id, cluster_id):
        logger.warning(
            "Rejected result for command %s from cluster %s (not the issuing cluster)",
            payload.command_id,
            cluster_id,
        )
        raise HTTPException(403, "Command was not issued to this cluster")

    # Store result using queue module
    await queue_module.store_result(payload.command_id, payload.dict())

    return {"status": "accepted", "command_id": payload.command_id}


# Capability Endpoints (Executor capability reporting)


class CapabilityReport(BaseModel):
    """Capability report from executor - maps to DynamicCommandWhitelist config."""

    mode: str = Field(
        ...,
        pattern=r"^(readOnly|extendedReadOnly|fullAccess)$",
        description="Security mode (readOnly, extendedReadOnly, fullAccess)",
    )
    allowed_verbs: list[str] = Field(
        default_factory=list, max_length=50, description="Allowed kubectl verbs"
    )
    restricted_resources: list[str] = Field(
        default_factory=list, max_length=50, description="Restricted resources"
    )
    allowed_flags: list[str] = Field(
        default_factory=list, max_length=100, description="Allowed flags"
    )
    executor_version: str | None = Field(None, max_length=50, description="Executor version")
    executor_pod: str | None = Field(
        None,
        max_length=253,  # Max K8s pod name length
        description="Executor pod name",
    )
    cloud: dict[str, Any] | None = Field(
        None,
        description=(
            "Cloud telemetry access held via workload identity: provider, "
            "identity, and the whitelisted operations usable with it. Absent "
            "when the executor holds no cloud identity."
        ),
    )


@app.post("/executor/capabilities")
async def report_capabilities(
    report: CapabilityReport,
    cluster_id: str = Depends(verify_executor_auth),
):
    """
    Report executor capabilities to central API.

    Called by executors on startup to advertise their DynamicCommandWhitelist
    configuration. This allows the API and agent to know what each cluster
    can do before sending commands.

    This endpoint is optional - executors that don't report capabilities
    will still function normally (graceful degradation).

    Returns:
        200: Capabilities stored successfully
        401: Unauthorized
        503: Service not initialized
    """
    if not capability_module:
        raise HTTPException(503, "Service not initialized")

    # Convert report to ExecutorCapabilities
    capabilities = ExecutorCapabilities(
        cluster_id=cluster_id,
        mode=report.mode,
        allowed_verbs=report.allowed_verbs,
        restricted_resources=report.restricted_resources,
        allowed_flags=report.allowed_flags,
        executor_version=report.executor_version,
        executor_pod=report.executor_pod,
        cloud=report.cloud,
        features={
            "exec": report.mode in ["extendedReadOnly", "fullAccess"],
            "port_forward": report.mode in ["extendedReadOnly", "fullAccess"],
            "proxy": report.mode == "fullAccess",
        },
    )

    success = await capability_module.store_capabilities(capabilities)
    if not success:
        raise HTTPException(500, "Failed to store capabilities")

    return {
        "status": "success",
        "message": f"Capabilities stored for cluster {cluster_id}",
        "mode": report.mode,
        "ttl_seconds": capability_module.default_ttl,
    }


@app.post("/executor/heartbeat")
async def executor_heartbeat(
    cluster_id: str = Depends(verify_executor_auth),
):
    """
    Refresh capability TTL (heartbeat).

    Called periodically by executors to keep their capabilities from expiring.
    This is optional - if capabilities expire, the system continues to function
    normally without them.

    Returns:
        200: TTL refreshed
        404: No capabilities found (executor may need to re-report)
        401: Unauthorized
    """
    if not capability_module:
        raise HTTPException(503, "Service not initialized")

    success = await capability_module.refresh_ttl(cluster_id)
    if not success:
        # Not an error - just means no capabilities stored (graceful degradation)
        return {
            "status": "not_found",
            "message": f"No capabilities found for cluster {cluster_id}. Consider re-reporting.",
        }

    return {
        "status": "success",
        "message": f"TTL refreshed for cluster {cluster_id}",
    }


@app.get("/api/v1/clusters/{cluster_id}/capabilities")
async def get_cluster_capabilities(
    cluster_id: str,
    auth_info: tuple[bool, str | None] = Depends(verify_api_key),
):
    """
    Get capabilities for a specific cluster.

    Used by agents to check what operations are allowed before executing commands.
    Returns null capabilities if none are stored (graceful degradation).

    Returns:
        200: Capabilities (or null if not stored)
        401: Unauthorized
    """
    if not capability_module:
        raise HTTPException(503, "Service not initialized")

    capabilities = await capability_module.get_capabilities(cluster_id)

    return {
        "clusterId": cluster_id,
        "capabilities": capabilities.to_dict() if capabilities else None,
        "available": capabilities is not None,
    }


# AI/User/A2A Service Endpoints


async def require_registered_cluster(cluster_id: str) -> None:
    """404 unless an executor is registered for `cluster_id`.

    The cluster registry is executor-owned: only an executor token (written by
    admin registration or the Helm bootstrap) defines a cluster. Callers that
    merely *target* a cluster — session creation, command execution — are
    consumers of that registry and must never be able to write to it.
    """
    if not redis_client:
        raise HTTPException(503, "Service not initialized")

    if await redis_client.exists(f"executor:token:{cluster_id}"):
        return

    valid_clusters = sorted(
        (key.decode() if isinstance(key, bytes) else key).replace("executor:token:", "")
        for key in await redis_client.keys("executor:token:*")
    )
    error_msg = f"Cluster '{cluster_id}' not found."
    if valid_clusters:
        error_msg += f" Available clusters: {', '.join(valid_clusters)}"
    else:
        error_msg += " No clusters are currently registered."

    logger.warning(f"Invalid cluster requested: {cluster_id}")
    raise HTTPException(404, error_msg)


@app.post("/debug/session", response_model=SessionResponse, status_code=201)
async def create_session(
    request: CreateSessionRequest,
    auth_info: tuple[str, str] = Depends(verify_dual_auth),
    x_correlation_id: str | None = Header(None, description="Correlation ID for A2A tracking"),
    x_service_identity: str | None = Header(None, description="Calling service identifier"),
):
    """
    Create a new debugging session for a cluster.

    Returns:
        201: Session created successfully
        401: Unauthorized
        404: No executor registered for that cluster
    """
    if not session_module:
        raise HTTPException(503, "Service not initialized")

    # Same validation /debug/execute performs: a session may only target a
    # cluster that has a registered executor. Without it, session creation
    # injected arbitrary ids into /debug/clusters (issue #89).
    await require_registered_cluster(request.cluster_id)

    _, extracted_service = auth_info
    service_identity = x_service_identity or extracted_service or "direct"

    # Create session with A2A tracking
    session_id = await session_module.create_session(
        cluster_id=request.cluster_id,
        user_id=request.user_id,
        correlation_id=x_correlation_id or request.correlation_id,
        service_identity=service_identity,
    )

    # Get session details for response
    session = await session_module.get_session(session_id)
    if not session:
        raise HTTPException(500, "Failed to create session")

    return SessionResponse(
        session_id=session["session_id"],
        cluster_id=session["cluster_id"],
        status=SessionStatus.ACTIVE,
        created_at=session["created_at"],
        expires_at=session["expires_at"],
        ttl_seconds=session["ttl"],
        correlation_id=session.get("correlation_id"),
        service_identity=session.get("service_identity"),
    )


@app.post("/debug/execute", response_model=CommandResponse)
async def execute_command(
    request: ExecuteCommandRequest,
    auth_info: tuple[bool, str | None] = Depends(verify_api_key),
    x_correlation_id: str | None = Header(None, description="Correlation ID for A2A tracking"),
    x_request_timeout: int | None = Header(
        None, ge=1, le=60, description="Request timeout in seconds"
    ),
):
    """
    Execute a kubectl command by publishing to agent's Redis channel.

    Returns:
        200: Command executed successfully
        408: Request timeout
        401: Unauthorized
    """
    if not redis_client or not queue_module or not session_module:
        raise HTTPException(503, "Service not initialized")

    # === CLUSTER VALIDATION ===
    # Verify the cluster exists before creating any markers or publishing commands
    # This prevents phantom clusters and gives immediate feedback to the agent
    token_key = f"executor:token:{request.cluster_id}"
    cluster_exists = await redis_client.exists(token_key)

    if not cluster_exists:
        # Get list of valid clusters for helpful error message
        valid_clusters = []
        token_keys = await redis_client.keys("executor:token:*")
        for key in token_keys:
            raw = key.decode() if isinstance(key, bytes) else key
            valid_clusters.append(raw.replace("executor:token:", ""))
        valid_clusters.sort()

        error_msg = f"Cluster '{request.cluster_id}' not found."
        if valid_clusters:
            error_msg += f" Available clusters: {', '.join(valid_clusters)}"
        else:
            error_msg += " No clusters are currently registered."

        logger.warning(f"Invalid cluster requested: {request.cluster_id}")
        raise HTTPException(404, error_msg)
    # === END CLUSTER VALIDATION ===

    # === A2A FIX STARTS HERE ===
    # Mark cluster as active for fast polling (only for VALID clusters)
    # This ensures A2A calls get same performance as session-based calls
    cluster_active_key = f"cluster:active:{request.cluster_id}"
    await redis_client.setex(cluster_active_key, 60, "1")  # 60s fast polling window

    # Log for debugging
    if x_correlation_id:
        logger.info(
            f"A2A call detected (correlation: {x_correlation_id}), enabling fast polling for cluster: {request.cluster_id}"
        )
    # === A2A FIX ENDS HERE ===

    # Validate session if provided
    if request.session_id:
        session = await session_module.get_session(request.session_id)
        if not session:
            raise HTTPException(404, "Session not found")
        if session["cluster_id"] != request.cluster_id:
            raise HTTPException(400, "Session cluster mismatch")

        # Keep session alive
        await session_module.keep_alive(request.session_id)

    # Prepare command - combine command_type with args for kubectl format
    kubectl_args = [request.command_type]
    if request.args:
        kubectl_args.extend(request.args)
    if request.namespace:
        kubectl_args.extend(["-n", request.namespace])
    # After building the initial kubectl_args...
    if request.extra_args:
        kubectl_args.extend(request.extra_args)

    command = {
        "id": str(uuid.uuid4()),  # Generate unique command ID
        "args": kubectl_args,
        "timeout": request.timeout_seconds or 10,
        "correlation_id": x_correlation_id or request.correlation_id,
    }

    # Bind this command to the target cluster BEFORE publishing, so a result
    # can only be accepted from the executor that was actually asked. The TTL is
    # derived from how long we will actually wait, so an operator who raises
    # COMMAND_TIMEOUT past the default can't have valid slow results rejected.
    timeout = x_request_timeout or request.timeout_seconds or config.get("command_timeout")
    await queue_module.bind_command(
        command["id"], request.cluster_id, ttl=max(120, int(timeout) * 2)
    )

    # Publish command to executor's Redis channel
    channel = f"executor-commands:{request.cluster_id}"
    await redis_client.publish(channel, json.dumps(command))
    logger.info(f"Published command {command['id']} to channel {channel}")

    # Wait for result using existing queue mechanism
    result = await queue_module.wait_for_result(command["id"], timeout=timeout)

    # === OPTIONAL ENHANCEMENT ===
    # Extend active window if successful (likely more commands coming)
    if result and result.get("success") and not request.session_id:
        await redis_client.expire(cluster_active_key, 60)
    # === END OPTIONAL ===

    # Record what ran, where, and how it ended -- but never the output. This is
    # the only writer of `command_executed` entries; `GET /audit` reads them
    # back. Recording happens after the result (or the timeout) is known so the
    # entry carries a real outcome, and never raises: an audit write must not
    # be able to fail a command that already executed against the cluster.
    _, caller_identity = auth_info
    if audit_module:
        # Outcome comes from `success`, not from `status`. The executor posts a
        # CommandResult, whose model has no `status` field at all -- so the
        # `result.get("status", SUCCESS)` used for the HTTP response below
        # always falls through to "success", and a kubectl call that came back
        # Forbidden would be filed as a successful one. An audit trail that
        # records every denied command as succeeded is worse than no trail.
        if not result:
            outcome = str(ExecutionStatus.TIMEOUT)
            error = "Command execution timeout"
        else:
            outcome = str(
                ExecutionStatus.SUCCESS if result.get("success") else ExecutionStatus.FAILURE
            )
            error = result.get("error")

        await audit_module.record_command(
            service_identity=caller_identity,
            cluster_id=request.cluster_id,
            command_id=command["id"],
            args=kubectl_args,
            session_id=request.session_id,
            outcome=outcome,
            error=error,
            correlation_id=x_correlation_id or request.correlation_id,
        )

    if not result:
        return CommandResponse(
            command_id=command["id"],
            session_id=request.session_id,
            cluster_id=request.cluster_id,
            status=ExecutionStatus.TIMEOUT,
            correlation_id=x_correlation_id or request.correlation_id,
            error="Command execution timeout",
        )

    return CommandResponse(
        command_id=command["id"],
        session_id=request.session_id,
        cluster_id=request.cluster_id,
        status=result.get("status", ExecutionStatus.SUCCESS),
        correlation_id=x_correlation_id or request.correlation_id,
        output=result.get("output"),
        error=result.get("error"),
        execution_time_ms=result.get("execution_time_ms"),
        executed_at=result.get("executed_at"),
    )


async def _run_executor_tool(
    cluster_id: str,
    tool: str,
    tool_request: dict,
    timeout: int,
    correlation_id: str | None,
    timeout_error: str,
) -> CommandResponse:
    """Publish a non-kubectl tool envelope to a cluster's executor and await
    the result.

    Shares the /debug/execute flow: validate the cluster (fail fast with the
    list of valid clusters instead of queueing a command nothing will pick
    up), mark it active for fast polling, bind the command id to the cluster
    so only the asked executor can answer, publish, wait.
    """
    token_key = f"executor:token:{cluster_id}"
    if not await redis_client.exists(token_key):
        valid_clusters = sorted(
            (k.decode() if isinstance(k, bytes) else k).replace("executor:token:", "")
            for k in await redis_client.keys("executor:token:*")
        )
        error_msg = f"Cluster '{cluster_id}' not found."
        if valid_clusters:
            error_msg += f" Available clusters: {', '.join(valid_clusters)}"
        else:
            error_msg += " No clusters are currently registered."
        raise HTTPException(404, error_msg)

    cluster_active_key = f"cluster:active:{cluster_id}"
    await redis_client.setex(cluster_active_key, 60, "1")

    command = {
        "id": str(uuid.uuid4()),
        "tool": tool,
        "request": tool_request,
        "timeout": timeout,
        "correlation_id": correlation_id,
    }

    await queue_module.bind_command(command["id"], cluster_id, ttl=max(120, timeout * 2))

    channel = f"executor-commands:{cluster_id}"
    await redis_client.publish(channel, json.dumps(command))
    logger.info(f"Published {tool} command {command['id']} to channel {channel}")

    result = await queue_module.wait_for_result(command["id"], timeout=timeout)

    if not result:
        return CommandResponse(
            command_id=command["id"],
            session_id=None,
            cluster_id=cluster_id,
            status=ExecutionStatus.TIMEOUT,
            correlation_id=correlation_id,
            error=timeout_error,
        )

    return CommandResponse(
        command_id=command["id"],
        session_id=None,
        cluster_id=cluster_id,
        status=ExecutionStatus.SUCCESS if result.get("success") else ExecutionStatus.FAILURE,
        correlation_id=correlation_id,
        output=result.get("output"),
        error=result.get("error"),
        execution_time_ms=result.get("execution_time_ms"),
        executed_at=result.get("executed_at"),
    )


class CloudExecuteRequest(BaseModel):
    """Request to run a whitelisted cloud read operation on a cluster's executor."""

    cluster_id: str = Field(..., min_length=1, max_length=253)
    operation: str = Field(
        ...,
        max_length=100,
        description="Whitelisted operation name, e.g. 'aws.logs.insights_query'",
    )
    params: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int | None = Field(
        None, ge=1, le=60, description="How long to wait for the executor"
    )
    correlation_id: str | None = None


@app.post("/cloud/execute", response_model=CommandResponse)
async def execute_cloud_operation(
    request: CloudExecuteRequest,
    auth_info: tuple[bool, str | None] = Depends(verify_api_key),
    x_correlation_id: str | None = Header(None, description="Correlation ID for A2A tracking"),
):
    """
    Execute a read-only cloud telemetry operation on a cluster's executor.

    The executor queries cloud APIs (CloudWatch, CloudTrail, Cloud Logging,
    Cloud Monitoring) from inside the customer's account using the workload
    identity its pod holds. Results stream back over the existing outbound-only
    channel; no cloud credentials ever touch this control plane.

    The operation must be on the code-level allowlist — validated here AND
    again on the executor, so neither side alone can widen the surface.

    Returns:
        200: Operation result (structured JSON in `output`)
        400: Operation not on the allowlist
        404: Unknown cluster
        401: Unauthorized
    """
    if not redis_client or not queue_module:
        raise HTTPException(503, "Service not initialized")

    # Allowlist validation (dependency-free import; no cloud SDKs needed here)
    from kubently.modules.executor.cloud.operations import ALLOWED_CLOUD_OPERATIONS

    if request.operation not in ALLOWED_CLOUD_OPERATIONS:
        raise HTTPException(
            400,
            f"Operation '{request.operation}' is not on the cloud operation "
            f"allowlist. Allowed operations: {sorted(ALLOWED_CLOUD_OPERATIONS)}",
        )

    # Cloud queries poll remote APIs (Logs Insights can take ~25s), so the
    # default wait is longer than kubectl's.
    return await _run_executor_tool(
        cluster_id=request.cluster_id,
        tool="cloud",
        tool_request={"operation": request.operation, "params": request.params},
        timeout=request.timeout_seconds or 40,
        correlation_id=x_correlation_id or request.correlation_id,
        timeout_error=(
            "Cloud operation timeout - the executor may be offline, or "
            "may not have cloud mode enabled"
        ),
    )


@app.post("/debug/logs/search", response_model=CommandResponse)
async def search_logs(
    request: LogSearchRequest,
    auth_info: tuple[bool, str | None] = Depends(verify_api_key),
    x_correlation_id: str | None = Header(None, description="Correlation ID for A2A tracking"),
):
    """
    Search logs across the pods matching a selector on one cluster.

    The search rides the same outbound channel as kubectl commands (Redis
    pub/sub -> executor SSE -> result POST): the executor inside the target
    cluster resolves pods, fetches logs through its whitelist-enforced kubectl
    runner, filters locally and returns only matching lines (capped). Raw logs
    never transit this API.

    Returns:
        200: Search result (or executor-side error)
        404: Cluster not found
        401: Unauthorized
    """
    if not redis_client or not queue_module:
        raise HTTPException(503, "Service not initialized")

    return await _run_executor_tool(
        cluster_id=request.cluster_id,
        tool="log_search",
        tool_request={
            "namespace": request.namespace,
            "selector": request.selector,
            "pod_name": request.pod_name,
            "container": request.container,
            "query": request.query,
            "use_regex": request.use_regex,
            "case_sensitive": request.case_sensitive,
            "since": request.since,
            "since_time": request.since_time,
            "tail_lines": request.tail_lines,
            "previous": request.previous,
            "context_lines": request.context_lines,
        },
        timeout=request.timeout_seconds or 60,
        correlation_id=x_correlation_id or request.correlation_id,
        timeout_error="Log search timeout (no executor picked it up in time)",
    )


@app.post("/debug/loki", response_model=CommandResponse)
async def execute_loki_query(
    request: LokiQueryRequest,
    auth_info: tuple[bool, str | None] = Depends(verify_api_key),
    x_correlation_id: str | None = Header(None, description="Correlation ID for A2A tracking"),
):
    """
    Run a read-only LogQL range query on a cluster's Loki.

    The query rides the same outbound channel as kubectl commands: the
    executor inside the target cluster performs the HTTP GET against its
    locally configured LOKI_URL. This API never contacts Loki directly and
    never forwards a URL — only the validated query parameters.

    Returns:
        200: Query result (or executor-side error, e.g. Loki not configured)
        404: Cluster not found
        401: Unauthorized
    """
    if not redis_client or not queue_module:
        raise HTTPException(503, "Service not initialized")

    return await _run_executor_tool(
        cluster_id=request.cluster_id,
        tool="loki",
        tool_request={
            "query": request.query,
            "start": request.start,
            "end": request.end,
            "limit": request.limit,
            "direction": request.direction.value,
        },
        timeout=request.timeout_seconds or 30,
        correlation_id=x_correlation_id or request.correlation_id,
        timeout_error="Loki query timeout (no executor picked it up in time)",
    )


@app.post("/debug/prometheus", response_model=CommandResponse)
async def execute_prometheus_query(
    request: PrometheusQueryRequest,
    auth_info: tuple[bool, str | None] = Depends(verify_api_key),
    x_correlation_id: str | None = Header(None, description="Correlation ID for A2A tracking"),
):
    """
    Run a read-only PromQL query on a cluster's Prometheus.

    The query rides the same outbound channel as kubectl commands (Redis
    pub/sub -> executor SSE -> result POST): the executor inside the target
    cluster performs the HTTP GET against its locally configured
    PROMETHEUS_URL. This API never contacts Prometheus directly and never
    forwards a URL — only the validated query parameters.

    Returns:
        200: Query result (or executor-side error, e.g. Prometheus not configured)
        404: Cluster not found
        401: Unauthorized
    """
    if not redis_client or not queue_module:
        raise HTTPException(503, "Service not initialized")

    return await _run_executor_tool(
        cluster_id=request.cluster_id,
        tool="prometheus",
        tool_request={
            "query_type": request.query_type.value,
            "query": request.query,
            "time": request.time,
            "start": request.start,
            "end": request.end,
            "step": request.step,
        },
        timeout=request.timeout_seconds or 30,
        correlation_id=x_correlation_id or request.correlation_id,
        timeout_error="Prometheus query timeout (no executor picked it up in time)",
    )


@app.post("/debug/helm", response_model=CommandResponse)
async def execute_helm_command(
    request: HelmCommandRequest,
    auth_info: tuple[bool, str | None] = Depends(verify_api_key),
    x_correlation_id: str | None = Header(None, description="Correlation ID for A2A tracking"),
):
    """
    Run a read-only helm subcommand (history/list) on a cluster's executor.

    Used for change correlation: helm release history answers "what was
    deployed, and when?". The executor builds the argv from these validated
    fields — no raw arguments travel over the channel. Requires the executor
    to have helm history enabled (HELM_HISTORY_ENABLED / RBAC on release
    Secrets); otherwise the executor answers with a clear "unavailable" error.

    Returns:
        200: Subcommand result (or executor-side error)
        404: Cluster not found
        401: Unauthorized
    """
    if not redis_client or not queue_module:
        raise HTTPException(503, "Service not initialized")

    return await _run_executor_tool(
        cluster_id=request.cluster_id,
        tool="helm",
        tool_request={
            "subcommand": request.subcommand.value,
            "release_name": request.release_name,
            "namespace": request.namespace,
            "max": request.max,
        },
        timeout=request.timeout_seconds or 30,
        correlation_id=x_correlation_id or request.correlation_id,
        timeout_error="Helm command timeout (no executor picked it up in time)",
    )


@app.post("/debug/argocd", response_model=CommandResponse)
async def execute_argocd_query(
    request: ArgoCDQueryRequest,
    auth_info: tuple[bool, str | None] = Depends(verify_api_key),
    x_correlation_id: str | None = Header(None, description="Correlation ID for A2A tracking"),
):
    """
    Run a read-only ArgoCD Application query on a cluster's executor.

    Used for change correlation: sync history answers "which GitOps deploys
    happened, and when?". The executor performs the HTTP GET against its
    locally configured ARGOCD_URL with its own token — this API never
    contacts ArgoCD and never forwards a URL or credentials.

    Returns:
        200: Query result (or executor-side error, e.g. ArgoCD not configured)
        404: Cluster not found
        401: Unauthorized
    """
    if not redis_client or not queue_module:
        raise HTTPException(503, "Service not initialized")

    return await _run_executor_tool(
        cluster_id=request.cluster_id,
        tool="argocd",
        tool_request={
            "operation": request.operation.value,
            "app_name": request.app_name,
            "revision": request.revision,
            "selector": request.selector,
        },
        timeout=request.timeout_seconds or 30,
        correlation_id=x_correlation_id or request.correlation_id,
        timeout_error="ArgoCD query timeout (no executor picked it up in time)",
    )


@app.get("/debug/session/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str, auth_info: tuple[bool, str | None] = Depends(verify_api_key)
):
    """
    Get session status (useful for A2A polling).

    Returns:
        200: Session details
        404: Session not found
        401: Unauthorized
    """
    if not session_module:
        raise HTTPException(503, "Service not initialized")

    session = await session_module.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    return SessionResponse(
        session_id=session["session_id"],
        cluster_id=session["cluster_id"],
        status=SessionStatus.ACTIVE if session.get("active") else SessionStatus.IDLE,
        created_at=session["created_at"],
        expires_at=session["expires_at"],
        ttl_seconds=session["ttl"],
        correlation_id=session.get("correlation_id"),
        service_identity=session.get("service_identity"),
    )


@app.delete("/debug/session/{session_id}", status_code=204)
async def end_session(
    session_id: str, auth_info: tuple[bool, str | None] = Depends(verify_api_key)
):
    """
    End a debugging session.

    Returns:
        204: Session ended
        404: Session not found
        401: Unauthorized
    """
    if not session_module:
        raise HTTPException(503, "Service not initialized")

    session = await session_module.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    await session_module.end_session(session_id)
    return Response(status_code=204)


# Admin Endpoints for CLI


@app.post("/admin/agents/{cluster_id}/token")
async def create_agent_token(
    cluster_id: str,
    request: CreateTokenRequest | None = Body(None),
    auth_info: tuple[bool, str | None] = Depends(verify_api_key),
):
    """
    Create authentication token for cluster executor.

    Supports two modes:
    1. Auto-generate: POST without body - generates a secure random token
    2. Custom token: POST with {"token": "your-token"} - uses your provided token

    The custom token must be 32-128 characters and contain only alphanumeric
    characters, hyphens, or underscores.

    Args:
        cluster_id: Unique identifier for the cluster
        request: Optional request body with custom token

    Returns:
        201: Token created successfully
        400: Invalid token format
        401: Unauthorized
        409: Token already exists for this cluster
        500: Internal server error

    Examples:
        # Auto-generate token
        POST /admin/agents/my-cluster/token

        # Provide custom token
        POST /admin/agents/my-cluster/token
        {"token": "my-secret-token-from-vault-abc123"}
    """
    if not redis_client:
        raise HTTPException(503, "Service not initialized")

    try:
        # Check if token already exists for this cluster
        existing_token = await redis_client.get(f"executor:token:{cluster_id}")
        if existing_token:
            raise HTTPException(
                409,
                f"Token already exists for cluster '{cluster_id}'. Delete it first to create a new one.",
            )

        # Use custom token if provided, otherwise auto-generate
        if request and request.token:
            token = request.token
            logger.info(
                f"Created executor token for cluster '{cluster_id}' (custom token provided)"
            )
        else:
            # Generate secure token for executor
            import secrets

            token = secrets.token_hex(32)  # 64 character hex string
            logger.info(f"Created executor token for cluster '{cluster_id}' (auto-generated)")

        # Store token: executor:token:{cluster_id} = token_value
        await redis_client.set(f"executor:token:{cluster_id}", token)

        return {
            "token": token,
            "clusterId": cluster_id,
            "createdAt": datetime.now(UTC).isoformat(),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create token for cluster {cluster_id}: {e}")
        raise HTTPException(500, "Failed to create token") from e


@app.get("/admin/agents")
async def list_agents(
    auth_info: tuple[bool, str | None] = Depends(verify_api_key),
):
    """
    List all registered cluster executors.

    Returns information about all clusters that have executor tokens registered,
    including their connection status.

    Returns:
        200: List of cluster executors with connection status
        401: Unauthorized
        503: Service unavailable
    """
    if not redis_client:
        raise HTTPException(503, "Service not initialized")

    try:
        clusters = []

        # Get all executor tokens from Redis
        token_prefix = "executor:token:"
        token_keys = await redis_client.keys(f"{token_prefix}*")
        for key in token_keys:
            raw = key.decode() if isinstance(key, bytes) else key
            if raw.startswith(token_prefix):
                cluster_id = raw[len(token_prefix) :]

                # Check if cluster is currently connected (has active marker)
                is_active = await redis_client.exists(f"cluster:active:{cluster_id}")

                clusters.append(
                    {
                        "id": cluster_id,
                        "connected": bool(is_active),
                        "lastSeen": None,  # Could track with TTL timestamp
                    }
                )

        # Sort by cluster_id for consistent ordering
        clusters.sort(key=lambda x: x["id"])

        return {"clusters": clusters, "count": len(clusters)}

    except Exception as e:
        logger.error(f"Failed to list agents: {e}")
        raise HTTPException(500, "Failed to list agents") from e


@app.get("/admin/agents/{cluster_id}/status")
async def get_agent_status(
    cluster_id: str,
    auth_info: tuple[bool, str | None] = Depends(verify_api_key),
):
    """
    Get detailed status of a specific cluster executor.

    Returns connection status and metadata for a cluster executor.

    Returns:
        200: Executor status details
        404: Executor not found (no token registered)
        401: Unauthorized
    """
    if not redis_client:
        raise HTTPException(503, "Service not initialized")

    try:
        # Check if executor token exists
        redis_key = f"executor:token:{cluster_id}"
        has_token = await redis_client.exists(redis_key)

        if not has_token:
            raise HTTPException(404, f"No executor found for cluster '{cluster_id}'")

        # Check if cluster is currently connected
        active_key = f"cluster:active:{cluster_id}"
        is_active = await redis_client.exists(active_key)

        # Get capabilities if available (graceful degradation)
        capabilities = None
        capabilities_mode = None
        if capability_module:
            caps = await capability_module.get_capabilities(cluster_id)
            if caps:
                capabilities = caps.to_dict()
                capabilities_mode = caps.mode

        return {
            "id": cluster_id,
            "connected": bool(is_active),
            "status": "connected" if is_active else "disconnected",
            "lastSeen": None,  # Could track with Redis TTL or separate timestamp
            "version": capabilities.get("executor_version") if capabilities else None,
            "kubernetesVersion": None,  # Could be reported by executor in future
            "capabilities": capabilities,  # Full capability details (null if not reported)
            "mode": capabilities_mode,  # Quick access to security mode
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get status for cluster {cluster_id}: {e}")
        raise HTTPException(500, "Failed to get agent status") from e


@app.delete("/admin/agents/{cluster_id}/token")
async def revoke_agent_token(
    cluster_id: str,
    auth_info: tuple[bool, str | None] = Depends(verify_api_key),
):
    """
    Revoke authentication token for cluster executor.

    Deletes the executor token from Redis, preventing further authentication.
    Any connected executor will be disconnected on the next authentication attempt.

    Returns:
        204: Token revoked successfully
        404: Token not found for this cluster
        401: Unauthorized
    """
    if not redis_client:
        raise HTTPException(503, "Service not initialized")

    try:
        redis_key = f"executor:token:{cluster_id}"
        token_exists = await redis_client.exists(redis_key)

        if not token_exists:
            raise HTTPException(404, f"No token found for cluster '{cluster_id}'")

        # Delete the token from Redis
        await redis_client.delete(redis_key)

        # Also remove cluster active marker if exists
        await redis_client.delete(f"cluster:active:{cluster_id}")

        # Clean up capabilities to prevent stale advertisements
        await redis_client.delete(f"cluster:{cluster_id}:capabilities")

        logger.info(f"Revoked executor token for cluster: {cluster_id}")
        return Response(status_code=204)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to revoke token for cluster {cluster_id}: {e}")
        raise HTTPException(500, "Failed to revoke token") from e


# Cluster Management Endpoints


@app.get("/debug/clusters")
async def list_clusters(
    auth_info: tuple[bool, str | None] = Depends(verify_api_key),
):
    """
    List available Kubernetes clusters.

    Registered executors are the only source: `cluster:active:*` and
    `cluster:session:*` are written by session creation, so including them
    let any API-key holder inject a phantom cluster into the fleet the agent
    sees (issue #89).

    Returns:
        JSON with list of cluster IDs that can be targeted for debugging
    """
    try:
        clusters_set = set()

        if redis_client:
            # Executor tokens: executor:token:<id>
            token_prefix = "executor:token:"
            token_keys = await redis_client.keys(f"{token_prefix}*")
            for key in token_keys:
                raw = key.decode() if isinstance(key, bytes) else key
                if raw.startswith(token_prefix):
                    clusters_set.add(raw[len(token_prefix) :])

        clusters = sorted(clusters_set)

        return {"clusters": clusters, "count": len(clusters)}

    except Exception as e:
        logger.error(f"Failed to list clusters: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/debug/clusters/{cluster_id}")
async def get_cluster_detail(
    cluster_id: str,
    auth_info: tuple[bool, str | None] = Depends(verify_api_key),
):
    """
    Get detailed status for a specific cluster, including capabilities.

    Returns comprehensive cluster information including:
    - Token status (whether executor token exists)
    - Active session status
    - Executor capabilities (if reported)
    - Capability TTL remaining

    This endpoint supports graceful degradation - if capabilities haven't
    been reported, the capabilities field will be null but other status
    information will still be returned.

    Returns:
        200: Cluster details
        401: Unauthorized
        404: Cluster not found
    """
    if not capability_module:
        raise HTTPException(503, "Service not initialized")

    try:
        # Use capability module's aggregation method
        detail = await capability_module.get_cluster_detail(cluster_id)

        # Check if cluster exists at all (has token, session, or capabilities)
        has_token = detail["status"].get("hasToken", False)
        has_session = detail["status"].get("hasActiveSession", False)
        has_capabilities = detail["status"].get("executorReporting", False)

        if not (has_token or has_session or has_capabilities):
            raise HTTPException(
                status_code=404,
                detail=f"Cluster '{cluster_id}' not found. No token, session, or capabilities exist.",
            )

        return detail

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get cluster detail for {cluster_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


# Health/Monitoring Endpoints


def _parse_audit_time(value: str | None, field: str) -> datetime | None:
    """Parse an ISO 8601 query parameter, defaulting a naive value to UTC."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(400, f"Invalid {field}: expected ISO 8601, got '{value}'") from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@app.get("/audit", response_model=AuditResponse)
async def get_audit(
    event_type: str | None = Query(None, description="Only this event type, e.g. command_executed"),
    cluster_id: str | None = Query(None, description="Only entries targeting this cluster"),
    session_id: str | None = Query(None, description="Only entries from this debug session"),
    since: str | None = Query(None, description="Only entries at or after this ISO 8601 time"),
    until: str | None = Query(None, description="Only entries at or before this ISO 8601 time"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum entries to return"),
    auth_info: tuple[bool, str | None] = Depends(verify_api_key),
):
    """
    Read the command audit trail. Read-only.

    This endpoint never writes: it issues a single LRANGE and returns what it
    finds. There is deliberately no companion POST/PUT/DELETE, and no way to
    reach the list's TTL or trim behaviour from here -- retention is a property
    of the deployment (see docs/AUDIT.md), not something a caller may adjust.

    **Scope.** The response contains only entries stamped with the caller's own
    service identity. There is no parameter that widens that, and no admin
    identity that bypasses it. A caller who asks for a cluster only somebody
    else has touched gets an empty list, not somebody else's commands.

    Because scoping keys off the identity, an API key configured without one
    (a bare `key` rather than `service:key` in API_KEYS) has no scope to read
    and is refused, rather than being quietly shown everything.
    """
    if not audit_module:
        raise HTTPException(503, "Service not initialized")

    _, identity = auth_info
    if not identity:
        raise HTTPException(
            403,
            "This API key has no service identity, so its audit scope cannot be "
            "determined. Configure the key as 'service-name:key' in API_KEYS.",
        )

    entries = await audit_module.query(
        identity=identity,
        event_type=event_type,
        cluster_id=cluster_id,
        session_id=session_id,
        since=_parse_audit_time(since, "since"),
        until=_parse_audit_time(until, "until"),
        limit=limit,
    )

    return AuditResponse(entries=entries, count=len(entries), service_identity=identity)


@app.get("/healthz")
async def healthz():
    """
    Minimal health check endpoint for Kubernetes readiness/liveness probes.

    This endpoint is unauthenticated and returns a simple OK response.
    Use this for container orchestration health checks.

    Returns:
        200: Service is running
    """
    return {"status": "ok"}


@app.get("/health")
async def health_check(request: Request):
    """
    Enhanced health check endpoint with security status.

    Returns:
        200: Service healthy
        503: Service unhealthy
    """
    try:
        # Check Redis connection
        if redis_client:
            await redis_client.ping()
            redis_status = "connected"
        else:
            redis_status = "disconnected"

        # Check module initialization
        modules_ready = all([auth_service, session_module, queue_module])

        # Check if we're running with TLS
        tls_status = "enabled" if request.url.scheme == "https" else "disabled"

        # Warn if not using TLS in production mode
        environment = config.get("environment", "development")
        if environment == "production" and tls_status == "disabled":
            logger.warning("⚠️ Running in production without TLS!")

        if redis_status == "connected" and modules_ready:
            return {
                "status": "healthy",
                "redis": redis_status,
                "modules": "initialized",
                "tls": tls_status,
                "environment": environment,
                "version": "1.0.0",
            }
        else:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "unhealthy",
                    "redis": redis_status,
                    "modules": "not initialized" if not modules_ready else "initialized",
                    "tls": tls_status,
                    "environment": environment,
                },
            )
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return JSONResponse(status_code=503, content={"status": "unhealthy", "error": str(e)})


@app.get("/metrics")
async def metrics():
    """
    Prometheus-compatible metrics endpoint (optional).

    Returns basic metrics about the system.
    """
    if not session_module or not queue_module:
        return Response(content="", status_code=503)

    # Get basic metrics
    active_sessions = await session_module.get_active_sessions()

    # Format as Prometheus metrics
    metrics_text = f"""# HELP kubently_active_sessions Number of active debugging sessions
# TYPE kubently_active_sessions gauge
kubently_active_sessions {len(active_sessions)}
"""

    return Response(content=metrics_text, media_type="text/plain")


# Error handlers


@app.exception_handler(redis.ConnectionError)
async def redis_error_handler(request, exc):
    """Handle Redis connection errors."""
    logger.error(f"Redis connection error: {exc}")
    return JSONResponse(status_code=503, content={"error": "Database connection failed"})


@app.exception_handler(ValueError)
async def validation_error_handler(request, exc):
    """Handle validation errors."""
    logger.error(f"Validation error: {exc}")
    return JSONResponse(status_code=400, content={"error": str(exc)})


if __name__ == "__main__":
    # Use dict config for logging, not file path
    uvicorn.run(
        "main:app",
        host=config.get("host"),
        port=config.get("port"),
        log_level=config.get("log_level").lower(),
        reload=config.get("debug"),
        log_config=get_logging_config(),
    )
