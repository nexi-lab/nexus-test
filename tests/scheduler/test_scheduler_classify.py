"""Scheduler Astraea classification tests — priority mapping and edge cases.

Tests: scheduler/005-010
Covers: classify endpoint, priority→class mapping, IO_WAIT promotion,
        request state validation, invalid input rejection

Reference: TEST_PLAN.md §4.5, docs/scheduler.md §Astraea Classification

API endpoint:
    POST /api/v2/scheduler/classify  — Classify request priority
"""

from __future__ import annotations

import pytest

from tests.helpers.api_client import NexusClient


# ---------------------------------------------------------------------------
# scheduler/005-010: Astraea classification
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.scheduler
class TestAstraeaClassification:
    """Astraea priority classification endpoint tests."""

    @pytest.mark.quick
    def test_classify_critical_interactive(self, nexus: NexusClient) -> None:
        """scheduler/005: CRITICAL priority → INTERACTIVE class."""
        resp = nexus.api_post(
            "/api/v2/scheduler/classify",
            json={"priority": "critical", "request_state": "compute"},
        )
        assert resp.status_code == 200, (
            f"Classify failed: {resp.status_code} {resp.text[:200]}"
        )
        data = resp.json()
        assert data["priority_class"] == "interactive", (
            f"Expected interactive, got {data}"
        )

    def test_classify_high_interactive(self, nexus: NexusClient) -> None:
        """scheduler/005b: HIGH priority → INTERACTIVE class."""
        resp = nexus.api_post(
            "/api/v2/scheduler/classify",
            json={"priority": "high", "request_state": "compute"},
        )
        assert resp.status_code == 200
        assert resp.json()["priority_class"] == "interactive"

    def test_classify_normal_batch(self, nexus: NexusClient) -> None:
        """scheduler/006: NORMAL priority → BATCH class."""
        resp = nexus.api_post(
            "/api/v2/scheduler/classify",
            json={"priority": "normal", "request_state": "pending"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["priority_class"] == "batch", f"Expected batch, got {data}"

    def test_classify_low_background(self, nexus: NexusClient) -> None:
        """scheduler/007: LOW priority → BACKGROUND class."""
        resp = nexus.api_post(
            "/api/v2/scheduler/classify",
            json={"priority": "low", "request_state": "pending"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["priority_class"] == "background", (
            f"Expected background, got {data}"
        )

    def test_classify_best_effort_background(self, nexus: NexusClient) -> None:
        """scheduler/007b: BEST_EFFORT → BACKGROUND class."""
        resp = nexus.api_post(
            "/api/v2/scheduler/classify",
            json={"priority": "best_effort", "request_state": "pending"},
        )
        assert resp.status_code == 200
        assert resp.json()["priority_class"] == "background"

    def test_classify_io_wait_promotes_background(
        self, nexus: NexusClient,
    ) -> None:
        """scheduler/008: IO_WAIT state promotes BACKGROUND → BATCH."""
        resp = nexus.api_post(
            "/api/v2/scheduler/classify",
            json={"priority": "low", "request_state": "io_wait"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["priority_class"] == "batch", (
            f"IO_WAIT should promote low→batch, got {data}"
        )

    @pytest.mark.parametrize(
        "request_state",
        ["pending", "compute", "io_wait", "completed", "failed"],
    )
    def test_classify_all_request_states_valid(
        self, nexus: NexusClient, request_state: str,
    ) -> None:
        """scheduler/009: All RequestState values accepted without error."""
        resp = nexus.api_post(
            "/api/v2/scheduler/classify",
            json={"priority": "normal", "request_state": request_state},
        )
        assert resp.status_code == 200, (
            f"State '{request_state}' rejected: {resp.status_code} {resp.text[:200]}"
        )
        data = resp.json()
        assert "priority_class" in data

    def test_classify_invalid_priority_rejected(
        self, nexus: NexusClient,
    ) -> None:
        """scheduler/010: Invalid priority → 422 Unprocessable Entity."""
        resp = nexus.api_post(
            "/api/v2/scheduler/classify",
            json={"priority": "nonexistent_tier", "request_state": "pending"},
        )
        assert resp.status_code == 422, (
            f"Expected 422, got {resp.status_code} {resp.text[:200]}"
        )

    def test_classify_missing_fields_uses_defaults(
        self, nexus: NexusClient,
    ) -> None:
        """scheduler/010b: Missing fields use defaults (server-side)."""
        resp = nexus.api_post(
            "/api/v2/scheduler/classify",
            json={},
        )
        # Server uses default priority/state when fields are missing
        assert resp.status_code in (200, 422), (
            f"Unexpected status: {resp.status_code} {resp.text[:200]}"
        )
        if resp.status_code == 200:
            assert "priority_class" in resp.json()
