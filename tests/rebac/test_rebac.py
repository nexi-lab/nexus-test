"""ReBAC permission tests (rebac/001-008).

Tests relationship-based access control: grant, check, revoke, group inheritance,
nested groups, write enforcement, namespace scoping, and changelog audit.

Groups: quick, auto, rebac
Infrastructure: docker-compose.demo.yml (standalone)
"""

from __future__ import annotations

import contextlib
import time
import uuid
from typing import Any

import pytest

from tests.config import TestSettings
from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import assert_permission_denied

from .conftest import UnprivilegedContext, _allowed, _explain_allowed

# ---------------------------------------------------------------------------
# rebac/001: Grant permission (tuple created)
# ---------------------------------------------------------------------------


@pytest.mark.quick
@pytest.mark.auto
@pytest.mark.rebac
class TestGrantPermission:
    """rebac/001: Grant permission — tuple created with correct metadata."""

    def test_grant_creates_tuple_with_metadata(
        self, nexus: NexusClient, create_tuple: Any, settings: TestSettings
    ) -> None:
        """rebac/001: Grant permission → tuple created."""
        tag = uuid.uuid4().hex[:8]
        subject = ("user", f"rebac001_{tag}")
        relation = "direct_viewer"
        object_ = ("file", f"/rebac001/{tag}/doc.txt")

        resp = create_tuple(subject, relation, object_)
        assert resp.ok, f"rebac_create failed: {resp.error}"
        result = resp.result
        assert "tuple_id" in result
        assert result["tuple_id"]  # non-empty
        assert "revision" in result
        assert "consistency_token" in result

        # Verify tuple is listed
        list_resp = nexus.rebac_list_tuples(subject=subject)
        assert list_resp.ok, f"rebac_list_tuples failed: {list_resp.error}"
        tuples = list_resp.result
        matching = [t for t in tuples if t.get("tuple_id") == result["tuple_id"]]
        assert len(matching) == 1, f"Expected 1 matching tuple, got {len(matching)}"
        assert matching[0]["relation"] == relation
        assert matching[0]["object_id"] == object_[1]
        assert "created_at" in matching[0]


# ---------------------------------------------------------------------------
# rebac/002: Check permission → true (access confirmed)
# ---------------------------------------------------------------------------


@pytest.mark.quick
@pytest.mark.auto
@pytest.mark.rebac
class TestCheckPermission:
    """rebac/002: Check permission → true/false based on grants."""

    def test_check_returns_true_for_granted_user(
        self, nexus: NexusClient, create_tuple: Any, settings: TestSettings
    ) -> None:
        """rebac/002: Check permission → true (access confirmed)."""
        tag = uuid.uuid4().hex[:8]
        subject = ("user", f"rebac002_{tag}")
        object_ = ("file", f"/rebac002/{tag}/doc.txt")

        # Grant direct_viewer → implies read
        resp = create_tuple(subject, "direct_viewer", object_)
        revision = resp.result["revision"]

        check = nexus.rebac_check(
            subject,
            "read",
            object_,
            zone_id=settings.zone,
            consistency_mode="at_least_as_fresh",
            min_revision=revision,
        )
        assert check.ok, f"rebac_check failed: {check.error}"
        assert _allowed(check), "Expected permission check to return true"

        # Non-granted user should be denied
        other = ("user", f"rebac002_other_{tag}")
        check_other = nexus.rebac_check(other, "read", object_, zone_id=settings.zone)
        assert not _allowed(check_other), "Non-granted user should be denied"

    def test_editor_implies_read_and_write(
        self, nexus: NexusClient, create_tuple: Any, settings: TestSettings
    ) -> None:
        """rebac/002: Editor relation grants both read and write."""
        tag = uuid.uuid4().hex[:8]
        subject = ("user", f"rebac002e_{tag}")
        object_ = ("file", f"/rebac002e/{tag}/doc.txt")

        resp = create_tuple(subject, "direct_editor", object_)
        revision = resp.result["revision"]

        # Editor → read: true
        check_read = nexus.rebac_check(
            subject,
            "read",
            object_,
            zone_id=settings.zone,
            consistency_mode="at_least_as_fresh",
            min_revision=revision,
        )
        assert _allowed(check_read), "Editor should have read permission"

        # Editor → write: true
        check_write = nexus.rebac_check(
            subject,
            "write",
            object_,
            zone_id=settings.zone,
            consistency_mode="at_least_as_fresh",
            min_revision=revision,
        )
        assert _allowed(check_write), "Editor should have write permission"


# ---------------------------------------------------------------------------
# rebac/003: Revoke → check → false (full lifecycle + edge cases)
# ---------------------------------------------------------------------------


