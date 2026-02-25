"""search/006: Query expansion — expanded query finds more results.

Verifies that the LLM-based query expansion endpoint works and
produces queries that find additional relevant results.

Requires OPENROUTER_API_KEY to be configured on the server.
"""

from __future__ import annotations

import pytest

from tests.helpers.api_client import NexusClient
from tests.search.conftest import extract_search_results


def _expansion_available(nexus: NexusClient) -> bool:
    """Check if query expansion endpoint is functional."""
    resp = nexus.search_expand("test query")
    if resp.status_code != 200:
        return False
    data = resp.json()
    return "expanded_query" in data or "expansions" in data or "queries" in data


@pytest.mark.auto
@pytest.mark.search
class TestQueryExpansion:
    """search/006: LLM query expansion produces broader search results."""

    def test_expansion_endpoint_responds(self, nexus: NexusClient) -> None:
        """Query expansion endpoint returns a response (even if LLM unavailable)."""
        resp = nexus.search_expand("team performance metrics")
        # Should return 200 with expansion, or a clear error (not 500)
        assert resp.status_code in (200, 400, 422, 503), (
            f"Unexpected status {resp.status_code}: {resp.text}"
        )

    def test_expansion_produces_queries(self, nexus: NexusClient) -> None:
        """If LLM is available, expansion produces alternative queries."""
        if not _expansion_available(nexus):
            pytest.skip("Query expansion not available (requires OPENROUTER_API_KEY)")

        resp = nexus.search_expand("revenue growth Q1")
        assert resp.status_code == 200

        data = resp.json()
        # Response should contain expanded/alternative queries
        expanded = (
            data.get("expanded_query")
            or data.get("expansions", [])
            or data.get("queries", [])
        )
        assert expanded, "Expansion returned no alternative queries"

    def test_expanded_query_finds_more(self, nexus: NexusClient) -> None:
        """Expanded query retrieves at least as many results as original."""
        if not _expansion_available(nexus):
            pytest.skip("Query expansion not available")

        original_query = "AI tuning system"

        # Search with original query
        orig_resp = nexus.search_query(original_query, search_type="keyword", limit=20)
        assert orig_resp.status_code == 200
        orig_results = extract_search_results(orig_resp)
        orig_count = len(orig_results)

        # Get expanded query
        exp_resp = nexus.search_expand(original_query)
        assert exp_resp.status_code == 200
        exp_data = exp_resp.json()

        expanded = (
            exp_data.get("expanded_query")
            or (exp_data.get("expansions", [None])[0])
            or (exp_data.get("queries", [None])[0])
        )
        if not expanded or expanded == original_query:
            pytest.skip("Expansion didn't produce a different query")

        # Search with expanded query
        exp_search_resp = nexus.search_query(
            str(expanded), search_type="keyword", limit=20
        )
        assert exp_search_resp.status_code == 200
        exp_results = extract_search_results(exp_search_resp)

        # Expanded query should find at least as many results
        assert len(exp_results) >= orig_count, (
            f"Expanded query found fewer results ({len(exp_results)}) "
            f"than original ({orig_count})"
        )
