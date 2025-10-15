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
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional, Tuple

import redis.asyncio as redis
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from kubently.modules.a2a import create_a2a_server
from kubently.modules.api import (
    CommandResponse,
    CommandResult,
    CreateSessionRequest,
    ExecuteCommandRequest,
    ExecutionStatus,
    SessionResponse,
    SessionStatus,
)
from kubently.config.provider import EnvConfigProvider, ConfigProvider
from kubently.modules.auth.factory import AuthFactory
from kubently.modules.auth.service import AuthenticationService, AuthResult
from kubently.modules.api.oidc_discovery import create_discovery_router

# Import modules through their black box interfaces
from kubently.modules.config import get_config
from kubently.modules.queue import QueueModule
from kubently.modules.session import SessionModule

# Get configuration
config = get_config()

# Configure logging with health check suppression
from kubently.logging_config import get_logging_config
import logging.config as log_config

log_config.dictConfig(get_logging_config())
logger = logging.getLogger(__name__)

# Configuration provider (centralized config access)
config_provider: ConfigProvider = EnvConfigProvider()

# Module instances (initialized at startup)
auth_service: Optional[AuthenticationService] = None
session_module: Optional[SessionModule] = None
queue_module: Optional[QueueModule] = None
redis_client: Optional[redis.Redis] = None
a2a_server = None  # A2A server instance
a2a_app = None  # A2A FastAPI sub-application
pubsub_connections = {}  # Active SSE connections for agents


async def get_redis_client() -> redis.Redis:
    """Create Redis client from configuration."""
    # Build basic Redis URL without password (password passed separately)
    redis_url = f"redis://{config.get('redis_host')}:{config.get('redis_port')}/{config.get('redis_db')}"

    # Get password if configured
    redis_password = config.get('redis_password')

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
    global auth_service, session_module, queue_module, redis_client, a2a_server

    # Startup
    logger.info("Starting Kubently API with Black Box Architecture...")

    # Initialize Redis connection
    redis_client = await get_redis_client()

    # Build authentication service via factory (dependency injection)
    auth_service = AuthFactory.build(config_provider, redis_client)
    logger.info("Authentication service initialized via factory")
    session_module = SessionModule(redis_client, default_ttl=config.get("session_ttl"))
    queue_module = QueueModule(
        redis_client, max_commands_per_fetch=config.get("max_commands_per_fetch")
    )

    # Mount A2A server (core functionality)
    # Get external URL for A2A (for agent card)
    a2a_external_url = config.get(
        "a2a_external_url", f"http://localhost:{config.get('port', 8080)}/a2a/"
    )
    a2a_server = create_a2a_server(
        host="0.0.0.0",
        port=config.get("port", 8080),  # Use main API port since A2A is mounted
        external_url=a2a_external_url,
        redis_client=redis_client
    )
    if a2a_server:
        # A2A module provides its own mount configuration (black box interface)
        mount_path, a2a_app = a2a_server.get_mount_config()
        app.mount(mount_path, a2a_app)
        logger.info(f"A2A server mounted at {mount_path} on main port {config.get('port', 8080)}")
    else:
        logger.error("Failed to initialize A2A server - this is a critical failure")
        raise RuntimeError("A2A server initialization failed")

    logger.info("Kubently API started successfully")

    yield

    # Shutdown
    logger.info("Shutting down Kubently API...")


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
    x_api_key: str = Header(..., description="API key for authentication")
) -> Tuple[bool, Optional[str]]:
    """Verify API key and return service identity."""
    if not auth_service:
        raise HTTPException(503, "Service not initialized")

    result = await auth_service.authenticate(api_key=x_api_key, authorization=None)
    
    if not result.ok:
        raise HTTPException(401, "Invalid API key")
    
    service_identity = result.identity
    is_valid = True
    if not is_valid:
        raise HTTPException(401, "Invalid API key")

    return is_valid, service_identity


