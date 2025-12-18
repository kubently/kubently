"""
Unit tests for the Capability Module.

Tests cover:
- ExecutorCapabilities dataclass serialization/deserialization
- CapabilityModule Redis operations (store, get, refresh, delete)
- Cluster detail aggregation
- Error handling and edge cases
"""

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kubently.modules.capability import CapabilityModule, ExecutorCapabilities


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_redis():
    """Create a mock Redis client with all required methods."""
    redis = AsyncMock()
    redis.setex = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.exists = AsyncMock(return_value=0)
    redis.delete = AsyncMock(return_value=1)
    redis.keys = AsyncMock(return_value=[])
    redis.ttl = AsyncMock(return_value=3600)
    return redis


@pytest.fixture
def capability_module(mock_redis):
    """Create a CapabilityModule instance with mock Redis."""
    return CapabilityModule(mock_redis, default_ttl=3600)


@pytest.fixture
def sample_capabilities():
    """Create a sample ExecutorCapabilities object."""
    return ExecutorCapabilities(
        cluster_id="test-cluster",
        mode="readOnly",
        allowed_verbs=["get", "describe", "logs"],
        restricted_resources=["secrets", "configmaps"],
        allowed_flags=["--namespace", "--all-namespaces"],
        executor_version="1.0.0",
        executor_pod="executor-pod-abc123",
        features={"exec": False, "port_forward": False, "proxy": False, "cp": False},
    )


@pytest.fixture
def sample_capabilities_extended():
    """Create an ExtendedReadOnly mode capabilities object."""
    return ExecutorCapabilities(
        cluster_id="dev-cluster",
        mode="extendedReadOnly",
        allowed_verbs=["get", "describe", "logs", "exec", "port-forward"],
        restricted_resources=["secrets"],
        allowed_flags=["--namespace", "--all-namespaces", "--container"],
        executor_version="1.1.0",
        executor_pod="executor-dev-xyz",
        features={"exec": True, "port_forward": True, "proxy": False, "cp": False},
    )


# =============================================================================
# ExecutorCapabilities Dataclass Tests
# =============================================================================


