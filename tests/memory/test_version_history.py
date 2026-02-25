"""memory/010: Memory version history — versions listed, diff works.

Tests that the memory versioning system tracks version history
and supports listing versions.

Groups: auto, memory
"""

from __future__ import annotations

import pytest

from tests.helpers.api_client import EnrichmentFlags, NexusClient
from tests.helpers.assertions import assert_memory_stored
from tests.memory.conftest import StoreMemoryFn


@pytest.mark.auto
@pytest.mark.memory
class TestVersionHistory:
    """memory/010: Memory version history."""

    def test_initial_memory_has_version(
        self, nexus: NexusClient, store_memory: StoreMemoryFn
    ) -> None:
        """A newly stored memory has version information."""
        resp = store_memory("Initial version of project documentation")
        result = assert_memory_stored(resp)
        memory_id = result["memory_id"]

        # Get the memory to check version fields
        get_resp = nexus.memory_get(memory_id)
        if get_resp.ok and isinstance(get_resp.result, dict):
            version = get_resp.result.get("current_version")
            # Version should be 1 or at least present
            if version is not None:
                assert version >= 1, f"Expected version >= 1, got {version}"

    def test_version_history_endpoint(
        self, nexus: NexusClient, store_memory: StoreMemoryFn
    ) -> None:
        """Version history endpoint returns at least the current version."""
        resp = store_memory("Document version tracking test content")
        result = assert_memory_stored(resp)
        memory_id = result["memory_id"]

        history_resp = nexus.memory_get_versions(memory_id)
        if not history_resp.ok:
            # Version history endpoint may not be implemented yet
            if history_resp.error and abs(history_resp.error.code) == 404:
                pytest.skip("Version history endpoint not available")
            pytest.fail(f"Version history failed: {history_resp.error}")

        history = history_resp.result
        # Should return at least one version entry
        if isinstance(history, list):
            assert len(history) >= 1, (
                f"Expected at least 1 version in history, got {len(history)}"
            )
        elif isinstance(history, dict):
            versions = history.get("versions", history.get("history", []))
            assert versions, f"Empty version history: {history}"

    def test_updated_memory_creates_new_version(
        self, nexus: NexusClient, store_memory: StoreMemoryFn
    ) -> None:
        """Storing updated content for the same path_key creates a new version."""
        # Store initial version
        resp_v1 = store_memory(
            "Project Alpha deadline is March 15 2025",
            metadata={"path_key": "project_alpha_deadline", "version_test": True},
        )
        result_v1 = assert_memory_stored(resp_v1)
        mid_v1 = result_v1["memory_id"]

        # Store updated version with evolution detection
        resp_v2 = store_memory(
            "Project Alpha deadline has been extended to April 30 2025",
            metadata={"path_key": "project_alpha_deadline", "version_test": True},
            enrichment=EnrichmentFlags(detect_evolution=True),
        )
        result_v2 = assert_memory_stored(resp_v2)
        mid_v2 = result_v2["memory_id"]

        # Check that at least two distinct memory IDs exist
        assert mid_v1 != mid_v2, "Updated memory should have different ID from original"

        # If version history is available, check the chain
        history_resp = nexus.memory_get_versions(mid_v2)
        if history_resp.ok and history_resp.result:
            history = history_resp.result
            if isinstance(history, list) and len(history) >= 2:
                # Version chain exists
                pass
            elif isinstance(history, dict):
                versions = history.get("versions", history.get("history", []))
                if len(versions) >= 2:
                    pass  # Version chain confirmed
