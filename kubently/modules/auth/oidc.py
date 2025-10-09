"""
OIDC/JWT Authentication Support for Kubently

This module provides JWT token validation and OIDC integration
for user authentication alongside the existing API key authentication.
"""

import json
import logging
import os
import time
from datetime import UTC, datetime
from typing import Dict, Optional, Tuple
from urllib.parse import urljoin

import httpx
import jwt
from jwt import PyJWKClient

logger = logging.getLogger(__name__)


class OIDCValidator:
    """
    Validates JWT tokens from OIDC providers.
    
    This class handles:
    - JWKS key fetching and caching
    - JWT signature verification
    - Token claims validation
    - User information extraction
    """
    
    def __init__(
        self,
        issuer: Optional[str] = None,
        client_id: Optional[str] = None,
        jwks_uri: Optional[str] = None,
        audience: Optional[str] = None
    ):
        """
        Initialize OIDC validator.
        
        Args:
            issuer: OIDC issuer URL (e.g., https://auth.example.com)
            client_id: OAuth client ID for this application
            jwks_uri: JWKS endpoint URL (auto-discovered if not provided)
            audience: Expected audience claim (defaults to client_id)
        """
        self.issuer = issuer or os.environ.get("OIDC_ISSUER")
        self.client_id = client_id or os.environ.get("OIDC_CLIENT_ID", "kubently-cli")
        self.audience = audience or os.environ.get("OIDC_AUDIENCE", self.client_id)
        
        # Configure JWKS URI
        if jwks_uri:
            self.jwks_uri = jwks_uri
        elif os.environ.get("OIDC_JWKS_URI"):
            self.jwks_uri = os.environ.get("OIDC_JWKS_URI")
        elif self.issuer:
            # Auto-discover from issuer
            self.jwks_uri = urljoin(self.issuer, "/jwks")
        else:
            self.jwks_uri = None
        
        # Initialize JWKS client if we have a URI
        self.jwks_client = None
        if self.jwks_uri:
            try:
                self.jwks_client = PyJWKClient(
                    self.jwks_uri,
                    cache_keys=True,
                    lifespan=3600  # Cache keys for 1 hour
                )
            except Exception as e:
                logger.warning(f"Failed to initialize JWKS client: {e}")
        
        # Cache for user info to reduce token parsing
        self.user_cache: Dict[str, Tuple[dict, float]] = {}
        self.cache_ttl = 300  # 5 minutes
    
    async def discover_configuration(self) -> dict:
        """
        Discover OIDC configuration from provider.
        
        Returns:
            OpenID Connect discovery document
        """
        if not self.issuer:
            raise ValueError("Issuer URL not configured")
        
        discovery_url = urljoin(self.issuer, "/.well-known/openid-configuration")
        
        async with httpx.AsyncClient() as client:
            response = await client.get(discovery_url)
            response.raise_for_status()
            return response.json()
    
    def validate_jwt(self, token: str) -> Tuple[bool, Optional[dict]]:
        """
        Validate a JWT token synchronously.
        
        Args:
            token: JWT token string
            
        Returns:
            Tuple of (is_valid, claims_dict)
        """
        # Remove Bearer prefix if present
        if token.startswith("Bearer "):
            token = token[7:]
        
        # Check cache first
        if token in self.user_cache:
            claims, expires_at = self.user_cache[token]
            if time.time() < expires_at:
                return True, claims
            else:
                del self.user_cache[token]
        
        try:
            # If we have a JWKS client, use it for verification
            if self.jwks_client:
                # Get the signing key from JWKS
                signing_key = self.jwks_client.get_signing_key_from_jwt(token)
                
                # Decode and verify the token
                claims = jwt.decode(
                    token,
                    signing_key.key,
                    algorithms=["RS256", "RS384", "RS512"],
                    audience=self.audience,
                    issuer=self.issuer,
                    options={
                        "verify_signature": True,
                        "verify_aud": bool(self.audience),
                        "verify_iss": bool(self.issuer),
                        "verify_exp": True,
                        "require": ["exp", "iat", "sub"]
                    }
                )
            else:
                # No JWKS client - decode without verification (for testing only)
                logger.warning("JWKS not configured - decoding JWT without verification")
                claims = jwt.decode(
                    token,
                    options={"verify_signature": False},
                    audience=self.audience,
                    issuer=self.issuer
                )
            
            # Cache the valid claims
            self.user_cache[token] = (claims, time.time() + self.cache_ttl)
            
            return True, claims
            
        except jwt.ExpiredSignatureError:
            logger.debug("JWT token expired")
            return False, None
        except jwt.InvalidAudienceError:
            logger.debug(f"Invalid audience in JWT (expected {self.audience})")
            return False, None
        except jwt.InvalidIssuerError:
            logger.debug(f"Invalid issuer in JWT (expected {self.issuer})")
            return False, None
        except jwt.InvalidTokenError as e:
            logger.debug(f"Invalid JWT token: {e}")
            return False, None
        except Exception as e:
            logger.error(f"Unexpected error validating JWT: {e}")
            return False, None
    
    async def validate_jwt_async(self, token: str) -> Tuple[bool, Optional[dict]]:
        """
        Validate a JWT token asynchronously.
        
        This is a wrapper around validate_jwt for consistency with async code.
        
        Args:
            token: JWT token string
            
        Returns:
            Tuple of (is_valid, claims_dict)
        """
        return self.validate_jwt(token)
    
    def extract_user_info(self, claims: dict) -> dict:
        """
        Extract user information from JWT claims.
        
        Args:
            claims: JWT claims dictionary
            
        Returns:
            User information dictionary
        """
        return {
            "sub": claims.get("sub"),
            "email": claims.get("email"),
            "name": claims.get("name", claims.get("preferred_username")),
            "groups": claims.get("groups", []),
            "roles": claims.get("roles", []),
            "iss": claims.get("iss"),
            "aud": claims.get("aud"),
            "exp": claims.get("exp"),
            "iat": claims.get("iat")
        }
    
    def is_token_expired(self, claims: dict) -> bool:
        """
        Check if token is expired based on claims.
        
        Args:
            claims: JWT claims dictionary
            
        Returns:
            True if expired, False otherwise
        """
        exp = claims.get("exp")
        if not exp:
            return True
        
        return time.time() >= exp
    
    def get_token_remaining_time(self, claims: dict) -> int:
        """
        Get remaining valid time for token in seconds.
        
        Args:
            claims: JWT claims dictionary
            
        Returns:
            Seconds until expiration (0 if expired)
        """
        exp = claims.get("exp")
        if not exp:
            return 0
        
        remaining = exp - time.time()
        return max(0, int(remaining))


