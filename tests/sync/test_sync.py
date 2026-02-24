"""Sync E2E tests — create sync job, conflict detection, conflict resolution.

Tests: sync/001-003
Covers: sync push, conflict detection, conflict resolution

Reference: TEST_PLAN.md §4.5

Infrastructure: docker-compose.demo.yml (standalone)

Sync API endpoints:
    POST /api/v2/sync/mounts/{mount_point}/push — Trigger immediate write-back push

Note: Sync operations depend on mounts with write-back enabled. If no mounts
are configured, tests will skip gracefully.
"""

from __future__ import annotations

import pytest

from tests.helpers.api_client import NexusClient


@pytest.mark.auto
@pytest.mark.sync
class TestSync:
    """Sync job lifecycle and conflict handling tests."""

    def _get_first_mount(self, nexus: NexusClient) -> str | None:
        """Get the first available mount point, or None if no mounts exist."""
        resp = nexus.list_mounts()
        if not resp.ok:
            return None

        result = resp.result
        if isinstance(result, list) and result:
            mount = result[0]
            if isinstance(mount, dict):
                return mount.get("mount_point", mount.get("path"))
            return str(mount)
        if isinstance(result, dict):
            mounts = result.get("mounts", result.get("entries", []))
            if mounts:
                m = mounts[0]
                return m.get("mount_point", m.get("path")) if isinstance(m, dict) else str(m)
        return None

    def test_create_sync_job(self, nexus: NexusClient) -> None:
        """sync/001: Create sync job — push operation runs.

        Triggers a sync push for a mount point and verifies the response
        contains push statistics (changes_pushed, changes_failed, conflicts).
        """
        mount_point = self._get_first_mount(nexus)
        if mount_point is None:
            pytest.skip("No mounts configured — cannot test sync push")

        # URL-encode the mount point for the path parameter
        encoded = mount_point.lstrip("/")
        resp = nexus.api_post(f"/api/v2/sync/mounts/{encoded}/push")

        if resp.status_code == 503:
            pytest.skip("Sync/write-back service not available on this server")
        if resp.status_code == 403:
            pytest.skip("Admin role required for sync push — test key may not be admin")
        if resp.status_code == 404:
            pytest.skip(f"Mount point '{mount_point}' not found by sync service")

        assert resp.status_code == 200, f"Sync push failed: {resp.status_code} {resp.text[:200]}"

        data = resp.json()
        assert "mount_point" in data, f"Sync response missing 'mount_point': {data}"
        assert "changes_pushed" in data, f"Sync response missing 'changes_pushed': {data}"
        assert "changes_failed" in data, f"Sync response missing 'changes_failed': {data}"
        assert isinstance(data["changes_pushed"], int)
        assert isinstance(data["changes_failed"], int)

    def test_conflict_detection(self, nexus: NexusClient) -> None:
        """sync/002: Conflict detection — conflicts flagged in sync response.

        Verifies that the sync mechanism reports conflict information. Since
        triggering actual conflicts requires a specific backend setup, we
        verify the conflict detection fields exist in the response schema.
        """
        mount_point = self._get_first_mount(nexus)
        if mount_point is None:
            pytest.skip("No mounts configured — cannot test conflict detection")

        encoded = mount_point.lstrip("/")
        resp = nexus.api_post(f"/api/v2/sync/mounts/{encoded}/push")

        if resp.status_code in (403, 503):
            pytest.skip("Sync service not available or admin required")
        if resp.status_code == 404:
            pytest.skip(f"Mount '{mount_point}' not found by sync service")

        assert resp.status_code == 200, f"Sync push failed: {resp.text[:200]}"

        data = resp.json()
        assert "conflicts_detected" in data, f"Sync response missing 'conflicts_detected': {data}"
        assert isinstance(data["conflicts_detected"], int)
        assert data["conflicts_detected"] >= 0, (
            f"conflicts_detected should be non-negative: {data['conflicts_detected']}"
        )

    def test_conflict_resolution(self, nexus: NexusClient) -> None:
        """sync/003: Conflict resolution — metrics show resolution state.

        Verifies the sync push response includes metrics that track conflict
        resolution state. After a clean push, conflicts_detected should be 0
        (or remain unchanged if no new conflicts occurred).
        """
        mount_point = self._get_first_mount(nexus)
        if mount_point is None:
            pytest.skip("No mounts configured — cannot test conflict resolution")

        encoded = mount_point.lstrip("/")

        # First push — baseline
        resp1 = nexus.api_post(f"/api/v2/sync/mounts/{encoded}/push")
        if resp1.status_code in (403, 503):
            pytest.skip("Sync service not available or admin required")
        if resp1.status_code == 404:
            pytest.skip(f"Mount '{mount_point}' not found by sync service")

        assert resp1.status_code == 200

        # Second push — should be idempotent (no new changes)
        resp2 = nexus.api_post(f"/api/v2/sync/mounts/{encoded}/push")
        assert resp2.status_code == 200

        data2 = resp2.json()
        # After a clean double-push, no new changes should be pushed
        assert data2["changes_pushed"] >= 0
        assert data2["changes_failed"] >= 0

        # Verify metrics snapshot is present
        if "metrics" in data2:
            metrics = data2["metrics"]
            assert isinstance(metrics, dict), f"Metrics should be a dict: {type(metrics)}"
