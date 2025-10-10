"""
Kubently shared data models.

These models define the structure of all data passed between
components in the Kubently system.
"""

import json
import re
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

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
    API_VERSIONS = "api-versions"
    EXPLAIN = "explain"


# Request Models (API Input)


class CreateSessionRequest(BaseModel):
    """Request to create a debugging session."""

    cluster_id: str = Field(
        ...,
        description="Target cluster identifier",
        min_length=1,
        max_length=100,
        pattern="^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$",
    )
    user_id: Optional[str] = Field(None, description="Optional user/AI identifier")
    correlation_id: Optional[str] = Field(
        None, description="Correlation ID for A2A request tracking"
    )
    service_identity: Optional[str] = Field(None, description="Calling service identity for A2A")
    ttl_seconds: Optional[int] = Field(
        default=300, description="Session TTL in seconds", ge=60, le=3600
    )


class ExecuteCommandRequest(BaseModel):
    """Request to execute a kubectl command."""

    cluster_id: str = Field(..., description="Target cluster identifier")
    session_id: Optional[str] = Field(None, description="Associated session ID")
    correlation_id: Optional[str] = Field(
        None, description="Correlation ID for A2A request tracking"
    )
    command_type: CommandType = Field(
        default=CommandType.GET, description="Type of kubectl command"
    )
    args: List[str] = Field(
        ..., description="kubectl command arguments", min_length=1, max_length=20
    )
    namespace: Optional[str] = Field(default="default", description="Kubernetes namespace")
    timeout_seconds: Optional[int] = Field(default=10, description="Command timeout", ge=1, le=30)
    extra_args: Optional[List[str]] = Field(None, description="A list of additional, safe arguments to pass to the kubectl command, like ['-o', 'yaml'].")

    @field_validator("args")
    @classmethod
    def validate_args(cls, v):
        """Ensure args don't contain dangerous operations."""
        forbidden = ["delete", "apply", "create", "patch", "edit", "replace", "scale"]
        for arg in v:
            if any(f in arg.lower() for f in forbidden):
                raise ValueError(f"Forbidden argument: {arg}")
        return v

    @field_validator("extra_args")
    @classmethod
    def validate_extra_args(cls, v):
        """Ensure extra_args contain only safe flags."""
        if v is None:
            return v
        
        # Whitelist of safe flags for output formatting and filtering
        safe_flags = {
            "-o", "--output",  # Output format
            "-l", "--selector",  # Label selector
            "--field-selector",  # Field selector
            "--show-labels",  # Show labels
            "--show-kind",  # Show resource kind
            "--no-headers",  # No headers in output
            "-w", "--watch",  # Watch for changes (though may timeout)
            "--sort-by",  # Sort output
            "-A", "--all-namespaces",  # All namespaces
        }
        
        # Allowed output formats
        allowed_output_formats = {
            "json", "yaml", "wide", "name", "custom-columns", "custom-columns-file", 
            "go-template", "go-template-file", "jsonpath", "jsonpath-file"
        }
        
        # Forbidden flags that could be dangerous
        forbidden_flags = {
            "--token", "--kubeconfig", "--server", "--insecure",
            "--username", "--password", "--client-certificate",
            "--as", "--as-group", "--certificate-authority",
            "-f", "--filename", "--recursive"
        }
        
        i = 0
        while i < len(v):
            arg = v[i]
            
            # Check if it's a forbidden flag
            if any(arg.startswith(flag) for flag in forbidden_flags):
                raise ValueError(f"Forbidden flag in extra_args: {arg}")
            
            # Check if it's a safe flag
            if arg in safe_flags:
                # Check if it needs a value (next argument)
                if arg in ["-o", "--output", "-l", "--selector", "--field-selector", "--sort-by"]:
                    if i + 1 < len(v):
                        # Validate output format if it's -o/--output
                        if arg in ["-o", "--output"]:
                            output_format = v[i + 1]
                            # Handle jsonpath and go-template with equals sign
                            if "=" in output_format:
                                base_format = output_format.split("=")[0]
                                if base_format not in allowed_output_formats:
                                    raise ValueError(f"Invalid output format: {output_format}")
                            elif output_format not in allowed_output_formats:
                                raise ValueError(f"Invalid output format: {output_format}")
                        i += 2  # Skip the value
                        continue
                i += 1
                continue
            
            # Check for combined format like -ojson
            if arg.startswith("-o") and len(arg) > 2:
                output_format = arg[2:]
                if output_format not in allowed_output_formats:
                    raise ValueError(f"Invalid output format: {arg}")
                i += 1
                continue
            
            # If we get here, it's not a recognized safe flag
            raise ValueError(f"Unrecognized or unsafe flag in extra_args: {arg}")
        
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
    output: Optional[str] = Field(None, description="Command stdout")
    error: Optional[str] = Field(None, description="Error message if failed")
    execution_time_ms: Optional[int] = Field(None, description="Execution time in milliseconds")
    executed_at: Optional[datetime] = Field(None, description="When command was executed")


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = Field(..., description="Health status", pattern="^(healthy|unhealthy|degraded)$")
    redis: str = Field(..., description="Redis connection status")
    version: str = Field(default="1.0.0", description="API version")
    uptime_seconds: Optional[int] = None
    active_sessions: Optional[int] = None