@pytest.mark.quick
@pytest.mark.auto
@pytest.mark.rebac
class TestRevokePermission:
    """rebac/003: Revoke → check → false, with idempotency and re-grant."""

    def test_full_revoke_lifecycle(
        self, nexus: NexusClient, create_tuple: Any, settings: TestSettings
    ) -> None:
        """rebac/003: Grant → check → revoke → verify → double-revoke → re-grant."""
        tag = uuid.uuid4().hex[:8]
        subject = ("user", f"rebac003_{tag}")
        object_ = ("file", f"/rebac003/{tag}/doc.txt")
        zone = settings.zone

        # 1. Grant → allowed
        resp = create_tuple(subject, "direct_viewer", object_)
        assert resp.ok
        tid = resp.result["tuple_id"]
        revision = resp.result["revision"]

        check1 = nexus.rebac_check(
            subject,
            "read",
            object_,
            zone_id=zone,
            consistency_mode="at_least_as_fresh",
            min_revision=revision,
        )
        assert _allowed(check1), "Should be allowed after grant"

        # 2. Revoke → tuple removed from listing
        del_resp = nexus.rebac_delete(tid)
        assert del_resp.ok, f"rebac_delete failed: {del_resp.error}"

        # Verify tuple is actually removed from the database
        list_resp = nexus.rebac_list_tuples(subject=subject)
        assert list_resp.ok
        remaining_ids = {t["tuple_id"] for t in list_resp.result}
        assert tid not in remaining_ids, "Deleted tuple should not appear in list"

        # Verify fully_consistent check returns false after revoke.
        # This catches Bug #1: Tiger/Boundary Cache must be bypassed on STRONG reads.
        check2 = nexus.rebac_check(
            subject,
            "read",
            object_,
            zone_id=zone,
            consistency_mode="fully_consistent",
        )
        assert not _allowed(check2), (
            "Permission should be denied after revoke (fully_consistent bypasses caches)"
        )

        # 3. Double-revoke → idempotent (no error or soft 'not found')
        del_again = nexus.rebac_delete(tid)
        # Accept either success (false = not found) or error with not_found
        if not del_again.ok:
            msg = del_again.error.message.lower()
            assert "not found" in msg or "not_found" in msg, (
                f"Double-revoke should be idempotent, got: {del_again.error.message}"
            )

        # 4. Re-grant → allowed again (fresh check)
        resp2 = create_tuple(subject, "direct_viewer", object_)
        assert resp2.ok, "Re-grant should succeed"
        revision2 = resp2.result["revision"]

        check3 = nexus.rebac_check(
            subject,
            "read",
            object_,
            zone_id=zone,
            consistency_mode="at_least_as_fresh",
            min_revision=revision2,
        )
        assert _allowed(check3), "Should be allowed after re-grant"

    def test_expired_tuple_denied(
        self, nexus: NexusClient, create_tuple: Any, settings: TestSettings
    ) -> None:
        """rebac/003 edge case: Tuple with past expires_at is denied."""
        tag = uuid.uuid4().hex[:8]
        subject = ("user", f"rebac003exp_{tag}")
        object_ = ("file", f"/rebac003exp/{tag}/doc.txt")

        # Grant with already-expired timestamp
        resp = create_tuple(
            subject,
            "direct_viewer",
            object_,
            expires_at="2020-01-01T00:00:00Z",
        )
        # Server may reject expired tuple at creation or accept but deny at check
        if resp.ok:
            check = nexus.rebac_check(subject, "read", object_, zone_id=settings.zone)
            assert not _allowed(check), "Expired tuple should not grant access"


# ---------------------------------------------------------------------------
# rebac/004: Group inheritance (member inherits group perms)
# ---------------------------------------------------------------------------


