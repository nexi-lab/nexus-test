"""Hook test fixtures — production-observable metadata verification.

Tests verify VFS hook pipeline correctness by observing production side
effects (metadata population, version updates, timestamps) rather than
injected test hooks.  No server-side test scaffolding required.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Generator
from typing import Any

import pytest

from tests.helpers.api_client import NexusClient


# ---------------------------------------------------------------------------
# Metadata extraction helpers
# ---------------------------------------------------------------------------


def extract_metadata_field(meta_result: dict, field: str) -> Any:
    """Extract field from metadata, handling nested 'metadata' wrapper.

    The server may return metadata at the top level or nested under a
    ``"metadata"`` key.  This helper checks both.
    """
    if field in meta_result:
        return meta_result[field]
    inner = meta_result.get("metadata", {})
    if isinstance(inner, dict):
        return inner.get(field)
    return None


def flatten_metadata(meta_result: dict) -> dict:
    """Merge top-level and nested metadata keys into a single dict."""
    flat: dict[str, Any] = dict(meta_result)
    inner = meta_result.get("metadata", {})
    if isinstance(inner, dict):
        for k, v in inner.items():
            flat.setdefault(k, v)
    return flat


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def hook_test_path(worker_id: str) -> str:
    """Unique path for positive hook tests."""
    tag = uuid.uuid4().hex[:8]
    return f"/test-hooks/{worker_id}/{tag}/data.txt"


@pytest.fixture
def hook_file(nexus: NexusClient, hook_test_path: str) -> Generator[str, Any, None]:
    """Yield a test path and auto-delete the file on teardown."""
    yield hook_test_path
    with contextlib.suppress(Exception):
        nexus.delete_file(hook_test_path)
