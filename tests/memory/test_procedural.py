"""memory/022: Procedural memory — feedback improves responses.

Tests that storing feedback signals (corrections, preferences)
leads to improved subsequent query results.

Groups: auto, memory
"""

from __future__ import annotations

import logging
import uuid

import pytest

from tests.helpers.api_client import NexusClient

from .conftest import StoreMemoryFn, poll_memory_query_with_latency

logger = logging.getLogger(__name__)

QUERY_LATENCY_SLO_MS = 500.0


@pytest.mark.auto
@pytest.mark.memory
class TestProceduralMemory:
    """memory/022: Response quality improves after accumulated feedback."""

    def test_feedback_improves_responses(
        self, nexus: NexusClient, store_memory: StoreMemoryFn
    ) -> None:
        """Store feedback signals, verify subsequent queries reflect them."""
        tag = uuid.uuid4().hex[:8]

        memory_ids: list[str] = []

        # Phase 1: Store initial knowledge
        resp = store_memory(
            f"Project {tag} uses a monolithic architecture",
            metadata={"topic": "architecture", "project": tag},
        )
        assert resp.ok, f"Failed to store initial memory: {resp.error}"
        mid = (resp.result or {}).get("memory_id")
        if mid:
            memory_ids.append(mid)

        # Phase 2: Store feedback/correction
        correction = store_memory(
            f"CORRECTION: Project {tag} was migrated to microservices in Q2 2025",
            metadata={
                "topic": "architecture",
                "project": tag,
                "type": "correction",
                "supersedes": "monolithic",
            },
        )
        assert correction.ok, f"Failed to store correction: {correction.error}"
        mid = (correction.result or {}).get("memory_id")
        if mid:
            memory_ids.append(mid)

        # Phase 3: Store preference signal
        preference = store_memory(
            f"User prefers concise technical summaries for project {tag}",
            metadata={
                "topic": "preferences",
                "project": tag,
                "type": "preference",
            },
        )
        assert preference.ok, f"Failed to store preference: {preference.error}"

        # Phase 4: Query should reflect the correction
        pr = poll_memory_query_with_latency(
            nexus, f"Project {tag} architecture",
            match_substring="microservices",
            memory_ids=memory_ids,
        )

        assert pr.results, "Expected non-empty results for architecture query"

        contents = [
            r.get("content", "") if isinstance(r, dict) else str(r)
            for r in pr.results
        ]
        correction_found = any("microservices" in c.lower() for c in contents)
        assert correction_found, (
            f"Expected microservices correction in results. Got: {contents[:3]}"
        )

        logger.info(
            "test_feedback_improves_responses: query_latency=%.1fms via_fallback=%s",
            pr.query_latency_ms, pr.via_fallback,
        )
        assert pr.query_latency_ms < QUERY_LATENCY_SLO_MS, (
            f"Query latency {pr.query_latency_ms:.0f}ms exceeds SLO {QUERY_LATENCY_SLO_MS:.0f}ms"
        )
