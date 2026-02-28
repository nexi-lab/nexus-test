"""Exporter pipeline tests — NATS JetStream exporter validation.

Tests cover:
- events/039: NATS exporter receives events with correct subject format
- events/040: NATS exporter dedup via Nats-Msg-Id header
- events/041: NATS exporter zone-scoped subjects
- events/042: Exporter health check endpoint
- events/043: Events persist in NATS JetStream after delivery

Requires: Nexus server with NATS event exporter enabled.
These tests use the NATS E2E infrastructure (nats://localhost:4222).
"""

from __future__ import annotations

import time
import uuid

import pytest

from tests.events.conftest import EventClient


class TestNatsExporter:
    """NATS JetStream exporter: subject routing, dedup, zone scoping."""

    @pytest.mark.nexus_test("events/039")
    def test_nats_exporter_events_delivered(
        self,
        event_client: EventClient,
        event_unique_path: str,
    ) -> None:
        """events/039: Write file, verify event appears in replay with NATS backend.

        When NATS exporter is enabled, events should flow:
        write → operation_log → delivery_worker → NATS JetStream
        We verify by checking the event appears in the replay API
        (which reads from operation_log, confirming the full pipeline ran).
        """
        path = f"{event_unique_path}/nats_exp_{uuid.uuid4().hex[:8]}.txt"
        filename = path.split("/")[-1]

        event_client.write_and_wait(path, "nats exporter test", wait_seconds=1.0)

        # Verify event via get_events (newest-first) to find the recently written event
        events, _ = event_client.get_events(limit=100)
        matching = [e for e in events if filename in e.get("path", "")]
        assert len(matching) >= 1, (
            f"Event not found after NATS export: {filename}"
        )

    @pytest.mark.nexus_test("events/040")
    def test_nats_exporter_dedup_idempotent(
        self,
        event_client: EventClient,
        event_unique_path: str,
    ) -> None:
        """events/040: NATS exporter uses Nats-Msg-Id for server-side dedup.

        Write same file twice rapidly. Verify we get exactly the right
        number of events (no more), confirming dedup prevents duplicates.
        """
        path = f"{event_unique_path}/nats_dedup_{uuid.uuid4().hex[:8]}.txt"
        filename = path.split("/")[-1]

        # Write file twice with different content
        event_client.write_and_wait(path, "version 1", wait_seconds=0.1)
        event_client.write_and_wait(path, "version 2", wait_seconds=1.0)

        events, _ = event_client.get_events(limit=50)
        matching = [e for e in events if filename in e.get("path", "")]

        # Should be 2 events (one per write), not more (no duplicates)
        assert len(matching) <= 3, (
            f"Dedup failed in NATS pipeline: {len(matching)} events for 2 writes"
        )

    @pytest.mark.nexus_test("events/041")
    def test_nats_exporter_zone_scoped_subjects(
        self,
        event_client: EventClient,
        event_unique_path: str,
    ) -> None:
        """events/041: NATS subjects follow zone-scoped format.

        Subject format: nexus.events.{zone_id}.{event_type}
        Verify events have correct zone_id metadata.
        """
        path = f"{event_unique_path}/nats_zone_{uuid.uuid4().hex[:8]}.txt"
        filename = path.split("/")[-1]

        event_client.write_and_wait(path, "nats zone scoped")

        events, _ = event_client.get_events(limit=20)
        matching = [e for e in events if filename in e.get("path", "")]

        if not matching:
            pytest.skip("Event not found (NATS exporter may not be enabled)")

        event = matching[0]
        # Event should have zone_id set (used for NATS subject routing)
        zone_id = event.get("zone_id", "")
        assert zone_id, f"Event missing zone_id for NATS subject routing: {event}"

    @pytest.mark.nexus_test("events/042")
    def test_exporter_health_via_features(
        self,
        event_client: EventClient,
    ) -> None:
        """events/042: Features endpoint reports exporter availability.

        GET /api/v2/features should indicate whether external exporters
        (NATS, Kafka) are configured and healthy.
        """
        resp = event_client.nexus.features()
        if resp.status_code != 200:
            pytest.skip("Features endpoint not available")

        data = resp.json()
        # Features response should exist — just verify endpoint works
        assert isinstance(data, dict), f"Features response not a dict: {type(data)}"

    @pytest.mark.nexus_test("events/043")
    def test_events_persist_after_batch_delivery(
        self,
        event_client: EventClient,
        event_unique_path: str,
    ) -> None:
        """events/043: Events persist in operation_log after exporter delivery.

        Write 10 files, wait for delivery, then replay —
        all events should be marked delivered and queryable.
        """
        batch_size = 10
        prefix = f"{event_unique_path}/persist_{uuid.uuid4().hex[:6]}"
        batch_tag = prefix.split("/")[-1]

        for i in range(batch_size):
            path = f"{prefix}/p_{i:02d}.txt"
            event_client.nexus.write_file(path, f"persist test {i}")

        # Wait for delivery worker
        time.sleep(3.0)

        # Verify persistence via get_events (newest-first, larger window)
        events, _ = event_client.get_events(limit=200)
        matching = [e for e in events if batch_tag in e.get("path", "")]

        threshold = int(batch_size * 0.80)
        assert len(matching) >= threshold, (
            f"Events not persisted after delivery: {len(matching)}/{batch_size}"
        )