async def verify_dual_auth(
    x_api_key: Optional[str] = Header(None, description="API key for authentication"),
    authorization: Optional[str] = Header(None, description="Bearer token for authentication")
) -> Tuple[str, str]:
    """
    Verify either API key or JWT Bearer token using authentication service.
    
    Returns:
        Tuple of (identity, auth_method)
    """
    if not auth_service:
        raise HTTPException(503, "Service not initialized")
    
    # Use authentication service facade
    result = await auth_service.authenticate(
        api_key=x_api_key,
        authorization=authorization
    )
    
    if not result.ok:
        raise HTTPException(
            status_code=401,
            detail=f"Authentication failed: {result.error}"
        )
    
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

        try:
            # Subscribe to executor's command channel
            await pubsub.subscribe(channel)
            logger.info(f"Subscribed to channel: {channel}")

            # Send initial keepalive
            yield {
                "event": "connected",
                "data": json.dumps({"status": "connected", "cluster_id": cluster_id}),
            }

            # Listen for commands
            async for message in pubsub.listen():
                if message["type"] == "message":
                    # Command received from Redis
                    command_data = message["data"]
                    if isinstance(command_data, str):
                        logger.info(f"Sending command to executor {cluster_id}")
                        yield {"event": "command", "data": command_data}

                # Send periodic keepalive to detect disconnections
                # Also renew cluster active status with EXPIRE (more efficient than SETEX)
                try:
                    await redis_client.expire(cluster_active_key, 90)
                except Exception as e:
                    logger.warning(f"Failed to renew cluster TTL for {cluster_id}: {e}")

                yield {
                    "event": "keepalive",
                    "data": json.dumps({"timestamp": asyncio.get_event_loop().time()}),
                }

        except asyncio.CancelledError:
            logger.info(f"Executor {cluster_id} disconnecting")
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()
            # Do NOT delete cluster:active key - let TTL handle cleanup naturally
            # This prevents false "inactive" state if multiple executors are connected
            logger.info(f"Executor {cluster_id} disconnected (cluster will expire via TTL if no other executors)")

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

    # Store result using queue module
    await queue_module.store_result(payload.command_id, payload.dict())

    return {"status": "accepted", "command_id": payload.command_id}


# AI/User/A2A Service Endpoints


