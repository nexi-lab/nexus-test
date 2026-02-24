"""Zone test fixtures.

Provides zone-specific fixtures for multi-tenancy and zone isolation tests.

Path convention:
    Zone-scoped clients use simple paths like /za/{tag}/file.txt.
    The server adds its own /zone/{zone_id}/ prefix internally.
    We avoid putting zone names in the client path to prevent deep nesting
    that triggers the batched permission checker's ancestor resolution.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Callable, Generator

import pytest

from tests.config import TestSettings
from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import assert_rpc_success
from tests.helpers.zone_keys import (
    create_zone_direct,
    create_zone_key,
    delete_zone_direct,
    grant_zone_permission,
)


@pytest.fixture
def zone_a(settings: TestSettings) -> str:
    """Return the primary zone ID from settings."""
    return settings.zone


@pytest.fixture
def zone_b(settings: TestSettings) -> str:
    """Return the scratch zone ID (a second zone for isolation tests)."""
    return settings.scratch_zone


@pytest.fixture
def zone_a_root() -> str:
    """Return the root path for zone A's namespace.

    Simple prefix — the server adds /zone/{zone_id}/ internally.
    """
    return "/za"


@pytest.fixture
def zone_b_root() -> str:
    """Return the root path for zone B's namespace.

    Simple prefix — the server adds /zone/{zone_id}/ internally.
    """
    return "/zb"


@pytest.fixture
def nexus_a(nexus: NexusClient, settings: TestSettings) -> Generator[NexusClient]:
    """Create a zone-scoped NexusClient for zone A.

    Creates a non-admin API key bound to zone A with full permissions,
    so the client sees only zone A's data (zone isolation enforced by ReBAC).
    """
    user_id = f"zone-a-user-{uuid.uuid4().hex[:6]}"
    try:
        raw_key = create_zone_key(
            nexus, settings.zone, name=f"test-a-{uuid.uuid4().hex[:8]}",
            user_id=user_id,
        )
        # Grant full access to zone A's namespace
        grant_zone_permission(settings.zone, user_id, "/", "direct_owner")
    except RuntimeError:
        pytest.skip("Cannot create zone-scoped API key (DB not accessible)")
        return

    client = nexus.for_zone(raw_key)
    try:
        yield client
    finally:
        client.http.close()


@pytest.fixture
def nexus_b(nexus: NexusClient, settings: TestSettings) -> Generator[NexusClient]:
    """Create a zone-scoped NexusClient for zone B."""
    user_id = f"zone-b-user-{uuid.uuid4().hex[:6]}"
    try:
        raw_key = create_zone_key(
            nexus, settings.scratch_zone,
            name=f"test-b-{uuid.uuid4().hex[:8]}",
            user_id=user_id,
        )
        # Grant full access to zone B's namespace
        grant_zone_permission(settings.scratch_zone, user_id, "/", "direct_owner")
    except RuntimeError:
        pytest.skip("Cannot create zone-scoped API key (DB not accessible)")
        return

    client = nexus.for_zone(raw_key)
    try:
        yield client
    finally:
        client.http.close()


@pytest.fixture
def make_file_a(
    nexus_a: NexusClient, zone_a_root: str
) -> Callable[[str, str], str]:
    """Factory to write files in zone A's namespace.

    Returns the full path (including zone prefix) after writing.
    """
    created: list[str] = []

    def _write(rel_path: str, content: str) -> str:
        full_path = f"{zone_a_root}/{rel_path}"
        assert_rpc_success(nexus_a.write_file(full_path, content))
        created.append(full_path)
        return full_path

    return _write


@pytest.fixture
def make_file_b(
    nexus_b: NexusClient, zone_b_root: str
) -> Callable[[str, str], str]:
    """Factory to write files in zone B's namespace."""
    created: list[str] = []

    def _write(rel_path: str, content: str) -> str:
        full_path = f"{zone_b_root}/{rel_path}"
        assert_rpc_success(nexus_b.write_file(full_path, content))
        created.append(full_path)
        return full_path

    return _write


@pytest.fixture
def ephemeral_zone(
    nexus: NexusClient,
    worker_id: str,
) -> Generator[str]:
    """Create and yield an ephemeral zone, then terminate it.

    The zone is created before the test and terminated after.
    """
    tag = uuid.uuid4().hex[:8]
    zone_id = f"eph-{worker_id}-{tag}"

    # Create the zone (REST with DB fallback)
    resp = nexus.create_zone(zone_id, name=f"Ephemeral {zone_id}")
    if resp.status_code not in (200, 201):
        try:
            create_zone_direct(zone_id, f"Ephemeral {zone_id}")
        except RuntimeError:
            pytest.skip("Zone creation not available")

    yield zone_id

    # Cleanup: terminate the zone
    with contextlib.suppress(Exception):
        delete_resp = nexus.delete_zone(zone_id)
        if delete_resp.status_code not in (200, 202, 204):
            delete_zone_direct(zone_id)


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
