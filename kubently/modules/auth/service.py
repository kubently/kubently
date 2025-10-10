"""
Authentication Service Facade following Black Box Design principles.

This module provides:
- A clean interface for authentication that hides implementation details
- Standardized authentication results
- Protocol definitions for swappable implementations
"""

from dataclasses import dataclass
from typing import Optional, Literal, Protocol, Any


@dataclass
class AuthResult:
    """Standardized authentication result."""
    ok: bool
    identity: Optional[str]
    method: Optional[Literal["api_key", "jwt"]]
    error: Optional[str] = None
    claims: Optional[dict] = None
    permissions: Optional[dict] = None


class AuthenticationService(Protocol):
    """Protocol for authentication services."""
    
    async def authenticate(
        self, 
        api_key: Optional[str], 
        authorization: Optional[str]
    ) -> AuthResult:
        """
        Authenticate a request.
        
        Args:
            api_key: API key from X-API-Key header
            authorization: Authorization header value
            
        Returns:
            AuthResult with authentication status and details
        """
        ...


class DefaultAuthenticationService:
    """
    Default implementation of AuthenticationService.
    
    This facade hides the complexity of the underlying auth module
    and provides a clean, stable interface for the API layer.
    """
    
    def __init__(self, auth_module: Any):
        """
        Initialize with any auth module that has verify_credentials.
        
        Args:
            auth_module: Module with verify_credentials method
        """
        self._auth = auth_module
    
    async def authenticate(
        self,
        api_key: Optional[str],
        authorization: Optional[str]
    ) -> AuthResult:
        """
        Authenticate a request using the underlying auth module.
        
        Args:
            api_key: API key from X-API-Key header
            authorization: Authorization header value
            
        Returns:
            AuthResult with authentication status and details
        """
        # Extract bearer token from authorization header if present
        bearer_token = None
        if authorization and authorization.startswith("Bearer "):
            bearer_token = authorization
        
        # Call the underlying auth module
        ok, identity, method = await self._auth.verify_credentials(
            api_key=api_key,
            bearer_token=bearer_token
        )
        
        # Build standardized result
        if ok:
            # Get permissions if available
            permissions = None
            if hasattr(self._auth, 'get_user_permissions'):
                permissions = await self._auth.get_user_permissions(identity, method)
            
            return AuthResult(
                ok=True,
                identity=identity,
                method=method,
                error=None,
                permissions=permissions
            )
        else:
            return AuthResult(
                ok=False,
                identity=None,
                method=None,
                error="Invalid credentials"
            )