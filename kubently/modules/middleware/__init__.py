"""
Authentication Middleware Module - Black Box Interface

Purpose: Provide reusable authentication middleware for FastAPI applications
Interface: Middleware factory functions that return configured middleware
Hidden: Authentication logic, header extraction, error formatting

Can be used by any FastAPI app or sub-app that needs authentication.
Completely independent and replaceable.
"""

import logging
from typing import Callable, Optional, Tuple, Dict, Any
from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class AuthMiddleware:
    """
    Configurable authentication middleware for FastAPI applications.
    
    This is a black box that handles authentication for any FastAPI app.
    Simply provide an auth validator function and optional configuration.
    """
    
    def __init__(
        self,
        auth_validator: Callable[[str], Tuple[bool, Optional[str]]],
        header_names: Optional[list] = None,
        skip_paths: Optional[Dict[str, list]] = None,
        error_format: str = "json",
        log_attempts: bool = True
    ):
        """
        Initialize authentication middleware.
        
        Args:
            auth_validator: Async function that validates API key, returns (is_valid, identity)
            header_names: List of header names to check for API key (default: X-API-Key variants)
            skip_paths: Dict of {path: [methods]} to skip authentication
            error_format: Error response format ("json" or "jsonrpc")
            log_attempts: Whether to log authentication attempts
        """
        self.auth_validator = auth_validator
        self.header_names = header_names or ["x-api-key", "X-API-Key", "X-Api-Key"]
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
    
    def extract_api_key(self, request: Request) -> Optional[str]:
        """Extract API key from request headers."""
        for header_name in self.header_names:
            api_key = request.headers.get(header_name)
            if api_key:
                return api_key
        return None
    
    def format_error(self, status_code: int, message: str, request_id: Optional[str] = None) -> Dict[str, Any]:
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
        """Process the request through authentication middleware."""
        # Check if we should skip authentication for this request
        if self.should_skip_auth(request):
            if self.log_attempts:
                logger.debug(f"Skipping auth for {request.method} {request.url.path}")
            return await call_next(request)

        # Skip authentication for internal service-to-service calls (localhost/127.0.0.1)
        client_host = request.client.host if request.client else None
        if client_host in ("127.0.0.1", "localhost", "::1"):
            if self.log_attempts:
                logger.debug(f"Skipping auth for internal request from {client_host}")
            return await call_next(request)
        
        # Extract API key
        api_key = self.extract_api_key(request)
        
        if not api_key:
            if self.log_attempts:
                logger.warning(f"Request to {request.url.path} without API key")
            
            return JSONResponse(
                status_code=401,
                content=self.format_error(401, "Authentication required: API key not provided")
            )
        
        # Validate API key
        try:
            is_valid, service_identity = await self.auth_validator(api_key)
            
            if not is_valid:
                if self.log_attempts:
                    logger.warning(f"Invalid API key attempted: {api_key[:8]}...")
                
                return JSONResponse(
                    status_code=401,
                    content=self.format_error(401, "Authentication failed: Invalid API key")
                )
            
            if self.log_attempts:
                logger.info(f"Request authenticated for service: {service_identity}")
            
            # Store service identity for downstream use
            request.state.service_identity = service_identity
            
        except Exception as e:
            logger.error(f"Error during authentication: {e}")
            return JSONResponse(
                status_code=500,
                content=self.format_error(500, "Internal error during authentication")
            )
        
        # Proceed with authenticated request
        response = await call_next(request)
        return response


def create_api_key_middleware(
    auth_module,
    skip_paths: Optional[Dict[str, list]] = None,
    error_format: str = "json"
) -> AuthMiddleware:
    """
    Factory function to create API key authentication middleware.
    
    Args:
        auth_module: AuthModule instance with verify_api_key method
        skip_paths: Paths to skip authentication {"/path": ["GET", "POST"]}
        error_format: "json" or "jsonrpc" error format
    
    Returns:
        Configured AuthMiddleware instance
    """
    async def validator(api_key: str) -> Tuple[bool, Optional[str]]:
        """Validate API key using auth module."""
        return await auth_module.verify_api_key(api_key)
    
    return AuthMiddleware(
        auth_validator=validator,
        skip_paths=skip_paths,
        error_format=error_format
    )


def create_bearer_token_middleware(
    auth_module,
    skip_paths: Optional[Dict[str, list]] = None,
    error_format: str = "json"
) -> AuthMiddleware:
    """
    Factory function to create Bearer token authentication middleware.
    
    Args:
        auth_module: AuthModule instance with verify_executor_token method
        skip_paths: Paths to skip authentication
        error_format: "json" or "jsonrpc" error format
    
    Returns:
        Configured AuthMiddleware instance
    """
    async def validator(token: str) -> Tuple[bool, Optional[str]]:
        """Validate Bearer token using auth module."""
        # Remove "Bearer " prefix if present
        if token.startswith("Bearer "):
            token = token[7:]
        return await auth_module.verify_executor_token(token)
    
    return AuthMiddleware(
        auth_validator=validator,
        header_names=["authorization", "Authorization"],
        skip_paths=skip_paths,
        error_format=error_format
    )


# Module interface - what this module provides
__all__ = [
    "AuthMiddleware",
    "create_api_key_middleware",
    "create_bearer_token_middleware"
]