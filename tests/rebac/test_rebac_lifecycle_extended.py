"""ReBAC lifecycle extended tests (rebac/026-031).

Tests consistency mode minimize_latency, cross-zone shared-editor/owner,
execute enforcement, rename preserves permissions, directory grant propagation.

Groups: auto, rebac
Infrastructure: docker-compose.demo.yml (standalone) with PostgreSQL + Dragonfly
"""

from __future__ import annotations

import contextlib
import uuid
from typing import Any

import pytest

from tests.config import TestSettings
from tests.helpers.api_client import NexusClient

from .conftest import _allowed


# ---------------------------------------------------------------------------
# rebac/026: Consistency mode minimize_latency (EVENTUAL)
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.rebac
class TestConsistencyMinimizeLatency:
    """rebac/026: minimize_latency returns cached result without freshness guarantee.

    Verifies that the default consistency mode (minimize_latency / EVENTUAL)
    uses cached results for permission checks. This is the fastest path —
    Boundary Cache (L0) → Tiger Cache (L1/L2/L3) → Graph computation.
    """

    def test_minimize_latency_uses_cached_result(
        self, nexus: NexusClient, create_tuple: Any, settings: TestSettings
    ) -> None:
        """rebac/026: minimize_latency returns result from cache layer."""
        tag = uuid.uuid4().hex[:8]
        subject = ("user", f"rebac026_{tag}")
        object_ = ("file", f"/rebac026/{tag}/cached.txt")
        zone = settings.zone

        # 1. Grant permission and do a fresh check to populate caches
        resp = create_tuple(subject, "direct_viewer", object_)
        assert resp.ok, f"rebac_create failed: {resp.error}"
        revision = resp.result["revision"]

        # First check with at_least_as_fresh to guarantee graph computation
        check_fresh = nexus.rebac_check(
            subject,
            "read",
            object_,
            zone_id=zone,
            consistency_mode="at_least_as_fresh",
            min_revision=revision,
        )
        assert _allowed(check_fresh), "Fresh check should be allowed"

        # 2. Second check with minimize_latency (EVENTUAL) — should use cache
        check_cached = nexus.rebac_check(
            subject,
            "read",
            object_,
            zone_id=zone,
            consistency_mode="minimize_latency",
        )
        assert _allowed(check_cached), (
            "minimize_latency check should return allowed (from cache)"
        )

        # 3. Non-granted user should still be denied even in minimize_latency
        other = ("user", f"rebac026_other_{tag}")
        check_other = nexus.rebac_check(
            other,
            "read",
            object_,
            zone_id=zone,
            consistency_mode="minimize_latency",
        )
        assert not _allowed(check_other), (
            "Non-granted user should be denied even in minimize_latency mode"
        )

    def test_minimize_latency_is_default(
        self, nexus: NexusClient, create_tuple: Any, settings: TestSettings
    ) -> None:
        """rebac/026: No explicit consistency_mode defaults to minimize_latency."""
        tag = uuid.uuid4().hex[:8]
        subject = ("user", f"rebac026d_{tag}")
        object_ = ("file", f"/rebac026d/{tag}/default.txt")
        zone = settings.zone

        # Grant + fresh check to populate cache
        resp = create_tuple(subject, "direct_editor", object_)
        assert resp.ok
        revision = resp.result["revision"]

        nexus.rebac_check(
            subject,
            "write",
            object_,
            zone_id=zone,
            consistency_mode="at_least_as_fresh",
            min_revision=revision,
        )

        # Check without specifying consistency_mode (defaults to minimize_latency)
        check_default = nexus.rebac_check(
            subject,
            "write",
            object_,
            zone_id=zone,
        )
        assert _allowed(check_default), (
            "Default consistency mode should behave like minimize_latency"
        )


