"""search/004: Index on write — files become immediately searchable.

Verifies that files written to NexusFS become searchable after
index refresh is triggered.
"""

from __future__ import annotations

import time
import uuid

import pytest

from tests.helpers.api_client import NexusClient
from tests.search.conftest import extract_search_results


@pytest.mark.auto
@pytest.mark.search
class TestIndexOnWrite:
    """search/004: Written files become searchable after refresh."""

    def test_memory_immediately_queryable(self, nexus: NexusClient) -> None:
        """Stored memories are immediately queryable via the query endpoint."""
        tag = uuid.uuid4().hex[:8]
        canary = f"indexwrite_canary_{tag}_photosynthesis"
        mid = None
        try:
            resp = nexus.memory_store(
                f"The {canary} process converts sunlight into energy",
                metadata={"_test": "index_on_write"},
            )
            assert resp.ok, f"Store failed: {resp.error}"
            mid = (resp.result or {}).get("memory_id")

            # Memory should be immediately queryable (no embedding needed)
            time.sleep(1)
            query_resp = nexus.memory_query(canary, limit=5)
            assert query_resp.ok, f"Query failed: {query_resp.error}"

            results = query_resp.result if isinstance(query_resp.result, list) else []
            found = any(mid == r.get("memory_id") for r in results)
            assert found, f"Memory {mid} not found in query results"
        finally:
            if mid:
                try:
                    nexus.memory_delete(mid)
                except Exception:
                    pass

    def test_file_searchable_after_refresh(
        self, nexus: NexusClient, make_searchable_file
    ) -> None:
        """File becomes searchable after write + refresh + BM25 rebuild."""
        tag = uuid.uuid4().hex[:8]
        canary = f"idxwrite_{tag}_bioluminescence"
        make_searchable_file(
            f"canary_{tag}.txt",
            f"The deep-sea {canary} creature glows in the dark ocean depths",
        )

        # Daemon debounces refresh for ~5s, then reads file + indexes + force-merges delta.
        # Poll until the canary appears in keyword search results.
        found = False
        attempts = 10
        for attempt in range(attempts):
            time.sleep(3)
            resp = nexus.search_query(canary, search_type="keyword", limit=5)
            if resp.status_code == 200:
                results = extract_search_results(resp)
                for r in results:
                    if canary in r.get("chunk_text", ""):
                        found = True
                        break
            if found:
                break

        assert found, (
            f"File with canary '{canary}' not found in BM25S after "
            f"{attempts * 3}s — dynamic reindex may be broken"
        )

    def test_write_refresh_search_latency(self, nexus: NexusClient) -> None:
        """Search latency remains reasonable after index refresh."""
        resp = nexus.search_query("architecture", search_type="keyword", limit=5)
        assert resp.status_code == 200

        data = resp.json()
        latency = data.get("latency_ms", 0)
        # BM25S keyword search should be fast (< 500ms)
        assert latency < 500, f"Search latency too high: {latency}ms"
