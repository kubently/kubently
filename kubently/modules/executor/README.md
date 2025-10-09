# Kubently Executor

The Kubently Executor is a lightweight component that runs in Kubernetes clusters to execute kubectl commands on behalf of the central Kubently API.

## Overview

The executor operates as a black box that:
- Connects to the Kubently API for commands via Server-Sent Events (SSE)
- Executes read-only kubectl commands in the local cluster
- Returns results to the API
- Maintains persistent connection for instant command delivery

## Architecture

The executor is designed to be:
- **Stateless**: No local state storage
- **Secure**: Only executes whitelisted read-only commands
- **Lightweight**: < 100MB memory, < 0.1 CPU usage
- **Reliable**: Automatic reconnection and error handling

## Deployment

### Prerequisites

1. A running Kubently API instance
2. A Kubernetes cluster where you want to deploy the executor
3. kubectl access to the cluster

### Quick Start

1. **Generate a secure token for your cluster:**
   ```bash
   # Generate a random token
   TOKEN=$(openssl rand -hex 32)
   echo "Save this token: $TOKEN"
   
   # Create base64 encoded version for Kubernetes secret
   echo -n "$TOKEN" | base64
   ```

2. **Update the deployment configuration:**
   
   Edit `k8s-deployment.yaml` and set:
   - `KUBENTLY_API_URL`: Your Kubently API endpoint
   - `CLUSTER_ID`: A unique identifier for this cluster
   - Update the Secret with your base64-encoded token

3. **Deploy the executor:**
   ```bash
   kubectl apply -f k8s-deployment.yaml
   ```

4. **Verify deployment:**
   ```bash
   kubectl -n kubently get pods
   kubectl -n kubently logs -l app=kubently-executor
   ```

### Configuration

Environment variables:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `KUBENTLY_API_URL` | Yes | - | URL of the Kubently API |
| `CLUSTER_ID` | Yes | - | Unique identifier for this cluster |
| `KUBENTLY_TOKEN` | Yes | - | Authentication token |
| `EXECUTOR_TYPE` | No | sse | Executor type (sse, smart, legacy) |
| `LOG_LEVEL` | No | INFO | Logging level (DEBUG, INFO, WARNING, ERROR) |

### Building the Docker Image

```bash
# Build the image
docker build -t kubently/executor:latest .

# Push to your registry
docker tag kubently/executor:latest your-registry/kubently/executor:latest
docker push your-registry/kubently/executor:latest
```

## Security

### RBAC Permissions

The executor uses a read-only ClusterRole that allows:
- `get`, `list`, `watch` on all resources
- No write permissions

### Command Validation

The executor validates all commands before execution:
- Only whitelisted kubectl verbs (get, describe, logs, etc.)
- Rejects any write operations
- Blocks credential-related arguments
- Prevents command injection

### Network Security

- Executor only makes outbound HTTPS connections
- No inbound network exposure required
- Uses bearer token authentication
- All communication encrypted with TLS

## Operations

### Monitoring

Check executor health:
```bash
kubectl -n kubently get pods -l app=kubently-executor
kubectl -n kubently logs -l app=kubently-executor --tail=50
```

### Troubleshooting

1. **Executor not receiving commands:**
   - Check network connectivity to API
   - Verify token is correct
   - Check API logs for authentication errors

2. **Commands failing:**
   - Verify ServiceAccount has correct permissions
   - Check kubectl is working in the pod
   - Review executor logs for validation errors

3. **High latency:**
   - Check network latency to API
   - Verify Redis is responsive (API side)
   - Look for rate limiting in logs

### Updating

To update the executor:
```bash
# Update the image tag in deployment
kubectl -n kubently set image deployment/kubently-executor executor=kubently/executor:new-version

# Or reapply the manifest
kubectl apply -f k8s-deployment.yaml
```

### Removing

To remove the executor:
```bash
kubectl delete -f k8s-deployment.yaml
```

## Development

### Local Testing

Run the executor locally:
```bash
# Set environment variables
export KUBENTLY_API_URL=http://localhost:8000
export CLUSTER_ID=local-test
export KUBENTLY_TOKEN=test-token

# Run the executor
python sse_executor.py
```

### Testing with Mock API

Create a simple mock API for testing:
```python
from fastapi import FastAPI
app = FastAPI()

@app.get("/executor/stream")
def stream():
    return StreamingResponse(sse_stream(), media_type="text/plain")

@app.post("/executor/results")
def results(data: dict):
    print(f"Received result: {data}")
    return {"status": "ok"}
```

## Performance

Expected resource usage:
- **Memory**: 50-100MB typical, 256MB limit
- **CPU**: 0.01-0.05 cores idle, 0.1-0.2 cores active
- **Network**: < 1KB/poll, < 100KB/command result
- **Latency**: < 500ms command execution

## API Integration

The executor communicates with these API endpoints:

- `GET /executor/stream` - SSE connection for instant command delivery
- `POST /executor/results` - Submit command results

## License

Part of the Kubently project. See main repository for license details.