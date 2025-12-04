"""
Capability Module for Kubently API.

This module handles storage and retrieval of executor capability advertisements.
Executors report their DynamicCommandWhitelist configuration on startup, allowing
the API and agent to know what each cluster can do before sending commands.

Design Principles:
- Graceful degradation: Missing capabilities = proceed with current behavior
- Non-blocking: Capability operations never block core functionality
- TTL-based: Capabilities expire if executor stops heartbeating
"""

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger("kubently.capability")


@dataclass
class ExecutorCapabilities:
    """
    Capability report from an executor.

    Maps directly to DynamicCommandWhitelist.get_config_summary() output,
    with additional metadata for tracking.
    """

    cluster_id: str
    mode: str  # "readOnly", "extendedReadOnly", "fullAccess"
    allowed_verbs: List[str]
    restricted_resources: List[str] = field(default_factory=list)
    allowed_flags: List[str] = field(default_factory=list)

    # Metadata
    executor_version: Optional[str] = None
    executor_pod: Optional[str] = None
    reported_at: Optional[str] = None
    expires_at: Optional[str] = None

    # Feature flags derived from mode
    features: Dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExecutorCapabilities":
        """Create from dictionary (e.g., from JSON)."""
        return cls(
            cluster_id=data.get("cluster_id", ""),
            mode=data.get("mode", "unknown"),
            allowed_verbs=data.get("allowed_verbs", []),
            restricted_resources=data.get("restricted_resources", []),
            allowed_flags=data.get("allowed_flags", []),
            executor_version=data.get("executor_version"),
            executor_pod=data.get("executor_pod"),
            reported_at=data.get("reported_at"),
            expires_at=data.get("expires_at"),
            features=data.get("features", {}),
        )

    @classmethod
    def from_whitelist_summary(
        cls,
        cluster_id: str,
        summary: Dict[str, Any],
        executor_version: Optional[str] = None,
        executor_pod: Optional[str] = None,
    ) -> "ExecutorCapabilities":
        """
        Create from DynamicCommandWhitelist.get_config_summary() output.

        This is the primary way executors create capability reports.
        """
        mode = summary.get("mode", "unknown")

        # Derive feature flags from mode
        features = {
            "exec": mode in ["extendedReadOnly", "fullAccess"],
            "port_forward": mode in ["extendedReadOnly", "fullAccess"],
            "proxy": mode == "fullAccess",
            "cp": mode == "fullAccess",
        }

        return cls(
            cluster_id=cluster_id,
            mode=mode,
            allowed_verbs=summary.get("allowed_verbs", []),
            restricted_resources=list(summary.get("restricted_resources", [])),
            allowed_flags=list(summary.get("allowed_flags", [])),
            executor_version=executor_version,
            executor_pod=executor_pod,
            features=features,
        )


