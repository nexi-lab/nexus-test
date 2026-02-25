"""memory/018: Selective forgetting — GDPR-style entity purge.

Tests that forgetting an entity removes data from:
store, search index, and knowledge graph (triple verify).
Also verifies no collateral damage to other entities.

Groups: auto, memory, security
"""

from __future__ import annotations

import logging
import uuid

import pytest

from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import assert_memory_purged

from .conftest import ENTITY_MEMORIES, StoreMemoryFn, poll_memory_query_with_latency

logger = logging.getLogger(__name__)

QUERY_LATENCY_SLO_MS = 500.0


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

        memory_ids: list[str] = []
        for mem in ENTITY_MEMORIES:
            if mem["entity"] == "bob":
                resp = store_memory(
                    mem["content"].replace("Bob", entity_name),
                    metadata={**mem.get("metadata", {}), "person": entity_name},
                    timestamp=mem.get("timestamp"),
                )
                assert resp.ok, f"Failed to store entity memory: {resp.error}"
                mid = (resp.result or {}).get("memory_id")
                if mid:
                    memory_ids.append(mid)

        # Verify entity is queryable before forgetting (poll for indexing)
        pre_pr = poll_memory_query_with_latency(
            nexus, entity_name, match_substring=entity_name,
            memory_ids=memory_ids,
        )
        if not pre_pr.results:
            logger.info("Entity not yet indexed; proceeding with forget anyway")

        forget_resp = nexus.memory_forget_entity(entity_name)
        assert forget_resp.ok, f"memory_forget_entity failed: {forget_resp.error}"

        assert_memory_purged(nexus, entity_name)

        logger.info(
            "test_forget_entity_triple_verify: query_latency=%.1fms via_fallback=%s",
            pre_pr.query_latency_ms, pre_pr.via_fallback,
        )
        assert pre_pr.query_latency_ms < QUERY_LATENCY_SLO_MS, (
            f"Query latency {pre_pr.query_latency_ms:.0f}ms exceeds SLO {QUERY_LATENCY_SLO_MS:.0f}ms"
        )

    def test_forget_no_collateral_damage(
        self, nexus: NexusClient, store_memory: StoreMemoryFn
    ) -> None:
        """Forget entity A -> entity B memories intact."""
        tag = uuid.uuid4().hex[:8]
        entity_a = f"alice_{tag}"
        entity_b = f"diana_{tag}"

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
        mid_b = (resp_b.result or {}).get("memory_id")

        # Forget entity A only
        forget_resp = nexus.memory_forget_entity(entity_a)
        assert forget_resp.ok, f"memory_forget_entity failed: {forget_resp.error}"

        assert_memory_purged(nexus, entity_a)

        # Verify entity B is still intact (poll + fallback to GET)
        pr = poll_memory_query_with_latency(
            nexus, entity_b,
            match_substring=entity_b,
            memory_ids=[mid_b] if mid_b else None,
        )

        b_found = any(
            entity_b in (r.get("content", "") if isinstance(r, dict) else str(r))
            for r in pr.results
        )
        assert b_found, (
            f"Entity B ({entity_b}) should still be queryable after "
            f"forgetting entity A. Got: {[r.get('content', '')[:60] if isinstance(r, dict) else '' for r in pr.results[:3]]}"
        )

        logger.info(
            "test_forget_no_collateral_damage: query_latency=%.1fms via_fallback=%s",
            pr.query_latency_ms, pr.via_fallback,
        )
        assert pr.query_latency_ms < QUERY_LATENCY_SLO_MS, (
            f"Query latency {pr.query_latency_ms:.0f}ms exceeds SLO {QUERY_LATENCY_SLO_MS:.0f}ms"
        )
