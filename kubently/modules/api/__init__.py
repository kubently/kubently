"""
API Module - Black Box Interface

Purpose: HTTP routing and module orchestration
Interface: REST API endpoints
Hidden: Module initialization, request handling, error responses

The API module only orchestrates - it contains no business logic.
All logic is delegated to appropriate modules.
"""

from .models import (
    CommandResponse,
    CommandResult,
    CreateSessionRequest,
    ExecuteCommandRequest,
    ExecutionStatus,
    LogSearchRequest,
    LokiQueryDirection,
    LokiQueryRequest,
    PrometheusQueryRequest,
    PrometheusQueryType,
    SessionResponse,
    SessionStatus,
)

__all__ = [
    "CreateSessionRequest",
    "ExecuteCommandRequest",
    "LogSearchRequest",
    "LokiQueryRequest",
    "LokiQueryDirection",
    "PrometheusQueryRequest",
    "PrometheusQueryType",
    "SessionResponse",
    "CommandResponse",
    "CommandResult",
    "ExecutionStatus",
    "SessionStatus",
]
