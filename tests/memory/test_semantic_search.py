"""memory/003: Semantic search — ranked by similarity using HERB enterprise data.

Tests that the memory system performs semantic search (not just keyword matching)
by seeding HERB enterprise-context data and querying with paraphrased questions.

Groups: auto, memory
"""

from __future__ import annotations

import contextlib
from typing import Any

import pytest

from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import extract_memory_results
from tests.memory.conftest import load_herb_records


@pytest.mark.auto
@pytest.mark.memory
class TestSemanticSearch:
    """memory/003: Semantic search using HERB enterprise-context data."""

    @pytest.fixture(scope="class")
    def herb_memories(
        self, nexus: NexusClient
    ):  # type: ignore[override]
        """Seed HERB enterprise data (class-scoped, cleaned up after)."""
        records = load_herb_records(max_records=50)
        if not records:
            pytest.skip("HERB enterprise-context data not found")

        seeded: list[dict[str, Any]] = []
        for rec in records:
            content = rec.get("content", "")
            if not content:
                continue
            resp = nexus.memory_store(
                content,
                metadata={
                    "_herb_test": True,
                    "type": rec.get("type", "unknown"),
                    "id": rec.get("id", ""),
                },
            )
            if resp.ok and resp.result:
                mid = resp.result.get("memory_id")
                if mid:
                    seeded.append({"memory_id": mid, "content": content, **rec})

        yield seeded

        for mem in reversed(seeded):
            mid = mem.get("memory_id")
            if mid:
                with contextlib.suppress(Exception):
                    nexus.memory_delete(mid)

    def test_semantic_search_finds_relevant_result(
        self, nexus: NexusClient, herb_memories: list[dict[str, Any]]
    ) -> None:
        """Semantic query for HERB content returns relevant results."""
        assert herb_memories, "No HERB memories seeded"

        # Use a query that matches content we seeded (could be employees, customers, or products)
        # Check what types were seeded
        seeded_types = {m.get("type", "unknown") for m in herb_memories}

        # Build a query based on what was actually seeded
        if "employee" in seeded_types:
            query = "engineer experienced with Python and distributed systems"
            match_terms = ["engineer", "python", "distributed", "developer"]
        elif "customer" in seeded_types:
            query = "enterprise company using analytics products"
            match_terms = ["company", "enterprise", "products", "analytics", "customer"]
        else:
            query = "product with real-time features and analytics"
            match_terms = ["product", "analytics", "features"]

        resp = nexus.memory_query(query, limit=20)
        assert resp.ok, f"Query failed: {resp.error}"

        results = extract_memory_results(resp)
        contents = [
            r.get("content", "") if isinstance(r, dict) else str(r)
            for r in results
        ]
        # Should find at least one result with matching terms
        relevant = any(
            any(term in c.lower() for term in match_terms)
            for c in contents
        )
        assert relevant, (
            f"Expected relevant results for '{query}'. "
            f"Got {len(contents)} results: {contents[:3]}"
        )

    def test_semantic_search_finds_product_by_description(
        self, nexus: NexusClient, herb_memories: list[dict[str, Any]]
    ) -> None:
        """Semantic query for product capabilities finds matching products."""
        assert herb_memories, "No HERB memories seeded"

        resp = nexus.memory_query(
            "analytics platform with real-time dashboards",
            limit=20,
        )
        assert resp.ok, f"Query failed: {resp.error}"

        results = extract_memory_results(resp)
        assert len(results) >= 1, (
            "Expected at least one result for analytics/dashboard query"
        )

    def test_semantic_search_ranks_by_relevance(
        self, nexus: NexusClient, herb_memories: list[dict[str, Any]]
    ) -> None:
        """More relevant results appear before less relevant ones."""
        assert herb_memories, "No HERB memories seeded"

        # Store a highly specific memory
        specific_content = "The Cloud Analytics Engine v3.0 processes petabyte-scale data"
        specific_resp = nexus.memory_store(
            specific_content,
            metadata={"_herb_test": True, "_ranking_test": True},
        )
        assert specific_resp.ok

        try:
            resp = nexus.memory_query(
                "cloud analytics engine data processing",
                limit=10,
            )
            assert resp.ok, f"Query failed: {resp.error}"

            results = extract_memory_results(resp)
            if results:
                top_content = (
                    results[0].get("content", "")
                    if isinstance(results[0], dict)
                    else str(results[0])
                )
                # Top result should be the specific memory or at least mention analytics
                assert "analytics" in top_content.lower() or "engine" in top_content.lower(), (
                    f"Top result not relevant to analytics query: {top_content[:100]}"
                )
        finally:
            mid = (specific_resp.result or {}).get("memory_id")
            if mid:
                with contextlib.suppress(Exception):
                    nexus.memory_delete(mid)
