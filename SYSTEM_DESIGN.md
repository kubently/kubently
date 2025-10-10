# Kubently System Design

## Overview

Kubently (*Kubernetes + Agentically*) is a real-time system that enables AI agents to troubleshoot Kubernetes clusters through agentic workflows. It uses Server-Sent Events (SSE) and Redis pub/sub for instant command delivery while supporting horizontal scaling.

## Architecture

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         External Clients                         │
│                   (AI Services, CLI, Web UI)                     │
└─────────────────┬───────────────────────────────────────────────┘
                  │ HTTPS
                  ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Kubernetes Ingress/LB                        │
└─────────────────┬───────────────────────────────────────────────┘
                  │ Round-robin distribution
                  ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Kubently API Pods                           │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │  Pod 1   │  │  Pod 2   │  │  Pod 3   │  │  Pod N   │  ...  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘       │
│       │              │              │              │             │
│       └──────────────┴──────────────┴──────────────┘            │
│                              │                                   │
│                   Redis Pub/Sub Channel                          │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                  ┌────────────▼────────────┐
                  │      Redis Cluster       │
                  │    (Pub/Sub + State)     │
                  └────────────┬────────────┘
                               │ SSE Connection
                               ▼
                  ┌──────────────────────────┐
                  │   Kubently Executors      │
                  │  (One per K8s cluster)    │
                  └──────────────────────────┘
```

### Data Flow

#### 1. Executor Connection (SSE)
```
Executor → GET /executor/stream → Any API Pod
         ↓
    Pod subscribes to Redis channel: executor-commands:{cluster_id}
         ↓
    Maintains persistent SSE connection
```

#### 2. Command Execution
```
Client → POST /debug/execute → Load Balancer → Any API Pod
         ↓
    Pod publishes command to Redis: executor-commands:{cluster_id}
         ↓
    Redis delivers to subscribed pod(s)
         ↓
    Pod with SSE connection sends command to executor
         ↓
    Executor executes kubectl command
         ↓
    Executor → POST /executor/results → Any API Pod
         ↓
    Result stored in Redis and returned to client
```

## Core Components

### 1. Kubently API (FastAPI)

**Purpose**: Orchestrates debugging sessions and command execution

**Key Features**:
- Horizontally scalable (multiple pods)
- Stateless design (all state in Redis)
- SSE endpoint for real-time agent streaming
- RESTful API for client interactions

**Endpoints**:
- `GET /executor/stream` - SSE connection for executors
- `POST /debug/execute` - Execute kubectl commands
- `POST /debug/session` - Create debugging session
- `POST /executor/results` - Receive command results
- `GET /health` - Health check

### 2. Kubently Executor

**Purpose**: Executes kubectl commands in target clusters

**Key Features**:
- SSE client for instant command reception
- No polling overhead
- Automatic reconnection
- Secure token-based authentication

**Executor Types**:
- `sse` - Server-Sent Events (default, recommended)
- `smart` - Adaptive polling (legacy)
- `legacy` - Simple polling (compatibility)

### 3. Redis

**Purpose**: Message distribution and state management

**Usage**:
- **Pub/Sub Channels**: Command distribution to executors
- **Keys**: Session state, command queues, results
- **TTL**: Automatic cleanup of expired data

**Channel Format**:
```
executor-commands:{cluster_id}  # Commands for specific executor
executor-results:{command_id}   # Command results
```

## Scalability Design

### Horizontal Scaling

The system supports unlimited API pod replicas through:

1. **Stateless API Pods**: All state stored in Redis
2. **Redis Pub/Sub**: Commands published to channels, not specific pods
3. **SSE Persistence**: Each executor maintains connection to one pod
4. **Load Balancing**: Ingress distributes requests evenly

### Performance Optimizations

1. **Instant Delivery**: SSE eliminates polling delays (~50ms delivery)
2. **Connection Reuse**: Single persistent connection per agent
3. **Efficient Pub/Sub**: O(1) message distribution via Redis
4. **Async Processing**: Non-blocking I/O throughout

### Scaling Limits

| Component | Limit | Bottleneck |
|-----------|-------|------------|
| API Pods | Unlimited | Kubernetes cluster capacity |
| Concurrent Executors | 10,000+ | Redis pub/sub connections |
| Commands/sec | 1,000+ | Redis throughput |
| Latency | ~50ms | Network + Redis |

## Security Architecture

### Authentication Layers

1. **Client → API**: API key authentication
2. **Executor → API**: Unique token per cluster
3. **API → Redis**: Network isolation (internal only)

### Command Validation

```python
ALLOWED_VERBS = ["get", "describe", "logs", "top", "explain"]
FORBIDDEN_ARGS = ["--token", "--kubeconfig", "--server"]
```

### RBAC Configuration

Minimal executor permissions:
```yaml
rules:
- apiGroups: ["*"]
  resources: ["*"]
  verbs: ["get", "list", "watch"]
