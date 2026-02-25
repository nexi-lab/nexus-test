"""memory/016: Abstention — hallucination guard.

Tests both directions:
- Not-in-memory query returns unknown/empty (not fabricated)
- In-memory query returns correct answer (not false abstention)

Groups: auto, memory
"""

from __future__ import annotations

import uuid

import pytest

from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import extract_memory_results, assert_memory_query_contains

from .conftest import StoreMemoryFn


@pytest.mark.auto
@pytest.mark.memory
class TestAbstention:
    """memory/016: Hallucination guard — refuse when answer not in memory."""

    def test_unknown_query_returns_empty(
        self, nexus: NexusClient, store_memory: StoreMemoryFn
    ) -> None:
        """Query about topic not in memory returns unknown/empty, not fabricated."""
        # Store a specific memory so the system has *something*
        resp = store_memory(
            "The server runs on port 8080",
            metadata={"topic": "infrastructure"},
        )
        assert resp.ok, f"Failed to store memory: {resp.error}"

        # Query about a completely unrelated, obscure topic
        nonsense_topic = f"quantum_flux_capacitor_{uuid.uuid4().hex[:8]}"
        query_resp = nexus.memory_query(nonsense_topic)
        assert query_resp.ok, f"Query failed: {query_resp.error}"

        # Result should be empty or contain no fabricated content about this topic
        results = extract_memory_results(query_resp)
        relevant = [
            r for r in results
            if nonsense_topic in (
                r.get("content", "") if isinstance(r, dict) else str(r)
            )
        ]
        assert not relevant, (
            f"System should not fabricate results for unknown topic "
            f"{nonsense_topic!r}, got: {relevant[:3]}"
        )

    def test_known_query_returns_correct(
        self, nexus: NexusClient, store_memory: StoreMemoryFn
    ) -> None:
        """Query about topic IN memory returns correct answer (not false abstention)."""
        tag = uuid.uuid4().hex[:8]
        known_fact = f"The deployment target for project {tag} is Kubernetes v1.28"

        resp = store_memory(
            known_fact,
            metadata={"topic": "deployment", "tag": tag},
        )
        assert resp.ok, f"Failed to store memory: {resp.error}"

        # Query for the known fact
        query_resp = nexus.memory_query(f"deployment target project {tag}")
        assert_memory_query_contains(query_resp, content_substring="Kubernetes")