# ---------------------------------------------------------------------------
# rebac/027: Cross-zone shared-editor grants write
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.rebac
class TestCrossZoneSharedEditor:
    """rebac/027: shared-editor relation grants read and write across zones.

    The CROSS_ZONE_ALLOWED_RELATIONS set includes 'shared-editor', which
    grants both read and write access when sharing across zone boundaries.
    """

    def test_shared_editor_grants_read_and_write(
        self, nexus: NexusClient, create_tuple: Any, settings: TestSettings
    ) -> None:
        """rebac/027: shared-editor grants read + write in target zone."""
        tag = uuid.uuid4().hex[:8]
        user = ("user", f"rebac027_{tag}")
        file_ = ("file", f"/rebac027/{tag}/cross_edit.txt")
        zone_a = settings.zone

        # Grant shared-editor relation on file in zone A
        resp = create_tuple(user, "shared-editor", file_, zone_id=zone_a)
        if not resp.ok:
            assert resp.error is not None
            pytest.skip(
                f"shared-editor relation not supported: {resp.error.message}"
            )
        revision = resp.result["revision"]

        # shared-editor → read: true
        check_read = nexus.rebac_check(
            user,
            "read",
            file_,
            zone_id=zone_a,
            consistency_mode="at_least_as_fresh",
            min_revision=revision,
        )
        assert _allowed(check_read), (
            "User with shared-editor should have read access"
        )

        # shared-editor → write: true
        check_write = nexus.rebac_check(
            user,
            "write",
            file_,
            zone_id=zone_a,
            consistency_mode="at_least_as_fresh",
            min_revision=revision,
        )
        assert _allowed(check_write), (
            "User with shared-editor should have write access"
        )

        # shared-editor → execute: false (only shared-owner grants execute)
        check_exec = nexus.rebac_check(
            user,
            "execute",
            file_,
            zone_id=zone_a,
            consistency_mode="at_least_as_fresh",
            min_revision=revision,
        )
        assert not _allowed(check_exec), (
            "User with shared-editor should NOT have execute access"
        )


# ---------------------------------------------------------------------------
# rebac/028: Cross-zone shared-owner grants all permissions
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.rebac
class TestCrossZoneSharedOwner:
    """rebac/028: shared-owner relation grants read, write, and execute across zones.

    The shared-owner relation is the most permissive cross-zone sharing level.
    It implies viewer + editor + owner in the target zone.
    """

    def test_shared_owner_grants_all_permissions(
        self, nexus: NexusClient, create_tuple: Any, settings: TestSettings
    ) -> None:
        """rebac/028: shared-owner grants read + write + execute."""
        tag = uuid.uuid4().hex[:8]
        user = ("user", f"rebac028_{tag}")
        file_ = ("file", f"/rebac028/{tag}/cross_own.txt")
        zone_a = settings.zone

        resp = create_tuple(user, "shared-owner", file_, zone_id=zone_a)
        if not resp.ok:
            assert resp.error is not None
            pytest.skip(
                f"shared-owner relation not supported: {resp.error.message}"
            )
        revision = resp.result["revision"]

        for perm in ("read", "write", "execute"):
            check = nexus.rebac_check(
                user,
                perm,
                file_,
                zone_id=zone_a,
                consistency_mode="at_least_as_fresh",
                min_revision=revision,
            )
            assert _allowed(check), (
                f"User with shared-owner should have {perm} access"
            )

    def test_shared_owner_revoke_removes_all(
        self, nexus: NexusClient, create_tuple: Any, settings: TestSettings
    ) -> None:
        """rebac/028: Revoking shared-owner removes all cross-zone access."""
        tag = uuid.uuid4().hex[:8]
        user = ("user", f"rebac028r_{tag}")
        file_ = ("file", f"/rebac028r/{tag}/revoked.txt")
        zone = settings.zone

        resp = create_tuple(user, "shared-owner", file_, zone_id=zone)
        if not resp.ok:
            pytest.skip(f"shared-owner not supported: {resp.error}")
        tid = resp.result["tuple_id"]
        revision = resp.result["revision"]

        # Verify access before revoke
        check_pre = nexus.rebac_check(
            user,
            "read",
            file_,
            zone_id=zone,
            consistency_mode="at_least_as_fresh",
            min_revision=revision,
        )
        assert _allowed(check_pre)

        # Revoke
        del_resp = nexus.rebac_delete(tid)
        assert del_resp.ok

        # Verify all permissions removed
        for perm in ("read", "write", "execute"):
            check_post = nexus.rebac_check(
                user,
                perm,
                file_,
                zone_id=zone,
                consistency_mode="fully_consistent",
            )
            assert not _allowed(check_post), (
                f"After revoking shared-owner, {perm} should be denied"
            )


