"""Federation test fixtures.

Provides fixtures for multi-node federation tests including
replication verification and managed node lifecycle.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Generator

import httpx
import pytest

from tests.config import TestSettings
from tests.helpers.api_client import NexusClient
from tests.helpers.docker_helpers import (
    CONTAINER_NODE_1,
    CONTAINER_NODE_2,
    CONTAINER_WITNESS,
    start_node,
    stop_node,
)


def _is_node_reachable(url: str, api_key: str) -> bool:
    """Check if a node is reachable (non-retrying, single attempt)."""
    try:
        resp = httpx.get(
            f"{url}/health",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=3.0,
        )
        return resp.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException):
        return False


def wait_for_replication(
    nexus_leader: NexusClient,
    nexus_follower: NexusClient,
    path: str,
    *,
    timeout: float = 30.0,
    poll_interval: float = 0.5,
) -> bool:
    """Wait until a file written on the leader is readable on the follower.

    Args:
        nexus_leader: Client pointing at the leader node.
        nexus_follower: Client pointing at the follower node.
        path: File path to check.
        timeout: Maximum seconds to wait.
        poll_interval: Seconds between polls.

    Returns:
        True if the file appeared on the follower within the timeout.
    """
    deadline = time.monotonic() + timeout

    # Read expected content from leader
    leader_resp = nexus_leader.read_file(path)
    if not leader_resp.ok:
        return False
    expected = leader_resp.content_str

    while time.monotonic() < deadline:
        follower_resp = nexus_follower.read_file(path)
        if follower_resp.ok and follower_resp.content_str == expected:
            return True
        time.sleep(poll_interval)

    return False


@pytest.fixture
def federation_ready(settings: TestSettings) -> None:
    """Skip the test if the follower node is not reachable.

    Federation tests require at least 2 nodes. If the follower is down,
    the test is skipped rather than failing.
    """
    if not _is_node_reachable(settings.url_follower, settings.api_key):
        pytest.skip(
            f"Follower node not reachable at {settings.url_follower}. "
            "Start the full cluster for federation tests."
        )


@pytest.fixture
def managed_node_1() -> Generator[str]:
    """Yield the node-1 container name, auto-restarting on teardown if stopped."""
    yield CONTAINER_NODE_1

    with contextlib.suppress(Exception):
        start_node(CONTAINER_NODE_1)


@pytest.fixture
def managed_node_2() -> Generator[str]:
    """Yield the node-2 container name, auto-restarting on teardown if stopped."""
    yield CONTAINER_NODE_2

    with contextlib.suppress(Exception):
        start_node(CONTAINER_NODE_2)


@pytest.fixture
def managed_witness() -> Generator[str]:
    """Yield the witness container name, auto-restarting on teardown if stopped."""
    yield CONTAINER_WITNESS

    with contextlib.suppress(Exception):
        start_node(CONTAINER_WITNESS)
