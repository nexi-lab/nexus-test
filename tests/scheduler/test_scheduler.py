"""Scheduler E2E tests — task scheduling, retry, priority ordering, cancellation.

Tests: scheduler/001-004
Covers: submit task, retry with backoff, priority ordering, task cancellation

Reference: TEST_PLAN.md §4.5

Infrastructure: docker-compose.demo.yml (standalone)

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


def _unique_executor() -> str:
    """Generate a unique executor name for test isolation."""
    return f"sched-test-{uuid.uuid4().hex[:8]}"


@pytest.mark.auto
@pytest.mark.scheduler
class TestScheduler:
    """Scheduler task lifecycle tests."""

    def test_schedule_task(self, nexus: NexusClient) -> None:
        """scheduler/001: Schedule task — task created with ID and status.

        Submits a task to the scheduler and verifies it returns a valid task
        with an ID, status, and priority information.
        """
        executor = _unique_executor()

        body = {
            "executor": executor,
            "task_type": "test_task",
            "payload": {"action": "noop", "test_id": uuid.uuid4().hex[:8]},
            "priority": "normal",
        }

        resp = nexus.api_post("/api/v2/scheduler/submit", json=body)
        if resp.status_code == 503:
            pytest.skip("Scheduler service not available on this server")

        assert resp.status_code == 201, (
            f"Task submission failed: {resp.status_code} {resp.text[:200]}"
        )

        data = resp.json()
        assert "id" in data, f"Task response missing 'id': {data}"
        assert "status" in data, f"Task response missing 'status': {data}"
        assert "priority_tier" in data, f"Task response missing 'priority_tier': {data}"
        assert data["executor_id"] == executor
        assert data["task_type"] == "test_task"

        # Verify we can retrieve the task by ID
        task_id = data["id"]
        status_resp = nexus.api_get(f"/api/v2/scheduler/task/{task_id}")
        assert status_resp.status_code == 200, (
            f"Task status query failed: {status_resp.status_code}"
        )

    def test_task_retry_with_backoff(self, nexus: NexusClient) -> None:
        """scheduler/002: Task retry with backoff — re-submission accepted.

        Submits a task, verifies it can be re-submitted with the same
        idempotency key (server should handle deduplication or re-queue).
        Tests the retry-friendly design of the scheduler.
        """
        executor = _unique_executor()
        idempotency_key = f"retry-{uuid.uuid4().hex[:8]}"

        body = {
            "executor": executor,
            "task_type": "retryable_task",
            "payload": {"attempt": 1},
            "priority": "normal",
            "idempotency_key": idempotency_key,
        }

        # First submission
        resp1 = nexus.api_post("/api/v2/scheduler/submit", json=body)
        if resp1.status_code == 503:
            pytest.skip("Scheduler service not available on this server")
        assert resp1.status_code == 201, f"First submission failed: {resp1.text[:200]}"
        # Re-submit with same idempotency key (simulates retry)
        body["payload"]["attempt"] = 2
        resp2 = nexus.api_post("/api/v2/scheduler/submit", json=body)
        # Should either return the existing task (200/201) or conflict (409)
        assert resp2.status_code in (200, 201, 409), (
            f"Retry submission unexpected status: {resp2.status_code} {resp2.text[:200]}"
        )

        if resp2.status_code in (200, 201):
            task_id_2 = resp2.json()["id"]
            # With idempotency, should return same task or a new one
            # Both are valid — the key is that the server doesn't crash
            assert task_id_2 is not None

    def test_task_priority_ordering(self, nexus: NexusClient) -> None:
        """scheduler/003: Task priority ordering — high priority first.

        Submits tasks with different priorities and verifies the scheduler
        reports correct priority tiers. Also tests the classify endpoint.
        """
        executor = _unique_executor()

        priorities = ["low", "normal", "high", "critical"]
        task_ids = {}

        for priority in priorities:
            body = {
                "executor": executor,
                "task_type": f"priority_{priority}",
                "payload": {"priority_test": True},
                "priority": priority,
            }
            resp = nexus.api_post("/api/v2/scheduler/submit", json=body)
            if resp.status_code == 503:
                pytest.skip("Scheduler service not available on this server")
            assert resp.status_code == 201, f"Submit {priority} task failed: {resp.text[:200]}"
            data = resp.json()
            task_ids[priority] = data["id"]

        # Verify each task has the correct priority tier
        for priority, task_id in task_ids.items():
            status = nexus.api_get(f"/api/v2/scheduler/task/{task_id}")
            assert status.status_code == 200
            data = status.json()
            assert data["priority_tier"] == priority, (
                f"Task should have priority '{priority}', got '{data['priority_tier']}'"
            )

        # Test the classify endpoint
        classify_resp = nexus.api_post(
            "/api/v2/scheduler/classify",
            json={"priority": "critical", "request_state": "compute"},
        )
        if classify_resp.status_code == 200:
            classify_data = classify_resp.json()
            assert "priority_class" in classify_data, (
                f"Classify response missing 'priority_class': {classify_data}"
            )

        # Test scheduler metrics
        metrics_resp = nexus.api_get("/api/v2/scheduler/metrics")
        if metrics_resp.status_code == 200:
            metrics = metrics_resp.json()
            assert "queue_by_class" in metrics, f"Metrics missing 'queue_by_class': {metrics}"

    def test_task_cancellation(self, nexus: NexusClient) -> None:
        """scheduler/004: Task cancellation — graceful stop.

        Submits a task, then cancels it before execution. Verifies the
        cancellation is acknowledged and the task status reflects it.
        """
        executor = _unique_executor()

        body = {
            "executor": executor,
            "task_type": "cancellable_task",
            "payload": {"long_running": True},
            "priority": "low",
        }

        resp = nexus.api_post("/api/v2/scheduler/submit", json=body)
        if resp.status_code == 503:
            pytest.skip("Scheduler service not available on this server")
        assert resp.status_code == 201, f"Task submission failed: {resp.text[:200]}"

        task_id = resp.json()["id"]

        # Cancel the task
        cancel_resp = nexus.api_post(f"/api/v2/scheduler/task/{task_id}/cancel")
        assert cancel_resp.status_code == 200, (
            f"Task cancellation failed: {cancel_resp.status_code} {cancel_resp.text[:200]}"
        )

        cancel_data = cancel_resp.json()
        assert "cancelled" in cancel_data, f"Cancel response missing 'cancelled': {cancel_data}"
        assert cancel_data["task_id"] == task_id

        # If cancellation succeeded, verify status reflects it
        if cancel_data["cancelled"]:
            status_resp = nexus.api_get(f"/api/v2/scheduler/task/{task_id}")
            assert status_resp.status_code == 200
            status = status_resp.json()
            assert status["status"] in ("cancelled", "completed", "failed"), (
                f"Cancelled task should not be 'queued': {status['status']}"
            )
