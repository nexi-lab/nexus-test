"""Hook test fixtures and contract constants.

Documents the test hook contract established by NEXUS_TEST_HOOKS=true.
The server must register test hooks that:
    - Record audit markers on post-write
    - Block writes to /blocked/ paths on pre-write
    - Record chain execution order on post-write

Hook state is queried via REST endpoints at /api/test-hooks/*.
"""

from __future__ import annotations

import contextlib
import hashlib
import uuid
from collections.abc import Generator
from typing import Any

import pytest

from tests.helpers.api_client import NexusClient

# ---------------------------------------------------------------------------
# Contract constants (must match nexus/core/test_hooks.py)
# ---------------------------------------------------------------------------

HOOK_BLOCKED_PREFIX = "/blocked/"
HOOK_TEST_ENDPOINT = "/api/test-hooks"
CHAIN_EXPECTED_ORDER = "BA"  # B (registered first) runs before A


def path_hash(path: str) -> str:
    """Compute path hash matching the server-side convention."""
    return hashlib.sha256(path.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def hook_test_path(worker_id: str) -> str:
    """Unique path NOT under /blocked/ for positive hook tests."""
    tag = uuid.uuid4().hex[:8]
    return f"/test-hooks/{worker_id}/{tag}/data.txt"


@pytest.fixture
def blocked_path(worker_id: str) -> str:
    """Unique path under /blocked/ for rejection tests."""
    tag = uuid.uuid4().hex[:8]
    return f"{HOOK_BLOCKED_PREFIX}{worker_id}/{tag}/secret.txt"


def _hooks_available(nexus: NexusClient) -> bool:
    """Check if test hook endpoints are available."""
    try:
        resp = nexus.api_get(f"{HOOK_TEST_ENDPOINT}/state")
        return resp.status_code == 200
    except Exception:
        return False


@pytest.fixture
def hook_file(nexus: NexusClient, hook_test_path: str) -> Generator[str, Any, None]:
    """Yield a test path and auto-delete the file on teardown."""
    yield hook_test_path
    with contextlib.suppress(Exception):
        nexus.delete_file(hook_test_path)


@pytest.fixture(autouse=True)
def _skip_if_hooks_unavailable(nexus: NexusClient) -> None:
    """Skip hook tests if NEXUS_TEST_HOOKS is not enabled on the server."""
    if not _hooks_available(nexus):
        pytest.skip(
            "Test hooks not available. Start server with NEXUS_TEST_HOOKS=true"
        )
