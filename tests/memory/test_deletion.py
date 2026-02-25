"""memory/005: Memory deletion — removed from store and index.

Tests that deleting a memory removes it from both the data store
and the search index.

Groups: auto, memory
"""

from __future__ import annotations

import pytest

from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import (
    assert_memory_not_found,
    assert_memory_stored,
    extract_memory_results,
)


@pytest.mark.auto
@pytest.mark.memory
class TestMemoryDeletion:
    """memory/005: Memory deletion — removed from store + index."""

    def test_delete_removes_from_store(self, nexus: NexusClient) -> None:
        """Deleted memory is not retrievable by ID."""
        # Store without fixture cleanup (we're testing manual delete)
        resp = nexus.memory_store(
            "Temporary fact for deletion test",
            metadata={"_deletion_test": True},
        )
        result = assert_memory_stored(resp)
        memory_id = result["memory_id"]

        # Delete
        del_resp = nexus.memory_delete(memory_id)
        assert del_resp.ok, f"Delete failed: {del_resp.error}"

        # Verify not found
        assert_memory_not_found(nexus, memory_id)

    def test_delete_removes_from_search_index(self, nexus: NexusClient) -> None:
        """Deleted memory does not appear in search/query results."""
        unique_marker = "xdel_zebra_quantum_marker_7742"
        resp = nexus.memory_store(
            f"This memory contains {unique_marker} for search verification",
            metadata={"_deletion_test": True},
        )
        result = assert_memory_stored(resp)
        memory_id = result["memory_id"]

        # Verify it's queryable before deletion
        query_resp = nexus.memory_query(unique_marker, limit=20)
        assert query_resp.ok

        # Delete
        del_resp = nexus.memory_delete(memory_id)
        assert del_resp.ok, f"Delete failed: {del_resp.error}"

        # Verify not in query results
        query_resp_after = nexus.memory_query(unique_marker, limit=20)
        if query_resp_after.ok:
            results = extract_memory_results(query_resp_after)
            matching = [
                r for r in results
                if memory_id == (r.get("memory_id", "") if isinstance(r, dict) else "")
            ]
            assert not matching, (
                f"Deleted memory {memory_id} still appears in query results"
            )

    def test_delete_nonexistent_memory(self, nexus: NexusClient) -> None:
        """Deleting a non-existent memory returns an error or no-op."""
        fake_id = "mem_nonexistent_00000000"
        resp = nexus.memory_delete(fake_id)
        # Either error (404) or success (idempotent) — both are acceptable
        # But it should NOT cause a 500
        if not resp.ok and resp.error is not None:
            assert abs(resp.error.code) != 500, (
                f"Delete of non-existent memory caused server error: {resp.error}"
            )
