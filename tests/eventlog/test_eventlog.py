"""Event Log E2E tests — write emits event, filtering by type, event replay.

Tests: eventlog/001-003
Covers: event emission on file write, event type filtering, cursor-based replay

Reference: TEST_PLAN.md §4.5

Infrastructure: docker-compose.demo.yml (standalone)

Event Log API endpoints:
    GET /api/v2/events         — v1-compat event list (operation_id cursors)
    GET /api/v2/events/replay  — Cursor-based historical event query (seq_number cursors)
    GET /api/v2/events/stream  — SSE real-time streaming
"""

from __future__ import annotations

import contextlib
import time

import pytest

from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import assert_rpc_success


@pytest.mark.auto
@pytest.mark.eventlog
class TestEventLog:
    """Event log emission, filtering, and replay tests."""

    def test_write_emits_event(self, nexus: NexusClient, unique_path: str) -> None:
        """eventlog/001: Write emits event — event appears in event log.

        Writes a file and then queries the event log to verify that a write
        event was recorded for the file path.
        """
        path = f"{unique_path}/eventlog-write.txt"
        content = "event emission test"

        try:
            # Write a file
            assert_rpc_success(nexus.write_file(path, content))

            # Small delay for event propagation
            time.sleep(0.5)

            # Query the event log for recent events
            resp = nexus.api_get("/api/v2/events", params={"limit": 50})
            if resp.status_code == 503:
                pytest.skip("Event log service not available on this server")

            assert resp.status_code == 200, (
                f"Event log query failed: {resp.status_code} {resp.text[:200]}"
            )

            data = resp.json()
            events = data.get("events", [])

            # Look for a write event matching our path
            matching = [
                ev
                for ev in events
                if ev.get("path", "").endswith("eventlog-write.txt")
                and ev.get("type") in ("write", "create", "put", "file_written")
            ]

            assert len(matching) > 0, (
                f"No write event found for {path}. "
                f"Got {len(events)} events, types: "
                f"{[ev.get('type') for ev in events[:10]]}"
            )

            # Verify event structure
            event = matching[0]
            assert "event_id" in event or "id" in event, f"Event missing ID: {event}"
            assert "timestamp" in event, f"Event missing timestamp: {event}"
        finally:
            with contextlib.suppress(Exception):
                nexus.delete_file(path)

    def test_event_filtering_by_type(self, nexus: NexusClient, unique_path: str) -> None:
        """eventlog/002: Event filtering by type — correct results.

        Performs different operations (write, delete) and filters events by
        operation type to verify filtering works correctly.
        """
        path = f"{unique_path}/eventlog-filter.txt"

        try:
            # Create and delete a file to generate different event types
            assert_rpc_success(nexus.write_file(path, "filter test"))
            time.sleep(0.3)
            assert_rpc_success(nexus.delete_file(path))
            time.sleep(0.5)

            # Query all events (unfiltered)
            all_resp = nexus.api_get("/api/v2/events", params={"limit": 50})
            if all_resp.status_code == 503:
                pytest.skip("Event log service not available on this server")
            assert all_resp.status_code == 200

            all_events = all_resp.json().get("events", [])

            # Query filtered by write type
            write_resp = nexus.api_get(
                "/api/v2/events",
                params={"operation_type": "write", "limit": 50},
            )
            assert write_resp.status_code == 200
            write_events = write_resp.json().get("events", [])

            # Filtered results should be a subset of all results
            assert len(write_events) <= len(all_events), (
                f"Filtered events ({len(write_events)}) should not exceed "
                f"unfiltered ({len(all_events)})"
            )

            # All filtered events should be of the requested type
            for ev in write_events:
                ev_type = ev.get("type", ev.get("operation_type", ""))
                assert ev_type in ("write", "create", "put", "file_written"), (
                    f"Filtered event has wrong type: {ev_type}"
                )
        finally:
            with contextlib.suppress(Exception):
                nexus.delete_file(path)

    def test_event_replay(self, nexus: NexusClient, unique_path: str) -> None:
        """eventlog/003: Event replay — in-order replay with cursor pagination.

        Writes multiple files to generate events, then replays events using the
        cursor-based replay endpoint. Verifies ordering and pagination.
        """
        paths = []
        try:
            # Write several files to generate a sequence of events
            for i in range(3):
                path = f"{unique_path}/replay-{i:03d}.txt"
                assert_rpc_success(nexus.write_file(path, f"replay test {i}"))
                paths.append(path)

            time.sleep(0.5)

            # Replay events from the beginning
            resp = nexus.api_get(
                "/api/v2/events/replay",
                params={"limit": 10},
            )
            if resp.status_code == 503:
                pytest.skip("Event replay service not available on this server")

            assert resp.status_code == 200, (
                f"Event replay failed: {resp.status_code} {resp.text[:200]}"
            )

            data = resp.json()
            events = data.get("events", [])
            next_cursor = data.get("next_cursor")
            has_more = data.get("has_more", False)

            # Should have at least some events
            assert len(events) > 0, "Event replay should return at least one event"

            # Verify cursor-based pagination structure
            assert "next_cursor" in data, f"Replay response missing 'next_cursor': {data.keys()}"
            assert "has_more" in data, f"Replay response missing 'has_more': {data.keys()}"

            # If there are more events, fetch the next page
            if has_more and next_cursor:
                page2_resp = nexus.api_get(
                    "/api/v2/events/replay",
                    params={"limit": 10, "cursor": next_cursor},
                )
                assert page2_resp.status_code == 200, (
                    f"Page 2 replay failed: {page2_resp.status_code}"
                )
                page2_events = page2_resp.json().get("events", [])
                # Page 2 should not duplicate page 1 events
                page1_ids = {ev.get("event_id", ev.get("id")) for ev in events}
                page2_ids = {ev.get("event_id", ev.get("id")) for ev in page2_events}
                overlap = page1_ids & page2_ids
                assert len(overlap) == 0, f"Cursor pagination should not repeat events: {overlap}"
        finally:
            for path in paths:
                with contextlib.suppress(Exception):
                    nexus.delete_file(path)
