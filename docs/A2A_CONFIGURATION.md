# A2A (Agent-to-Agent) Configuration Guide

## Overview

Kubently implements the **standard A2A (Agent-to-Agent) protocol** from the [A2A Project](https://github.com/a2aproject/A2A), allowing it to interact with other AI agents in a multi-agent system. The A2A server runs integrated with the main API server with endpoints mounted at `/a2a` on the main API port.

**Important**: A2A is core functionality in Kubently and is always enabled. It cannot be disabled.

### Protocol Implementation

Kubently uses the official A2A Python SDK (`a2a>=0.1.0`) for server-side implementation, ensuring full compatibility with the A2A standard. This means:

- Any A2A-compliant client can communicate with Kubently
- Kubently follows the standard message formats and streaming protocols
- The implementation is maintained and updated with the official A2A specification

## Configuration Options

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `A2A_EXTERNAL_URL` | `http://localhost:8080/a2a/` | External URL for A2A agent card (used by clients) |
| `API_PORT` | `8080` | Main API server port |
| `A2A_SERVER_DEBUG` | `false` | Enable debug logging for A2A server |

## Deployment Configuration

A2A endpoints are available at `/a2a` on the main API port:

```yaml
# Environment configuration
API_PORT: "8080"
A2A_EXTERNAL_URL: "http://localhost:8080/a2a/"  # Adjust for your environment
```

Access A2A endpoints at: `http://your-api-host:8080/a2a`

## Kubernetes Deployment

### Using Helm

Update your `values.yaml`:

```yaml
api:
  env:
    A2A_EXTERNAL_URL: "http://localhost:8080/a2a/"  # For local development
    API_PORT: "8080"
```

Deploy with:
```bash
helm upgrade --install kubently ./deployment/helm/kubently
```

### Using Raw Kubernetes Manifests

1. Generate manifests from Helm:
```bash
make helm-template
```

2. Update the generated ConfigMap in `generated-manifests/kubently-manifests.yaml`:
```yaml
data:
  A2A_EXTERNAL_URL: "http://your-service:8080/a2a/"
  API_PORT: "8080"
```

3. Ensure the API port is exposed in the deployment:
```yaml
ports:
- containerPort: 8080
  name: http
```

3. Update the service to expose the API port:
```yaml
ports:
- port: 8080
  targetPort: 8080
  name: http
```

## Local Development & Testing

### Port Forwarding for A2A Client

When testing with a local A2A client, use port forwarding:

```bash
# Forward API port (A2A accessible at /a2a)
kubectl port-forward svc/kubently-api 8080:8080
```

### Testing with A2A Clients

#### Using Official A2A Client Libraries

For Python applications:
```bash
# Install the official A2A client
pip install a2a

# Example usage
from a2a.client import AsyncA2AClient
from a2a.types import Message, MessagePart

client = AsyncA2AClient(
    base_url="http://localhost:8080/a2a",
    headers={"X-API-Key": "your-api-key"}
)

message = Message(
    role="user",
    parts=[MessagePart(text="List pods in kind cluster")]
)

async for event in client.send_message_stream(message):
    # Process streaming response
    print(event)
```

For JavaScript/TypeScript applications:
```bash
# Install the official A2A client
npm install @a2aproject/a2a-client

# Example usage
import { A2AClient } from '@a2aproject/a2a-client';

const client = new A2AClient({
  baseUrl: 'http://localhost:8080/a2a',
  headers: { 'X-API-Key': 'your-api-key' }
});

const response = await client.sendMessage({
  role: 'user',
  parts: [{ text: 'List pods in kind cluster' }]
});
```

#### Using curl for Testing

For interactive testing, use curl with the proper headers:
```bash
# Test A2A endpoint with authentication
curl -X POST http://localhost:8080/a2a/ \
  -H "Content-Type: application/json" \
  -H "x-api-key: test-api-key" \
  -d @docs/test-queries/simple-test.json
```

See `docs/TEST_QUERIES.md` for complete examples and message formats.

### What `message/stream` emits

The stream is SSE. A subscriber sees, in this order:

```
task                                    immediately
status-update  working                  the model's text, as it is produced
status-update  working  (tool call)     each tool call, when it finishes
status-update  working                  more text ...
artifact-update         debug_result    the whole answer, authoritative
status-update  completed | failed       terminal; the stream ends here
```

Tool calls arrive **before** the answer they informed, in the order the agent
ran them.

**Text frames.** The answer is split across `working` statuses at newline
boundaries, and the newline a frame was cut on is *not* included in the frame.
Join consecutive `working` texts with `"\n"` and you have the model's output
character for character. The final `artifact-update` carries the whole answer
and is authoritative -- replace what you accumulated with it rather than
appending to it.

**Tool calls.** A `working` status for a tool call carries two renderings of
the same facts. The text part is the original prose:

```
🔧 Tool Call: execute_kubectl({
  "cluster_id": "prod-1",
  "command": "get pods -n checkout"
})
✅ Result: checkout-7d9  0/1  CrashLoopBackOff  5  2m...
```

and `metadata` -- on the status update *and* on its message -- carries the same
call as typed fields:

```json
"metadata": {
  "kubently/event": "tool_call",
  "kubently/tool_call": {
    "schema": "kubently.tool_call/v1",
    "id": "tc_1758...",
    "tool": "execute_kubectl",
    "args": {"cluster_id": "prod-1", "command": "get pods -n checkout"},
    "outcome": "ok",
    "result": "checkout-7d9  0/1  CrashLoopBackOff  5  2m",
    "startedAt": "2026-08-21T10:14:07.921000",
    "completedAt": "2026-08-21T10:14:09.104000"
  }
}
```

Prefer the metadata: it is typed, whereas the text is prose that a formatting
change can break. `outcome` is `ok` or `error`, or **absent** when the call
never reported one -- render that as unknown rather than guessing. Ignore a
`kubently/tool_call` whose `schema` you do not recognise and fall back to the
text, which is not going away.

Other `working` statuses carry `"kubently/event": "token"` (a fragment of the
answer), or `"message"` / `"error"`. Treat an unknown `kubently/event` as text.

**Terminal states.** `completed` means the artifact is a whole answer. `failed`
means the run died -- its status message is the reason, not an answer. A stream
that ends without a terminal state was dropped in transit, and what you have is
a fragment.

### Verifying A2A Configuration

Check the agent card endpoint:
```bash
# `/.well-known/agent-card.json` is the current spec path; Kubently also
# serves the pre-1.0 `/.well-known/agent.json` for older clients.
curl http://localhost:8080/a2a/.well-known/agent-card.json | jq .
```

Expected response:
```json
{
  "name": "Kubently Kubernetes Debugger",
  "description": "AI agent for debugging Kubernetes clusters through kubectl commands",
  "url": "http://localhost:8080/a2a/",
  "version": "1.0.0",
  "capabilities": {
    "streaming": true,
    "pushNotifications": false
  },
  "skills": [...]
}
```

## Production Considerations

### External URL Configuration

For production deployments, set `A2A_EXTERNAL_URL` to the publicly accessible URL:

```yaml
A2A_EXTERNAL_URL: "https://kubently.your-domain.com/a2a/"
```

This URL is included in the agent card and tells A2A clients how to connect back to your service.

### Security

- Use TLS/HTTPS in production
- Configure appropriate network policies
- Use API keys for authentication
- Consider using an ingress controller for external access

### Example Ingress Configuration

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: kubently-api
spec:
  rules:
  - host: kubently.your-domain.com
    http:
      paths:
      - path: /a2a
        pathType: Prefix
        backend:
          service:
            name: kubently-api
            port:
              number: 8080
```

## Troubleshooting

### Common Issues

1. **Connection refused to A2A endpoints**
   - Check pod logs: `kubectl logs deployment/kubently-api | grep -i a2a`
   - A2A is always enabled; ensure port 8080 is accessible
   - Verify the service is running: `kubectl get pods -l app=kubently-api`

2. **Client gets wrong URL from agent card**
   - Set `A2A_EXTERNAL_URL` to the correct external URL (including `/a2a/` path)
   - Restart the deployment after changing environment variables

3. **A2A client connection issues**
   - Verify A2A endpoints are accessible at `/a2a` path
   - Check firewall/ingress rules for port 8080

### Debug Commands

```bash
# Check A2A server status
kubectl logs deployment/kubently-api | grep -i "a2a"

# Verify environment variables
kubectl exec deployment/kubently-api -- env | grep A2A

# Test A2A endpoint directly
curl -X POST http://localhost:8080/a2a/agent/tasks \
  -H "Content-Type: application/json" \
  -d '{"query": "List all pods"}'
```

## Multi-Agent System Integration

When integrating with platforms like CNOE's platform-engineering-mas:

1. Ensure A2A is enabled with correct external URL
2. Configure service discovery or DNS for agent-to-agent communication  
3. Set appropriate `A2A_EXTERNAL_URL` for your network topology (including `/a2a/` path)
4. Use service mesh or network policies for secure communication

## References

- [A2A Protocol Specification](https://github.com/a2aproject/A2A) - Official A2A Protocol
- [A2A Python SDK](https://pypi.org/project/a2a/) - Official Python implementation
- [A2A JavaScript SDK](https://github.com/a2aproject/a2a-js) - Official JavaScript/TypeScript implementation
- [Kubently Documentation](../README.md)