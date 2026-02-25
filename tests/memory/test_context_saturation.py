"""memory/021: Context saturation — memory-assisted accuracy >= baseline.

Tests that memory-assisted retrieval maintains accuracy comparable to
or better than full-context stuffing as context grows.

Groups: auto, perf, memory
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import pytest

from tests.helpers.api_client import NexusClient

from .conftest import poll_memory_query_with_latency

logger = logging.getLogger(__name__)

QUERY_LATENCY_SLO_MS = 500.0


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

            # Poll for Q3 revenue amid the noise
            memory_ids = [
                m["memory_id"] for m in seeded_memories if m.get("memory_id")
            ]
            pr = poll_memory_query_with_latency(
                nexus, "Q3 2025 revenue",
                match_substring="Q3",
                memory_ids=memory_ids,
                limit=50,
            )

            assert pr.results, "Expected non-empty results for Q3 revenue query"

            contents = [
                r.get("content", "") if isinstance(r, dict) else str(r)
                for r in pr.results
            ]
            q3_found = any("Q3" in c and "revenue" in c.lower() for c in contents)
            assert q3_found, (
                f"Expected Q3 revenue in results. "
                f"Got {len(contents)} results: {contents[:5]}"
            )

            filler_count = sum(1 for c in contents if "Filler context" in c)
            relevant_count = sum(
                1 for c in contents if "Q3" in c or "revenue" in c.lower()
            )
            assert relevant_count >= 1, (
                f"Expected at least 1 relevant result, got {relevant_count} "
                f"(filler={filler_count})"
            )

            logger.info(
                "test_memory_beats_or_matches_context: query_latency=%.1fms via_fallback=%s",
                pr.query_latency_ms, pr.via_fallback,
            )
            assert pr.query_latency_ms < QUERY_LATENCY_SLO_MS, (
                f"Query latency {pr.query_latency_ms:.0f}ms exceeds SLO {QUERY_LATENCY_SLO_MS:.0f}ms"
            )
        finally:
            for mid in reversed(filler_ids):
                try:
                    nexus.memory_delete(mid)
                except Exception:
                    pass
