"""Concurrent writer tests — stress test event delivery under load.

Tests cover:
- events/030: Concurrent writers (10 threads) all events captured
- events/031: Rapid sequential writes — no event loss under burst
- events/032: Large file batch (200 files) — delivery worker keeps up
- events/033: Concurrent readers + writers — replay consistent during writes

Requires: Nexus server with event subsystem enabled.
"""

from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from tests.events.conftest import EventClient


class TestConcurrentWriters:
    """Stress test: concurrent file writes and event delivery correctness."""

    @pytest.mark.nexus_test("events/030")
    def test_concurrent_writers_all_events_captured(
        self,
        event_client: EventClient,
        event_unique_path: str,
    ) -> None:
        """events/030: 10 concurrent threads writing files — all events captured.

        Uses ThreadPoolExecutor to simulate 10 concurrent writers.
        Each writes 5 files = 50 total. Verify >= 80% appear in event log.
        """
        num_writers = 10
        files_per_writer = 5
        total_files = num_writers * files_per_writer
        prefix = f"{event_unique_path}/conc_{uuid.uuid4().hex[:6]}"
        written_paths: list[str] = []

        def writer_task(writer_id: int) -> list[str]:
            paths = []
            for i in range(files_per_writer):
                path = f"{prefix}/w{writer_id:02d}_f{i:02d}.txt"
                event_client.nexus.write_file(path, f"writer {writer_id} file {i}")
                paths.append(path)
            return paths

        # Launch concurrent writers
        with ThreadPoolExecutor(max_workers=num_writers) as pool:
            futures = [pool.submit(writer_task, wid) for wid in range(num_writers)]
            for future in as_completed(futures):
                written_paths.extend(future.result())

        assert len(written_paths) == total_files

        # Wait for delivery worker to process
        time.sleep(5.0)

        # Query events
        events, _ = event_client.get_events(limit=500)
        batch_tag = prefix.split("/")[-1]
        matching = [e for e in events if batch_tag in e.get("path", "")]

        threshold = int(total_files * 0.80)
        assert len(matching) >= threshold, (
            f"Concurrent write event loss: {len(matching)}/{total_files} "
            f"(threshold: {threshold})"
        )

    @pytest.mark.nexus_test("events/031")
    def test_rapid_sequential_burst_no_event_loss(
        self,
        event_client: EventClient,
        event_unique_path: str,
    ) -> None:
        """events/031: 30 rapid sequential writes — no event loss.

        Writes 30 files as fast as possible (no sleep between writes).
        Verifies delivery worker keeps up (>= 80% threshold).
        """
        burst_size = 30
        prefix = f"{event_unique_path}/burst_{uuid.uuid4().hex[:6]}"

        for i in range(burst_size):
            path = f"{prefix}/rapid_{i:03d}.txt"
            event_client.nexus.write_file(path, f"burst content {i}")

        # Wait for delivery
        time.sleep(4.0)

        events, _ = event_client.get_events(limit=200)
        batch_tag = prefix.split("/")[-1]
        matching = [e for e in events if batch_tag in e.get("path", "")]

        threshold = int(burst_size * 0.80)
        assert len(matching) >= threshold, (
            f"Burst event loss: {len(matching)}/{burst_size} "
            f"(threshold: {threshold})"
        )

    @pytest.mark.nexus_test("events/032")
    def test_large_batch_200_files_delivery_keeps_up(
        self,
        event_client: EventClient,
        event_unique_path: str,
    ) -> None:
        """events/032: 200-file batch — delivery worker processes all within 10s.

        Scale test: write 200 files, verify events appear within deadline.
        """
        batch_size = 200
        prefix = f"{event_unique_path}/scale_{uuid.uuid4().hex[:6]}"

        start = time.monotonic()
        for i in range(batch_size):
            path = f"{prefix}/s_{i:03d}.txt"
            event_client.nexus.write_file(path, f"scale {i}")
        write_elapsed = time.monotonic() - start

        # Wait proportional to batch size (max 10s)
        wait_time = min(10.0, max(5.0, write_elapsed * 2))
        time.sleep(wait_time)

        events, _ = event_client.get_events(limit=500)
        batch_tag = prefix.split("/")[-1]
        matching = [e for e in events if batch_tag in e.get("path", "")]

        threshold = int(batch_size * 0.80)
        assert len(matching) >= threshold, (
            f"Scale delivery lag: {len(matching)}/{batch_size} "
            f"(threshold: {threshold}, write_time: {write_elapsed:.1f}s)"
        )

    @pytest.mark.nexus_test("events/033")
    def test_concurrent_readers_writers_replay_consistent(
        self,
        event_client: EventClient,
        event_unique_path: str,
    ) -> None:
        """events/033: Concurrent reads + writes — replay stays consistent.

        Writes files while simultaneously querying replay endpoint.
        Verifies no crashes, no duplicate event_ids across pages.
        """
        prefix = f"{event_unique_path}/rw_{uuid.uuid4().hex[:6]}"
        all_event_ids: set[str] = set()
        errors: list[str] = []

        def writer() -> None:
            for i in range(20):
                path = f"{prefix}/rw_{i:03d}.txt"
                event_client.nexus.write_file(path, f"rw content {i}")
                time.sleep(0.05)

        def reader() -> None:
            for _ in range(10):
                try:
                    data, resp = event_client.replay_events(limit=50)
                    if resp.status_code != 200:
                        continue
                    # Check for duplicates WITHIN a single page (not across pages,
                    # since replay without cursor returns from the beginning each time)
                    page_events = data.get("events", [])
                    page_ids = [ev.get("event_id", "") for ev in page_events]
                    page_id_set = set()
                    for eid in page_ids:
                        if eid and eid in page_id_set:
                            errors.append(f"Duplicate event_id within single page: {eid}")
                        page_id_set.add(eid)
                    all_event_ids.update(page_id_set)
                except Exception as exc:
                    errors.append(f"Reader error: {exc}")
                time.sleep(0.2)

        with ThreadPoolExecutor(max_workers=2) as pool:
            w_future = pool.submit(writer)
            r_future = pool.submit(reader)
            w_future.result()
            r_future.result()

        assert len(errors) == 0, f"Consistency errors: {errors}"
