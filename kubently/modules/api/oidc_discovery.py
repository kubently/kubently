"""
OIDC Discovery Endpoint for Kubently API

This module provides an endpoint that clients can use to discover
the OIDC configuration for authentication.
"""

from typing import Dict
from fastapi import APIRouter
from kubently.config.provider import ConfigProvider


def create_discovery_router(config_provider: ConfigProvider) -> APIRouter:
    """
    Create OIDC discovery router with injected config provider.
    
    Args:
        config_provider: Configuration provider instance
        
    Returns:
        FastAPI router with discovery endpoints
    """
    router = APIRouter(tags=["discovery"])
    
    @router.get("/.well-known/kubently-auth")
    async def get_auth_config() -> Dict:
        """
        Get authentication configuration for clients.
        
        This endpoint tells clients:
        - Whether OAuth is enabled
        - OIDC provider details
        - Supported authentication methods
        
        Returns:
            Authentication configuration dictionary
        """
        # Get configurations from provider (no direct env access)
        auth_config = config_provider.get_auth_config()
        oidc_config = config_provider.get_oidc_config()
        
        # Build discovery response
        response = {
            "authentication_methods": [],
            "api_key": {
                "header": "X-API-Key",
                "description": "Static API key for service authentication"
            }
        }
        
        # Always support API keys
        if auth_config.api_keys_enabled:
            response["authentication_methods"].append("api_key")
        
        # Add OAuth if configured
        if oidc_config.is_configured and auth_config.oauth_enabled:
            response["authentication_methods"].append("oauth")
            response["oauth"] = {
                "enabled": True,
                "issuer": oidc_config.issuer,
                "client_id": oidc_config.client_id,
                "device_authorization_endpoint": oidc_config.device_endpoint,
                "token_endpoint": oidc_config.token_endpoint,
                "grant_types": ["urn:ietf:params:oauth:grant-type:device_code"],
                "response_types": ["token", "id_token"],
                "scopes": oidc_config.scopes
            }
        else:
            response["oauth"] = {
                "enabled": False,
                "message": "OAuth authentication is not configured for this instance"
            }
        
        return response
    
    @router.get("/auth/discovery")
    async def auth_discovery() -> Dict:
        """
        Alternative discovery endpoint.
        
        Some clients might look for this instead of .well-known.
        """
        return await get_auth_config()
    
    @router.get("/health/auth")
    async def auth_health() -> Dict:
        """
        Check authentication system health.
        
        Returns:
            Status of authentication systems
        """
        auth_config = config_provider.get_auth_config()
        oidc_config = config_provider.get_oidc_config()
        
        health = {
            "api_key": {
                "enabled": auth_config.api_keys_enabled,
                "healthy": True  # API keys are always available
            },
            "oauth": {
                "enabled": auth_config.oauth_enabled,
                "healthy": False
            }
        }
        
        # Check OAuth health if enabled
        if oidc_config.is_configured:
            health["oauth"]["healthy"] = True
            health["oauth"]["issuer"] = oidc_config.issuer
        
        return health
    
    return router