class TestExecutorCapabilities:
    """Tests for the ExecutorCapabilities dataclass."""

    def test_to_dict(self, sample_capabilities):
        """Test serialization to dictionary."""
        result = sample_capabilities.to_dict()

        assert result["cluster_id"] == "test-cluster"
        assert result["mode"] == "readOnly"
        assert result["allowed_verbs"] == ["get", "describe", "logs"]
        assert result["restricted_resources"] == ["secrets", "configmaps"]
        assert result["executor_version"] == "1.0.0"
        assert result["features"]["exec"] is False

    def test_from_dict(self):
        """Test deserialization from dictionary."""
        data = {
            "cluster_id": "prod-cluster",
            "mode": "fullAccess",
            "allowed_verbs": ["get", "describe", "apply", "delete"],
            "restricted_resources": [],
            "allowed_flags": ["--all"],
            "executor_version": "2.0.0",
            "executor_pod": "prod-executor",
            "reported_at": "2025-01-01T00:00:00Z",
            "expires_at": "2025-01-01T01:00:00Z",
            "features": {"exec": True, "port_forward": True, "proxy": True, "cp": True},
        }

        caps = ExecutorCapabilities.from_dict(data)

        assert caps.cluster_id == "prod-cluster"
        assert caps.mode == "fullAccess"
        assert "apply" in caps.allowed_verbs
        assert caps.features["proxy"] is True

    def test_from_dict_with_defaults(self):
        """Test deserialization with missing optional fields."""
        data = {
            "cluster_id": "minimal-cluster",
            "mode": "readOnly",
        }

        caps = ExecutorCapabilities.from_dict(data)

        assert caps.cluster_id == "minimal-cluster"
        assert caps.allowed_verbs == []
        assert caps.restricted_resources == []
        assert caps.executor_version is None
        assert caps.features == {}

    def test_from_whitelist_summary_readonly(self):
        """Test creation from DynamicCommandWhitelist summary (readOnly mode)."""
        summary = {
            "mode": "readOnly",
            "allowed_verbs": ["get", "describe", "logs", "top"],
            "restricted_resources": {"secrets", "configmaps"},  # Note: set, not list
            "allowed_flags": {"--namespace", "--output"},  # Note: set, not list
        }

        caps = ExecutorCapabilities.from_whitelist_summary(
            cluster_id="ro-cluster",
            summary=summary,
            executor_version="1.0.0",
            executor_pod="executor-ro",
        )

        assert caps.cluster_id == "ro-cluster"
        assert caps.mode == "readOnly"
        assert caps.features["exec"] is False
        assert caps.features["port_forward"] is False
        assert caps.features["proxy"] is False
        assert caps.features["cp"] is False
        # Sets should be converted to lists
        assert isinstance(caps.restricted_resources, list)

    def test_from_whitelist_summary_extended(self):
        """Test creation from DynamicCommandWhitelist summary (extendedReadOnly mode)."""
        summary = {
            "mode": "extendedReadOnly",
            "allowed_verbs": ["get", "describe", "logs", "exec", "port-forward"],
            "restricted_resources": set(),
            "allowed_flags": set(),
        }

        caps = ExecutorCapabilities.from_whitelist_summary(
            cluster_id="ext-cluster",
            summary=summary,
        )

        assert caps.features["exec"] is True
        assert caps.features["port_forward"] is True
        assert caps.features["proxy"] is False
        assert caps.features["cp"] is False

    def test_from_whitelist_summary_fullaccess(self):
        """Test creation from DynamicCommandWhitelist summary (fullAccess mode)."""
        summary = {
            "mode": "fullAccess",
            "allowed_verbs": ["get", "describe", "apply", "delete", "exec", "cp"],
            "restricted_resources": set(),
            "allowed_flags": set(),
        }

        caps = ExecutorCapabilities.from_whitelist_summary(
            cluster_id="full-cluster",
            summary=summary,
        )

        assert caps.features["exec"] is True
        assert caps.features["port_forward"] is True
        assert caps.features["proxy"] is True
        assert caps.features["cp"] is True

    def test_roundtrip_serialization(self, sample_capabilities):
        """Test that to_dict -> from_dict preserves all data."""
        dict_data = sample_capabilities.to_dict()
        restored = ExecutorCapabilities.from_dict(dict_data)

        assert restored.cluster_id == sample_capabilities.cluster_id
        assert restored.mode == sample_capabilities.mode
        assert restored.allowed_verbs == sample_capabilities.allowed_verbs
        assert restored.executor_version == sample_capabilities.executor_version


# =============================================================================
# CapabilityModule Tests
# =============================================================================


class TestCapabilityModuleStore:
    """Tests for storing capabilities."""

    @pytest.mark.asyncio
    async def test_store_capabilities_success(
        self, capability_module, mock_redis, sample_capabilities
    ):
        """Test successful capability storage."""
        result = await capability_module.store_capabilities(sample_capabilities)

        assert result is True
        mock_redis.setex.assert_called_once()

        # Verify key format
        call_args = mock_redis.setex.call_args[0]
        assert call_args[0] == "cluster:test-cluster:capabilities"
        assert call_args[1] == 3600  # TTL

        # Verify stored data
        stored_data = json.loads(call_args[2])
        assert stored_data["cluster_id"] == "test-cluster"
        assert stored_data["mode"] == "readOnly"
        assert stored_data["reported_at"] is not None
        assert stored_data["expires_at"] is not None

    @pytest.mark.asyncio
    async def test_store_capabilities_adds_timestamps(
        self, capability_module, mock_redis, sample_capabilities
    ):
        """Test that timestamps are added on store."""
        # Ensure original has no timestamps
        sample_capabilities.reported_at = None
        sample_capabilities.expires_at = None

        await capability_module.store_capabilities(sample_capabilities)

        # Verify timestamps were added
        call_args = mock_redis.setex.call_args[0]
        stored_data = json.loads(call_args[2])

        assert stored_data["reported_at"] is not None
        assert stored_data["expires_at"] is not None

        # Verify expires_at is ~1 hour after reported_at
        reported = datetime.fromisoformat(stored_data["reported_at"].replace("Z", "+00:00"))
        expires = datetime.fromisoformat(stored_data["expires_at"].replace("Z", "+00:00"))
        delta = expires - reported
        assert 3590 < delta.total_seconds() < 3610  # ~1 hour

    @pytest.mark.asyncio
    async def test_store_capabilities_redis_error(
        self, capability_module, mock_redis, sample_capabilities
    ):
        """Test graceful handling of Redis errors on store."""
        mock_redis.setex.side_effect = Exception("Redis connection lost")

        result = await capability_module.store_capabilities(sample_capabilities)

        assert result is False


