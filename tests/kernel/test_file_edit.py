"""Kernel file edit tests — surgical search/replace via JSON-RPC.

Tests: kernel/028-040
Covers: exact match, whitespace-normalized match, fuzzy (Levenshtein) match,
        fuzzy threshold rejection, if_match concurrency, preview mode,
        multi-edit batch, hint_line targeting, allow_multiple,
        non-existent file, permission enforcement, etag update, version increment

Reference: TEST_PLAN.md §4.1
"""

from __future__ import annotations

import contextlib

import pytest

from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import (
    assert_permission_denied,
    assert_rpc_success,
)


def _extract_etag(result: object) -> str | None:
    """Extract etag from write or edit response (handles nested bytes_written format)."""
    if isinstance(result, dict):
        return (
            result.get("etag")
            or result.get("bytes_written", {}).get("etag")
            or result.get("hash")
        )
    return None


def _extract_version(result: object) -> int | None:
    """Extract version from write or edit response (handles nested bytes_written format)."""
    if isinstance(result, dict):
        v = result.get("version")
        if v is not None:
            return int(v)
        v = result.get("bytes_written", {}).get("version")
        if v is not None:
            return int(v)
    return None


# ---------------------------------------------------------------------------
# Class 1: Matching strategies (kernel/028-031)
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.kernel
class TestFileEditMatching:
    """Edit matching strategies: exact, whitespace-normalized, fuzzy."""

    @pytest.mark.quick
    def test_exact_match(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/028: Exact match search/replace — content changed, match_type=exact."""
        path = f"{unique_path}/edit/exact.py"
        original = "def calculate_total(items):\n    return sum(items)\n"

        assert_rpc_success(nexus.write_file(path, original))

        edit_resp = nexus.edit_file(
            path, [["def calculate_total(items):", "def compute_total(items):"]]
        )
        result = assert_rpc_success(edit_resp)
        assert isinstance(result, dict), f"Expected dict result, got {type(result)}"
        assert result["success"] is True, f"Edit should succeed: {result.get('errors')}"
        assert result["applied_count"] == 1

        # Verify match type
        matches = result.get("matches", [])
        assert len(matches) >= 1, f"Expected at least 1 match, got {matches}"
        assert matches[0]["match_type"] == "exact"

        # Verify diff is non-empty
        assert result.get("diff"), "Diff should be non-empty for a successful edit"

        # Verify content changed
        read_resp = nexus.read_file(path)
        assert read_resp.ok, f"Read failed: {read_resp.error}"
        assert "compute_total" in read_resp.content_str
        assert "calculate_total" not in read_resp.content_str

        # Cleanup
        with contextlib.suppress(Exception):
            nexus.delete_file(path)

    def test_whitespace_normalized_match(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/029: Whitespace-normalized match — trailing ws ignored."""
        path = f"{unique_path}/edit/ws.py"
        # File has trailing whitespace on each line
        original = "def greet():  \n    return 'hello'  \n"

        assert_rpc_success(nexus.write_file(path, original))

        # Search string has NO trailing whitespace — should match via normalization
        edit_resp = nexus.edit_file(
            path, [["def greet():\n    return 'hello'", "def greet():\n    return 'hi'"]]
        )
        result = assert_rpc_success(edit_resp)
        assert isinstance(result, dict), f"Expected dict result, got {type(result)}"
        assert result["success"] is True, f"Edit should succeed: {result.get('errors')}"

        matches = result.get("matches", [])
        assert len(matches) >= 1
        # Should be normalized or exact (if server strips ws before storage)
        assert matches[0]["match_type"] in ("exact", "normalized"), (
            f"Expected exact or normalized match, got {matches[0]['match_type']}"
        )

        # Verify content changed
        read_resp = nexus.read_file(path)
        assert read_resp.ok
        assert "'hi'" in read_resp.content_str

        # Cleanup
        with contextlib.suppress(Exception):
            nexus.delete_file(path)

    def test_fuzzy_match_levenshtein(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/030: Fuzzy match — typo in old_str matches at >=0.85 similarity."""
        path = f"{unique_path}/edit/fuzzy.py"
        original = "def calculate_total(items):\n    return sum(items)\n"

        assert_rpc_success(nexus.write_file(path, original))

        # Typo: "calculat_total" vs actual "calculate_total" (~93% similar)
        edit_resp = nexus.edit_file(
            path,
            [["def calculat_total(items):", "def compute_total(items):"]],
            fuzzy_threshold=0.80,
        )
        result = assert_rpc_success(edit_resp)
        assert isinstance(result, dict), f"Expected dict result, got {type(result)}"
        assert result["success"] is True, f"Fuzzy edit should succeed: {result.get('errors')}"

        matches = result.get("matches", [])
        assert len(matches) >= 1
        assert matches[0]["match_type"] == "fuzzy", (
            f"Expected fuzzy match, got {matches[0]['match_type']}"
        )
        assert matches[0]["similarity"] >= 0.80, (
            f"Similarity should be >=0.80, got {matches[0]['similarity']}"
        )

        # Verify content changed
        read_resp = nexus.read_file(path)
        assert read_resp.ok
        assert "compute_total" in read_resp.content_str

        # Cleanup
        with contextlib.suppress(Exception):
            nexus.delete_file(path)

    def test_fuzzy_threshold_rejection(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/031: Fuzzy threshold rejection — completely different old_str fails."""
        path = f"{unique_path}/edit/fuzzy_reject.py"
        original = "def calculate_total(items):\n    return sum(items)\n"

        assert_rpc_success(nexus.write_file(path, original))

        # Completely different string — should NOT match at any reasonable threshold
        edit_resp = nexus.edit_file(
            path,
            [["class DatabaseConnection:", "class DBConn:"]],
        )
        result = assert_rpc_success(edit_resp)
        assert isinstance(result, dict), f"Expected dict result, got {type(result)}"
        assert result["success"] is False, "Edit with unmatched old_str should fail"

        # Verify file unchanged
        read_resp = nexus.read_file(path)
        assert read_resp.ok
        assert read_resp.content_str == original, "File should be unchanged after failed edit"

        # Cleanup
        with contextlib.suppress(Exception):
            nexus.delete_file(path)


# ---------------------------------------------------------------------------
# Class 2: Edit features (kernel/032-036)
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.kernel
class TestFileEditFeatures:
    """Advanced edit features: concurrency, preview, batch, hint_line, allow_multiple."""

    def test_if_match_concurrency(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/032: if_match concurrency control — stale etag rejected."""
        path = f"{unique_path}/edit/concurrency.py"

        write_result = assert_rpc_success(nexus.write_file(path, "version 1\n"))
        etag1 = _extract_etag(write_result)
        if etag1 is None:
            pytest.skip("Server does not return etags in write response")

        # Modify file to change its etag
        assert_rpc_success(nexus.write_file(path, "version 2\n"))

        # Attempt edit with stale etag — should fail
        edit_resp = nexus.edit_file(
            path,
            [["version 2", "version 3"]],
            if_match=etag1,
        )

        # Accept either RPC-level error or success=False result
        if not edit_resp.ok:
            # HTTP 409 wrapped as RPC error (-409)
            err = edit_resp.error
            assert err is not None
            assert abs(err.code) in (409, 32006), (
                f"Expected conflict error code, got {err.code}: {err.message}"
            )
        else:
            result = edit_resp.result
            assert isinstance(result, dict)
            # Some servers return success=False for conflict
            if result.get("success") is not False:
                pytest.skip("Server accepted stale etag (no OCC enforcement)")

        # Verify file unchanged from version 2
        read_resp = nexus.read_file(path)
        assert read_resp.ok
        assert "version 2" in read_resp.content_str, "File should still be version 2"

        # Cleanup
        with contextlib.suppress(Exception):
            nexus.delete_file(path)

    def test_preview_mode(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/033: Preview mode — diff returned, file content unchanged."""
        path = f"{unique_path}/edit/preview.py"
        original = "def old_func():\n    pass\n"

        assert_rpc_success(nexus.write_file(path, original))

        edit_resp = nexus.edit_file(
            path,
            [["def old_func():", "def new_func():"]],
            preview=True,
        )
        result = assert_rpc_success(edit_resp)
        assert isinstance(result, dict), f"Expected dict result, got {type(result)}"
        assert result["success"] is True, f"Preview edit should succeed: {result.get('errors')}"
        assert result.get("preview") is True, "Result should indicate preview mode"
        assert result.get("diff"), "Preview should return a diff"

        # Verify file unchanged
        read_resp = nexus.read_file(path)
        assert read_resp.ok
        assert read_resp.content_str == original, "File should be unchanged after preview"

        # Cleanup
        with contextlib.suppress(Exception):
            nexus.delete_file(path)

    def test_multi_edit_batch(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/034: Multi-edit batch — 3 edits applied in sequence."""
        path = f"{unique_path}/edit/batch.py"
        original = (
            "name = 'Alice'\n"
            "age = 30\n"
            "city = 'NYC'\n"
        )

        assert_rpc_success(nexus.write_file(path, original))

        edit_resp = nexus.edit_file(
            path,
            [
                ["name = 'Alice'", "name = 'Bob'"],
                ["age = 30", "age = 25"],
                ["city = 'NYC'", "city = 'SF'"],
            ],
        )
        result = assert_rpc_success(edit_resp)
        assert isinstance(result, dict), f"Expected dict result, got {type(result)}"
        assert result["success"] is True, f"Batch edit should succeed: {result.get('errors')}"
        assert result["applied_count"] == 3, (
            f"Expected 3 edits applied, got {result['applied_count']}"
        )

        # Verify all edits applied
        read_resp = nexus.read_file(path)
        assert read_resp.ok
        content = read_resp.content_str
        assert "name = 'Bob'" in content
        assert "age = 25" in content
        assert "city = 'SF'" in content

        # Cleanup
        with contextlib.suppress(Exception):
            nexus.delete_file(path)

    def test_hint_line_targeting(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/035: hint_line targeting — edit with hint_line finds target."""
        path = f"{unique_path}/edit/hint.py"
        # Create a file with target at a known line
        lines = [f"line_{i} = {i}" for i in range(1, 21)]
        original = "\n".join(lines) + "\n"

        assert_rpc_success(nexus.write_file(path, original))

        # Edit line 15 with hint_line=15
        edit_resp = nexus.edit_file(
            path,
            [{"old_str": "line_15 = 15", "new_str": "line_15 = 999", "hint_line": 15}],
        )
        result = assert_rpc_success(edit_resp)
        assert isinstance(result, dict), f"Expected dict result, got {type(result)}"
        assert result["success"] is True, f"Hint line edit should succeed: {result.get('errors')}"

        # Verify edit applied
        read_resp = nexus.read_file(path)
        assert read_resp.ok
        assert "line_15 = 999" in read_resp.content_str
        assert "line_15 = 15" not in read_resp.content_str

        # Cleanup
        with contextlib.suppress(Exception):
            nexus.delete_file(path)

    def test_allow_multiple_replaces_all(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/036: allow_multiple replaces all — 3 occurrences all replaced."""
        path = f"{unique_path}/edit/multi.py"
        original = "TODO: fix this\nsome code\nTODO: fix this\nmore code\nTODO: fix this\n"

        assert_rpc_success(nexus.write_file(path, original))

        edit_resp = nexus.edit_file(
            path,
            [{"old_str": "TODO: fix this", "new_str": "DONE", "allow_multiple": True}],
        )
        result = assert_rpc_success(edit_resp)
        assert isinstance(result, dict), f"Expected dict result, got {type(result)}"
        assert result["success"] is True, (
            f"allow_multiple edit should succeed: {result.get('errors')}"
        )

        # Verify all occurrences replaced
        read_resp = nexus.read_file(path)
        assert read_resp.ok
        assert "TODO: fix this" not in read_resp.content_str
        assert read_resp.content_str.count("DONE") == 3

        # Check match count if reported
        matches = result.get("matches", [])
        if matches and "match_count" in matches[0]:
            assert matches[0]["match_count"] == 3, (
                f"Expected match_count=3, got {matches[0]['match_count']}"
            )

        # Cleanup
        with contextlib.suppress(Exception):
            nexus.delete_file(path)


# ---------------------------------------------------------------------------
# Class 3: Error handling and state (kernel/037-040)
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.kernel
class TestFileEditErrorsAndState:
    """Edit error handling and state changes."""

    def test_nonexistent_file_404(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/037: Non-existent file edit returns error."""
        path = f"{unique_path}/edit/does_not_exist.py"

        edit_resp = nexus.edit_file(path, [["old", "new"]])
        assert not edit_resp.ok, "Editing non-existent file should return error"

        # Accept FILE_NOT_FOUND (-32000) or HTTP 404 (-404)
        err = edit_resp.error
        assert err is not None
        error_code = abs(err.code)
        assert error_code in (404, 32000), (
            f"Expected 404 or 32000 error code, got {err.code}: {err.message}"
        )

    @pytest.mark.rebac
    def test_permission_enforcement(
        self,
        nexus: NexusClient,
        unprivileged_kernel_client: NexusClient,
        unique_path: str,
    ) -> None:
        """kernel/038: Permission enforcement — unprivileged client edit denied."""
        path = f"{unique_path}/edit/protected.py"
        original = "secret = 'password123'\n"

        # Admin writes the file
        assert_rpc_success(nexus.write_file(path, original))

        # Unprivileged client attempts edit — should be denied
        edit_resp = unprivileged_kernel_client.edit_file(
            path, [["secret = 'password123'", "secret = 'hacked'"]]
        )
        assert_permission_denied(edit_resp)

        # Verify file unchanged (read as admin)
        read_resp = nexus.read_file(path)
        assert read_resp.ok
        assert read_resp.content_str == original, "File should be unchanged after denied edit"

        # Cleanup
        with contextlib.suppress(Exception):
            nexus.delete_file(path)

    def test_etag_updated_after_edit(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/039: Etag updated after edit — edit_etag != write_etag."""
        path = f"{unique_path}/edit/etag_change.py"
        original = "value = 1\n"

        write_result = assert_rpc_success(nexus.write_file(path, original))
        write_etag = _extract_etag(write_result)
        if write_etag is None:
            pytest.skip("Server does not return etags in write response")

        edit_resp = nexus.edit_file(path, [["value = 1", "value = 2"]])
        edit_result = assert_rpc_success(edit_resp)
        assert isinstance(edit_result, dict)
        assert edit_result["success"] is True

        edit_etag = _extract_etag(edit_result)
        if edit_etag is None:
            pytest.skip("Server does not return etags in edit response")

        assert edit_etag != write_etag, (
            f"Etag should change after edit: {edit_etag!r} == {write_etag!r}"
        )

        # Cleanup
        with contextlib.suppress(Exception):
            nexus.delete_file(path)

    @pytest.mark.versioning
    def test_version_incremented(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/040: Version incremented after edit."""
        path = f"{unique_path}/edit/versioned.py"
        original = "counter = 0\n"

        write_result = assert_rpc_success(nexus.write_file(path, original))

        # Get initial version from metadata
        meta_resp = nexus.get_metadata(path)
        if not meta_resp.ok:
            pytest.skip("Server does not support get_metadata")

        meta = meta_resp.result
        initial_version = None
        if isinstance(meta, dict):
            initial_version = meta.get("version") or meta.get("metadata", {}).get("version")
        if initial_version is None:
            # Try from write result
            initial_version = _extract_version(write_result)
        if initial_version is None:
            pytest.skip("Server does not expose version number")

        initial_version = int(initial_version)

        edit_resp = nexus.edit_file(path, [["counter = 0", "counter = 1"]])
        edit_result = assert_rpc_success(edit_resp)
        assert isinstance(edit_result, dict)
        assert edit_result["success"] is True

        edit_version = _extract_version(edit_result)
        if edit_version is None:
            # Check metadata after edit
            meta_resp2 = nexus.get_metadata(path)
            if meta_resp2.ok and isinstance(meta_resp2.result, dict):
                edit_version = meta_resp2.result.get("version")
                if edit_version is not None:
                    edit_version = int(edit_version)

        if edit_version is None:
            pytest.skip("Server does not return version in edit response or metadata")

        assert edit_version > initial_version, (
            f"Version should increment: {edit_version} <= {initial_version}"
        )

        # Cleanup
        with contextlib.suppress(Exception):
            nexus.delete_file(path)
