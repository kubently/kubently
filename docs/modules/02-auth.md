# Module: Authentication

## Black Box Interface

**Purpose**: Validate authentication credentials for agents and API clients

**What this module does** (Public Interface):
- Verifies if credentials are valid
- Extracts service identity from API keys
- Creates/revokes agent tokens

**What this module hides** (Implementation):
- How tokens are stored (Redis, database, memory)
- Token format and encoding
- Validation algorithms
- Rate limiting logic
- Audit log storage

## Overview
The Authentication module is a black box that validates credentials. It can be completely replaced with any auth system (OAuth, JWT, LDAP, etc.) without affecting other modules.

## Dependencies
- Redis client (provided by API Core)
- Python 3.13+ standard library (secrets, hashlib)
- No external auth libraries (keep it simple)

## Interfaces

### Public Methods

```python
class AuthModule:
    async def verify_agent(self, token: str, cluster_id: str) -> bool:
        """Verify agent authentication token"""
        
    async def verify_api_key(self, api_key: str) -> tuple[bool, Optional[str]]:
        """
        Verify AI/User/A2A Service API key.
        Returns (is_valid, service_identity)
        """
        
    async def create_agent_token(self, cluster_id: str) -> str:
        """Generate new agent token for cluster"""
        
    async def revoke_agent_token(self, cluster_id: str) -> None:
        """Revoke agent token"""
        
    async def extract_service_identity(self, api_key: str) -> Optional[str]:
        """Extract service identity from API key if present"""
```

## Implementation Requirements

### File Structure
```text
kubently/api/
└── auth.py
```

### Implementation (`auth.py`)

```python
import os
import secrets
import hashlib
from typing import Optional, Set, Dict, Tuple
from datetime import datetime, timedelta

class AuthModule:
    def __init__(self, redis_client):
        """
        Initialize auth module.
        
        Args:
            redis_client: Async Redis client from API Core
        """
        self.redis = redis_client
        
        # Load API keys from environment with optional service identity
        # Format: API_KEYS="key1,service1:key2,service2:key3"
        # Examples: "abc123,orchestrator:def456,monitoring:ghi789"
        self.api_keys: Dict[str, Optional[str]] = self._load_api_keys()
        
        # Load agent tokens from environment (fallback for Redis)
        # Format: AGENT_TOKEN_<CLUSTER_ID>="token"
        self.static_agent_tokens = self._load_static_tokens()
    
    def _load_api_keys(self) -> Dict[str, Optional[str]]:
        """Load API keys with optional service identities"""
        keys = {}
        api_keys_env = os.environ.get("API_KEYS", "")
        
        for entry in api_keys_env.split(","):
            entry = entry.strip()
            if not entry:
                continue
                
            # Check for service:key format
            if ":" in entry:
                service, key = entry.split(":", 1)
                keys[key] = service
            else:
                # Plain key without service identity
                keys[entry] = None
                
        return keys
    
    def _load_static_tokens(self) -> dict:
        """Load static agent tokens from environment variables"""
        tokens = {}
        for key, value in os.environ.items():
            if key.startswith("AGENT_TOKEN_"):
                cluster_id = key[12:].lower().replace("_", "-")
                tokens[cluster_id] = value
        return tokens
    
    async def verify_agent(self, auth_header: str, cluster_id: str) -> bool:
        """
        Verify agent authentication.
        
        Args:
            auth_header: Authorization header value (e.g., "Bearer token123")
            cluster_id: Cluster identifier
            
        Returns:
            True if valid, False otherwise
            
        Logic:
        1. Extract token from Bearer header
        2. Check Redis for dynamic token
        3. Fall back to environment variable
        4. Constant-time comparison
        """
        # Extract token from "Bearer <token>"
        if not auth_header.startswith("Bearer "):
            return False
        
        provided_token = auth_header[7:]
        
        # Check Redis first (dynamic tokens)
        redis_key = f"agent:token:{cluster_id}"
        stored_token = await self.redis.get(redis_key)
        
        if stored_token:
            # Use constant-time comparison
            return secrets.compare_digest(provided_token, stored_token)
        
        # Fall back to static tokens from environment
        if cluster_id in self.static_agent_tokens:
            return secrets.compare_digest(
                provided_token, 
                self.static_agent_tokens[cluster_id]
            )
        
        return False
    
    async def verify_api_key(self, api_key: str) -> Tuple[bool, Optional[str]]:
        """
        Verify AI/User/A2A Service API key.
        
        Args:
            api_key: API key from X-API-Key header
            
        Returns:
            Tuple of (is_valid, service_identity)
            
        Logic:
        1. Check if key exists in loaded dict
        2. Return service identity if present
        3. Could be extended to check Redis for dynamic keys
        4. Could add rate limiting per key and service
        """
        if not api_key:
            return False, None
        
        # Check if key exists and return associated service identity
        if api_key in self.api_keys:
            service_identity = self.api_keys[api_key]
            return True, service_identity
        
        # Could check Redis for dynamic keys here
        # redis_key = f"api:key:{api_key}"
        # stored_data = await self.redis.get(redis_key)
        
        return False, None
    
    async def extract_service_identity(self, api_key: str) -> Optional[str]:
        """
        Extract service identity from API key if present.
        
        Args:
            api_key: API key to check
            
        Returns:
            Service identity string or None
        """
        if api_key in self.api_keys:
            return self.api_keys[api_key]
        return None
    
    async def create_agent_token(self, cluster_id: str) -> str:
        """
        Generate new agent token for cluster.
        
        Args:
            cluster_id: Cluster identifier
            
        Returns:
            Generated token
            
        Logic:
        1. Generate cryptographically secure token
        2. Store in Redis with no expiration
        3. Return token for agent configuration
        """
        # Generate secure token (32 bytes = 256 bits)
        token = secrets.token_urlsafe(32)
        
        # Store in Redis
        redis_key = f"agent:token:{cluster_id}"
        await self.redis.set(redis_key, token)
        
        # Log token creation (audit)
        await self._log_event("agent_token_created", {
            "cluster_id": cluster_id,
            "timestamp": datetime.utcnow().isoformat()
        })
        
        return token
    
    async def revoke_agent_token(self, cluster_id: str) -> None:
        """
        Revoke agent token.
        
        Args:
            cluster_id: Cluster identifier
            
        Logic:
        1. Delete from Redis
        2. Agent will be unable to authenticate
        3. Log revocation for audit
        """
        redis_key = f"agent:token:{cluster_id}"
        await self.redis.delete(redis_key)
        
        await self._log_event("agent_token_revoked", {
            "cluster_id": cluster_id,
            "timestamp": datetime.utcnow().isoformat()
        })
    
    async def _log_event(self, event_type: str, data: dict, correlation_id: Optional[str] = None):
        """Log security event for audit with optional correlation ID"""
        # Store in Redis list for audit trail
        event = {
            "type": event_type,
            "data": data,
            "correlation_id": correlation_id
        }
        await self.redis.lpush("auth:audit", str(event))
        # Keep last 10000 events
        await self.redis.ltrim("auth:audit", 0, 9999)
```

