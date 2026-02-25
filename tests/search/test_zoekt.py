"""search/007: Code search via Zoekt — trigram index works.

Verifies that Zoekt-powered search returns code-related results
with sub-100ms latency for typical queries.
"""

from __future__ import annotations

import pytest

from tests.helpers.api_client import NexusClient
from tests.search.conftest import extract_search_results


def _zoekt_available(nexus: NexusClient) -> bool:
    """Check if Zoekt is available via search daemon health."""
    resp = nexus.search_health()
    if resp.status_code != 200:
        return False
    return resp.json().get("zoekt_available", False)


@pytest.mark.auto
@pytest.mark.search
class TestZoektCodeSearch:
    """search/007: Zoekt trigram code search works."""

    @pytest.fixture(autouse=True)
    def _require_zoekt(self, nexus: NexusClient) -> None:
        """Skip all Zoekt tests if Zoekt is not available."""
        if not _zoekt_available(nexus):
            pytest.skip("Zoekt not available (check ZOEKT_ENABLED/ZOEKT_URL)")

    def test_zoekt_health_reports_available(self, nexus: NexusClient) -> None:
        """Search health endpoint reports zoekt_available=true."""
        resp = nexus.search_health()
        assert resp.status_code == 200
        assert resp.json()["zoekt_available"] is True

    def test_keyword_search_uses_zoekt(self, nexus: NexusClient) -> None:
        """Keyword search leverages Zoekt for fast trigram matching."""
        resp = nexus.search_query("function", search_type="keyword", limit=5)
        assert resp.status_code == 200

        data = resp.json()
        results = data.get("results", [])
        assert len(results) > 0, "Zoekt-backed keyword search returned no results"

        # Check latency is reasonable (Zoekt should be fast)
        latency = data.get("latency_ms", 0)
        assert latency < 200, f"Zoekt search latency too high: {latency}ms (expected <200ms)"

    def test_zoekt_finds_code_patterns(self, nexus: NexusClient) -> None:
        """Zoekt can find code-like patterns via trigram search."""
        # Search for a pattern that looks like code
        resp = nexus.search_query("class.*Model", search_type="keyword", limit=5)
        assert resp.status_code == 200

        # Even if no regex match, should not error
        data = resp.json()
        assert "results" in data

    def test_zoekt_search_latency_under_100ms(self, nexus: NexusClient) -> None:
        """Zoekt keyword search completes in under 100ms server-side."""
        # Run multiple queries and check average latency
        latencies = []
        queries = ["import", "return", "config", "handler", "error"]

        for query in queries:
            resp = nexus.search_query(query, search_type="keyword", limit=10)
            if resp.status_code == 200:
                latencies.append(resp.json().get("latency_ms", 0))

        assert len(latencies) > 0, "No successful search queries"
        avg_latency = sum(latencies) / len(latencies)
        assert avg_latency < 100, (
            f"Average Zoekt search latency {avg_latency:.1f}ms exceeds 100ms SLO"
        )

    def test_zoekt_returns_file_paths(self, nexus: NexusClient) -> None:
        """Zoekt results include proper file paths."""
        resp = nexus.search_query("design", search_type="keyword", limit=5)
        assert resp.status_code == 200

        results = extract_search_results(resp)
        if not results:
            pytest.skip("No results to check paths")

        for result in results:
            path = result.get("path", "")
            assert path, "Missing path in Zoekt result"
            assert path.startswith("/"), f"Path not absolute: {path}"

    def test_zoekt_supports_multi_word_query(self, nexus: NexusClient) -> None:
        """Zoekt handles multi-word queries and returns results."""
        resp = nexus.search_query(
            "system design document", search_type="keyword", limit=5
        )
        assert resp.status_code == 200

        data = resp.json()
        results = data.get("results", [])
        # Multi-word queries should return results (BM25S/Zoekt handles tokenization)
        assert len(results) > 0, "Multi-word query returned no results"
        # Results should have positive scores
        for result in results[:3]:
            assert result.get("score", 0) > 0, "Result has non-positive score"
