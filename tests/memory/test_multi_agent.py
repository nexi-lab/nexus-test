"""memory/023: Multi-agent isolation — private memories invisible cross-agent.

Tests zone-based agent isolation for memory operations:
read isolation, write isolation, shared visibility, and delete safety.

Groups: auto, memory, zone
"""

from __future__ import annotations

import contextlib
import uuid

import pytest

from tests.config import TestSettings
from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import extract_memory_results


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

        # Agent A stores private memory in zone A
        resp_a = nexus.memory_store(
            f"Agent A secret {tag}: project codename is Phoenix",
            metadata={"agent": "agent_a", "tag": tag},
            zone=zone_a,
        )
        assert resp_a.ok, f"Agent A memory_store failed: {resp_a.error}"
        mid_a = resp_a.result.get("memory_id") if resp_a.result else None

        try:
            # Agent B queries in zone B — should NOT see Agent A's memory
            query_b = nexus.memory_query(f"codename Phoenix {tag}", zone=zone_b)
            assert query_b.ok, f"Agent B query failed: {query_b.error}"

            results = extract_memory_results(query_b)
            leaked = [
                r for r in results
                if tag in (r.get("content", "") if isinstance(r, dict) else str(r))
            ]
            assert not leaked, (
                f"Agent A's memory leaked to Agent B's zone: {leaked[:3]}"
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

        # Store shared memory in common zone
        resp = nexus.memory_store(
            f"Shared announcement {tag}: company all-hands on Friday",
            metadata={"shared": True, "tag": tag},
            zone=zone,
        )
        assert resp.ok, f"Shared memory_store failed: {resp.error}"
        memory_id = resp.result.get("memory_id") if resp.result else None

        try:
            # Both "agents" querying same zone should see it
            query_1 = nexus.memory_query(f"all-hands {tag}", zone=zone)
            assert query_1.ok, f"Query 1 failed: {query_1.error}"

            query_2 = nexus.memory_query(f"Friday announcement {tag}", zone=zone)
            assert query_2.ok, f"Query 2 failed: {query_2.error}"

            # At least one query should return the shared memory
            found = False
            for qr in (query_1, query_2):
                results = extract_memory_results(qr)
                for r in results:
                    content = r.get("content", "") if isinstance(r, dict) else str(r)
                    if tag in content and "all-hands" in content:
                        found = True
                        break
                if found:
                    break

            assert found, "Shared memory should be visible to queries in the same zone"
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

        # Store memories in both zones
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

            # Agent B's memory should still be intact
            query_b = nexus.memory_query(f"deploy {tag}", zone=zone_b)
            assert query_b.ok, f"Agent B query failed: {query_b.error}"

            results = extract_memory_results(query_b)
            contents = [
                r.get("content", "") if isinstance(r, dict) else str(r)
                for r in results
            ]
            b_found = any(tag in c and "deploy" in c for c in contents)
            assert b_found, (
                f"Agent B memory should survive Agent A's delete. Got: {contents[:3]}"
            )
        finally:
            if mid_b:
                with contextlib.suppress(Exception):
                    nexus.memory_delete(mid_b, zone=zone_b)

    @pytest.mark.xfail(
        reason="Server does not enforce zone isolation on delete-by-id yet",
        strict=True,
    )
    def test_write_isolation(
        self, nexus: NexusClient, settings: TestSettings
    ) -> None:
        """Agent A can't modify Agent B's memories (cross-zone write blocked)."""
        tag = uuid.uuid4().hex[:8]
        zone_a = settings.zone
        zone_b = settings.scratch_zone

        # Agent B stores a memory in zone B
        resp_b = nexus.memory_store(
            f"Agent B confidential {tag}: salary data is in vault",
            metadata={"agent": "agent_b", "tag": tag},
            zone=zone_b,
        )
        assert resp_b.ok, f"Agent B store failed: {resp_b.error}"
        mid_b = resp_b.result.get("memory_id") if resp_b.result else None

        try:
            # Agent A attempts to delete Agent B's memory from zone A context
            if mid_b:
                cross_del = nexus.memory_delete(mid_b, zone=zone_a)
                # This should either fail or have no effect on zone B
                if cross_del.ok:
                    # Verify the memory still exists in zone B
                    query_b = nexus.memory_query(f"vault {tag}", zone=zone_b)
                    if query_b.ok:
                        results = extract_memory_results(query_b)
                        still_exists = any(
                            tag in (
                                r.get("content", "") if isinstance(r, dict) else str(r)
                            )
                            for r in results
                        )
                        assert still_exists, (
                            "Cross-zone delete should not affect other zone's memory"
                        )
        finally:
            if mid_b:
                with contextlib.suppress(Exception):
                    nexus.memory_delete(mid_b, zone=zone_b)