## Configuration

Environment variables:
- `API_KEYS`: Comma-separated list of API keys with optional service identity
  - Plain key: `key123`
  - With service identity: `service-name:key123`
  - Example: `API_KEYS="key1,orchestrator:key2,monitoring-agent:key3"`
- `AGENT_TOKEN_<CLUSTER_ID>`: Static token for specific cluster

Redis keys used:
- `agent:token:{cluster_id}`: Dynamic agent tokens
- `auth:audit`: Security event audit log with correlation IDs

## Security Requirements

1. **Constant-time comparison**: Use `secrets.compare_digest()` for all token comparisons
2. **Secure token generation**: Use `secrets.token_urlsafe()` with sufficient entropy
3. **No token logging**: Never log actual token values
4. **Audit trail**: Log all token operations for security audit
5. **Token rotation**: Support token rotation without downtime

## Testing Requirements

### Unit Tests
```python
async def test_verify_agent_valid_token():
    # Test with valid token from Redis
    
async def test_verify_agent_static_token():
    # Test fallback to environment variable
    
async def test_verify_agent_invalid_token():
    # Test with invalid token
    
async def test_verify_api_key():
    # Test API key validation with and without service identity
    
async def test_verify_api_key_with_service():
    # Test API key with service identity returns correct tuple
    
async def test_extract_service_identity():
    # Test service identity extraction from API keys
    
async def test_create_and_revoke_token():
    # Test token lifecycle
    
async def test_audit_log_with_correlation_id():
    # Test audit logging includes correlation IDs
```

## Error Handling

- Redis connection failures: Return False (deny access)
- Missing environment variables: Use empty defaults
- Invalid header format: Return False
- All errors should deny access (fail closed)

## Performance Considerations

- Cache API keys in memory (already done)
- Use Redis pipelining for multiple checks
- Consider adding TTL cache for recent validations

## Deliverables

1. `auth.py` with AuthModule implementation
2. Unit tests in `tests/test_auth.py`
3. Documentation of environment variables
4. Security audit checklist

## Development Notes

- Start with static tokens for easy testing
- Add dynamic tokens once Redis is working
- Keep authentication logic simple and auditable
- Fail closed - deny access on any error
- Consider adding rate limiting per token/key and per service identity
- Service identity enables fine-grained access control and audit trails
- Correlation IDs essential for tracing requests across multi-agent systems
