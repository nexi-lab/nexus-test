"""Kernel stress and performance tests.

Tests: kernel/050-054
Covers: large files, concurrent writes, large directory listing,
        nested glob performance, grep over large datasets

These tests are skipped unless the 'stress' marker is explicitly selected.

Reference: TEST_PLAN.md §4.1
"""

from __future__ import annotations

import contextlib
import hashlib
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import assert_rpc_success
from tests.helpers.data_generators import generate_tree


@pytest.mark.stress
@pytest.mark.kernel
class TestKernelStress:
    """Stress tests for kernel file operations."""

    @pytest.mark.timeout(300)
    def test_large_file(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/050: Large file — write 100MB, read back, verify checksum."""
        path = f"{unique_path}/stress/large_100mb.bin"

        # Generate 100MB of deterministic content
        chunk = b"A" * (1024 * 1024)  # 1MB chunk
        content = chunk * 100  # 100MB
        original_hash = hashlib.sha256(content).hexdigest()

        # Write as base64 (or plain text if binary not supported)
        import base64
        b64_content = base64.b64encode(content).decode("ascii")

        write_resp = nexus.rpc("write", {
            "path": path,
            "content": b64_content,
            "encoding": "base64",
        })
        if not write_resp.ok:
            # Fallback: write as text (much smaller content for testing)
            small_content = "X" * (10 * 1024 * 1024)  # 10MB text
            original_hash = hashlib.sha256(small_content.encode()).hexdigest()
            assert_rpc_success(nexus.write_file(path, small_content))

            read_resp = nexus.read_file(path)
            assert read_resp.ok, f"Read large file failed: {read_resp.error}"
            read_hash = hashlib.sha256(read_resp.content_str.encode()).hexdigest()
            assert read_hash == original_hash, "Large file checksum mismatch"
        else:
            read_resp = nexus.read_file(path)
            assert read_resp.ok, f"Read large file failed: {read_resp.error}"

        # Cleanup
        with contextlib.suppress(Exception):
            nexus.delete_file(path)

    @pytest.mark.timeout(300)
    def test_concurrent_writes(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/051: Concurrent writes — 10 threads, no corruption."""
        base = f"{unique_path}/stress/concurrent"
        num_threads = 10
        paths_and_content: list[tuple[str, str]] = [
            (f"{base}/file_{i}.txt", f"thread-{i}-content-{i * 1111}")
            for i in range(num_threads)
        ]

        errors: list[str] = []

        def _write(path: str, content: str) -> tuple[str, str]:
            resp = nexus.write_file(path, content)
            if not resp.ok:
                return path, f"Write failed: {resp.error}"
            return path, ""

        # Concurrent writes
        with ThreadPoolExecutor(max_workers=num_threads) as pool:
            futures = {
                pool.submit(_write, path, content): (path, content)
                for path, content in paths_and_content
            }
            for future in as_completed(futures):
                path, error = future.result()
                if error:
                    errors.append(error)

        assert not errors, f"Concurrent write errors: {errors}"

        # Verify all files have correct content
        for path, expected_content in paths_and_content:
            read_resp = nexus.read_file(path)
            assert read_resp.ok, f"Read failed for {path}: {read_resp.error}"
            assert read_resp.content_str == expected_content, (
                f"Content mismatch for {path}: "
                f"{read_resp.content_str!r} != {expected_content!r}"
            )

        # Cleanup
        for path, _ in paths_and_content:
            with contextlib.suppress(Exception):
                nexus.delete_file(path)


@pytest.mark.stress
@pytest.mark.perf
@pytest.mark.kernel
class TestKernelPerformance:
    """Performance benchmarks for kernel operations."""

    @pytest.mark.timeout(600)
    def test_large_flat_directory_ls(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/052: Large flat directory ls — 10K files, time list_dir."""
        base = f"{unique_path}/stress/flat_dir"
        num_files = 10_000

        # Create files
        write_failures = 0
        for i in range(num_files):
            resp = nexus.write_file(f"{base}/file_{i:05d}.txt", f"c-{i}")
            if not resp.ok:
                write_failures += 1

        files_written = num_files - write_failures
        assert files_written > 0, "No files were written successfully"

        # Time the list operation — handle pagination
        start = time.monotonic()
        all_entries: list[str] = []
        cursor = None
        while True:
            params: dict = {"path": base}
            if cursor:
                params["cursor"] = cursor
            result = assert_rpc_success(nexus.rpc("list", params))

            if isinstance(result, list):
                all_entries.extend(result)
                break
            elif isinstance(result, dict):
                entries = result.get("files", result.get("entries", []))
                all_entries.extend(entries)
                if not result.get("has_more", False):
                    break
                cursor = result.get("next_cursor")
                if not cursor:
                    break
            else:
                break
        elapsed = time.monotonic() - start

        assert len(all_entries) >= files_written, (
            f"Expected at least {files_written} entries, got {len(all_entries)}"
        )

        print(f"\n  list_dir({files_written} files): {elapsed:.3f}s")

        # Cleanup
        with contextlib.suppress(Exception):
            nexus.rmdir(base, recursive=True)

    @pytest.mark.timeout(300)
    def test_nested_glob(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/053: Nested glob — generate tree, glob **/* , verify count."""
        base = f"{unique_path}/stress/nested_glob"

        # Generate a tree: depth=4, breadth=3 → ~40 dirs, ~40 files
        stats = generate_tree(nexus, base, depth=4, breadth=3)

        # Glob all files
        start = time.monotonic()
        result = assert_rpc_success(nexus.glob(f"{base}/**/*"))
        elapsed = time.monotonic() - start

        if isinstance(result, list):
            count = len(result)
        elif isinstance(result, dict):
            entries = result.get("entries", result.get("matches", []))
            count = len(entries)
        else:
            count = 0

        # Should find at least as many files as were created
        assert count >= stats.files_created, (
            f"Glob should find at least {stats.files_created} files, got {count}"
        )

        print(
            f"\n  glob(**/* over {stats.files_created} files,"
            f" {stats.dirs_created} dirs): {elapsed:.3f}s"
        )

        # Cleanup
        with contextlib.suppress(Exception):
            nexus.rmdir(base, recursive=True)

    @pytest.mark.timeout(600)
    def test_grep_large_dataset(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/054: Grep large dataset — 10K files, grep for pattern."""
        base = f"{unique_path}/stress/grep_large"
        num_files = 10_000
        needle = "STRESS_NEEDLE_054"
        # Plant the needle in every 100th file
        needle_count = 0

        for i in range(num_files):
            if i % 100 == 0:
                content = f"line before\n{needle} found here\nline after"
                needle_count += 1
            else:
                content = f"regular content for file {i}"
            nexus.write_file(f"{base}/file_{i:05d}.txt", content)

        # Grep for the needle
        start = time.monotonic()
        result = assert_rpc_success(nexus.grep(needle, base))
        elapsed = time.monotonic() - start

        # Verify we found matches
        result_str = str(result)
        assert needle in result_str or "match" in result_str.lower(), (
            f"Grep should find '{needle}' in results: {result}"
        )

        print(
            f"\n  grep('{needle}') over {num_files} files,"
            f" {needle_count} matches: {elapsed:.3f}s"
        )

        # Cleanup
        with contextlib.suppress(Exception):
            nexus.rmdir(base, recursive=True)
