"""memory/017: Conflict resolution — contradictory facts.

Tests that the memory system handles conflicting information:
latest fact wins or conflict is surfaced to the caller.

Groups: auto, memory
"""

from __future__ import annotations

import logging
import uuid

import pytest

from tests.helpers.api_client import NexusClient

from .conftest import CONFLICT_MEMORIES, StoreMemoryFn, poll_memory_query_with_latency

logger = logging.getLogger(__name__)

QUERY_LATENCY_SLO_MS = 500.0


@pytest.mark.auto
@pytest.mark.memory
class TestConflictResolution:
    """memory/017: Contradictory facts — latest wins or conflict surfaced."""

    def test_latest_fact_wins(
        self, nexus: NexusClient, store_memory: StoreMemoryFn
    ) -> None:
        """Store 'uses Python' then 'uses Rust' -> query returns Rust."""
        tag = uuid.uuid4().hex[:8]

        memory_ids: list[str] = []
        for mem in CONFLICT_MEMORIES:
            resp = store_memory(
                f"Project {tag}: {mem['content']}",
                metadata={**mem.get("metadata", {}), "conflict_tag": tag},
                timestamp=mem.get("timestamp"),
            )
            assert resp.ok, f"Failed to store v{mem['version']} memory: {resp.error}"
            mid = (resp.result or {}).get("memory_id")
            if mid:
                memory_ids.append(mid)

        pr = poll_memory_query_with_latency(
            nexus, f"Project {tag} backend language",
            match_substring=tag,
            memory_ids=memory_ids,
        )

        assert pr.results, "Expected non-empty results for conflict query"

        all_contents = [
            r.get("content", "") if isinstance(r, dict) else str(r)
            for r in pr.results
        ]
        rust_found = any("Rust" in c for c in all_contents)
        assert rust_found, (
            f"Expected Rust (latest fact) in results, got: {all_contents[:3]}"
        )

        logger.info(
            "test_latest_fact_wins: query_latency=%.1fms via_fallback=%s",
            pr.query_latency_ms, pr.via_fallback,
        )
        assert pr.query_latency_ms < QUERY_LATENCY_SLO_MS, (
            f"Query latency {pr.query_latency_ms:.0f}ms exceeds SLO {QUERY_LATENCY_SLO_MS:.0f}ms"
        )

    def test_conflict_metadata_surfaced(
        self, nexus: NexusClient, store_memory: StoreMemoryFn
    ) -> None:
        """If API supports version/conflict metadata, verify it's present."""
        tag = uuid.uuid4().hex[:8]

        memory_ids: list[str] = []
        for mem in CONFLICT_MEMORIES:
            resp = store_memory(
                f"Database {tag}: {mem['content'].replace('Alpha', tag)}",
                metadata={**mem.get("metadata", {}), "conflict_tag": tag},
                timestamp=mem.get("timestamp"),
            )
            assert resp.ok, f"Failed to store memory: {resp.error}"
            mid = (resp.result or {}).get("memory_id")
            if mid:
                memory_ids.append(mid)

        pr = poll_memory_query_with_latency(
            nexus, f"Database {tag} backend",
            match_substring=tag,
            memory_ids=memory_ids,
        )

        assert pr.results, "Expected non-empty results for conflict metadata query"

        first = pr.results[0] if isinstance(pr.results[0], dict) else {}
        has_ordering = any(
            key in first
            for key in ("version", "versions", "conflict", "conflicts", "timestamp", "created_at")
        )
        assert has_ordering, (
            f"Expected ordering metadata (version/conflict/timestamp), "
            f"got keys: {list(first.keys())}"
        )

        logger.info(
            "test_conflict_metadata_surfaced: query_latency=%.1fms via_fallback=%s",
            pr.query_latency_ms, pr.via_fallback,
        )
        assert pr.query_latency_ms < QUERY_LATENCY_SLO_MS, (
            f"Query latency {pr.query_latency_ms:.0f}ms exceeds SLO {QUERY_LATENCY_SLO_MS:.0f}ms"
        )
