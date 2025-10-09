# Middleware Module

## Purpose

Provide reusable authentication middleware for FastAPI applications following black box principles.

## Interface

### AuthMiddleware Class

Main middleware class that can be configured for different authentication schemes:

```python
from kubently.modules.middleware import AuthMiddleware

# Create custom middleware
middleware = AuthMiddleware(
    auth_validator=your_validator_function,
    header_names=["x-api-key"],
    skip_paths={"/health": ["GET"]},
    error_format="json",
    log_attempts=True
)
```

### Factory Functions

#### create_api_key_middleware

Creates middleware for API key authentication:

```python
from kubently.modules.middleware import create_api_key_middleware

middleware = create_api_key_middleware(
    auth_module=auth_module,
    skip_paths={"/": ["GET"]},
    error_format="jsonrpc"
)
```

#### create_bearer_token_middleware

Creates middleware for Bearer token authentication:

```python
from kubently.modules.middleware import create_bearer_token_middleware

middleware = create_bearer_token_middleware(
    auth_module=auth_module,
    skip_paths={"/health": ["GET", "HEAD"]},
    error_format="json"
)
```

## Usage Example

```python
from fastapi import FastAPI
from kubently.modules.auth import AuthModule
from kubently.modules.middleware import create_api_key_middleware

app = FastAPI()
auth_module = AuthModule(redis_client)

# Create and register middleware
auth_middleware = create_api_key_middleware(
    auth_module=auth_module,
    skip_paths={"/docs": ["GET"], "/health": ["GET"]}
)

@app.middleware("http")
async def add_authentication(request, call_next):
    return await auth_middleware(request, call_next)
```

## Configuration Options

### auth_validator
- Type: `Callable[[str], Tuple[bool, Optional[str]]]`
- Async function that validates credentials
- Returns: (is_valid, service_identity)

### header_names
- Type: `list[str]`
- Headers to check for authentication credentials
- Default: `["x-api-key", "X-API-Key", "X-Api-Key"]`

### skip_paths
- Type: `Dict[str, list[str]]`
- Paths and methods to skip authentication
- Example: `{"/health": ["GET"], "/": ["*"]}`

### error_format
- Type: `str`
- Format for error responses
- Options: `"json"` or `"jsonrpc"`
- Default: `"json"`

### log_attempts
- Type: `bool`
- Whether to log authentication attempts
- Default: `True`

## Error Responses

### JSON Format

```json
{
  "error": "Authentication required: API key not provided",
  "status": 401
}
```

### JSON-RPC Format

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

## Features

- **Configurable header names** - Support different authentication headers
- **Path exclusions** - Skip auth for specific paths/methods
- **Multiple error formats** - JSON or JSON-RPC responses
- **Logging control** - Optional authentication attempt logging
- **Service identity tracking** - Store authenticated identity in request state
- **Async validation** - Support for async authentication validators

## Black Box Design

This module follows black box principles:

1. **Hidden Implementation** - Authentication logic is encapsulated
2. **Clean Interface** - Simple factory functions and configuration
3. **Replaceable** - Can swap auth providers without changing usage
4. **Single Responsibility** - Only handles authentication middleware
5. **No External Dependencies** - Only depends on FastAPI and auth module interface

## Testing

```python
# Test without authentication
response = client.get("/health")  # Should succeed if /health is in skip_paths

# Test with invalid key
response = client.post("/api/data", headers={"X-API-Key": "invalid"})
assert response.status_code == 401

# Test with valid key
response = client.post("/api/data", headers={"X-API-Key": "valid-key"})
assert response.status_code == 200
```