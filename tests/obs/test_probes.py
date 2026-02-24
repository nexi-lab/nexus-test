"""Observability E2E tests — Kubernetes probes.

Tests: obs/003, obs/004, obs/005
Covers: /healthz/live, /healthz/ready, /healthz/startup

Reference: TEST_PLAN.md §4.40
"""

from __future__ import annotations

import pytest

from tests.helpers.api_client import NexusClient


@pytest.mark.auto
@pytest.mark.observability
class TestKubernetesProbes:
    """Verify Kubernetes liveness, readiness, and startup probes."""

    def test_liveness_probe(self, nexus: NexusClient) -> None:
        """obs/003: GET /healthz/live → 200 with status field."""
        resp = nexus.probe_live()
        assert resp.status_code == 200, (
            f"Liveness probe failed: {resp.status_code} {resp.text[:200]}"
        )
        data = resp.json()
        assert "status" in data, f"Liveness probe missing 'status': {data}"

    def test_readiness_probe(self, nexus: NexusClient) -> None:
        """obs/004: GET /healthz/ready → 200 (server is ready if tests run).

        If 503, verifies graceful startup response with pending_phases.
        """
        resp = nexus.probe_ready()

        if resp.status_code == 503:
            data = resp.json()
            assert "pending_phases" in data or "status" in data, (
                f"503 readiness should explain pending phases: {data}"
            )
            return

        assert resp.status_code == 200, (
            f"Readiness probe failed: {resp.status_code} {resp.text[:200]}"
        )
        data = resp.json()
        assert "status" in data, f"Readiness probe missing 'status': {data}"

    def test_startup_probe(self, nexus: NexusClient) -> None:
        """obs/005: GET /healthz/startup → 200 (server has started if tests run)."""
        resp = nexus.probe_startup()
        assert resp.status_code == 200, (
            f"Startup probe failed: {resp.status_code} {resp.text[:200]}"
        )
        data = resp.json()
        assert "status" in data, f"Startup probe missing 'status': {data}"