```

## Module Architecture

Following black box principles from `sys-arch-prompt.md`:

### Module Boundaries

```
kubently/
├── modules/
│   ├── config/      # Configuration management
│   ├── auth/        # Authentication module
│   ├── session/     # Session management
│   ├── queue/       # Command queue operations
│   ├── api/         # API models and schemas
│   ├── executor/    # Agent implementations
│   └── a2a/         # Multi-agent communication
```

### Module Communication

- Modules communicate through well-defined interfaces
- No direct cross-module dependencies
- All inter-module data passes through Redis
- Each module is independently testable

## Deployment Architecture

### Kubernetes Resources

```yaml
Deployments:
  - kubently-api (3+ replicas)
  - kubently-executor (1 per cluster)

Services:
  - kubently-api (ClusterIP)

ConfigMaps:
  - kubently-config (API configuration)
  - kubently-executor-tokens (authentication)

Secrets:
  - kubently-api-keys
  - kubently-executor-token
```

### Helm Chart Structure

```
deployment/helm/kubently/
├── Chart.yaml
├── values.yaml
├── templates/
│   ├── api-deployment.yaml
│   ├── api-service.yaml
│   ├── executor-deployment.yaml
│   ├── configmap.yaml
│   └── secrets.yaml
```

## Performance Characteristics

### Latency Breakdown

```
Total Latency: ~350ms
├── Command Delivery (SSE): ~50ms
├── kubectl Execution: ~250ms
├── Result Return: ~50ms
```

### Resource Usage

| Component | CPU | Memory | Network |
|-----------|-----|--------|---------|
| API Pod | 100m-500m | 200-400MB | Low |
| Executor | 50m-200m | 50-80MB | Minimal |
| Redis | 100m-1000m | 100MB-1GB | Moderate |

## Failure Handling

### Executor Disconnection
1. SSE connection drops
2. Executor automatically reconnects
3. Resubscribes to Redis channel
4. No commands lost (queued in Redis)

### API Pod Failure
1. Load balancer routes to healthy pods
2. Executor SSE connections to failed pod reconnect
3. New pod receives connection
4. Commands continue flowing

### Redis Failure
1. Commands queue in API memory (short-term)
2. Alerts triggered for ops team
3. Redis Sentinel promotes replica (if configured)
4. Service resumes automatically

## Future Enhancements

### Near-term (Q1 2025)
- [ ] WebSocket support for bidirectional streaming
- [ ] Metrics and tracing (OpenTelemetry)
- [ ] Multi-cluster command fanout
- [ ] Command result caching

### Long-term (2025)
- [ ] Event-driven webhooks for A2A
- [ ] Automated remediation actions
- [ ] Time-travel debugging (command replay)
- [ ] GraphQL API for complex queries

## Design Decisions

### Why SSE over WebSockets?
1. **Simplicity**: HTTP-based, works through proxies
2. **Compatibility**: Better firewall/proxy support
3. **Unidirectional**: Matches our command flow model
4. **Auto-reconnect**: Built-in browser support

### Why Redis Pub/Sub?
1. **Simplicity**: No complex routing logic needed
2. **Performance**: O(1) message distribution
3. **Reliability**: Battle-tested in production
4. **Flexibility**: Easy to add new channels/patterns

### Why Not Connection Registry?
1. **Complexity**: Pod discovery and routing logic
2. **Failure Modes**: More points of failure
3. **Latency**: Additional hops for routing
4. **Maintenance**: Complex state management

## Testing Strategy

### Unit Tests
- Module isolation tests
- Mock Redis interactions
- Command validation logic

### Integration Tests
- Multi-pod command routing
- SSE connection handling
- Redis pub/sub flow

### E2E Tests
- Full command execution
- Latency measurements
- Failure recovery scenarios

### Load Tests
- 1000+ concurrent agents
- 1000+ commands/second
- Pod scaling behavior

## Monitoring & Observability

### Key Metrics
- SSE connection count
- Command latency (p50, p95, p99)
- Redis pub/sub lag
- Pod resource usage

### Alerts
- Executor disconnection rate > 5%
- Command latency p95 > 1s
- Redis memory > 80%
- API pod restarts > 3/hour

### Dashboards
- Real-time command flow
- Executor connection status
- Cluster health overview
- Performance trends

## Conclusion

The SSE + Redis pub/sub architecture provides:
- **Instant command delivery** (~50ms)
- **True horizontal scaling** (unlimited pods)
- **Simple, maintainable design**
- **Production-ready reliability**

This design balances performance, scalability, and maintainability while following established architectural principles and best practices.