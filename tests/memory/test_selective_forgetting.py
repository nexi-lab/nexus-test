"""memory/018: Selective forgetting — GDPR-style entity purge.

Tests that forgetting an entity removes data from:
store, search index, and knowledge graph (triple verify).
Also verifies no collateral damage to other entities.

Groups: auto, memory, security
"""

from __future__ import annotations

import uuid

import pytest

from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import extract_memory_results, assert_memory_purged

from .conftest import ENTITY_MEMORIES, StoreMemoryFn


@pytest.mark.auto
@pytest.mark.memory
@pytest.mark.security
class TestSelectiveForgetting:
    """memory/018: GDPR-style entity purge from store + index + graph."""

    def test_forget_entity_triple_verify(
        self, nexus: NexusClient, store_memory: StoreMemoryFn
    ) -> None:
        """Forget entity -> purged from store, search index, and graph."""
        tag = uuid.uuid4().hex[:8]
        entity_name = f"bob_{tag}"

        # Store memories about the entity
        for mem in ENTITY_MEMORIES:
            if mem["entity"] == "bob":
                resp = store_memory(
                    mem["content"].replace("Bob", entity_name),
                    metadata={**mem.get("metadata", {}), "person": entity_name},
                    timestamp=mem.get("timestamp"),
                )
                assert resp.ok, f"Failed to store entity memory: {resp.error}"

        # Verify entity is queryable before forgetting
        pre_query = nexus.memory_query(entity_name)
        assert pre_query.ok, f"Pre-forget query failed: {pre_query.error}"

        # Forget the entity
        forget_resp = nexus.memory_forget_entity(entity_name)
        assert forget_resp.ok, f"memory_forget_entity failed: {forget_resp.error}"

        # Triple verify: entity purged from all layers
        assert_memory_purged(nexus, entity_name)

    def test_forget_no_collateral_damage(
        self, nexus: NexusClient, store_memory: StoreMemoryFn
    ) -> None:
        """Forget entity A -> entity B memories intact."""
        tag = uuid.uuid4().hex[:8]
        entity_a = f"alice_{tag}"
        entity_b = f"diana_{tag}"

        # Store memories for both entities
        resp_a = store_memory(
            f"{entity_a} works in engineering",
            metadata={"person": entity_a, "department": "engineering"},
        )
        assert resp_a.ok, f"Failed to store entity A memory: {resp_a.error}"

        resp_b = store_memory(
            f"{entity_b} works in marketing",
            metadata={"person": entity_b, "department": "marketing"},
        )
        assert resp_b.ok, f"Failed to store entity B memory: {resp_b.error}"

        # Forget entity A only
        forget_resp = nexus.memory_forget_entity(entity_a)
        assert forget_resp.ok, f"memory_forget_entity failed: {forget_resp.error}"

        # Verify entity A is purged
        assert_memory_purged(nexus, entity_a)

        # Verify entity B is still intact
        query_b = nexus.memory_query(entity_b)
        assert query_b.ok, f"Post-forget query for B failed: {query_b.error}"

        results = extract_memory_results(query_b)
        contents = [
            r.get("content", "") if isinstance(r, dict) else str(r)
            for r in results
        ]
        b_found = any(entity_b in c for c in contents)
        assert b_found, (
            f"Entity B ({entity_b}) should still be queryable after "
            f"forgetting entity A. Got: {contents[:3]}"
        )
