"""VFS hook E2E tests — backend, remote, zone, and stress scenarios.

Tests: hooks/003 through hooks/008
Covers: follower-node metadata, overwrite metadata updates, concurrent
        metadata population, cross-zone metadata, distinct metadata per
        path, and large-content metadata.

Reference: TEST_PLAN.md §4.3

Strategy: Verify hook pipeline correctness by observing production metadata
populated by RecordStoreWriteObserver via ``get_metadata()`` RPC calls.
No injected test hooks or ``/api/test-hooks/*`` endpoints required.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import pytest

from tests.config import TestSettings
from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import assert_rpc_success
from tests.hooks.conftest import extract_metadata_field, flatten_metadata


# ---------------------------------------------------------------------------
# Fixtures local to this module
# ---------------------------------------------------------------------------


@pytest.fixture
def follower_hook_file(
    nexus_follower: NexusClient, worker_id: str
) -> Generator[str, Any, None]:
    """Unique path for follower-node hook tests with auto-cleanup."""
    tag = uuid.uuid4().hex[:8]
    path = f"/test-hooks-remote/{worker_id}/{tag}/data.txt"
    yield path
    with contextlib.suppress(Exception):
        nexus_follower.delete_file(path)


@pytest.fixture
def zone_hook_file(
    nexus: NexusClient, worker_id: str, settings: TestSettings,
) -> Generator[str, Any, None]:
    """Unique path in scratch zone for cross-zone hook tests."""
    tag = uuid.uuid4().hex[:8]
    path = f"/test-hooks-zone/{worker_id}/{tag}/data.txt"
    yield path
    with contextlib.suppress(Exception):
        nexus.delete_file(path, zone=settings.scratch_zone)


@pytest.fixture
def overwrite_file(
    nexus: NexusClient, worker_id: str
) -> Generator[str, Any, None]:
    """Unique path for overwrite tests with auto-cleanup."""
    tag = uuid.uuid4().hex[:8]
    path = f"/test-hooks-overwrite/{worker_id}/{tag}/data.txt"
    yield path
    with contextlib.suppress(Exception):
        nexus.delete_file(path)


# ---------------------------------------------------------------------------
# hooks/003 — Follower node metadata
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.hooks
@pytest.mark.federation
class TestHookFollowerMetadata:
    """Verify that writing via follower populates metadata."""

    def test_follower_write_has_metadata(
        self,
        nexus_follower: NexusClient,
        follower_hook_file: str,
    ) -> None:
        """hooks/003: Write via follower node -> get_metadata() returns valid data.

        Hooks must fire regardless of which Raft node handles the write.
        Verifies metadata population through follower/remote backend.
        """
        content = f"follower_meta_{uuid.uuid4().hex[:8]}"
        resp = nexus_follower.write_file(follower_hook_file, content)

        # Follower may redirect or proxy — skip if write not supported
        if not resp.ok and "redirect" in (resp.error.message or "").lower():
            pytest.skip("Follower does not accept writes (redirect mode)")

        assert_rpc_success(resp)

        meta_resp = nexus_follower.get_metadata(follower_hook_file)
        assert meta_resp.ok, (
            f"get_metadata on follower failed: {meta_resp.error}"
        )

        meta = meta_resp.result
        assert isinstance(meta, dict), f"Expected dict metadata, got {type(meta)}"

        flat = flatten_metadata(meta)
        # Verify basic metadata fields are populated
        size = flat.get("size")
        assert size is not None and int(size) > 0, (
            f"Follower metadata should have non-zero size, got: {size}"
        )


# ---------------------------------------------------------------------------
# hooks/004 — Overwrite updates metadata
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.hooks
class TestHookOverwriteMetadata:
    """Verify that metadata reflects the latest write on overwrite."""

    def test_overwrite_updates_metadata(
        self,
        nexus: NexusClient,
        overwrite_file: str,
    ) -> None:
        """hooks/004: Two writes -> metadata reflects latest content.

        First write creates the file; second write overwrites with longer
        content.  Metadata size should reflect the latest write.
        """
        # Initial write
        initial_content = "initial_content_v1"
        assert_rpc_success(nexus.write_file(overwrite_file, initial_content))

        # Overwrite with different (longer) content
        updated_content = "updated_content_v2_longer_payload"
        assert_rpc_success(nexus.write_file(overwrite_file, updated_content))

        meta_resp = nexus.get_metadata(overwrite_file)
        assert meta_resp.ok, f"get_metadata failed: {meta_resp.error}"

        meta = meta_resp.result
        assert isinstance(meta, dict)

        # Size should match the LATEST write
        size = extract_metadata_field(meta, "size")
        if size is not None:
            assert int(size) >= len(updated_content), (
                f"Metadata size should reflect overwritten content "
                f"(>= {len(updated_content)}), got {size}"
            )

        # If etag is present, verify it's not empty
        etag = extract_metadata_field(meta, "etag")
        if etag is not None:
            assert etag, "Etag should be non-empty after overwrite"


# ---------------------------------------------------------------------------
# hooks/005 — Concurrent writes all produce metadata
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.hooks
@pytest.mark.stress
class TestHookConcurrentMetadata:
    """Verify that concurrent writes each populate metadata."""

    def test_concurrent_writes_all_have_metadata(
        self,
        nexus: NexusClient,
        worker_id: str,
    ) -> None:
        """hooks/005: N concurrent writes -> all have valid get_metadata().

        Uses ThreadPoolExecutor to issue parallel writes and verifies
        each one has populated metadata.  Validates hook pipeline is
        thread-safe with all backends.
        """
        n_writes = 5
        tag = uuid.uuid4().hex[:6]
        paths = [
            f"/test-hooks-concurrent/{worker_id}/{tag}/file_{i}.txt"
            for i in range(n_writes)
        ]

        def _write_and_check(path: str) -> tuple[str, bool, str]:
            content = f"concurrent_{uuid.uuid4().hex[:8]}"
            w_resp = nexus.write_file(path, content)
            if not w_resp.ok:
                return path, False, f"write failed: {w_resp.error}"
            m_resp = nexus.get_metadata(path)
            if not m_resp.ok:
                return path, False, f"get_metadata failed: {m_resp.error}"
            meta = m_resp.result
            if not isinstance(meta, dict):
                return path, False, f"metadata not dict: {type(meta)}"
            flat = flatten_metadata(meta)
            size = flat.get("size")
            if size is None or int(size) == 0:
                return path, False, f"size missing or zero: {flat}"
            return path, True, ""

        results: dict[str, tuple[bool, str]] = {}
        with ThreadPoolExecutor(max_workers=n_writes) as pool:
            futures = {pool.submit(_write_and_check, p): p for p in paths}
            for future in as_completed(futures):
                path, ok, msg = future.result()
                results[path] = (ok, msg)

        # Cleanup
        for p in paths:
            with contextlib.suppress(Exception):
                nexus.delete_file(p)

        failed = {p: msg for p, (ok, msg) in results.items() if not ok}
        assert not failed, (
            f"{len(failed)}/{n_writes} concurrent writes missing metadata: {failed}"
        )


# ---------------------------------------------------------------------------
# hooks/006 — Zone metadata
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.hooks
@pytest.mark.zone
class TestHookZoneMetadata:
    """Verify metadata is populated for writes in non-default zones."""

    def test_zone_write_has_metadata(
        self,
        nexus: NexusClient,
        zone_hook_file: str,
        settings: TestSettings,
    ) -> None:
        """hooks/006: Write in scratch zone -> get_metadata() returns valid data.

        Ensures hook pipeline runs correctly in non-default zones.
        """
        content = f"zone_meta_{uuid.uuid4().hex[:8]}"
        resp = nexus.write_file(
            zone_hook_file, content, zone=settings.scratch_zone
        )
        assert_rpc_success(resp)

        # Use rpc() directly because NexusClient.get_metadata() incorrectly
        # puts zone_id in params instead of sending it as a header.
        meta_resp = nexus.rpc(
            "get_metadata",
            {"path": zone_hook_file},
            zone=settings.scratch_zone,
        )
        assert meta_resp.ok, (
            f"get_metadata in zone {settings.scratch_zone} failed: {meta_resp.error}"
        )

        meta = meta_resp.result
        assert isinstance(meta, dict)

        flat = flatten_metadata(meta)
        size = flat.get("size")
        assert size is not None and int(size) > 0, (
            f"Zone metadata should have non-zero size, got: {size}"
        )


# ---------------------------------------------------------------------------
# hooks/007 — Distinct metadata per path
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.hooks
class TestHookDistinctMetadata:
    """Verify each write to a different path produces its own metadata."""

    def test_sequential_writes_distinct_metadata(
        self,
        nexus: NexusClient,
        worker_id: str,
    ) -> None:
        """hooks/007: N sequential writes to different paths -> N unique metadata.

        Each write should produce metadata with unique path and etag.
        Validates no cross-contamination between hook invocations.
        """
        tag = uuid.uuid4().hex[:6]
        n_files = 3
        paths = [
            f"/test-hooks-distinct/{worker_id}/{tag}/file_{i}.txt"
            for i in range(n_files)
        ]

        etags: list[str | None] = []
        for i, path in enumerate(paths):
            content = f"distinct_content_{i}_{uuid.uuid4().hex[:6]}"
            assert_rpc_success(nexus.write_file(path, content))

        # Verify each path has its own metadata
        for path in paths:
            meta_resp = nexus.get_metadata(path)
            assert meta_resp.ok, f"get_metadata failed for {path}: {meta_resp.error}"

            meta = meta_resp.result
            assert isinstance(meta, dict)

            flat = flatten_metadata(meta)
            size = flat.get("size")
            assert size is not None and int(size) > 0, (
                f"Metadata missing for {path}: {flat}"
            )

            etag = flat.get("etag")
            etags.append(etag)

        # If etags are available, verify they're all unique (different content)
        valid_etags = [e for e in etags if e is not None]
        if len(valid_etags) == n_files:
            assert len(set(valid_etags)) == n_files, (
                f"Expected {n_files} distinct etags, got: {valid_etags}"
            )

        # Cleanup
        for path in paths:
            with contextlib.suppress(Exception):
                nexus.delete_file(path)


# ---------------------------------------------------------------------------
# hooks/008 — Large content metadata
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.hooks
@pytest.mark.stress
class TestHookLargeContentMetadata:
    """Verify hooks handle large file content without failure."""

    def test_large_write_has_correct_metadata(
        self,
        nexus: NexusClient,
        worker_id: str,
    ) -> None:
        """hooks/008: Write 1 MB file -> get_metadata() size >= 1 MB.

        Tests hook pipeline stability with large payloads through all
        backends (Dragonfly cache, PostgreSQL RecordStore).
        """
        tag = uuid.uuid4().hex[:8]
        path = f"/test-hooks-large/{worker_id}/{tag}/big_file.txt"

        # 1 MB of content
        large_content = "X" * (1024 * 1024)
        assert_rpc_success(nexus.write_file(path, large_content))

        meta_resp = nexus.get_metadata(path)
        assert meta_resp.ok, f"get_metadata failed for large file: {meta_resp.error}"

        meta = meta_resp.result
        assert isinstance(meta, dict)

        size = extract_metadata_field(meta, "size")
        assert size is not None, f"Large file metadata should have size: {meta}"
        assert int(size) >= 1024 * 1024, (
            f"Metadata size should be >= 1 MB, got {size}"
        )

        # Cleanup
        with contextlib.suppress(Exception):
            nexus.delete_file(path)
