"""Kernel test fixtures.

Provides kernel-specific fixtures for file operation tests.
"""

from __future__ import annotations

import contextlib
from collections.abc import Generator
from typing import Any

import pytest

from tests.helpers.api_client import NexusClient


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
