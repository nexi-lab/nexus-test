"""memory/001: Store memory — basic store and retrieve.

Tests that the memory system can store content and return a valid memory_id.

Groups: quick, auto, memory
"""

from __future__ import annotations

import pytest

from tests.helpers.assertions import assert_memory_stored
from tests.memory.conftest import StoreMemoryFn


@pytest.mark.quick
@pytest.mark.auto
@pytest.mark.memory
class TestStoreMemory:
    """memory/001: Store memory."""

    def test_store_returns_memory_id(self, store_memory: StoreMemoryFn) -> None:
        """Storing a memory returns a valid memory_id."""
        resp = store_memory("The quarterly board meeting is scheduled for March 15")
        result = assert_memory_stored(resp)
        assert isinstance(result["memory_id"], str)
        assert len(result["memory_id"]) > 0

    def test_store_with_metadata(self, store_memory: StoreMemoryFn) -> None:
        """Storing a memory with metadata preserves the metadata."""
        resp = store_memory(
            "Engineering team completed the API redesign sprint",
            metadata={"team": "engineering", "sprint": "api-redesign"},
        )
        result = assert_memory_stored(resp)
        assert result["memory_id"]

    def test_store_with_timestamp(self, store_memory: StoreMemoryFn) -> None:
        """Storing a memory with a timestamp preserves temporal context."""
        resp = store_memory(
            "Product launch event held successfully",
            timestamp="2025-06-15T14:00:00Z",
            metadata={"event": "product_launch"},
        )
        result = assert_memory_stored(resp)
        assert result["memory_id"]

    def test_store_unicode_content(self, store_memory: StoreMemoryFn) -> None:
        """Unicode content (CJK, emoji-free, accented chars) is stored correctly."""
        content = "Le projet a atteint ses objectifs. Das Projekt war erfolgreich."
        resp = store_memory(content)
        result = assert_memory_stored(resp)
        assert result["memory_id"]

    def test_store_long_content(self, store_memory: StoreMemoryFn) -> None:
        """Long content (>1KB) is stored without truncation."""
        content = "This is a detailed technical document. " * 50  # ~1.9KB
        resp = store_memory(content, metadata={"type": "long_content"})
        result = assert_memory_stored(resp)
        assert result["memory_id"]
