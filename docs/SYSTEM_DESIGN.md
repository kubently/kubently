# Kubently System Design Document

## Executive Summary

Kubently (*Kubernetes + Agentically*) enables AI agents to troubleshoot Kubernetes clusters in real-time through agentic workflows. The system prioritizes sub-second latency for debugging sessions while maintaining simplicity, security, and reliability. It is designed to integrate seamlessly with multi-agent systems via the Agent-to-Agent (A2A) protocol.

## Problem Statement

DevOps engineers and SREs need AI agents that can autonomously debug Kubernetes clusters through agentic workflows. Current solutions require manual kubectl commands or complex scripting. Kubently bridges this gap by enabling agents to:

- Real-time command execution in remote clusters
- Interactive debugging sessions with < 500ms latency
- Secure, read-only access to cluster resources
- Simple deployment model (single API pod + lightweight agents)

## Architecture Overview

```text
┌─────────────┐      ┌─────────────────┐      ┌─────────┐
│   AI/User   │─────▶│   Kubently API  │◀────▶│  Redis  │
│             │:8080 │   (Single Pod)   │      └─────────┘
└─────────────┘      │                 │
                     │  REST API:8080   │
┌─────────────┐      │  A2A Server:8000 │
│ A2A Client  │─────▶│  A2A WS: /a2a/ws │
│(Orchestrator│:8000 │                 │
│   agents)   │      │  Single process │
└─────────────┘      │  Dual interface │
                     └─────────────────┘
┌─────────────┐            ▲ HTTP
│ Kubently CLI│────────────┤ (Long Polling)
│  - Admin    │ REST       │
│  - Debug    │ A2A/WS     │
└─────────────┘     ┌──────┴────────┐
                    │ Kubently Agent │
                    │  (Per Cluster) │
                    └────────────────┘
```

### Multi-Agent Integration
Kubently runs as a single service with dual interfaces:
1. **REST API** (Port 8080): Traditional HTTP endpoints for direct integration
2. **A2A Server** (Port 8000): Agent-to-Agent protocol for multi-agent systems

Both interfaces run in the same process/pod, sharing:
- The same Redis connection and state
- Authentication and session management
- Command execution logic
- Resource limits and lifecycle

This simplified architecture:
- Reduces operational complexity (one pod instead of two)
- Shares resources efficiently
- Maintains clean separation through ports
- Simplifies deployment and scaling

## Core Primitives

The system is built around three fundamental primitives:

1. **Command** - A read-only kubectl operation to execute
2. **Session** - A time-bounded debugging context
3. **Result** - The output from a command execution

All system complexity is built through composition of these primitives.

## Key Design Principles

1. **Black Box Modules**: Each module exposes only its interface, hiding all implementation
2. **Replaceable Components**: Any module can be rewritten using only its public API
3. **Single Responsibility**: Each module has one clear job that one person can maintain
4. **Primitive-First**: Everything flows through Command, Session, and Result primitives
5. **Interface Stability**: APIs designed to remain stable even if implementations change completely

## Core Components

### 1. Kubently API (Single Service)
- FastAPI application running in a single pod
- Handles both AI/User requests and Agent communication
- Uses Redis for all state management
- Can scale horizontally (multiple replicas)
- Exposes A2A WebSocket endpoint at `/a2a/ws` for interactive sessions

### 2. Kubently Agent
- Lightweight Python script (< 200 lines)
- Deployed one per cluster
- Long-polls API for commands
- Executes kubectl and returns results
- No direct Redis access (security boundary)

### 3. Kubently CLI
- Command-line interface for system management
- **Admin Mode**: Direct REST API calls for cluster registration, token management
- **Debug Mode**: A2A protocol over WebSocket for interactive sessions
- Stores config in `~/.kubently/config.json` with secure permissions
- Generates deployment manifests (K8s, Docker, Helm)

### 4. Redis
- Stores active debugging sessions
- Command queue per cluster
- Command results (temporary, 60s TTL)
- Enables near-instant command delivery via blocking operations

## Data Flow

### Interactive Debugging Session

1. **AI initiates session**:
   - AI calls `/debug/session` to create session
   - API marks cluster as "active" in Redis (5 min TTL)

2. **Command Execution**:
   - AI calls `/debug/execute` with kubectl command
   - API pushes command to Redis queue for cluster
   - API waits for result (blocking)

3. **Agent Processing**:
   - Agent long-polls `/agent/commands` endpoint
   - When cluster is active, polls every 100ms
   - Receives command instantly via Redis BRPOP
   - Executes kubectl locally
   - Posts result to `/agent/results`

4. **Result Delivery**:
   - API stores result in Redis
   - Waiting API call returns result to AI
   - Total latency: 200-500ms

## Security Model

### Agent Security
- Agents authenticate with cluster-specific tokens
- Read-only Kubernetes RBAC (get, list, watch)
- No network exposure (outbound only)
- No direct Redis access

