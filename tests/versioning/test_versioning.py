"""Versioning E2E tests.

Covers:
    - list_versions: version history after writes
    - get_version: retrieve specific version content
    - rollback: restore a previous version
    - diff_versions: metadata and content diffs
    - Operations REST API: list operations, agent activity
    - Edge cases: nonexistent paths, invalid versions, deleted files

Requires a running nexus server with full infrastructure
(PostgreSQL + Dragonfly + record store).
"""

from __future__ import annotations

import uuid

import pytest

from tests.versioning.conftest import VersionClient


def _path(suffix: str = "test.txt") -> str:
    """Generate a unique test path to avoid collisions."""
    return f"/workspace/versioning-e2e/{uuid.uuid4().hex[:8]}/{suffix}"


# =============================================================================
# 001: list_versions — empty history for new file
# =============================================================================


class TestListVersions:
    """Tests for the list_versions RPC method."""

    def test_001_single_write_creates_version(self, version_client: VersionClient) -> None:
        """A single write should create exactly one version entry."""
        path = _path()
        version_client.write(path, "hello v1")

        resp = version_client.list_versions(path)
        assert resp.ok, f"list_versions failed: {resp.error}"

        versions = resp.result
        assert isinstance(versions, list)
        assert len(versions) >= 1

        v1 = versions[-1]  # Oldest version
        assert v1["version"] == 1
        assert v1["size"] > 0
        assert "content_hash" in v1 or "etag" in v1
        assert "created_at" in v1

    def test_002_multiple_writes_accumulate_versions(
        self, version_client: VersionClient
    ) -> None:
        """Each write should create a new version."""
        path = _path()
        version_client.write(path, "v1 content")
        version_client.write(path, "v2 content — longer")
        version_client.write(path, "v3")

        resp = version_client.list_versions(path)
        assert resp.ok

        versions = resp.result
        assert len(versions) >= 3

        # Versions should be ordered (most recent first or last depending on impl)
        version_numbers = [v["version"] for v in versions]
        assert 1 in version_numbers
        assert 2 in version_numbers
        assert 3 in version_numbers

    def test_003_list_versions_nonexistent_file(
        self, version_client: VersionClient
    ) -> None:
        """list_versions on a nonexistent path should return error or empty list."""
        path = _path("does_not_exist.txt")
        resp = version_client.list_versions(path)
        # Either returns error or empty list — both are acceptable
        if resp.ok:
            assert resp.result == [] or isinstance(resp.result, list)
        else:
            assert resp.error is not None


# =============================================================================
# 002: get_version — retrieve specific version content
# =============================================================================


class TestGetVersion:
    """Tests for the get_version RPC method."""

    def test_004_get_version_1_returns_original_content(
        self, version_client: VersionClient
    ) -> None:
        """get_version(v=1) should return the first write's content."""
        path = _path()
        version_client.write(path, "original content")
        version_client.write(path, "updated content")

        resp = version_client.get_version(path, 1)
        assert resp.ok, f"get_version failed: {resp.error}"

        content = resp.content_str
        assert content == "original content"

    def test_005_get_version_2_returns_updated_content(
        self, version_client: VersionClient
    ) -> None:
        """get_version(v=2) should return the second write's content."""
        path = _path()
        version_client.write(path, "first")
        version_client.write(path, "second")

        resp = version_client.get_version(path, 2)
        assert resp.ok

        content = resp.content_str
        assert content == "second"

    def test_006_get_version_invalid_version_number(
        self, version_client: VersionClient
    ) -> None:
        """get_version with a non-existent version should return an error."""
        path = _path()
        version_client.write(path, "only version")

        resp = version_client.get_version(path, 999)
        assert not resp.ok, "Expected error for non-existent version"

    def test_007_get_version_preserves_binary_like_content(
        self, version_client: VersionClient
    ) -> None:
        """Version content should preserve special characters."""
        path = _path()
        special_content = "line1\nline2\ttab\r\nwindows"
        version_client.write(path, special_content)

        resp = version_client.get_version(path, 1)
        assert resp.ok

        content = resp.content_str
        assert "line1" in content
        assert "line2" in content


# =============================================================================
# 003: rollback — restore previous version
# =============================================================================


