"""memory/009: Invalidate + revalidate memory — state transitions correct.

Tests that memories can be deactivated (active → inactive) and
reactivated (inactive → active) via PUT state changes.

Groups: auto, memory
"""

from __future__ import annotations

import pytest

from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import assert_memory_stored, extract_memory_results
from tests.memory.conftest import StoreMemoryFn


@pytest.mark.auto
@pytest.mark.memory
class TestInvalidateRevalidate:
    """memory/009: Invalidate + revalidate memory."""

    def test_deactivate_changes_state(
        self, nexus: NexusClient, store_memory: StoreMemoryFn
    ) -> None:
        """Deactivating a memory changes its state to inactive."""
        resp = store_memory("Active memory for deactivation test")
        result = assert_memory_stored(resp)
        memory_id = result["memory_id"]

        # Deactivate via PUT state=inactive
        deact_resp = nexus.memory_deactivate(memory_id)
        assert deact_resp.ok, f"Deactivate failed: {deact_resp.error}"

        # Verify state changed — check original ID (server updates in place)
        get_resp = nexus.memory_get(memory_id)
        assert get_resp.ok, f"GET failed: {get_resp.error}"
        if isinstance(get_resp.result, dict):
            state = get_resp.result.get("state", "")
            assert state == "inactive", (
                f"Expected inactive state, got: {state}"
            )

    def test_reactivate_restores_state(
        self, nexus: NexusClient, store_memory: StoreMemoryFn
    ) -> None:
        """Reactivating (approving) a deactivated memory restores active state."""
        resp = store_memory("Memory for reactivation test")
        result = assert_memory_stored(resp)
        memory_id = result["memory_id"]

        # Deactivate then reactivate
        deact_resp = nexus.memory_deactivate(memory_id)
        assert deact_resp.ok, f"Deactivate failed: {deact_resp.error}"
        # Use the new version ID for reactivation
        deact_id = (deact_resp.result or {}).get("memory_id", memory_id)

        approve_resp = nexus.memory_approve(deact_id)
        assert approve_resp.ok, f"Approve (reactivate) failed: {approve_resp.error}"

        # Check state on the latest version
        approve_id = (approve_resp.result or {}).get("memory_id", deact_id)
        get_resp = nexus.memory_get(approve_id)
        assert get_resp.ok, f"GET failed: {get_resp.error}"
        if isinstance(get_resp.result, dict):
            state = get_resp.result.get("state", "")
            assert state == "active", (
                f"Expected active state after reactivation, got: {state}"
            )

    def test_deactivated_memory_not_in_default_query(
        self, nexus: NexusClient, store_memory: StoreMemoryFn
    ) -> None:
        """Deactivated memories should not appear in default (active-only) queries."""
        unique = "inv009_deact_query_marker"
        resp = store_memory(f"Memory with {unique} for query exclusion test")
        result = assert_memory_stored(resp)
        memory_id = result["memory_id"]

        # Deactivate
        deact_resp = nexus.memory_deactivate(memory_id)
        assert deact_resp.ok, f"Deactivate failed: {deact_resp.error}"

        # Query should not return deactivated memory by default
        query_resp = nexus.memory_query(unique, limit=20)
        if query_resp.ok:
            results = extract_memory_results(query_resp)
            matching_content = [
                r for r in results
                if unique in (
                    r.get("content", "") if isinstance(r, dict) else str(r)
                )
            ]
            assert not matching_content, (
                f"Deactivated memory content still appears in default query: "
                f"{matching_content[:2]}"
            )
