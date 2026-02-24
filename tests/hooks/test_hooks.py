"""VFS hook E2E tests — invocation, rejection, and chain ordering.

Tests: hooks/001, hooks/002, hooks/003, hooks/004, plus 1 positive control
Covers: post-write audit markers, pre-write rejection, chain execution order

Reference: TEST_PLAN.md §6

Requires: Server started with NEXUS_TEST_HOOKS=true
"""

from __future__ import annotations

import contextlib
import uuid

import pytest

from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import assert_rpc_success
from tests.hooks.conftest import (
    CHAIN_EXPECTED_ORDER,
    HOOK_TEST_ENDPOINT,
    path_hash,
)


@pytest.mark.auto
@pytest.mark.hooks
class TestHookInvocation:
    """Verify that VFS hooks are invoked on write operations."""

    def test_post_write_audit_marker_created(
        self,
        nexus: NexusClient,
        hook_file: str,
    ) -> None:
        """hooks/001: Write file → audit marker exists at /api/test-hooks/audit/<hash>.

        The AuditMarkerHook should record metadata for every non-internal write.
        """
        content = f"audit_test_{uuid.uuid4().hex[:8]}"
        assert_rpc_success(nexus.write_file(hook_file, content))

        # Query the audit marker via test endpoint
        ph = path_hash(hook_file)
        resp = nexus.api_get(f"{HOOK_TEST_ENDPOINT}/audit/{ph}")
        assert resp.status_code == 200, f"Audit endpoint failed: {resp.status_code}"

        data = resp.json()
        assert data.get("found") is True, (
            f"Audit marker not found for {hook_file} (hash={ph}): {data}"
        )
        assert data.get("path") == hook_file

    def test_post_write_audit_records_metadata(
        self,
        nexus: NexusClient,
        hook_file: str,
    ) -> None:
        """hooks/002: Write file → audit marker contains correct metadata.

        The audit marker should record path, timestamp, and size.
        """
        content = "metadata_test_content"
        assert_rpc_success(nexus.write_file(hook_file, content))

        ph = path_hash(hook_file)
        resp = nexus.api_get(f"{HOOK_TEST_ENDPOINT}/audit/{ph}")
        assert resp.status_code == 200

        data = resp.json()
        assert data.get("found") is True
        assert data.get("path") == hook_file
        assert isinstance(data.get("timestamp"), int | float), (
            f"Audit marker should have numeric timestamp: {data}"
        )
        assert data.get("size", 0) > 0, (
            f"Audit marker should record non-zero size: {data}"
        )


@pytest.mark.auto
@pytest.mark.hooks
class TestHookRejection:
    """Verify that post-write hooks can signal errors for blocked paths.

    Architecture note: KernelDispatch only supports post-operation hooks.
    BlockedPathHook raises AuditLogError AFTER the write completes, so
    the data IS committed but the client receives an error response.
    """

    def test_hook_rejection_returns_error(
        self,
        nexus: NexusClient,
        blocked_path: str,
    ) -> None:
        """hooks/003: Write to /blocked/ path → client gets error response.

        The BlockedPathHook raises AuditLogError for paths under /blocked/.
        The write data is committed (post-write hook), but the RPC response
        indicates failure.
        """
        resp = nexus.write_file(blocked_path, "should_be_blocked")
        assert not resp.ok, (
            f"Write to {blocked_path} should return error from post-write hook"
        )

        # Cleanup: file may exist since hook is post-write
        with contextlib.suppress(Exception):
            nexus.delete_file(blocked_path)

    def test_non_blocked_path_succeeds(
        self,
        nexus: NexusClient,
        hook_file: str,
    ) -> None:
        """Positive control: Write to non-blocked path succeeds.

        Ensures the BlockedPathHook only blocks /blocked/ paths,
        not all writes.
        """
        content = f"allowed_{uuid.uuid4().hex[:8]}"
        resp = nexus.write_file(hook_file, content)
        assert resp.ok, (
            f"Write to {hook_file} should succeed (not under /blocked/): "
            f"{resp.error}"
        )

        # Verify content
        read_resp = nexus.read_file(hook_file)
        assert read_resp.ok, f"Read should succeed: {read_resp.error}"
        assert read_resp.content_str == content


@pytest.mark.auto
@pytest.mark.hooks
class TestHookChainOrdering:
    """Verify that hooks execute in the correct priority order."""

    def test_hook_chain_executes_in_priority_order(
        self,
        nexus: NexusClient,
        hook_file: str,
    ) -> None:
        """hooks/004: Write file → chain trace shows correct execution order.

        ChainOrderHook B (registered first) should run before A,
        producing trace "BA".
        """
        content = f"chain_test_{uuid.uuid4().hex[:8]}"
        assert_rpc_success(nexus.write_file(hook_file, content))

        # Query the chain trace
        ph = path_hash(hook_file)
        resp = nexus.api_get(f"{HOOK_TEST_ENDPOINT}/chain/{ph}")
        assert resp.status_code == 200, f"Chain endpoint failed: {resp.status_code}"

        data = resp.json()
        assert data.get("found") is True, (
            f"Chain trace not found for {hook_file} (hash={ph}): {data}"
        )
        assert data.get("trace") == CHAIN_EXPECTED_ORDER, (
            f"Chain execution order should be {CHAIN_EXPECTED_ORDER!r}, "
            f"got {data.get('trace')!r}"
        )