class TestCapabilityModuleGet:
    """Tests for retrieving capabilities."""

    @pytest.mark.asyncio
    async def test_get_capabilities_found(self, capability_module, mock_redis):
        """Test successful capability retrieval."""
        stored_data = {
            "cluster_id": "test-cluster",
            "mode": "readOnly",
            "allowed_verbs": ["get", "describe"],
            "restricted_resources": [],
            "allowed_flags": [],
            "executor_version": "1.0.0",
            "executor_pod": "pod-123",
            "reported_at": "2025-01-01T00:00:00Z",
            "expires_at": "2025-01-01T01:00:00Z",
            "features": {},
        }
        mock_redis.get.return_value = json.dumps(stored_data)

        result = await capability_module.get_capabilities("test-cluster")

        assert result is not None
        assert result.cluster_id == "test-cluster"
        assert result.mode == "readOnly"
        assert result.executor_version == "1.0.0"
        mock_redis.get.assert_called_once_with("cluster:test-cluster:capabilities")

    @pytest.mark.asyncio
    async def test_get_capabilities_handles_bytes(self, capability_module, mock_redis):
        """Test that bytes response from Redis is handled correctly."""
        stored_data = {
            "cluster_id": "bytes-cluster",
            "mode": "extendedReadOnly",
            "allowed_verbs": [],
            "restricted_resources": [],
            "allowed_flags": [],
            "features": {},
        }
        # Redis often returns bytes
        mock_redis.get.return_value = json.dumps(stored_data).encode("utf-8")

        result = await capability_module.get_capabilities("bytes-cluster")

        assert result is not None
        assert result.cluster_id == "bytes-cluster"
        assert result.mode == "extendedReadOnly"

    @pytest.mark.asyncio
    async def test_get_capabilities_not_found(self, capability_module, mock_redis):
        """Test handling of missing capabilities."""
        mock_redis.get.return_value = None

        result = await capability_module.get_capabilities("nonexistent-cluster")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_capabilities_redis_error(self, capability_module, mock_redis):
        """Test graceful handling of Redis errors on get."""
        mock_redis.get.side_effect = Exception("Redis timeout")

        result = await capability_module.get_capabilities("error-cluster")

        assert result is None


