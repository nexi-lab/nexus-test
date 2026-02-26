"""Kernel test fixtures.

Provides kernel-specific fixtures for file operation tests.
"""

from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import Generator
from typing import Any

import httpx
import pytest

from tests.config import TestSettings
from tests.helpers.api_client import NexusClient
from tests.helpers.zone_keys import create_zone_key

logger = logging.getLogger(__name__)


@pytest.fixture
def kernel_tree(
    nexus: NexusClient, unique_path: str
) -> Generator[dict[str, Any], None, None]:
    """Create a small 3-level tree and return paths for tests.

    Structure:
        {unique_path}/tree/
        ├── root.txt
        ├── level1_a/
        │   ├── a.txt
        │   └── level2_a/
        │       └── deep.txt
        └── level1_b/
            └── b.py

    Yields:
        Dict with 'files' and 'dirs' lists of absolute paths.
    """
    base = f"{unique_path}/tree"
    files = [
        (f"{base}/root.txt", "root content"),
        (f"{base}/level1_a/a.txt", "level 1a content"),
        (f"{base}/level1_a/level2_a/deep.txt", "level 2 deep content"),
        (f"{base}/level1_b/b.py", "print('hello')"),
    ]
    dirs = [
        f"{base}",
        f"{base}/level1_a",
        f"{base}/level1_a/level2_a",
        f"{base}/level1_b",
    ]

    # Create all files (write_file auto-creates parents on most servers)
    for path, content in files:
        nexus.write_file(path, content)

    result = {
        "base": base,
        "files": [f[0] for f in files],
        "dirs": dirs,
    }

    yield result

    # Cleanup
    for path, _ in reversed(files):
        with contextlib.suppress(Exception):
            nexus.delete_file(path)
    for d in reversed(dirs):
        with contextlib.suppress(Exception):
            nexus.rmdir(d)


@pytest.fixture(scope="module")
def unprivileged_kernel_client(
    nexus: NexusClient, settings: TestSettings
) -> Generator[NexusClient, None, None]:
    """Non-admin client with no ReBAC grants for permission denial tests.

    Skips if the server does not enforce permissions.
    """
    # Check enforcement
    health_resp = nexus.health()
    if health_resp.status_code == 200:
        data = health_resp.json()
        if data.get("enforce_permissions") is False:
            pytest.skip("Server has enforce_permissions=false")

    # Ensure NEXUS_DATABASE_URL is visible for zone_keys fallback path
    if not os.environ.get("NEXUS_DATABASE_URL") and settings.database_url:
        os.environ["NEXUS_DATABASE_URL"] = settings.database_url

    # Create a non-admin key with no grants
    zone_id = settings.zone or "default"
    raw_key = create_zone_key(
        nexus, zone_id, name="kernel-unpriv", user_id="kernel-unpriv-user", is_admin=False
    )

    http = httpx.Client(
        base_url=settings.url,
        headers={"Authorization": f"Bearer {raw_key}"},
        timeout=httpx.Timeout(settings.request_timeout, connect=settings.connect_timeout),
    )
    client = NexusClient(http=http, base_url=settings.url, api_key=raw_key)

    yield client

    http.close()
