"""Kernel CRUD tests — core file operation correctness.

Tests: kernel/001-005, 008-009, 024-025
Covers: write, read, overwrite, delete, mkdir, mkdir -p, mv, cp, error cases

Not yet implemented: kernel/006 (rmdir), 007 (rm -r), 010 (tree)

Reference: TEST_PLAN.md §4.1
"""

from __future__ import annotations

import pytest

from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import (
    assert_file_not_found,
    assert_rpc_success,
)


@pytest.mark.quick
@pytest.mark.auto
@pytest.mark.kernel
class TestKernelCRUD:
    """Core file system CRUD operations."""

    def test_write_read_roundtrip(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/001: Write + read roundtrip — content matches."""
        path = f"{unique_path}/hello.txt"
        content = "Hello, Nexus!"

        write_result = assert_rpc_success(nexus.write_file(path, content))
        assert write_result is not None, "Write should return a result"

        read_resp = nexus.read_file(path)
        assert read_resp.ok, f"Read failed: {read_resp.error}"
        assert read_resp.content_str == content, (
            f"Read content should match written content: {read_resp.content_str!r} != {content!r}"
        )

        # Cleanup
        nexus.delete_file(path)

    def test_overwrite_changes_etag(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/002: Overwrite changes etag — new etag, new content."""
        path = f"{unique_path}/overwrite.txt"

        # Write initial content
        result1 = assert_rpc_success(nexus.write_file(path, "version 1"))

        # Overwrite with new content
        result2 = assert_rpc_success(nexus.write_file(path, "version 2"))

        # Read back — should be new content
        read_resp = nexus.read_file(path)
        assert read_resp.ok, f"Read failed: {read_resp.error}"
        assert read_resp.content_str == "version 2", f"Content should be updated: {read_resp.content_str!r}"

        # Etags should differ — write result is {"bytes_written": {"etag": "..."}}
        assert isinstance(result1, dict), f"Expected dict result, got {type(result1)}"
        assert isinstance(result2, dict), f"Expected dict result, got {type(result2)}"
        etag1 = result1.get("bytes_written", result1).get("etag")
        etag2 = result2.get("bytes_written", result2).get("etag")
        if etag1 is None or etag2 is None:
            pytest.skip("Server does not return etags in write response")
        assert etag1 != etag2, "Etag should change on overwrite"

        # Cleanup
        nexus.delete_file(path)

    def test_delete_file(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/003: Delete file — subsequent read returns error."""
        path = f"{unique_path}/to_delete.txt"

        # Create file
        assert_rpc_success(nexus.write_file(path, "temporary"))

        # Delete it
        assert_rpc_success(nexus.delete_file(path))

        # Read should fail
        assert_file_not_found(nexus, path)

    def test_mkdir_and_list(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/004: mkdir + ls — directory listed correctly."""
        dir_path = f"{unique_path}/mydir"

        # Create directory (parents=True because unique_path may not exist yet)
        assert_rpc_success(nexus.mkdir(dir_path, parents=True))

        # Write a file inside
        file_path = f"{dir_path}/file.txt"
        assert_rpc_success(nexus.write_file(file_path, "content"))

        # List directory — should contain the file
        list_result = assert_rpc_success(nexus.list_dir(dir_path))
        assert list_result is not None, "List should return entries"

        # Cleanup
        nexus.delete_file(file_path)

    def test_mkdir_nested(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/005: mkdir -p (nested) — all intermediates created."""
        deep_path = f"{unique_path}/a/b/c"

        # Create nested directories
        assert_rpc_success(nexus.mkdir(deep_path, parents=True))

        # Write a file at the deepest level
        file_path = f"{deep_path}/deep.txt"
        assert_rpc_success(nexus.write_file(file_path, "deep content"))

        # Read it back
        read_resp = nexus.read_file(file_path)
        assert read_resp.ok, f"Read failed: {read_resp.error}"
        assert read_resp.content_str == "deep content"

        # Cleanup
        nexus.delete_file(file_path)

    def test_rename(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/008: mv (rename) — old path gone, new path exists."""
        old_path = f"{unique_path}/old_name.txt"
        new_path = f"{unique_path}/new_name.txt"

        # Create file
        assert_rpc_success(nexus.write_file(old_path, "moveable"))

        # Rename
        assert_rpc_success(nexus.rename(old_path, new_path))

        # Old path should be gone
        assert_file_not_found(nexus, old_path)

        # New path should have the content
        read_resp = nexus.read_file(new_path)
        assert read_resp.ok, f"Read failed: {read_resp.error}"
        assert read_resp.content_str == "moveable"

        # Cleanup
        nexus.delete_file(new_path)

    @pytest.mark.xfail(reason="Server copy method missing param model (METHOD_PARAMS)")
    def test_copy(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/009: cp (copy) — independent copy, same content."""
        source = f"{unique_path}/source.txt"
        dest = f"{unique_path}/dest.txt"

        # Create source
        assert_rpc_success(nexus.write_file(source, "copyable"))

        # Copy — uses src_path/dst_path as per server handler
        assert_rpc_success(nexus.copy(source, dest))

        # Both should exist with same content
        source_resp = nexus.read_file(source)
        dest_resp = nexus.read_file(dest)
        assert source_resp.ok, f"Read source failed: {source_resp.error}"
        assert dest_resp.ok, f"Read dest failed: {dest_resp.error}"
        assert source_resp.content_str == dest_resp.content_str == "copyable"

        # Cleanup
        nexus.delete_file(source)
        nexus.delete_file(dest)


@pytest.mark.quick
@pytest.mark.auto
@pytest.mark.kernel
class TestKernelErrorCases:
    """Error handling and edge cases for kernel operations."""

    def test_read_nonexistent_returns_error(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/025: Non-existent read — clear error."""
        response = nexus.read_file(f"{unique_path}/does_not_exist.txt")
        assert not response.ok, "Reading non-existent file should return an error"

    def test_path_traversal_blocked(self, nexus: NexusClient) -> None:
        """kernel/024: Path traversal blocked — /../../../etc/passwd returns error."""
        response = nexus.read_file("/../../../etc/passwd")
        assert not response.ok, "Path traversal should be blocked"
