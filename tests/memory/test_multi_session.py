"""memory/015: Multi-session reasoning — synthesize across sessions.

Tests that the memory system can synthesize information
stored across 3+ separate sessions into a coherent answer.

Groups: auto, memory
"""

from __future__ import annotations

import uuid

import pytest

from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import extract_memory_results

from .conftest import MULTI_SESSION_MEMORIES, StoreMemoryFn


@pytest.mark.auto
@pytest.mark.memory
class TestMultiSessionReasoning:
    """memory/015: Synthesize information from 3+ sessions."""

    def test_cross_session_synthesis(
        self, nexus: NexusClient, store_memory: StoreMemoryFn
    ) -> None:
        """Store facts across 3 sessions, query requiring all 3 to answer."""
        tag = uuid.uuid4().hex[:8]

        # Store memories simulating 3 different sessions
        for mem in MULTI_SESSION_MEMORIES:
            resp = store_memory(
                mem["content"],
                metadata={**mem.get("metadata", {}), "session_tag": tag},
                timestamp=mem.get("timestamp"),
            )
            assert resp.ok, f"Failed to store session {mem['session']} memory: {resp.error}"

        # Query that requires synthesizing info from all 3 sessions
        query_resp = nexus.memory_query("Alice career progression")
        assert query_resp.ok, f"Cross-session query failed: {query_resp.error}"

        results = extract_memory_results(query_resp)
        assert results, "Expected non-empty results for cross-session query"

        contents = " ".join(
            r.get("content", "") if isinstance(r, dict) else str(r)
            for r in results
        )
        # At least 2 of the 3 session facts should appear
        facts_found = sum([
            "joined" in contents.lower() or "backend developer" in contents.lower(),
            "lead" in contents.lower() or "promoted" in contents.lower(),
            "migration" in contents.lower() or "proposed" in contents.lower(),
        ])
        assert facts_found >= 2, (
            f"Expected at least 2 of 3 session facts, found {facts_found}. "
            f"Content: {contents[:300]}"
        )
