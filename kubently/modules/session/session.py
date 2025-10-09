import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import List, Optional


class SessionModule:
    def __init__(self, redis_client, default_ttl: int = 300):
        """
        Initialize session module.

        Args:
            redis_client: Async Redis client
            default_ttl: Default session TTL in seconds (5 minutes)
        """
        self.redis = redis_client
        self.default_ttl = default_ttl

    async def create_session(
        self,
        cluster_id: str,
        user_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        service_identity: Optional[str] = None,
    ) -> str:
        """
        Create a new debugging session.

        Args:
            cluster_id: Target cluster identifier
            user_id: Optional user/AI identifier
            correlation_id: Optional A2A correlation tracking ID
            service_identity: Optional calling service identifier

        Returns:
            Session ID (UUID)

        Logic:
        1. Generate UUID for session
        2. Store session data in Redis
        3. Mark cluster as active
        4. Set TTL on all keys
        5. Index by correlation ID if provided
        """
        session_id = str(uuid.uuid4())

        # Session data with A2A tracking
        now = datetime.now(UTC)
        session_data = {
            "session_id": session_id,
            "cluster_id": cluster_id,
            "user_id": user_id or "anonymous",
            "service_identity": service_identity,
            "correlation_id": correlation_id,
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=self.default_ttl)).isoformat(),
            "last_activity": now.isoformat(),
            "command_count": 0,
            "ttl": self.default_ttl,
        }

        # Store session data
        session_key = f"session:{session_id}"
        await self.redis.setex(session_key, self.default_ttl, json.dumps(session_data))

        # Mark cluster as active (for fast polling)
        cluster_active_key = f"cluster:active:{cluster_id}"
        await self.redis.setex(cluster_active_key, self.default_ttl, session_id)

        # Add to active sessions set
        await self.redis.sadd("sessions:active", session_id)

        # Store reverse mapping (cluster -> session)
        cluster_session_key = f"cluster:session:{cluster_id}"
        await self.redis.setex(cluster_session_key, self.default_ttl, session_id)

        # Index by correlation ID if provided (for A2A chains)
        if correlation_id:
            correlation_key = f"correlation:{correlation_id}:sessions"
            await self.redis.sadd(correlation_key, session_id)
            await self.redis.expire(correlation_key, self.default_ttl)

        # Publish event for monitoring
        await self._publish_event("session.created", session_data)

        return session_id

    async def get_session(self, session_id: str) -> Optional[dict]:
        """
        Get session details.

        Args:
            session_id: Session identifier

        Returns:
            Session data dict or None if not found
        """
        session_key = f"session:{session_id}"
        data = await self.redis.get(session_key)

        if data:
            return json.loads(data)
        return None

    async def is_cluster_active(self, cluster_id: str) -> bool:
        """
        Check if cluster has an active debugging session.

        This is called frequently by agents to determine polling interval.

        Args:
            cluster_id: Cluster identifier

        Returns:
            True if cluster has active session

        Performance:
        - This is a hot path, must be fast
        - Single Redis EXISTS operation
        - Consider caching if needed
        """
        cluster_active_key = f"cluster:active:{cluster_id}"
        return await self.redis.exists(cluster_active_key) > 0

    async def keep_alive(self, session_id: str) -> bool:
        """
        Extend session TTL (heartbeat).

        Called when commands are executed to keep session active.

        Args:
            session_id: Session identifier

        Returns:
            True if session exists and was extended
        """
        session_key = f"session:{session_id}"

        # Get session data
        data = await self.redis.get(session_key)
        if not data:
            return False

        session_data = json.loads(data)

        # Update last activity
        session_data["last_activity"] = datetime.now(UTC).isoformat()
        session_data["command_count"] += 1

        # Update session with new TTL
        await self.redis.setex(session_key, self.default_ttl, json.dumps(session_data))

        # Also extend cluster active marker
        cluster_id = session_data["cluster_id"]
        cluster_active_key = f"cluster:active:{cluster_id}"
        await self.redis.expire(cluster_active_key, self.default_ttl)

        # Extend cluster->session mapping
        cluster_session_key = f"cluster:session:{cluster_id}"
        await self.redis.expire(cluster_session_key, self.default_ttl)

        # Extend correlation index TTL if present
        correlation_id = session_data.get("correlation_id")
        if correlation_id:
            correlation_key = f"correlation:{correlation_id}:sessions"
            await self.redis.expire(correlation_key, self.default_ttl)

        return True

    async def end_session(self, session_id: str) -> None:
        """
        End a session early.

        Args:
            session_id: Session identifier

        Logic:
        1. Get session data for cluster_id
        2. Delete all session-related keys
        3. Remove cluster active marker
        4. Publish event
        """
        session_key = f"session:{session_id}"

        # Get session data first
        data = await self.redis.get(session_key)
        if not data:
            return

        session_data = json.loads(data)
        cluster_id = session_data["cluster_id"]
        correlation_id = session_data.get("correlation_id")

        # Delete all related keys
        keys_to_delete = [
            session_key,
            f"cluster:active:{cluster_id}",
            f"cluster:session:{cluster_id}",
        ]

        if keys_to_delete:
            await self.redis.delete(*keys_to_delete)

        # Remove from active set
        await self.redis.srem("sessions:active", session_id)

        # Remove from correlation index if present
        if correlation_id:
            correlation_key = f"correlation:{correlation_id}:sessions"
            await self.redis.srem(correlation_key, session_id)

        # Publish event
        await self._publish_event(
            "session.ended",
            {
                "session_id": session_id,
                "cluster_id": cluster_id,
                "correlation_id": correlation_id,
                "service_identity": session_data.get("service_identity"),
                "ended_at": datetime.now(UTC).isoformat(),
            },
        )

    async def get_active_sessions(self) -> List[dict]:
        """
        Get all active sessions.

        Used for monitoring/admin purposes.

        Returns:
            List of active session data
        """
        # Get all session IDs from set
        session_ids = await self.redis.smembers("sessions:active")

        sessions = []
        for session_id in session_ids:
            session_key = f"session:{session_id}"
            data = await self.redis.get(session_key)

            if data:
                sessions.append(json.loads(data))
            else:
                # Clean up stale entry
                await self.redis.srem("sessions:active", session_id)

        return sessions

    async def get_sessions_by_correlation(self, correlation_id: str) -> List[dict]:
        """
        Get all sessions linked to a correlation ID (A2A chains).

        Args:
            correlation_id: The correlation ID to search for

        Returns:
            List of session data dicts linked to the correlation ID
        """
        correlation_key = f"correlation:{correlation_id}:sessions"
        session_ids = await self.redis.smembers(correlation_key)

        sessions = []
        for session_id in session_ids:
            session_key = f"session:{session_id}"
            data = await self.redis.get(session_key)

            if data:
                sessions.append(json.loads(data))
            else:
                # Clean up stale entry
                await self.redis.srem(correlation_key, session_id)

        return sessions

    async def cleanup_expired(self) -> int:
        """
        Clean up expired sessions from active set.

        Should be called periodically.

        Returns:
            Number of sessions cleaned up
        """
        session_ids = await self.redis.smembers("sessions:active")
        cleaned = 0

        for session_id in session_ids:
            session_key = f"session:{session_id}"
            if not await self.redis.exists(session_key):
                await self.redis.srem("sessions:active", session_id)
                cleaned += 1

        return cleaned

    async def _publish_event(self, event_type: str, data: dict):
        """Publish session event for monitoring"""
        event = {"type": event_type, "timestamp": datetime.now(UTC).isoformat(), "data": data}

        # Publish to Redis pub/sub for real-time monitoring
        await self.redis.publish(f"events:session", json.dumps(event))

        # Also store in list for history
        await self.redis.lpush("session:events", json.dumps(event))
        await self.redis.ltrim("session:events", 0, 999)  # Keep last 1000
