"""memory/015: Multi-session reasoning — synthesize across sessions.

Tests that the memory system can synthesize information
stored across 3+ separate sessions into a coherent answer.

Groups: auto, memory
"""

from __future__ import annotations

import logging
import uuid

import pytest

from tests.helpers.api_client import NexusClient

from .conftest import MULTI_SESSION_MEMORIES, StoreMemoryFn, poll_memory_query_with_latency

logger = logging.getLogger(__name__)

QUERY_LATENCY_SLO_MS = 500.0


@pytest.mark.auto
@pytest.mark.memory
class TestMultiSessionReasoning:
    """memory/015: Synthesize information from 3+ sessions."""

    def test_cross_session_synthesis(
        self, nexus: NexusClient, store_memory: StoreMemoryFn
    ) -> None:
        """Store facts across 3 sessions, query requiring all 3 to answer."""
        tag = uuid.uuid4().hex[:8]

        memory_ids: list[str] = []
        for mem in MULTI_SESSION_MEMORIES:
            resp = store_memory(
                mem["content"],
                metadata={**mem.get("metadata", {}), "session_tag": tag},
                timestamp=mem.get("timestamp"),
            )
            assert resp.ok, f"Failed to store session {mem['session']} memory: {resp.error}"
            mid = (resp.result or {}).get("memory_id")
            if mid:
                memory_ids.append(mid)

        pr = poll_memory_query_with_latency(
            nexus, "Alice career progression",
            match_substring="Alice",
            memory_ids=memory_ids,
        )

        assert pr.results, "Expected non-empty results for cross-session query"

        contents = " ".join(
            r.get("content", "") if isinstance(r, dict) else str(r)
            for r in pr.results
        )
        facts_found = sum([
            "joined" in contents.lower() or "backend developer" in contents.lower(),
            "lead" in contents.lower() or "promoted" in contents.lower(),
            "migration" in contents.lower() or "proposed" in contents.lower(),
        ])
        assert facts_found >= 2, (
            f"Expected at least 2 of 3 session facts, found {facts_found}. "
            f"Content: {contents[:300]}"
        )

        logger.info(
            "test_cross_session_synthesis: query_latency=%.1fms via_fallback=%s",
            pr.query_latency_ms, pr.via_fallback,
        )
        assert pr.query_latency_ms < QUERY_LATENCY_SLO_MS, (
            f"Query latency {pr.query_latency_ms:.0f}ms exceeds SLO {QUERY_LATENCY_SLO_MS:.0f}ms"
        )
