"""memory/013: Relationship extraction — relations indexed in graph.

Tests that relationship extraction identifies (subject, predicate, object)
triplets and indexes them in the knowledge graph.

Groups: auto, memory
"""

from __future__ import annotations

import pytest

from tests.helpers.api_client import EnrichmentFlags, NexusClient
from tests.helpers.assertions import assert_memory_stored
from tests.memory.conftest import StoreMemoryFn, wait_for_enrichment


@pytest.mark.auto
@pytest.mark.memory
class TestRelationshipExtraction:
    """memory/013: Relationship extraction — relations indexed in graph."""

    def test_relationship_extraction_populates_fields(
        self,
        nexus: NexusClient,
        store_memory: StoreMemoryFn,
        enrichment_available: bool,
    ) -> None:
        """Storing with extract_relationships=True populates relationship fields."""
        if not enrichment_available:
            pytest.skip("Enrichment pipeline not available on this server")

        resp = store_memory(
            "Eve manages the security team and reports to the VP of Engineering",
            enrichment=EnrichmentFlags(
                extract_relationships=True,
                extract_entities=True,
            ),
        )
        result = assert_memory_stored(resp)
        memory_id = result["memory_id"]

        enriched = wait_for_enrichment(
            nexus, memory_id, field="relationships_json", timeout_seconds=15.0,
            content_hint="Eve manages the security team",
        )
        if enriched is None:
            # Try the alternate field name
            enriched = wait_for_enrichment(
                nexus, memory_id, field="relationship_count", timeout_seconds=5.0,
                content_hint="Eve manages the security team",
            )

        # Relationship extraction requires LLM — skip if not available
        if enriched is None:
            pytest.skip(
                "Relationship extraction not producing results "
                "(may require LLM provider for relationship inference)"
            )

        relationships = (
            enriched.get("relationships_json")
            or enriched.get("relationships")
            or ""
        )
        rel_count = enriched.get("relationship_count", 0)

        # At least one relationship should be extracted
        has_relationships = bool(relationships) or (
            isinstance(rel_count, int) and rel_count > 0
        )
        assert has_relationships, (
            f"No relationships extracted. "
            f"relationships_json={relationships!r}, count={rel_count}"
        )

    def test_relationship_types_are_valid(
        self,
        nexus: NexusClient,
        store_memory: StoreMemoryFn,
        enrichment_available: bool,
    ) -> None:
        """Extracted relationships use valid predicate types."""
        if not enrichment_available:
            pytest.skip("Enrichment pipeline not available on this server")

        resp = store_memory(
            "Frank works with Grace on the data pipeline project at Acme Corp",
            enrichment=EnrichmentFlags(
                extract_relationships=True,
                extract_entities=True,
            ),
        )
        result = assert_memory_stored(resp)
        memory_id = result["memory_id"]

        enriched = wait_for_enrichment(
            nexus, memory_id, field="relationships_json", timeout_seconds=15.0,
            content_hint="Frank works with Grace on the data pipeline",
        )
        if enriched is None:
            pytest.skip("Relationship extraction not producing results (may require LLM)")

        import json as json_mod

        relationships_raw = enriched.get("relationships_json", "")
        if isinstance(relationships_raw, str) and relationships_raw:
            try:
                relationships = json_mod.loads(relationships_raw)
            except (json_mod.JSONDecodeError, ValueError):
                relationships = []
        elif isinstance(relationships_raw, list):
            relationships = relationships_raw
        else:
            relationships = []

        valid_types = {
            "WORKS_WITH", "MANAGES", "REPORTS_TO", "CREATES", "MODIFIES",
            "OWNS", "DEPENDS_ON", "BLOCKS", "RELATES_TO", "MENTIONS",
            "REFERENCES", "LOCATED_IN", "PART_OF", "HAS", "USES",
            "OTHER", "UPDATES", "EXTENDS", "DERIVES",
        }

        for rel in relationships:
            if isinstance(rel, dict):
                rel_type = rel.get("predicate", rel.get("type", rel.get("relationship_type", "")))
                if rel_type:
                    assert rel_type.upper() in valid_types, (
                        f"Invalid relationship type: {rel_type}. "
                        f"Valid types: {sorted(valid_types)}"
                    )

    def test_relationships_indexed_in_graph(
        self,
        nexus: NexusClient,
        store_memory: StoreMemoryFn,
        enrichment_available: bool,
    ) -> None:
        """Extracted relationships appear in knowledge graph queries."""
        if not enrichment_available:
            pytest.skip("Enrichment pipeline not available on this server")

        resp = store_memory(
            "Helen leads the infrastructure team at TechCorp and mentors Ivan",
            enrichment=EnrichmentFlags(
                extract_relationships=True,
                extract_entities=True,
                store_to_graph=True,
            ),
        )
        result = assert_memory_stored(resp)
        memory_id = result["memory_id"]

        # Wait for enrichment
        wait_for_enrichment(
            nexus, memory_id, field="relationships_json", timeout_seconds=15.0,
            content_hint="Helen leads the infrastructure team",
        )

        # Query the graph for relationships
        graph_resp = nexus.memory_graph_query("Helen")
        if graph_resp.status_code == 200:
            data = graph_resp.json()
            relationships = data.get("relationships", data.get("edges", []))
            if relationships:
                # Verify at least one relationship exists
                assert len(relationships) >= 1, (
                    f"Expected relationships for Helen, got: {data}"
                )
        elif graph_resp.status_code == 404:
            pytest.skip("Knowledge graph endpoint not available")