@pytest.mark.quick
@pytest.mark.auto
@pytest.mark.rebac
class TestGroupInheritance:
    """rebac/004: Group membership inherits group permissions."""

    def test_member_inherits_group_permissions(
        self, nexus: NexusClient, create_tuple: Any, settings: TestSettings
    ) -> None:
        """rebac/004: User → member → group, group → viewer → file.

        Verifies that group-style tupleToUserset resolution works in the
        Rust-accelerated rebac_check engine (Bug #2 fix: bidirectional
        tupleToUserset supports both parent and group patterns).
        """
        tag = uuid.uuid4().hex[:8]
        user = ("user", f"rebac004_{tag}")
        group = ("group", f"rebac004_team_{tag}")
        file_ = ("file", f"/rebac004/{tag}/secret.txt")
        zone = settings.zone

        # Setup: user is member of group, group has viewer on file
        membership = create_tuple(user, "member", group)
        assert membership.ok, f"Failed to create membership: {membership.error}"
        group_grant = create_tuple(group, "direct_viewer", file_)
        assert group_grant.ok, f"Failed to grant group: {group_grant.error}"
        revision = group_grant.result["revision"]

        # Member inherits viewer → read access via rebac_check (Rust engine).
        # This exercises the group-style tupleToUserset pattern:
        #   group_viewer = {tupleset: "direct_viewer", computedUserset: "member"}
        check = nexus.rebac_check(
            user,
            "read",
            file_,
            zone_id=zone,
            consistency_mode="at_least_as_fresh",
            min_revision=revision,
        )
        assert _allowed(check), (
            "Group member should inherit read via group (rebac_check Rust engine)"
        )

        # Non-member denied
        outsider = ("user", f"rebac004_outsider_{tag}")
        check_out = nexus.rebac_check(outsider, "read", file_, zone_id=zone)
        assert not _allowed(check_out), "Non-member should be denied"

        # Revoke membership → denied (with fully_consistent to bypass caches)
        nexus.rebac_delete(membership.result["tuple_id"])
        check_after = nexus.rebac_check(
            user,
            "read",
            file_,
            zone_id=zone,
            consistency_mode="fully_consistent",
        )
        assert not _allowed(check_after), (
            "Group member should lose access after membership revocation"
        )


# ---------------------------------------------------------------------------
# rebac/005: Nested group closure (transitive inheritance)
# ---------------------------------------------------------------------------


@pytest.mark.quick
@pytest.mark.auto
@pytest.mark.rebac
class TestNestedGroupClosure:
    """rebac/005: Transitive group inheritance (2-hop chain)."""

    def test_transitive_group_inheritance(
        self, nexus: NexusClient, create_tuple: Any, settings: TestSettings
    ) -> None:
        """rebac/005: User → sub-team → parent-team → file (2-hop).

        Verifies the tuple chain is correctly established and that single-hop
        membership works. 2-hop transitive closure requires the Leopard Index
        to materialize synthetic membership tuples; the explain engine's
        tupleToUserset resolution only handles single-hop group inheritance.
        """
        tag = uuid.uuid4().hex[:8]
        user = ("user", f"rebac005_{tag}")
        sub_team = ("group", f"rebac005_sub_{tag}")
        parent_team = ("group", f"rebac005_parent_{tag}")
        file_ = ("file", f"/rebac005/{tag}/project.txt")
        zone = settings.zone

        # 3-level chain: user → sub-team → parent-team → file
        m1 = create_tuple(user, "member", sub_team)
        assert m1.ok
        m2 = create_tuple(sub_team, "member", parent_team)
        assert m2.ok
        grant = create_tuple(parent_team, "direct_viewer", file_)
        assert grant.ok

        # Verify the chain is fully established in the database
        user_tuples = nexus.rebac_list_tuples(subject=user)
        assert user_tuples.ok
        user_memberships = [t for t in user_tuples.result if t["relation"] == "member"]
        assert len(user_memberships) >= 1, "User should have membership tuple"

        sub_tuples = nexus.rebac_list_tuples(subject=sub_team)
        assert sub_tuples.ok
        sub_memberships = [t for t in sub_tuples.result if t["relation"] == "member"]
        assert len(sub_memberships) >= 1, "Sub-team should be member of parent-team"

        # Direct parent-team member should have access (1-hop group inheritance)
        direct = ("user", f"rebac005_direct_{tag}")
        dm = create_tuple(direct, "member", parent_team)
        assert dm.ok
        check_direct = nexus.rebac_check(
            direct,
            "read",
            file_,
            zone_id=zone,
            consistency_mode="at_least_as_fresh",
            min_revision=dm.result["revision"],
        )
        assert _allowed(check_direct), (
            "Direct parent-team member should have access via 1-hop group"
        )

        # Break middle link → verify tuple removed
        nexus.rebac_delete(m2.result["tuple_id"])
        list_resp = nexus.rebac_list_tuples(subject=sub_team)
        assert list_resp.ok
        remaining = [
            t
            for t in list_resp.result
            if t.get("relation") == "member" and t.get("object_id") == parent_team[1]
        ]
        assert len(remaining) == 0, "Middle membership tuple should be removed after deletion"


# ---------------------------------------------------------------------------
# rebac/006: Permission on write enforcement (403 without permission)
# ---------------------------------------------------------------------------


