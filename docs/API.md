# Kubently API Reference

## Table of Contents
- [Overview](#overview)
- [Authentication](#authentication)
- [API Endpoints](#api-endpoints)
- [Data Models](#data-models)
- [Error Handling](#error-handling)
- [Rate Limiting](#rate-limiting)
- [Examples](#examples)

## Overview

The Kubently API provides RESTful endpoints for managing Kubernetes debugging sessions and executing kubectl commands. All requests and responses use JSON format.

### Base URL

```text
https://api.kubently.example.com
```

### API Version
Current version: `1.0.0`

### Content Type
- Request: `application/json`
- Response: `application/json`

## Authentication

Kubently uses token-based authentication for both clients and agents.

### Client Authentication

Clients (AI agents, users) authenticate using API keys in the Authorization header:

```http
Authorization: Bearer <api-key>
```

### Agent Authentication

Agents authenticate using cluster-specific tokens:

```http
Authorization: Bearer <agent-token>
X-Cluster-ID: <cluster-id>
```

### A2A Headers

For agent-to-agent communication, include additional headers:

```http
X-API-Key: <service-scoped-key>
X-Correlation-ID: <trace-id>
X-Service-Identity: <calling-service>
X-Request-Timeout: <timeout-seconds>
```

## API Endpoints

### Health Check

#### GET /health

Check API health and status.

**Request:**
```http
GET /health HTTP/1.1
```

**Response:**
```json
{
  "status": "healthy",
  "redis": "connected",
  "version": "1.0.0",
  "uptime_seconds": 3600,
  "active_sessions": 5
}
```

**Status Codes:**
- `200 OK` - Service is healthy
- `503 Service Unavailable` - Service is unhealthy

---

### Session Management

#### POST /debug/session

Create a new debugging session.

**Request:**
```json
{
  "cluster_id": "production-cluster-1",
  "user_id": "user-123",
  "correlation_id": "trace-abc-123",
  "service_identity": "orchestrator",
  "ttl_seconds": 300
}
```

**Response:**
```json
{
  "session_id": "sess-abc-123",
  "cluster_id": "production-cluster-1",
  "status": "active",
  "created_at": "2024-01-01T12:00:00Z",
  "expires_at": "2024-01-01T12:05:00Z",
  "ttl_seconds": 300,
  "correlation_id": "trace-abc-123",
  "service_identity": "orchestrator"
}
```

**Status Codes:**
- `201 Created` - Session created successfully
- `400 Bad Request` - Invalid request parameters
- `401 Unauthorized` - Invalid or missing API key
- `429 Too Many Requests` - Rate limit exceeded

#### GET /debug/session/{session_id}

Get session details.

**Request:**
```http
GET /debug/session/sess-abc-123 HTTP/1.1
Authorization: Bearer <api-key>
```

**Response:**
```json
{
  "session_id": "sess-abc-123",
  "cluster_id": "production-cluster-1",
  "status": "active",
  "created_at": "2024-01-01T12:00:00Z",
  "expires_at": "2024-01-01T12:05:00Z",
  "last_activity": "2024-01-01T12:02:00Z",
  "command_count": 5,
  "ttl_seconds": 300
}
```

**Status Codes:**
- `200 OK` - Session found
- `404 Not Found` - Session not found
- `401 Unauthorized` - Invalid or missing API key

#### DELETE /debug/session/{session_id}

End a debugging session.

**Request:**
```http
DELETE /debug/session/sess-abc-123 HTTP/1.1
Authorization: Bearer <api-key>
```

**Response:**
```json
{
  "message": "Session ended successfully",
  "session_id": "sess-abc-123"
}
```

**Status Codes:**
- `200 OK` - Session ended
- `404 Not Found` - Session not found
- `401 Unauthorized` - Invalid or missing API key

---

### Command Execution

#### POST /debug/execute

Execute a kubectl command.

**Request:**
```json
{
  "cluster_id": "production-cluster-1",
  "session_id": "sess-abc-123",
  "correlation_id": "trace-def-456",
  "command_type": "get",
  "args": ["pods", "-n", "default", "-o", "wide"],
  "namespace": "default",
  "timeout_seconds": 10
}
```

**Response:**
```json
{
  "command_id": "cmd-xyz-789",
  "session_id": "sess-abc-123",
  "cluster_id": "production-cluster-1",
  "status": "success",
  "correlation_id": "trace-def-456",
  "output": "NAME                     READY   STATUS    RESTARTS   AGE\nnginx-deployment-xyz     1/1     Running   0          5d",
  "error": null,
  "execution_time_ms": 250,
  "executed_at": "2024-01-01T12:02:30Z"
}
```

**Status Codes:**
- `200 OK` - Command executed successfully
- `400 Bad Request` - Invalid command or parameters
- `401 Unauthorized` - Invalid or missing API key
- `404 Not Found` - Session or cluster not found
- `408 Request Timeout` - Command execution timeout
- `422 Unprocessable Entity` - Forbidden command detected

#### POST /debug/execute/async

Execute a command asynchronously (returns immediately).

**Request:**
```json
{
  "cluster_id": "production-cluster-1",
  "command_type": "logs",
  "args": ["pod/nginx-abc", "-f", "--tail=100"],
  "timeout_seconds": 30
}
```

**Response:**
```json
{
  "operation_id": "op-123-456",
  "status": "pending",
  "poll_url": "/debug/operations/op-123-456"
}
```

**Status Codes:**
- `202 Accepted` - Command queued
- `400 Bad Request` - Invalid parameters
- `401 Unauthorized` - Invalid or missing API key

#### GET /debug/operations/{operation_id}

Check async operation status.

**Request:**
```http
GET /debug/operations/op-123-456 HTTP/1.1
Authorization: Bearer <api-key>
```

**Response (Pending):**
```json
{
  "operation_id": "op-123-456",
  "status": "pending",
  "created_at": "2024-01-01T12:00:00Z"
}
```

**Response (Completed):**
```json
{
  "operation_id": "op-123-456",
  "status": "completed",
  "result": {
    "command_id": "cmd-xyz-789",
    "status": "success",
    "output": "...",
    "execution_time_ms": 1500
  },
  "created_at": "2024-01-01T12:00:00Z",
  "completed_at": "2024-01-01T12:00:02Z"
}
```

---

### Agent Endpoints

#### GET /agent/status

Get cluster status for agent polling optimization.

**Request:**
```http
GET /agent/status HTTP/1.1
Authorization: Bearer <agent-token>
X-Cluster-ID: production-cluster-1
```

**Response:**
```json
{
  "cluster_id": "production-cluster-1",
  "is_active": true,
  "queue_depth": 3
}
```

**Status Codes:**
- `200 OK` - Status retrieved
- `401 Unauthorized` - Invalid token or cluster ID

#### GET /agent/commands

Long poll for commands (agent use only).

**Request:**
```http
GET /agent/commands?wait=30 HTTP/1.1
Authorization: Bearer <agent-token>
X-Cluster-ID: production-cluster-1
```

**Response:**
```json
{
  "commands": [
    {
      "id": "cmd-abc-123",
      "args": ["get", "pods", "-A"],
      "timeout": 10,
      "session_id": "sess-xyz-789"
    }
  ]
}
```

**Status Codes:**
- `200 OK` - Commands available
- `204 No Content` - No commands (timeout)
- `401 Unauthorized` - Invalid token

#### POST /agent/results

Submit command execution results.

**Request:**
```json
{
  "command_id": "cmd-abc-123",
  "result": {
    "success": true,
    "output": "NAME                     READY   STATUS\nnginx-deployment-xyz     1/1     Running",
    "error": null,
    "exit_code": 0,
    "execution_time_ms": 150
  }
}
```

**Response:**
```json
{
  "message": "Result stored successfully",
  "command_id": "cmd-abc-123"
}
```

**Status Codes:**
- `200 OK` - Result stored
- `400 Bad Request` - Invalid result format
- `401 Unauthorized` - Invalid token
- `404 Not Found` - Command not found

---

## Data Models

### CreateSessionRequest

```typescript
{
  cluster_id: string;          // Required, pattern: ^[a-z0-9][a-z0-9-]*[a-z0-9]$
  user_id?: string;            // Optional user/AI identifier
  correlation_id?: string;     // Optional A2A correlation ID
  service_identity?: string;   // Optional calling service
  ttl_seconds?: number;        // Optional, default: 300, range: 60-3600
}
```

### ExecuteCommandRequest

```typescript
{
  cluster_id: string;          // Required
  session_id?: string;         // Optional session association
  correlation_id?: string;     // Optional A2A correlation ID
  command_type: CommandType;   // Enum: get, describe, logs, top, etc.
  args: string[];             // Required, 1-20 items
  namespace?: string;         // Optional, default: "default"
  timeout_seconds?: number;   // Optional, default: 10, range: 1-30
}
```

### CommandType Enum

```typescript
enum CommandType {
  GET = "get",
  DESCRIBE = "describe",
  LOGS = "logs",
  TOP = "top",
  EVENTS = "events",
  VERSION = "version",
  API_RESOURCES = "api-resources",
  API_VERSIONS = "api-versions",
  EXPLAIN = "explain"
}
```

### ExecutionStatus Enum

```typescript
enum ExecutionStatus {
  PENDING = "pending",
  RUNNING = "running",
  SUCCESS = "success",
  FAILURE = "failure",
  TIMEOUT = "timeout",
  CANCELLED = "cancelled"
}
```

### SessionStatus Enum

```typescript
enum SessionStatus {
  ACTIVE = "active",
  IDLE = "idle",
  EXPIRED = "expired",
  ENDED = "ended"
}
```

### ErrorResponse

```typescript
{
  error: string;              // Error message
  details?: object;           // Additional error details
  request_id?: string;        // Request tracking ID
  timestamp: string;          // ISO 8601 timestamp
}
```

## Error Handling

### Error Response Format

All errors return a consistent JSON structure:

```json
{
  "error": "Human-readable error message",
  "details": {
    "code": "ERROR_CODE",
    "field": "cluster_id",
    "value": "Invalid-Cluster"
  },
  "request_id": "req-abc-123",
  "timestamp": "2024-01-01T12:00:00Z"
}
```

### Common Error Codes

| Code | HTTP Status | Description |
|------|-------------|-------------|
| `INVALID_REQUEST` | 400 | Malformed request |
| `UNAUTHORIZED` | 401 | Missing or invalid authentication |
| `FORBIDDEN` | 403 | Insufficient permissions |
| `NOT_FOUND` | 404 | Resource not found |
| `COMMAND_FORBIDDEN` | 422 | Dangerous command detected |
| `RATE_LIMITED` | 429 | Too many requests |
| `TIMEOUT` | 408 | Request timeout |
| `INTERNAL_ERROR` | 500 | Server error |
| `SERVICE_UNAVAILABLE` | 503 | Service temporarily unavailable |

### Validation Errors

```json
{
  "error": "Validation failed",
  "details": {
    "code": "VALIDATION_ERROR",
    "errors": [
      {
        "field": "cluster_id",
        "message": "Must be lowercase alphanumeric with hyphens"
      },
      {
        "field": "args",
        "message": "Forbidden argument: delete"
      }
    ]
  },
  "request_id": "req-abc-123",
  "timestamp": "2024-01-01T12:00:00Z"
}
```

## Rate Limiting

### Limits

- **Client API calls**: 100 requests per minute per API key
- **Command execution**: 50 commands per minute per cluster
- **Session creation**: 10 sessions per minute per API key

### Rate Limit Headers

```http
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 45
X-RateLimit-Reset: 1704110460
```

### Rate Limit Error

```json
{
  "error": "Rate limit exceeded",
  "details": {
    "code": "RATE_LIMITED",
    "limit": 100,
    "reset_at": "2024-01-01T12:01:00Z"
  },
  "request_id": "req-abc-123",
  "timestamp": "2024-01-01T12:00:00Z"
}
```

## Examples

### Python Client Example

```python
import requests
import json

class KubentlyClient:
    def __init__(self, api_url, api_key):
        self.api_url = api_url
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

    def create_session(self, cluster_id):
        response = requests.post(
            f"{self.api_url}/debug/session",
            headers=self.headers,
            json={"cluster_id": cluster_id}
        )
        response.raise_for_status()
        return response.json()

    def execute_command(self, cluster_id, command, args, session_id=None):
        payload = {
            "cluster_id": cluster_id,
            "command_type": command,
            "args": args
        }
        if session_id:
            payload["session_id"] = session_id

        response = requests.post(
            f"{self.api_url}/debug/execute",
            headers=self.headers,
            json=payload
        )
        response.raise_for_status()
        return response.json()

# Usage
client = KubentlyClient("https://api.kubently.example.com", "your-api-key")

# Create session
session = client.create_session("production-cluster-1")
print(f"Session created: {session['session_id']}")

# Execute command
result = client.execute_command(
    "production-cluster-1",
    "get",
    ["pods", "-n", "kube-system"],
    session["session_id"]
)
print(f"Command output:\n{result['output']}")
```

### cURL Examples

#### Create Session
```bash
curl -X POST https://api.kubently.example.com/debug/session \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "cluster_id": "production-cluster-1",
    "ttl_seconds": 600
  }'
```

#### Execute Command
```bash
curl -X POST https://api.kubently.example.com/debug/execute \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "cluster_id": "production-cluster-1",
    "command_type": "get",
    "args": ["pods", "-A", "--no-headers", "|", "wc", "-l"]
  }'
```

#### Get Logs
```bash
curl -X POST https://api.kubently.example.com/debug/execute \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "cluster_id": "production-cluster-1",
    "command_type": "logs",
    "args": ["deployment/nginx", "--tail=50"],
    "namespace": "default"
  }'
```

### JavaScript/TypeScript Example

```typescript
interface KubentlySession {
  session_id: string;
  cluster_id: string;
  status: string;
  expires_at: string;
}

interface CommandResult {
  command_id: string;
  status: string;
  output?: string;
  error?: string;
  execution_time_ms: number;
}

class KubentlyClient {
  private apiUrl: string;
  private headers: Record<string, string>;

  constructor(apiUrl: string, apiKey: string) {
    this.apiUrl = apiUrl;
    this.headers = {
      'Authorization': `Bearer ${apiKey}`,
      'Content-Type': 'application/json'
    };
  }

  async createSession(clusterId: string): Promise<KubentlySession> {
    const response = await fetch(`${this.apiUrl}/debug/session`, {
      method: 'POST',
      headers: this.headers,
      body: JSON.stringify({ cluster_id: clusterId })
    });

    if (!response.ok) {
      throw new Error(`Failed to create session: ${response.statusText}`);
    }

    return response.json();
  }

  async executeCommand(
    clusterId: string,
    commandType: string,
    args: string[],
    sessionId?: string
  ): Promise<CommandResult> {
    const payload = {
      cluster_id: clusterId,
      command_type: commandType,
      args: args,
      ...(sessionId && { session_id: sessionId })
    };

    const response = await fetch(`${this.apiUrl}/debug/execute`, {
      method: 'POST',
      headers: this.headers,
      body: JSON.stringify(payload)
    });

    if (!response.ok) {
      throw new Error(`Command execution failed: ${response.statusText}`);
    }

    return response.json();
  }
}

// Usage
const client = new KubentlyClient(
  'https://api.kubently.example.com',
  'your-api-key'
);

async function debugPods() {
  try {
    // Create session
    const session = await client.createSession('production-cluster-1');
    console.log(`Session created: ${session.session_id}`);

    // Get pods
    const result = await client.executeCommand(
      'production-cluster-1',
      'get',
      ['pods', '-n', 'default'],
      session.session_id
    );

    console.log(`Pods in default namespace:\n${result.output}`);
  } catch (error) {
    console.error('Error:', error);
  }
}

debugPods();
```

### Go Client Example

```go
package main

import (
    "bytes"
    "encoding/json"
    "fmt"
    "net/http"
)

type KubentlyClient struct {
    APIUrl string
    APIKey string
}

type Session struct {
    SessionID string `json:"session_id"`
    ClusterID string `json:"cluster_id"`
    Status    string `json:"status"`
}

type CommandResult struct {
    CommandID      string `json:"command_id"`
    Status         string `json:"status"`
    Output         string `json:"output"`
    ExecutionTime  int    `json:"execution_time_ms"`
}

func (c *KubentlyClient) CreateSession(clusterID string) (*Session, error) {
    payload := map[string]string{
        "cluster_id": clusterID,
    }

    body, _ := json.Marshal(payload)
    req, err := http.NewRequest("POST",
        fmt.Sprintf("%s/debug/session", c.APIUrl),
        bytes.NewBuffer(body))

    if err != nil {
        return nil, err
    }

    req.Header.Set("Authorization", fmt.Sprintf("Bearer %s", c.APIKey))
    req.Header.Set("Content-Type", "application/json")

    client := &http.Client{}
    resp, err := client.Do(req)
    if err != nil {
        return nil, err
    }
    defer resp.Body.Close()

    var session Session
    json.NewDecoder(resp.Body).Decode(&session)
    return &session, nil
}

func main() {
    client := &KubentlyClient{
        APIUrl: "https://api.kubently.example.com",
        APIKey: "your-api-key",
    }

    session, err := client.CreateSession("production-cluster-1")
    if err != nil {
        panic(err)
    }

    fmt.Printf("Session created: %s\n", session.SessionID)
}
```

## WebSocket Support (Future)

### Connection

```javascript
const ws = new WebSocket('wss://api.kubently.example.com/ws');

ws.onopen = () => {
  // Authenticate
  ws.send(JSON.stringify({
    type: 'auth',
    token: 'your-api-key'
  }));
};

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);

  switch(data.type) {
    case 'auth_success':
      // Start session
      ws.send(JSON.stringify({
        type: 'create_session',
        cluster_id: 'production-cluster-1'
      }));
      break;

    case 'command_result':
      console.log('Result:', data.output);
      break;
  }
};

// Execute command
ws.send(JSON.stringify({
  type: 'execute',
  cluster_id: 'production-cluster-1',
  command_type: 'get',
  args: ['pods']
}));
```

## API Versioning

The API uses URL-based versioning. Future versions will be available at:

- `https://api.kubently.example.com/v1/` (current)
- `https://api.kubently.example.com/v2/` (future)

Version information is also available in response headers:

```http
X-API-Version: 1.0.0
```