class EnhancedAuthModule:
    """
    Enhanced authentication module supporting both API keys and JWT tokens.
    
    This module extends the base AuthModule to support:
    - Legacy API key authentication (for machines/services)
    - JWT/OIDC authentication (for human users)
    - Unified authentication interface
    """
    
    def __init__(self, redis_client, base_auth_module):
        """
        Initialize enhanced auth module.
        
        Args:
            redis_client: Async Redis client
            base_auth_module: Existing AuthModule instance
        """
        self.redis = redis_client
        self.base_auth = base_auth_module
        self.oidc_validator = OIDCValidator()
        
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
            is_valid, claims = await self.oidc_validator.validate_jwt_async(bearer_token)
            if is_valid and claims:
                user_info = self.oidc_validator.extract_user_info(claims)
                identity = user_info.get("email", user_info.get("sub"))
                
                # Log JWT authentication
                await self._log_auth_event(
                    "jwt_authenticated",
                    {
                        "user": identity,
                        "sub": user_info.get("sub"),
                        "groups": user_info.get("groups", [])
                    }
                )
                
                self.auth_stats["jwt"] += 1
                return True, identity, "jwt"
        
        # Fall back to API key (machines/services)
        if api_key:
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
        await self.redis.lpush("auth:audit:enhanced", json.dumps(event))
        await self.redis.ltrim("auth:audit:enhanced", 0, 9999)
    
    async def get_auth_stats(self) -> dict:
        """Get authentication statistics."""
        return {
            "stats": self.auth_stats,
            "timestamp": datetime.now(UTC).isoformat()
        }