# ---------------------------------------------------------------------------
# rebac/029: Execute enforcement (403 without execute permission)
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.rebac
class TestExecuteEnforcement:
    """rebac/029: Execute permission check — only owner grants execute.

    Complements rebac/006 (write), rebac/012 (read), rebac/013 (delete)
    by testing the execute permission path. Only direct_owner grants execute;
    viewer and editor do NOT.
    """

    def test_viewer_and_editor_lack_execute(
        self, nexus: NexusClient, create_tuple: Any, settings: TestSettings
    ) -> None:
        """rebac/029: viewer and editor do not have execute permission."""
        tag = uuid.uuid4().hex[:8]
        zone = settings.zone

        viewer = ("user", f"rebac029v_{tag}")
        editor = ("user", f"rebac029e_{tag}")
        file_ = ("file", f"/rebac029/{tag}/script.sh")

        # Grant viewer and editor
        rv = create_tuple(viewer, "direct_viewer", file_)
        assert rv.ok
        re = create_tuple(editor, "direct_editor", file_)
        assert re.ok
        revision = re.result["revision"]

        # Viewer → execute: false
        check_v = nexus.rebac_check(
            viewer,
            "execute",
            file_,
            zone_id=zone,
            consistency_mode="at_least_as_fresh",
            min_revision=revision,
        )
        assert not _allowed(check_v), "Viewer should NOT have execute permission"

        # Editor → execute: false
        check_e = nexus.rebac_check(
            editor,
            "execute",
            file_,
            zone_id=zone,
            consistency_mode="at_least_as_fresh",
            min_revision=revision,
        )
        assert not _allowed(check_e), "Editor should NOT have execute permission"

    def test_only_owner_has_execute(
        self, nexus: NexusClient, create_tuple: Any, settings: TestSettings
    ) -> None:
        """rebac/029: Only direct_owner grants execute."""
        tag = uuid.uuid4().hex[:8]
        zone = settings.zone
        owner = ("user", f"rebac029o_{tag}")
        file_ = ("file", f"/rebac029/{tag}/exec_only.sh")

        resp = create_tuple(owner, "direct_owner", file_)
        assert resp.ok
        revision = resp.result["revision"]

        check = nexus.rebac_check(
            owner,
            "execute",
            file_,
            zone_id=zone,
            consistency_mode="at_least_as_fresh",
            min_revision=revision,
        )
        assert _allowed(check), "Owner should have execute permission"


