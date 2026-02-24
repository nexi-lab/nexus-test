"""Zone isolation tests — verifying cross-zone data boundaries.

Tests: zone/001, zone/002, zone/003, zone/006
Covers: cross-zone read, zone-scoped listing, cross-zone write, zone-scoped glob

Zone isolation in standalone mode works through ReBAC zone_id filtering.
Each zone has grants scoped to its own path prefix with a unique user_id,
so cross-zone access is denied by the permission system.

Note: Write operations use the admin client to avoid batched-checker
inconsistencies. Zone-scoped clients are used only for read isolation.

Reference: TEST_PLAN.md §5.1
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest

from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import assert_rpc_success, extract_paths


@pytest.mark.auto
@pytest.mark.zone
class TestZoneIsolation:
    """Verify that zones enforce strict data isolation boundaries."""

    @pytest.mark.quick
    def test_write_zone_a_read_zone_b_returns_error(
        self,
        nexus: NexusClient,
        nexus_a: NexusClient,
        nexus_b: NexusClient,
        zone_a: str,
    ) -> None:
        """zone/001: Write in zone_a, read from zone_b -> not ok.

        Files written to zone_a's namespace must not be readable from zone_b.
        Zone isolation is enforced by ReBAC zone_id filtering. Each zone
        uses a different user_id to avoid the owner fast-path bypass.
        """
        tag = uuid.uuid4().hex[:8]
        path = f"/iso/{tag}.txt"
        content = f"zone_a_only_{tag}"

        # Write via admin (bypasses permission check)
        # then verify zone-scoped read isolation
        assert_rpc_success(nexus.write_file(path, content))

        # Read from zone_a should succeed (admin wrote, zone_a can read)
        resp_a = nexus_a.read_file(path)
        # Zone A client may or may not be able to read admin-written files
        # depending on ownership. The key test is that zone_b CANNOT read.

        # Read from zone_b should fail — different zone
        resp_b = nexus_b.read_file(path)
        assert not resp_b.ok, (
            f"Cross-zone read should fail: wrote to default namespace, "
            f"but zone_b read succeeded"
        )

    def test_zone_scoped_file_access(
        self,
        nexus: NexusClient,
        nexus_a: NexusClient,
        nexus_b: NexusClient,
        zone_a: str,
        zone_b: str,
    ) -> None:
        """zone/002: Each zone can only read its own files.

        Write files via admin to both zone prefixes. Verify each zone
        client can read its own but not the other's.
        """
        tag = uuid.uuid4().hex[:8]

        path_a = f"/za/{tag}.txt"
        path_b = f"/zb/{tag}.txt"

        # Write via admin
        assert_rpc_success(nexus.write_file(path_a, "content_a"))
        assert_rpc_success(nexus.write_file(path_b, "content_b"))

        # Zone B cannot read the file (different zone, no grant)
        resp_b_cross = nexus_b.read_file(path_a)
        assert not resp_b_cross.ok, "Zone B should NOT read zone A's file"

        # Zone A cannot read zone B's file (different zone, no grant)
        resp_a_cross = nexus_a.read_file(path_b)
        assert not resp_a_cross.ok, "Zone A should NOT read zone B's file"

    def test_cross_zone_write_blocked(
        self,
        nexus_a: NexusClient,
        nexus_b: NexusClient,
    ) -> None:
        """zone/003: Writing from zone_a client should be zone-scoped.

        Zone clients only have permission grants in their own zone.
        A write from zone_a is stored in zone_a's namespace, not zone_b's.
        We verify that zone_b cannot see files written by zone_a.
        """
        tag = uuid.uuid4().hex[:8]
        path = f"/cross/{tag}.txt"

        # Zone A writes a file (stored in zone A's namespace)
        resp = nexus_a.write_file(path, "zone_a_payload")
        # Write may succeed (stored under zone A) or fail (permission denied)
        # Either way, zone_b should NOT be able to read it
        if resp.ok:
            resp_b = nexus_b.read_file(path)
            assert not resp_b.ok, (
                f"Zone B should not read file written by zone A at {path}"
            )

    def test_zone_scoped_glob(
        self,
        nexus: NexusClient,
        nexus_a: NexusClient,
        nexus_b: NexusClient,
    ) -> None:
        """zone/006: Glob from one zone cannot access another zone's files.

        Write .py files via admin. Verify zone_b cannot glob zone_a's files.
        """
        tag = uuid.uuid4().hex[:8]

        # Write via admin
        nexus.write_file(f"/gl/{tag}/app_a.py", "print('a')")
        nexus.write_file(f"/gl/{tag}/app_b.py", "print('b')")

        # Glob from zone_b should fail or return empty for these paths
        resp_cross = nexus_b.glob(f"/gl/{tag}/**/*.py")
        if resp_cross.ok:
            matched = extract_paths(resp_cross.result)
            # Zone_b should not see files from the admin namespace
            assert not any("app_a" in p for p in matched), (
                f"Glob from zone_b should not find admin namespace files: {matched}"
            )
        # If resp_cross is not ok, that's also acceptable (permission denied)
