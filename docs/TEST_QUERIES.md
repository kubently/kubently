# Kubently Test Queries Reference

This document provides ready-to-use test queries for validating Kubently functionality after deployments or changes.

## A2A Protocol Testing

### Endpoint Information
- **A2A Endpoint**: `http://localhost:8080/a2a/` (note the trailing slash - causes 307 redirect without it)
- **API Key Header**: `X-Api-Key: test-api-key`
- **Protocol**: JSON-RPC 2.0 with A2A message format

### Basic A2A Test Query (No Cluster Specified)

```bash
curl -L -X POST http://localhost:8080/a2a/ \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: test-api-key" \
  -d '{
    "jsonrpc": "2.0",
    "method": "message/stream",
    "params": {
      "message": {
        "messageId": "msg-test-'$(date +%s)'",
        "role": "user",
        "parts": [
          {
            "partId": "part-001",
            "text": "what pods are running in the kubently namespace?"
          }
        ]
      }
    },
    "id": 1
  }'
```

**Expected Response**: Should ask user to specify which cluster (kind or kubently)

### A2A Test Query (With Cluster Specified)

```bash
curl -L -X POST http://localhost:8080/a2a/ \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: test-api-key" \
  -d '{
    "jsonrpc": "2.0",
    "method": "message/stream",
    "params": {
      "message": {
        "messageId": "msg-test-'$(date +%s)'",
        "role": "user",
        "parts": [
          {
            "partId": "part-001",
            "text": "what pods are running in the kubently namespace in the kind cluster?"
          }
        ]
      }
    },
    "id": 1
  }'
```

**Expected Response**: Should list actual pods running in the kubently namespace

### Parse A2A Streaming Response

To extract just the text responses from the SSE stream:

```bash
# Get only the final response text
curl -L -X POST http://localhost:8080/a2a/ \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: test-api-key" \
  -d '{...}' 2>/dev/null | \
  grep '"text":' | \
  tail -1 | \
  jq -r '.result.status.message.parts[0].text // .result.artifact.parts[0].text'
```

### A2A Multi-Turn Conversation

```bash
# First message
CONTEXT_ID=$(curl -L -X POST http://localhost:8080/a2a/ \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: test-api-key" \
  -d '{
    "jsonrpc": "2.0",
    "method": "message/stream",
    "params": {
      "message": {
        "messageId": "msg-1",
        "role": "user",
        "parts": [{"partId": "part-1", "text": "use cluster kind"}]
      }
    },
    "id": 1
  }' 2>/dev/null | grep contextId | head -1 | jq -r '.result.contextId')

# Follow-up message using context
curl -L -X POST http://localhost:8080/a2a/ \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: test-api-key" \
  -d '{
    "jsonrpc": "2.0",
    "method": "message/stream",
    "params": {
      "message": {
        "messageId": "msg-2",
        "role": "user",
        "parts": [{"partId": "part-1", "text": "show me the api pods"}]
      },
      "contextId": "'$CONTEXT_ID'"
    },
    "id": 2
  }'
```

## Direct API Testing

### Health Check
```bash
curl http://localhost:8080/health
```

**Expected**: `{"status":"healthy","redis":"connected",...}`

### List Clusters
```bash
curl -H "X-Api-Key: test-api-key" \
  http://localhost:8080/debug/clusters
```

**Expected**: `{"clusters":["kind","kubently"]}`

### Execute kubectl Command
```bash
curl -X POST http://localhost:8080/debug/execute \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: test-api-key" \
  -d '{
    "cluster_id": "kind",
    "command_type": "get",
    "args": ["pods"],
    "namespace": "kubently"
  }'
```

## CLI Testing

### Kubently CLI (Interactive)
```bash
kubently --api-url localhost:8080 --api-key test-api-key debug
# Then in the session:
# > use cluster kind
# > show pods in kubently namespace
```

### Testing with Authentication

All A2A requests require the `x-api-key` header for authentication. Use the curl examples above with your API key.

## Common Issues and Solutions

### Issue: "Cannot POST /a2a" (404 Error)
**Solution**: Ensure trailing slash: `/a2a/` not `/a2a`

### Issue: "Authentication required: API key not provided"
**Solution**: Add header: `-H "X-Api-Key: test-api-key"`

### Issue: "Request payload validation error"
**Solution**: Check the message format - must include messageId, role, and parts array

### Issue: Port-forward lost after deployment
**Solution**: Re-establish with:
```bash
kubectl port-forward -n kubently svc/kubently-api 8080:8080 &
```

### Issue: Agent using old prompt behavior
**Solution**: Check system prompt version is up to date:
```bash
kubectl exec -n kubently deployment/kubently-api -- \
  grep "version:" /etc/kubently/prompts/system.prompt.yaml
```

## Automated Test Script

Create a test script `test-a2a.sh`:

```bash
#!/bin/bash
set -e

echo "Testing A2A endpoint..."

# Test 1: Query without cluster
echo "Test 1: Query without cluster specified"
response=$(curl -sL -X POST http://localhost:8080/a2a/ \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: test-api-key" \
  -d '{
    "jsonrpc": "2.0",
    "method": "message/stream",
    "params": {
      "message": {
        "messageId": "test-1",
        "role": "user",
        "parts": [{"partId": "p1", "text": "what pods are running?"}]
      }
    },
    "id": 1
  }' | grep -o '"text":"[^"]*"' | tail -1)

if [[ $response == *"specify which cluster"* ]]; then
  echo "✅ Test 1 passed: Agent asks for cluster specification"
else
  echo "❌ Test 1 failed: Unexpected response"
  echo "$response"
  exit 1
fi

# Test 2: Query with cluster
echo "Test 2: Query with cluster specified"
response=$(curl -sL -X POST http://localhost:8080/a2a/ \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: test-api-key" \
  -d '{
    "jsonrpc": "2.0",
    "method": "message/stream",
    "params": {
      "message": {
        "messageId": "test-2",
        "role": "user",
        "parts": [{"partId": "p1", "text": "list pods in kubently namespace in kind cluster"}]
      }
    },
    "id": 2
  }' | grep -o '"text":"[^"]*"' | tail -1)

if [[ $response == *"kubently-api"* ]] || [[ $response == *"Running"* ]]; then
  echo "✅ Test 2 passed: Agent returns pod information"
else
  echo "❌ Test 2 failed: Unexpected response"
  echo "$response"
  exit 1
fi

echo "✅ All tests passed!"
```

## Integration with deploy-test.sh

Add this to the end of `deploy-test.sh` to automatically run tests after deployment:

```bash
# Run automated tests
if [ "${RUN_TESTS:-true}" = "true" ]; then
  echo "🧪 Running automated tests..."
  bash test-a2a.sh
fi
```