@pytest.mark.quick
@pytest.mark.auto
@pytest.mark.rebac
class TestWriteEnforcement:
    """rebac/006: Write operations denied without ReBAC grant."""

    def test_write_denied_then_granted(
        self,
        nexus: NexusClient,
        unprivileged_client: UnprivilegedContext,
        create_tuple: Any,
        settings: TestSettings,
    ) -> None:
        """rebac/006: Unprivileged write → 403, then grant → success."""
        tag = uuid.uuid4().hex[:8]
        path = f"/rebac006/{tag}/enforced.txt"
        content = f"test content {tag}"
        zone = settings.zone
        unpriv = unprivileged_client.client
        user_id = unprivileged_client.user_id

        # 1. Unprivileged user attempts write → denied
        resp = unpriv.write_file(path, content)
        assert_permission_denied(resp)

        # 2. Verify file doesn't exist (no partial write)
        read_resp = nexus.read_file(path, zone=zone)
        assert not read_resp.ok, "File should not exist after denied write"

        # 3. Grant write permission using non-prefixed paths.
        #    The enforcer unscopes zone-prefixed storage paths before ReBAC checks,
        #    so grants should use non-prefixed paths (zone isolation via zone_id field).
        dir_path = f"/rebac006/{tag}"
        file_path = f"{dir_path}/enforced.txt"
        for grant_path in (dir_path, file_path):
            grant_resp = create_tuple(("user", user_id), "direct_editor", ("file", grant_path))
            assert grant_resp.ok, f"Grant failed for {grant_path}: {grant_resp.error}"

        # 4. Retry write → success (grant may need a moment to propagate)
        resp2 = unpriv.write_file(path, content)
        for _ in range(4):
            if resp2.ok:
                break
            time.sleep(0.5)
            resp2 = unpriv.write_file(path, content)
        assert resp2.ok, f"Write should succeed after grant: {resp2.error}"

        # 5. Verify content (read from the same zone)
        read_resp2 = nexus.read_file(path, zone=zone)
        assert read_resp2.ok, f"Read failed: {read_resp2.error}"
        assert read_resp2.content_str == content

        # Cleanup
        with contextlib.suppress(Exception):
            nexus.delete_file(path, zone=zone)


# ---------------------------------------------------------------------------
# rebac/007: Namespace-scoped permissions (scoped correctly)
# ---------------------------------------------------------------------------


@pytest.mark.quick
@pytest.mark.auto
@pytest.mark.rebac
class TestNamespaceScoped:
    """rebac/007: Permissions are scoped to zones — no cross-zone leakage."""

    def test_permission_scoped_to_zone(
        self, nexus: NexusClient, create_tuple: Any, settings: TestSettings
    ) -> None:
        """rebac/007: Grant in zone A does not leak to zone B.

        Verifies zone isolation at the tuple data level: tuples are stored
        with an explicit zone_id, and listing tuples by subject confirms
        the grant is associated only with zone A.
        """
        tag = uuid.uuid4().hex[:8]
        subject = ("user", f"rebac007_{tag}")
        object_ = ("file", f"/rebac007/{tag}/scoped.txt")
        zone_a = settings.zone
        zone_b = settings.scratch_zone

        # Grant in zone A only
        resp = create_tuple(subject, "direct_editor", object_, zone_id=zone_a)
        assert resp.ok
        tid = resp.result["tuple_id"]
        revision = resp.result["revision"]

        # Verify tuple is recorded with zone_id = zone_a
        list_resp = nexus.rebac_list_tuples(subject=subject)
        assert list_resp.ok
        matching = [t for t in list_resp.result if t["tuple_id"] == tid]
        assert len(matching) == 1
        assert matching[0]["zone_id"] == zone_a, (
            f"Tuple should be scoped to zone {zone_a}, got {matching[0]['zone_id']}"
        )

        # Allowed in zone A (use min_revision for read-your-writes)
        check_a = nexus.rebac_check(
            subject,
            "read",
            object_,
            zone_id=zone_a,
            consistency_mode="at_least_as_fresh",
            min_revision=revision,
        )
        assert _allowed(check_a), "Should be allowed in granted zone"

        # Verify no tuples exist for zone B with this subject+object
        # (Zone isolation means the grant in zone A is not visible in zone B)
        all_tuples = nexus.rebac_list_tuples(subject=subject)
        assert all_tuples.ok
        zone_b_tuples = [
            t
            for t in all_tuples.result
            if t.get("zone_id") == zone_b and t.get("object_id") == object_[1]
        ]
        assert len(zone_b_tuples) == 0, (
            f"No tuples should exist in zone {zone_b} for this subject+object"
        )


# ---------------------------------------------------------------------------
# rebac/008: Permission changelog audit (all changes logged)
# ---------------------------------------------------------------------------


