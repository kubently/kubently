"""
Dual Authentication Middleware

Supports both API key and JWT authentication in a single middleware.
"""

import logging
from typing import Callable, Dict, Optional, Tuple

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class DualAuthMiddleware:
    """
    Authentication middleware supporting both API keys and JWT tokens.
    
    This middleware checks for:
    1. JWT in Authorization: Bearer header (for human users)
    2. API key in X-API-Key header (for services/machines)
    """
    
    def __init__(
        self,
        enhanced_auth_module,
        skip_paths: Optional[Dict[str, list]] = None,
        error_format: str = "json",
        log_attempts: bool = True
    ):
        """
        Initialize dual authentication middleware.
        
        Args:
            enhanced_auth_module: EnhancedAuthModule instance with verify_credentials method
            skip_paths: Dict of {path: [methods]} to skip authentication
            error_format: Error response format ("json" or "jsonrpc")
            log_attempts: Whether to log authentication attempts
        """
        self.auth_module = enhanced_auth_module
        self.skip_paths = skip_paths or {}
        self.error_format = error_format
        self.log_attempts = log_attempts
    
    def should_skip_auth(self, request: Request) -> bool:
        """Check if authentication should be skipped for this request."""
        path = str(request.url.path)
        method = request.method.upper()
        
        if path in self.skip_paths:
            allowed_methods = self.skip_paths[path]
            if "*" in allowed_methods or method in allowed_methods:
                return True
        
        return False
    
    def extract_credentials(self, request: Request) -> Tuple[Optional[str], Optional[str]]:
        """
        Extract authentication credentials from request.
        
        Returns:
            Tuple of (api_key, bearer_token)
        """
        # Check for JWT in Authorization header
        auth_header = request.headers.get("Authorization", request.headers.get("authorization"))
        bearer_token = None
        if auth_header and auth_header.startswith("Bearer "):
            bearer_token = auth_header
        
        # Check for API key in X-API-Key header
        api_key = None
        for header_name in ["x-api-key", "X-API-Key", "X-Api-Key"]:
            api_key = request.headers.get(header_name)
            if api_key:
                break
        
        return api_key, bearer_token
    
    def format_error(self, status_code: int, message: str, request_id: Optional[str] = None) -> Dict:
        """Format error response based on configured format."""
        if self.error_format == "jsonrpc":
            return {
                "jsonrpc": "2.0",
                "error": {
                    "code": -32700 if status_code == 401 else -32603,
                    "message": message
                },
                "id": request_id
            }
        else:
            return {
                "error": message,
                "status": status_code
            }
    
    async def __call__(self, request: Request, call_next):
        """Process the request through dual authentication middleware."""
        # Check if we should skip authentication for this request
        if self.should_skip_auth(request):
            if self.log_attempts:
                logger.debug(f"Skipping auth for {request.method} {request.url.path}")
            return await call_next(request)
        
        # Extract credentials
        api_key, bearer_token = self.extract_credentials(request)
        
        # Check if any credentials were provided
        if not api_key and not bearer_token:
            if self.log_attempts:
                logger.warning(f"Request to {request.url.path} without credentials")
            
            return JSONResponse(
                status_code=401,
                content=self.format_error(
                    401,
                    "Authentication required: No API key or Bearer token provided"
                )
            )
        
        # Validate credentials
        try:
            is_valid, identity, auth_method = await self.auth_module.verify_credentials(
                api_key=api_key,
                bearer_token=bearer_token
            )
            
            if not is_valid:
                if self.log_attempts:
                    logger.warning(f"Invalid credentials attempted for {request.url.path}")
                
                return JSONResponse(
                    status_code=401,
                    content=self.format_error(401, "Authentication failed: Invalid credentials")
                )
            
            if self.log_attempts:
                logger.info(
                    f"Request authenticated via {auth_method} for identity: {identity}"
                )
            
            # Store authentication info for downstream use
            request.state.auth_identity = identity
            request.state.auth_method = auth_method
            
            # Get and store permissions
            permissions = await self.auth_module.get_user_permissions(identity, auth_method)
            request.state.permissions = permissions
            
        except Exception as e:
            logger.error(f"Error during authentication: {e}")
            return JSONResponse(
                status_code=500,
                content=self.format_error(500, "Internal error during authentication")
            )
        
        # Proceed with authenticated request
        response = await call_next(request)
        return response


def create_dual_auth_middleware(
    enhanced_auth_module,
    skip_paths: Optional[Dict[str, list]] = None,
    error_format: str = "json"
) -> DualAuthMiddleware:
    """
    Factory function to create dual authentication middleware.
    
    Args:
        enhanced_auth_module: EnhancedAuthModule instance
        skip_paths: Paths to skip authentication {"/path": ["GET", "POST"]}
        error_format: "json" or "jsonrpc" error format
    
    Returns:
        Configured DualAuthMiddleware instance
    """
    # Add common paths to skip
    default_skip_paths = {
        "/health": ["GET"],
        "/metrics": ["GET"],
        "/.well-known/openid-configuration": ["GET"],
        "/jwks": ["GET"],
        "/device": ["GET", "POST"],
        "/device/code": ["POST"],
        "/device/approve": ["POST"],
        "/token": ["POST"],
    }
    
    if skip_paths:
        default_skip_paths.update(skip_paths)
    
    return DualAuthMiddleware(
        enhanced_auth_module=enhanced_auth_module,
        skip_paths=default_skip_paths,
        error_format=error_format
    )