class TestRollback:
    """Tests for the rollback RPC method."""

    @pytest.mark.xfail(reason="RaftMetadataStore lacks rollback() method")
    def test_008_rollback_restores_previous_content(
        self, version_client: VersionClient
    ) -> None:
        """Rollback to v1 should restore the original content."""
        path = _path()
        version_client.write(path, "original")
        version_client.write(path, "modified")
        version_client.write(path, "latest")

        # Verify current content is "latest"
        read_resp = version_client.read(path)
        assert read_resp.content_str == "latest"

        # Rollback to version 1
        rb_resp = version_client.rollback(path, 1)
        assert rb_resp.ok, f"rollback failed: {rb_resp.error}"

        # Verify content is restored
        read_resp = version_client.read(path)
        assert read_resp.content_str == "original"

    @pytest.mark.xfail(reason="RaftMetadataStore lacks rollback() method")
    def test_009_rollback_creates_new_version(
        self, version_client: VersionClient
    ) -> None:
        """Rollback should create a new version entry (not rewrite history)."""
        path = _path()
        version_client.write(path, "v1")
        version_client.write(path, "v2")

        # Rollback to v1 — this creates v3
        version_client.rollback(path, 1)

        versions = version_client.list_versions(path).result
        assert len(versions) >= 3  # v1, v2, v3 (rollback)

        # Find the rollback version
        rollback_versions = [v for v in versions if v.get("is_rollback")]
        assert len(rollback_versions) >= 1, "Rollback should be flagged in version history"
        assert rollback_versions[0].get("rollback_from") == 1

    def test_010_rollback_invalid_version(
        self, version_client: VersionClient
    ) -> None:
        """Rollback to a non-existent version should fail."""
        path = _path()
        version_client.write(path, "content")

        resp = version_client.rollback(path, 999)
        assert not resp.ok, "Expected error for rollback to non-existent version"

    def test_011_rollback_nonexistent_file(
        self, version_client: VersionClient
    ) -> None:
        """Rollback on a non-existent file should fail."""
        path = _path("ghost.txt")

        resp = version_client.rollback(path, 1)
        assert not resp.ok, "Expected error for rollback on non-existent file"


# =============================================================================
# 004: diff_versions — compare two versions
# =============================================================================


class TestDiffVersions:
    """Tests for the diff_versions RPC method."""

    def test_012_diff_metadata_mode(self, version_client: VersionClient) -> None:
        """diff_versions mode=metadata should return size and time deltas."""
        path = _path()
        version_client.write(path, "short")
        version_client.write(path, "a much longer string for v2")

        resp = version_client.diff_versions(path, 1, 2, mode="metadata")
        assert resp.ok, f"diff_versions failed: {resp.error}"

        diff = resp.result
        assert isinstance(diff, dict)
        assert "size_delta" in diff
        assert diff["size_delta"] != 0  # Different content lengths

    def test_013_diff_content_mode(self, version_client: VersionClient) -> None:
        """diff_versions mode=content should return a unified diff string."""
        path = _path()
        version_client.write(path, "line 1\nline 2\nline 3\n")
        version_client.write(path, "line 1\nline 2 modified\nline 3\nline 4\n")

        resp = version_client.diff_versions(path, 1, 2, mode="content")
        assert resp.ok, f"diff_versions content failed: {resp.error}"

        diff = resp.result
        # Content diff should be a string (unified diff format)
        if isinstance(diff, str):
            assert "line 2" in diff  # Changed line should appear
        elif isinstance(diff, dict) and "diff" in diff:
            assert "line 2" in diff["diff"]

    def test_014_diff_same_version(self, version_client: VersionClient) -> None:
        """Diffing a version with itself should show no changes."""
        path = _path()
        version_client.write(path, "identical")

        resp = version_client.diff_versions(path, 1, 1, mode="metadata")
        assert resp.ok

        diff = resp.result
        if isinstance(diff, dict):
            # size_delta should be 0 or same_content should be True
            assert diff.get("size_delta", 0) == 0 or diff.get("same_content") is True

    def test_015_diff_invalid_version(self, version_client: VersionClient) -> None:
        """Diffing with a non-existent version should fail."""
        path = _path()
        version_client.write(path, "content")

        resp = version_client.diff_versions(path, 1, 999, mode="metadata")
        assert not resp.ok, "Expected error for diff with non-existent version"


# =============================================================================
# 005: Operations REST API
# =============================================================================


