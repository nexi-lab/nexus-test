"""search/005: HERB Q&A accuracy — score against ground truth.

Measures search accuracy against the HERB benchmark QA dataset.
Skips if QA data is not available.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.helpers.api_client import NexusClient
from tests.search.conftest import (
    extract_search_results,
    load_herb_qa,
)


@pytest.mark.auto
@pytest.mark.search
@pytest.mark.perf
class TestHerbQA:
    """search/005: HERB Q&A accuracy benchmark."""

    @pytest.fixture(scope="class")
    def herb_qa_data(self) -> list[dict[str, Any]]:
        """Load HERB QA benchmark data."""
        data = load_herb_qa()
        if not data:
            pytest.skip("HERB QA benchmark data not available (benchmarks/herb/qa/)")
        return data

    def test_herb_qa_answerable_accuracy(
        self, nexus: NexusClient, herb_qa_data: list[dict[str, Any]]
    ) -> None:
        """Answerable questions should return relevant results."""
        answerable = [q for q in herb_qa_data if q.get("answerable", True)][:50]
        if not answerable:
            pytest.skip("No answerable questions in HERB QA dataset")

        hits = 0
        total = len(answerable)
        for qa in answerable:
            query = qa.get("question", "")
            expected = qa.get("answer", qa.get("ground_truth", ""))
            resp = nexus.search_query(query, search_type="hybrid", limit=10)
            if resp.status_code != 200:
                continue

            results = extract_search_results(resp)
            # Check if any result contains relevant content
            for r in results:
                chunk = r.get("chunk_text", "").lower()
                if any(
                    term.lower() in chunk
                    for term in expected.split()[:5]
                    if len(term) > 3
                ):
                    hits += 1
                    break

        accuracy = hits / total if total > 0 else 0
        assert accuracy >= 0.3, (
            f"HERB QA accuracy too low: {accuracy:.1%} ({hits}/{total}). "
            f"Expected >= 30% for basic relevance."
        )

    def test_herb_context_search_finds_enterprise_docs(self, nexus: NexusClient) -> None:
        """Search over HERB enterprise data finds indexed documents."""
        # Use terms known to be in the HERB AutoTuneForce dataset
        resp = nexus.search_query("AutoTuneForce", search_type="keyword", limit=10)
        assert resp.status_code == 200

        results = extract_search_results(resp)
        assert len(results) > 0, "No results for 'AutoTuneForce' query over HERB data"

    def test_herb_context_search_finds_products(self, nexus: NexusClient) -> None:
        """Search finds product-related content from enterprise context."""
        resp = nexus.search_query("product requirements", search_type="keyword", limit=10)
        assert resp.status_code == 200

        results = extract_search_results(resp)
        assert len(results) > 0, "No results for 'product requirements' query"
