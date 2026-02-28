"""Event subsystem test fixtures.

Provides EventClient wrapper with latency measurement and helper fixtures
for the events/ test suite (events/001-020).

References:
    - Event API: GET /api/v2/events, /api/v2/events/replay, /api/v2/events/stream
    - TEST_PLAN.md section 4.5
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Generator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from tests.helpers.api_client import NexusClient


@dataclass(frozen=True)
class LatencyMeasurement:
    """Immutable latency measurement result."""

    operation: str
    elapsed_ms: float
    success: bool


@dataclass
class EventClient:
    """Wrapper around NexusClient with event-specific helpers and latency tracking.

    Provides convenience methods for event querying, replay, and latency
    measurement. All latency measurements are recorded for post-test analysis.
    """

    nexus: NexusClient
    measurements: list[LatencyMeasurement] = field(default_factory=list)

    def get_events(
        self,
        *,
        limit: int = 50,
        operation_type: str | None = None,
        zone: str | None = None,
    ) -> tuple[list[dict[str, Any]], httpx.Response]:
        """Query the event log and return (events, response).

        Skips the test if the service returns 503 (not available).
        """
        params: dict[str, Any] = {"limit": limit}
        if operation_type:
            params["operation_type"] = operation_type
        headers: dict[str, str] = {}
        if zone:
            headers["X-Nexus-Zone-ID"] = zone

        start = time.monotonic()
        resp = self.nexus.api_get("/api/v2/events", params=params, headers=headers)
        elapsed_ms = (time.monotonic() - start) * 1000

        self.measurements.append(
            LatencyMeasurement("get_events", elapsed_ms, resp.status_code == 200)
        )

        if resp.status_code == 503:
            pytest.skip("Event log service not available on this server")

        return resp.json().get("events", []), resp

    def replay_events(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
        zone: str | None = None,
    ) -> tuple[dict[str, Any], httpx.Response]:
        """Replay events via cursor-based pagination.

        Returns (replay_data_dict, response). Skips if 503.
        """
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        headers: dict[str, str] = {}
        if zone:
            headers["X-Nexus-Zone-ID"] = zone

        start = time.monotonic()
        resp = self.nexus.api_get(
            "/api/v2/events/replay", params=params, headers=headers
        )
        elapsed_ms = (time.monotonic() - start) * 1000

        self.measurements.append(
            LatencyMeasurement("replay_events", elapsed_ms, resp.status_code == 200)
        )

        if resp.status_code == 503:
            pytest.skip("Event replay service not available on this server")

        return resp.json(), resp

    def stream_events(
        self,
        *,
        timeout: float = 5.0,
        zone: str | None = None,
    ) -> httpx.Response:
        """Open an SSE event stream connection.

        Returns the response object (caller should iterate lines).
        """
        headers: dict[str, str] = {"Accept": "text/event-stream"}
        if zone:
            headers["X-Nexus-Zone-ID"] = zone

        return self.nexus.http.get(
            "/api/v2/events/stream",
            headers=headers,
            timeout=httpx.Timeout(timeout, connect=5.0),
        )

    def write_and_wait(
        self,
        path: str,
        content: str,
        *,
        zone: str | None = None,
        wait_seconds: float = 0.5,
    ) -> dict[str, Any]:
        """Write a file and wait for event propagation.

        Returns the RPC write result.
        """
        from tests.helpers.assertions import assert_rpc_success

        result = assert_rpc_success(self.nexus.write_file(path, content, zone=zone))
        time.sleep(wait_seconds)
        return result

    def measure_publish_to_replay_latency(
        self,
        path: str,
        content: str,
        *,
        zone: str | None = None,
        max_wait_seconds: float = 5.0,
    ) -> float:
        """Measure end-to-end latency from file write to event appearing in replay.

        Returns latency in milliseconds.
        """
        from tests.helpers.assertions import assert_rpc_success

        start = time.monotonic()
        assert_rpc_success(self.nexus.write_file(path, content, zone=zone))

        # Poll replay endpoint until the event appears
        deadline = start + max_wait_seconds
        while time.monotonic() < deadline:
            events, _ = self.get_events(limit=20)
            matching = [
                ev for ev in events if path.split("/")[-1] in ev.get("path", "")
            ]
            if matching:
                elapsed_ms = (time.monotonic() - start) * 1000
                self.measurements.append(
                    LatencyMeasurement("publish_to_replay", elapsed_ms, True)
                )
                return elapsed_ms
            time.sleep(0.1)

        elapsed_ms = (time.monotonic() - start) * 1000
        self.measurements.append(
            LatencyMeasurement("publish_to_replay", elapsed_ms, False)
        )
        return elapsed_ms

    @property
    def avg_latency_ms(self) -> float:
        """Average latency across all measurements."""
        if not self.measurements:
            return 0.0
        return sum(m.elapsed_ms for m in self.measurements) / len(self.measurements)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def event_client(nexus: NexusClient) -> EventClient:
    """Per-test EventClient wrapping the session-scoped NexusClient."""
    return EventClient(nexus=nexus)


@pytest.fixture
def event_unique_path(unique_path: str) -> str:
    """Unique path prefix for event tests."""
    return f"{unique_path}/events"


@pytest.fixture
def make_event_file(
    nexus: NexusClient, event_unique_path: str
) -> Generator[Any]:
    """Factory fixture: creates files and cleans up after test.

    Yields a callable (name, content) -> path that writes files
    under the event-unique namespace.
    """
    created_paths: list[str] = []

    def _make(name: str, content: str = "event test content") -> str:
        path = f"{event_unique_path}/{name}"
        nexus.write_file(path, content)
        created_paths.append(path)
        return path

    yield _make

    for path in reversed(created_paths):
        with contextlib.suppress(Exception):
            nexus.delete_file(path)
