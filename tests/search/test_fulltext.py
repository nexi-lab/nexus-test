"""search/001: Full-text search — matching files returned.

Verifies that keyword (BM25S) search returns documents matching query terms.
Uses the pre-indexed HERB enterprise-context data in the BM25S index.
"""

from __future__ import annotations

import uuid
import pytest

from tests.helpers.api_client import NexusClient
from tests.search.conftest import extract_search_results


@pytest.mark.auto
@pytest.mark.search
class TestFullTextSearch:
    """search/001: Keyword search returns matching documents."""

    def test_keyword_search_returns_results(self, nexus: NexusClient) -> None:
        """BM25S keyword search finds documents containing query terms."""
        resp = nexus.search_query("system design document", search_type="keyword", limit=5)
        assert resp.status_code == 200, f"Search failed: {resp.text}"

        data = resp.json()
        results = data.get("results", [])
        assert len(results) > 0, "Keyword search returned no results"
        assert data["search_type"] == "keyword"

    def test_keyword_search_scores_are_positive(self, nexus: NexusClient) -> None:
        """All BM25S results have positive relevance scores."""
        resp = nexus.search_query("engineering team performance", search_type="keyword", limit=10)
        assert resp.status_code == 200

        results = extract_search_results(resp)
        if not results:
            pytest.skip("No results for keyword query (index may be empty)")

        for result in results:
            assert result.get("score", 0) > 0, f"Non-positive score: {result}"
            assert result.get("keyword_score") is not None, "Missing keyword_score"

    def test_keyword_search_respects_limit(self, nexus: NexusClient) -> None:
        """Search respects the limit parameter."""
        for limit in (1, 3, 5):
            resp = nexus.search_query("document", search_type="keyword", limit=limit)
            assert resp.status_code == 200
            results = extract_search_results(resp)
            assert len(results) <= limit, f"Got {len(results)} results for limit={limit}"

    def test_keyword_search_returns_chunk_text(self, nexus: NexusClient) -> None:
        """Results include chunk_text with the matching content."""
        resp = nexus.search_query("optimization", search_type="keyword", limit=3)
        assert resp.status_code == 200

        results = extract_search_results(resp)
        if not results:
            pytest.skip("No results for 'optimization' query")

        for result in results:
            assert "chunk_text" in result, "Missing chunk_text in result"
            assert len(result["chunk_text"]) > 0, "Empty chunk_text"

    def test_keyword_search_results_have_paths(self, nexus: NexusClient) -> None:
        """All results include a file path."""
        resp = nexus.search_query("product requirements", search_type="keyword", limit=5)
        assert resp.status_code == 200

        results = extract_search_results(resp)
        if not results:
            pytest.skip("No keyword results for test query")

        for result in results:
            assert "path" in result, "Missing path in result"
            assert result["path"].startswith("/"), f"Path not absolute: {result['path']}"

    def test_keyword_search_handles_no_match_gracefully(self, nexus: NexusClient) -> None:
        """Unusual queries return 200 without server errors."""
        gibberish = f"xyzzy_{uuid.uuid4().hex[:12]}_qqq"
        resp = nexus.search_query(gibberish, search_type="keyword", limit=5)
        assert resp.status_code == 200, f"Search failed on unusual query: {resp.text}"

        data = resp.json()
        # BM25S may still return partial matches — that's acceptable
        # The key invariant is no server error
        assert "results" in data, "Missing results key in response"

    def test_keyword_search_reports_latency(self, nexus: NexusClient) -> None:
        """Search response includes latency_ms metric."""
        resp = nexus.search_query("design", search_type="keyword", limit=3)
        assert resp.status_code == 200

        data = resp.json()
        assert "latency_ms" in data, "Missing latency_ms in response"
        assert data["latency_ms"] >= 0, f"Negative latency: {data['latency_ms']}"
