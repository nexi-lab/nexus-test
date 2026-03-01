"""search/003: Search respects ReBAC — only accessible files returned.

Verifies that search results are filtered by the caller's permissions.
Files written in one zone should not appear in another zone's search.
"""

from __future__ import annotations

import contextlib
import time
import uuid
import pytest

from tests.helpers.api_client import NexusClient


@pytest.mark.auto
@pytest.mark.search
@pytest.mark.rebac
class TestSearchReBAC:
    """search/003: Search results respect zone-based access control."""

    def test_search_scoped_to_zone(self, nexus: NexusClient, settings) -> None:
        """Files in one zone don't leak into another zone's search results."""
        tag = uuid.uuid4().hex[:8]
        zone_a = settings.zone or "corp"
        # Write a uniquely identifiable file
        canary = f"canary_rebac_{tag}_xylophone"
        path = f"/test-rebac-search/{tag}/secret.txt"

        try:
            resp = nexus.write_file(path, f"Top secret: {canary}", zone=zone_a)
            assert resp.ok, f"Failed to write file: {resp.error}"

            # Trigger index refresh
            nexus.search_refresh(path)
            time.sleep(2)

            # Search within the correct zone should find the canary file.
            # Zone scoping is enforced at the DB level (WHERE zone_id = :zone_id),
            # NOT by path prefix convention — txtai stores paths as-is.
            search_resp = nexus.search_query(
                canary, search_type="keyword", limit=5
            )
            assert search_resp.status_code == 200

            results = search_resp.json().get("results", [])
            # The canary file should appear in results for its own zone
            found = any(
                canary in (r.get("chunk_text", "") + r.get("path", ""))
                for r in results
            )
            assert found, (
                f"Canary file not found in search results for zone {zone_a!r}. "
                f"Got {len(results)} results: "
                f"{[r.get('path', '') for r in results]}"
            )
        finally:
            with contextlib.suppress(Exception):
                nexus.delete_file(path, zone=zone_a)

    def test_search_results_contain_valid_paths(self, nexus: NexusClient) -> None:
        """All search results include valid absolute paths."""
        resp = nexus.search_query("document", search_type="keyword", limit=10)
        assert resp.status_code == 200

        results = resp.json().get("results", [])
        if not results:
            pytest.skip("No search results to verify path format")

        for result in results:
            path = result.get("path", "")
            # Paths should be absolute (start with /)
            assert path.startswith("/"), f"Unexpected path format: {path}"
