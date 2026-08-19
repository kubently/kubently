"""Authentication interfaces following Black Box Design principles."""
from dataclasses import dataclass
from typing import Any, Protocol


class TokenValidator(Protocol):
    """Protocol for token validation - allows swappable implementations."""

    async def validate_jwt_async(self, token: str) -> tuple[bool, dict[str, Any] | None]:
        """
        Validate a JWT token.
        
        Args:
            token: JWT token string
            
        Returns:
            Tuple of (is_valid, claims_dict or None)
        """
        ...


class AuthModule(Protocol):
    """Protocol for authentication modules."""

    async def verify_credentials(
        self,
        api_key: str | None = None,
        authorization: str | None = None
    ) -> tuple[bool, str | None, str | None]:
        """
        Verify credentials.
        
        Returns:
            Tuple of (authenticated, user_identity, auth_method)
        """
        ...


@dataclass
class AuthConfig:
    """Authentication configuration."""
    api_keys_enabled: bool = True
    oauth_enabled: bool = False
    require_auth: bool = True
