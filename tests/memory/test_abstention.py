"""memory/016: Abstention — hallucination guard.

Tests both directions:
- Not-in-memory query returns unknown/empty (not fabricated)
- In-memory query returns correct answer (not false abstention)

Groups: auto, memory
"""

from __future__ import annotations

import logging
import time
import uuid

import pytest

from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import extract_memory_results

from .conftest import StoreMemoryFn, poll_memory_query_with_latency

logger = logging.getLogger(__name__)

QUERY_LATENCY_SLO_MS = 500.0


@pytest.mark.auto
@pytest.mark.memory
class TestAbstention:
    """memory/016: Hallucination guard — refuse when answer not in memory."""

    def test_unknown_query_returns_empty(
        self, nexus: NexusClient, store_memory: StoreMemoryFn
    ) -> None:
        """Query about topic not in memory returns unknown/empty, not fabricated."""
        resp = store_memory(
            "The server runs on port 8080",
            metadata={"topic": "infrastructure"},
        )
        assert resp.ok, f"Failed to store memory: {resp.error}"

        t0 = time.monotonic()
        nonsense_topic = f"quantum_flux_capacitor_{uuid.uuid4().hex[:8]}"
        query_resp = nexus.memory_query(nonsense_topic)
        query_latency_ms = (time.monotonic() - t0) * 1000
        assert query_resp.ok, f"Query failed: {query_resp.error}"

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

        logger.info(
            "test_unknown_query_returns_empty: query_latency=%.1fms",
            query_latency_ms,
        )
        assert query_latency_ms < QUERY_LATENCY_SLO_MS, (
            f"Query latency {query_latency_ms:.0f}ms exceeds SLO {QUERY_LATENCY_SLO_MS:.0f}ms"
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
        memory_id = (resp.result or {}).get("memory_id")

        pr = poll_memory_query_with_latency(
            nexus, f"deployment target project {tag}",
            match_substring="Kubernetes",
            memory_ids=[memory_id] if memory_id else None,
        )

        found = any(
            "Kubernetes" in (r.get("content", "") if isinstance(r, dict) else str(r))
            for r in pr.results
        )
        assert found, (
            f"Expected Kubernetes in results for known query. Got: "
            f"{[r.get('content', '')[:80] if isinstance(r, dict) else str(r)[:80] for r in pr.results[:3]]}"
        )

        logger.info(
            "test_known_query_returns_correct: query_latency=%.1fms via_fallback=%s",
            pr.query_latency_ms, pr.via_fallback,
        )
        assert pr.query_latency_ms < QUERY_LATENCY_SLO_MS, (
            f"Query latency {pr.query_latency_ms:.0f}ms exceeds SLO {QUERY_LATENCY_SLO_MS:.0f}ms"
        )
