"""Observability test fixtures.

Provides shared fixtures for health, probe, feature, and metrics tests.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.helpers.api_client import NexusClient


@pytest.fixture(scope="module")
def server_features(nexus: NexusClient) -> dict[str, Any]:
    """Cache the features response for the module.

    Returns an empty dict if the features endpoint is unavailable.
    """
    resp = nexus.features()
    if resp.status_code == 200:
        return resp.json()
    return {}