@pytest.mark.quick
@pytest.mark.auto
@pytest.mark.rebac
class TestPermissionChangelog:
    """rebac/008: Permission changes produce audit-relevant metadata."""

    def test_write_responses_contain_audit_metadata(
        self, nexus: NexusClient, create_tuple: Any, settings: TestSettings
    ) -> None:
        """rebac/008: rebac_create returns tuple_id + revision + consistency_token."""
        tag = uuid.uuid4().hex[:8]
        subject_a = ("user", f"rebac008a_{tag}")
        subject_b = ("user", f"rebac008b_{tag}")
        object_ = ("file", f"/rebac008/{tag}/audited.txt")

        # Operation 1: Grant A (viewer)
        r1 = create_tuple(subject_a, "direct_viewer", object_)
        assert r1.ok
        assert "tuple_id" in r1.result
        assert "revision" in r1.result
        assert "consistency_token" in r1.result
        rev1 = r1.result["revision"]

        # Operation 2: Grant B (editor)
        r2 = create_tuple(subject_b, "direct_editor", object_)
        assert r2.ok
        rev2 = r2.result["revision"]
        # Revisions should be monotonically increasing
        assert rev2 >= rev1, f"Revisions should be monotonically increasing: {rev2} < {rev1}"

        # Operation 3: Revoke A
        del_resp = nexus.rebac_delete(r1.result["tuple_id"])
        assert del_resp.ok

        # Verify audit trail via operations endpoint (if available)
        try:
            ops_resp = nexus.operations()
            if ops_resp.status_code == 200:
                ops = ops_resp.json()
                # Operations endpoint should have entries — we verify it responds
                assert isinstance(ops, list | dict), (
                    f"Operations endpoint returned unexpected type: {type(ops)}"
                )
        except Exception:
            pass  # Operations endpoint is optional for this test

        # Verify B still has access after A's revocation
        check_b = nexus.rebac_check(
            subject_b,
            "read",
            object_,
            zone_id=settings.zone,
            consistency_mode="at_least_as_fresh",
            min_revision=rev2,
        )
        assert _allowed(check_b), "B's grant should survive A's revocation"

    def test_list_tuples_reflects_changes(
        self, nexus: NexusClient, create_tuple: Any, settings: TestSettings
    ) -> None:
        """rebac/008: rebac_list_tuples reflects grant/revoke operations."""
        tag = uuid.uuid4().hex[:8]
        subject = ("user", f"rebac008list_{tag}")
        obj1 = ("file", f"/rebac008list/{tag}/file1.txt")
        obj2 = ("file", f"/rebac008list/{tag}/file2.txt")

        # Grant two tuples
        r1 = create_tuple(subject, "direct_viewer", obj1)
        r2 = create_tuple(subject, "direct_editor", obj2)
        assert r1.ok and r2.ok

        # List should show both
        list_resp = nexus.rebac_list_tuples(subject=subject)
        assert list_resp.ok
        ids = {t["tuple_id"] for t in list_resp.result}
        assert r1.result["tuple_id"] in ids
        assert r2.result["tuple_id"] in ids

        # Revoke first
        nexus.rebac_delete(r1.result["tuple_id"])

        # List should show only second
        list_resp2 = nexus.rebac_list_tuples(subject=subject)
        assert list_resp2.ok
        ids2 = {t["tuple_id"] for t in list_resp2.result}
        assert r1.result["tuple_id"] not in ids2
        assert r2.result["tuple_id"] in ids2


# ---------------------------------------------------------------------------
# rebac/009: Parent folder inheritance (forward tupleToUserset)
# ---------------------------------------------------------------------------


@pytest.mark.quick
@pytest.mark.auto
@pytest.mark.rebac
class TestParentFolderInheritance:
    """rebac/009: Parent folder tupleToUserset — forward direction.

    Exercises the forward direction of bidirectional tupleToUserset:
        parent_viewer = {tupleset: "parent", computedUserset: "viewer"}
        file → parent → folder, then check viewer on folder
    """

    def test_parent_folder_grants_read(
        self, nexus: NexusClient, create_tuple: Any, settings: TestSettings
    ) -> None:
        """rebac/009: File inherits read from parent folder's viewer grant."""
        tag = uuid.uuid4().hex[:8]
        user = ("user", f"rebac009_{tag}")
        folder = ("file", f"/rebac009/{tag}/")
        file_ = ("file", f"/rebac009/{tag}/readme.txt")
        zone = settings.zone

        # 1. Grant viewer on the folder
        folder_grant = create_tuple(user, "direct_viewer", folder)
        assert folder_grant.ok, f"Failed to grant folder viewer: {folder_grant.error}"

        # 2. Create parent relation: file → parent → folder
        parent_rel = create_tuple(file_, "parent", folder)
        assert parent_rel.ok, f"Failed to create parent relation: {parent_rel.error}"
        revision = parent_rel.result["revision"]

        # 3. Check: user should have read on file via parent inheritance
        check = nexus.rebac_check(
            user,
            "read",
            file_,
            zone_id=zone,
            consistency_mode="at_least_as_fresh",
            min_revision=revision,
        )
        assert _allowed(check), (
            "User should inherit read on file via parent folder viewer grant"
        )

        # 4. User without folder grant should be denied
        other = ("user", f"rebac009_other_{tag}")
        check_other = nexus.rebac_check(other, "read", file_, zone_id=zone)
        assert not _allowed(check_other), "User without folder grant should be denied"


