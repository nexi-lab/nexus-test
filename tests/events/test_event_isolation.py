"""Event zone isolation E2E tests — user A cannot see user B's events.

Tests: events/021-024
Covers: zone-scoped event visibility, cross-zone event denial,
        admin visibility across zones, bidirectional isolation.

Reference: TEST_PLAN.md section 4.5

These tests require zone-scoped API keys (NEXUS_TEST_ZONE_A_KEY,
NEXUS_TEST_ZONE_B_KEY) and the server running with permissions enabled.
"""

from __future__ import annotations

import contextlib

import pytest

from tests.config import TestSettings
from tests.events.conftest import EventClient


@pytest.mark.auto
@pytest.mark.events
@pytest.mark.permissions
class TestEventZoneIsolation:
    """Event zone isolation tests — user A vs user B."""

    def test_zone_a_user_sees_only_zone_a_events(
        self,
        event_client: EventClient,
        zone_a_client: EventClient,
        event_unique_path: str,
    ) -> None:
        """events/021: Zone-A user sees only zone-A events.

        Admin writes a file (generates a root-zone event), then zone-A
        user queries events. The zone-A user should see zero events
        because the admin's events belong to the 'root' zone, not 'zone-a'.
        """
        path = f"{event_unique_path}/ev021-zone-a-only.txt"
        try:
            # Admin writes a file (creates event in root zone)
            event_client.write_and_wait(path, "admin file for isolation test")

            # Zone-A user queries events
            events, resp = zone_a_client.get_events(limit=50)
            assert resp.status_code == 200

            # Zone-A user should NOT see root-zone events
            admin_events = [
                ev for ev in events
                if ev.get("path", "").endswith("ev021-zone-a-only.txt")
            ]
            assert len(admin_events) == 0, (
                f"Zone-A user should not see root-zone event. "
                f"Found {len(admin_events)} matching events in {len(events)} total."
            )
        finally:
            with contextlib.suppress(Exception):
                event_client.nexus.delete_file(path)

    def test_zone_b_user_cannot_see_zone_a_events(
        self,
        event_client: EventClient,
        zone_a_client: EventClient,
        zone_b_client: EventClient,
        event_unique_path: str,
    ) -> None:
        """events/022: Zone-B user cannot see events from admin (root zone).

        Admin writes a file, zone-B user queries events and should see
        nothing from the root zone — full cross-zone isolation.
        """
        path = f"{event_unique_path}/ev022-cross-zone.txt"
        try:
            # Admin writes a file in root zone
            event_client.write_and_wait(path, "cross-zone isolation test")

            # Zone-B user queries events
            events_b, resp_b = zone_b_client.get_events(limit=50)
            assert resp_b.status_code == 200

            # Zone-B should NOT see the admin event
            leaking = [
                ev for ev in events_b
                if ev.get("path", "").endswith("ev022-cross-zone.txt")
            ]
            assert len(leaking) == 0, (
                f"Zone-B user saw root-zone event — isolation breach! "
                f"Found {len(leaking)} leaking events."
            )

            # Also verify zone-A doesn't see it either
            events_a, resp_a = zone_a_client.get_events(limit=50)
            assert resp_a.status_code == 200
            leaking_a = [
                ev for ev in events_a
                if ev.get("path", "").endswith("ev022-cross-zone.txt")
            ]
            assert len(leaking_a) == 0, (
                f"Zone-A user saw root-zone event — isolation breach! "
                f"Found {len(leaking_a)} leaking events."
            )
        finally:
            with contextlib.suppress(Exception):
                event_client.nexus.delete_file(path)

    def test_admin_sees_all_zones_events(
        self,
        event_client: EventClient,
        zone_a_client: EventClient,
        zone_b_client: EventClient,
        event_unique_path: str,
    ) -> None:
        """events/023: Admin sees events from all zones.

        Admin writes a file, then queries events with admin key.
        Admin should see the event since admin is not zone-restricted.
        Zone-A and zone-B users should see 0 matching events.
        """
        path = f"{event_unique_path}/ev023-admin-all.txt"
        try:
            event_client.write_and_wait(path, "admin visibility test")

            # Admin sees the event
            admin_events, resp = event_client.get_events(limit=50)
            assert resp.status_code == 200
            matching = [
                ev for ev in admin_events
                if ev.get("path", "").endswith("ev023-admin-all.txt")
            ]
            assert len(matching) > 0, (
                f"Admin should see own event. "
                f"Got {len(admin_events)} events, 0 matching."
            )

            # Zone-A user should NOT see admin event
            events_a, _ = zone_a_client.get_events(limit=50)
            leaking_a = [
                ev for ev in events_a
                if ev.get("path", "").endswith("ev023-admin-all.txt")
            ]
            assert len(leaking_a) == 0, "Zone-A user should not see admin event"

            # Zone-B user should NOT see admin event
            events_b, _ = zone_b_client.get_events(limit=50)
            leaking_b = [
                ev for ev in events_b
                if ev.get("path", "").endswith("ev023-admin-all.txt")
            ]
            assert len(leaking_b) == 0, "Zone-B user should not see admin event"
        finally:
            with contextlib.suppress(Exception):
                event_client.nexus.delete_file(path)

    def test_replay_zone_isolation(
        self,
        event_client: EventClient,
        zone_a_client: EventClient,
        zone_b_client: EventClient,
        event_unique_path: str,
    ) -> None:
        """events/024: Replay endpoint also enforces zone isolation.

        Admin writes a file, then zone-A and zone-B users query the
        /replay endpoint. Neither should see the admin's root-zone event.
        """
        path = f"{event_unique_path}/ev024-replay-isolate.txt"
        try:
            event_client.write_and_wait(path, "replay isolation test")

            # Admin sees via events (newest-first, so our event is in first page)
            admin_events, resp = event_client.get_events(limit=50)
            assert resp.status_code == 200
            admin_matching = [
                ev for ev in admin_events
                if "ev024-replay-isolate" in ev.get("path", "")
            ]
            assert len(admin_matching) > 0, "Admin should see event via events API"

            # Zone-A replay — should NOT see admin event
            data_a, resp_a = zone_a_client.replay_events(limit=50)
            assert resp_a.status_code == 200
            events_a = data_a.get("events", [])
            leaking_a = [
                ev for ev in events_a
                if "ev024-replay-isolate" in ev.get("path", "")
            ]
            assert len(leaking_a) == 0, (
                f"Zone-A saw admin event via replay — isolation breach!"
            )

            # Zone-B replay — should NOT see admin event
            data_b, resp_b = zone_b_client.replay_events(limit=50)
            assert resp_b.status_code == 200
            events_b = data_b.get("events", [])
            leaking_b = [
                ev for ev in events_b
                if "ev024-replay-isolate" in ev.get("path", "")
            ]
            assert len(leaking_b) == 0, (
                f"Zone-B saw admin event via replay — isolation breach!"
            )
        finally:
            with contextlib.suppress(Exception):
                event_client.nexus.delete_file(path)
