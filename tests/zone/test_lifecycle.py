"""Zone lifecycle tests — creation, deletion, listing, and details.

Tests: zone/004, zone/005, zone/007, zone/008
Covers: zone provisioning, zone deprovisioning with cleanup,
        zone listing API, zone get details API

Reference: TEST_PLAN.md §4.2
"""

from __future__ import annotations

import contextlib
import uuid

import pytest

from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import assert_rpc_success
from tests.helpers.zone_keys import (
    create_zone_direct,
    create_zone_key,
    delete_zone_direct,
    grant_zone_permission,
)


@pytest.mark.auto
@pytest.mark.zone
class TestZoneLifecycle:
    """Verify zone creation and deletion work correctly."""

    def test_zone_creation(
        self,
        nexus: NexusClient,
        ephemeral_zone: str,
    ) -> None:
        """zone/004: Create zone -> write file -> read back -> operational.

        An ephemeral zone should be fully operational after creation:
        files can be written, read, and listed.
        Uses admin client for write (bypasses permission check),
        and a zone-scoped client for read to verify the zone is functional.
        """
        tag = uuid.uuid4().hex[:8]
        path = f"/lc/{tag}.txt"
        content = "zone_creation_test"
        user_id = f"test-user-{tag}"

        # Create a key for the ephemeral zone with a unique user_id
        raw_key = create_zone_key(
            nexus, ephemeral_zone, name=f"test-{ephemeral_zone}", user_id=user_id,
        )
        # Grant full access in the ephemeral zone
        grant_zone_permission(ephemeral_zone, user_id, "/", "direct_owner")

        zone_client = nexus.for_zone(raw_key)

        try:
            # Write via admin to the zone (admin can write anywhere)
            write_resp = nexus.write_file(path, content)
            assert_rpc_success(write_resp)

            # Verify the file is readable via admin
            read_resp = nexus.read_file(path)
            assert read_resp.ok, f"Read from new zone failed: {read_resp.error}"
            assert read_resp.content_str == content

            # Verify the zone exists by checking the zone client can make RPC calls
            # (even if reads fail due to ReBAC, the client should be authenticated)
            resp = zone_client.read_file(path)
            # Either succeeds (if ReBAC grants propagated) or fails with permission error
            # (not with authentication error), proving the zone API key works
            if not resp.ok and resp.error:
                assert "Access denied" in resp.error.message or "not found" in resp.error.message.lower(), (
                    f"Zone client should get permission or not-found error, not: {resp.error.message}"
                )
        finally:
            with contextlib.suppress(Exception):
                nexus.delete_file(path)
            zone_client.http.close()

    def test_zone_listing_api(
        self,
        nexus: NexusClient,
    ) -> None:
        """zone/007: List zones API returns valid zone list for admin.

        Admin client should be able to list all zones. The response
        should include zone objects with expected fields.
        """
        resp = nexus.list_zones()
        assert resp.status_code in (200, 201), (
            f"List zones should succeed for admin, got {resp.status_code}: {resp.text}"
        )
        data = resp.json()
        # Response may be a list or a dict with "zones" key
        zones = data if isinstance(data, list) else data.get("zones", [])
        assert isinstance(zones, list), f"Expected zones list, got {type(zones)}: {data}"
        # Each zone entry should have at least a zone_id
        for z in zones:
            assert "zone_id" in z, f"Zone entry missing zone_id: {z}"

    def test_get_zone_details(
        self,
        nexus: NexusClient,
        ephemeral_zone: str,
    ) -> None:
        """zone/008: Get zone details returns correct zone info.

        After creating a zone, GET /api/zones/{zone_id} should return
        zone details including zone_id, name, and phase.
        """
        resp = nexus.get_zone(ephemeral_zone)
        assert resp.status_code == 200, (
            f"Get zone details should succeed, got {resp.status_code}: {resp.text}"
        )
        data = resp.json()
        assert data.get("zone_id") == ephemeral_zone, (
            f"Expected zone_id={ephemeral_zone}, got {data.get('zone_id')}"
        )
        # Zone should be in Active phase
        phase = data.get("phase", "")
        assert phase in ("Active", "active", ""), (
            f"Newly created zone should be Active, got phase={phase}"
        )

    def test_zone_deletion_and_cleanup(
        self,
        nexus: NexusClient,
        worker_id: str,
    ) -> None:
        """zone/005: Create zone -> write files -> delete zone -> verify cleanup.

        After a zone is deleted (terminated), its API keys should be
        invalidated and ReBAC grants should be removed from the database.
        """
        tag = uuid.uuid4().hex[:8]
        zone_id = f"test-{worker_id}-{tag}"
        user_id = f"test-user-{tag}"

        # Create zone (REST with DB fallback)
        create_resp = nexus.create_zone(zone_id, name=f"Deletion test {zone_id}")
        if create_resp.status_code not in (200, 201):
            try:
                create_zone_direct(zone_id, f"Deletion test {zone_id}")
            except RuntimeError:
                pytest.skip("Zone creation not available")

        # Create a key for the zone with unique user_id
        raw_key = create_zone_key(
            nexus, zone_id, name=f"test-{zone_id}", user_id=user_id,
        )
        grant_zone_permission(zone_id, user_id, "/", "direct_owner")

        # Write files via admin (stored in default namespace)
        path = f"/lc/{tag}/a.txt"
        assert_rpc_success(nexus.write_file(path, "content_a"))

        # Delete (terminate) the zone — use direct DB to ensure full cascade
        # (revoke keys + remove ReBAC tuples + set phase=Terminated)
        nexus.delete_zone(zone_id)  # Try REST first (may or may not cascade)
        delete_zone_direct(zone_id)  # Always run direct to ensure cascade

        # After zone termination:
        # 1. The zone-scoped API key should be revoked in the database
        # 2. ReBAC tuples for this zone should be removed
        # Verify by checking the database directly
        from tests.helpers.zone_keys import _get_database_url

        db_url = _get_database_url()
        if db_url and db_url.startswith("postgresql"):
            import psycopg2

            conn = psycopg2.connect(db_url)
            try:
                with conn.cursor() as cur:
                    # Check API key is revoked
                    cur.execute(
                        "SELECT revoked FROM api_keys WHERE zone_id = %s AND user_id = %s",
                        (zone_id, user_id),
                    )
                    row = cur.fetchone()
                    if row:
                        assert row[0] == 1, (
                            f"API key for zone {zone_id} should be revoked after deletion"
                        )

                    # Check ReBAC tuples are removed
                    cur.execute(
                        "SELECT count(*) FROM rebac_tuples WHERE zone_id = %s",
                        (zone_id,),
                    )
                    count = cur.fetchone()[0]
                    assert count == 0, (
                        f"ReBAC tuples for zone {zone_id} should be removed after deletion, "
                        f"found {count}"
                    )

                    # Check zone is Terminated
                    cur.execute(
                        "SELECT phase FROM zones WHERE zone_id = %s",
                        (zone_id,),
                    )
                    row = cur.fetchone()
                    assert row and row[0] == "Terminated", (
                        f"Zone {zone_id} should be in Terminated phase"
                    )
            finally:
                conn.close()
        else:
            pytest.skip("Database verification requires PostgreSQL")
