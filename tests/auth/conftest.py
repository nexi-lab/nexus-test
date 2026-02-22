"""Auth test fixtures.

Provides authentication-specific fixtures for API key, OAuth, session,
and rate-limiting tests.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Callable, Generator

import httpx
import pytest

from tests.config import TestSettings
from tests.helpers.api_client import NexusClient


@pytest.fixture
def unauthenticated_client(settings: TestSettings) -> Generator[httpx.Client]:
    """An httpx client with NO authentication headers.

    Used to verify that unauthenticated requests are properly rejected.
    """
    with httpx.Client(
        base_url=settings.url,
        timeout=httpx.Timeout(settings.request_timeout, connect=settings.connect_timeout),
    ) as client:
        yield client


@pytest.fixture
def create_api_key(
    nexus: NexusClient,
) -> Generator[Callable[[str], dict]]:
    """Factory fixture: create temporary API keys and clean them up after.

    Usage:
        def test_something(create_api_key):
            key_info = create_api_key("test-key-label")
            # key_info contains {"key": "sk-...", "id": "..."}
    """
    created_key_ids: list[str] = []

    def _create(label: str | None = None) -> dict:
        key_label = label or f"test-key-{uuid.uuid4().hex[:8]}"
        resp = nexus.api_post("/api/v2/auth/keys", json={"label": key_label})
        data = resp.json()
        key_id = data.get("id", data.get("key_id", ""))
        if key_id:
            created_key_ids.append(key_id)
        return data

    yield _create

    # Cleanup: revoke all created API keys
    for key_id in created_key_ids:
        with contextlib.suppress(Exception):
            nexus.api_delete(f"/api/v2/auth/keys/{key_id}")


@pytest.fixture
def rate_limit_client(settings: TestSettings) -> Generator[httpx.Client]:
    """An httpx client configured for rate-limit testing.

    Uses a short timeout and no retry logic so rate-limit responses
    (HTTP 429) are surfaced immediately.
    """
    with httpx.Client(
        base_url=settings.url,
        headers={"Authorization": f"Bearer {settings.api_key}"},
        timeout=httpx.Timeout(5.0, connect=3.0),
    ) as client:
        yield client
