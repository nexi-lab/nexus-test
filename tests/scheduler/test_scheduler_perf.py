"""Scheduler performance benchmarks — latency SLOs in milliseconds.

Tests: scheduler/018-020
Covers: submit latency, status query latency, classify latency
SLOs: submit p95 < 200ms, status p95 < 100ms, classify p95 < 100ms

Reference: TEST_PLAN.md §4.5, docs/scheduler.md §Performance

Uses LatencyCollector from tests/helpers/data_generators.py for
nanosecond-precision timing with percentile statistics.
"""

from __future__ import annotations

import uuid

import pytest

from tests.helpers.api_client import NexusClient
from tests.helpers.data_generators import LatencyCollector
from tests.scheduler.conftest import SubmitFn


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Number of iterations for each benchmark
_SUBMIT_ITERATIONS = 20
_STATUS_ITERATIONS = 30
_CLASSIFY_ITERATIONS = 30

# SLO thresholds (milliseconds)
_SUBMIT_P95_MS = 200.0
_STATUS_P95_MS = 100.0
_CLASSIFY_P95_MS = 100.0


# ---------------------------------------------------------------------------
# scheduler/018-020: Performance benchmarks
# ---------------------------------------------------------------------------


@pytest.mark.perf
@pytest.mark.scheduler
class TestSchedulerPerformance:
    """Scheduler latency benchmarks with millisecond SLOs."""

    def test_submit_latency_p95(
        self, nexus: NexusClient, submit_task: SubmitFn,
    ) -> None:
        """scheduler/018: Submit task p95 latency < 200ms."""
        collector = LatencyCollector("scheduler_submit")

        for i in range(_SUBMIT_ITERATIONS):
            executor = f"perf-submit-{uuid.uuid4().hex[:8]}"
            with collector.measure():
                resp = submit_task(
                    executor,
                    f"perf_task_{i}",
                    {"perf_test": True, "iteration": i},
                )
            assert resp.status_code == 201, (
                f"Submit failed at iteration {i}: {resp.status_code} {resp.text[:200]}"
            )

        stats = collector.stats()
        print(
            f"\n  Submit latency ({stats.count} samples):"
            f"\n    p50={stats.p50_ms:.1f}ms"
            f"\n    p95={stats.p95_ms:.1f}ms"
            f"\n    p99={stats.p99_ms:.1f}ms"
            f"\n    min={stats.min_ms:.1f}ms"
            f"\n    max={stats.max_ms:.1f}ms"
            f"\n    mean={stats.mean_ms:.1f}ms"
        )
        assert stats.p95_ms < _SUBMIT_P95_MS, (
            f"Submit p95 {stats.p95_ms:.1f}ms exceeds SLO {_SUBMIT_P95_MS}ms"
        )

    def test_status_query_latency_p95(
        self, nexus: NexusClient, submit_task: SubmitFn, unique_executor: str,
    ) -> None:
        """scheduler/019: Status query p95 latency < 100ms."""
        # Create a task to query
        resp = submit_task(
            unique_executor,
            "perf_status_target",
            {"perf_test": True},
        )
        assert resp.status_code == 201
        task_id = resp.json()["id"]

        collector = LatencyCollector("scheduler_status")

        for _ in range(_STATUS_ITERATIONS):
            with collector.measure():
                status_resp = nexus.api_get(
                    f"/api/v2/scheduler/task/{task_id}",
                )
            assert status_resp.status_code == 200

        stats = collector.stats()
        print(
            f"\n  Status query latency ({stats.count} samples):"
            f"\n    p50={stats.p50_ms:.1f}ms"
            f"\n    p95={stats.p95_ms:.1f}ms"
            f"\n    p99={stats.p99_ms:.1f}ms"
            f"\n    min={stats.min_ms:.1f}ms"
            f"\n    max={stats.max_ms:.1f}ms"
            f"\n    mean={stats.mean_ms:.1f}ms"
        )
        assert stats.p95_ms < _STATUS_P95_MS, (
            f"Status p95 {stats.p95_ms:.1f}ms exceeds SLO {_STATUS_P95_MS}ms"
        )

    def test_classify_latency_p95(self, nexus: NexusClient) -> None:
        """scheduler/020: Classify endpoint p95 latency < 100ms."""
        collector = LatencyCollector("scheduler_classify")

        priorities = ["critical", "high", "normal", "low", "best_effort"]
        states = ["pending", "compute", "io_wait"]

        for i in range(_CLASSIFY_ITERATIONS):
            priority = priorities[i % len(priorities)]
            state = states[i % len(states)]
            with collector.measure():
                resp = nexus.api_post(
                    "/api/v2/scheduler/classify",
                    json={"priority": priority, "request_state": state},
                )
            assert resp.status_code == 200, (
                f"Classify failed: {resp.status_code} {resp.text[:200]}"
            )

        stats = collector.stats()
        print(
            f"\n  Classify latency ({stats.count} samples):"
            f"\n    p50={stats.p50_ms:.1f}ms"
            f"\n    p95={stats.p95_ms:.1f}ms"
            f"\n    p99={stats.p99_ms:.1f}ms"
            f"\n    min={stats.min_ms:.1f}ms"
            f"\n    max={stats.max_ms:.1f}ms"
            f"\n    mean={stats.mean_ms:.1f}ms"
        )
        assert stats.p95_ms < _CLASSIFY_P95_MS, (
            f"Classify p95 {stats.p95_ms:.1f}ms exceeds SLO {_CLASSIFY_P95_MS}ms"
        )
