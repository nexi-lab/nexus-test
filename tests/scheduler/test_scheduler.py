"""Scheduler E2E tests — task lifecycle, classification, metrics, cancellation.

Tests: scheduler/001-004
Covers: submit task, idempotency retry, priority ordering, task cancellation

Reference: TEST_PLAN.md §4.5, docs/scheduler.md

Infrastructure: PostgreSQL (full mode) or InMemory (fallback)

Scheduler API endpoints:
    POST /api/v2/scheduler/submit          — Submit a task
    GET  /api/v2/scheduler/task/{id}       — Get task status
    POST /api/v2/scheduler/task/{id}/cancel — Cancel a task
    GET  /api/v2/scheduler/metrics         — Queue metrics
    POST /api/v2/scheduler/classify        — Classify request priority
"""

from __future__ import annotations

import uuid

import pytest

from tests.helpers.api_client import NexusClient
from tests.scheduler.conftest import SubmitFn


def _unique_executor() -> str:
    """Generate a unique executor name for test isolation."""
    return f"sched-test-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# scheduler/001-004: Core lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.quick
@pytest.mark.auto
@pytest.mark.scheduler
class TestSchedulerLifecycle:
    """Scheduler task lifecycle tests."""

    def test_schedule_task(
        self, nexus: NexusClient, submit_task: SubmitFn, unique_executor: str,
    ) -> None:
        """scheduler/001: Submit task — returns ID, status, priority_tier."""
        resp = submit_task(
            unique_executor,
            "test_task",
            {"action": "noop", "test_id": uuid.uuid4().hex[:8]},
        )
        assert resp.status_code == 201, (
            f"Task submission failed: {resp.status_code} {resp.text[:200]}"
        )

        data = resp.json()
        assert "id" in data, f"Missing 'id': {data}"
        assert "status" in data, f"Missing 'status': {data}"
        assert "priority_tier" in data, f"Missing 'priority_tier': {data}"
        assert data["executor_id"] == unique_executor
        assert data["task_type"] == "test_task"
        assert data["status"] == "queued"

        # Round-trip: retrieve by ID
        status_resp = nexus.api_get(f"/api/v2/scheduler/task/{data['id']}")
        assert status_resp.status_code == 200

    def test_task_retry_with_idempotency(
        self, nexus: NexusClient, submit_task: SubmitFn, unique_executor: str,
    ) -> None:
        """scheduler/002: Idempotency key — re-submit returns same task or 409."""
        idempotency_key = f"retry-{uuid.uuid4().hex[:8]}"

        resp1 = submit_task(
            unique_executor,
            "retryable_task",
            {"attempt": 1},
            idempotency_key=idempotency_key,
        )
        assert resp1.status_code == 201, f"First submission failed: {resp1.text[:200]}"
        task_id_1 = resp1.json()["id"]

        # Re-submit with same idempotency key
        resp2 = submit_task(
            unique_executor,
            "retryable_task",
            {"attempt": 2},
            idempotency_key=idempotency_key,
        )
        assert resp2.status_code in (200, 201, 409), (
            f"Retry unexpected: {resp2.status_code} {resp2.text[:200]}"
        )
        if resp2.status_code in (200, 201):
            task_id_2 = resp2.json()["id"]
            assert task_id_2 == task_id_1, "Idempotent retry should return same task ID"

    def test_task_priority_ordering(
        self, nexus: NexusClient, submit_task: SubmitFn, unique_executor: str,
    ) -> None:
        """scheduler/003: Priority tiers — each task stores correct tier."""
        priorities = ["low", "normal", "high", "critical"]
        task_ids: dict[str, str] = {}

        for priority in priorities:
            resp = submit_task(
                unique_executor,
                f"priority_{priority}",
                {"priority_test": True},
                priority=priority,
            )
            assert resp.status_code == 201, f"Submit {priority} failed: {resp.text[:200]}"
            task_ids[priority] = resp.json()["id"]

        # Verify each task has correct priority_tier
        for priority, task_id in task_ids.items():
            status = nexus.api_get(f"/api/v2/scheduler/task/{task_id}")
            assert status.status_code == 200
            assert status.json()["priority_tier"] == priority

    def test_task_cancellation(
        self, nexus: NexusClient, submit_task: SubmitFn, unique_executor: str,
    ) -> None:
        """scheduler/004: Cancel task — status transitions to cancelled."""
        resp = submit_task(
            unique_executor,
            "cancellable_task",
            {"long_running": True},
            priority="low",
        )
        assert resp.status_code == 201
        task_id = resp.json()["id"]

        cancel_resp = nexus.api_post(f"/api/v2/scheduler/task/{task_id}/cancel")
        assert cancel_resp.status_code == 200
        cancel_data = cancel_resp.json()
        assert "cancelled" in cancel_data
        assert cancel_data["task_id"] == task_id

        if cancel_data["cancelled"]:
            status = nexus.api_get(f"/api/v2/scheduler/task/{task_id}")
            assert status.status_code == 200
            assert status.json()["status"] in ("cancelled", "completed", "failed")
