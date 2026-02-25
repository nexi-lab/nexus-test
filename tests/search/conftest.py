"""Search test fixtures — enforcement gate, seed data, helpers.

Fixture scoping:
    module:  _search_available (auto-skip gate)
    class:   seeded_files (HERB enterprise context files)
    function: make_searchable_file (factory with cleanup)
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
import uuid
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

import pytest

from tests.helpers.api_client import NexusClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-scoped enforcement gate
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _search_available(nexus: NexusClient) -> None:
    """Skip search tests if search daemon is not initialized."""
    try:
        resp = nexus.search_health()
        if resp.status_code != 200:
            pytest.skip(f"Search daemon not reachable (HTTP {resp.status_code})")
        data = resp.json()
        if not data.get("daemon_initialized"):
            pytest.skip("Search daemon not initialized")
    except Exception as exc:
        pytest.skip(f"Search daemon health check failed: {exc}")


# ---------------------------------------------------------------------------
# HERB enterprise-context data loader
# ---------------------------------------------------------------------------

HERB_DATA_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "benchmarks"
    / "herb"
    / "enterprise-context"
)

HERB_QA_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "benchmarks"
    / "herb"
    / "qa"
)


def load_herb_context(max_records: int = 50) -> list[dict[str, Any]]:
    """Load HERB enterprise-context records from JSONL files."""
    records: list[dict[str, Any]] = []
    for jsonl_file in sorted(HERB_DATA_DIR.glob("*.jsonl")):
        with jsonl_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
                    if len(records) >= max_records:
                        return records
    return records


def load_herb_qa() -> list[dict[str, Any]]:
    """Load HERB Q&A benchmark data from JSONL files.

    Returns empty list if QA directory doesn't exist.
    """
    if not HERB_QA_DIR.exists():
        return []
    records: list[dict[str, Any]] = []
    for jsonl_file in sorted(HERB_QA_DIR.glob("*.jsonl")):
        with jsonl_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    return records


# ---------------------------------------------------------------------------
# File seeding helpers
# ---------------------------------------------------------------------------


def _file_content_from_record(record: dict[str, Any]) -> str:
    """Convert a HERB record into file content for search indexing."""
    parts: list[str] = []
    name = record.get("name", record.get("company", ""))
    if name:
        parts.append(f"# {name}")
    for key in ("department", "role", "category", "industry", "description"):
        val = record.get(key)
        if val:
            parts.append(f"{key}: {val}")
    # Include skills, features, products as searchable text
    for list_key in ("skills", "features", "products_used"):
        items = record.get(list_key, [])
        if items:
            parts.append(f"{list_key}: {', '.join(str(i) for i in items)}")
    # Fallback: dump all remaining fields
    if not parts:
        parts.append(json.dumps(record, indent=2))
    return "\n".join(parts)


@pytest.fixture(scope="class")
def seeded_search_files(
    nexus: NexusClient,
) -> Generator[list[dict[str, Any]], None, None]:
    """Seed HERB enterprise-context records as NexusFS files.

    Returns list of dicts with 'path' and 'content' keys.
    Cleaned up after the class completes.
    """
    records = load_herb_context(max_records=30)
    if not records:
        pytest.skip("HERB enterprise-context data not found")

    tag = uuid.uuid4().hex[:8]
    base_path = f"/test-search/{tag}"
    seeded: list[dict[str, Any]] = []

    for i, record in enumerate(records):
        content = _file_content_from_record(record)
        name = record.get("name", record.get("company", f"record_{i}"))
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(name))
        path = f"{base_path}/{safe_name}.md"
        resp = nexus.write_file(path, content)
        if resp.ok:
            seeded.append({"path": path, "content": content, "record": record})

    # Trigger index refresh for the base path
    nexus.search_refresh(base_path)
    # Allow time for BM25S reindex
    time.sleep(3)

    yield seeded

    # Cleanup
    for entry in reversed(seeded):
        with contextlib.suppress(Exception):
            nexus.delete_file(entry["path"])
    with contextlib.suppress(Exception):
        nexus.rmdir(base_path, recursive=True)


@pytest.fixture
def make_searchable_file(
    nexus: NexusClient,
) -> Generator[Callable[[str, str], str], None, None]:
    """Factory: create a file and trigger search index refresh.

    Returns the file path. Files are cleaned up after the test.
    """
    tag = uuid.uuid4().hex[:8]
    base_path = f"/test-search/{tag}"
    created: list[str] = []

    def _make(name: str, content: str) -> str:
        path = f"{base_path}/{name}"
        resp = nexus.write_file(path, content)
        assert resp.ok, f"Failed to write {path}: {resp.error}"
        created.append(path)
        # Trigger refresh
        nexus.search_refresh(path)
        return path

    yield _make

    for path in reversed(created):
        with contextlib.suppress(Exception):
            nexus.delete_file(path)
    with contextlib.suppress(Exception):
        nexus.rmdir(base_path, recursive=True)


# ---------------------------------------------------------------------------
# Search result helpers
# ---------------------------------------------------------------------------


def extract_search_results(resp) -> list[dict[str, Any]]:
    """Extract results list from a search query response."""
    if hasattr(resp, "json"):
        data = resp.json()
    else:
        data = resp
    if isinstance(data, dict):
        return data.get("results", [])
    return []


def search_result_paths(resp) -> list[str]:
    """Extract just the file paths from search results."""
    return [r.get("path", "") for r in extract_search_results(resp)]
