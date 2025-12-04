"""
API Endpoint tests for Capability Reporting feature.

Tests cover:
- POST /executor/capabilities - Executor capability reporting
- POST /executor/heartbeat - TTL refresh
- GET /api/v1/clusters/{cluster_id}/capabilities - Client capability lookup
- GET /debug/clusters/{cluster_id} - Admin cluster detail

These tests use FastAPI TestClient with mocked dependencies to test
the endpoints in isolation.
"""

import json
import os
import sys
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Depends, HTTPException
from fastapi.testclient import TestClient

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kubently.modules.capability import CapabilityModule, ExecutorCapabilities


# =============================================================================
# Test App Setup
# =============================================================================


def create_test_app():
    """
    Create a minimal test FastAPI app with capability endpoints.

    This isolates the capability endpoints from the full application,
    making tests faster and more focused.
    """
    app = FastAPI()

    # Mock modules
    mock_redis = AsyncMock()
    mock_capability_module = AsyncMock(spec=CapabilityModule)
    mock_capability_module.default_ttl = 3600

    # Store in app state for access
    app.state.capability_module = mock_capability_module
    app.state.redis_client = mock_redis

    # Mock auth dependencies
    async def mock_verify_executor_auth() -> str:
        """Mock executor authentication - returns cluster_id."""
        return "test-cluster"

    async def mock_verify_api_key():
        """Mock API key authentication."""
        return (True, "test-service")

    # Endpoints (copied from main.py with simplified auth)
    from pydantic import BaseModel, Field
    from typing import Optional

    class CapabilityReport(BaseModel):
        mode: str = Field(..., pattern=r"^(readOnly|extendedReadOnly|fullAccess)$")
        allowed_verbs: list[str] = Field(default_factory=list, max_length=50)
        restricted_resources: list[str] = Field(default_factory=list, max_length=50)
        allowed_flags: list[str] = Field(default_factory=list, max_length=100)
        executor_version: Optional[str] = Field(None, max_length=50)
        executor_pod: Optional[str] = Field(None, max_length=253)

    @app.post("/executor/capabilities")
    async def report_capabilities(
        report: CapabilityReport,
        cluster_id: str = Depends(mock_verify_executor_auth),
    ):
        capability_module = app.state.capability_module
        if not capability_module:
            raise HTTPException(503, "Service not initialized")

        capabilities = ExecutorCapabilities(
            cluster_id=cluster_id,
            mode=report.mode,
            allowed_verbs=report.allowed_verbs,
            restricted_resources=report.restricted_resources,
            allowed_flags=report.allowed_flags,
            executor_version=report.executor_version,
            executor_pod=report.executor_pod,
            features={
                "exec": report.mode in ["extendedReadOnly", "fullAccess"],
                "port_forward": report.mode in ["extendedReadOnly", "fullAccess"],
                "proxy": report.mode == "fullAccess",
            },
        )

        success = await capability_module.store_capabilities(capabilities)
        if not success:
            raise HTTPException(500, "Failed to store capabilities")

        return {
            "status": "success",
            "message": f"Capabilities stored for cluster {cluster_id}",
            "mode": report.mode,
            "ttl_seconds": capability_module.default_ttl,
        }

    @app.post("/executor/heartbeat")
    async def executor_heartbeat(
        cluster_id: str = Depends(mock_verify_executor_auth),
    ):
        capability_module = app.state.capability_module
        if not capability_module:
            raise HTTPException(503, "Service not initialized")

        success = await capability_module.refresh_ttl(cluster_id)
        if not success:
            return {
                "status": "not_found",
                "message": f"No capabilities found for cluster {cluster_id}. Consider re-reporting.",
            }

        return {
            "status": "success",
            "message": f"TTL refreshed for cluster {cluster_id}",
        }

    @app.get("/api/v1/clusters/{cluster_id}/capabilities")
    async def get_cluster_capabilities(
        cluster_id: str,
        auth_info = Depends(mock_verify_api_key),
    ):
        capability_module = app.state.capability_module
        if not capability_module:
            raise HTTPException(503, "Service not initialized")

        capabilities = await capability_module.get_capabilities(cluster_id)

        return {
            "clusterId": cluster_id,
            "capabilities": capabilities.to_dict() if capabilities else None,
            "available": capabilities is not None,
        }

    @app.get("/debug/clusters/{cluster_id}")
    async def get_cluster_detail(
        cluster_id: str,
        auth_info = Depends(mock_verify_api_key),
    ):
        capability_module = app.state.capability_module
        if not capability_module:
            raise HTTPException(503, "Service not initialized")

        detail = await capability_module.get_cluster_detail(cluster_id)
        return detail

    return app


@pytest.fixture
def test_app():
    """Create test app fixture."""
    return create_test_app()


@pytest.fixture
def client(test_app):
    """Create test client fixture."""
    return TestClient(test_app)


@pytest.fixture
def capability_module(test_app):
    """Get the mock capability module from app state."""
    return test_app.state.capability_module


# =============================================================================
# POST /executor/capabilities Tests
# =============================================================================


