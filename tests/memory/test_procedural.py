"""memory/022: Procedural memory — feedback improves responses.

Tests that storing feedback signals (corrections, preferences)
leads to improved subsequent query results.

Groups: auto, memory
"""

from __future__ import annotations

import uuid

import pytest

from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import extract_memory_results

from .conftest import StoreMemoryFn


@pytest.mark.auto
@pytest.mark.memory
class TestProceduralMemory:
    """memory/022: Response quality improves after accumulated feedback."""

    def test_feedback_improves_responses(
        self, nexus: NexusClient, store_memory: StoreMemoryFn
    ) -> None:
        """Store feedback signals, verify subsequent queries reflect them."""
        tag = uuid.uuid4().hex[:8]

        # Phase 1: Store initial knowledge
        resp = store_memory(
            f"Project {tag} uses a monolithic architecture",
            metadata={"topic": "architecture", "project": tag},
        )
        assert resp.ok, f"Failed to store initial memory: {resp.error}"

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

        # Phase 4: Query should reflect the correction, not outdated info
        query_resp = nexus.memory_query(f"Project {tag} architecture")
        assert query_resp.ok, f"Post-feedback query failed: {query_resp.error}"

        results = extract_memory_results(query_resp)
        assert results, "Expected non-empty results for architecture query"

        contents = [
            r.get("content", "") if isinstance(r, dict) else str(r)
            for r in results
        ]
        correction_found = any("microservices" in c.lower() for c in contents)
        assert correction_found, (
            f"Expected microservices correction in results. Got: {contents[:3]}"
        )
