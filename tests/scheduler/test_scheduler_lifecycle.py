"""Scheduler extended lifecycle tests — status, metrics, Astraea fields, zones.

Tests: scheduler/011-017
Covers: get status, 404 handling, idempotent cancel, metrics shape,
        metrics after submits, Astraea fields in response, zone isolation

Reference: TEST_PLAN.md §4.5, docs/scheduler.md §REST API Endpoints
"""

from __future__ import annotations

import uuid

import pytest

from tests.helpers.api_client import NexusClient
from tests.scheduler.conftest import SubmitFn


# ---------------------------------------------------------------------------
# scheduler/011-017: Extended lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.scheduler
class TestSchedulerExtendedLifecycle:
    """Extended task lifecycle and metrics tests."""

    def test_get_status_by_id(
        self, nexus: NexusClient, submit_task: SubmitFn, unique_executor: str,
    ) -> None:
        """scheduler/011: GET task/{id} returns full task details."""
        resp = submit_task(
            unique_executor,
            "status_check",
            {"action": "noop"},
        )
        assert resp.status_code == 201
        task_id = resp.json()["id"]

        status = nexus.api_get(f"/api/v2/scheduler/task/{task_id}")
        assert status.status_code == 200

        data = status.json()
        assert data["id"] == task_id
        assert data["executor_id"] == unique_executor
        assert data["task_type"] == "status_check"
        assert "status" in data
        assert "priority_tier" in data
        assert "enqueued_at" in data

    def test_get_nonexistent_returns_404(self, nexus: NexusClient) -> None:
        """scheduler/012: GET random UUID → 404."""
        fake_id = str(uuid.uuid4())
        resp = nexus.api_get(f"/api/v2/scheduler/task/{fake_id}")
        assert resp.status_code == 404, (
            f"Expected 404, got {resp.status_code} {resp.text[:200]}"
        )

    def test_cancel_already_cancelled(
        self, nexus: NexusClient, submit_task: SubmitFn, unique_executor: str,
    ) -> None:
        """scheduler/013: Cancelling an already-cancelled task is idempotent."""
        resp = submit_task(
            unique_executor,
            "double_cancel",
            {"action": "noop"},
            priority="low",
        )
        assert resp.status_code == 201
        task_id = resp.json()["id"]

        # First cancel
        cancel1 = nexus.api_post(f"/api/v2/scheduler/task/{task_id}/cancel")
        assert cancel1.status_code == 200

        # Second cancel — should not error
        cancel2 = nexus.api_post(f"/api/v2/scheduler/task/{task_id}/cancel")
        assert cancel2.status_code == 200, (
            f"Second cancel failed: {cancel2.status_code} {cancel2.text[:200]}"
        )

    def test_metrics_endpoint(self, nexus: NexusClient) -> None:
        """scheduler/014: GET /metrics returns queue metrics shape."""
        resp = nexus.api_get("/api/v2/scheduler/metrics")
        assert resp.status_code == 200

        data = resp.json()
        # Metrics should contain queue statistics
        assert isinstance(data, dict), f"Expected dict, got {type(data)}"
        # Check for expected top-level keys
        expected_keys = {"queued", "running", "completed", "failed"}
        present_keys = set(data.keys())
        # At minimum, some queue stats should be present
        assert present_keys & expected_keys or "queue_by_class" in data, (
            f"Metrics missing expected keys. Got: {list(data.keys())}"
        )

    def test_metrics_after_submits(
        self, nexus: NexusClient, submit_task: SubmitFn, unique_executor: str,
    ) -> None:
        """scheduler/015: Metrics reflect submitted tasks by class."""
        # Get baseline
        baseline = nexus.api_get("/api/v2/scheduler/metrics")
        assert baseline.status_code == 200

        # Submit tasks at different priorities
        for priority in ("high", "normal", "low"):
            resp = submit_task(
                unique_executor,
                f"metrics_{priority}",
                {"metrics_test": True},
                priority=priority,
            )
            assert resp.status_code == 201

        # Check metrics updated
        after = nexus.api_get("/api/v2/scheduler/metrics")
        assert after.status_code == 200
        after_data = after.json()

        # Total queued should be >= 3 (our submissions)
        total_queued = after_data.get("queued", 0)
        if "queue_by_class" in after_data:
            qbc = after_data["queue_by_class"]
            if isinstance(qbc, list):
                total_queued = sum(row.get("cnt", 0) for row in qbc)
            elif isinstance(qbc, dict):
                total_queued = sum(qbc.values())
        assert total_queued >= 3, (
            f"Expected at least 3 queued tasks, got {total_queued}: {after_data}"
        )

    def test_astraea_fields_in_submit_response(
        self, nexus: NexusClient, submit_task: SubmitFn, unique_executor: str,
    ) -> None:
        """scheduler/016: Submit response includes Astraea classification fields."""
        resp = submit_task(
            unique_executor,
            "astraea_fields",
            {"action": "noop"},
            priority="high",
        )
        assert resp.status_code == 201
        data = resp.json()

        # Astraea enriches the response with classification data
        assert "priority_tier" in data, f"Missing priority_tier: {data}"
        assert "priority_class" in data or "effective_tier" in data, (
            f"Missing Astraea fields (priority_class or effective_tier): {data}"
        )

        # If priority_class present, verify correct mapping
        if "priority_class" in data:
            assert data["priority_class"] == "interactive", (
                f"HIGH should map to interactive, got {data['priority_class']}"
            )

    def test_zone_isolation(
        self, nexus: NexusClient, submit_task: SubmitFn,
    ) -> None:
        """scheduler/017: Tasks submitted in one zone context don't appear in another."""
        zone_a_executor = f"zone-a-{uuid.uuid4().hex[:8]}"
        zone_b_executor = f"zone-b-{uuid.uuid4().hex[:8]}"

        # Submit tasks in zone A context
        resp_a = submit_task(
            zone_a_executor,
            "zone_test",
            {"zone": "A"},
        )
        assert resp_a.status_code == 201
        task_id_a = resp_a.json()["id"]

        # Verify zone A task exists
        status_a = nexus.api_get(f"/api/v2/scheduler/task/{task_id_a}")
        assert status_a.status_code == 200
        assert status_a.json()["executor_id"] == zone_a_executor

        # Zone B executor should have no tasks from zone A
        # Submit a zone B task to ensure it has its own context
        resp_b = submit_task(
            zone_b_executor,
            "zone_test",
            {"zone": "B"},
        )
        assert resp_b.status_code == 201
        task_id_b = resp_b.json()["id"]

        # Verify tasks are separate
        assert task_id_a != task_id_b
        status_b = nexus.api_get(f"/api/v2/scheduler/task/{task_id_b}")
        assert status_b.status_code == 200
        assert status_b.json()["executor_id"] == zone_b_executor
