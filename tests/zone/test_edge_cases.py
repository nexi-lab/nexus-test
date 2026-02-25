"""Zone edge-case tests — error handling and input validation.

Tests: zone/011, zone/012
Covers: deleted zone errors, invalid zone ID rejection

Reference: TEST_PLAN.md §4.2
"""

from __future__ import annotations

import contextlib
import uuid

import pytest

from tests.helpers.api_client import NexusClient
from tests.helpers.zone_keys import create_zone_direct, create_zone_key, delete_zone_direct


@pytest.mark.auto
@pytest.mark.zone
class TestZoneEdgeCases:
    """Verify zone operations handle edge cases cleanly."""

    def test_deleted_zone_read_fails_cleanly(
        self,
        nexus: NexusClient,
        worker_id: str,
    ) -> None:
        """zone/011: Reading from a deleted zone returns a clear error (not 500).

        After zone deletion, operations should fail with a client-level
        error (4xx or descriptive RPC error), not a server crash.
        """
        tag = uuid.uuid4().hex[:8]
        zone_id = f"test-{worker_id}-{tag}"

        # Create zone (REST with SQLite fallback)
        create_resp = nexus.create_zone(zone_id)
        if create_resp.status_code not in (200, 201):
            try:
                create_zone_direct(zone_id, f"Deleted zone test {zone_id}")
            except RuntimeError:
                pytest.skip("Zone creation not available")

        # Create a key for the zone, then delete the zone
        raw_key = create_zone_key(nexus, zone_id, name=f"test-{zone_id}")
        zone_client = nexus.for_zone(raw_key)

        try:
            # Terminate the zone
            delete_resp = nexus.delete_zone(zone_id)
            if delete_resp.status_code not in (200, 202, 204):
                delete_zone_direct(zone_id)

            # Read from deleted zone — should fail cleanly
            resp = zone_client.read_file("/any/path.txt")
            assert not resp.ok, "Read from deleted zone should fail"

            # Verify it's not a 500 (server error)
            if resp.error is not None:
                assert resp.error.code != -500, (
                    f"Deleted zone read should not cause 500: {resp.error.message}"
                )
        finally:
            zone_client.http.close()
            with contextlib.suppress(Exception):
                delete_zone_direct(zone_id)

    def test_invalid_zone_id_rejected(
        self,
        nexus: NexusClient,
    ) -> None:
        """zone/012: Zone IDs with injection patterns are rejected at zone creation.

        Validates that the server rejects zone IDs containing
        SQL injection or path traversal patterns.
        """
        invalid_ids = [
            "../../../etc/passwd",
            "zone'; DROP TABLE zones; --",
            "zone/../../root",
            "",  # empty string
        ]

        for zone_id in invalid_ids:
            resp = nexus.create_zone(zone_id, name=f"Invalid {zone_id}")
            # Server should reject with 4xx, never crash with 5xx
            assert resp.status_code != 500, (
                f"Invalid zone_id {zone_id!r} should not cause 500: "
                f"{resp.status_code} {resp.text}"
            )
            # REST API validates zone_id format (3-63 chars, slug pattern)
            # so invalid IDs should be rejected outright
            assert resp.status_code in (400, 401, 404, 422), (
                f"Invalid zone_id {zone_id!r} should be rejected, "
                f"got {resp.status_code}: {resp.text}"
            )
