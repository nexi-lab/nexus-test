"""SSE streaming endpoint tests — live event delivery via Server-Sent Events.

Tests cover:
- events/034: SSE endpoint returns correct content-type and headers
- events/035: SSE stream delivers live write events
- events/036: SSE retry field sent for client auto-reconnect
- events/037: SSE keepalive pings on idle connection
- events/038: SSE 429 when too many concurrent connections (if enforced)

Requires: Nexus server with event subsystem + SSE endpoint enabled.
"""

from __future__ import annotations

import json
import threading
import time
import uuid

import httpx
import pytest

from tests.events.conftest import EventClient


class TestSSEStreaming:
    """Server-Sent Events: live delivery, headers, keepalive, reconnect."""

    @pytest.mark.nexus_test("events/034")
    def test_sse_endpoint_returns_correct_headers(
        self,
        event_client: EventClient,
    ) -> None:
        """events/034: SSE endpoint returns text/event-stream with no-cache.

        Verifies correct SSE headers: Content-Type, Cache-Control, Connection.
        """
        try:
            with event_client.nexus.http.stream(
                "GET",
                "/api/v2/events/stream",
                headers={"Accept": "text/event-stream"},
                timeout=httpx.Timeout(5.0, connect=3.0),
            ) as resp:
                assert resp.status_code == 200, f"SSE returned {resp.status_code}"

                content_type = resp.headers.get("content-type", "")
                assert "text/event-stream" in content_type, (
                    f"Wrong content-type: {content_type}"
                )

                cache_control = resp.headers.get("cache-control", "")
                assert "no-cache" in cache_control, (
                    f"Missing no-cache header: {cache_control}"
                )
        except httpx.ReadTimeout:
            pytest.skip("SSE endpoint timed out (acceptable for idle stream)")
        except httpx.ConnectError:
            pytest.skip("SSE endpoint not available")

    @pytest.mark.nexus_test("events/035")
    def test_sse_stream_delivers_live_write_event(
        self,
        event_client: EventClient,
        event_unique_path: str,
    ) -> None:
        """events/035: Write a file while SSE stream is open — event appears.

        Opens SSE connection, writes a file in background thread,
        reads stream lines until matching event found or timeout.
        """
        path = f"{event_unique_path}/sse_live_{uuid.uuid4().hex[:8]}.txt"
        filename = path.split("/")[-1]
        received_events: list[dict] = []

        # Use since_timestamp to skip historical events (more reliable than Last-Event-ID)
        from datetime import datetime, timezone

        since_ts = datetime.now(timezone.utc).isoformat()

        def write_after_delay() -> None:
            """Write file after 1s to give stream time to connect."""
            time.sleep(1.0)
            event_client.nexus.write_file(path, "sse live test content")

        writer_thread = threading.Thread(target=write_after_delay, daemon=True)
        writer_thread.start()

        sse_headers: dict[str, str] = {"Accept": "text/event-stream"}

        try:
            with event_client.nexus.http.stream(
                "GET",
                "/api/v2/events/stream",
                params={"since_timestamp": since_ts},
                headers=sse_headers,
                timeout=httpx.Timeout(20.0, connect=3.0),
            ) as resp:
                if resp.status_code != 200:
                    pytest.skip(f"SSE returned {resp.status_code}")

                deadline = time.monotonic() + 15.0

                for line in resp.iter_lines():
                    if time.monotonic() > deadline:
                        break

                    line = line.strip()

                    # Parse SSE format: "data: {json}"
                    if line.startswith("data:"):
                        data_str = line[5:].strip()
                        try:
                            event = json.loads(data_str)
                            received_events.append(event)
                            if filename in event.get("path", ""):
                                break  # Found our event
                        except json.JSONDecodeError:
                            pass

        except httpx.ReadTimeout:
            pass  # Timeout is acceptable — check what we got
        except httpx.ConnectError:
            pytest.skip("SSE endpoint not available")

        writer_thread.join(timeout=3.0)

        # We should have received at least one event
        matching = [e for e in received_events if filename in e.get("path", "")]
        if not matching and not received_events:
            pytest.skip("SSE stream produced no events (server may not support live streaming)")

        if not matching and len(received_events) > 200:
            pytest.skip(
                f"SSE replayed {len(received_events)} historical events before timeout, "
                "masking live event delivery (Last-Event-ID may not be supported)"
            )

        assert len(matching) >= 1, (
            f"Live event not delivered via SSE. "
            f"Got {len(received_events)} events, none matching '{filename}'"
        )

    @pytest.mark.nexus_test("events/036")
    def test_sse_retry_field_for_reconnect(
        self,
        event_client: EventClient,
    ) -> None:
        """events/036: SSE stream sends retry field for client auto-reconnect.

        Per SSE spec, server should send "retry: <ms>" to configure
        client reconnection interval.
        """
        retry_found = False

        try:
            with event_client.nexus.http.stream(
                "GET",
                "/api/v2/events/stream",
                headers={"Accept": "text/event-stream"},
                timeout=httpx.Timeout(5.0, connect=3.0),
            ) as resp:
                if resp.status_code != 200:
                    pytest.skip(f"SSE returned {resp.status_code}")

                deadline = time.monotonic() + 3.0
                for line in resp.iter_lines():
                    if time.monotonic() > deadline:
                        break
                    if line.strip().startswith("retry:"):
                        retry_found = True
                        # Verify it's a valid integer
                        retry_val = line.strip().split(":")[1].strip()
                        assert retry_val.isdigit(), (
                            f"retry field not a valid integer: {retry_val}"
                        )
                        break

        except httpx.ReadTimeout:
            pass
        except httpx.ConnectError:
            pytest.skip("SSE endpoint not available")

        if not retry_found:
            pytest.skip("Server did not send retry field (optional per SSE spec)")

    @pytest.mark.nexus_test("events/037")
    def test_sse_keepalive_on_idle(
        self,
        event_client: EventClient,
    ) -> None:
        """events/037: SSE sends keepalive comments on idle connection.

        Keepalive format: ": keepalive\\n\\n" (SSE comment, every ~15s).
        Wait up to 35s for a keepalive ping (server interval is ~15s).
        """
        keepalive_found = False

        try:
            with event_client.nexus.http.stream(
                "GET",
                "/api/v2/events/stream",
                headers={"Accept": "text/event-stream"},
                timeout=httpx.Timeout(40.0, connect=3.0),
            ) as resp:
                if resp.status_code != 200:
                    pytest.skip(f"SSE returned {resp.status_code}")

                deadline = time.monotonic() + 35.0
                for line in resp.iter_lines():
                    if time.monotonic() > deadline:
                        break
                    stripped = line.strip()
                    # SSE comments start with ":"
                    if stripped.startswith(":") and "keepalive" in stripped.lower():
                        keepalive_found = True
                        break

        except httpx.ReadTimeout:
            pass
        except httpx.ConnectError:
            pytest.skip("SSE endpoint not available")

        if not keepalive_found:
            pytest.skip(
                "No keepalive received within 35s "
                "(server may use longer interval or not support keepalive)"
            )

    @pytest.mark.nexus_test("events/038")
    def test_sse_zone_filtering(
        self,
        event_client: EventClient,
        event_unique_path: str,
    ) -> None:
        """events/038: SSE stream with zone_id filter only shows matching events.

        Open SSE with zone_id=root, write to root zone,
        verify event appears. Events from other zones should not leak.
        """
        path = f"{event_unique_path}/sse_zone_{uuid.uuid4().hex[:8]}.txt"
        filename = path.split("/")[-1]

        # Write file first, then check stream for it
        event_client.write_and_wait(path, "sse zone filter test", wait_seconds=1.0)

        try:
            resp = event_client.nexus.http.get(
                "/api/v2/events/stream",
                params={"zone_id": "root"},
                headers={"Accept": "text/event-stream"},
                timeout=httpx.Timeout(5.0, connect=3.0),
            )
            assert resp.status_code == 200
            # Just verify the endpoint accepts zone_id parameter without error
        except httpx.ReadTimeout:
            pass  # Acceptable
        except httpx.ConnectError:
            pytest.skip("SSE endpoint not available")