class TestCapabilityModuleRefreshTTL:
    """Tests for TTL refresh on heartbeat."""

    @pytest.mark.asyncio
    async def test_refresh_ttl_success(self, capability_module, mock_redis):
        """Test successful TTL refresh."""
        stored_data = {
            "cluster_id": "refresh-cluster",
            "mode": "readOnly",
            "allowed_verbs": [],
            "restricted_resources": [],
            "allowed_flags": [],
            "expires_at": "2025-01-01T00:00:00Z",
            "features": {},
        }
        mock_redis.exists.return_value = 1
        mock_redis.get.return_value = json.dumps(stored_data)

        result = await capability_module.refresh_ttl("refresh-cluster")

        assert result is True
        mock_redis.exists.assert_called_once_with("cluster:refresh-cluster:capabilities")
        mock_redis.setex.assert_called_once()

        # Verify expires_at was updated
        call_args = mock_redis.setex.call_args[0]
        updated_data = json.loads(call_args[2])
        assert updated_data["expires_at"] != stored_data["expires_at"]

    @pytest.mark.asyncio
    async def test_refresh_ttl_key_not_found(self, capability_module, mock_redis):
        """Test TTL refresh when key doesn't exist."""
        mock_redis.exists.return_value = 0

        result = await capability_module.refresh_ttl("missing-cluster")

        assert result is False
        mock_redis.setex.assert_not_called()

    @pytest.mark.asyncio
    async def test_refresh_ttl_redis_error(self, capability_module, mock_redis):
        """Test graceful handling of Redis errors on refresh."""
        mock_redis.exists.side_effect = Exception("Redis error")

        result = await capability_module.refresh_ttl("error-cluster")

        assert result is False


class TestCapabilityModuleDelete:
    """Tests for capability deletion."""

    @pytest.mark.asyncio
    async def test_delete_capabilities_success(self, capability_module, mock_redis):
        """Test successful capability deletion."""
        mock_redis.delete.return_value = 1

        result = await capability_module.delete_capabilities("delete-cluster")

        assert result is True
        mock_redis.delete.assert_called_once_with("cluster:delete-cluster:capabilities")

    @pytest.mark.asyncio
    async def test_delete_capabilities_not_found(self, capability_module, mock_redis):
        """Test deletion when key doesn't exist."""
        mock_redis.delete.return_value = 0

        result = await capability_module.delete_capabilities("nonexistent")

        assert result is False

    @pytest.mark.asyncio
    async def test_delete_capabilities_redis_error(self, capability_module, mock_redis):
        """Test graceful handling of Redis errors on delete."""
        mock_redis.delete.side_effect = Exception("Redis error")

        result = await capability_module.delete_capabilities("error-cluster")

        assert result is False


class TestCapabilityModuleListAll:
    """Tests for listing all capabilities."""

    @pytest.mark.asyncio
    async def test_list_all_capabilities_multiple(self, capability_module, mock_redis):
        """Test listing capabilities from multiple clusters."""
        # Mock keys response
        mock_redis.keys.return_value = [
            b"cluster:cluster-1:capabilities",
            b"cluster:cluster-2:capabilities",
        ]

        # Mock get responses for each cluster
        cluster_1_data = {
            "cluster_id": "cluster-1",
            "mode": "readOnly",
            "allowed_verbs": [],
            "restricted_resources": [],
            "allowed_flags": [],
            "features": {},
        }
        cluster_2_data = {
            "cluster_id": "cluster-2",
            "mode": "fullAccess",
            "allowed_verbs": [],
            "restricted_resources": [],
            "allowed_flags": [],
            "features": {},
        }

        mock_redis.get.side_effect = [
            json.dumps(cluster_1_data),
            json.dumps(cluster_2_data),
        ]

        result = await capability_module.list_all_capabilities()

        assert len(result) == 2
        assert result[0].cluster_id == "cluster-1"
        assert result[1].cluster_id == "cluster-2"

    @pytest.mark.asyncio
    async def test_list_all_capabilities_empty(self, capability_module, mock_redis):
        """Test listing when no capabilities exist."""
        mock_redis.keys.return_value = []

        result = await capability_module.list_all_capabilities()

        assert result == []

    @pytest.mark.asyncio
    async def test_list_all_capabilities_handles_stale(self, capability_module, mock_redis):
        """Test that stale keys (with no data) are handled gracefully."""
        mock_redis.keys.return_value = [
            b"cluster:stale-cluster:capabilities",
        ]
        mock_redis.get.return_value = None  # Key exists but data is gone

        result = await capability_module.list_all_capabilities()

        assert result == []


