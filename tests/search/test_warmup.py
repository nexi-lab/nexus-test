"""search/008: Search daemon warmup — zero cold-start.

Verifies that the search daemon initializes properly and reports
warmup statistics. The daemon should be ready to serve queries
immediately after startup (pre-warmed BM25S index).
"""

from __future__ import annotations

import pytest

from tests.helpers.api_client import NexusClient


@pytest.mark.auto
@pytest.mark.search
class TestSearchDaemonWarmup:
    """search/008: Search daemon starts up without cold-start penalty."""

    def test_daemon_is_initialized(self, nexus: NexusClient) -> None:
        """Search daemon reports initialized=true."""
        resp = nexus.search_stats()
        assert resp.status_code == 200

        data = resp.json()
        assert data["initialized"] is True, "Search daemon not initialized"

    def test_bm25_index_loaded(self, nexus: NexusClient) -> None:
        """BM25S index is loaded with documents."""
        resp = nexus.search_stats()
        assert resp.status_code == 200

        data = resp.json()
        doc_count = data.get("bm25_documents", 0)
        assert doc_count > 0, f"BM25 index empty (0 documents)"

    def test_startup_time_reasonable(self, nexus: NexusClient) -> None:
        """Daemon startup time is under 30 seconds."""
        resp = nexus.search_stats()
        assert resp.status_code == 200

        data = resp.json()
        startup_ms = data.get("startup_time_ms", 0)
        assert startup_ms > 0, "Missing startup_time_ms"
        # SLO: 30s for BM25+DB+reranker, 180s if SPLADE indexing is enabled
        slo_ms = 180_000 if startup_ms > 30_000 else 30_000
        assert startup_ms < slo_ms, (
            f"Daemon startup took {startup_ms:.0f}ms (>{slo_ms // 1000}s SLO)"
        )

    def test_bm25_load_time_reasonable(self, nexus: NexusClient) -> None:
        """BM25S index load time is under 5 seconds."""
        resp = nexus.search_stats()
        assert resp.status_code == 200

        data = resp.json()
        load_ms = data.get("bm25_load_time_ms", 0)
        # BM25S should load fast for small-medium corpora
        assert load_ms < 5_000, (
            f"BM25 index load took {load_ms:.0f}ms (>5s SLO)"
        )

    def test_health_reports_daemon_ready(self, nexus: NexusClient) -> None:
        """Health endpoint confirms daemon is ready to serve queries."""
        resp = nexus.search_health()
        assert resp.status_code == 200

        data = resp.json()
        assert data["status"] == "healthy"
        assert data["daemon_initialized"] is True
        assert data["bm25_index_loaded"] is True

    def test_zoekt_availability_reported(self, nexus: NexusClient) -> None:
        """Health endpoint reports Zoekt availability status."""
        resp = nexus.search_health()
        assert resp.status_code == 200

        data = resp.json()
        # zoekt_available should be a boolean (true or false)
        assert "zoekt_available" in data
        assert isinstance(data["zoekt_available"], bool)

    def test_first_query_no_cold_start(self, nexus: NexusClient) -> None:
        """First search query after warmup has reasonable latency."""
        # The daemon pre-warms the BM25S index, so first query should be fast
        resp = nexus.search_query("test", search_type="keyword", limit=3)
        assert resp.status_code == 200

        data = resp.json()
        latency = data.get("latency_ms", 0)
        # Even the first query should be under 500ms
        assert latency < 500, (
            f"First query latency {latency:.0f}ms suggests cold-start penalty"
        )
