"""Search test fixtures — enforcement gates, indexed files, ReBAC setup.

Fixture scoping:
    module:  _search_available (auto-skip gate), _semantic_available, _zoekt_available,
             indexed_files (diverse test data), search_zone
    function: rebac_search_clients (per-test ReBAC isolation)
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from collections.abc import Generator

import httpx
import pytest
from tenacity import retry, stop_after_delay, wait_exponential

from tests.config import TestSettings
from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import extract_search_results
from tests.helpers.data_generators import seed_search_files
from tests.helpers.zone_keys import create_zone_key, grant_zone_permission

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Test data constants
# ---------------------------------------------------------------------------

SEARCH_FILES = [
    {
        "name": "auth_handler.py",
        "content": (
            "def authenticate_user(username, password):\n"
            "    \"\"\"Validate user credentials against the identity store.\"\"\"\n"
            "    if not username or not password:\n"
            "        raise ValueError('Missing credentials')\n"
            "    return identity_store.verify(username, password)\n"
        ),
    },
    {
        "name": "database_config.md",
        "content": (
            "# Database Configuration\n\n"
            "PostgreSQL 16 is the primary data store.\n"
            "Connection pool: min=5, max=20\n"
            "SSL mode: verify-full in production.\n"
        ),
    },
    {
        "name": "api_routes.ts",
        "content": (
            "export const routes = {\n"
            "  login: '/api/auth/login',\n"
            "  logout: '/api/auth/logout',\n"
            "  profile: '/api/users/profile',\n"
            "  search: '/api/v2/search',\n"
            "};\n"
        ),
    },
    {
        "name": "meeting_notes.txt",
        "content": (
            "Q3 2025 planning meeting notes\n"
            "Attendees: Alice, Bob, Carol\n"
            "Topic: Migration from monolith to microservices\n"
            "Decision: Use event-driven architecture with Kafka.\n"
        ),
    },
    {
        "name": "deployment.yaml",
        "content": (
            "apiVersion: apps/v1\n"
            "kind: Deployment\n"
            "metadata:\n"
            "  name: nexus-api\n"
            "spec:\n"
            "  replicas: 3\n"
            "  template:\n"
            "    spec:\n"
            "      containers:\n"
            "        - name: nexus\n"
            "          image: nexus:latest\n"
        ),
    },
    {
        "name": "error_handling.py",
        "content": (
            "class AuthenticationError(Exception):\n"
            "    \"\"\"Raised when user authentication fails.\"\"\"\n"
            "    pass\n\n"
            "class AuthorizationError(Exception):\n"
            "    \"\"\"Raised when user lacks required permissions.\"\"\"\n"
            "    pass\n"
        ),
    },
    {
        "name": "readme.md",
        "content": (
            "# Project Overview\n\n"
            "This service handles user authentication, authorization,\n"
            "and file management for the Nexus platform.\n"
            "Built with Python and FastAPI.\n"
        ),
    },
    {
        "name": "security_audit.md",
        "content": (
            "## Security Audit Report 2025\n\n"
            "Vulnerabilities found: 3 medium, 1 low.\n"
            "SQL injection risk in legacy query builder — patched.\n"
            "CSRF token rotation implemented.\n"
            "Recommendation: Enable HSTS headers.\n"
        ),
    },
    {
        "name": "performance_report.json",
        "content": (
            '{"p50_ms": 12, "p95_ms": 45, "p99_ms": 120, '
            '"qps": 5000, "error_rate": 0.001, '
            '"measurement_window": "2025-09-01/2025-09-30"}'
        ),
    },
    {
        "name": "user_guide.md",
        "content": (
            "# User Guide\n\n"
            "Authentication requires a valid API key.\n"
            "Generate keys via Settings > API Keys.\n"
            "Rate limit: 1000 requests per minute per key.\n"
        ),
    },
]


# ---------------------------------------------------------------------------
# Module-scoped enforcement gates
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _search_available(nexus: NexusClient) -> None:
    """Skip search tests if search functionality is not available.

    Probes GET /api/v2/search/health for daemon status.
    """
    try:
        health_resp = nexus.search_health()
        if health_resp.status_code == 200:
            health = health_resp.json()
            if health.get("status") == "disabled":
                pytest.skip("Search daemon is disabled")
            if not health.get("daemon_initialized", False):
                pytest.skip("Search daemon not initialized")
            return
    except httpx.HTTPError as exc:
        logger.debug("Search health endpoint unavailable (%s), trying probe", exc)

    # Fallback: probe the search endpoint directly
    probe_resp = nexus.search("test", limit=1)
    if not probe_resp.ok:
        pytest.skip("Search endpoint not available on this server")


@pytest.fixture(scope="module")
def _semantic_available(nexus: NexusClient) -> None:
    """Skip if semantic search (embeddings) not available.

    Checks search health for db_pool_ready and probes semantic mode.
    Used by search/002, 006.
    """
    # Semantic search requires the DB pool for pgvector queries
    try:
        health_resp = nexus.search_health()
        if health_resp.status_code == 200:
            health = health_resp.json()
            if not health.get("db_pool_ready", False):
                pytest.skip("Semantic search not available (DB pool not ready)")
    except httpx.HTTPError:
        pass

    resp = nexus.search("test probe", search_mode="semantic", limit=1)
    if not resp.ok:
        error_msg = resp.error.message.lower() if resp.error else ""
        if any(
            kw in error_msg
            for kw in ("embedding", "semantic", "not available", "not enabled", "database")
        ):
            pytest.skip("Semantic search not available (embeddings not enabled)")


@pytest.fixture(scope="module")
def _zoekt_available(nexus: NexusClient) -> None:
    """Skip if Zoekt trigram search not available.

    Checks /api/v2/search/health for zoekt_available flag.
    """
    try:
        health_resp = nexus.search_health()
        if health_resp.status_code == 200:
            health = health_resp.json()
            if not health.get("zoekt_available", False):
                pytest.skip("Zoekt code search not available")
            return
    except httpx.HTTPError:
        pass

    # Fallback: try a keyword search and check for results
    resp = nexus.search_zoekt("test", limit=1)
    if not resp.ok:
        pytest.skip("Zoekt code search not available")


# ---------------------------------------------------------------------------
# Wait helper (tenacity-based polling)
# ---------------------------------------------------------------------------


def wait_until_searchable(
    nexus: NexusClient,
    query: str,
    *,
    expected_path: str | None = None,
    timeout: float = 30.0,
    zone: str | None = None,
) -> None:
    """Poll search until query returns results (or expected_path found).

    Uses tenacity retry with exponential backoff. On each retry, optionally
    triggers a search refresh for the expected_path to handle cases where
    automatic indexing is not available.
    """

    @retry(
        stop=stop_after_delay(timeout),
        wait=wait_exponential(multiplier=0.5, max=5),
        reraise=True,
    )
    def _poll() -> None:
        # Trigger refresh for the expected path if provided
        if expected_path:
            try:
                nexus.search_refresh(expected_path, zone=zone)
            except Exception:
                logger.debug("search_refresh failed during poll (non-fatal)", exc_info=True)
        resp = nexus.search(query, limit=20)
        assert resp.ok, f"Search probe failed: {resp.error}"
        results = extract_search_results(resp)
        if expected_path:
            paths = [
                r.get("path", r.get("file_path", ""))
                for r in results
                if isinstance(r, dict)
            ]
            assert any(
                p == expected_path or p.endswith(expected_path) for p in paths
            ), f"Expected path {expected_path!r} not yet in search results: {paths}"
        else:
            assert results, "Search returned no results yet"

    _poll()


# ---------------------------------------------------------------------------
# Module-scoped test data fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def search_zone(settings: TestSettings) -> str:
    """Return the primary zone for search tests."""
    return settings.zone


@pytest.fixture(scope="module")
def indexed_files(
    nexus: NexusClient, _search_available: None, settings: TestSettings,
) -> Generator[list[dict[str, str]], None, None]:
    """Seed 10 diverse files for search tests. Module-scoped, auto-cleanup.

    Uses wait_until_searchable() to verify indexing complete before yielding.
    """
    tag = uuid.uuid4().hex[:8]
    base_path = f"/test-search/{tag}"

    seeded = seed_search_files(nexus, base_path, SEARCH_FILES, zone=settings.zone)
    assert len(seeded) == len(SEARCH_FILES), (
        f"Expected {len(SEARCH_FILES)} seeded files, got {len(seeded)}"
    )

    # Wait for at least one file to become searchable (trigger refresh with zone)
    wait_until_searchable(
        nexus,
        "authenticate_user",
        expected_path=f"{base_path}/auth_handler.py",
        timeout=30.0,
        zone=settings.zone,
    )

    yield seeded

    # Cleanup: delete all seeded files
    for f in reversed(seeded):
        with contextlib.suppress(Exception):
            nexus.delete_file(f["path"])
    with contextlib.suppress(Exception):
        nexus.rmdir(base_path, recursive=True)


# ---------------------------------------------------------------------------
# ReBAC fixtures for permission-filtered search
# ---------------------------------------------------------------------------


@pytest.fixture
def rebac_search_clients(
    nexus: NexusClient, settings: TestSettings, indexed_files: list[dict[str, str]]
) -> Generator[tuple[NexusClient, NexusClient, list[str], list[str]], None, None]:
    """Create two zone-scoped clients for ReBAC search testing.

    Returns: (viewer_client, denied_client, granted_paths, denied_paths)
    - viewer_client: has direct_viewer on the first half of indexed_files
    - denied_client: no permissions on any indexed_files
    """
    import os

    # ReBAC search filtering only works when permissions are enforced.
    # Quick check: search with the denied client should see fewer results.
    # If the server doesn't enforce permissions, skip early.

    # Ensure zone_keys helpers can find the database URL
    if settings.database_url and not os.environ.get("NEXUS_DATABASE_URL"):
        os.environ["NEXUS_DATABASE_URL"] = settings.database_url

    zone = settings.zone
    viewer_user = f"search-viewer-{uuid.uuid4().hex[:8]}"
    denied_user = f"search-denied-{uuid.uuid4().hex[:8]}"

    # Split files into granted and denied halves
    mid = len(indexed_files) // 2
    granted_paths = [f["path"] for f in indexed_files[:mid]]
    denied_paths = [f["path"] for f in indexed_files[mid:]]

    # Create zone keys
    try:
        viewer_key = create_zone_key(
            nexus, zone, name=f"viewer-{viewer_user}", user_id=viewer_user
        )
        denied_key = create_zone_key(
            nexus, zone, name=f"denied-{denied_user}", user_id=denied_user
        )
    except (RuntimeError, ConnectionError) as exc:
        pytest.skip(f"Cannot create zone keys for ReBAC test: {exc}")

    # Grant viewer permission on first half, track tuple IDs for cleanup
    grant_tuple_ids: list[str] = []
    for path in granted_paths:
        grant_zone_permission(zone, viewer_user, path, relation="direct_viewer")
        # Look up the tuple ID we just created for cleanup
        with contextlib.suppress(Exception):
            tuples_resp = nexus.rebac_list_tuples(
                subject=["user", viewer_user],
                object_=["file", path],
            )
            if tuples_resp.ok and isinstance(tuples_resp.result, list):
                for t in tuples_resp.result:
                    tid = t.get("tuple_id") if isinstance(t, dict) else None
                    if tid:
                        grant_tuple_ids.append(tid)

    viewer_client = nexus.for_zone(viewer_key)
    denied_client = nexus.for_zone(denied_key)

    yield viewer_client, denied_client, granted_paths, denied_paths

    # Cleanup: revoke ReBAC grants
    for tid in grant_tuple_ids:
        with contextlib.suppress(Exception):
            nexus.rebac_delete(tid)

    # Cleanup: close HTTP sessions
    with contextlib.suppress(Exception):
        viewer_client.http.close()
    with contextlib.suppress(Exception):
        denied_client.http.close()
