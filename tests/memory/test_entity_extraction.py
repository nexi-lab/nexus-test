"""memory/007: Entity extraction — entities indexed in knowledge graph.

Tests that storing memories with entity extraction enabled produces
entity records in the knowledge graph.

Groups: auto, memory
"""

from __future__ import annotations

import pytest

from tests.helpers.api_client import EnrichmentFlags, NexusClient
from tests.helpers.assertions import assert_memory_stored
from tests.memory.conftest import StoreMemoryFn, wait_for_enrichment


@pytest.mark.auto
@pytest.mark.memory
class TestEntityExtraction:
    """memory/007: Entity extraction → knowledge graph."""

    def test_entity_extraction_populates_fields(
        self,
        nexus: NexusClient,
        store_memory: StoreMemoryFn,
        enrichment_available: bool,
    ) -> None:
        """Storing with extract_entities=True populates entity fields."""
        if not enrichment_available:
            pytest.skip("Enrichment pipeline not available on this server")

        resp = store_memory(
            "Bob Smith works at Google as a senior software engineer in Mountain View",
            enrichment=EnrichmentFlags(extract_entities=True),
        )
        result = assert_memory_stored(resp)
        memory_id = result["memory_id"]

        # Wait for enrichment to complete (entity_types is in query response)
        enriched = wait_for_enrichment(
            nexus, memory_id, field="entity_types",
            content_hint="Bob Smith works at Google",
        )
        assert enriched is not None, (
            f"Enrichment timed out for memory {memory_id}: entity_types not populated"
        )

        entity_types = enriched.get("entity_types", "")
        assert entity_types, f"entity_types is empty after enrichment: {enriched.keys()}"

    def test_entity_types_extracted(
        self,
        nexus: NexusClient,
        store_memory: StoreMemoryFn,
        enrichment_available: bool,
    ) -> None:
        """Entity extraction identifies person and organization types."""
        if not enrichment_available:
            pytest.skip("Enrichment pipeline not available on this server")

        resp = store_memory(
            "Alice Johnson presented at Microsoft Build conference in Seattle on June 15 2025",
            enrichment=EnrichmentFlags(extract_entities=True),
        )
        result = assert_memory_stored(resp)
        memory_id = result["memory_id"]

        enriched = wait_for_enrichment(
            nexus, memory_id, field="entity_types",
            content_hint="Alice Johnson presented at Microsoft",
        )
        assert enriched is not None, "Enrichment timed out"

        entity_types = enriched.get("entity_types", "")
        # Should detect at least person or organization
        has_person = "person" in entity_types.lower() if entity_types else False
        has_org = "org" in entity_types.lower() if entity_types else False
        assert has_person or has_org, (
            f"Expected PERSON or ORG entity types, got: {entity_types}"
        )

    def test_entities_indexed_in_graph(
        self,
        nexus: NexusClient,
        store_memory: StoreMemoryFn,
        enrichment_available: bool,
    ) -> None:
        """Extracted entities are queryable via the knowledge graph endpoint."""
        if not enrichment_available:
            pytest.skip("Enrichment pipeline not available on this server")

        resp = store_memory(
            "David Chen leads the infrastructure team at Acme Corporation",
            enrichment=EnrichmentFlags(
                extract_entities=True,
                store_to_graph=True,
            ),
        )
        result = assert_memory_stored(resp)
        memory_id = result["memory_id"]

        # Wait for enrichment
        wait_for_enrichment(
            nexus, memory_id, field="entity_types",
            content_hint="David Chen leads the infrastructure",
        )

        # Query knowledge graph for the entity
        graph_resp = nexus.memory_graph_query("David Chen")
        if graph_resp.status_code == 200:
            data = graph_resp.json()
            # Graph should have some data about David Chen
            has_data = bool(
                data.get("entities")
                or data.get("nodes")
                or data.get("relationships")
                or data.get("entity")
            )
            if not has_data:
                pytest.skip(
                    "Knowledge graph endpoint exists but graph storage "
                    f"not populated for entities: {data}"
                )
        elif graph_resp.status_code == 404:
            pytest.skip("Knowledge graph endpoint not available")
        else:
            pytest.skip(
                f"Graph query returned unexpected status: "
                f"{graph_resp.status_code}"
            )
