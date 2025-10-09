# A2A Authentication

## Overview

The Kubently A2A (Agent-to-Agent) protocol server requires API key authentication for all debug session requests. This ensures that only authorized clients can execute Kubernetes operations through the debug interface.

## Authentication Flow

1. **Client sends request** with `X-API-Key` header
2. **Middleware validates** the API key against Redis storage
3. **Valid keys** proceed to agent execution
4. **Invalid/missing keys** receive 401 Unauthorized response

## Required Headers

All A2A requests (except the agent card endpoint) must include:

```http
X-API-Key: your-api-key-here
```

Alternative header names also accepted:
- `X-Api-Key`
- `x-api-key`

## Endpoints

### Public Endpoints (No Auth Required)

- `GET /` - Returns agent card with capabilities

### Protected Endpoints (Auth Required)

- `POST /` - JSON-RPC message endpoint for debug sessions
  - Method: `message/send`
  - Requires valid API key

## Response Formats

### Successful Authentication

```json
{
  "jsonrpc": "2.0",
  "id": "request-id",
  "result": {
    "contextId": "session-id",
    "artifacts": [...]
  }
}
```

### Authentication Failure

```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32700,
    "message": "Authentication failed: Invalid API key"
  },
  "id": null
}
```

### Missing API Key

```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32700,
    "message": "Authentication required: API key not provided"
  },
  "id": null
}
```

## CLI Integration

The `kubently-cli` automatically includes the API key in all A2A requests:

```python
# kubently-cli/kubently_pkg/a2a_chat.py
self.client = httpx.AsyncClient(
    headers={
        "X-API-Key": api_key,
        "Content-Type": "application/json",
    }
)
```

## Testing Authentication

Use the provided test script to verify authentication is working:

```bash
# Test with default settings
python test_a2a_auth.py

# Test with custom URL and key
python test_a2a_auth.py http://your-api:8080 your-api-key
```

The test script verifies:
1. Requests without API key are rejected (401)
2. Requests with invalid API key are rejected (401)
3. Requests with valid API key are processed (200)
4. Agent card endpoint doesn't require auth (200)

## Security Considerations

1. **API keys are validated** on every request - no session bypass
2. **Failed attempts are logged** for security monitoring
3. **Keys are stored in Redis** with encryption at rest
4. **Use HTTPS in production** to protect API keys in transit
5. **Rotate API keys regularly** for enhanced security

## Implementation Details

The authentication is implemented as FastAPI middleware in `/kubently/modules/a2a/__init__.py`:

```python
@self._app.middleware("http")
async def validate_api_key(request: Request, call_next):
    # Skip validation for agent card
    if request.url.path == "/" and request.method == "GET":
        return await call_next(request)
    
    # Extract and validate API key
    api_key = request.headers.get("x-api-key")
    if not api_key:
        return JSONResponse(status_code=401, ...)
    
    # Validate against Redis
    is_valid, service_identity = await auth_module.verify_api_key(api_key)
    if not is_valid:
        return JSONResponse(status_code=401, ...)
    
    # Proceed with authenticated request
    return await call_next(request)
```

## Monitoring

Authentication events are logged for monitoring:

- **Info**: Successful authentication with service identity
- **Warning**: Failed authentication attempts with partial key
- **Error**: Authentication system errors

Example logs:
```
INFO: A2A request authenticated for service: cli-user-1
WARNING: Invalid API key attempted for A2A access: test-key...
WARNING: A2A request to / without API key
```

## Troubleshooting

### "Authentication required" error

**Cause**: API key not provided in request headers

**Solution**: Ensure your client includes the `X-API-Key` header:
```bash
curl -X POST http://api:8080/ \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"message/send",...}'
```

### "Invalid API key" error

**Cause**: The provided API key doesn't exist in Redis

**Solution**: 
1. Verify the API key is correct
2. Check if the key exists: `redis-cli GET "api:key:your-key"`
3. Re-create the key if needed: `kubently init`

### Authentication bypassed

**Cause**: Should not happen - all endpoints except agent card are protected

**Action**: Check middleware is properly registered in `get_app()` method