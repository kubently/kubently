"""Configuration provider following Black Box Design principles."""
import os
from dataclasses import dataclass
from typing import Optional, Protocol, List


@dataclass
class OIDCConfig:
    """OIDC configuration."""
    enabled: bool
    issuer: Optional[str]
    client_id: str
    jwks_uri: Optional[str]
    token_endpoint: Optional[str]
    device_endpoint: Optional[str]
    audience: Optional[str]
    scopes: List[str]
    
    @property
    def is_configured(self) -> bool:
        """Check if OIDC is properly configured."""
        return self.enabled and bool(self.issuer)


@dataclass
class APIConfig:
    """API configuration."""
    port: int
    host: str
    debug: bool
    cors_origins: List[str]


@dataclass 
class AuthConfig:
    """Authentication configuration."""
    api_keys_enabled: bool
    oauth_enabled: bool
    require_auth: bool
    api_keys: List[str]


class ConfigProvider(Protocol):
    """Protocol for configuration providers."""
    
    def get_oidc_config(self) -> OIDCConfig:
        """Get OIDC configuration."""
        ...
    
    def get_api_config(self) -> APIConfig:
        """Get API configuration."""
        ...
    
    def get_auth_config(self) -> AuthConfig:
        """Get authentication configuration."""
        ...


class EnvConfigProvider:
    """Environment-based configuration provider."""
    
    def get_oidc_config(self) -> OIDCConfig:
        """Get OIDC configuration from environment variables."""
        enabled = os.getenv("OIDC_ENABLED", "false").lower() == "true"
        issuer = os.getenv("OIDC_ISSUER")
        client_id = os.getenv("OIDC_CLIENT_ID", "kubently-cli")
        
        return OIDCConfig(
            enabled=enabled,
            issuer=issuer,
            client_id=client_id,
            jwks_uri=os.getenv("OIDC_JWKS_URI") or (f"{issuer}/jwks" if issuer else None),
            token_endpoint=os.getenv("OIDC_TOKEN_ENDPOINT") or (f"{issuer}/token" if issuer else None),
            device_endpoint=os.getenv("OIDC_DEVICE_AUTH_ENDPOINT") or (f"{issuer}/device/code" if issuer else None),
            audience=os.getenv("OIDC_AUDIENCE") or client_id,
            scopes=os.getenv("OIDC_SCOPES", "openid email profile groups").split()
        )
    
    def get_api_config(self) -> APIConfig:
        """Get API configuration from environment variables."""
        return APIConfig(
            port=int(os.getenv("API_PORT", "8080")),
            host=os.getenv("API_HOST", "0.0.0.0"),
            debug=os.getenv("API_DEBUG", "false").lower() == "true",
            cors_origins=os.getenv("CORS_ORIGINS", "*").split(",")
        )
    
    def get_auth_config(self) -> AuthConfig:
        """Get authentication configuration from environment variables."""
        oauth_enabled = os.getenv("OIDC_ENABLED", "false").lower() == "true"

        # API keys are required - no default for security
        api_keys_env = os.getenv("API_KEYS")
        if not api_keys_env:
            raise ValueError(
                "API_KEYS environment variable is required. "
                "Set this via the kubently-api-keys secret (format: service:key,service:key). "
                "Example: admin:your-generated-key"
            )

        api_keys = api_keys_env.split(",")

        return AuthConfig(
            api_keys_enabled=True,  # Required for authentication
            oauth_enabled=oauth_enabled,
            require_auth=os.getenv("REQUIRE_AUTH", "true").lower() == "true",
            api_keys=[key.strip() for key in api_keys if key.strip()]
        )