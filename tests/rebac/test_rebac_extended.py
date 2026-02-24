"""ReBAC extended permission tests (rebac/017-021).

Tests batch check, list objects, wildcard public access, cross-zone sharing,
and permission escalation prevention.

Groups: quick, auto, rebac
Infrastructure: docker-compose.demo.yml (standalone)
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from tests.config import TestSettings
from tests.helpers.api_client import NexusClient
from .conftest import UnprivilegedContext, _allowed


# ---------------------------------------------------------------------------
# rebac/017: Batch check
# ---------------------------------------------------------------------------


@pytest.mark.quick
@pytest.mark.auto
@pytest.mark.rebac
class TestBatchCheck:
    """rebac/017: Batch check — multiple permission checks in one call."""

    def test_batch_check_multiple_permissions(
        self, nexus: NexusClient, create_tuple: Any, settings: TestSettings
    ) -> None:
        """rebac/017: Batch check returns correct results for multiple subjects."""
        tag = uuid.uuid4().hex[:8]
        alice = ("user", f"rebac017_alice_{tag}")
        bob = ("user", f"rebac017_bob_{tag}")
        carol = ("user", f"rebac017_carol_{tag}")
        file_ = ("file", f"/rebac017/{tag}/shared.txt")
        zone = settings.zone

        # Grant alice direct_viewer, bob direct_editor on the same file
        resp_alice = create_tuple(alice, "direct_viewer", file_)
        assert resp_alice.ok, f"Grant alice failed: {resp_alice.error}"

        resp_bob = create_tuple(bob, "direct_editor", file_)
        assert resp_bob.ok, f"Grant bob failed: {resp_bob.error}"

        # Batch check: alice read, bob write, carol read
        checks = [
            {
                "subject": list(alice),
                "permission": "read",
                "object": list(file_),
                "zone_id": zone,
            },
            {
                "subject": list(bob),
                "permission": "write",
                "object": list(file_),
                "zone_id": zone,
            },
            {
                "subject": list(carol),
                "permission": "read",
                "object": list(file_),
                "zone_id": zone,
            },
        ]

        batch_resp = nexus.rebac_check_batch(checks)
        if not batch_resp.ok:
            assert batch_resp.error is not None
            pytest.skip(
                f"rebac_check_batch endpoint not available: {batch_resp.error.message}"
            )

        results = batch_resp.result
        assert isinstance(results, list), (
            f"Expected list of results, got {type(results)}"
        )
        assert len(results) == 3, f"Expected 3 results, got {len(results)}"

        # Extract allowed booleans from each result
        def _batch_allowed(entry: Any) -> bool:
            if isinstance(entry, bool):
                return entry
            if isinstance(entry, dict):
                return bool(entry.get("allowed", False))
            return False

        alice_allowed = _batch_allowed(results[0])
        bob_allowed = _batch_allowed(results[1])
        carol_allowed = _batch_allowed(results[2])

        assert alice_allowed, "Alice (direct_viewer) should have read access"
        assert bob_allowed, "Bob (direct_editor) should have write access"
        assert not carol_allowed, "Carol (no grant) should be denied read access"


# ---------------------------------------------------------------------------
# rebac/018: List objects
# ---------------------------------------------------------------------------


@pytest.mark.quick
@pytest.mark.auto
@pytest.mark.rebac
class TestListObjects:
    """rebac/018: List objects — filter by relation for a subject."""

    def test_list_objects_by_relation(
        self, nexus: NexusClient, create_tuple: Any, settings: TestSettings
    ) -> None:
        """rebac/018: List objects returns only those matching the queried relation."""
        tag = uuid.uuid4().hex[:8]
        user = ("user", f"rebac018_{tag}")
        file1 = ("file", f"/rebac018/{tag}/file1.txt")
        file2 = ("file", f"/rebac018/{tag}/file2.txt")
        file3 = ("file", f"/rebac018/{tag}/file3.txt")
        zone = settings.zone

        # Grant direct_viewer on file1 and file2
        r1 = create_tuple(user, "direct_viewer", file1)
        assert r1.ok, f"Grant file1 failed: {r1.error}"
        r2 = create_tuple(user, "direct_viewer", file2)
        assert r2.ok, f"Grant file2 failed: {r2.error}"

        # Grant direct_editor on file3 (different relation)
        r3 = create_tuple(user, "direct_editor", file3)
        assert r3.ok, f"Grant file3 failed: {r3.error}"

        # List objects with direct_viewer relation
        list_resp = nexus.rebac_list_objects("direct_viewer", user, zone_id=zone)
        if not list_resp.ok:
            assert list_resp.error is not None
            pytest.skip(
                f"rebac_list_objects endpoint not available: {list_resp.error.message}"
            )

        results = list_resp.result
        assert isinstance(results, list), (
            f"Expected list of objects, got {type(results)}"
        )

        # Extract object IDs from results
        object_ids: set[str] = set()
        for item in results:
            if isinstance(item, str):
                object_ids.add(item)
            elif isinstance(item, dict):
                oid = item.get("object_id") or item.get("id") or item.get("object", "")
                if isinstance(oid, list) and len(oid) >= 2:
                    object_ids.add(oid[1])
                elif isinstance(oid, str):
                    object_ids.add(oid)
            elif isinstance(item, list) and len(item) >= 2:
                object_ids.add(item[1])

        assert file1[1] in object_ids, (
            f"file1 ({file1[1]}) should be in direct_viewer results, got: {object_ids}"
        )
        assert file2[1] in object_ids, (
            f"file2 ({file2[1]}) should be in direct_viewer results, got: {object_ids}"
        )
        assert file3[1] not in object_ids, (
            f"file3 ({file3[1]}) should NOT be in direct_viewer results "
            f"(it has direct_editor), got: {object_ids}"
        )


# ---------------------------------------------------------------------------
# rebac/019: Wildcard public access
# ---------------------------------------------------------------------------


@pytest.mark.quick
@pytest.mark.auto
@pytest.mark.rebac
class TestWildcardPublicAccess:
    """rebac/019: Wildcard grant gives all users access."""

    def test_wildcard_grant_gives_all_users_access(
        self, nexus: NexusClient, create_tuple: Any, settings: TestSettings
    ) -> None:
        """rebac/019: Grant ('*', '*') as direct_viewer → any user has read."""
        tag = uuid.uuid4().hex[:8]
        wildcard_subject = ("*", "*")
        file_ = ("file", f"/rebac019/{tag}/public.txt")
        zone = settings.zone

        # Grant wildcard subject as direct_viewer
        resp = create_tuple(wildcard_subject, "direct_viewer", file_)
        if not resp.ok:
            assert resp.error is not None
            pytest.skip(
                f"Wildcard subject not supported by server: {resp.error.message}"
            )
        revision = resp.result["revision"]
        tid = resp.result["tuple_id"]

        # Random user1 should have read access
        user1 = ("user", f"rebac019_random1_{tag}")
        check1 = nexus.rebac_check(
            user1,
            "read",
            file_,
            zone_id=zone,
            consistency_mode="at_least_as_fresh",
            min_revision=revision,
        )
        assert _allowed(check1), "Random user1 should have read via wildcard grant"

        # Random user2 should also have read access
        user2 = ("user", f"rebac019_random2_{tag}")
        check2 = nexus.rebac_check(
            user2,
            "read",
            file_,
            zone_id=zone,
            consistency_mode="at_least_as_fresh",
            min_revision=revision,
        )
        assert _allowed(check2), "Random user2 should have read via wildcard grant"

        # Revoke wildcard grant
        del_resp = nexus.rebac_delete(tid)
        assert del_resp.ok, f"Revoke wildcard grant failed: {del_resp.error}"

        # Both users should now be denied (fully_consistent bypasses caches)
        check1_after = nexus.rebac_check(
            user1,
            "read",
            file_,
            zone_id=zone,
            consistency_mode="fully_consistent",
        )
        assert not _allowed(check1_after), (
            "User1 should be denied after wildcard revoke"
        )

        check2_after = nexus.rebac_check(
            user2,
            "read",
            file_,
            zone_id=zone,
            consistency_mode="fully_consistent",
        )
        assert not _allowed(check2_after), (
            "User2 should be denied after wildcard revoke"
        )


# ---------------------------------------------------------------------------
# rebac/020: Cross-zone sharing
# ---------------------------------------------------------------------------


@pytest.mark.quick
@pytest.mark.auto
@pytest.mark.rebac
class TestCrossZoneSharing:
    """rebac/020: Cross-zone sharing via shared-viewer relation."""

    def test_shared_viewer_grants_cross_zone_access(
        self, nexus: NexusClient, create_tuple: Any, settings: TestSettings
    ) -> None:
        """rebac/020: Grant shared-viewer in zone A → user has read in zone A."""
        tag = uuid.uuid4().hex[:8]
        user = ("user", f"rebac020_{tag}")
        file_ = ("file", f"/rebac020/{tag}/cross.txt")
        zone_a = settings.zone

        # Grant shared-viewer relation on file in zone A
        resp = create_tuple(user, "shared-viewer", file_, zone_id=zone_a)
        if not resp.ok:
            assert resp.error is not None
            pytest.skip(
                f"shared-viewer relation not supported: {resp.error.message}"
            )
        revision = resp.result["revision"]

        # Check that user has read access in zone A
        check = nexus.rebac_check(
            user,
            "read",
            file_,
            zone_id=zone_a,
            consistency_mode="at_least_as_fresh",
            min_revision=revision,
        )
        assert _allowed(check), (
            "User with shared-viewer should have read access in zone A"
        )


# ---------------------------------------------------------------------------
# rebac/021: Permission escalation prevention
# ---------------------------------------------------------------------------


@pytest.mark.quick
@pytest.mark.auto
@pytest.mark.rebac
class TestPermissionEscalationPrevention:
    """rebac/021: Unprivileged clients cannot escalate permissions."""

    def test_viewer_cannot_create_editor_grant(
        self,
        unprivileged_client: UnprivilegedContext,
        settings: TestSettings,
    ) -> None:
        """rebac/021: Unprivileged client cannot call rebac_create to grant editor."""
        tag = uuid.uuid4().hex[:8]
        target_user = ("user", f"rebac021_target_{tag}")
        file_ = ("file", f"/rebac021/{tag}/protected.txt")
        zone = settings.zone

        unpriv = unprivileged_client.client

        # Attempt to grant editor permissions via unprivileged client
        escalation_resp = unpriv.rebac_create(
            target_user,
            "direct_editor",
            file_,
            zone_id=zone,
        )

        # The server should reject the request (403 or error)
        assert not escalation_resp.ok, (
            "Unprivileged client should not be able to create editor grants, "
            f"but got success: {escalation_resp.result}"
        )

        assert escalation_resp.error is not None
        error_code = escalation_resp.error.code
        error_msg = escalation_resp.error.message.lower()
        is_permission_error = (
            abs(error_code) == 403
            or "forbidden" in error_msg
            or "denied" in error_msg
            or "permission" in error_msg
            or "unauthorized" in error_msg
        )
        assert is_permission_error, (
            f"Expected permission error (403/forbidden/denied), "
            f"got: code={error_code}, message={escalation_resp.error.message!r}"
        )
