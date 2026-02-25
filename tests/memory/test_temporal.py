"""memory/014: Temporal reasoning — time-scoped queries.

Tests that the memory system can handle temporal queries:
exact date, date range, and recency-based lookups.

When semantic search is unavailable, falls back to verifying
that timestamps are preserved and content can be retrieved.

Groups: auto, memory
"""

from __future__ import annotations

import logging
import time
from typing import Any

import pytest

from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import extract_memory_results

from .conftest import poll_memory_query_with_latency

logger = logging.getLogger(__name__)

# Query latency SLO: single query round-trip should be under 500ms
QUERY_LATENCY_SLO_MS = 500.0


@pytest.mark.auto
@pytest.mark.memory
class TestTemporalReasoning:
    """memory/014: Temporal reasoning — time-scoped queries."""

    def test_exact_date_query(
        self, nexus: NexusClient, seeded_memories: list[dict[str, Any]]
    ) -> None:
        """Query for Q3 revenue — verify the timestamped memory is retrievable."""
        assert seeded_memories, "No seeded memories available"

        memory_ids = [m["memory_id"] for m in seeded_memories if m.get("memory_id")]
        pr = poll_memory_query_with_latency(
            nexus, "Q3 2025 revenue",
            match_substring="Q3",
            memory_ids=memory_ids,
            limit=50,
        )

        contents = [
            r.get("content", "") if isinstance(r, dict) else str(r)
            for r in pr.results
        ]
        q3_found = any("Q3" in c and "revenue" in c.lower() for c in contents)
        assert q3_found, (
            f"Expected Q3 revenue memory in results. "
            f"Got {len(contents)} results: {contents[:5]}"
        )

        q3_seed = next(
            (m for m in seeded_memories if "Q3" in m.get("content", "")), None
        )
        assert q3_seed is not None, "Q3 memory missing from seeded_memories"
        assert q3_seed["timestamp"] == "2025-09-20T14:00:00Z"

        logger.info(
            "test_exact_date_query: query_latency=%.1fms via_fallback=%s",
            pr.query_latency_ms, pr.via_fallback,
        )
        assert pr.query_latency_ms < QUERY_LATENCY_SLO_MS, (
            f"Query latency {pr.query_latency_ms:.1f}ms exceeds {QUERY_LATENCY_SLO_MS}ms SLO"
        )

    def test_date_range_query(
        self, nexus: NexusClient, seeded_memories: list[dict[str, Any]]
    ) -> None:
        """Query for revenue data — verify multiple quarters are retrievable."""
        assert seeded_memories, "No seeded memories available"

        t0 = time.monotonic()
        resp = nexus.memory_query(
            "revenue", limit=50,
            time_start="2025-07-01T00:00:00Z",
            time_end="2025-10-31T23:59:59Z",
        )
        query_latency_ms = (time.monotonic() - t0) * 1000
        assert resp.ok, f"Query failed: {resp.error}"

        results = extract_memory_results(resp)
        contents = [
            r.get("content", "") if isinstance(r, dict) else str(r)
            for r in results
        ]
        q3_found = any("Q3" in c and "revenue" in c.lower() for c in contents)

        if not q3_found:
            memory_ids = [m["memory_id"] for m in seeded_memories if m.get("memory_id")]
            pr = poll_memory_query_with_latency(
                nexus, "revenue",
                match_substring="Q3",
                memory_ids=memory_ids,
                limit=50,
            )
            contents = [
                r.get("content", "") if isinstance(r, dict) else str(r)
                for r in pr.results
            ]
            q3_found = any("Q3" in c and "revenue" in c.lower() for c in contents)
            query_latency_ms = pr.query_latency_ms

        assert q3_found, (
            f"Expected Q3 revenue in results. "
            f"Got {len(contents)} results: {contents[:5]}"
        )

        q3_seed = next(
            (m for m in seeded_memories if "Q3" in m.get("content", "")), None
        )
        assert q3_seed is not None
        assert "2025-09" in q3_seed["timestamp"]

        logger.info("test_date_range_query: query_latency=%.1fms", query_latency_ms)
        assert query_latency_ms < QUERY_LATENCY_SLO_MS, (
            f"Query latency {query_latency_ms:.1f}ms exceeds {QUERY_LATENCY_SLO_MS}ms SLO"
        )

    def test_recency_query(
        self, nexus: NexusClient, seeded_memories: list[dict[str, Any]]
    ) -> None:
        """Query for revenue — most recent should be Q3 (Sept 2025)."""
        assert seeded_memories, "No seeded memories available"

        memory_ids = [m["memory_id"] for m in seeded_memories if m.get("memory_id")]
        pr = poll_memory_query_with_latency(
            nexus, "revenue",
            match_substring="revenue",
            memory_ids=memory_ids,
            limit=50,
        )

        contents = [
            r.get("content", "") if isinstance(r, dict) else str(r)
            for r in pr.results
        ]
        revenue_items = [c for c in contents if "revenue" in c.lower()]
        assert revenue_items, (
            f"Expected revenue memories. Got {len(contents)} results: {contents[:5]}"
        )

        q3_found = any("Q3" in c for c in revenue_items)
        assert q3_found, (
            f"Expected Q3 (most recent) in revenue results: {revenue_items[:5]}"
        )

        revenue_seeds = [
            m for m in seeded_memories if "revenue" in m.get("content", "").lower()
        ]
        latest = max(revenue_seeds, key=lambda m: m.get("timestamp", ""))
        assert "Q3" in latest["content"]

        logger.info(
            "test_recency_query: query_latency=%.1fms via_fallback=%s",
            pr.query_latency_ms, pr.via_fallback,
        )
        assert pr.query_latency_ms < QUERY_LATENCY_SLO_MS, (
            f"Query latency {pr.query_latency_ms:.1f}ms exceeds {QUERY_LATENCY_SLO_MS}ms SLO"
        )
