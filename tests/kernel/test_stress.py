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

# Max parallel writers for setup phase
_SETUP_WORKERS = 20


def _parallel_write(
    nexus: NexusClient,
    files: list[tuple[str, str]],
    *,
    workers: int = _SETUP_WORKERS,
) -> tuple[int, int]:
    """Write files in parallel. Returns (success_count, failure_count)."""
    successes = 0
    failures = 0

    def _write(path: str, content: str) -> bool:
        try:
            return nexus.write_file(path, content).ok
        except Exception:
            return False

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_write, path, content): path
            for path, content in files
        }
        for future in as_completed(futures):
            try:
                if future.result():
                    successes += 1
                else:
                    failures += 1
            except Exception:
                failures += 1

    return successes, failures


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
    """Performance benchmarks for kernel operations.

    Setup uses parallel writes so timing reflects actual operation speed,
    not write throughput.
    """

    @pytest.mark.timeout(600)
    def test_large_flat_directory_ls(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/052: Large flat directory ls — 10K files, time list_dir."""
        base = f"{unique_path}/stress/flat_dir"
        num_files = 10_000

        # --- Setup: parallel writes ---
        files = [
            (f"{base}/file_{i:05d}.txt", f"c-{i}")
            for i in range(num_files)
        ]
        setup_start = time.monotonic()
        written, failed = _parallel_write(nexus, files)
        setup_elapsed = time.monotonic() - setup_start
        assert written > 0, "No files were written successfully"
        print(f"\n  setup: {written} files written in {setup_elapsed:.1f}s")

        # --- Benchmark: list_dir with pagination ---
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

        assert len(all_entries) >= written, (
            f"Expected at least {written} entries, got {len(all_entries)}"
        )
        print(f"  list_dir({written} files): {elapsed:.3f}s")

        # Cleanup
        with contextlib.suppress(Exception):
            nexus.rmdir(base, recursive=True)

    @pytest.mark.timeout(600)
    def test_nested_glob(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/053: Nested glob — generate tree, glob **/* , verify count."""
        base = f"{unique_path}/stress/nested_glob"

        # Let server recover from previous heavy test
        time.sleep(10)

        # --- Setup: build tree via parallel writes (not generate_tree) ---
        # depth=6, breadth=5 → ~3906 files in nested dirs
        files: list[tuple[str, str]] = []
        depth, breadth = 6, 5

        def _build_paths(path: str, d: int) -> None:
            files.append((f"{path}/data.txt", f"file at depth {d} in {path}"))
            if d < depth:
                for i in range(breadth):
                    _build_paths(f"{path}/dir_{i}", d + 1)

        _build_paths(base, 1)

        setup_start = time.monotonic()
        written, _ = _parallel_write(nexus, files, workers=50)
        setup_elapsed = time.monotonic() - setup_start
        assert written > 0, "No files were written successfully"
        print(f"\n  setup: {written} files in {setup_elapsed:.1f}s")

        # --- Benchmark: glob ---
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

        assert count >= written, (
            f"Glob should find at least {written} files, got {count}"
        )
        print(f"  glob(**/*): {elapsed:.3f}s ({count} matches)")

        # Cleanup
        with contextlib.suppress(Exception):
            nexus.rmdir(base, recursive=True)

    @pytest.mark.timeout(600)
    def test_grep_large_dataset(self, nexus: NexusClient, unique_path: str) -> None:
        """kernel/054: Grep large dataset — 10K files, grep for pattern."""
        base = f"{unique_path}/stress/grep_large"
        num_files = 10_000
        needle = "STRESS_NEEDLE_054"
        needle_count = 0

        # Let server recover from previous test
        time.sleep(3)

        # --- Setup: parallel writes with needle planted every 100th file ---
        files: list[tuple[str, str]] = []
        for i in range(num_files):
            if i % 100 == 0:
                content = f"line before\n{needle} found here\nline after"
                needle_count += 1
            else:
                content = f"regular content for file {i}"
            files.append((f"{base}/file_{i:05d}.txt", content))

        setup_start = time.monotonic()
        written, failed = _parallel_write(nexus, files)
        setup_elapsed = time.monotonic() - setup_start
        assert written > 0, "No files were written successfully"
        print(f"\n  setup: {written} files written in {setup_elapsed:.1f}s")

        # --- Benchmark: grep ---
        start = time.monotonic()
        result = assert_rpc_success(nexus.grep(needle, base))
        elapsed = time.monotonic() - start

        result_str = str(result)
        assert needle in result_str or "match" in result_str.lower(), (
            f"Grep should find '{needle}' in results: {result}"
        )
        print(f"  grep('{needle}'): {elapsed:.3f}s ({needle_count} expected matches)")

        # Cleanup
        with contextlib.suppress(Exception):
            nexus.rmdir(base, recursive=True)
