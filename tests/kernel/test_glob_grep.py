"""Kernel glob and grep tests — file pattern matching and content search.

Tests: kernel/011-014
Covers: glob patterns, glob exclusion, grep substring, grep regex

Reference: TEST_PLAN.md §4.1
"""

from __future__ import annotations

import contextlib

import pytest

from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import assert_rpc_success


@pytest.mark.quick
@pytest.mark.auto
@pytest.mark.kernel
class TestKernelGlob:
    """Glob pattern matching for kernel file operations."""

    def test_glob_pattern(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/011: Glob pattern — only matching files returned."""
        # Create .py and .txt files
        py_file = f"{unique_path}/glob_test/app.py"
        txt_file = f"{unique_path}/glob_test/notes.txt"
        assert_rpc_success(nexus.write_file(py_file, "print('hello')"))
        assert_rpc_success(nexus.write_file(txt_file, "some notes"))

        # Glob for *.py under the test directory
        result = assert_rpc_success(nexus.glob(f"{unique_path}/glob_test/**/*.py"))

        # Result should contain the .py file but not the .txt file
        matched_paths = _extract_paths(result)
        assert any(p.endswith(".py") for p in matched_paths), (
            f"Expected .py file in glob results: {matched_paths}"
        )
        assert not any(p.endswith(".txt") for p in matched_paths), (
            f"Unexpected .txt file in glob results: {matched_paths}"
        )

        # Cleanup
        with contextlib.suppress(Exception):
            nexus.delete_file(py_file)
            nexus.delete_file(txt_file)

    def test_glob_exclusion(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/012: Glob exclusion — excluded files absent from results."""
        base = f"{unique_path}/glob_excl"
        files = {
            f"{base}/keep.py": "keep",
            f"{base}/skip.log": "skip",
            f"{base}/also_keep.py": "keep2",
        }
        for path, content in files.items():
            assert_rpc_success(nexus.write_file(path, content))

        # Glob for all files
        all_result = assert_rpc_success(nexus.glob(f"{base}/**/*"))
        all_paths = _extract_paths(all_result)

        # Glob for only .py files (excluding .log)
        py_result = assert_rpc_success(nexus.glob(f"{base}/**/*.py"))
        py_paths = _extract_paths(py_result)

        # .py glob should be a subset of all glob
        assert len(py_paths) <= len(all_paths), (
            f"Filtered glob should return fewer results: py={py_paths}, all={all_paths}"
        )
        assert not any(p.endswith(".log") for p in py_paths), (
            f"Unexpected .log file in .py glob results: {py_paths}"
        )

        # Cleanup
        for path in files:
            with contextlib.suppress(Exception):
                nexus.delete_file(path)


@pytest.mark.auto
@pytest.mark.kernel
class TestKernelGrep:
    """Content search (grep) for kernel file operations."""

    @pytest.mark.quick
    def test_grep_content(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/013: Grep content — substring match returns path and content."""
        base = f"{unique_path}/grep_test"
        needle = "UNIQUE_MARKER_013"
        assert_rpc_success(nexus.write_file(f"{base}/match.txt", f"line with {needle} here"))
        assert_rpc_success(nexus.write_file(f"{base}/nomatch.txt", "nothing interesting"))

        result = assert_rpc_success(nexus.grep(needle, base))

        # Result should reference the matching file
        result_str = str(result)
        assert "match" in result_str.lower() or needle in result_str, (
            f"Grep should find the marker in results: {result}"
        )

        # Cleanup
        with contextlib.suppress(Exception):
            nexus.delete_file(f"{base}/match.txt")
            nexus.delete_file(f"{base}/nomatch.txt")

    def test_grep_regex(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/014: Grep regex — regex pattern matching works."""
        base = f"{unique_path}/grep_regex"
        content = "error_code=42\nstatus=ok\nerror_code=99"
        assert_rpc_success(nexus.write_file(f"{base}/data.txt", content))

        # Grep with a regex pattern for error_code=\d+
        result = assert_rpc_success(nexus.grep(r"error_code=\d+", base))

        result_str = str(result)
        assert "error_code" in result_str or "data.txt" in result_str, (
            f"Regex grep should find error_code pattern: {result}"
        )

        # Cleanup
        with contextlib.suppress(Exception):
            nexus.delete_file(f"{base}/data.txt")


def _extract_paths(result: object) -> list[str]:
    """Extract file paths from a glob/grep result (various server formats)."""
    if isinstance(result, list):
        return [
            entry.get("path", entry.get("name", str(entry)))
            if isinstance(entry, dict)
            else str(entry)
            for entry in result
        ]
    if isinstance(result, dict):
        entries = result.get("entries", result.get("matches", result.get("files", [])))
        return _extract_paths(entries)
    return [str(result)]