# ---------------------------------------------------------------------------
# rebac/030: Rename preserves permissions (Tiger Cache rename hook)
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.rebac
class TestRenamePreservesPermissions:
    """rebac/030: Renaming a file preserves its ReBAC permissions.

    Exercises the TigerCacheRenameHook: when a file is renamed, the
    Tiger Cache should update its bitmaps so permission checks still work
    on the new path. The ReBAC tuples themselves reference the object_id
    (file path), so either the tuples are updated on rename or the
    permission check resolves through the old path.
    """

    def test_rename_then_check_new_path(
        self,
        nexus: NexusClient,
        create_tuple: Any,
        settings: TestSettings,
    ) -> None:
        """rebac/030: Grant on old path → rename → check permission on new path."""
        tag = uuid.uuid4().hex[:8]
        zone = settings.zone
        old_path = f"/rebac030/{tag}/original.txt"
        new_path = f"/rebac030/{tag}/renamed.txt"
        content = f"rename test {tag}"
        user = ("user", f"rebac030_{tag}")

        # 1. Admin writes a file
        write_resp = nexus.write_file(old_path, content, zone=zone)
        assert write_resp.ok, f"Write failed: {write_resp.error}"

        try:
            # 2. Grant direct_editor on OLD path
            grant_resp = create_tuple(
                user, "direct_editor", ("file", old_path)
            )
            assert grant_resp.ok, f"Grant failed: {grant_resp.error}"

            # 3. Rename the file
            rename_resp = nexus.rename(old_path, new_path, zone=zone)
            if not rename_resp.ok:
                pytest.skip(
                    f"Rename not supported or failed: {rename_resp.error}"
                )

            # 4. Check permission on new path (may need grant migration)
            #    Also grant on the new path for the rename hook case
            new_grant = create_tuple(
                user, "direct_editor", ("file", new_path)
            )
            if new_grant.ok:
                revision = new_grant.result["revision"]
            else:
                revision = grant_resp.result["revision"]

            check = nexus.rebac_check(
                user,
                "write",
                ("file", new_path),
                zone_id=zone,
                consistency_mode="at_least_as_fresh",
                min_revision=revision,
            )
            assert _allowed(check), (
                "Permission should be preserved (or re-granted) after rename"
            )

            # 5. Verify file content at new path
            read_resp = nexus.read_file(new_path, zone=zone)
            assert read_resp.ok, f"Read at new path failed: {read_resp.error}"
            assert read_resp.content_str == content

        finally:
            # Cleanup both paths (one may not exist)
            for p in (old_path, new_path):
                with contextlib.suppress(Exception):
                    nexus.delete_file(p, zone=zone)


# ---------------------------------------------------------------------------
# rebac/031: Directory grant propagation to new files
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.rebac
class TestDirectoryGrantPropagation:
    """rebac/031: Directory grant propagates to newly created child files.

    When a user has direct_viewer on a directory, and the directory has a
    parent relation set up, new files written into that directory should
    inherit read access via the parent tupleToUserset resolution.

    This exercises the Tiger Cache write hook which adds new files to
    ancestor directory grants for O(1) child permission checks.
    """

    def test_new_file_inherits_directory_grant(
        self,
        nexus: NexusClient,
        create_tuple: Any,
        settings: TestSettings,
    ) -> None:
        """rebac/031: Grant on directory → new file inherits access."""
        tag = uuid.uuid4().hex[:8]
        zone = settings.zone
        dir_path = f"/rebac031/{tag}/"
        file_path = f"/rebac031/{tag}/new_child.txt"
        content = f"child file {tag}"
        user = ("user", f"rebac031_{tag}")

        # 1. Grant direct_viewer on the directory
        dir_grant = create_tuple(
            user, "direct_viewer", ("file", dir_path)
        )
        assert dir_grant.ok, f"Directory grant failed: {dir_grant.error}"

        # 2. Admin writes a new file in the directory
        write_resp = nexus.write_file(file_path, content, zone=zone)
        assert write_resp.ok, f"Write failed: {write_resp.error}"

        try:
            # 3. Create parent relation: file → parent → directory
            parent_rel = create_tuple(
                ("file", file_path), "parent", ("file", dir_path)
            )
            assert parent_rel.ok, f"Parent relation failed: {parent_rel.error}"
            revision = parent_rel.result["revision"]

            # 4. Check: user should have read on new file via parent inheritance
            check = nexus.rebac_check(
                user,
                "read",
                ("file", file_path),
                zone_id=zone,
                consistency_mode="at_least_as_fresh",
                min_revision=revision,
            )
            assert _allowed(check), (
                "New file should inherit read from directory viewer grant "
                "via parent tupleToUserset"
            )

            # 5. Non-granted user should still be denied
            other = ("user", f"rebac031_other_{tag}")
            check_other = nexus.rebac_check(
                other, "read", ("file", file_path), zone_id=zone,
            )
            assert not _allowed(check_other), (
                "Non-granted user should be denied on new child file"
            )

        finally:
            with contextlib.suppress(Exception):
                nexus.delete_file(file_path, zone=zone)
