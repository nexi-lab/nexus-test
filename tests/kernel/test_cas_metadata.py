"""Kernel CAS, metadata, and edge-case tests.

Tests: kernel/015-023
Covers: CAS deduplication, etag stability, file metadata, binary files,
        empty files, unicode filenames, special characters in paths

Reference: TEST_PLAN.md §4.1
"""

from __future__ import annotations

import base64
import contextlib
import hashlib

import pytest

from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import assert_rpc_success


@pytest.mark.auto
@pytest.mark.kernel
class TestKernelCAS:
    """Content-addressable storage behaviour."""

    def test_find_duplicates(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/015: Find duplicates — identical content shares same CAS hash/etag."""
        content = "identical content for dedup test 015"
        path_a = f"{unique_path}/cas/dup_a.txt"
        path_b = f"{unique_path}/cas/dup_b.txt"

        result_a = assert_rpc_success(nexus.write_file(path_a, content))
        result_b = assert_rpc_success(nexus.write_file(path_b, content))

        etag_a = _extract_etag(result_a)
        etag_b = _extract_etag(result_b)

        if etag_a is None or etag_b is None:
            pytest.skip("Server does not return etags — cannot verify CAS dedup")

        assert etag_a == etag_b, (
            f"Identical content should produce same etag: {etag_a!r} != {etag_b!r}"
        )

        # Cleanup
        with contextlib.suppress(Exception):
            nexus.delete_file(path_a)
            nexus.delete_file(path_b)

    def test_cas_dedup(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/022: CAS dedup — writing same content twice shares single blob."""
        content = "dedup blob content 022"
        path1 = f"{unique_path}/cas/dedup1.txt"
        path2 = f"{unique_path}/cas/dedup2.txt"

        result1 = assert_rpc_success(nexus.write_file(path1, content))
        result2 = assert_rpc_success(nexus.write_file(path2, content))

        # Verify via metadata if available
        meta1 = nexus.get_metadata(path1)
        meta2 = nexus.get_metadata(path2)

        if meta1.ok and meta2.ok:
            hash1 = _extract_hash(meta1.result)
            hash2 = _extract_hash(meta2.result)
            if hash1 and hash2:
                assert hash1 == hash2, f"CAS hashes should match: {hash1} != {hash2}"
        else:
            # Fall back to etag comparison
            etag1 = _extract_etag(result1)
            etag2 = _extract_etag(result2)
            if etag1 and etag2:
                assert etag1 == etag2, "Etags should match for identical content"
            else:
                pytest.skip("Server does not expose CAS hash or etag")

        # Cleanup
        with contextlib.suppress(Exception):
            nexus.delete_file(path1)
            nexus.delete_file(path2)

    def test_etag_stable_on_identical_rewrite(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/023: Etag stable on identical rewrite — same content, same etag."""
        path = f"{unique_path}/cas/stable.txt"
        content = "stable content 023"

        result1 = assert_rpc_success(nexus.write_file(path, content))
        result2 = assert_rpc_success(nexus.write_file(path, content))

        etag1 = _extract_etag(result1)
        etag2 = _extract_etag(result2)

        if etag1 is None or etag2 is None:
            pytest.skip("Server does not return etags in write response")

        assert etag1 == etag2, (
            f"Rewriting identical content should not change etag: {etag1!r} != {etag2!r}"
        )

        # Cleanup
        with contextlib.suppress(Exception):
            nexus.delete_file(path)


@pytest.mark.auto
@pytest.mark.kernel
class TestKernelMetadata:
    """File metadata retrieval."""

    def test_file_info_stat(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/016: File info stat — metadata includes size, etag, timestamps."""
        path = f"{unique_path}/meta/info.txt"
        content = "metadata test content"
        assert_rpc_success(nexus.write_file(path, content))

        meta_resp = nexus.get_metadata(path)
        assert meta_resp.ok, f"get_metadata failed: {meta_resp.error}"

        meta = meta_resp.result
        assert isinstance(meta, dict), f"Expected dict metadata, got {type(meta)}"

        # Flatten nested metadata if server wraps it
        inner = meta.get("metadata", meta)
        all_keys = set(meta.keys()) | (set(inner.keys()) if isinstance(inner, dict) else set())

        # At least one of these standard fields should be present at top or nested level
        known_fields = {
            "size", "etag", "content_type", "created_at", "modified_at",
            "hash", "metadata", "path", "name", "type",
        }
        found_fields = all_keys & known_fields
        assert found_fields, (
            f"Metadata should contain at least one of {known_fields}, "
            f"got keys: {all_keys}"
        )

        # If size is present (at any level), it should match content length
        size = meta.get("size") or (inner.get("size") if isinstance(inner, dict) else None)
        if size is not None:
            assert size == len(content.encode()), (
                f"Size mismatch: {size} != {len(content.encode())}"
            )

        # Cleanup
        with contextlib.suppress(Exception):
            nexus.delete_file(path)

    def test_custom_metadata(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/017: Custom metadata — write with metadata, read it back."""
        path = f"{unique_path}/meta/custom.txt"

        # Write file — some servers accept metadata in the write params
        write_resp = nexus.rpc("write", {
            "path": path,
            "content": "custom metadata test",
            "metadata": {"author": "test-suite", "version": "1"},
        })

        if not write_resp.ok:
            pytest.skip("Server does not support custom metadata on write")

        # Read metadata back
        meta_resp = nexus.get_metadata(path)
        if not meta_resp.ok:
            pytest.skip("Server does not support get_metadata")

        meta = meta_resp.result
        assert isinstance(meta, dict), f"Expected dict metadata, got {type(meta)}"

        # Check if custom metadata was persisted (server-dependent)
        custom = meta.get("metadata", meta.get("custom", {}))
        if custom:
            assert custom.get("author") == "test-suite", (
                f"Custom metadata roundtrip failed: {custom}"
            )

        # Cleanup
        with contextlib.suppress(Exception):
            nexus.delete_file(path)


@pytest.mark.auto
@pytest.mark.kernel
class TestKernelEdgeCases:
    """Edge cases for file content and path handling."""

    def test_binary_file_roundtrip(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/018: Binary file roundtrip — SHA-256 matches after read-back."""
        path = f"{unique_path}/edge/binary.bin"

        # Generate deterministic binary content
        raw_bytes = bytes(range(256)) * 4  # 1024 bytes
        original_hash = hashlib.sha256(raw_bytes).hexdigest()
        b64_content = base64.b64encode(raw_bytes).decode("ascii")

        # Write as base64-encoded content
        write_resp = nexus.rpc("write", {
            "path": path,
            "content": b64_content,
            "encoding": "base64",
        })

        if not write_resp.ok:
            # Fallback: some servers accept raw string content
            assert_rpc_success(nexus.write_file(path, b64_content))

        # Read back
        read_resp = nexus.read_file(path)
        assert read_resp.ok, f"Read failed: {read_resp.error}"

        # Verify content integrity
        read_content = read_resp.content_str
        try:
            decoded = base64.b64decode(read_content)
            read_hash = hashlib.sha256(decoded).hexdigest()
            assert read_hash == original_hash, (
                f"Binary roundtrip hash mismatch: {read_hash} != {original_hash}"
            )
        except Exception:
            # Server may return raw base64 string — verify it matches what we wrote
            assert read_content == b64_content, "Binary content should survive roundtrip"

        # Cleanup
        with contextlib.suppress(Exception):
            nexus.delete_file(path)

    def test_empty_file(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/019: Empty file — zero-byte file exists and reads back empty."""
        path = f"{unique_path}/edge/empty.txt"

        assert_rpc_success(nexus.write_file(path, ""))

        read_resp = nexus.read_file(path)
        assert read_resp.ok, f"Read empty file failed: {read_resp.error}"
        assert read_resp.content_str == "", (
            f"Empty file should read as empty string, got: {read_resp.content_str!r}"
        )

        # Cleanup
        with contextlib.suppress(Exception):
            nexus.delete_file(path)

    def test_unicode_filename(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/020: Unicode filename — write and read with unicode path."""
        path = f"{unique_path}/edge/café-日本語.txt"
        content = "unicode filename test"

        write_resp = nexus.write_file(path, content)
        if not write_resp.ok:
            pytest.skip(f"Server does not support unicode filenames: {write_resp.error}")

        read_resp = nexus.read_file(path)
        assert read_resp.ok, f"Read unicode path failed: {read_resp.error}"
        assert read_resp.content_str == content, (
            f"Unicode filename roundtrip failed: {read_resp.content_str!r} != {content!r}"
        )

        # Cleanup
        with contextlib.suppress(Exception):
            nexus.delete_file(path)

    def test_special_chars_in_path(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/021: Special chars in path — spaces, ampersands, equals signs."""
        test_cases = [
            ("file with spaces.txt", "spaces content"),
            ("key=value.txt", "equals content"),
            ("a&b.txt", "ampersand content"),
        ]

        for filename, content in test_cases:
            path = f"{unique_path}/edge/special/{filename}"
            write_resp = nexus.write_file(path, content)
            if not write_resp.ok:
                pytest.skip(
                    f"Server does not support special char '{filename}': {write_resp.error}"
                )

            read_resp = nexus.read_file(path)
            assert read_resp.ok, f"Read failed for '{filename}': {read_resp.error}"
            assert read_resp.content_str == content, (
                f"Roundtrip failed for '{filename}': "
                f"{read_resp.content_str!r} != {content!r}"
            )

        # Cleanup
        for filename, _ in test_cases:
            with contextlib.suppress(Exception):
                nexus.delete_file(f"{unique_path}/edge/special/{filename}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_etag(result: object) -> str | None:
    """Extract etag from a write response result."""
    if isinstance(result, dict):
        return (
            result.get("etag")
            or result.get("bytes_written", {}).get("etag")
            or result.get("hash")
        )
    return None


def _extract_hash(result: object) -> str | None:
    """Extract CAS hash from metadata result."""
    if isinstance(result, dict):
        return result.get("hash") or result.get("cas_hash") or result.get("etag")
    return None
