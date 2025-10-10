"""
Enhanced authentication module with dependency injection.

This module follows Black Box Design principles:
- Accepts dependencies via constructor injection
- Does not create its own dependencies
- Implements a clear interface
"""

import json
import logging
from datetime import UTC, datetime
from typing import Optional, Tuple

from .interfaces import TokenValidator, AuthModule as AuthModuleProtocol

logger = logging.getLogger(__name__)


class EnhancedAuthModule:
    """
    Enhanced authentication supporting both API keys and JWT tokens.
    
    This is a black box that:
    - Accepts any TokenValidator implementation
    - Accepts any AuthModule implementation for API keys
    - Provides unified authentication interface
    """
    
    def __init__(
        self, 
        redis_client,
        base_auth_module: AuthModuleProtocol,
        token_validator: TokenValidator
    ):
        """
        Initialize with injected dependencies.
        
        Args:
            redis_client: Async Redis client for audit logging
            base_auth_module: Module for API key authentication
            token_validator: Module for JWT token validation
        """
        self.redis = redis_client
        self.base_auth = base_auth_module
        self.token_validator = token_validator
        
        # Track authentication methods for metrics
        self.auth_stats = {
            "api_key": 0,
            "jwt": 0,
            "failed": 0
        }
    
    async def verify_credentials(
        self,
        api_key: Optional[str] = None,
        bearer_token: Optional[str] = None
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Verify either API key or JWT bearer token.
        
        Args:
            api_key: API key from X-API-Key header
            bearer_token: JWT from Authorization: Bearer header
            
        Returns:
            Tuple of (is_valid, identity, auth_method)
            - identity: service name for API key, user email for JWT
            - auth_method: "api_key" or "jwt"
        """
        # Try JWT first (human users)
        if bearer_token:
            is_valid, claims = await self.token_validator.validate_jwt_async(bearer_token)
            if is_valid and claims:
                # Extract user identity from claims
                identity = claims.get("email", claims.get("sub"))
                
                # Log JWT authentication
                await self._log_auth_event(
                    "jwt_authenticated",
                    {
                        "user": identity,
                        "sub": claims.get("sub"),
                        "groups": claims.get("groups", [])
                    }
                )
                
                self.auth_stats["jwt"] += 1
                return True, identity, "jwt"
        
        # Fall back to API key (machines/services)
        if api_key:
            # Use the base auth module's verification
            is_valid, service_identity = await self.base_auth.verify_api_key(api_key)
            
            if is_valid:
                self.auth_stats["api_key"] += 1
                return True, service_identity, "api_key"
        
        # Authentication failed
        self.auth_stats["failed"] += 1
        return False, None, None
    
    async def get_user_permissions(self, identity: str, auth_method: str) -> dict:
        """
        Get permissions for authenticated user/service.
        
        Args:
            identity: User email or service name
            auth_method: "api_key" or "jwt"
            
        Returns:
            Permissions dictionary
        """
        # For now, return default permissions
        # This can be extended with RBAC later
        if auth_method == "jwt":
            # Human user permissions
            return {
                "clusters": ["*"],  # Access to all clusters for now
                "operations": ["debug", "execute", "status"],
                "admin": False
            }
        else:
            # Service/machine permissions
            return {
                "clusters": ["*"],
                "operations": ["*"],
                "admin": True
            }
    
    async def _log_auth_event(self, event_type: str, data: dict):
        """Log authentication event for audit."""
        event = {
            "type": event_type,
            "data": data,
            "timestamp": datetime.now(UTC).isoformat()
        }
        
        # Store in Redis for audit trail
        if self.redis:
            await self.redis.lpush("auth:audit:enhanced", json.dumps(event))
            await self.redis.ltrim("auth:audit:enhanced", 0, 9999)
    
    async def get_auth_stats(self) -> dict:
        """Get authentication statistics."""
        return {
            "stats": self.auth_stats,
            "timestamp": datetime.now(UTC).isoformat()
        }