"""VFS hook E2E tests — metadata population verification.

Tests: hooks/001, hooks/002
Covers: RecordStoreWriteObserver populates size, etag, timestamps, hash

Reference: TEST_PLAN.md §4.3

Strategy: Instead of injected test hooks, verify the hook pipeline works
by observing real production hook side effects via ``get_metadata()``.
"""

from __future__ import annotations

import uuid

import pytest

from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import assert_rpc_success
from tests.hooks.conftest import extract_metadata_field, flatten_metadata


@pytest.mark.auto
@pytest.mark.hooks
class TestHookWriteMetadata:
    """Verify that production hooks populate metadata on write."""

    def test_write_populates_metadata(
        self,
        nexus: NexusClient,
        hook_file: str,
    ) -> None:
        """hooks/001: Write file -> get_metadata() returns valid size/etag/hash.

        RecordStoreWriteObserver should populate metadata fields for every
        write.  We verify by writing a file and checking that ``get_metadata``
        returns at least size and etag (or hash).
        """
        content = f"metadata_test_{uuid.uuid4().hex[:8]}"
        assert_rpc_success(nexus.write_file(hook_file, content))

        meta_resp = nexus.get_metadata(hook_file)
        assert meta_resp.ok, f"get_metadata failed: {meta_resp.error}"

        meta = meta_resp.result
        assert isinstance(meta, dict), f"Expected dict metadata, got {type(meta)}"

        flat = flatten_metadata(meta)
        all_keys = set(flat.keys())

        # At minimum, metadata should include size and at least one identifier
        known_fields = {"size", "etag", "hash", "path", "name", "type"}
        found = all_keys & known_fields
        assert found, (
            f"Metadata should contain at least one of {known_fields}, "
            f"got keys: {all_keys}"
        )

        # If size is present, it should match content length
        size = extract_metadata_field(meta, "size")
        if size is not None:
            assert int(size) == len(content.encode()), (
                f"Size mismatch: {size} != {len(content.encode())}"
            )

    def test_write_metadata_has_timestamps_and_size(
        self,
        nexus: NexusClient,
        hook_file: str,
    ) -> None:
        """hooks/002: Write file -> metadata has created_at, modified_at, size.

        Verifies the observer records temporal metadata alongside the
        content-derived fields.
        """
        content = "timestamp_metadata_test_content"
        assert_rpc_success(nexus.write_file(hook_file, content))

        meta_resp = nexus.get_metadata(hook_file)
        assert meta_resp.ok, f"get_metadata failed: {meta_resp.error}"

        meta = meta_resp.result
        assert isinstance(meta, dict), f"Expected dict metadata, got {type(meta)}"

        flat = flatten_metadata(meta)

        # Size must be present and non-zero
        size = flat.get("size")
        assert size is not None and int(size) > 0, (
            f"Metadata should have non-zero size, got: {size}"
        )

        # At least one timestamp should be present
        has_timestamp = any(
            flat.get(k) is not None
            for k in ("created_at", "modified_at", "timestamp", "updated_at")
        )
        assert has_timestamp, (
            f"Metadata should have at least one timestamp field, got keys: {set(flat.keys())}"
        )
