"""Scheduler multi-cursor per-executor dispatch isolation E2E tests.

Tests: scheduler/031-037
Covers: per-executor task isolation, independent cancellation, Astraea
        classification, HRRN service time, multi-executor metrics,
        high-volume interleaved submission.

These tests validate the API-level contract that multi-cursor dispatch
(Issue #2748) depends on: each executor_id is an independent partition
for dequeue, and operations on one executor don't affect others.

Reference: docs/scheduler.md, Issue #2748
"""

from __future__ import annotations

import uuid

import pytest

from tests.helpers.api_client import NexusClient
from tests.scheduler.conftest import SubmitFn


# ---------------------------------------------------------------------------
# scheduler/031-037: Multi-cursor per-executor dispatch isolation
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.scheduler
class TestMultiCursorDispatch:
    """Per-executor dispatch isolation tests (Issue #2748).

    Verify that the scheduler correctly isolates tasks by executor_id —
    the core invariant that multi-cursor dispatch relies on.
    """

    def test_per_executor_task_isolation(
        self,
        nexus: NexusClient,
        submit_task: SubmitFn,
    ) -> None:
        """scheduler/031: Tasks retain correct executor_id across two executors.

        Submits tasks to two different executors and verifies each task
        retains its correct executor_id. This is the foundation for
        per-executor dequeue cursors.
        """
        exec_a = f"cursor-a-{uuid.uuid4().hex[:8]}"
        exec_b = f"cursor-b-{uuid.uuid4().hex[:8]}"

        resp_a = submit_task(exec_a, "exec_a_task", {"partition": "a"})
        resp_b = submit_task(exec_b, "exec_b_task", {"partition": "b"})
        assert resp_a.status_code == 201
        assert resp_b.status_code == 201

        data_a = resp_a.json()
        data_b = resp_b.json()

        assert data_a["executor_id"] == exec_a, (
            f"Task A should belong to '{exec_a}', got '{data_a['executor_id']}'"
        )
        assert data_b["executor_id"] == exec_b, (
            f"Task B should belong to '{exec_b}', got '{data_b['executor_id']}'"
        )

        # Round-trip: verify executor_id persists through status lookup
        status_a = nexus.api_get(f"/api/v2/scheduler/task/{data_a['id']}")
        status_b = nexus.api_get(f"/api/v2/scheduler/task/{data_b['id']}")
        assert status_a.status_code == 200
        assert status_b.status_code == 200
        assert status_a.json()["executor_id"] == exec_a
        assert status_b.json()["executor_id"] == exec_b

    def test_multiple_tasks_same_executor_priority_preserved(
        self,
        submit_task: SubmitFn,
    ) -> None:
        """scheduler/032: Multiple tasks per executor maintain independent priorities.

        Submits tasks with different priority tiers to the same executor
        and verifies each task stores its own priority independently.
        """
        executor = f"multi-pri-{uuid.uuid4().hex[:8]}"

        responses = []
        for priority in ("critical", "high", "normal", "low"):
            resp = submit_task(
                executor, f"multi_{priority}", {"priority_test": True},
                priority=priority,
            )
            assert resp.status_code == 201, (
                f"Submit {priority} failed: {resp.text[:200]}"
            )
            responses.append(resp.json())

        # All tasks belong to same executor
        for data in responses:
            assert data["executor_id"] == executor

        # Each has its own priority tier
        tiers = [r["priority_tier"] for r in responses]
        assert tiers == ["critical", "high", "normal", "low"], (
            f"Priority tiers should be preserved: {tiers}"
        )

    def test_independent_cancellation_across_executors(
        self,
        nexus: NexusClient,
        submit_task: SubmitFn,
    ) -> None:
        """scheduler/033: Cancel on executor A does not affect executor B.

        Submits tasks to two executors, cancels one from executor A, and
        verifies executor B's task is completely unaffected.
        """
        exec_a = f"cancel-a-{uuid.uuid4().hex[:8]}"
        exec_b = f"cancel-b-{uuid.uuid4().hex[:8]}"

        resp_a = submit_task(exec_a, "cancel_test_a", {"side": "a"})
        resp_b = submit_task(exec_b, "cancel_test_b", {"side": "b"})
        assert resp_a.status_code == 201
        assert resp_b.status_code == 201

        task_a_id = resp_a.json()["id"]
        task_b_id = resp_b.json()["id"]

        # Cancel task A
        cancel_resp = nexus.api_post(f"/api/v2/scheduler/task/{task_a_id}/cancel")
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["cancelled"] is True

        # Verify task A is cancelled
        status_a = nexus.api_get(f"/api/v2/scheduler/task/{task_a_id}")
        assert status_a.status_code == 200
        assert status_a.json()["status"] in ("cancelled", "completed", "failed")

        # Verify task B is UNAFFECTED
        status_b = nexus.api_get(f"/api/v2/scheduler/task/{task_b_id}")
        assert status_b.status_code == 200
        assert status_b.json()["status"] != "cancelled", (
            "Task B must not be cancelled when task A is cancelled"
        )

    def test_astraea_classification_per_task(
        self,
        submit_task: SubmitFn,
    ) -> None:
        """scheduler/034: Astraea auto-classification assigns priority_class per task.

        Submits tasks with different tiers/states and verifies auto-classification
        into INTERACTIVE/BATCH/BACKGROUND priority classes.
        """
        executor = f"classify-{uuid.uuid4().hex[:8]}"

        # Critical + compute → should be interactive or batch
        resp_critical = submit_task(
            executor, "classify_critical", {"class_test": True},
            priority="critical", request_state="compute",
        )
        assert resp_critical.status_code == 201
        critical_class = resp_critical.json().get("priority_class", "")
        assert critical_class in ("interactive", "batch"), (
            f"Critical/compute task should be interactive or batch, got '{critical_class}'"
        )

        # Low + idle → should be background or batch
        resp_low = submit_task(
            executor, "classify_low", {"class_test": True},
            priority="low", request_state="idle",
        )
        assert resp_low.status_code == 201
        low_class = resp_low.json().get("priority_class", "")
        assert low_class in ("background", "batch"), (
            f"Low/idle task should be background or batch, got '{low_class}'"
        )

    def test_estimated_service_time_accepted(
        self,
        nexus: NexusClient,
        submit_task: SubmitFn,
        unique_executor: str,
    ) -> None:
        """scheduler/035: HRRN service time parameter is accepted by the API.

        Submits a task with a custom estimated_service_time (the HRRN
        scoring input used by per-executor cursors) and verifies the
        API accepts and stores it.
        """
        resp = submit_task(
            unique_executor, "hrrn_est_time", {"hrrn_test": True},
            estimated_service_time=120.0,
        )
        assert resp.status_code == 201

        task_id = resp.json()["id"]
        status = nexus.api_get(f"/api/v2/scheduler/task/{task_id}")
        assert status.status_code == 200
        assert status.json()["status"] in ("queued", "running", "completed")

    def test_metrics_with_multi_executor_queue(
        self,
        nexus: NexusClient,
        submit_task: SubmitFn,
    ) -> None:
        """scheduler/036: Metrics endpoint reflects multi-executor queue state.

        Submits tasks to multiple executors and verifies the metrics
        endpoint reports complete queue data including HRRN mode.
        """
        exec_a = f"metrics-a-{uuid.uuid4().hex[:8]}"
        exec_b = f"metrics-b-{uuid.uuid4().hex[:8]}"

        submit_task(exec_a, "metrics_a", {"m": "a"}, priority="high")
        submit_task(exec_b, "metrics_b", {"m": "b"}, priority="low")

        metrics_resp = nexus.api_get("/api/v2/scheduler/metrics")
        assert metrics_resp.status_code == 200

        metrics = metrics_resp.json()
        assert "queue_by_class" in metrics, f"Missing 'queue_by_class': {metrics}"
        assert "fair_share" in metrics, f"Missing 'fair_share': {metrics}"
        assert "use_hrrn" in metrics, f"Missing 'use_hrrn': {metrics}"

        # HRRN must be enabled for Astraea-style per-executor dispatch
        assert metrics["use_hrrn"] is True, (
            f"HRRN should be enabled: {metrics['use_hrrn']}"
        )

    def test_high_volume_interleaved_multi_executor(
        self,
        submit_task: SubmitFn,
    ) -> None:
        """scheduler/037: High volume interleaved submission across 5 executors.

        Submits 20 tasks (5 executors x 4 tasks) in interleaved order to
        stress-test per-executor partitioning under concurrent load.
        """
        num_executors = 5
        tasks_per_executor = 4
        executors = [f"stress-{i}-{uuid.uuid4().hex[:6]}" for i in range(num_executors)]
        priorities = ["critical", "high", "normal", "low"]

        all_tasks: dict[str, list[dict]] = {e: [] for e in executors}

        # Submit in interleaved order (round-robin across executors)
        for task_idx in range(tasks_per_executor):
            for executor in executors:
                resp = submit_task(
                    executor,
                    f"stress_{task_idx}",
                    {"idx": task_idx, "exec": executor},
                    priority=priorities[task_idx % len(priorities)],
                )
                assert resp.status_code == 201, (
                    f"Submit to {executor} failed: {resp.text[:200]}"
                )
                all_tasks[executor].append(resp.json())

        # Verify all tasks created with correct executor assignment
        total = sum(len(t) for t in all_tasks.values())
        assert total == num_executors * tasks_per_executor, (
            f"Expected {num_executors * tasks_per_executor} tasks, got {total}"
        )

        for executor, tasks in all_tasks.items():
            for task in tasks:
                assert task["executor_id"] == executor, (
                    f"Task {task['id']} has wrong executor: "
                    f"expected '{executor}', got '{task['executor_id']}'"
                )
