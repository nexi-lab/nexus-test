"""memory/017: Conflict resolution — contradictory facts.

Tests that the memory system handles conflicting information:
latest fact wins or conflict is surfaced to the caller.

Groups: auto, memory
"""

from __future__ import annotations

import uuid

import pytest

from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import extract_memory_results

from .conftest import CONFLICT_MEMORIES, StoreMemoryFn


@pytest.mark.auto
@pytest.mark.memory
class TestConflictResolution:
    """memory/017: Contradictory facts — latest wins or conflict surfaced."""

    def test_latest_fact_wins(
        self, nexus: NexusClient, store_memory: StoreMemoryFn
    ) -> None:
        """Store 'uses Python' then 'uses Rust' -> query returns Rust."""
        tag = uuid.uuid4().hex[:8]

        # Store conflicting facts in chronological order
        for mem in CONFLICT_MEMORIES:
            resp = store_memory(
                f"Project {tag}: {mem['content']}",
                metadata={**mem.get("metadata", {}), "conflict_tag": tag},
                timestamp=mem.get("timestamp"),
            )
            assert resp.ok, f"Failed to store v{mem['version']} memory: {resp.error}"

        # Query about the tech stack
        query_resp = nexus.memory_query(f"Project {tag} backend language")
        assert query_resp.ok, f"Conflict query failed: {query_resp.error}"

        results = extract_memory_results(query_resp)
        assert results, "Expected non-empty results for conflict query"

        all_contents = [
            r.get("content", "") if isinstance(r, dict) else str(r)
            for r in results
        ]
        rust_found = any("Rust" in c for c in all_contents)
        assert rust_found, (
            f"Expected Rust (latest fact) in results, got: {all_contents[:3]}"
        )

    def test_conflict_metadata_surfaced(
        self, nexus: NexusClient, store_memory: StoreMemoryFn
    ) -> None:
        """If API supports version/conflict metadata, verify it's present."""
        tag = uuid.uuid4().hex[:8]

        # Store conflicting facts
        for mem in CONFLICT_MEMORIES:
            resp = store_memory(
                f"Database {tag}: {mem['content'].replace('Alpha', tag)}",
                metadata={**mem.get("metadata", {}), "conflict_tag": tag},
                timestamp=mem.get("timestamp"),
            )
            assert resp.ok, f"Failed to store memory: {resp.error}"

        # Query and check for version/conflict metadata
        query_resp = nexus.memory_query(f"Database {tag} backend")
        assert query_resp.ok, f"Query failed: {query_resp.error}"

        results = extract_memory_results(query_resp)
        assert results, "Expected non-empty results for conflict metadata query"

        first = results[0] if isinstance(results[0], dict) else {}
        # At minimum, the result should have ordering metadata (timestamp/version)
        has_ordering = any(
            key in first
            for key in ("version", "versions", "conflict", "conflicts", "timestamp", "created_at")
        )
        assert has_ordering, (
            f"Expected ordering metadata (version/conflict/timestamp), "
            f"got keys: {list(first.keys())}"
        )
