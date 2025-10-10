"""Authentication interfaces following Black Box Design principles."""
from typing import Protocol, Optional, Tuple, Dict, Any
from dataclasses import dataclass


class TokenValidator(Protocol):
    """Protocol for token validation - allows swappable implementations."""
    
    async def validate_jwt_async(self, token: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
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
        api_key: Optional[str] = None,
        authorization: Optional[str] = None
    ) -> Tuple[bool, Optional[str], Optional[str]]:
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