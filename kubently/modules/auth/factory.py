"""
Authentication Factory following Black Box Design principles.

This factory:
- Constructs the authentication stack based on configuration
- Wires dependencies together
- Returns only the service facade (hiding implementation)
"""

import logging
from typing import Optional, Any

from .auth import AuthModule
from .enhanced import EnhancedAuthModule
from .oidc_validator import OIDCValidator
from .service import DefaultAuthenticationService, AuthenticationService
from ...config.provider import ConfigProvider

logger = logging.getLogger(__name__)


class AuthFactory:
    """
    Factory for building the authentication stack.
    
    This is the composition root that:
    - Creates all auth components
    - Wires them together via dependency injection
    - Returns only the public interface
    """
    
    @staticmethod
    def build(
        config_provider: ConfigProvider,
        redis_client: Optional[Any] = None
    ) -> AuthenticationService:
        """
        Build the complete authentication stack.
        
        Args:
            config_provider: Configuration provider
            redis_client: Optional Redis client for audit logging
            
        Returns:
            AuthenticationService facade (hides all implementation details)
        """
        # Get configurations
        auth_config = config_provider.get_auth_config()
        oidc_config = config_provider.get_oidc_config()
        
        # Create base API key auth module
        base_auth = AuthModule(redis_client)
        
        # Determine which auth module to use
        if oidc_config.is_configured and auth_config.oauth_enabled:
            logger.info("Building authentication stack with OAuth support")
            
            # Create token validator
            token_validator = OIDCValidator(oidc_config)
            
            # Create enhanced module with dependency injection
            auth_module = EnhancedAuthModule(
                redis_client=redis_client,
                base_auth_module=base_auth,
                token_validator=token_validator
            )
        else:
            logger.info("Building authentication stack with API keys only")
            # Use basic auth module only
            auth_module = base_auth
        
        # Wrap in service facade
        return DefaultAuthenticationService(auth_module)
    
    @staticmethod
    def build_for_testing(
        mock_validator: Optional[Any] = None,
        mock_auth_module: Optional[Any] = None
    ) -> AuthenticationService:
        """
        Build auth stack for testing with mock dependencies.
        
        Args:
            mock_validator: Mock token validator
            mock_auth_module: Mock auth module
            
        Returns:
            AuthenticationService for testing
        """
        if mock_auth_module:
            return DefaultAuthenticationService(mock_auth_module)
        
        # Create test configuration
        from ...config.provider import EnvConfigProvider
        config_provider = EnvConfigProvider()
        
        # Build with optional mock validator
        if mock_validator:
            base_auth = AuthModule(redis_client)
            auth_module = EnhancedAuthModule(
                redis_client=None,
                base_auth_module=base_auth,
                token_validator=mock_validator
            )
            return DefaultAuthenticationService(auth_module)
        
        # Build normal stack for integration testing
        return AuthFactory.build(config_provider)