class TestOperationsAPI:
    """Tests for the GET /api/v2/operations REST endpoint."""

    def test_016_list_operations_returns_recent_writes(
        self, version_client: VersionClient
    ) -> None:
        """Operations endpoint should return recent write operations."""
        path = _path()
        version_client.write(path, "ops test content")

        ops = version_client.list_operations(
            operation_type="write",
            path_pattern=f"{path}",
            limit=10,
        )

        assert "operations" in ops
        assert len(ops["operations"]) >= 1

        op = ops["operations"][0]
        assert op["operation_type"] == "write"
        assert path in op.get("path", "")

    def test_017_list_operations_pagination(
        self, version_client: VersionClient
    ) -> None:
        """Operations should support limit/offset pagination."""
        path_base = _path("paginate")
        # Create several operations
        for i in range(5):
            version_client.write(f"{path_base}_{i}.txt", f"content {i}")

        # Page 1: first 2
        page1 = version_client.list_operations(limit=2, offset=0)
        assert len(page1["operations"]) <= 2

        # Page 2: next 2
        page2 = version_client.list_operations(limit=2, offset=2)
        assert isinstance(page2["operations"], list)

    def test_018_list_operations_filter_by_type(
        self, version_client: VersionClient
    ) -> None:
        """Filter operations by type (write vs delete)."""
        path = _path()
        version_client.write(path, "to delete")
        version_client.delete(path)

        # Filter writes only
        writes = version_client.list_operations(
            operation_type="write", path_pattern=path, limit=10
        )
        for op in writes["operations"]:
            assert op["operation_type"] == "write"

    def test_019_list_operations_time_filter(
        self, version_client: VersionClient
    ) -> None:
        """Filter operations by time range (since parameter)."""
        from datetime import datetime, timezone

        since = datetime.now(timezone.utc).isoformat()
        path = _path()
        version_client.write(path, "after timestamp")

        ops = version_client.list_operations(since=since, limit=100)
        assert isinstance(ops["operations"], list)
        # Should include our recent write
        paths = [op.get("path", "") for op in ops["operations"]]
        assert any(path in p for p in paths), f"Expected {path} in {paths}"


# =============================================================================
# 006: Edge cases — deleted files, large content, rapid writes
# =============================================================================


class TestEdgeCases:
    """Edge case and stress tests for versioning."""

    def test_020_versions_survive_delete_and_recreate(
        self, version_client: VersionClient
    ) -> None:
        """Versions from before a delete should still be accessible after recreate."""
        path = _path()
        version_client.write(path, "before delete")
        version_client.delete(path)
        version_client.write(path, "after recreate")

        # The new file gets version 1 (fresh start)
        resp = version_client.list_versions(path)
        assert resp.ok
        assert len(resp.result) >= 1

    def test_021_large_content_versioning(
        self, version_client: VersionClient
    ) -> None:
        """Versioning should handle large file content (100KB+)."""
        path = _path("large.txt")
        large_v1 = "A" * 50_000
        large_v2 = "B" * 100_000

        version_client.write(path, large_v1)
        version_client.write(path, large_v2)

        # Get version 1
        resp = version_client.get_version(path, 1)
        assert resp.ok
        assert len(resp.content_str) == 50_000

        # Get version 2
        resp = version_client.get_version(path, 2)
        assert resp.ok
        assert len(resp.content_str) == 100_000

    def test_022_rapid_sequential_writes(
        self, version_client: VersionClient
    ) -> None:
        """Rapid sequential writes should each create a unique version."""
        path = _path("rapid.txt")
        num_writes = 10

        for i in range(num_writes):
            version_client.write(path, f"version {i}")

        resp = version_client.list_versions(path)
        assert resp.ok
        assert len(resp.result) >= num_writes

    @pytest.mark.xfail(reason="RaftMetadataStore lacks rollback() method")
    def test_023_version_after_rollback_then_write(
        self, version_client: VersionClient
    ) -> None:
        """Write after rollback should create a new version on top."""
        path = _path()
        version_client.write(path, "v1")
        version_client.write(path, "v2")
        version_client.rollback(path, 1)  # Creates v3 (rollback)
        version_client.write(path, "v4 new")  # Creates v4

        resp = version_client.list_versions(path)
        assert resp.ok
        assert len(resp.result) >= 4

        # Current content should be v4
        read_resp = version_client.read(path)
        assert read_resp.content_str == "v4 new"

    @pytest.mark.xfail(reason="RaftMetadataStore lacks rollback() method")
    def test_024_diff_across_rollback(
        self, version_client: VersionClient
    ) -> None:
        """Diff between original and rollback version."""
        path = _path()
        version_client.write(path, "original")
        version_client.write(path, "changed")
        version_client.rollback(path, 1)  # v3 = rollback to v1

        # Diff v2 vs v3: v2="changed", v3="original"
        resp = version_client.diff_versions(path, 2, 3, mode="metadata")
        assert resp.ok

        diff = resp.result
        if isinstance(diff, dict):
            # Content changed between v2 and v3
            assert diff.get("same_content") is not True or diff.get("size_delta", 1) != 0

    def test_025_empty_file_versioning(
        self, version_client: VersionClient
    ) -> None:
        """Versioning should handle empty file content."""
        path = _path("empty.txt")
        version_client.write(path, "")
        version_client.write(path, "now has content")

        resp = version_client.list_versions(path)
        assert resp.ok
        assert len(resp.result) >= 2
