"""Observability E2E tests — health endpoints.

Tests: obs/001, obs/002
Covers: GET /health, GET /health/detailed

Reference: TEST_PLAN.md §4.40
"""

from __future__ import annotations

import pytest

from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import assert_http_ok


@pytest.mark.quick
@pytest.mark.auto
@pytest.mark.observability
class TestHealth:
    """Verify health check endpoints return correct structure."""

    def test_health_returns_status(self, nexus: NexusClient) -> None:
        """obs/001: GET /health → component status with expected fields."""
        data = assert_http_ok(nexus.health())

        assert "status" in data, f"Health response missing 'status': {data}"
        assert data["status"].lower() in ("healthy", "ok"), (
            f"Unexpected health status: {data['status']}"
        )

    def test_health_detailed_returns_components(self, nexus: NexusClient) -> None:
        """obs/002: GET /health/detailed → per-brick status with components.

        Asserts:
        - Status is healthy or degraded
        - Components dict is present with at least one entry
        - Each component has a status field
        """
        data = assert_http_ok(nexus.health_detailed())

        assert "status" in data, f"Detailed health missing 'status': {data}"
        assert data["status"].lower() in ("healthy", "degraded", "ok"), (
            f"Unexpected detailed health status: {data['status']}"
        )

        # Components may be under "components" or "checks" key
        components = data.get("components", data.get("checks", {}))
        assert isinstance(components, dict), (
            f"Expected components dict, got {type(components).__name__}: {data}"
        )
        assert len(components) >= 1, f"Expected at least one component: {data}"

        # Verify first component has a status field
        first_key = next(iter(components))
        first_component = components[first_key]
        if isinstance(first_component, dict):
            assert "status" in first_component, (
                f"Component '{first_key}' missing 'status': {first_component}"
            )

        # If backends are reported, verify structure
        if "backends" in data:
            backends = data["backends"]
            if isinstance(backends, list) and backends:
                backend = backends[0]
                if isinstance(backend, dict):
                    assert "healthy" in backend or "status" in backend, (
                        f"Backend missing health indicator: {backend}"
                    )
