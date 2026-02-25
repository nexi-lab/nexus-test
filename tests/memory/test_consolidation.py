"""memory/004: ACE consolidation — 50 memories into coherent summary.

Tests that the ACE consolidation engine can merge multiple memories
into a consolidated summary while preserving key facts.

Requires an LLM provider (e.g. ANTHROPIC_API_KEY) on the server.
Skipped automatically when consolidation engine is unavailable.

Groups: auto, memory
"""

from __future__ import annotations

import contextlib
from typing import Any

import pytest

from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import extract_memory_results
from tests.memory.conftest import (
    CONSOLIDATION_KEY_FACTS,
    _generate_consolidation_memories,
)


@pytest.mark.auto
@pytest.mark.memory
class TestACEConsolidation:
    """memory/004: ACE consolidation — 50 memories to coherent summary."""

    MEMORY_COUNT = 50
    KEY_FACT_SURVIVAL_RATE = 0.6  # At least 60% of key facts must survive

    @pytest.fixture(scope="class")
    def consolidation_data(
        self, nexus: NexusClient, consolidation_available: bool
    ):  # type: ignore[override]
        """Seed 50 memories and trigger consolidation (class-scoped)."""
        if not consolidation_available:
            pytest.skip(
                "Consolidation engine not available (no LLM provider configured)"
            )

        memories = _generate_consolidation_memories(self.MEMORY_COUNT)
        created_ids: list[str] = []

        for mem in memories:
            resp = nexus.memory_store(
                mem["content"],
                metadata={
                    "_consolidation_test": True,
                    "category": mem["metadata_category"],
                },
            )
            assert resp.ok, f"Failed to store memory: {resp.error}"
            mid = (resp.result or {}).get("memory_id")
            if mid:
                created_ids.append(mid)

        assert len(created_ids) >= self.MEMORY_COUNT, (
            f"Expected {self.MEMORY_COUNT} memories, stored {len(created_ids)}"
        )

        # Trigger consolidation
        consol_resp = nexus.memory_consolidate()

        yield {
            "source_ids": created_ids,
            "consolidation_response": consol_resp,
            "key_facts": CONSOLIDATION_KEY_FACTS,
        }

        # Cleanup all created memories
        for mid in reversed(created_ids):
            with contextlib.suppress(Exception):
                nexus.memory_delete(mid)

    def test_consolidation_succeeds(
        self, consolidation_data: dict[str, Any]
    ) -> None:
        """Consolidation endpoint returns success."""
        resp = consolidation_data["consolidation_response"]
        assert resp.ok, f"Consolidation failed: {resp.error}"

    def test_key_facts_survive_consolidation(
        self, nexus: NexusClient, consolidation_data: dict[str, Any]
    ) -> None:
        """Key facts are still retrievable after consolidation."""
        key_facts = consolidation_data["key_facts"]
        facts_found = 0

        for fact in key_facts:
            # Use distinctive terms (numbers, proper nouns) for better recall
            key_terms = _extract_key_terms(fact)
            query = " ".join(key_terms[:4]) if key_terms else fact

            resp = nexus.memory_query(query, limit=50)
            if not resp.ok:
                continue

            results = extract_memory_results(resp)
            contents = [
                r.get("content", "") if isinstance(r, dict) else str(r)
                for r in results
            ]

            # Check if any result contains key fact content
            for content in contents:
                # Check for distinctive numeric/named values from the fact
                if any(
                    term in content
                    for term in _extract_key_terms(fact)
                ):
                    facts_found += 1
                    break

        min_required = int(len(key_facts) * self.KEY_FACT_SURVIVAL_RATE)
        assert facts_found >= min_required, (
            f"Only {facts_found}/{len(key_facts)} key facts survived consolidation "
            f"(required {min_required}, rate={self.KEY_FACT_SURVIVAL_RATE})"
        )


def _extract_key_terms(fact: str) -> list[str]:
    """Extract distinctive terms (numbers, proper nouns) from a fact string."""
    terms = []
    for word in fact.replace(",", "").split():
        # Numbers, dollar amounts, percentages, proper nouns
        if (
            word.startswith("$")
            or word.endswith("%")
            or word[0].isdigit()
            or (word[0].isupper() and len(word) > 1 and word not in {"The", "In", "A", "An"})
        ):
            terms.append(word)
    return terms if terms else [fact.split()[0]]
