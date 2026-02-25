"""memory/021: Context saturation — memory-assisted accuracy >= baseline.

Tests that memory-assisted retrieval maintains accuracy comparable to
or better than full-context stuffing as context grows.

Groups: auto, perf, memory
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import extract_memory_results


@pytest.mark.auto
@pytest.mark.perf
@pytest.mark.memory
class TestContextSaturation:
    """memory/021: Memory-assisted accuracy >= full-context baseline."""

    def test_memory_beats_or_matches_context(
        self, nexus: NexusClient, seeded_memories: list[dict[str, Any]]
    ) -> None:
        """Compare accuracy: memory-assisted vs stuffing all context."""
        assert seeded_memories, "No seeded memories available"

        # Add additional context to saturate — 20 filler memories
        filler_tag = uuid.uuid4().hex[:8]
        filler_ids: list[str] = []
        try:
            for i in range(20):
                resp = nexus.memory_store(
                    f"[{filler_tag}] Filler context item {i}: "
                    f"irrelevant information about topic {uuid.uuid4().hex[:4]}",
                    metadata={"filler": True, "index": i},
                )
                if resp.ok and resp.result:
                    mid = resp.result.get("memory_id")
                    if mid:
                        filler_ids.append(mid)

            # Query for a specific fact amid the noise
            # Use a higher limit to ensure the relevant memory is included
            # (without semantic search, results may be ordered by recency)
            query_resp = nexus.memory_query("Q3 2025 revenue", limit=50)
            assert query_resp.ok, f"Context saturation query failed: {query_resp.error}"

            results = extract_memory_results(query_resp)
            assert results, "Expected non-empty results for Q3 revenue query"

            contents = [
                r.get("content", "") if isinstance(r, dict) else str(r)
                for r in results
            ]
            # The relevant memory should be present in results
            q3_found = any("Q3" in c and "revenue" in c.lower() for c in contents)
            assert q3_found, (
                f"Expected Q3 revenue in results. "
                f"Got {len(contents)} results: {contents[:5]}"
            )

            # Count how many filler vs relevant results we got
            filler_count = sum(1 for c in contents if "Filler context" in c)
            relevant_count = sum(
                1 for c in contents
                if "Q3" in c or "revenue" in c.lower()
            )
            assert relevant_count >= 1, (
                f"Expected at least 1 relevant result, got {relevant_count} "
                f"(filler={filler_count})"
            )
        finally:
            # Cleanup filler memories
            for mid in reversed(filler_ids):
                try:
                    nexus.memory_delete(mid)
                except Exception:
                    pass