@app.post("/debug/session", response_model=SessionResponse, status_code=201)
async def create_session(
    request: CreateSessionRequest,
    auth_info: Tuple[str, str] = Depends(verify_dual_auth),
    x_correlation_id: Optional[str] = Header(None, description="Correlation ID for A2A tracking"),
    x_service_identity: Optional[str] = Header(None, description="Calling service identifier"),
):
    """
    Create a new debugging session for a cluster.

    Returns:
        201: Session created successfully
        401: Unauthorized
    """
    if not session_module:
        raise HTTPException(503, "Service not initialized")

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
    auth_info: Tuple[bool, Optional[str]] = Depends(verify_api_key),
    x_correlation_id: Optional[str] = Header(None, description="Correlation ID for A2A tracking"),
    x_request_timeout: Optional[int] = Header(
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

    # === A2A FIX STARTS HERE ===
    # Always mark cluster as active for fast polling
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

    # Publish command to executor's Redis channel
    channel = f"executor-commands:{request.cluster_id}"
    await redis_client.publish(channel, json.dumps(command))
    logger.info(f"Published command {command['id']} to channel {channel}")

    # Wait for result using existing queue mechanism
    timeout = x_request_timeout or request.timeout_seconds or config.get("command_timeout")
    result = await queue_module.wait_for_result(command["id"], timeout=timeout)

    # === OPTIONAL ENHANCEMENT ===
    # Extend active window if successful (likely more commands coming)
    if result and result.get("success") and not request.session_id:
        await redis_client.expire(cluster_active_key, 60)
    # === END OPTIONAL ===

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


@app.get("/debug/session/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str, auth_info: Tuple[bool, Optional[str]] = Depends(verify_api_key)
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
    session_id: str, auth_info: Tuple[bool, Optional[str]] = Depends(verify_api_key)
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
    auth_info: Tuple[bool, Optional[str]] = Depends(verify_api_key),
):
    """
    Create authentication token for cluster executor.

    Generates a secure token and stores it in Redis for executor authentication.
    The executor should use this token in the Authorization header when connecting.

    Returns:
        201: Token created successfully with kubectl command for deployment
        401: Unauthorized
        409: Token already exists for this cluster
        500: Internal server error
    """
    if not redis_client:
        raise HTTPException(503, "Service not initialized")

    try:
        # Check if token already exists for this cluster
        existing_token = await redis_client.get(f"executor:token:{cluster_id}")
        if existing_token:
            raise HTTPException(409, f"Token already exists for cluster '{cluster_id}'. Delete it first to create a new one.")

        # Generate secure token for executor
        import secrets
        token = secrets.token_hex(32)  # 64 character hex string

        # Store token: executor:token:{cluster_id} = token_value
        await redis_client.set(f"executor:token:{cluster_id}", token)
        logger.info(f"Created executor token for cluster: {cluster_id}")

        return {
            "token": token,
            "clusterId": cluster_id,
            "createdAt": datetime.now(timezone.utc).isoformat(),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create token for cluster {cluster_id}: {e}")
        raise HTTPException(500, "Failed to create token")


@app.get("/admin/agents")
async def list_agents(
    auth_info: Tuple[bool, Optional[str]] = Depends(verify_api_key),
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
                cluster_id = raw[len(token_prefix):]

                # Check if cluster is currently connected (has active marker)
                is_active = await redis_client.exists(f"cluster:active:{cluster_id}")

                clusters.append({
                    "id": cluster_id,
                    "connected": bool(is_active),
                    "lastSeen": None  # Could track with TTL timestamp
                })

        # Sort by cluster_id for consistent ordering
        clusters.sort(key=lambda x: x["id"])

        return {
            "clusters": clusters,
            "count": len(clusters)
        }

    except Exception as e:
        logger.error(f"Failed to list agents: {e}")
        raise HTTPException(500, "Failed to list agents")


@app.get("/admin/agents/{cluster_id}/status")
async def get_agent_status(
    cluster_id: str,
    auth_info: Tuple[bool, Optional[str]] = Depends(verify_api_key),
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

        return {
            "id": cluster_id,
            "connected": bool(is_active),
            "status": "connected" if is_active else "disconnected",
            "lastSeen": None,  # Could track with Redis TTL or separate timestamp
            "version": None,  # Could be reported by executor
            "kubernetesVersion": None  # Could be reported by executor
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get status for cluster {cluster_id}: {e}")
        raise HTTPException(500, "Failed to get agent status")


@app.delete("/admin/agents/{cluster_id}/token")
async def revoke_agent_token(
    cluster_id: str,
    auth_info: Tuple[bool, Optional[str]] = Depends(verify_api_key),
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

        logger.info(f"Revoked executor token for cluster: {cluster_id}")
        return Response(status_code=204)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to revoke token for cluster {cluster_id}: {e}")
        raise HTTPException(500, "Failed to revoke token")


# Cluster Management Endpoints


@app.get("/debug/clusters")
async def list_clusters(
    auth_info: Tuple[bool, Optional[str]] = Depends(verify_api_key),
):
    """
    List available Kubernetes clusters.

    Returns:
        JSON with list of cluster IDs that can be targeted for debugging
    """
    try:
        clusters_set = set()

        if redis_client:
            # Active cluster markers: cluster:active:<id>
            active_prefix = "cluster:active:"
            active_keys = await redis_client.keys(f"{active_prefix}*")
            for key in active_keys:
                raw = key.decode() if isinstance(key, bytes) else key
                if raw.startswith(active_prefix):
                    clusters_set.add(raw[len(active_prefix):])

            # Active sessions per cluster: cluster:session:<id>
            session_prefix = "cluster:session:"
            session_keys = await redis_client.keys(f"{session_prefix}*")
            for key in session_keys:
                raw = key.decode() if isinstance(key, bytes) else key
                if raw.startswith(session_prefix):
                    clusters_set.add(raw[len(session_prefix):])

            # Executor tokens: executor:token:<id>
            token_prefix = "executor:token:"
            token_keys = await redis_client.keys(f"{token_prefix}*")
            for key in token_keys:
                raw = key.decode() if isinstance(key, bytes) else key
                if raw.startswith(token_prefix):
                    clusters_set.add(raw[len(token_prefix):])

        clusters = sorted(clusters_set)

        return {"clusters": clusters, "count": len(clusters)}

    except Exception as e:
        logger.error(f"Failed to list clusters: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Health/Monitoring Endpoints


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
                "version": "1.0.0"
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