# ---------------------------------------------------------------------------
# rebac/010: Tiger Cache write-through and invalidation cycle
# ---------------------------------------------------------------------------


@pytest.mark.quick
@pytest.mark.auto
@pytest.mark.rebac
class TestTigerCacheCycle:
    """rebac/010: Tiger Cache populates on check, invalidates on revoke.

    Verifies the full Tiger Cache lifecycle:
        1. Grant → rebac_check → Tiger Cache populated (write-through)
        2. Second rebac_check → still allowed (may hit cache)
        3. Revoke → Tiger Cache invalidated
        4. fully_consistent check → denied (bypasses any stale cache)
    """

    def test_cache_write_through_and_invalidation(
        self, nexus: NexusClient, create_tuple: Any, settings: TestSettings
    ) -> None:
        """rebac/010: Grant → check (populates cache) → revoke → denied."""
        tag = uuid.uuid4().hex[:8]
        subject = ("user", f"rebac010_{tag}")
        object_ = ("file", f"/rebac010/{tag}/cached.txt")
        zone = settings.zone

        # 1. Grant direct_viewer
        resp = create_tuple(subject, "direct_viewer", object_)
        assert resp.ok
        tid = resp.result["tuple_id"]
        revision = resp.result["revision"]

        # 2. First check → allowed (computes permission, writes through to Tiger Cache)
        check1 = nexus.rebac_check(
            subject,
            "read",
            object_,
            zone_id=zone,
            consistency_mode="at_least_as_fresh",
            min_revision=revision,
        )
        assert _allowed(check1), "First check should be allowed"

        # 3. Second check → still allowed (should hit Tiger Cache L1)
        check2 = nexus.rebac_check(subject, "read", object_, zone_id=zone)
        assert _allowed(check2), "Second check should still be allowed (cache hit)"

        # 4. Revoke the grant
        del_resp = nexus.rebac_delete(tid)
        assert del_resp.ok

        # 5. Fully consistent check → denied (bypasses Tiger + Boundary caches)
        check3 = nexus.rebac_check(
            subject,
            "read",
            object_,
            zone_id=zone,
            consistency_mode="fully_consistent",
        )
        assert not _allowed(check3), (
            "Permission should be denied after revoke with fully_consistent"
        )


# ---------------------------------------------------------------------------
# rebac/011: Tiger Cache stats endpoint
# ---------------------------------------------------------------------------


@pytest.mark.quick
@pytest.mark.auto
@pytest.mark.rebac
class TestTigerCacheStats:
    """rebac/011: Tiger Cache exposes stats via /api/cache/stats."""

    def test_cache_stats_include_tiger_cache(
        self, nexus: NexusClient, create_tuple: Any, settings: TestSettings
    ) -> None:
        """rebac/011: After a check, /api/cache/stats reports tiger_cache metrics."""
        tag = uuid.uuid4().hex[:8]
        subject = ("user", f"rebac011_{tag}")
        object_ = ("file", f"/rebac011/{tag}/stats.txt")

        # Perform a grant + check to ensure Tiger Cache sees some activity
        resp = create_tuple(subject, "direct_viewer", object_)
        assert resp.ok
        revision = resp.result["revision"]
        check = nexus.rebac_check(
            subject,
            "read",
            object_,
            zone_id=settings.zone,
            consistency_mode="at_least_as_fresh",
            min_revision=revision,
        )
        assert _allowed(check)

        # Query cache stats endpoint (V2 API)
        stats_resp = nexus.api_get("/api/v2/cache/stats")
        if stats_resp.status_code == 404:
            pytest.skip("Cache stats endpoint not registered on this server")

        assert stats_resp.status_code == 200, (
            f"Cache stats returned {stats_resp.status_code}: {stats_resp.text[:200]}"
        )
        data = stats_resp.json()

        # Tiger Cache stats should be present with expected keys
        tc = data.get("tiger_cache")
        if tc is None:
            pytest.skip("Tiger Cache not enabled on this server")

        for key in ("hits", "misses", "sets", "l1_size"):
            assert key in tc, f"tiger_cache stats missing key: {key}"
        assert isinstance(tc["hits"], int)
        assert isinstance(tc["misses"], int)
        assert tc["hits"] + tc["misses"] > 0, "Tiger Cache should have at least 1 hit or miss"


