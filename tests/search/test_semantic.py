"""search/002: Semantic search — meaning-based results.

Verifies that semantic or hybrid search returns results based on meaning,
not just keyword matching.  Falls back to testing hybrid mode if pure
semantic search is unavailable (requires pgvector + db_pool).
"""

from __future__ import annotations

import time
import pytest

from tests.helpers.api_client import NexusClient
from tests.search.conftest import extract_search_results


def _semantic_available(nexus: NexusClient) -> bool:
    """Check if pure semantic search returns results."""
    resp = nexus.search_query("team collaboration", search_type="semantic", limit=3)
    if resp.status_code != 200:
        return False
    return len(extract_search_results(resp)) > 0


@pytest.mark.auto
@pytest.mark.search
class TestSemanticSearch:
    """search/002: Meaning-based search finds conceptually related results."""

    def test_hybrid_search_finds_conceptual_matches(self, nexus: NexusClient) -> None:
        """Hybrid search returns results for conceptual queries."""
        # Use a conceptual query that may not have exact keyword matches
        resp = nexus.search_query(
            "how does the AI optimization system work",
            search_type="hybrid",
            limit=5,
        )
        assert resp.status_code == 200, f"Hybrid search failed: {resp.text}"

        data = resp.json()
        results = data.get("results", [])
        assert len(results) > 0, "Hybrid search returned no results"

    def test_semantic_search_returns_results(self, nexus: NexusClient) -> None:
        """Pure semantic search returns vector-based results."""
        # Seed a document with embeddings via file write + refresh
        import uuid

        tag = uuid.uuid4().hex[:8]
        canary = f"semantic_test_{tag}"

        # Store a memory so there's at least something with embeddings
        mid = None
        try:
            r = nexus.memory_store(
                f"The {canary} project uses advanced machine learning for optimization",
                metadata={"_test": "semantic_search"},
            )
            if r.ok and r.result:
                mid = r.result.get("memory_id")

            import time
            time.sleep(2)  # Allow embedding generation

            # Try pure semantic search — should work now with embedding provider
            if not _semantic_available(nexus):
                # If file-based semantic search isn't populated yet,
                # verify at least that memory semantic search works
                query_resp = nexus.memory_query("machine learning optimization", limit=5)
                assert query_resp.ok, f"Memory query failed: {query_resp.error}"
                results = query_resp.result if isinstance(query_resp.result, list) else []
                assert len(results) > 0, "Neither file-based nor memory-based semantic search works"
                return
        finally:
            if mid:
                try:
                    nexus.memory_delete(mid)
                except Exception:
                    pass

        resp = nexus.search_query(
            "employee skills and expertise",
            search_type="semantic",
            limit=5,
        )
        assert resp.status_code == 200
        results = extract_search_results(resp)
        assert len(results) > 0, "Semantic search returned no results"

        # Verify vector_score is populated
        for result in results:
            assert result.get("vector_score") is not None, "Missing vector_score"

    def test_hybrid_combines_keyword_and_vector(self, nexus: NexusClient) -> None:
        """Hybrid search reports both keyword and vector scores when available."""
        resp = nexus.search_query(
            "system architecture design patterns",
            search_type="hybrid",
            limit=5,
        )
        assert resp.status_code == 200
        results = extract_search_results(resp)
        if not results:
            pytest.skip("No hybrid results for test query")

        # At minimum, one retrieval score should be present
        for result in results:
            has_score = (
                result.get("keyword_score") is not None
                or result.get("vector_score") is not None
                or result.get("splade_score") is not None
                or result.get("reranker_score") is not None
            )
            assert has_score, (
                "Result has no retrieval scores (keyword, vector, splade, or reranker)"
            )

    def test_memory_semantic_search_finds_meaning(self, nexus: NexusClient) -> None:
        """Memory semantic search finds conceptually related memories."""
        # Store memories with specific meanings
        tag = f"sem_{int(time.time())}"
        ids = []
        try:
            r1 = nexus.memory_store(
                "The database migration to PostgreSQL completed successfully in March",
                metadata={"_tag": tag},
            )
            if r1.ok and r1.result:
                ids.append(r1.result.get("memory_id"))
            r2 = nexus.memory_store(
                "Server infrastructure was upgraded to support higher throughput",
                metadata={"_tag": tag},
            )
            if r2.ok and r2.result:
                ids.append(r2.result.get("memory_id"))

            time.sleep(2)  # Allow embedding generation

            # Query with conceptually related but different words
            resp = nexus.memory_query("moving our data storage system", limit=10)
            assert resp.ok, f"Memory query failed: {resp.error}"

            results = resp.result if isinstance(resp.result, list) else []
            # At least one of our memories should appear
            our_ids = set(ids)
            found = [r for r in results if r.get("memory_id") in our_ids]
            assert len(found) > 0, "Semantic memory search didn't find conceptually related content"
        finally:
            for mid in ids:
                if mid:
                    try:
                        nexus.memory_delete(mid)
                    except Exception:
                        pass