### API Security
- AI/A2A services authenticate with API keys
- Service identity embedded in API keys (e.g., "orchestrator:key123")
- Rate limiting per API key and service identity
- Command validation (read-only operations)
- Audit logging of all commands with service attribution
- Correlation ID tracking for multi-agent request chains

## Performance Targets

- **Command Latency**: < 500ms during active sessions
- **Concurrent Sessions**: 100+ simultaneous debugging sessions
- **Commands/Second**: 100+ per cluster during debugging
- **Agent Overhead**: < 100MB RAM, < 0.1 CPU

## Deployment Model

### API Deployment
- Single Kubernetes Deployment (2-3 replicas)
- Behind LoadBalancer/Ingress
- Single Redis instance (or Redis cluster for HA)
- 500MB RAM, 0.5 CPU per replica

### Agent Deployment
- One pod per target cluster
- Minimal ServiceAccount with read permissions
- 256MB RAM, 0.1 CPU
- Can be deployed via Helm or simple YAML

## Technology Stack

- **Language**: Python 3.13 (entire system)
- **API Framework**: FastAPI
- **A2A Framework**: a2a-server (Starlette-based)
- **Queue/State**: Redis 7+
- **Container Runtime**: Docker
- **Orchestration**: Kubernetes
- **Agent Dependencies**: requests, subprocess
- **A2A Dependencies**: a2a, httpx, pydantic

## Module Breakdown

Each module is a black box with clearly defined interfaces:

### 1. **Models Module** (Primitives Definition)
- **Purpose**: Define system primitives (Command, Session, Result)
- **Interface**: Data structures only, no logic
- **Replaceable**: Yes - could switch to protobuf, JSON schema, etc.

### 2. **Authentication Module**
- **Purpose**: Validate tokens and API keys
- **Interface**: `verify_agent()`, `verify_api_key()`, `create_agent_token()`
- **Hidden**: Token storage, validation logic, key formats
- **Replaceable**: Yes - could use OAuth, JWT, or external auth service

### 3. **Session Module**
- **Purpose**: Manage debugging session lifecycle
- **Interface**: `create_session()`, `get_session()`, `end_session()`
- **Hidden**: Session storage, TTL management, state transitions
- **Replaceable**: Yes - could use database, in-memory, or external service

### 4. **Queue Module**
- **Purpose**: Handle command queuing and result delivery
- **Interface**: `push_command()`, `pop_command()`, `store_result()`, `get_result()`
- **Hidden**: Queue implementation, blocking logic, result storage
- **Replaceable**: Yes - could use RabbitMQ, Kafka, or any message queue

### 5. **Agent Module**
- **Purpose**: Execute kubectl commands in clusters
- **Interface**: HTTP endpoints for commands/results
- **Hidden**: kubectl execution, error handling, retry logic
- **Replaceable**: Yes - could rewrite in Go, Rust, or use K8s API directly

### 6. **API Core Module**
- **Purpose**: HTTP routing and module orchestration
- **Interface**: REST API endpoints
- **Hidden**: Module initialization, request handling, error responses
- **Replaceable**: Yes - could use different framework or language

### 7. **A2A Module**
- **Purpose**: Enable agent-to-agent communication for multi-agent systems
- **Interface**: Secondary server on port 8000 with A2A protocol
- **Hidden**: Protocol handling, tool mapping, response formatting
- **Replaceable**: Yes - could disable A2A or use different protocol
- **Integration**: Runs in same process, uses existing API modules

### 8. **CLI Module**
- **Purpose**: Command-line interface for Kubently management
- **Interface**: Two distinct modes:
  - **Admin Operations**: Direct API calls for cluster management
  - **Interactive Debugging**: A2A protocol over WebSocket for real-time sessions
- **Hidden**: HTTP client implementation, WebSocket handling, config file format
- **Replaceable**: Yes - could create web UI, mobile app, or different CLI tool

### 9. **Deployment Module**
- **Purpose**: Package and deploy the system
- **Interface**: Helm values, environment variables
- **Hidden**: Kubernetes specifics, resource definitions
- **Replaceable**: Yes - could use Kustomize, operators, or terraform

## Success Criteria

1. **Functional**: Can debug pod crashes interactively via AI chat
2. **Performance**: < 500ms latency for command execution
3. **Reliability**: 99.9% command success rate
4. **Simplicity**: < 1500 total lines of code (including A2A)
5. **Security**: Pass security audit with no critical findings
6. **A2A Integration**: Successfully register and execute via A2A protocol
7. **Tool Discovery**: Dynamically expose available clusters and commands
8. **CLI Usability**: Complete cluster setup in < 2 minutes via CLI
9. **Interactive Debugging**: Real-time A2A sessions with streaming support

## A2A Protocol Integration

