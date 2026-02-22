"""Chaos test fixtures.

Provides fixtures for fault injection, network partition,
and leader-kill scenarios with automatic recovery in teardown.
"""

from __future__ import annotations

import contextlib
from collections.abc import Generator

import pytest

from tests.helpers.docker_helpers import (
    CONTAINER_NODE_1,
    CONTAINER_WITNESS,
    NETWORK_NAME,
    disconnect_node,
    reconnect_node,
    start_node,
    stop_node,
)


@pytest.fixture
def partition_witness() -> Generator[str]:
    """Disconnect the witness from the network, reconnect on teardown.

    Yields the witness container name. The fixture handles reconnection
    in teardown to prevent test pollution.
    """
    disconnect_node(CONTAINER_WITNESS, NETWORK_NAME)

    yield CONTAINER_WITNESS

    with contextlib.suppress(Exception):
        reconnect_node(CONTAINER_WITNESS, NETWORK_NAME)


@pytest.fixture
def kill_leader() -> Generator[str]:
    """Stop the leader node (node-1), restart on teardown.

    Yields the leader container name. The fixture handles restart
    in teardown so subsequent tests have a healthy cluster.
    """
    stop_node(CONTAINER_NODE_1)

    yield CONTAINER_NODE_1

    with contextlib.suppress(Exception):
        start_node(CONTAINER_NODE_1)
