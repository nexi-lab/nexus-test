"""Scheduler test fixtures — service gate, task factory, latency helpers.

Provides:
    - Module-scoped gate: skip all if scheduler not available
    - scheduler_backend: detected backend ("postgresql" or "in_memory")
    - submit_task: function-scoped factory with auto-cleanup
    - unique_executor: UUID-based executor name for isolation
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from typing import Any, Protocol

import httpx
import pytest

from tests.helpers.api_client import NexusClient


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class SubmitFn(Protocol):
    """Callable signature for submit_task fixture."""

    def __call__(
        self,
        executor: str,
        task_type: str,
        payload: dict[str, Any],
        *,
        priority: str = "normal",
        **kw: Any,
    ) -> httpx.Response: ...


# ---------------------------------------------------------------------------
# Module-scoped gates
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _scheduler_available(nexus: NexusClient) -> None:
    """Skip all scheduler tests if service not available."""
    resp = nexus.api_post(
        "/api/v2/scheduler/submit",
        json={
            "executor": f"probe-{uuid.uuid4().hex[:6]}",
            "task_type": "health_probe",
            "payload": {},
            "priority": "low",
        },
    )
    if resp.status_code == 503:
        pytest.skip("Scheduler service not available on this server")
    # Clean up probe task
    if resp.status_code == 201:
        task_id = resp.json().get("id")
        if task_id:
            nexus.api_post(f"/api/v2/scheduler/task/{task_id}/cancel")


@pytest.fixture(scope="module")
def scheduler_backend(nexus: NexusClient) -> str:
    """Detect scheduler backend: 'postgresql' or 'in_memory'.

    Checks the metrics endpoint for HRRN support indicator.
    """
    resp = nexus.api_get("/api/v2/scheduler/metrics")
    if resp.status_code != 200:
        return "unknown"
    data = resp.json()
    if data.get("use_hrrn") is True:
        return "postgresql"
    return "in_memory"


# ---------------------------------------------------------------------------
# Function-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def unique_executor() -> str:
    """Generate a unique executor name for test isolation."""
    return f"sched-test-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def submit_task(nexus: NexusClient) -> Generator[SubmitFn, None, None]:
    """Factory that submits tasks and auto-cancels them on teardown."""
    created_ids: list[str] = []

    def _submit(
        executor: str,
        task_type: str,
        payload: dict[str, Any],
        *,
        priority: str = "normal",
        **kw: Any,
    ) -> httpx.Response:
        body: dict[str, Any] = {
            "executor": executor,
            "task_type": task_type,
            "payload": payload,
            "priority": priority,
            **kw,
        }
        resp = nexus.api_post("/api/v2/scheduler/submit", json=body)
        if resp.status_code == 201:
            task_id = resp.json().get("id")
            if task_id:
                created_ids.append(task_id)
        return resp

    yield _submit

    # Teardown: cancel all created tasks (best-effort)
    for tid in reversed(created_ids):
        try:
            nexus.api_post(f"/api/v2/scheduler/task/{tid}/cancel")
        except Exception:
            pass