class CapabilityModule:
    """
    Manages executor capability storage in Redis.

    Follows the same patterns as SessionModule and AuthModule:
    - Receives redis_client in __init__
    - Uses consistent key naming (cluster:{id}:capabilities)
    - TTL-based expiration with heartbeat refresh
    """

    # Default TTL: 1 hour - executors should heartbeat every 5 minutes
    DEFAULT_TTL = 3600

    def __init__(self, redis_client, default_ttl: int = DEFAULT_TTL):
        """
        Initialize capability module.

        Args:
            redis_client: Async Redis client from API Core
            default_ttl: TTL for capability records in seconds (default: 1 hour)
        """
        self.redis = redis_client
        self.default_ttl = default_ttl

    def _key(self, cluster_id: str) -> str:
        """Generate Redis key for cluster capabilities."""
        return f"cluster:{cluster_id}:capabilities"

    async def store_capabilities(
        self, capabilities: ExecutorCapabilities
    ) -> bool:
        """
        Store executor capabilities in Redis.

        Args:
            capabilities: Capability report from executor

        Returns:
            True if stored successfully, False otherwise
        """
        try:
            key = self._key(capabilities.cluster_id)

            # Add timestamps
            now = datetime.now(UTC)
            capabilities.reported_at = now.isoformat()
            capabilities.expires_at = (
                now + timedelta(seconds=self.default_ttl)
            ).isoformat()

            # Store with TTL
            data = json.dumps(capabilities.to_dict())
            await self.redis.setex(key, self.default_ttl, data)

            logger.info(
                f"Stored capabilities for cluster {capabilities.cluster_id} "
                f"(mode: {capabilities.mode}, TTL: {self.default_ttl}s)"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to store capabilities: {e}")
            return False

    async def get_capabilities(
        self, cluster_id: str
    ) -> Optional[ExecutorCapabilities]:
        """
        Retrieve executor capabilities from Redis.

        Args:
            cluster_id: Cluster identifier

        Returns:
            ExecutorCapabilities if found, None otherwise
        """
        try:
            key = self._key(cluster_id)
            data = await self.redis.get(key)

            if not data:
                logger.debug(f"No capabilities found for cluster {cluster_id}")
                return None

            # Handle bytes from Redis
            if isinstance(data, bytes):
                data = data.decode("utf-8")

            return ExecutorCapabilities.from_dict(json.loads(data))

        except Exception as e:
            logger.error(f"Failed to get capabilities for {cluster_id}: {e}")
            return None

    async def refresh_ttl(self, cluster_id: str) -> bool:
        """
        Refresh TTL on heartbeat from executor.

        Args:
            cluster_id: Cluster identifier

        Returns:
            True if TTL was refreshed, False if key doesn't exist
        """
        try:
            key = self._key(cluster_id)

            # Check if key exists
            if not await self.redis.exists(key):
                logger.debug(f"Cannot refresh TTL - no capabilities for {cluster_id}")
                return False

            # Update expires_at in the stored data
            data = await self.redis.get(key)
            if data:
                if isinstance(data, bytes):
                    data = data.decode("utf-8")

                capabilities = json.loads(data)
                capabilities["expires_at"] = (
                    datetime.now(UTC) + timedelta(seconds=self.default_ttl)
                ).isoformat()

                # Re-store with fresh TTL
                await self.redis.setex(key, self.default_ttl, json.dumps(capabilities))
                logger.debug(f"Refreshed TTL for cluster {cluster_id}")
                return True

            return False

        except Exception as e:
            logger.error(f"Failed to refresh TTL for {cluster_id}: {e}")
            return False

    async def delete_capabilities(self, cluster_id: str) -> bool:
        """
        Delete capabilities for a cluster.

        Args:
            cluster_id: Cluster identifier

        Returns:
            True if deleted, False otherwise
        """
        try:
            key = self._key(cluster_id)
            result = await self.redis.delete(key)
            return result > 0

        except Exception as e:
            logger.error(f"Failed to delete capabilities for {cluster_id}: {e}")
            return False

    async def list_all_capabilities(self) -> List[ExecutorCapabilities]:
        """
        List capabilities for all clusters.

        Used for admin/monitoring purposes.

        Returns:
            List of all stored capabilities
        """
        try:
            # Find all capability keys
            pattern = "cluster:*:capabilities"
            keys = await self.redis.keys(pattern)

            capabilities_list = []
            for key in keys:
                if isinstance(key, bytes):
                    key = key.decode("utf-8")

                # Extract cluster_id from key
                parts = key.split(":")
                if len(parts) >= 2:
                    cluster_id = parts[1]
                    caps = await self.get_capabilities(cluster_id)
                    if caps:
                        capabilities_list.append(caps)

            return capabilities_list

        except Exception as e:
            logger.error(f"Failed to list capabilities: {e}")
            return []

    async def get_cluster_detail(self, cluster_id: str) -> Dict[str, Any]:
        """
        Get detailed cluster status including capabilities.

        This aggregates data from multiple Redis keys for admin display.

        Args:
            cluster_id: Cluster identifier

        Returns:
            Dictionary with cluster status and capabilities
        """
        try:
            # Check for executor token
            token_key = f"executor:token:{cluster_id}"
            has_token = await self.redis.exists(token_key) > 0

            # Check for active session
            session_key = f"cluster:active:{cluster_id}"
            has_active_session = await self.redis.exists(session_key) > 0

            # Get capabilities (may be None)
            capabilities = await self.get_capabilities(cluster_id)

            # Calculate TTL remaining
            ttl_remaining = None
            if capabilities:
                caps_key = self._key(cluster_id)
                ttl_remaining = await self.redis.ttl(caps_key)

            return {
                "clusterId": cluster_id,
                "status": {
                    "hasToken": has_token,
                    "hasActiveSession": has_active_session,
                    "executorReporting": capabilities is not None,
                },
                "capabilities": capabilities.to_dict() if capabilities else None,
                "ttlRemaining": ttl_remaining,
            }

        except Exception as e:
            logger.error(f"Failed to get cluster detail for {cluster_id}: {e}")
            return {
                "clusterId": cluster_id,
                "status": {"error": str(e)},
                "capabilities": None,
            }
