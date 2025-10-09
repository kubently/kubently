"""
Authentication module for Kubently API.

This module handles all authentication for executors and API clients.
It's designed as a black box that can be replaced with any auth system
without affecting other modules.
"""

import hashlib
import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Dict, Optional, Tuple


class AuthModule:
    """
    Authentication module for validating credentials.

    This module verifies executor tokens and API keys, manages token lifecycle,
    and provides service identity extraction for A2A communication.
    """

    @staticmethod
    def extract_first_api_key(api_keys_env: Optional[str] = None) -> str:
        """
        Extract the first API key from environment variable, handling service:key format.

        This is a utility for internal service-to-service communication where
        components need to authenticate to the API using the configured keys.

        Args:
            api_keys_env: API_KEYS environment variable value. If None, reads from os.environ.

        Returns:
            First API key (without service prefix)

        Raises:
            ValueError: If API_KEYS is not configured

        Example:
            >>> os.environ["API_KEYS"] = "service1:key123,key456"
            >>> AuthModule.extract_first_api_key()
            'key123'
        """
        if api_keys_env is None:
            api_keys_env = os.environ.get("API_KEYS")

        if not api_keys_env:
            raise ValueError(
                "API_KEYS environment variable is required for internal service operations. "
                "Set this to the same value as the kubently-api-keys secret (format: key or service:key)."
            )

        # Get first entry
        first_entry = api_keys_env.split(",")[0].strip()

        if not first_entry:
            raise ValueError("API_KEYS environment variable is empty")

        # Handle service:key format - extract just the key
        if ":" in first_entry:
            _, api_key = first_entry.split(":", 1)
            return api_key.strip()

        return first_entry.strip()

    def __init__(self, redis_client):
        """
        Initialize auth module.

        Args:
            redis_client: Async Redis client from API Core
        """
        self.redis = redis_client

        # Load API keys from environment with optional service identity
        # Format: API_KEYS="key1,service1:key2,service2:key3"
        # Examples: "abc123,orchestrator:def456,monitoring:ghi789"
        self.api_keys: Dict[str, Optional[str]] = self._load_api_keys()

    def _load_api_keys(self) -> Dict[str, Optional[str]]:
        """Load API keys with optional service identities from environment."""
        keys = {}
        api_keys_env = os.environ.get("API_KEYS", "")

        for entry in api_keys_env.split(","):
            entry = entry.strip()
            if not entry:
                continue

            # Check for service:key format
            if ":" in entry:
                service, key = entry.split(":", 1)
                keys[key] = service
            else:
                # Plain key without service identity
                keys[entry] = None

        return keys

    async def verify_executor(self, token: str, cluster_id: str) -> bool:
        """
        Verify executor authentication token.

        Args:
            token: Authentication token from executor (may include "Bearer " prefix)
            cluster_id: Cluster identifier

        Returns:
            True if valid, False otherwise
        """
        if not token:
            return False

        # Strip "Bearer " prefix if present
        if token.startswith("Bearer "):
            token = token[7:]

        # Check Redis for executor token
        redis_key = f"executor:token:{cluster_id}"
        stored_token = await self.redis.get(redis_key)

        if stored_token:
            # Use constant-time comparison for security
            stored_token = (
                stored_token.decode("utf-8") if isinstance(stored_token, bytes) else stored_token
            )
            return secrets.compare_digest(token, stored_token)

        return False

    async def verify_api_key(self, api_key: str) -> Tuple[bool, Optional[str]]:
        """
        Verify AI/User/A2A Service API key.

        Args:
            api_key: API key from X-API-Key header

        Returns:
            Tuple of (is_valid, service_identity)
        """
        if not api_key:
            return False, None

        # Check if key exists and return associated service identity
        if api_key in self.api_keys:
            service_identity = self.api_keys[api_key]

            # Log API key usage with service identity
            await self._log_event(
                "api_key_verified",
                {"service_identity": service_identity, "timestamp": datetime.now(UTC).isoformat()},
            )

            return True, service_identity

        # Could check Redis for dynamic keys here in future
        # redis_key = f"api:key:{hashlib.sha256(api_key.encode()).hexdigest()}"
        # stored_data = await self.redis.get(redis_key)

        return False, None

    async def verify_credentials(
        self,
        api_key: Optional[str] = None,
        bearer_token: Optional[str] = None
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Verify credentials (API key only for base module).

        Args:
            api_key: API key from X-API-Key header
            bearer_token: JWT token (not supported in base module)

        Returns:
            Tuple of (is_valid, identity, auth_method)
        """
        # Base module only supports API keys
        if api_key:
            is_valid, service_identity = await self.verify_api_key(api_key)
            if is_valid:
                return True, service_identity, "api_key"

        # No valid authentication
        return False, None, None

    async def extract_service_identity(self, api_key: str) -> Optional[str]:
        """
        Extract service identity from API key if present.

        Args:
            api_key: API key to check

        Returns:
            Service identity string or None
        """
        if api_key in self.api_keys:
            return self.api_keys[api_key]
        return None

    async def create_executor_token(self, cluster_id: str) -> str:
        """
        Generate new executor token for cluster.

        Args:
            cluster_id: Cluster identifier

        Returns:
            Generated token
        """
        # Generate cryptographically secure token (32 bytes = 256 bits)
        token = secrets.token_urlsafe(32)

        # Store in Redis with no expiration
        redis_key = f"executor:token:{cluster_id}"
        await self.redis.set(redis_key, token)

        # Log token creation for audit
        await self._log_event(
            "executor_token_created",
            {"cluster_id": cluster_id, "timestamp": datetime.now(UTC).isoformat()},
        )

        return token

    async def revoke_executor_token(self, cluster_id: str) -> None:
        """
        Revoke executor token.

        Args:
            cluster_id: Cluster identifier
        """
        redis_key = f"executor:token:{cluster_id}"
        await self.redis.delete(redis_key)

        # Log revocation for audit
        await self._log_event(
            "executor_token_revoked",
            {"cluster_id": cluster_id, "timestamp": datetime.now(UTC).isoformat()},
        )

    async def _log_event(self, event_type: str, data: dict, correlation_id: Optional[str] = None):
        """
        Log security event for audit with optional correlation ID.

        Args:
            event_type: Type of security event
            data: Event data
            correlation_id: Optional correlation ID for multi-agent tracing
        """
        event = {
            "type": event_type,
            "data": data,
            "correlation_id": correlation_id,
            "timestamp": datetime.now(UTC).isoformat(),
        }

        # Store in Redis list for audit trail
        await self.redis.lpush("auth:audit", json.dumps(event))

        # Keep last 10000 events
        await self.redis.ltrim("auth:audit", 0, 9999)
