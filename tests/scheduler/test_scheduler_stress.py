"""Scheduler stress tests — concurrent submission and burst+cancel cycles.

Tests: scheduler/021-022
Covers: concurrent task submission, burst+cancel lifecycle

Reference: TEST_PLAN.md §4.5, docs/scheduler.md §Stress

Uses ThreadPoolExecutor for concurrent HTTP submissions to test
scheduler behavior under load.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from tests.helpers.api_client import NexusClient


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONCURRENT_TASKS = 20
_BURST_SIZE = 10
_MAX_WORKERS = 5


# ---------------------------------------------------------------------------
# scheduler/021-022: Stress tests
# ---------------------------------------------------------------------------


@pytest.mark.stress
@pytest.mark.scheduler
class TestSchedulerStress:
    """Scheduler stress and concurrency tests."""

    def test_concurrent_submit(self, nexus: NexusClient) -> None:
        """scheduler/021: Submit 20 tasks concurrently — all succeed."""
        executor_base = f"stress-{uuid.uuid4().hex[:8]}"
        created_ids: list[str] = []
        errors: list[str] = []

        def _submit_one(idx: int) -> tuple[int, str | None, str | None]:
            """Submit a single task, return (idx, task_id, error)."""
            try:
                resp = nexus.api_post(
                    "/api/v2/scheduler/submit",
                    json={
                        "executor": f"{executor_base}-{idx}",
                        "task_type": f"stress_task_{idx}",
                        "payload": {"stress": True, "index": idx},
                        "priority": "normal",
                    },
                )
                if resp.status_code == 201:
                    return (idx, resp.json()["id"], None)
                return (idx, None, f"status={resp.status_code}: {resp.text[:100]}")
            except Exception as exc:
                return (idx, None, str(exc))

        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
            futures = {
                pool.submit(_submit_one, i): i
                for i in range(_CONCURRENT_TASKS)
            }
            for future in as_completed(futures):
                idx, task_id, error = future.result()
                if task_id:
                    created_ids.append(task_id)
                if error:
                    errors.append(f"Task {idx}: {error}")

        # Cleanup: cancel all created tasks (best-effort)
        for tid in created_ids:
            try:
                nexus.api_post(f"/api/v2/scheduler/task/{tid}/cancel")
            except Exception:
                pass

        # Assertions
        assert len(errors) == 0, (
            f"{len(errors)}/{_CONCURRENT_TASKS} submissions failed:\n"
            + "\n".join(errors[:10])
        )
        assert len(created_ids) == _CONCURRENT_TASKS, (
            f"Expected {_CONCURRENT_TASKS} tasks, got {len(created_ids)}"
        )

        # Verify all task IDs are unique
        assert len(set(created_ids)) == _CONCURRENT_TASKS, (
            "Duplicate task IDs detected in concurrent submission"
        )

    def test_burst_cancel_cycle(self, nexus: NexusClient) -> None:
        """scheduler/022: Submit burst of 10, cancel all, verify clean state."""
        executor = f"burst-{uuid.uuid4().hex[:8]}"
        created_ids: list[str] = []

        # Phase 1: Submit burst
        for i in range(_BURST_SIZE):
            resp = nexus.api_post(
                "/api/v2/scheduler/submit",
                json={
                    "executor": executor,
                    "task_type": f"burst_task_{i}",
                    "payload": {"burst": True, "index": i},
                    "priority": "low",
                },
            )
            assert resp.status_code == 201, (
                f"Burst submit {i} failed: {resp.status_code} {resp.text[:200]}"
            )
            created_ids.append(resp.json()["id"])

        assert len(created_ids) == _BURST_SIZE

        # Phase 2: Cancel all
        cancel_results: list[tuple[str, bool]] = []
        for tid in created_ids:
            cancel_resp = nexus.api_post(
                f"/api/v2/scheduler/task/{tid}/cancel",
            )
            assert cancel_resp.status_code == 200, (
                f"Cancel {tid} failed: {cancel_resp.status_code}"
            )
            cancel_data = cancel_resp.json()
            cancel_results.append((tid, cancel_data.get("cancelled", False)))

        # Phase 3: Verify all tasks are in terminal state
        for tid in created_ids:
            status = nexus.api_get(f"/api/v2/scheduler/task/{tid}")
            assert status.status_code == 200
            task_status = status.json()["status"]
            assert task_status in ("cancelled", "completed", "failed"), (
                f"Task {tid} in unexpected state: {task_status}"
            )
