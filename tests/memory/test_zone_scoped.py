"""memory/006: Zone-scoped memory — not visible cross-zone.

Tests that memories stored in one zone are not visible when querying
from a different zone.

Groups: auto, memory, zone
"""

from __future__ import annotations

import time

import pytest

from tests.config import TestSettings
from tests.helpers.api_client import NexusClient, RpcResponse
from tests.helpers.assertions import extract_memory_results


def _retry_on_rate_limit(fn, *, max_retries: int = 4, backoff: float = 20.0) -> RpcResponse:
    """Retry a callable that returns RpcResponse when rate-limited (429).

    Uses fixed backoff since the rate limit window is 1 minute.
    """
    for attempt in range(max_retries + 1):
        resp = fn()
        if resp.ok or not resp.error or resp.error.code != -429:
            return resp
        if attempt < max_retries:
            time.sleep(backoff)
    return resp


@pytest.mark.auto
@pytest.mark.memory
@pytest.mark.zone
class TestZoneScopedMemory:
    """memory/006: Zone-scoped memory — not visible cross-zone."""

    def test_memory_visible_in_own_zone(
        self, nexus: NexusClient, settings: TestSettings
    ) -> None:
        """Memory stored in a zone is queryable from the same zone."""
        zone = settings.zone
        unique = "zone006_visible_alpha_marker"
        resp = _retry_on_rate_limit(lambda: nexus.memory_store(
            f"This memory with {unique} should be visible in its own zone",
            zone=zone,
            metadata={"_zone_test": True},
        ))
        assert resp.ok, f"Store failed: {resp.error}"
        memory_id = (resp.result or {}).get("memory_id")

        try:
            query_resp = nexus.memory_query(unique, limit=20, zone=zone)
            assert query_resp.ok, f"Query in same zone failed: {query_resp.error}"

            results = extract_memory_results(query_resp)
            contents = [
                r.get("content", "") if isinstance(r, dict) else str(r)
                for r in results
            ]
            found = any(unique in c for c in contents)
            assert found, (
                f"Memory not found in its own zone. "
                f"Got {len(contents)} results: {contents[:3]}"
            )
        finally:
            if memory_id:
                nexus.memory_delete(memory_id, zone=zone)

    def test_memory_not_visible_cross_zone(
        self, nexus: NexusClient, settings: TestSettings
    ) -> None:
        """Memory stored in zone A is NOT visible when querying from zone B."""
        zone_a = settings.zone
        zone_b = settings.scratch_zone
        unique = "zone006_crosszone_beta_marker"

        resp = _retry_on_rate_limit(lambda: nexus.memory_store(
            f"Secret data with {unique} in zone A only",
            zone=zone_a,
            metadata={"_zone_test": True},
        ))
        assert resp.ok, f"Store in zone A failed: {resp.error}"
        memory_id = (resp.result or {}).get("memory_id")

        try:
            # Query from zone B — should NOT find zone A's memory
            query_resp = nexus.memory_query(unique, limit=20, zone=zone_b)
            if query_resp.ok:
                results = extract_memory_results(query_resp)
                matching = [
                    r for r in results
                    if unique in (
                        r.get("content", "") if isinstance(r, dict) else str(r)
                    )
                ]
                assert not matching, (
                    f"Zone isolation breach: zone B can see zone A's memory. "
                    f"Found {len(matching)} matches: {matching[:2]}"
                )
        finally:
            if memory_id:
                nexus.memory_delete(memory_id, zone=zone_a)

    def test_same_content_different_zones_independent(
        self, nexus: NexusClient, settings: TestSettings
    ) -> None:
        """Same content stored in two zones exists independently."""
        zone_a = settings.zone
        zone_b = settings.scratch_zone
        content = "Shared knowledge: the speed of light is 299792458 m/s"

        resp_a = _retry_on_rate_limit(lambda: nexus.memory_store(
            content, zone=zone_a, metadata={"_zone_test": True},
        ))
        resp_b = _retry_on_rate_limit(lambda: nexus.memory_store(
            content, zone=zone_b, metadata={"_zone_test": True},
        ))
        assert resp_a.ok and resp_b.ok

        mid_a = (resp_a.result or {}).get("memory_id")
        mid_b = (resp_b.result or {}).get("memory_id")

        try:
            # IDs should be different
            assert mid_a != mid_b, "Same content in different zones got same memory_id"

            # Deleting from zone A should not affect zone B
            if mid_a:
                nexus.memory_delete(mid_a, zone=zone_a)
            query_b = nexus.memory_query("speed of light", limit=20, zone=zone_b)
            if query_b.ok:
                results = extract_memory_results(query_b)
                contents = [
                    r.get("content", "") if isinstance(r, dict) else str(r)
                    for r in results
                ]
                found_in_b = any("speed of light" in c for c in contents)
                assert found_in_b, "Zone B memory disappeared after zone A deletion"
        finally:
            if mid_a:
                nexus.memory_delete(mid_a, zone=zone_a)
            if mid_b:
                nexus.memory_delete(mid_b, zone=zone_b)
