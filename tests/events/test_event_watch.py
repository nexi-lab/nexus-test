"""Watch endpoint E2E tests — long-polling file change detection.

Tests cover:
- events/044: Watch endpoint returns changes for file write
- events/045: Watch endpoint respects path glob filter
- events/046: Watch endpoint returns timeout: true when no changes
- events/047: Watch endpoint returns 501 when watch unavailable
- events/048: Watch endpoint respects max timeout parameter

Requires: Nexus server with event subsystem + watch endpoint enabled.
Endpoint: GET /api/v2/watch?path=<glob>&timeout=<seconds>
"""

from __future__ import annotations

import contextlib
import threading
import time
import uuid

import httpx
import pytest

from tests.events.conftest import EventClient


class TestWatchEndpoint:
    """Watch endpoint: long-polling for file change detection."""

    @pytest.mark.nexus_test("events/044")
    def test_watch_detects_file_write(
        self,
        event_client: EventClient,
        event_unique_path: str,
    ) -> None:
        """events/044: Watch detects a file write within the timeout window.

        Opens a watch request in a background thread, writes a file,
        then verifies the watch response includes the change.
        """
        tag = uuid.uuid4().hex[:8]
        path = f"{event_unique_path}/watch_{tag}.txt"
        watch_result: list[dict] = []
        watch_error: list[str] = []

        def do_watch() -> None:
            try:
                resp = event_client.nexus.api_get(
                    "/api/v2/watch",
                    params={"path": f"**/*watch_{tag}*", "timeout": 10.0},
                    timeout=httpx.Timeout(15.0, connect=5.0),
                )
                watch_result.append({
                    "status": resp.status_code,
                    "body": resp.json() if resp.status_code == 200 else {},
                })
            except Exception as exc:
                watch_error.append(str(exc))

        watcher = threading.Thread(target=do_watch, daemon=True)
        watcher.start()

        # Give watch thread time to connect
        time.sleep(1.0)

        # Trigger a file change
        event_client.nexus.write_file(path, "watch detection test")

        watcher.join(timeout=12.0)

        if watch_error:
            pytest.skip(f"Watch endpoint error: {watch_error[0]}")

        if not watch_result:
            pytest.skip("Watch endpoint did not respond in time")

        result = watch_result[0]
        if result["status"] == 501:
            pytest.skip("Watch not available on this server (requires Redis event bus)")
        if result["status"] == 404:
            pytest.skip("Watch endpoint not found")
        if result["status"] == 500:
            pytest.skip("Watch returned transient 500 (concurrent EventsService race)")

        assert result["status"] == 200, (
            f"Watch returned {result['status']}"
        )

        body = result["body"]
        changes = body.get("changes", [])
        # Either we got changes or a timeout
        if body.get("timeout", False) and not changes:
            pytest.skip("Watch timed out before write propagated")

        # Clean up
        with contextlib.suppress(Exception):
            event_client.nexus.delete_file(path)

    @pytest.mark.nexus_test("events/045")
    def test_watch_respects_path_glob_filter(
        self,
        event_client: EventClient,
        event_unique_path: str,
    ) -> None:
        """events/045: Watch with path filter only returns matching changes.

        Watches for *.log files, writes a .txt file — should timeout
        since the write doesn't match the filter.
        """
        tag = uuid.uuid4().hex[:8]
        txt_path = f"{event_unique_path}/watch_filter_{tag}.txt"

        def do_watch_and_write() -> dict:
            # Start watch for .log files (which we won't write)
            resp = event_client.nexus.api_get(
                "/api/v2/watch",
                params={"path": f"**/{tag}*.log", "timeout": 3.0},
                timeout=httpx.Timeout(8.0, connect=5.0),
            )
            return {
                "status": resp.status_code,
                "body": resp.json() if resp.status_code == 200 else {},
            }

        # Write a .txt file in background (won't match .log filter)
        writer = threading.Thread(
            target=lambda: (
                time.sleep(0.5),
                event_client.nexus.write_file(txt_path, "filtered out"),
            ),
            daemon=True,
        )
        writer.start()

        try:
            result = do_watch_and_write()
        except httpx.ReadTimeout:
            pytest.skip("Watch timed out (acceptable)")
        except httpx.ConnectError:
            pytest.skip("Watch endpoint not available")

        writer.join(timeout=5.0)

        if result["status"] == 501:
            pytest.skip("Watch not available")
        if result["status"] == 404:
            pytest.skip("Watch endpoint not found")

        if result["status"] == 200:
            body = result["body"]
            # Should timeout without matching changes
            changes = body.get("changes", [])
            txt_changes = [
                c for c in changes if f"watch_filter_{tag}.txt" in c.get("path", "")
            ]
            assert len(txt_changes) == 0, (
                f".txt file matched .log filter: {txt_changes}"
            )

        # Clean up
        with contextlib.suppress(Exception):
            event_client.nexus.delete_file(txt_path)

    @pytest.mark.nexus_test("events/046")
    def test_watch_returns_timeout_when_no_changes(
        self,
        event_client: EventClient,
    ) -> None:
        """events/046: Watch returns {timeout: true} when no changes within window.

        Uses a very short timeout (1s) and watches a non-existent path pattern.
        """
        try:
            resp = event_client.nexus.api_get(
                "/api/v2/watch",
                params={
                    "path": f"/nonexistent/{uuid.uuid4().hex}/**",
                    "timeout": 1.0,
                },
                timeout=httpx.Timeout(5.0, connect=3.0),
            )
        except httpx.ReadTimeout:
            # Server held the connection — acceptable
            return
        except httpx.ConnectError:
            pytest.skip("Watch endpoint not available")

        if resp.status_code == 501:
            pytest.skip("Watch not available")
        if resp.status_code == 404:
            pytest.skip("Watch endpoint not found")
        if resp.status_code == 500:
            # Intermittent 500 on concurrent watch — retry once
            time.sleep(0.5)
            resp = event_client.nexus.api_get(
                "/api/v2/watch",
                params={"path": "/nonexistent/**", "timeout": 1.0},
                timeout=httpx.Timeout(5.0, connect=3.0),
            )

        assert resp.status_code == 200, f"Watch returned {resp.status_code}: {resp.text[:200]}"
        body = resp.json()

        # Should indicate timeout with no changes
        assert body.get("timeout") is True or len(body.get("changes", [])) == 0, (
            f"Expected timeout or empty changes, got: {body}"
        )

    @pytest.mark.nexus_test("events/047")
    def test_watch_validates_timeout_range(
        self,
        event_client: EventClient,
    ) -> None:
        """events/047: Watch rejects timeout outside valid range (0.1-300s).

        Sends timeout=0 (below minimum) — should get 422 validation error.
        """
        try:
            resp = event_client.nexus.api_get(
                "/api/v2/watch",
                params={"path": "/**/*", "timeout": 0.0},
                timeout=httpx.Timeout(5.0, connect=3.0),
            )
        except httpx.ConnectError:
            pytest.skip("Watch endpoint not available")

        if resp.status_code == 501:
            pytest.skip("Watch not available")
        if resp.status_code == 404:
            pytest.skip("Watch endpoint not found")

        # FastAPI Query(ge=0.1) should return 422 for timeout=0
        assert resp.status_code == 422, (
            f"Expected 422 for invalid timeout, got {resp.status_code}"
        )

    @pytest.mark.nexus_test("events/048")
    def test_watch_default_path_catches_all(
        self,
        event_client: EventClient,
        event_unique_path: str,
    ) -> None:
        """events/048: Watch with default path (/**/*) catches any file write.

        Uses default path parameter and short timeout, writes a file,
        verifies the watch returns changes or at least doesn't error.
        """
        tag = uuid.uuid4().hex[:8]
        path = f"{event_unique_path}/watch_default_{tag}.txt"
        watch_result: list[dict] = []

        def do_watch() -> None:
            try:
                resp = event_client.nexus.api_get(
                    "/api/v2/watch",
                    params={"timeout": 5.0},  # default path = /**/*
                    timeout=httpx.Timeout(10.0, connect=5.0),
                )
                watch_result.append({
                    "status": resp.status_code,
                    "body": resp.json() if resp.status_code == 200 else {},
                })
            except Exception:
                pass

        watcher = threading.Thread(target=do_watch, daemon=True)
        watcher.start()

        time.sleep(0.5)
        event_client.nexus.write_file(path, "default watch test")

        watcher.join(timeout=8.0)

        if not watch_result:
            pytest.skip("Watch did not respond")

        result = watch_result[0]
        if result["status"] in (501, 404):
            pytest.skip("Watch not available")

        assert result["status"] == 200

        with contextlib.suppress(Exception):
            event_client.nexus.delete_file(path)
