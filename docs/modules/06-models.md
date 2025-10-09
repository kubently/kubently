# Module: Data Models (System Primitives)

## Black Box Interface

**Purpose**: Define the core primitives that flow through the system

**Core Primitives**:

1. **Command** - A read-only kubectl operation
2. **Session** - A time-bounded debugging context
3. **Result** - Output from command execution

**What this module does** (Public Interface):

- Defines data structures for system primitives
- Provides validation rules
- Ensures consistent serialization

**What this module hides** (Implementation):

- Serialization library (Pydantic, marshmallow, etc.)
- Validation logic details
- Default values and constraints

## Overview

The Models module defines the fundamental primitives of the system. These are the "nouns" that all other modules operate on. This module has NO dependencies and can be used by any other module.

## Dependencies

- Pydantic 2.0+
- Python 3.13+ standard library (datetime, enum, typing)

## Implementation Requirements

### File Structure

```text
kubently/api/
└── models.py
```

### Implementation (`models.py`)

```python
"""
Kubently shared data models.

These models define the structure of all data passed between
components in the Kubently system.
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from enum import Enum
from pydantic import BaseModel, Field, validator
import json

# Enums

class ExecutionStatus(str, Enum):
    """Status of command execution."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"

class SessionStatus(str, Enum):
    """Status of debugging session."""
    ACTIVE = "active"
    IDLE = "idle"
    EXPIRED = "expired"
    ENDED = "ended"

class CommandType(str, Enum):
    """Types of kubectl commands."""
    GET = "get"
    DESCRIBE = "describe"
    LOGS = "logs"
    TOP = "top"
    EVENTS = "events"
    VERSION = "version"
    API_RESOURCES = "api-resources"

# Request Models (API Input)

class CreateSessionRequest(BaseModel):
    """Request to create a debugging session."""
    cluster_id: str = Field(
        ...,
        description="Target cluster identifier",
        min_length=1,
        max_length=100,
        pattern="^[a-z0-9-]+$"
    )
    user_id: Optional[str] = Field(
        None,
        description="Optional user/AI identifier"
    )
    correlation_id: Optional[str] = Field(
        None,
        description="Correlation ID for A2A request tracking"
    )
    service_identity: Optional[str] = Field(
        None,
        description="Calling service identity for A2A"
    )
    ttl_seconds: Optional[int] = Field(
        300,
        description="Session TTL in seconds",
        ge=60,
        le=3600
    )

class ExecuteCommandRequest(BaseModel):
    """Request to execute a kubectl command."""
    cluster_id: str = Field(
        ...,
        description="Target cluster identifier"
    )
    session_id: Optional[str] = Field(
        None,
        description="Associated session ID"
    )
    correlation_id: Optional[str] = Field(
        None,
        description="Correlation ID for A2A request tracking"
    )
    command_type: CommandType = Field(
        CommandType.GET,
        description="Type of kubectl command"
    )
    args: List[str] = Field(
        ...,
        description="kubectl command arguments",
        min_items=1,
        max_items=20
    )
    namespace: Optional[str] = Field(
        "default",
        description="Kubernetes namespace"
    )
    timeout_seconds: Optional[int] = Field(
        10,
        description="Command timeout",
        ge=1,
        le=30
    )
    
    @validator('args')
    def validate_args(cls, v):
        """Ensure args don't contain dangerous operations."""
        forbidden = ['delete', 'apply', 'create', 'patch', 'edit']
        for arg in v:
            if any(f in arg.lower() for f in forbidden):
                raise ValueError(f"Forbidden argument: {arg}")
        return v

# Response Models (API Output)

class SessionResponse(BaseModel):
    """Response after creating a session."""
    session_id: str
    cluster_id: str
    status: SessionStatus
    created_at: datetime
    expires_at: datetime
    ttl_seconds: int
    correlation_id: Optional[str] = None
    service_identity: Optional[str] = None

class CommandResponse(BaseModel):
    """Response after command execution."""
    command_id: str
    session_id: Optional[str]
    cluster_id: str
    status: ExecutionStatus
    correlation_id: Optional[str] = None
    output: Optional[str] = Field(
        None,
        description="Command stdout"
    )
    error: Optional[str] = Field(
        None,
        description="Error message if failed"
    )
    execution_time_ms: Optional[int] = Field(
        None,
        description="Execution time in milliseconds"
    )
    executed_at: Optional[datetime] = Field(
        None,
        description="When command was executed"
    )

class HealthResponse(BaseModel):
    """Health check response."""
    status: str = Field(
        ...,
        description="Health status",
        pattern="^(healthy|unhealthy|degraded)$"
    )
    redis: str = Field(
        ...,
        description="Redis connection status"
    )
    version: str = Field(
        "1.0.0",
        description="API version"
    )
    uptime_seconds: Optional[int] = None
    active_sessions: Optional[int] = None

# Internal Models (Used between modules)

class Command(BaseModel):
    """Internal command representation."""
    id: str = Field(
        ...,
        description="Unique command ID"
    )
    cluster_id: str
    session_id: Optional[str] = None
    command_type: CommandType
    args: List[str]
    namespace: str = "default"
    timeout_seconds: int = 10
    queued_at: datetime = Field(
        default_factory=datetime.utcnow
    )
    priority: int = Field(
        1,
        description="Command priority (higher = more important)",
        ge=0,
        le=100
    )

class CommandResult(BaseModel):
    """Internal command result representation."""
    command_id: str
    success: bool
    output: Optional[str] = None
    error: Optional[str] = None
    exit_code: Optional[int] = None
    execution_time_ms: int
    executed_at: datetime = Field(
        default_factory=datetime.utcnow
    )

class Session(BaseModel):
    """Internal session representation."""
    session_id: str
    cluster_id: str
    user_id: Optional[str] = None
    status: SessionStatus = SessionStatus.ACTIVE
    created_at: datetime = Field(
        default_factory=datetime.utcnow
    )
    last_activity: datetime = Field(
        default_factory=datetime.utcnow
    )
    command_count: int = 0
    ttl_seconds: int = 300
    
    @property
    def expires_at(self) -> datetime:
        """Calculate expiration time."""
        from datetime import timedelta
        return self.last_activity + timedelta(seconds=self.ttl_seconds)
    
    @property
    def is_expired(self) -> bool:
        """Check if session has expired."""
        return datetime.utcnow() > self.expires_at

# Agent Models

class AgentCommand(BaseModel):
    """Command format sent to agent."""
    id: str
    args: List[str]
    timeout: int = 10
    session_id: Optional[str] = None

class AgentResult(BaseModel):
    """Result format from agent."""
    command_id: str
    result: Dict[str, Any] = Field(
        ...,
        description="Execution result with success, output, error"
    )

class AgentStatus(BaseModel):
    """Agent status information."""
    cluster_id: str
    is_active: bool = Field(
        False,
        description="Whether cluster has active session"
    )
    queue_depth: int = Field(
        0,
        description="Number of pending commands"
    )

# Webhook Models (for future integrations)

class WebhookEvent(BaseModel):
    """Webhook event notification."""
    event_type: str = Field(
        ...,
        description="Event type (session.created, command.executed, etc.)"
    )
    timestamp: datetime = Field(
        default_factory=datetime.utcnow
    )
    data: Dict[str, Any]

# Error Models

class ErrorResponse(BaseModel):
    """Standard error response."""
    error: str = Field(
        ...,
        description="Error message"
    )
    details: Optional[Dict[str, Any]] = Field(
        None,
        description="Additional error details"
    )
    request_id: Optional[str] = Field(
        None,
        description="Request ID for tracing"
    )
    timestamp: datetime = Field(
        default_factory=datetime.utcnow
    )

# Validation Helpers

def validate_cluster_id(cluster_id: str) -> str:
    """
    Validate cluster ID format.
    
    Rules:
    - Lowercase alphanumeric and hyphens only
    - Must start and end with alphanumeric
    - Max 100 characters
    """
    import re
    pattern = r'^[a-z0-9][a-z0-9-]{0,98}[a-z0-9]$'
    if not re.match(pattern, cluster_id):
        raise ValueError(
            "Cluster ID must be lowercase alphanumeric with hyphens, "
            "starting and ending with alphanumeric character"
        )
    return cluster_id

def validate_kubectl_args(args: List[str]) -> List[str]:
    """
    Validate kubectl arguments for safety.
    
    Ensures:
    - No write operations
    - No authentication changes
    - No dangerous flags
    """
    forbidden_verbs = {
        'delete', 'apply', 'create', 'patch', 'edit', 
        'replace', 'scale', 'autoscale', 'rollout'
    }
    
    forbidden_flags = {
        '--token', '--kubeconfig', '--server', '--insecure',
        '--username', '--password', '--client-certificate'
    }
    
    if args and args[0] in forbidden_verbs:
        raise ValueError(f"Forbidden verb: {args[0]}")
    
    for arg in args:
        if any(flag in arg for flag in forbidden_flags):
            raise ValueError(f"Forbidden flag: {arg}")
    
    return args

# Serialization Helpers

class KubentlyJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder for Kubently models."""
    
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        elif isinstance(obj, Enum):
            return obj.value
        elif hasattr(obj, 'dict'):
            return obj.dict()
        return super().default(obj)

# Configuration Model

class KubentlyConfig(BaseModel):
    """Configuration for Kubently components."""
    
    # Redis
    redis_url: str = Field(
        "redis://localhost:6379",
        description="Redis connection URL"
    )
    redis_max_connections: int = Field(
        50,
        description="Max Redis connections",
        ge=1,
        le=1000
    )
    
    # API
    api_host: str = Field(
        "0.0.0.0",
        description="API bind host"
    )
    api_port: int = Field(
        8080,
        description="API bind port",
        ge=1,
        le=65535
    )
    api_workers: int = Field(
        4,
        description="Number of API workers",
        ge=1,
        le=100
    )
    
    # Security
    api_keys: List[str] = Field(
        [],
        description="Valid API keys for AI/User access"
    )
    agent_tokens: Dict[str, str] = Field(
        {},
        description="Static agent tokens (cluster_id -> token)"
    )
    
    # Timeouts
    command_timeout: int = Field(
        10,
        description="Default command timeout in seconds",
        ge=1,
        le=60
    )
    session_ttl: int = Field(
        300,
        description="Default session TTL in seconds",
        ge=60,
        le=3600
    )
    result_ttl: int = Field(
        60,
        description="Result TTL in seconds",
        ge=10,
        le=300
    )
    
    # Performance
    max_commands_per_fetch: int = Field(
        10,
        description="Max commands per agent fetch",
        ge=1,
        le=100
    )
    long_poll_timeout: int = Field(
        30,
        description="Max long poll wait in seconds",
        ge=1,
        le=60
    )
    
    class Config:
        env_file = ".env"
        env_prefix = "KUBENTLY_"

# Type Aliases for clarity

CommandID = str
SessionID = str
ClusterID = str
Token = str

# Export all models
__all__ = [
    # Enums
    'ExecutionStatus',
    'SessionStatus', 
    'CommandType',
    
    # Request models
    'CreateSessionRequest',
    'ExecuteCommandRequest',
    
    # Response models
    'SessionResponse',
    'CommandResponse',
    'HealthResponse',
    
    # Internal models
    'Command',
    'CommandResult',
    'Session',
    
    # Agent models
    'AgentCommand',
    'AgentResult',
    'AgentStatus',
    
    # Other models
    'WebhookEvent',
    'ErrorResponse',
    'KubentlyConfig',
    
    # Validators
    'validate_cluster_id',
    'validate_kubectl_args',
    
    # Helpers
    'KubentlyJSONEncoder'
]
```