# Internal Models (Used between modules)


class Command(BaseModel):
    """Internal command representation."""

    id: str = Field(..., description="Unique command ID")
    cluster_id: str
    session_id: Optional[str] = None
    command_type: CommandType
    args: List[str]
    namespace: str = "default"
    timeout_seconds: int = 10
    queued_at: datetime = Field(default_factory=datetime.utcnow)
    priority: int = Field(
        default=1, description="Command priority (higher = more important)", ge=0, le=100
    )


class CommandResult(BaseModel):
    """Internal command result representation."""

    command_id: str
    success: bool
    output: Optional[str] = None
    error: Optional[str] = None
    exit_code: Optional[int] = None
    execution_time_ms: int
    executed_at: datetime = Field(default_factory=datetime.utcnow)


class Session(BaseModel):
    """Internal session representation."""

    session_id: str
    cluster_id: str
    user_id: Optional[str] = None
    correlation_id: Optional[str] = None
    service_identity: Optional[str] = None
    status: SessionStatus = SessionStatus.ACTIVE
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_activity: datetime = Field(default_factory=datetime.utcnow)
    command_count: int = 0
    ttl_seconds: int = 300

    @property
    def expires_at(self) -> datetime:
        """Calculate expiration time."""
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
    result: Dict[str, Any] = Field(..., description="Execution result with success, output, error")


class AgentStatus(BaseModel):
    """Agent status information."""

    cluster_id: str
    is_active: bool = Field(default=False, description="Whether cluster has active session")
    queue_depth: int = Field(default=0, description="Number of pending commands")


# Webhook Models (for future integrations)


class WebhookEvent(BaseModel):
    """Webhook event notification."""

    event_type: str = Field(..., description="Event type (session.created, command.executed, etc.)")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    data: Dict[str, Any]


# Error Models


class ErrorResponse(BaseModel):
    """Standard error response."""

    error: str = Field(..., description="Error message")
    details: Optional[Dict[str, Any]] = Field(default=None, description="Additional error details")
    request_id: Optional[str] = Field(default=None, description="Request ID for tracing")
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# Validation Helpers


def validate_cluster_id(cluster_id: str) -> str:
    """
    Validate cluster ID format.

    Rules:
    - Lowercase alphanumeric and hyphens only
    - Must start and end with alphanumeric
    - Max 100 characters
    """
    pattern = r"^[a-z0-9][a-z0-9-]{0,98}[a-z0-9]$|^[a-z0-9]$"
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
        "delete",
        "apply",
        "create",
        "patch",
        "edit",
        "replace",
        "scale",
        "autoscale",
        "rollout",
        "exec",
    }

    forbidden_flags = {
        "--token",
        "--kubeconfig",
        "--server",
        "--insecure",
        "--username",
        "--password",
        "--client-certificate",
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
        elif hasattr(obj, "model_dump"):
            return obj.model_dump()
        return super().default(obj)


# Configuration Model


class KubentlyConfig(BaseModel):
    """Configuration for Kubently components."""

    # Redis
    redis_url: str = Field(default="redis://localhost:6379", description="Redis connection URL")
    redis_max_connections: int = Field(
        default=50, description="Max Redis connections", ge=1, le=1000
    )

    # API
    api_host: str = Field(default="0.0.0.0", description="API bind host")
    api_port: int = Field(default=8080, description="API bind port", ge=1, le=65535)
    api_workers: int = Field(default=4, description="Number of API workers", ge=1, le=100)

    # Security
    api_keys: List[str] = Field(
        default_factory=list, description="Valid API keys for AI/User access"
    )
    agent_tokens: Dict[str, str] = Field(
        default_factory=dict, description="Static agent tokens (cluster_id -> token)"
    )

    # Timeouts
    command_timeout: int = Field(
        default=10, description="Default command timeout in seconds", ge=1, le=60
    )
    session_ttl: int = Field(
        default=300, description="Default session TTL in seconds", ge=60, le=3600
    )
    result_ttl: int = Field(default=60, description="Result TTL in seconds", ge=10, le=300)

    # Performance
    max_commands_per_fetch: int = Field(
        default=10, description="Max commands per agent fetch", ge=1, le=100
    )
    long_poll_timeout: int = Field(
        default=30, description="Max long poll wait in seconds", ge=1, le=60
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
    "ExecutionStatus",
    "SessionStatus",
    "CommandType",
    # Request models
    "CreateSessionRequest",
    "ExecuteCommandRequest",
    # Response models
    "SessionResponse",
    "CommandResponse",
    "HealthResponse",
    # Internal models
    "Command",
    "CommandResult",
    "Session",
    # Agent models
    "AgentCommand",
    "AgentResult",
    "AgentStatus",
    # Other models
    "WebhookEvent",
    "ErrorResponse",
    "KubentlyConfig",
    # Validators
    "validate_cluster_id",
    "validate_kubectl_args",
    # Helpers
    "KubentlyJSONEncoder",
]
