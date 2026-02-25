"""memory/023: Multi-agent isolation — private memories invisible cross-agent.

Tests zone-based agent isolation for memory operations:
read isolation, write isolation, shared visibility, and delete safety.

Groups: auto, memory, zone
"""

from __future__ import annotations

import contextlib
import logging
import time
import uuid

import pytest

from tests.config import TestSettings
from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import extract_memory_results
from tests.helpers.zone_keys import create_zone_key

from .conftest import poll_memory_query_with_latency

logger = logging.getLogger(__name__)

QUERY_LATENCY_SLO_MS = 500.0


@pytest.mark.auto
@pytest.mark.memory
@pytest.mark.zone
class TestMultiAgentIsolation:
    """memory/023: Agent A private memories invisible to Agent B."""

    def test_private_memory_invisible_cross_agent(
        self, nexus: NexusClient, settings: TestSettings
    ) -> None:
        """Agent A stores in zone A -> Agent B in zone B can't query it."""
        tag = uuid.uuid4().hex[:8]
        zone_a = settings.zone
        zone_b = settings.scratch_zone

        resp_a = nexus.memory_store(
            f"Agent A secret {tag}: project codename is Phoenix",
            metadata={"agent": "agent_a", "tag": tag},
            zone=zone_a,
        )
        assert resp_a.ok, f"Agent A memory_store failed: {resp_a.error}"
        mid_a = resp_a.result.get("memory_id") if resp_a.result else None

        try:
            # Agent B queries in zone B — should NOT see Agent A's memory
            t0 = time.monotonic()
            query_b = nexus.memory_query(f"codename Phoenix {tag}", zone=zone_b)
            query_latency_ms = (time.monotonic() - t0) * 1000
            assert query_b.ok, f"Agent B query failed: {query_b.error}"

            results = extract_memory_results(query_b)
            leaked = [
                r for r in results
                if tag in (r.get("content", "") if isinstance(r, dict) else str(r))
            ]
            assert not leaked, (
                f"Agent A's memory leaked to Agent B's zone: {leaked[:3]}"
            )

            logger.info(
                "test_private_memory_invisible: query_latency=%.1fms",
                query_latency_ms,
            )
            assert query_latency_ms < QUERY_LATENCY_SLO_MS, (
                f"Query latency {query_latency_ms:.0f}ms exceeds SLO {QUERY_LATENCY_SLO_MS:.0f}ms"
            )
        finally:
            if mid_a:
                with contextlib.suppress(Exception):
                    nexus.memory_delete(mid_a, zone=zone_a)

    def test_shared_memory_visible_to_both(
        self, nexus: NexusClient, settings: TestSettings
    ) -> None:
        """Shared memory (same zone) visible to both agents."""
        tag = uuid.uuid4().hex[:8]
        zone = settings.zone

        resp = nexus.memory_store(
            f"Shared announcement {tag}: company all-hands on Friday",
            metadata={"shared": True, "tag": tag},
            zone=zone,
        )
        assert resp.ok, f"Shared memory_store failed: {resp.error}"
        memory_id = resp.result.get("memory_id") if resp.result else None

        try:
            # Poll for the shared memory to be indexed
            pr = poll_memory_query_with_latency(
                nexus, f"all-hands {tag}",
                match_substring=tag,
                memory_ids=[memory_id] if memory_id else None,
                zone=zone,
            )

            found = any(
                tag in (r.get("content", "") if isinstance(r, dict) else str(r))
                and "all-hands" in (r.get("content", "") if isinstance(r, dict) else str(r))
                for r in pr.results
            )
            assert found, "Shared memory should be visible to queries in the same zone"

            logger.info(
                "test_shared_memory_visible: query_latency=%.1fms via_fallback=%s",
                pr.query_latency_ms, pr.via_fallback,
            )
            assert pr.query_latency_ms < QUERY_LATENCY_SLO_MS, (
                f"Query latency {pr.query_latency_ms:.0f}ms exceeds SLO {QUERY_LATENCY_SLO_MS:.0f}ms"
            )
        finally:
            if memory_id:
                with contextlib.suppress(Exception):
                    nexus.memory_delete(memory_id, zone=zone)

    def test_delete_doesnt_leak(
        self, nexus: NexusClient, settings: TestSettings
    ) -> None:
        """Agent A deletes own memory -> Agent B unaffected."""
        tag = uuid.uuid4().hex[:8]
        zone_a = settings.zone
        zone_b = settings.scratch_zone

        resp_a = nexus.memory_store(
            f"Agent A note {tag}: review PR #42",
            metadata={"agent": "agent_a", "tag": tag},
            zone=zone_a,
        )
        assert resp_a.ok, f"Agent A store failed: {resp_a.error}"

        resp_b = nexus.memory_store(
            f"Agent B note {tag}: deploy v2.1",
            metadata={"agent": "agent_b", "tag": tag},
            zone=zone_b,
        )
        assert resp_b.ok, f"Agent B store failed: {resp_b.error}"

        mid_a = resp_a.result.get("memory_id") if resp_a.result else None
        mid_b = resp_b.result.get("memory_id") if resp_b.result else None

        try:
            # Agent A deletes its own memory
            if mid_a:
                del_resp = nexus.memory_delete(mid_a, zone=zone_a)
                assert del_resp.ok, f"Agent A delete failed: {del_resp.error}"

            # Agent B's memory should still be intact (poll + fallback GET)
            pr = poll_memory_query_with_latency(
                nexus, f"deploy {tag}",
                match_substring=tag,
                memory_ids=[mid_b] if mid_b else None,
                zone=zone_b,
            )

            b_found = any(
                tag in (r.get("content", "") if isinstance(r, dict) else str(r))
                and "deploy" in (r.get("content", "") if isinstance(r, dict) else str(r))
                for r in pr.results
            )
            assert b_found, (
                f"Agent B memory should survive Agent A's delete. "
                f"Got: {[r.get('content', '')[:60] if isinstance(r, dict) else '' for r in pr.results[:3]]}"
            )

            logger.info(
                "test_delete_doesnt_leak: query_latency=%.1fms via_fallback=%s",
                pr.query_latency_ms, pr.via_fallback,
            )
            assert pr.query_latency_ms < QUERY_LATENCY_SLO_MS, (
                f"Query latency {pr.query_latency_ms:.0f}ms exceeds SLO {QUERY_LATENCY_SLO_MS:.0f}ms"
            )
        finally:
            if mid_b:
                with contextlib.suppress(Exception):
                    nexus.memory_delete(mid_b, zone=zone_b)

    def test_write_isolation(
        self, nexus: NexusClient, settings: TestSettings
    ) -> None:
        """Agent A can't modify Agent B's memories (cross-zone write blocked).

        Uses non-admin zone-scoped API keys so the server's ReBAC permission
        enforcer is exercised (admin keys bypass zone checks by design).
        """
        tag = uuid.uuid4().hex[:8]
        zone_a = settings.zone
        zone_b = settings.scratch_zone

        # Create non-admin zone-scoped keys
        key_a = create_zone_key(nexus, zone_a, user_id=f"agent_a_{tag}")
        key_b = create_zone_key(nexus, zone_b, user_id=f"agent_b_{tag}")
        client_a = nexus.for_zone(key_a)
        client_b = nexus.for_zone(key_b)

        mid_b: str | None = None
        try:
            # Agent B stores a memory in zone B using its own key
            resp_b = client_b.memory_store(
                f"Agent B confidential {tag}: salary data is in vault",
                metadata={"agent": "agent_b", "tag": tag},
                zone=zone_b,
            )
            assert resp_b.ok, f"Agent B store failed: {resp_b.error}"
            mid_b = resp_b.result.get("memory_id") if resp_b.result else None
            assert mid_b, "Agent B store did not return memory_id"

            # Agent A (non-admin, zone A) tries to delete Agent B's memory
            cross_del = client_a.memory_delete(mid_b, zone=zone_a)

            # Verify Agent B's memory still exists via admin client
            get_resp = nexus.memory_get(mid_b)
            assert get_resp.ok and get_resp.result, (
                "Agent B's memory should survive cross-zone delete attempt. "
                f"cross_del.ok={cross_del.ok}, get_resp.ok={get_resp.ok}"
            )
            mem = get_resp.result.get("memory", get_resp.result)
            assert tag in str(mem.get("content", "")), (
                "Agent B's memory content should be intact after cross-zone delete"
            )
        finally:
            # Admin cleanup
            if mid_b:
                with contextlib.suppress(Exception):
                    nexus.memory_delete(mid_b, zone=zone_b)
            with contextlib.suppress(Exception):
                client_a.http.close()
            with contextlib.suppress(Exception):
                client_b.http.close()