class TestReportCapabilities:
    """Tests for the capability reporting endpoint."""

    def test_report_capabilities_success(self, client, capability_module):
        """Test successful capability reporting."""
        capability_module.store_capabilities.return_value = True

        response = client.post(
            "/executor/capabilities",
            json={
                "mode": "readOnly",
                "allowed_verbs": ["get", "describe", "logs"],
                "restricted_resources": ["secrets"],
                "allowed_flags": ["--namespace"],
                "executor_version": "1.0.0",
                "executor_pod": "executor-abc123",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["mode"] == "readOnly"
        assert data["ttl_seconds"] == 3600

        # Verify store was called
        capability_module.store_capabilities.assert_called_once()
        call_arg = capability_module.store_capabilities.call_args[0][0]
        assert call_arg.cluster_id == "test-cluster"
        assert call_arg.mode == "readOnly"

    def test_report_capabilities_minimal(self, client, capability_module):
        """Test capability reporting with minimal required fields."""
        capability_module.store_capabilities.return_value = True

        response = client.post(
            "/executor/capabilities",
            json={"mode": "readOnly"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"

    def test_report_capabilities_extended_readonly(self, client, capability_module):
        """Test extendedReadOnly mode sets correct features."""
        capability_module.store_capabilities.return_value = True

        response = client.post(
            "/executor/capabilities",
            json={"mode": "extendedReadOnly"},
        )

        assert response.status_code == 200

        # Verify features were set correctly
        call_arg = capability_module.store_capabilities.call_args[0][0]
        assert call_arg.features["exec"] is True
        assert call_arg.features["port_forward"] is True
        assert call_arg.features["proxy"] is False

    def test_report_capabilities_full_access(self, client, capability_module):
        """Test fullAccess mode sets all features."""
        capability_module.store_capabilities.return_value = True

        response = client.post(
            "/executor/capabilities",
            json={"mode": "fullAccess"},
        )

        assert response.status_code == 200

        # Verify features were set correctly
        call_arg = capability_module.store_capabilities.call_args[0][0]
        assert call_arg.features["exec"] is True
        assert call_arg.features["port_forward"] is True
        assert call_arg.features["proxy"] is True

    def test_report_capabilities_invalid_mode(self, client, capability_module):
        """Test validation rejects invalid mode values."""
        response = client.post(
            "/executor/capabilities",
            json={"mode": "invalidMode"},
        )

        assert response.status_code == 422  # Validation error
        capability_module.store_capabilities.assert_not_called()

    def test_report_capabilities_storage_failure(self, client, capability_module):
        """Test handling of storage failure."""
        capability_module.store_capabilities.return_value = False

        response = client.post(
            "/executor/capabilities",
            json={"mode": "readOnly"},
        )

        assert response.status_code == 500
        assert "Failed to store" in response.json()["detail"]

    def test_report_capabilities_missing_mode(self, client, capability_module):
        """Test validation rejects missing required mode field."""
        response = client.post(
            "/executor/capabilities",
            json={"allowed_verbs": ["get"]},
        )

        assert response.status_code == 422

    def test_report_capabilities_version_too_long(self, client, capability_module):
        """Test validation rejects overly long executor_version."""
        response = client.post(
            "/executor/capabilities",
            json={
                "mode": "readOnly",
                "executor_version": "x" * 51,  # Max is 50
            },
        )

        assert response.status_code == 422


# =============================================================================
# POST /executor/heartbeat Tests
# =============================================================================


class TestExecutorHeartbeat:
    """Tests for the heartbeat endpoint."""

    def test_heartbeat_success(self, client, capability_module):
        """Test successful heartbeat refreshes TTL."""
        capability_module.refresh_ttl.return_value = True

        response = client.post("/executor/heartbeat")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "TTL refreshed" in data["message"]

        capability_module.refresh_ttl.assert_called_once_with("test-cluster")

    def test_heartbeat_no_capabilities(self, client, capability_module):
        """Test heartbeat when no capabilities exist (graceful degradation)."""
        capability_module.refresh_ttl.return_value = False

        response = client.post("/executor/heartbeat")

        assert response.status_code == 200  # Not an error!
        data = response.json()
        assert data["status"] == "not_found"
        assert "re-reporting" in data["message"]


# =============================================================================
# GET /api/v1/clusters/{cluster_id}/capabilities Tests
# =============================================================================


class TestGetClusterCapabilities:
    """Tests for the capability lookup endpoint."""

    def test_get_capabilities_found(self, client, capability_module):
        """Test successful capability retrieval."""
        mock_caps = ExecutorCapabilities(
            cluster_id="prod-cluster",
            mode="readOnly",
            allowed_verbs=["get", "describe"],
            restricted_resources=["secrets"],
            allowed_flags=["--namespace"],
            features={"exec": False},
        )
        capability_module.get_capabilities.return_value = mock_caps

        response = client.get("/api/v1/clusters/prod-cluster/capabilities")

        assert response.status_code == 200
        data = response.json()
        assert data["clusterId"] == "prod-cluster"
        assert data["available"] is True
        assert data["capabilities"]["mode"] == "readOnly"

    def test_get_capabilities_not_found(self, client, capability_module):
        """Test capability lookup when not stored (graceful degradation)."""
        capability_module.get_capabilities.return_value = None

        response = client.get("/api/v1/clusters/unknown-cluster/capabilities")

        assert response.status_code == 200  # Not an error!
        data = response.json()
        assert data["clusterId"] == "unknown-cluster"
        assert data["available"] is False
        assert data["capabilities"] is None


# =============================================================================
# GET /debug/clusters/{cluster_id} Tests
# =============================================================================


class TestGetClusterDetail:
    """Tests for the admin cluster detail endpoint."""

    def test_get_cluster_detail_full(self, client, capability_module):
        """Test cluster detail with all data present."""
        mock_detail = {
            "clusterId": "detail-cluster",
            "status": {
                "hasToken": True,
                "hasActiveSession": True,
                "executorReporting": True,
            },
            "capabilities": {
                "mode": "readOnly",
                "allowed_verbs": ["get", "describe"],
                "cluster_id": "detail-cluster",
            },
            "ttlRemaining": 1800,
        }
        capability_module.get_cluster_detail.return_value = mock_detail

        response = client.get("/debug/clusters/detail-cluster")

        assert response.status_code == 200
        data = response.json()
        assert data["clusterId"] == "detail-cluster"
        assert data["status"]["hasToken"] is True
        assert data["status"]["executorReporting"] is True
        assert data["capabilities"]["mode"] == "readOnly"
        assert data["ttlRemaining"] == 1800

    def test_get_cluster_detail_no_capabilities(self, client, capability_module):
        """Test cluster detail when no capabilities are reported."""
        mock_detail = {
            "clusterId": "no-caps-cluster",
            "status": {
                "hasToken": True,
                "hasActiveSession": False,
                "executorReporting": False,
            },
            "capabilities": None,
            "ttlRemaining": None,
        }
        capability_module.get_cluster_detail.return_value = mock_detail

        response = client.get("/debug/clusters/no-caps-cluster")

        assert response.status_code == 200
        data = response.json()
        assert data["status"]["executorReporting"] is False
        assert data["capabilities"] is None


# =============================================================================
# Pydantic Validation Tests
# =============================================================================


class TestCapabilityReportValidation:
    """Tests for CapabilityReport Pydantic model validation."""

    def test_mode_enum_validation(self, client, capability_module):
        """Test mode field only accepts valid enum values."""
        # Valid modes
        for mode in ["readOnly", "extendedReadOnly", "fullAccess"]:
            capability_module.store_capabilities.return_value = True
            response = client.post(
                "/executor/capabilities",
                json={"mode": mode},
            )
            assert response.status_code == 200, f"Mode {mode} should be valid"

        # Invalid modes
        for invalid_mode in ["readonly", "READONLY", "read-only", "admin", ""]:
            response = client.post(
                "/executor/capabilities",
                json={"mode": invalid_mode},
            )
            assert response.status_code == 422, f"Mode {invalid_mode} should be invalid"

    def test_array_length_limits(self, client, capability_module):
        """Test that array fields respect max_length constraints."""
        # allowed_verbs max is 50
        response = client.post(
            "/executor/capabilities",
            json={
                "mode": "readOnly",
                "allowed_verbs": ["verb"] * 51,  # Exceeds max
            },
        )
        assert response.status_code == 422

    def test_pod_name_length_limit(self, client, capability_module):
        """Test executor_pod respects K8s max pod name length."""
        response = client.post(
            "/executor/capabilities",
            json={
                "mode": "readOnly",
                "executor_pod": "x" * 254,  # Max is 253
            },
        )
        assert response.status_code == 422


# =============================================================================
# Integration-style Tests
# =============================================================================


class TestCapabilityWorkflow:
    """Tests for complete capability reporting workflows."""

    def test_report_then_heartbeat_workflow(self, client, capability_module):
        """Test typical executor workflow: report capabilities then heartbeat."""
        # Step 1: Report capabilities on startup
        capability_module.store_capabilities.return_value = True

        response = client.post(
            "/executor/capabilities",
            json={
                "mode": "readOnly",
                "allowed_verbs": ["get", "describe", "logs"],
                "executor_version": "1.0.0",
            },
        )
        assert response.status_code == 200

        # Step 2: Send heartbeat to refresh TTL
        capability_module.refresh_ttl.return_value = True

        response = client.post("/executor/heartbeat")
        assert response.status_code == 200
        assert response.json()["status"] == "success"

    def test_expired_capabilities_re_report(self, client, capability_module):
        """Test workflow when capabilities expire and need re-reporting."""
        # Heartbeat fails (capabilities expired)
        capability_module.refresh_ttl.return_value = False

        response = client.post("/executor/heartbeat")
        assert response.status_code == 200
        assert response.json()["status"] == "not_found"

        # Re-report capabilities
        capability_module.store_capabilities.return_value = True

        response = client.post(
            "/executor/capabilities",
            json={"mode": "readOnly"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "success"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
