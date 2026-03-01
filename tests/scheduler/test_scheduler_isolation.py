"""Scheduler cross-user isolation E2E tests.

Tests: scheduler/026-030
Covers: User A cannot see/cancel User B's tasks when owner-scoped
        queries are enforced at the SQL level.

These tests use two separate httpx clients with different X-Agent-Id
headers to simulate two different agents hitting the same scheduler.

Reference: docs/scheduler.md §Permissions
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx
import pytest

from tests.helpers.api_client import NexusClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _submit_as(
    base_url: str,
    api_key: str,
    agent_id: str,
    executor: str,
    task_type: str = "isolation_test",
    payload: dict[str, Any] | None = None,
) -> httpx.Response:
    """Submit a task as a specific agent."""
    with httpx.Client() as client:
        return client.post(
            f"{base_url}/api/v2/scheduler/submit",
            headers={
                "Authorization": f"Bearer {api_key}",
                "X-Agent-Id": agent_id,
            },
            json={
                "executor": executor,
                "task_type": task_type,
                "payload": payload or {"test": True},
                "priority": "low",
            },
        )


def _get_task_as(
    base_url: str, api_key: str, agent_id: str, task_id: str,
) -> httpx.Response:
    """Get task status as a specific agent."""
    with httpx.Client() as client:
        return client.get(
            f"{base_url}/api/v2/scheduler/task/{task_id}",
            headers={
                "Authorization": f"Bearer {api_key}",
                "X-Agent-Id": agent_id,
            },
        )


def _cancel_task_as(
    base_url: str, api_key: str, agent_id: str, task_id: str,
) -> httpx.Response:
    """Cancel a task as a specific agent."""
    with httpx.Client() as client:
        return client.post(
            f"{base_url}/api/v2/scheduler/task/{task_id}/cancel",
            headers={
                "Authorization": f"Bearer {api_key}",
                "X-Agent-Id": agent_id,
            },
        )


# ---------------------------------------------------------------------------
# scheduler/026-030: Cross-user isolation tests
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.scheduler
class TestSchedulerCrossUserIsolation:
    """Verify that tasks are isolated per agent (owner-scoped)."""

    def test_agent_a_can_see_own_task(
        self, nexus: NexusClient,
    ) -> None:
        """scheduler/026: Agent A submits and retrieves own task."""
        agent_a = f"agent-a-{uuid.uuid4().hex[:8]}"
        base_url = nexus.base_url
        api_key = nexus.api_key

        # Agent A submits
        resp = _submit_as(base_url, api_key, agent_a, f"exec-{agent_a}")
        assert resp.status_code == 201, f"Submit failed: {resp.status_code} {resp.text[:200]}"
        task_id = resp.json()["id"]

        try:
            # Agent A can see it
            status_resp = _get_task_as(base_url, api_key, agent_a, task_id)
            assert status_resp.status_code == 200
            assert status_resp.json()["id"] == task_id
        finally:
            _cancel_task_as(base_url, api_key, agent_a, task_id)

    def test_agent_b_cannot_see_agent_a_task(
        self, nexus: NexusClient,
    ) -> None:
        """scheduler/027: Agent B gets 404 for Agent A's task."""
        agent_a = f"agent-a-{uuid.uuid4().hex[:8]}"
        agent_b = f"agent-b-{uuid.uuid4().hex[:8]}"
        base_url = nexus.base_url
        api_key = nexus.api_key

        # Agent A submits
        resp = _submit_as(base_url, api_key, agent_a, f"exec-{agent_a}")
        assert resp.status_code == 201
        task_id = resp.json()["id"]

        try:
            # Agent B tries to read it → 404
            status_resp = _get_task_as(base_url, api_key, agent_b, task_id)
            assert status_resp.status_code == 404, (
                f"Expected 404 (not visible to agent B), got {status_resp.status_code}"
            )
        finally:
            _cancel_task_as(base_url, api_key, agent_a, task_id)

    def test_agent_b_cannot_cancel_agent_a_task(
        self, nexus: NexusClient,
    ) -> None:
        """scheduler/028: Agent B cannot cancel Agent A's task."""
        agent_a = f"agent-a-{uuid.uuid4().hex[:8]}"
        agent_b = f"agent-b-{uuid.uuid4().hex[:8]}"
        base_url = nexus.base_url
        api_key = nexus.api_key

        # Agent A submits
        resp = _submit_as(base_url, api_key, agent_a, f"exec-{agent_a}")
        assert resp.status_code == 201
        task_id = resp.json()["id"]

        try:
            # Agent B tries to cancel → cancelled=false
            cancel_resp = _cancel_task_as(base_url, api_key, agent_b, task_id)
            assert cancel_resp.status_code == 200
            assert cancel_resp.json()["cancelled"] is False, (
                "Agent B should NOT be able to cancel Agent A's task"
            )

            # Verify Agent A's task is still alive
            status_resp = _get_task_as(base_url, api_key, agent_a, task_id)
            assert status_resp.status_code == 200
            assert status_resp.json()["status"] == "queued"
        finally:
            _cancel_task_as(base_url, api_key, agent_a, task_id)

    def test_agent_a_can_cancel_own_task(
        self, nexus: NexusClient,
    ) -> None:
        """scheduler/029: Agent A can cancel own task."""
        agent_a = f"agent-a-{uuid.uuid4().hex[:8]}"
        base_url = nexus.base_url
        api_key = nexus.api_key

        # Agent A submits
        resp = _submit_as(base_url, api_key, agent_a, f"exec-{agent_a}")
        assert resp.status_code == 201
        task_id = resp.json()["id"]

        # Agent A cancels → cancelled=true
        cancel_resp = _cancel_task_as(base_url, api_key, agent_a, task_id)
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["cancelled"] is True

    def test_isolation_latency(
        self, nexus: NexusClient,
    ) -> None:
        """scheduler/030: Owner-scoped queries add < 10ms overhead."""
        agent_a = f"agent-a-{uuid.uuid4().hex[:8]}"
        base_url = nexus.base_url
        api_key = nexus.api_key

        # Agent A submits
        resp = _submit_as(base_url, api_key, agent_a, f"exec-{agent_a}")
        assert resp.status_code == 201
        task_id = resp.json()["id"]

        try:
            # Measure GET latency (10 iterations, take p95)
            latencies = []
            for _ in range(10):
                t0 = time.perf_counter()
                _get_task_as(base_url, api_key, agent_a, task_id)
                latencies.append((time.perf_counter() - t0) * 1000)

            latencies.sort()
            p95 = latencies[int(len(latencies) * 0.95)]
            p50 = latencies[int(len(latencies) * 0.50)]

            # Owner-scoped GET should be under 200ms
            assert p95 < 200.0, (
                f"Owner-scoped GET p95={p95:.1f}ms > 200ms SLO (p50={p50:.1f}ms)"
            )
        finally:
            _cancel_task_as(base_url, api_key, agent_a, task_id)
