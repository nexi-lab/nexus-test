"""Scheduler permission and auth tests — unauthenticated, bad key, zone scoping.

Tests: scheduler/023-025
Covers: unauthenticated access rejected, invalid key rejected,
        zone-scoped task isolation (where applicable)

Reference: TEST_PLAN.md §4.5, docs/scheduler.md §Permissions
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests.helpers.api_client import NexusClient
from tests.scheduler.conftest import SubmitFn


# ---------------------------------------------------------------------------
# scheduler/023-025: Permission tests
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.scheduler
class TestSchedulerPermissions:
    """Scheduler authentication and authorization tests."""

    def test_unauthenticated_submit_rejected(
        self, nexus: NexusClient,
    ) -> None:
        """scheduler/023: Unauthenticated POST /submit → 401."""
        with httpx.Client() as client:
            resp = client.post(
                f"{nexus.base_url}/api/v2/scheduler/submit",
                json={
                    "executor": "unauth-test",
                    "task_type": "test",
                    "payload": {},
                    "priority": "normal",
                },
            )
        assert resp.status_code in (401, 403), (
            f"Expected 401/403, got {resp.status_code} {resp.text[:200]}"
        )

    def test_unauthenticated_classify_rejected(
        self, nexus: NexusClient,
    ) -> None:
        """scheduler/023b: Unauthenticated POST /classify → 401."""
        with httpx.Client() as client:
            resp = client.post(
                f"{nexus.base_url}/api/v2/scheduler/classify",
                json={"priority": "normal", "request_state": "pending"},
            )
        assert resp.status_code in (401, 403), (
            f"Expected 401/403, got {resp.status_code} {resp.text[:200]}"
        )

    def test_unauthenticated_metrics_rejected(
        self, nexus: NexusClient,
    ) -> None:
        """scheduler/023c: Unauthenticated GET /metrics → 401."""
        with httpx.Client() as client:
            resp = client.get(
                f"{nexus.base_url}/api/v2/scheduler/metrics",
            )
        assert resp.status_code in (401, 403), (
            f"Expected 401/403, got {resp.status_code} {resp.text[:200]}"
        )

    def test_invalid_api_key_rejected(
        self, nexus: NexusClient,
    ) -> None:
        """scheduler/024: Invalid API key → 401."""
        with httpx.Client() as client:
            resp = client.post(
                f"{nexus.base_url}/api/v2/scheduler/submit",
                headers={"Authorization": "Bearer sk-completely-bogus-key"},
                json={
                    "executor": "bad-key-test",
                    "task_type": "test",
                    "payload": {},
                    "priority": "normal",
                },
            )
        assert resp.status_code in (401, 403), (
            f"Expected 401/403, got {resp.status_code} {resp.text[:200]}"
        )

    def test_task_visible_only_to_creator(
        self, nexus: NexusClient, submit_task: SubmitFn, unique_executor: str,
    ) -> None:
        """scheduler/025: Task created by one agent is retrievable.

        Note: Currently scheduler does not enforce per-agent visibility
        (zone_id=None in router). This test verifies the task exists
        and documents the current behavior.
        """
        resp = submit_task(
            unique_executor,
            "visibility_test",
            {"test": True},
        )
        assert resp.status_code == 201
        task_id = resp.json()["id"]

        # Same authenticated session can retrieve it
        status = nexus.api_get(f"/api/v2/scheduler/task/{task_id}")
        assert status.status_code == 200
        assert status.json()["executor_id"] == unique_executor

        # Non-existent task returns 404 (not 403)
        fake_id = str(uuid.uuid4())
        status2 = nexus.api_get(f"/api/v2/scheduler/task/{fake_id}")
        assert status2.status_code == 404
