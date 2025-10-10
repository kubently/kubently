"""
Authentication Module - Black Box Interface

Purpose: Validate tokens and API keys
Interface: verify_executor(), verify_api_key(), create_executor_token()
Hidden: Token storage, validation logic, key formats

This module can be completely replaced with any other auth implementation
(OAuth, JWT, external service) without affecting other modules.
"""

from .auth import AuthModule

__all__ = ["AuthModule"]
