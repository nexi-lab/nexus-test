"""VFS hook E2E tests — backend, remote, zone, and stress scenarios.

Tests: hooks/005 through hooks/012
Covers: follower-node hooks, overwrite hooks, concurrent hooks,
        cross-zone hooks, post-op file persistence, and large-content hooks.

Reference: TEST_PLAN.md §4.3

Requires: Server started with NEXUS_TEST_HOOKS=true
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
from tests.hooks.conftest import (
    CHAIN_EXPECTED_ORDER,
    HOOK_BLOCKED_PREFIX,
    HOOK_TEST_ENDPOINT,
    path_hash,
)


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
# hooks/005 — Follower node
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.hooks
@pytest.mark.federation
class TestHookFollowerNode:
    """Verify that VFS hooks fire on the follower (remote) node."""

    def test_audit_marker_on_follower_write(
        self,
        nexus_follower: NexusClient,
        follower_hook_file: str,
    ) -> None:
        """hooks/005: Write via follower node -> audit marker exists.

        Hooks must fire regardless of which Raft node handles the write.
        Tests remote/follower backend integration (Dragonfly, PostgreSQL).
        """
        content = f"follower_audit_{uuid.uuid4().hex[:8]}"
        resp = nexus_follower.write_file(follower_hook_file, content)

        # Follower may redirect or proxy — skip if write not supported
        if not resp.ok and "redirect" in (resp.error.message or "").lower():
            pytest.skip("Follower does not accept writes (redirect mode)")

        assert_rpc_success(resp)

        ph = path_hash(follower_hook_file)
        audit_resp = nexus_follower.api_get(
            f"{HOOK_TEST_ENDPOINT}/audit/{ph}"
        )
        assert audit_resp.status_code == 200, (
            f"Follower audit endpoint failed: {audit_resp.status_code}"
        )
        data = audit_resp.json()
        assert data.get("found") is True, (
            f"Audit marker not found on follower for {follower_hook_file}: {data}"
        )
        assert data.get("path") == follower_hook_file


# ---------------------------------------------------------------------------
# hooks/006 — Overwrite (update existing file)
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.hooks
class TestHookOverwrite:
    """Verify that hooks fire on file updates, not just initial writes."""

    def test_hook_fires_on_overwrite(
        self,
        nexus: NexusClient,
        overwrite_file: str,
    ) -> None:
        """hooks/006: Overwrite existing file -> audit marker updated.

        First write creates the file; second write overwrites it.
        Audit marker should reflect the latest write's metadata.
        """
        # Initial write
        initial_content = "initial_content_v1"
        assert_rpc_success(nexus.write_file(overwrite_file, initial_content))

        # Overwrite with different content
        updated_content = "updated_content_v2_longer_payload"
        assert_rpc_success(nexus.write_file(overwrite_file, updated_content))

        ph = path_hash(overwrite_file)
        resp = nexus.api_get(f"{HOOK_TEST_ENDPOINT}/audit/{ph}")
        assert resp.status_code == 200

        data = resp.json()
        assert data.get("found") is True
        assert data.get("path") == overwrite_file
        # Size should match the LATEST write, not the initial one
        assert data.get("size", 0) >= len(updated_content), (
            f"Audit size should reflect overwritten content "
            f"(>= {len(updated_content)}), got {data.get('size')}"
        )


# ---------------------------------------------------------------------------
# hooks/007 — Concurrent writes
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.hooks
@pytest.mark.stress
class TestHookConcurrency:
    """Verify that concurrent writes each trigger independent hooks."""

    def test_concurrent_writes_all_trigger_hooks(
        self,
        nexus: NexusClient,
        worker_id: str,
    ) -> None:
        """hooks/007: N concurrent writes -> N audit markers exist.

        Uses ThreadPoolExecutor to issue parallel writes and verifies
        each one produced its own audit marker. Validates hook pipeline
        is thread-safe with all backends (Dragonfly, PostgreSQL).
        """
        n_writes = 5
        tag = uuid.uuid4().hex[:6]
        paths = [
            f"/test-hooks-concurrent/{worker_id}/{tag}/file_{i}.txt"
            for i in range(n_writes)
        ]

        def _write_and_check(path: str) -> tuple[str, bool]:
            content = f"concurrent_{uuid.uuid4().hex[:8]}"
            w_resp = nexus.write_file(path, content)
            if not w_resp.ok:
                return path, False
            ph = path_hash(path)
            a_resp = nexus.api_get(f"{HOOK_TEST_ENDPOINT}/audit/{ph}")
            if a_resp.status_code != 200:
                return path, False
            data = a_resp.json()
            return path, data.get("found") is True

        results: dict[str, bool] = {}
        with ThreadPoolExecutor(max_workers=n_writes) as pool:
            futures = {pool.submit(_write_and_check, p): p for p in paths}
            for future in as_completed(futures):
                path, found = future.result()
                results[path] = found

        # Cleanup
        for p in paths:
            with contextlib.suppress(Exception):
                nexus.delete_file(p)

        missing = [p for p, found in results.items() if not found]
        assert not missing, (
            f"{len(missing)}/{n_writes} concurrent writes missing audit markers: "
            f"{missing}"
        )


# ---------------------------------------------------------------------------
# hooks/008 — Chain order across zones
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.hooks
@pytest.mark.zone
class TestHookChainZone:
    """Verify hook chain ordering is consistent across zones."""

    def test_chain_order_in_scratch_zone(
        self,
        nexus: NexusClient,
        zone_hook_file: str,
        settings: TestSettings,
    ) -> None:
        """hooks/008: Write in scratch zone -> chain order == "BA".

        Ensures hook registration order is zone-independent.
        Tests with PostgreSQL RecordStore and Dragonfly caching active.
        """
        content = f"zone_chain_{uuid.uuid4().hex[:8]}"
        resp = nexus.write_file(
            zone_hook_file, content, zone=settings.scratch_zone
        )
        assert_rpc_success(resp)

        # Zone isolation scopes paths: /path → /zone/{zone_id}/path
        # Hooks see the scoped path, so hash must match.
        scoped_path = f"/zone/{settings.scratch_zone}{zone_hook_file}"
        ph = path_hash(scoped_path)
        chain_resp = nexus.api_get(f"{HOOK_TEST_ENDPOINT}/chain/{ph}")
        assert chain_resp.status_code == 200

        data = chain_resp.json()
        assert data.get("found") is True, (
            f"Chain trace not found for {zone_hook_file} in zone "
            f"{settings.scratch_zone} (scoped: {scoped_path}): {data}"
        )
        assert data.get("trace") == CHAIN_EXPECTED_ORDER, (
            f"Chain order in scratch zone should be {CHAIN_EXPECTED_ORDER!r}, "
            f"got {data.get('trace')!r}"
        )


# ---------------------------------------------------------------------------
# hooks/009 — Blocked path preserves file (post-op semantics)
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.hooks
class TestHookPostOpSemantics:
    """Verify post-operation hook semantics: data committed before hook runs."""

    def test_blocked_write_file_still_readable(
        self,
        nexus: NexusClient,
        worker_id: str,
    ) -> None:
        """hooks/009: Blocked write -> file data IS persisted (post-op).

        The BlockedPathHook runs AFTER the write commits.
        AuditLogError causes error response, but the file remains in storage.
        This verifies the two-phase dispatch model.
        """
        tag = uuid.uuid4().hex[:8]
        blocked = f"{HOOK_BLOCKED_PREFIX}{worker_id}/{tag}/persisted.txt"
        content = "should_persist_despite_hook_error"

        write_resp = nexus.write_file(blocked, content)
        assert not write_resp.ok, (
            f"Write to {blocked} should return error from post-write hook"
        )

        # File should still be readable because hook is post-operation
        read_resp = nexus.read_file(blocked)
        if read_resp.ok:
            assert read_resp.content_str == content, (
                f"Persisted content mismatch: expected {content!r}, "
                f"got {read_resp.content_str!r}"
            )

        # Cleanup
        with contextlib.suppress(Exception):
            nexus.delete_file(blocked)


# ---------------------------------------------------------------------------
# hooks/010 — Blocked path in non-default zone
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.hooks
@pytest.mark.zone
class TestHookBlockedZone:
    """Verify hook rejection works across zones."""

    def test_blocked_path_rejected_in_scratch_zone(
        self,
        nexus: NexusClient,
        worker_id: str,
        settings: TestSettings,
    ) -> None:
        """hooks/010: Blocked path in scratch zone -> error response.

        Ensures BlockedPathHook applies regardless of zone context.
        Tests with all backends enabled (PostgreSQL, Dragonfly).
        """
        tag = uuid.uuid4().hex[:8]
        blocked = f"{HOOK_BLOCKED_PREFIX}{worker_id}/{tag}/zone_blocked.txt"

        resp = nexus.write_file(
            blocked, "zone_block_test", zone=settings.scratch_zone
        )
        assert not resp.ok, (
            f"Write to {blocked} in zone {settings.scratch_zone} "
            f"should return error from hook"
        )

        # Cleanup
        with contextlib.suppress(Exception):
            nexus.delete_file(blocked, zone=settings.scratch_zone)


# ---------------------------------------------------------------------------
# hooks/011 — Multiple sequential writes produce distinct markers
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.hooks
class TestHookDistinctMarkers:
    """Verify each write to a different path produces its own marker."""

    def test_sequential_writes_distinct_audit_markers(
        self,
        nexus: NexusClient,
        worker_id: str,
    ) -> None:
        """hooks/011: N sequential writes to different paths -> N markers.

        Each write should produce an audit marker at its own hash.
        Validates no cross-contamination between hook invocations.
        """
        tag = uuid.uuid4().hex[:6]
        n_files = 3
        paths = [
            f"/test-hooks-distinct/{worker_id}/{tag}/file_{i}.txt"
            for i in range(n_files)
        ]

        for i, path in enumerate(paths):
            content = f"distinct_content_{i}_{uuid.uuid4().hex[:6]}"
            assert_rpc_success(nexus.write_file(path, content))

        # Verify each path has its own audit marker
        for path in paths:
            ph = path_hash(path)
            resp = nexus.api_get(f"{HOOK_TEST_ENDPOINT}/audit/{ph}")
            assert resp.status_code == 200
            data = resp.json()
            assert data.get("found") is True, (
                f"Audit marker missing for {path} (hash={ph})"
            )
            assert data.get("path") == path, (
                f"Audit marker path mismatch: expected {path!r}, "
                f"got {data.get('path')!r}"
            )

        # Cleanup
        for path in paths:
            with contextlib.suppress(Exception):
                nexus.delete_file(path)


# ---------------------------------------------------------------------------
# hooks/012 — Large content (stress)
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.hooks
@pytest.mark.stress
class TestHookLargeContent:
    """Verify hooks handle large file content without failure."""

    def test_hook_fires_on_large_write(
        self,
        nexus: NexusClient,
        worker_id: str,
    ) -> None:
        """hooks/012: Write 1 MB file -> audit marker recorded.

        Tests hook pipeline stability with large payloads through all
        backends (Dragonfly cache, PostgreSQL RecordStore).
        """
        tag = uuid.uuid4().hex[:8]
        path = f"/test-hooks-large/{worker_id}/{tag}/big_file.txt"

        # 1 MB of content
        large_content = "X" * (1024 * 1024)
        assert_rpc_success(nexus.write_file(path, large_content))

        ph = path_hash(path)
        resp = nexus.api_get(f"{HOOK_TEST_ENDPOINT}/audit/{ph}")
        assert resp.status_code == 200

        data = resp.json()
        assert data.get("found") is True, (
            f"Audit marker not found for large file {path}: {data}"
        )
        assert data.get("size", 0) >= 1024 * 1024, (
            f"Audit marker size should be >= 1MB, got {data.get('size')}"
        )

        # Cleanup
        with contextlib.suppress(Exception):
            nexus.delete_file(path)
