"""
OIDC Token Validator implementing TokenValidator interface.

This module follows Black Box Design principles:
- Implements TokenValidator protocol
- Accepts configuration via dependency injection
- No direct environment variable access
"""

import json
import logging
import time
from typing import Dict, Optional, Tuple, Any
from urllib.parse import urljoin

import httpx
import jwt
from jwt import PyJWKClient

from .interfaces import TokenValidator
from ...config.provider import OIDCConfig

logger = logging.getLogger(__name__)


class OIDCValidator(TokenValidator):
    """
    Validates JWT tokens from OIDC providers.
    
    This class is a black box that:
    - Validates JWT signatures via JWKS
    - Verifies token claims
    - Caches validation results
    """
    
    def __init__(self, config: OIDCConfig):
        """
        Initialize OIDC validator with injected config.
        
        Args:
            config: OIDC configuration object
        """
        self.config = config
        self.issuer = config.issuer
        self.client_id = config.client_id
        self.audience = config.audience
        self.jwks_uri = config.jwks_uri
        
        # Initialize JWKS client if configured
        self.jwks_client = None
        if self.jwks_uri and config.is_configured:
            try:
                self.jwks_client = PyJWKClient(
                    self.jwks_uri,
                    cache_keys=True,
                    lifespan=3600  # Cache keys for 1 hour
                )
            except Exception as e:
                logger.warning(f"Failed to initialize JWKS client: {e}")
        
        # Cache for validation results
        self.cache: Dict[str, Tuple[Dict[str, Any], float]] = {}
        self.cache_ttl = 300  # 5 minutes
    
    async def validate_jwt_async(self, token: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Validate a JWT token asynchronously.
        
        Args:
            token: JWT token string (with or without Bearer prefix)
            
        Returns:
            Tuple of (is_valid, claims_dict or None)
        """
        # Remove Bearer prefix if present
        if token.startswith("Bearer "):
            token = token[7:]
        
        # Check cache first
        if token in self.cache:
            claims, expires_at = self.cache[token]
            if time.time() < expires_at:
                return True, claims
            else:
                del self.cache[token]
        
        try:
            # If OIDC is not configured, reject all tokens
            if not self.config.is_configured:
                logger.debug("OIDC not configured - rejecting token")
                return False, None
            
            # JWKS client is required for secure JWT validation
            if not self.jwks_client:
                logger.error("JWKS client not initialized - cannot verify JWT signatures")
                return False, None

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
            
            # Cache the valid claims
            self.cache[token] = (claims, time.time() + self.cache_ttl)
            
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
    
    def extract_user_info(self, claims: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract standardized user information from JWT claims.
        
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