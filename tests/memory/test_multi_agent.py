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


def _create_non_admin_key(
    nexus: NexusClient,
    user_id: str,
    zone_id: str,
    label: str,
) -> str | None:
    """Create a non-admin API key via POST /api/v2/auth/keys.

    Returns the raw key string, or None if the endpoint is unavailable.
    """
    resp = nexus.http.post(
        "/api/v2/auth/keys",
        json={
            "label": label,
            "user_id": user_id,
            "zone_id": zone_id,
            "subject_type": "agent",
            "is_admin": False,
        },
    )
    if resp.status_code in (200, 201):
        data = resp.json()
        return data.get("raw_key") or data.get("key")
    return None


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

    def test_write_isolation(
        self, nexus: NexusClient, settings: TestSettings
    ) -> None:
        """Agent A (non-admin) can't delete Agent B's memories cross-zone.

        Creates non-admin API keys to test proper ReBAC zone isolation,
        bypassing the admin bypass that grants admin users full access.
        """
        tag = uuid.uuid4().hex[:8]
        zone_a = settings.zone
        zone_b = settings.scratch_zone

        # Create non-admin keys for each zone
        key_a = _create_non_admin_key(
            nexus, f"agent_a_{tag}", zone_a, f"write-iso-a-{tag}"
        )
        key_b = _create_non_admin_key(
            nexus, f"agent_b_{tag}", zone_b, f"write-iso-b-{tag}"
        )
        if not key_a or not key_b:
            pytest.skip(
                "Cannot create non-admin API keys (auth/keys endpoint unavailable)"
            )

        # Build non-admin clients with their own httpx sessions
        import httpx

        http_a = httpx.Client(
            base_url=settings.url,
            headers={"Authorization": f"Bearer {key_a}"},
            timeout=httpx.Timeout(30.0, connect=10.0),
        )
        http_b = httpx.Client(
            base_url=settings.url,
            headers={"Authorization": f"Bearer {key_b}"},
            timeout=httpx.Timeout(30.0, connect=10.0),
        )
        agent_a = NexusClient(http=http_a, base_url=settings.url, api_key=key_a)
        agent_b = NexusClient(http=http_b, base_url=settings.url, api_key=key_b)

        # Agent B stores a memory in zone B
        resp_b = agent_b.memory_store(
            f"Agent B confidential {tag}: salary data is in vault",
            metadata={"agent": "agent_b", "tag": tag},
            zone=zone_b,
        )
        assert resp_b.ok, f"Agent B store failed: {resp_b.error}"
        mid_b = resp_b.result.get("memory_id") if resp_b.result else None

        try:
            if not mid_b:
                pytest.fail("Agent B memory_store did not return a memory_id")

            # Agent A attempts to delete Agent B's memory from zone A context
            cross_del = agent_a.memory_delete(mid_b, zone=zone_a)

            # Verify the memory still exists in zone B regardless of delete response
            get_resp = agent_b.memory_get(mid_b, zone=zone_b)
            assert get_resp.ok, (
                f"Cross-zone delete should not remove Agent B's memory. "
                f"Delete returned ok={cross_del.ok}, but memory is gone."
            )
        finally:
            if mid_b:
                with contextlib.suppress(Exception):
                    # Use admin client for cleanup
                    nexus.memory_delete(mid_b, zone=zone_b)
            http_a.close()
            http_b.close()