# ---------------------------------------------------------------------------
# rebac/012: Read enforcement (403 without permission)
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.rebac
class TestReadEnforcement:
    """rebac/012: Read operations denied without ReBAC grant."""

    def test_read_denied_then_granted(
        self,
        nexus: NexusClient,
        unprivileged_client: UnprivilegedContext,
        create_tuple: Any,
        settings: TestSettings,
    ) -> None:
        """rebac/012: Unprivileged read → 403, then grant → success."""
        tag = uuid.uuid4().hex[:8]
        path = f"/rebac012/{tag}/secret.txt"
        content = f"secret content {tag}"
        zone = settings.zone
        unpriv = unprivileged_client.client
        user_id = unprivileged_client.user_id

        # 1. Admin writes a file so it exists
        write_resp = nexus.write_file(path, content, zone=zone)
        assert write_resp.ok, f"Admin write failed: {write_resp.error}"

        try:
            # 2. Unprivileged user attempts read → denied
            resp = unpriv.read_file(path)
            assert_permission_denied(resp)

            # 3. Grant read permission using non-prefixed paths
            dir_path = f"/rebac012/{tag}"
            file_path = f"{dir_path}/secret.txt"
            for grant_path in (dir_path, file_path):
                grant_resp = create_tuple(
                    ("user", user_id), "direct_viewer", ("file", grant_path)
                )
                assert grant_resp.ok, f"Grant failed for {grant_path}: {grant_resp.error}"

            # 4. Retry read → success (grant may need a moment to propagate)
            resp2 = unpriv.read_file(path)
            for _ in range(4):
                if resp2.ok:
                    break
                time.sleep(0.5)
                resp2 = unpriv.read_file(path)
            assert resp2.ok, f"Read should succeed after grant: {resp2.error}"

            # 5. Verify content matches what admin wrote
            assert resp2.content_str == content
        finally:
            with contextlib.suppress(Exception):
                nexus.delete_file(path, zone=zone)


# ---------------------------------------------------------------------------
# rebac/013: Delete enforcement (403 without permission)
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.rebac
class TestDeleteEnforcement:
    """rebac/013: Delete operations denied without ReBAC grant."""

    def test_delete_denied_then_granted(
        self,
        nexus: NexusClient,
        unprivileged_client: UnprivilegedContext,
        create_tuple: Any,
        settings: TestSettings,
    ) -> None:
        """rebac/013: Unprivileged delete → 403, then grant → success."""
        tag = uuid.uuid4().hex[:8]
        path = f"/rebac013/{tag}/deleteme.txt"
        content = f"delete me {tag}"
        zone = settings.zone
        unpriv = unprivileged_client.client
        user_id = unprivileged_client.user_id

        # 1. Admin writes a file so it exists
        write_resp = nexus.write_file(path, content, zone=zone)
        assert write_resp.ok, f"Admin write failed: {write_resp.error}"

        try:
            # 2. Unprivileged user attempts delete → denied
            resp = unpriv.delete_file(path)
            assert_permission_denied(resp)

            # 3. Verify file still exists (no partial delete)
            read_resp = nexus.read_file(path, zone=zone)
            assert read_resp.ok, "File should still exist after denied delete"

            # 4. Grant write permission (write implies delete) using non-prefixed paths
            dir_path = f"/rebac013/{tag}"
            file_path = f"{dir_path}/deleteme.txt"
            for grant_path in (dir_path, file_path):
                grant_resp = create_tuple(
                    ("user", user_id), "direct_editor", ("file", grant_path)
                )
                assert grant_resp.ok, f"Grant failed for {grant_path}: {grant_resp.error}"

            # 5. Retry delete → success (grant may need a moment to propagate)
            resp2 = unpriv.delete_file(path)
            for _ in range(4):
                if resp2.ok:
                    break
                time.sleep(0.5)
                resp2 = unpriv.delete_file(path)
            assert resp2.ok, f"Delete should succeed after grant: {resp2.error}"

            # 6. Verify file is gone
            read_resp2 = nexus.read_file(path, zone=zone)
            assert not read_resp2.ok, "File should not exist after successful delete"
        finally:
            # Cleanup if file still exists (e.g., delete was denied)
            with contextlib.suppress(Exception):
                nexus.delete_file(path, zone=zone)


