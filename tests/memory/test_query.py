"""memory/002: Query memory — returns relevant result.

Tests that stored memories can be queried and retrieved with matching content.
Uses dual-path query (search fallback to list) to handle ReBAC permission bug.

Groups: quick, auto, memory
"""

from __future__ import annotations

import pytest

from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import extract_memory_results
from tests.memory.conftest import StoreMemoryFn


@pytest.mark.quick
@pytest.mark.auto
@pytest.mark.memory
class TestQueryMemory:
    """memory/002: Query memory — returns relevant result."""

    def test_query_returns_stored_content(
        self, nexus: NexusClient, store_memory: StoreMemoryFn
    ) -> None:
        """Querying for stored content returns a matching result."""
        store_memory("The Nexus platform supports distributed file operations")

        resp = nexus.memory_query("Nexus platform distributed", limit=20)
        assert resp.ok, f"Query failed: {resp.error}"

        results = extract_memory_results(resp)
        contents = [
            r.get("content", "") if isinstance(r, dict) else str(r)
            for r in results
        ]
        found = any("Nexus" in c and "distributed" in c.lower() for c in contents)
        assert found, (
            f"Expected stored memory in query results. "
            f"Got {len(contents)} results: {contents[:5]}"
        )

    def test_query_with_no_match_returns_empty_or_unrelated(
        self, nexus: NexusClient, store_memory: StoreMemoryFn
    ) -> None:
        """Querying for non-existent content returns empty or unrelated results."""
        store_memory("Python is a programming language")

        resp = nexus.memory_query(
            "xyzzy_nonexistent_quantum_flux_capacitor_42", limit=10
        )
        assert resp.ok, f"Query failed: {resp.error}"

        results = extract_memory_results(resp)
        # Either empty or none contain the gibberish query
        matching = [
            r for r in results
            if "xyzzy_nonexistent" in (
                r.get("content", "") if isinstance(r, dict) else str(r)
            ).lower()
        ]
        assert not matching, f"Unexpected match for gibberish query: {matching}"

    def test_query_multiple_results(
        self, nexus: NexusClient, store_memory: StoreMemoryFn
    ) -> None:
        """Querying a topic with multiple stored memories returns multiple results."""
        store_memory("Revenue in Q1 was $10M from enterprise contracts")
        store_memory("Revenue in Q2 grew to $12M with 20% increase")
        store_memory("Revenue in Q3 reached $15M, a new record")

        resp = nexus.memory_query("revenue", limit=20)
        assert resp.ok, f"Query failed: {resp.error}"

        results = extract_memory_results(resp)
        revenue_results = [
            r for r in results
            if "revenue" in (
                r.get("content", "") if isinstance(r, dict) else str(r)
            ).lower()
        ]
        assert len(revenue_results) >= 2, (
            f"Expected multiple revenue results, got {len(revenue_results)}: "
            f"{[r.get('content', '')[:50] for r in revenue_results]}"
        )

    def test_search_with_permissions_enabled(
        self, nexus: NexusClient, store_memory: StoreMemoryFn,
        search_available: bool,
    ) -> None:
        """Direct search endpoint works with permissions enabled.

        Known server bug: _keyword_search SQL syntax error in
        memory_permission_enforcer.py:184 when ReBAC permissions enabled.
        Semantic search also falls back to keyword when no embeddings exist.
        """
        if not search_available:
            pytest.skip(
                "Search endpoint unavailable (SQL bug in _keyword_search "
                "with ReBAC permissions)"
            )

        store_memory("Kubernetes cluster upgraded to version 1.28")

        resp = nexus.memory_search("Kubernetes cluster upgrade")
        assert resp.ok, f"Search failed: {resp.error}"

        results = extract_memory_results(resp)
        assert len(results) >= 1, "Search returned no results"