## Usage Examples

### In API Endpoints

```python
from fastapi import FastAPI
from .models import CreateSessionRequest, SessionResponse

@app.post("/debug/session", response_model=SessionResponse)
async def create_session(request: CreateSessionRequest):
    # Pydantic validates input automatically
    session = await session_module.create(
        request.cluster_id,
        request.user_id
    )
    return SessionResponse(
        session_id=session.id,
        cluster_id=session.cluster_id,
        # ...
    )
```

### In Agent

```python
from models import AgentCommand, AgentResult

# Parse command from API
command = AgentCommand.parse_obj(command_data)

# Execute and create result
result = AgentResult(
    command_id=command.id,
    result={
        "success": True,
        "output": kubectl_output
    }
)

# Serialize for sending
result_json = result.json()
```

## Testing Requirements

### Unit Tests

```python
def test_create_session_request_validation():
    # Test valid request
    # Test invalid cluster_id format
    # Test TTL bounds
    
def test_command_args_validation():
    # Test forbidden verbs rejected
    # Test forbidden flags rejected
    
def test_session_expiration():
    # Test expires_at calculation
    # Test is_expired property
```

## Deliverables

1. `models.py` with all data models
2. Unit tests in `tests/test_models.py`
3. JSON schema exports for API documentation
4. Type stub files for better IDE support

## Development Notes

- Use Pydantic 2.0 for better performance
- Keep models immutable where possible
- Add validators for security-critical fields
- Document all fields with descriptions
- Consider adding factory methods for complex models
- Export JSON schemas for API documentation