class TestCapabilityModuleClusterDetail:
    """Tests for the get_cluster_detail aggregation method."""

    @pytest.mark.asyncio
    async def test_get_cluster_detail_full(self, capability_module, mock_redis):
        """Test cluster detail with all data present."""
        # Setup mocks
        mock_redis.exists.side_effect = [1, 1]  # has token, has session
        mock_redis.ttl.return_value = 1800

        capabilities_data = {
            "cluster_id": "detail-cluster",
            "mode": "readOnly",
            "allowed_verbs": ["get", "describe"],
            "restricted_resources": [],
            "allowed_flags": [],
            "executor_version": "1.0.0",
            "executor_pod": "pod-123",
            "features": {},
        }
        mock_redis.get.return_value = json.dumps(capabilities_data)

        result = await capability_module.get_cluster_detail("detail-cluster")

        assert result["clusterId"] == "detail-cluster"
        assert result["status"]["hasToken"] is True
        assert result["status"]["hasActiveSession"] is True
        assert result["status"]["executorReporting"] is True
        assert result["capabilities"]["mode"] == "readOnly"
        assert result["ttlRemaining"] == 1800

    @pytest.mark.asyncio
    async def test_get_cluster_detail_no_capabilities(self, capability_module, mock_redis):
        """Test cluster detail when no capabilities are reported."""
        mock_redis.exists.side_effect = [1, 0]  # has token, no session
        mock_redis.get.return_value = None  # no capabilities

        result = await capability_module.get_cluster_detail("no-caps-cluster")

        assert result["clusterId"] == "no-caps-cluster"
        assert result["status"]["hasToken"] is True
        assert result["status"]["hasActiveSession"] is False
        assert result["status"]["executorReporting"] is False
        assert result["capabilities"] is None
        assert result["ttlRemaining"] is None

    @pytest.mark.asyncio
    async def test_get_cluster_detail_redis_error(self, capability_module, mock_redis):
        """Test cluster detail with Redis error."""
        mock_redis.exists.side_effect = Exception("Redis error")

        result = await capability_module.get_cluster_detail("error-cluster")

        assert result["clusterId"] == "error-cluster"
        assert "error" in result["status"]
        assert result["capabilities"] is None


# =============================================================================
# Edge Cases and Security Tests
# =============================================================================


class TestCapabilityModuleEdgeCases:
    """Edge cases and security considerations."""

    @pytest.mark.asyncio
    async def test_special_characters_in_cluster_id(
        self, capability_module, mock_redis, sample_capabilities
    ):
        """Test handling of special characters in cluster IDs."""
        sample_capabilities.cluster_id = "prod-us-west-1"

        await capability_module.store_capabilities(sample_capabilities)

        call_args = mock_redis.setex.call_args[0]
        assert call_args[0] == "cluster:prod-us-west-1:capabilities"

    @pytest.mark.asyncio
    async def test_empty_arrays_handled(self, capability_module, mock_redis):
        """Test that empty arrays are handled correctly."""
        caps = ExecutorCapabilities(
            cluster_id="empty-cluster",
            mode="readOnly",
            allowed_verbs=[],
            restricted_resources=[],
            allowed_flags=[],
        )

        result = await capability_module.store_capabilities(caps)

        assert result is True
        call_args = mock_redis.setex.call_args[0]
        stored_data = json.loads(call_args[2])
        assert stored_data["allowed_verbs"] == []

    @pytest.mark.asyncio
    async def test_custom_ttl(self, mock_redis):
        """Test custom TTL configuration."""
        custom_ttl = 7200  # 2 hours
        module = CapabilityModule(mock_redis, default_ttl=custom_ttl)

        caps = ExecutorCapabilities(
            cluster_id="custom-ttl-cluster",
            mode="readOnly",
            allowed_verbs=[],
        )

        await module.store_capabilities(caps)

        call_args = mock_redis.setex.call_args[0]
        assert call_args[1] == custom_ttl

    def test_key_generation(self, capability_module):
        """Test Redis key format."""
        key = capability_module._key("my-cluster")
        assert key == "cluster:my-cluster:capabilities"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
