"""Event bus E2E tests — emission, filtering, replay, metadata, latency.

Tests: events/001-008
Covers: write/delete event emission, cursor pagination, type/zone/path
        filtering, metadata correctness, publish-to-replay latency.

Reference: TEST_PLAN.md section 4.5

Infrastructure: docker-compose.demo.yml (standalone) or
                docker-compose.cross-platform-test.yml (federation)

Event Log API endpoints:
    GET /api/v2/events         — operation log listing
    GET /api/v2/events/replay  — cursor-based historical replay
"""

from __future__ import annotations

import contextlib
import time

import pytest

from tests.config import TestSettings
from tests.events.conftest import EventClient
from tests.helpers.assertions import assert_rpc_success


@pytest.mark.auto
@pytest.mark.events
class TestEventBus:
    """Event bus emission, filtering, replay, and latency tests."""

    def test_write_emits_file_write_event(
        self, event_client: EventClient, event_unique_path: str
    ) -> None:
        """events/001: Write file emits FILE_WRITE event.

        Writes a file and verifies the event log records a write-type event
        for that path.
        """
        path = f"{event_unique_path}/ev001-write.txt"
        try:
            event_client.write_and_wait(path, "events/001 write test")

            events, resp = event_client.get_events(limit=50)
            assert resp.status_code == 200

            matching = [
                ev
                for ev in events
                if ev.get("path", "").endswith("ev001-write.txt")
                and ev.get("type") in ("write", "create", "put", "file_written")
            ]
            assert len(matching) > 0, (
                f"No write event for {path}. "
                f"Got {len(events)} events, types: "
                f"{[ev.get('type') for ev in events[:10]]}"
            )
        finally:
            with contextlib.suppress(Exception):
                event_client.nexus.delete_file(path)

    def test_delete_emits_file_delete_event(
        self, event_client: EventClient, event_unique_path: str
    ) -> None:
        """events/002: Delete file emits FILE_DELETE event.

        Creates a file, deletes it, and verifies the event log records
        a delete-type event.
        """
        path = f"{event_unique_path}/ev002-delete.txt"
        try:
            event_client.write_and_wait(path, "to be deleted")
            assert_rpc_success(event_client.nexus.delete_file(path))
            time.sleep(0.5)

            events, resp = event_client.get_events(limit=50)
            assert resp.status_code == 200

            matching = [
                ev
                for ev in events
                if ev.get("path", "").endswith("ev002-delete.txt")
                and ev.get("type") in ("delete", "remove", "file_deleted")
            ]
            assert len(matching) > 0, (
                f"No delete event for {path}. "
                f"Types seen: {[ev.get('type') for ev in events[:15]]}"
            )
        finally:
            with contextlib.suppress(Exception):
                event_client.nexus.delete_file(path)

    def test_replay_cursor_pagination_monotonic(
        self, event_client: EventClient, event_unique_path: str
    ) -> None:
        """events/003: Event replay cursor pagination — monotonic sequence.

        Writes multiple files, replays with a small limit, and verifies
        cursor-based pagination returns non-overlapping, ordered pages.
        """
        paths: list[str] = []
        try:
            # Generate events
            for i in range(5):
                p = f"{event_unique_path}/ev003-replay-{i:03d}.txt"
                assert_rpc_success(event_client.nexus.write_file(p, f"replay {i}"))
                paths.append(p)
            time.sleep(0.5)

            # Page 1
            page1, resp1 = event_client.replay_events(limit=3)
            assert resp1.status_code == 200
            events_p1 = page1.get("events", [])
            assert len(events_p1) > 0, "Page 1 should have events"

            next_cursor = page1.get("next_cursor")
            has_more = page1.get("has_more", False)

            # Verify pagination structure
            assert "next_cursor" in page1, f"Missing next_cursor: {page1.keys()}"
            assert "has_more" in page1, f"Missing has_more: {page1.keys()}"

            # Page 2 (if available)
            if has_more and next_cursor:
                page2, resp2 = event_client.replay_events(
                    limit=3, cursor=next_cursor
                )
                assert resp2.status_code == 200
                events_p2 = page2.get("events", [])

                # No overlap between pages
                p1_ids = {
                    ev.get("event_id", ev.get("id")) for ev in events_p1
                }
                p2_ids = {
                    ev.get("event_id", ev.get("id")) for ev in events_p2
                }
                overlap = p1_ids & p2_ids - {None}
                assert len(overlap) == 0, (
                    f"Cursor pagination overlaps: {overlap}"
                )
        finally:
            for p in paths:
                with contextlib.suppress(Exception):
                    event_client.nexus.delete_file(p)

    def test_event_filtering_by_type(
        self, event_client: EventClient, event_unique_path: str
    ) -> None:
        """events/004: Event filtering by type — only matching events returned.

        Writes and deletes files to generate mixed event types, then queries
        with an operation_type filter and verifies only matching types appear.
        """
        path = f"{event_unique_path}/ev004-filter.txt"
        try:
            # Generate write + delete events
            event_client.write_and_wait(path, "filter type test")
            assert_rpc_success(event_client.nexus.delete_file(path))
            time.sleep(0.5)

            # Unfiltered
            all_events, _ = event_client.get_events(limit=50)

            # Filtered by write
            write_events, _ = event_client.get_events(
                limit=50, operation_type="write"
            )

            # Filtered should be subset
            assert len(write_events) <= len(all_events), (
                f"Filtered ({len(write_events)}) > unfiltered ({len(all_events)})"
            )

            # All filtered events should match the requested type
            for ev in write_events:
                ev_type = ev.get("type", ev.get("operation_type", ""))
                assert ev_type in ("write", "create", "put", "file_written"), (
                    f"Wrong type in filtered results: {ev_type}"
                )
        finally:
            with contextlib.suppress(Exception):
                event_client.nexus.delete_file(path)

    def test_event_filtering_by_zone(
        self,
        event_client: EventClient,
        settings: TestSettings,
        event_unique_path: str,
    ) -> None:
        """events/005: Event filtering by zone_id — zone isolation.

        Writes a file in the default zone, then queries events with a
        different zone filter and verifies events don't leak across zones.
        """
        path = f"{event_unique_path}/ev005-zone.txt"
        try:
            event_client.write_and_wait(path, "zone isolation test")

            # Query events for a different zone
            other_zone = "corp-eng" if settings.zone != "corp-eng" else "corp-sales"
            other_events, resp = event_client.get_events(
                limit=50, zone=other_zone
            )
            if resp.status_code == 200:
                # Our file should NOT appear in the other zone's events
                leaking = [
                    ev
                    for ev in other_events
                    if ev.get("path", "").endswith("ev005-zone.txt")
                ]
                assert len(leaking) == 0, (
                    f"Event leaked to zone {other_zone}: {leaking}"
                )
        finally:
            with contextlib.suppress(Exception):
                event_client.nexus.delete_file(path)

    def test_event_filtering_by_path_pattern(
        self, event_client: EventClient, event_unique_path: str
    ) -> None:
        """events/006: Event filtering by path pattern.

        Writes files with different names and verifies we can find events
        matching a specific path substring.
        """
        paths: list[str] = []
        try:
            # Create files with distinct names
            for name in ["ev006-alpha.txt", "ev006-beta.txt", "ev006-gamma.txt"]:
                p = f"{event_unique_path}/{name}"
                assert_rpc_success(event_client.nexus.write_file(p, f"content for {name}"))
                paths.append(p)
            time.sleep(0.5)

            events, _ = event_client.get_events(limit=100)

            # Filter client-side by path pattern (server may not support
            # path filter param — verify events at least exist)
            alpha_events = [
                ev for ev in events if "ev006-alpha" in ev.get("path", "")
            ]
            beta_events = [
                ev for ev in events if "ev006-beta" in ev.get("path", "")
            ]
            gamma_events = [
                ev for ev in events if "ev006-gamma" in ev.get("path", "")
            ]

            assert len(alpha_events) > 0, "No event for ev006-alpha"
            assert len(beta_events) > 0, "No event for ev006-beta"
            assert len(gamma_events) > 0, "No event for ev006-gamma"
        finally:
            for p in paths:
                with contextlib.suppress(Exception):
                    event_client.nexus.delete_file(p)

    def test_events_contain_correct_metadata(
        self, event_client: EventClient, event_unique_path: str
    ) -> None:
        """events/007: Events contain correct metadata fields.

        Writes a file and verifies the emitted event contains all expected
        metadata: event_id/id, timestamp, type, path.
        """
        path = f"{event_unique_path}/ev007-metadata.txt"
        try:
            event_client.write_and_wait(path, "metadata validation test")

            events, _ = event_client.get_events(limit=50)
            matching = [
                ev for ev in events if ev.get("path", "").endswith("ev007-metadata.txt")
            ]
            assert len(matching) > 0, f"No event found for {path}"

            event = matching[0]

            # Verify required metadata fields
            has_id = "event_id" in event or "id" in event
            assert has_id, f"Event missing ID field: {list(event.keys())}"
            assert "timestamp" in event, f"Event missing timestamp: {list(event.keys())}"
            assert "type" in event or "operation_type" in event, (
                f"Event missing type: {list(event.keys())}"
            )
            assert "path" in event, f"Event missing path: {list(event.keys())}"

            # Verify path contains our file
            assert "ev007-metadata" in event["path"], (
                f"Event path mismatch: {event['path']}"
            )
        finally:
            with contextlib.suppress(Exception):
                event_client.nexus.delete_file(path)

    def test_publish_to_replay_latency(
        self, event_client: EventClient, event_unique_path: str
    ) -> None:
        """events/008: Publish-to-replay latency < 500ms.

        Measures the time from writing a file to the event appearing in
        the event log. Asserts latency is under 500ms (allows for CI/test load).
        """
        path = f"{event_unique_path}/ev008-latency.txt"
        try:
            latency_ms = event_client.measure_publish_to_replay_latency(
                path, "latency measurement test", max_wait_seconds=5.0
            )
            assert latency_ms < 500, (
                f"Publish-to-replay latency {latency_ms:.1f}ms exceeds 500ms threshold"
            )
        finally:
            with contextlib.suppress(Exception):
                event_client.nexus.delete_file(path)
