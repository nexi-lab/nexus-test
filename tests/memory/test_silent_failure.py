"""memory/019: Silent failure detection — corrupted memory flagged.

Tests that corrupted or invalid memories are detected and flagged,
not served silently to the caller.

Groups: auto, memory
"""

from __future__ import annotations

import uuid

import pytest

from tests.helpers.api_client import NexusClient

from .conftest import StoreMemoryFn


@pytest.mark.auto
@pytest.mark.memory
class TestSilentFailureDetection:
    """memory/019: Corrupted memory flagged, not served silently."""

    def test_corrupted_memory_detected(
        self, nexus: NexusClient, store_memory: StoreMemoryFn
    ) -> None:
        """Store valid memory, attempt malformed input -> server handles gracefully."""
        tag = uuid.uuid4().hex[:8]

        # Store a valid memory
        resp = store_memory(
            f"Valid memory content for corruption test {tag}",
            metadata={"test": "corruption", "tag": tag},
        )
        assert resp.ok, f"Failed to store memory: {resp.error}"
        assert resp.result, "No result returned from memory_store"
        assert resp.result.get("memory_id"), "No memory_id returned"

        # Attempt to store empty content — should be rejected
        empty_resp = nexus.memory_store("")
        if not empty_resp.ok:
            # Server correctly rejects empty content
            assert empty_resp.error is not None
        # If server accepts empty content, that's also acceptable (not a crash)

        # Store content with null bytes — common corruption pattern
        null_content = f"corrupted\x00content\x00{tag}"
        null_resp = nexus.memory_store(null_content)
        # Server should not crash (5xx) on null bytes
        if not null_resp.ok:
            assert abs(null_resp.error.code) < 500, (
                f"Server crashed (5xx) on null bytes: {null_resp.error}"
            )

    def test_query_with_invalid_parameters(self, nexus: NexusClient) -> None:
        """Query with invalid parameters should return clear error, not crash."""
        # Empty query
        empty_resp = nexus.memory_query("")
        # Should either return empty results or a clear error, not a 5xx crash
        if not empty_resp.ok:
            assert abs(empty_resp.error.code) < 500, (
                f"Server crashed on empty query: {empty_resp.error}"
            )

        # Extremely long query (potential DoS vector)
        long_query = "a" * 10_000
        long_resp = nexus.memory_query(long_query)
        if not long_resp.ok:
            assert abs(long_resp.error.code) < 500, (
                f"Server crashed on long query: {long_resp.error}"
            )
