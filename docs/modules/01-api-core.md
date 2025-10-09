# Module: API Core

## Black Box Interface

**Purpose**: HTTP API gateway and module orchestration

**What this module does** (Public Interface):
- Exposes REST API endpoints
- Routes requests to appropriate modules
- Handles HTTP concerns (headers, status codes)
- Manages module lifecycle

**What this module hides** (Implementation):
- Web framework choice (FastAPI, Flask, etc.)
- Module initialization order
- Error handling strategy
- Request/response transformation
- Correlation ID propagation

## Overview
The API Core module is a black box that provides HTTP access to the system. It can be completely replaced with a different web framework or even a different protocol (gRPC, GraphQL) without affecting the business logic modules.

## Dependencies
- FastAPI 0.104+
- Uvicorn 0.24+
- Redis 5.0+ (redis-py with async support)
- Python 3.13+
- Pydantic 2.0+

## Interfaces

### External Interfaces

#### Agent Endpoints
- `GET /agent/commands` - Long polling for commands
- `POST /agent/results` - Submit command results

#### AI/User/A2A Service Endpoints  
- `POST /debug/session` - Create debugging session
- `POST /debug/execute` - Execute command synchronously
- `DELETE /debug/session/{session_id}` - End session
- `GET /debug/session/{session_id}` - Get session status (for A2A polling)

#### Health/Metrics
- `GET /health` - Health check
- `GET /metrics` - Prometheus metrics (optional)

### Internal Interfaces
The API Core imports and uses:
- `AuthModule` from auth module
- `SessionModule` from session module  
- `QueueModule` from queue module
- Shared models from models module

## Implementation Requirements

### File Structure
```text
kubently/api/
├── __init__.py
├── main.py           # FastAPI app and routes
├── config.py         # Configuration management
└── dependencies.py   # Shared dependencies (Redis connection, etc.)
```

### Core Implementation (`main.py`)

```python
from fastapi import FastAPI, Header, HTTPException, Response, Query
from typing import Optional
import redis.asyncio as redis
import os

# Create FastAPI app
app = FastAPI(
    title="Kubently API",
    description="Interactive Kubernetes Debugging System",
    version="1.0.0"
)

# Initialize Redis connection pool (singleton)
redis_client = redis.from_url(
    os.environ.get("REDIS_URL", "redis://localhost:6379"),
    decode_responses=True,
    max_connections=50
)

# Initialize modules (will be imported)
from .auth import AuthModule
from .session import SessionModule
from .queue import QueueModule

auth = AuthModule(redis_client)
sessions = SessionModule(redis_client)
queue = QueueModule(redis_client)

# Agent Endpoints

@app.get("/agent/commands")
async def get_commands(
    wait: int = Query(default=0, ge=0, le=30),
    authorization: str = Header(),
    x_cluster_id: str = Header()
):
    """
    Long polling endpoint for agents to fetch commands.
    
    Args:
        wait: Seconds to wait for commands (0-30)
        authorization: Bearer token
        x_cluster_id: Cluster identifier
        
    Returns:
        200: {"commands": [...]} if commands available
        204: No content if no commands
        401: Unauthorized
    """
    # Implementation here
    
@app.post("/agent/results")
async def post_result(
    payload: CommandResult,  # From models
    authorization: str = Header(),
    x_cluster_id: str = Header()
):
    """
    Endpoint for agents to submit command results.
    
    Returns:
        200: {"status": "accepted"}
        401: Unauthorized
    """
    # Implementation here

# AI/User Endpoints

@app.post("/debug/session")
async def create_session(
    request: CreateSessionRequest,  # From models
    x_api_key: str = Header(),
    x_correlation_id: Optional[str] = Header(None),
    x_service_identity: Optional[str] = Header(None)
):
    """
    Create a new debugging session for a cluster.
    
    Returns:
        201: {"session_id": "...", "cluster_id": "...", "ttl_seconds": 300, "correlation_id": "..."}
        401: Unauthorized
    """
    # Verify API key and extract service identity
    is_valid, service_identity = await auth.verify_api_key(x_api_key)
    if not is_valid:
        raise HTTPException(401, "Unauthorized")
    
    # Use provided service identity or extracted one
    service_id = x_service_identity or service_identity or "direct"
    
    # Implementation here

@app.post("/debug/execute")
async def execute_command(
    request: ExecuteCommandRequest,  # From models
    x_api_key: str = Header(),
    x_correlation_id: Optional[str] = Header(None),
    x_request_timeout: Optional[int] = Header(None)
):
    """
    Execute a kubectl command and wait for result.
    Synchronous from the caller's perspective.
    
    Returns:
        200: {"success": true, "output": "...", "execution_time_ms": 250, "correlation_id": "..."}
        408: Request timeout
        401: Unauthorized
    """
    # Verify API key
    is_valid, service_identity = await auth.verify_api_key(x_api_key)
    if not is_valid:
        raise HTTPException(401, "Unauthorized")
        
    # Implementation here

@app.get("/health")
async def health():
    """
    Health check endpoint.
    
    Returns:
        200: {"status": "healthy", "redis": "connected"}
        503: {"status": "unhealthy", "redis": "disconnected"}
    """
    # Implementation here
```

### Configuration (`config.py`)

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Redis
    redis_url: str = "redis://localhost:6379"
    redis_max_connections: int = 50
    
    # Security
    api_keys: list[str] = []  # Loaded from env
    
    # Timeouts
    command_timeout: int = 10  # seconds
    session_ttl: int = 300  # seconds
    result_ttl: int = 60  # seconds
    
    # Performance
    max_commands_per_fetch: int = 10
    long_poll_timeout: int = 30
    
    class Config:
        env_file = ".env"

settings = Settings()
```

## Error Handling

All endpoints must handle:
- Redis connection failures (503 Service Unavailable)
- Authentication failures (401 Unauthorized)  
- Invalid requests (400 Bad Request)
- Timeouts (408 Request Timeout)

Use FastAPI exception handlers:
```python
@app.exception_handler(redis.ConnectionError)
async def redis_error_handler(request, exc):
    return JSONResponse(
        status_code=503,
        content={"error": "Database connection failed"}
    )
```

## Testing Requirements

### Unit Tests
- Mock Redis connections
- Test each endpoint with valid/invalid inputs
- Test error conditions

### Integration Tests  
- Test with real Redis instance
- Test long polling behavior
- Test timeout handling

## Performance Requirements

- Startup time: < 2 seconds
- Request latency: < 10ms (excluding Redis operations)
- Memory usage: < 200MB
- Support 1000+ concurrent connections

## Security Requirements

- All endpoints require authentication
- Log all authentication failures
- Rate limiting (100 requests/second per API key)
- No sensitive data in logs

## Deliverables

1. `main.py` with all routes implemented
2. `config.py` with configuration management
3. `dependencies.py` with shared dependencies
4. Unit tests in `tests/test_api.py`
5. Integration tests in `tests/integration/test_api.py`

## Development Notes

- Start with health endpoint to verify setup
- Use FastAPI's automatic OpenAPI documentation
- Implement proper async/await for all Redis operations
- Use dependency injection for modules
- Add request ID for tracing
