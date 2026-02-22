"""Zone test fixtures.

Provides zone-specific fixtures for multi-tenancy and zone isolation tests.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Generator

import pytest

from tests.config import TestSettings
from tests.helpers.api_client import NexusClient


@pytest.fixture
def zone_a(settings: TestSettings) -> str:
    """Return the primary zone ID from settings."""
    return settings.zone


@pytest.fixture
def zone_b(settings: TestSettings) -> str:
    """Return the scratch zone ID (a second zone for isolation tests)."""
    return settings.scratch_zone


@pytest.fixture
def clean_zone_path(nexus: NexusClient, settings: TestSettings) -> Generator[str]:
    """Create and yield a unique path inside the scratch zone, then clean up.

    The path is removed after the test completes.
    """
    short_uuid = uuid.uuid4().hex[:8]
    path = f"/test-zone/{short_uuid}"
    zone = settings.scratch_zone

    yield path

    with contextlib.suppress(Exception):
        nexus.rmdir(path, recursive=True, zone=zone)
