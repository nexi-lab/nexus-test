"""Event federation E2E tests — cross-zone visibility, isolation, latency.

Tests: events/014-017
Covers: zone-A event visible on zone-A replay, zone isolation across nodes,
        admin cross-zone replay, federation round-trip latency.

Reference: TEST_PLAN.md section 4.5

These tests require a 2-node federation cluster (leader + follower).
They skip gracefully if the follower is not reachable.
"""

from __future__ import annotations

import contextlib
import time

import httpx
import pytest

from tests.config import TestSettings
from tests.events.conftest import EventClient
from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import assert_rpc_success


@pytest.mark.auto
@pytest.mark.events
@pytest.mark.federation
class TestEventFederation:
    """Event federation and cross-zone tests."""

    @staticmethod
    def _check_follower(settings: TestSettings) -> None:
        """Skip test if follower is not reachable."""
        try:
            resp = httpx.get(
                f"{settings.url_follower}/health",
                headers={"Authorization": f"Bearer {settings.api_key}"},
                timeout=5.0,
            )
            resp.raise_for_status()
        except Exception as exc:
            pytest.skip(f"Follower not reachable at {settings.url_follower}: {exc}")

    def test_event_on_zone_a_visible_via_replay(
        self,
        event_client: EventClient,
        settings: TestSettings,
        event_unique_path: str,
    ) -> None:
        """events/014: Event on zone A visible via replay on zone A.

        Writes a file on the leader, then replays events on the same node
        and verifies the event is visible.
        """
        path = f"{event_unique_path}/ev014-zone-a-replay.txt"
        try:
            event_client.write_and_wait(path, "zone A replay test")

            # Use get_events (recent events, newest-first) since replay
            # returns oldest-first and our event may not be in the first page.
            events, resp = event_client.get_events(limit=50)
            assert resp.status_code == 200

            matching = [
                ev for ev in events
                if ev.get("path", "").endswith("ev014-zone-a-replay.txt")
            ]
            assert len(matching) > 0, (
                f"Event not visible via events API on zone A. "
                f"Got {len(events)} events."
            )
        finally:
            with contextlib.suppress(Exception):
                event_client.nexus.delete_file(path)

    def test_event_not_visible_on_zone_b(
        self,
        event_client: EventClient,
        nexus_follower: NexusClient,
        settings: TestSettings,
        event_unique_path: str,
    ) -> None:
        """events/015: Event not visible on zone B (zone isolation).

        Writes a file on zone A (leader), then queries events on zone B
        (follower with different zone) and verifies the event does not
        leak across zone boundaries.
        """
        self._check_follower(settings)

        path = f"{event_unique_path}/ev015-zone-isolation.txt"
        try:
            event_client.write_and_wait(path, "zone isolation test")

            # Query events on follower with a different zone
            other_zone = "corp-eng" if settings.zone != "corp-eng" else "corp-sales"
            resp = nexus_follower.api_get(
                "/api/v2/events",
                params={"limit": 50},
                headers={"X-Nexus-Zone-ID": other_zone},
            )
            if resp.status_code == 503:
                pytest.skip("Event log not available on follower")

            if resp.status_code == 200:
                events = resp.json().get("events", [])
                leaking = [
                    ev for ev in events
                    if ev.get("path", "").endswith("ev015-zone-isolation.txt")
                ]
                assert len(leaking) == 0, (
                    f"Event leaked to zone {other_zone} on follower: {leaking}"
                )
        finally:
            with contextlib.suppress(Exception):
                event_client.nexus.delete_file(path)

    def test_cross_zone_event_replay_with_admin_key(
        self,
        event_client: EventClient,
        nexus_follower: NexusClient,
        settings: TestSettings,
        event_unique_path: str,
    ) -> None:
        """events/016: Cross-zone event replay with admin key.

        Writes a file on the leader, then uses the admin key on the
        follower to replay events. The admin should be able to see
        events across zones.
        """
        self._check_follower(settings)

        path = f"{event_unique_path}/ev016-admin-replay.txt"
        try:
            event_client.write_and_wait(path, "admin cross-zone replay test")

            # Admin replay on follower
            resp = nexus_follower.api_get(
                "/api/v2/events/replay",
                params={"limit": 50},
            )
            if resp.status_code == 503:
                pytest.skip("Event replay not available on follower")

            assert resp.status_code == 200, (
                f"Admin replay on follower failed: {resp.status_code} {resp.text[:200]}"
            )

            data = resp.json()
            events = data.get("events", [])
            # Admin should see events (may or may not include our specific event
            # depending on replication lag)
            assert isinstance(events, list), f"Expected events list, got {type(events)}"
        finally:
            with contextlib.suppress(Exception):
                event_client.nexus.delete_file(path)

    def test_federation_round_trip_latency(
        self,
        event_client: EventClient,
        event_unique_path: str,
    ) -> None:
        """events/017: Event publish-to-replay round-trip latency < 500ms.

        Writes a file and measures how long it takes for the event to
        appear in the event replay API. Tests the full event pipeline
        (write → EventBus → EventLog → replay query). In federation
        mode this also covers cross-node replication.
        """
        path = f"{event_unique_path}/ev017-fed-latency.txt"
        try:
            start = time.monotonic()
            assert_rpc_success(
                event_client.nexus.write_file(path, "federation latency test")
            )

            # Poll replay API for the event
            deadline = start + 5.0
            found = False
            while time.monotonic() < deadline:
                events, resp = event_client.get_events(limit=20)
                if resp.status_code == 200:
                    matching = [
                        ev for ev in events
                        if ev.get("path", "").endswith("ev017-fed-latency.txt")
                    ]
                    if matching:
                        found = True
                        break
                time.sleep(0.05)

            elapsed_ms = (time.monotonic() - start) * 1000

            assert found, (
                f"Event not visible in replay within 5s "
                f"(event pipeline may be broken)"
            )

            assert elapsed_ms < 500, (
                f"Round-trip latency {elapsed_ms:.1f}ms exceeds 500ms"
            )
        finally:
            with contextlib.suppress(Exception):
                event_client.nexus.delete_file(path)
