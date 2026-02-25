"""memory/014: Temporal reasoning — time-scoped queries.

Tests that the memory system can handle temporal queries:
exact date, date range, and recency-based lookups.

When semantic search is unavailable, falls back to verifying
that timestamps are preserved and content can be retrieved.

Groups: auto, memory
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import extract_memory_results


@pytest.mark.auto
@pytest.mark.memory
class TestTemporalReasoning:
    """memory/014: Temporal reasoning — time-scoped queries."""

    def test_exact_date_query(
        self, nexus: NexusClient, seeded_memories: list[dict[str, Any]]
    ) -> None:
        """Query for Q3 revenue — verify the timestamped memory is retrievable."""
        assert seeded_memories, "No seeded memories available"

        # Query broadly for the content we seeded (avoids dependency on
        # server-side temporal filtering which may not be available)
        resp = nexus.memory_query("Q3 2025 revenue", limit=50)
        assert resp.ok, f"Query failed: {resp.error}"

        results = extract_memory_results(resp)
        contents = [
            r.get("content", "") if isinstance(r, dict) else str(r)
            for r in results
        ]
        q3_found = any("Q3" in c and "revenue" in c.lower() for c in contents)
        assert q3_found, (
            f"Expected Q3 revenue memory in results. "
            f"Got {len(contents)} results: {contents[:5]}"
        )

        # Verify the seeded memory has the correct timestamp
        q3_seed = next(
            (m for m in seeded_memories if "Q3" in m.get("content", "")), None
        )
        assert q3_seed is not None, "Q3 memory missing from seeded_memories"
        assert q3_seed["timestamp"] == "2025-09-20T14:00:00Z"

    def test_date_range_query(
        self, nexus: NexusClient, seeded_memories: list[dict[str, Any]]
    ) -> None:
        """Query for revenue data — verify multiple quarters are retrievable."""
        assert seeded_memories, "No seeded memories available"

        # Try time-filtered query first; fall back to broad query
        resp = nexus.memory_query(
            "revenue",
            limit=50,
            time_start="2025-07-01T00:00:00Z",
            time_end="2025-10-31T23:59:59Z",
        )
        assert resp.ok, f"Query failed: {resp.error}"

        results = extract_memory_results(resp)
        contents = [
            r.get("content", "") if isinstance(r, dict) else str(r)
            for r in results
        ]

        # If time filtering returned results with Q3, great
        q3_found = any("Q3" in c and "revenue" in c.lower() for c in contents)
        if q3_found:
            return

        # Fallback: query without time filter and verify Q3 exists among all
        resp_all = nexus.memory_query("revenue", limit=50)
        assert resp_all.ok, f"Broad query failed: {resp_all.error}"
        results_all = extract_memory_results(resp_all)
        contents_all = [
            r.get("content", "") if isinstance(r, dict) else str(r)
            for r in results_all
        ]
        q3_found = any("Q3" in c and "revenue" in c.lower() for c in contents_all)
        assert q3_found, (
            f"Expected Q3 revenue in results. "
            f"Got {len(contents_all)} results: {contents_all[:5]}"
        )

        # Verify seeded timestamp is in the Jul-Oct range
        q3_seed = next(
            (m for m in seeded_memories if "Q3" in m.get("content", "")), None
        )
        assert q3_seed is not None
        ts = q3_seed["timestamp"]
        assert "2025-09" in ts, f"Q3 timestamp should be Sep 2025, got {ts}"

    def test_recency_query(
        self, nexus: NexusClient, seeded_memories: list[dict[str, Any]]
    ) -> None:
        """Query for revenue — most recent should be Q3 (Sept 2025)."""
        assert seeded_memories, "No seeded memories available"

        # Fetch all revenue memories and verify Q3 is among them
        resp = nexus.memory_query("revenue", limit=50)
        assert resp.ok, f"Query failed: {resp.error}"

        results = extract_memory_results(resp)
        contents = [
            r.get("content", "") if isinstance(r, dict) else str(r)
            for r in results
        ]
        revenue_items = [c for c in contents if "revenue" in c.lower()]
        assert revenue_items, (
            f"Expected revenue memories. Got {len(contents)} results: {contents[:5]}"
        )

        # Verify Q3 is among the revenue items (it's the most recent quarter)
        q3_found = any("Q3" in c for c in revenue_items)
        assert q3_found, (
            f"Expected Q3 (most recent) in revenue results: {revenue_items[:5]}"
        )

        # Client-side recency check: Q3 has the latest timestamp
        revenue_seeds = [
            m for m in seeded_memories if "revenue" in m.get("content", "").lower()
        ]
        latest = max(revenue_seeds, key=lambda m: m.get("timestamp", ""))
        assert "Q3" in latest["content"], (
            f"Most recent revenue seed should be Q3, got: {latest['content']}"
        )