### Agent Card Definition
Kubently exposes its capabilities through an A2A Agent Card:
```python
AgentCard(
    name="Kubently Kubernetes Debugger",
    description="Agent for debugging and inspecting Kubernetes clusters",
    url="http://kubently-api:8000/",
    skills=[
        AgentSkill(
            id="kubernetes-debug",
            name="Kubernetes Debugging",
            description="Execute read-only kubectl commands across registered clusters",
            tags=["kubernetes", "debugging", "observability", "troubleshooting"],
            examples=[
                "Show all pods in namespace X",
                "Get logs for deployment Y",
                "Describe service Z",
                "List failing pods in cluster A"
            ]
        )
    ]
)
```

### Dynamic Tool Registration
The A2A server dynamically registers tools based on:
1. **Available Clusters**: Each connected cluster becomes a tool parameter
2. **Allowed Commands**: Read-only kubectl operations (get, describe, logs, top, etc.)
3. **Resource Types**: Pods, services, deployments, configmaps, secrets (read-only)

Example tool definition:
```python
{
    "name": "execute_kubectl",
    "description": "Execute a kubectl command in a Kubernetes cluster",
    "parameters": {
        "cluster_id": {
            "type": "string",
            "enum": ["prod-cluster-1", "staging-cluster-2"],  # Dynamically populated
            "description": "Target cluster for command execution"
        },
        "command": {
            "type": "string",
            "enum": ["get", "describe", "logs", "top", "events"],
            "description": "kubectl command to execute"
        },
        "resource": {
            "type": "string",
            "description": "Kubernetes resource (e.g., 'pods', 'svc/nginx')"
        },
        "namespace": {
            "type": "string",
            "default": "default",
            "description": "Kubernetes namespace"
        },
        "args": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Additional command arguments"
        }
    }
}
```

### A2A Request Flow
1. **Tool Discovery**: A2A client queries available tools from Kubently
2. **Command Execution**: Client sends A2A request with tool parameters
3. **Session Management**: A2A server creates implicit session for request chain
4. **Result Streaming**: For long operations, results stream back via A2A protocol
5. **Correlation Tracking**: All operations tagged with correlation ID for tracing

### Implementation Structure

```text
kubently/
├── api/
│   ├── main.py              # FastAPI REST endpoints + A2A startup
│   ├── a2a_server.py        # A2A server configuration
│   └── ...
├── a2a/                      # A2A protocol support
│   ├── __init__.py
│   ├── agent.py             # Agent definition and system prompt
│   ├── executor.py          # Command execution via internal API
│   ├── tools.py             # Dynamic tool registration
│   └── __main__.py          # Standalone A2A mode (optional)
└── ...
```

### Integration Benefits
- **Single Process**: Both servers run in one process, sharing resources
- **Shared State**: Same Redis connection, no synchronization needed
- **Unified Auth**: Same API keys and authentication logic
- **Simple Deployment**: One container, two ports
- **Optional A2A**: Can be disabled via environment variable

## Development Timeline

- Week 1: Core API, Agent, Queue, and Auth modules
- Week 2: Session management and optimizations
- Week 3: A2A protocol implementation and tool registry
- Week 4: CLI development with A2A client support
- Week 5: Integration testing with multi-agent systems

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Redis failure | Redis persistence + backup strategy |
| Network latency | Long polling + regional deployment |
| Agent compromise | Read-only RBAC + token rotation |
| API overload | Horizontal scaling + rate limiting |

## Interface Contracts

Each module must adhere to these interface principles:

### Module Independence
- Modules MUST NOT import implementation code from other modules
- Modules MUST only use public interfaces of other modules
- Modules MUST NOT expose internal state or implementation details
- Modules MUST be testable in isolation with mocked dependencies

### API Stability Contract
- Public interfaces MUST remain backward compatible
- Breaking changes require new interface versions
- Internal implementation can change freely without affecting consumers
- All interfaces MUST be documented with clear contracts

### Dependency Rules
- Core primitives (Models) have no dependencies
- Authentication, Session, Queue modules depend only on Models
- API Core orchestrates but doesn't implement business logic
- Agent is completely independent (could be in different language)

## A2A Integration Patterns

### MCP Tool Exposure
When integrated via MCP (Model Context Protocol), Kubently exposes the following tools:
- `create_debug_session(cluster_id, correlation_id)`
- `execute_kubectl(session_id, command, timeout)`
- `get_command_result(result_id)`
- `close_session(session_id)`

### Request/Response Headers
For A2A communication, include:
- `X-API-Key`: Service-scoped API key
- `X-Correlation-ID`: Trace requests across agent boundaries
- `X-Service-Identity`: Calling service identifier
- `X-Request-Timeout`: Maximum wait time for long operations

### Async Response Pattern (Future)
For long-running commands in A2A scenarios:
- Immediate response with operation ID
- Webhook callback when complete
- Polling endpoint for status checks

## Future Enhancements (Out of Scope for V1)

- WebSocket support for lower latency
- Multi-cluster command execution
- Command history and replay
- Automated remediation actions
- Metrics and observability dashboards
- Full async/webhook pattern for A2A
- Service mesh integration for zero-trust A2A
