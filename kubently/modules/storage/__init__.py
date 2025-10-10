"""
Storage Module - Black Box Interface

Purpose: Abstract all data persistence
Interface: get_redis_client(), store(), retrieve(), delete()
Hidden: Redis specifics, connection pooling, serialization

Can be replaced with any storage backend without affecting other modules.
"""

import os
from typing import Optional

import redis.asyncio as redis


class StorageModule:
    """Black box storage abstraction."""

    def __init__(self, connection_url: str = None):
        """Initialize storage with connection URL."""
        self.url = connection_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self._client = None

    async def connect(self) -> redis.Redis:
        """Get storage connection."""
        if not self._client:
            self._client = redis.from_url(self.url, decode_responses=True)
        return self._client

    async def disconnect(self):
        """Close storage connection."""
        if self._client:
            await self._client.close()
            self._client = None


__all__ = ["StorageModule"]