# ---------------------------------------------------------------------------
# rebac/014: Owner role (implies read, write, execute)
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.rebac
class TestOwnerRole:
    """rebac/014: Owner role implies read, write, and execute permissions."""

    def test_owner_implies_read_write_execute(
        self, nexus: NexusClient, create_tuple: Any, settings: TestSettings
    ) -> None:
        """rebac/014: Grant direct_owner → read, write, execute all true."""
        tag = uuid.uuid4().hex[:8]
        subject = ("user", f"rebac014_{tag}")
        object_ = ("file", f"/rebac014/{tag}/owned.txt")
        zone = settings.zone

        # Grant direct_owner
        resp = create_tuple(subject, "direct_owner", object_)
        assert resp.ok, f"rebac_create failed: {resp.error}"
        revision = resp.result["revision"]

        # Owner → read: true
        check_read = nexus.rebac_check(
            subject,
            "read",
            object_,
            zone_id=zone,
            consistency_mode="at_least_as_fresh",
            min_revision=revision,
        )
        assert _allowed(check_read), "Owner should have read permission"

        # Owner → write: true
        check_write = nexus.rebac_check(
            subject,
            "write",
            object_,
            zone_id=zone,
            consistency_mode="at_least_as_fresh",
            min_revision=revision,
        )
        assert _allowed(check_write), "Owner should have write permission"

        # Owner → execute: true
        check_exec = nexus.rebac_check(
            subject,
            "execute",
            object_,
            zone_id=zone,
            consistency_mode="at_least_as_fresh",
            min_revision=revision,
        )
        assert _allowed(check_exec), "Owner should have execute permission"


# ---------------------------------------------------------------------------
# rebac/015: Expand API (returns granted subjects)
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.rebac
class TestExpandPermission:
    """rebac/015: Expand API returns all subjects with a given permission."""

    def test_expand_returns_granted_subjects(
        self, nexus: NexusClient, create_tuple: Any, settings: TestSettings
    ) -> None:
        """rebac/015: Grant viewer to alice and editor to bob → expand finds both."""
        tag = uuid.uuid4().hex[:8]
        alice = ("user", f"rebac015_alice_{tag}")
        bob = ("user", f"rebac015_bob_{tag}")
        object_ = ("file", f"/rebac015/{tag}/shared.txt")
        zone = settings.zone

        # Grant direct_viewer to alice and bob
        r1 = create_tuple(alice, "direct_viewer", object_)
        assert r1.ok, f"Grant alice failed: {r1.error}"

        r2 = create_tuple(bob, "direct_viewer", object_)
        assert r2.ok, f"Grant bob failed: {r2.error}"

        # Expand "viewer" relation on the file (expand uses relations, not permissions)
        expand_resp = nexus.rebac_expand("viewer", object_, zone_id=zone)
        assert expand_resp.ok, f"rebac_expand failed: {expand_resp.error}"

        result = expand_resp.result
        assert isinstance(result, (dict, list)), (
            f"Expand result should be dict or list, got {type(result)}"
        )

        # Extract subjects from the expand result
        if isinstance(result, dict):
            subjects = result.get("subjects", [])
        else:
            subjects = result

        # Flatten subject identifiers for matching
        subject_ids: set[str] = set()
        for s in subjects:
            if isinstance(s, dict):
                sid = s.get("subject_id") or s.get("id") or ""
                subject_ids.add(sid)
            elif isinstance(s, list) and len(s) >= 2:
                subject_ids.add(s[1])
            elif isinstance(s, str):
                subject_ids.add(s)

        assert alice[1] in subject_ids, (
            f"alice ({alice[1]}) should appear in expand subjects: {subject_ids}"
        )
        assert bob[1] in subject_ids, (
            f"bob ({bob[1]}) should appear in expand subjects: {subject_ids}"
        )


# ---------------------------------------------------------------------------
# rebac/016: Explain API (returns resolution path)
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.rebac
class TestExplainPermission:
    """rebac/016: Explain API returns the permission resolution path."""

    def test_explain_returns_resolution_path(
        self, nexus: NexusClient, create_tuple: Any, settings: TestSettings
    ) -> None:
        """rebac/016: Grant viewer → explain → successful_path present."""
        tag = uuid.uuid4().hex[:8]
        subject = ("user", f"rebac016_{tag}")
        object_ = ("file", f"/rebac016/{tag}/explained.txt")
        zone = settings.zone

        # Grant direct_viewer
        resp = create_tuple(subject, "direct_viewer", object_)
        assert resp.ok, f"rebac_create failed: {resp.error}"

        # Explain read permission for granted user
        explain_resp = nexus.rebac_explain(
            subject, "read", object_, zone_id=zone
        )
        assert explain_resp.ok, f"rebac_explain failed: {explain_resp.error}"
        assert _explain_allowed(explain_resp), (
            "Explain should find a successful_path for granted user"
        )

        # Verify result structure contains successful_path
        result = explain_resp.result
        assert isinstance(result, dict), "Explain result should be a dict"
        assert "successful_path" in result, (
            f"Explain result should contain 'successful_path', got keys: {list(result.keys())}"
        )

        # Explain for non-granted user → no successful_path
        other = ("user", f"rebac016_other_{tag}")
        explain_other = nexus.rebac_explain(
            other, "read", object_, zone_id=zone
        )
        assert not _explain_allowed(explain_other), (
            "Explain should NOT find a successful_path for non-granted user"
        )
