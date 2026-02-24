"""ReBAC test fixtures — permission tuple lifecycle, enforcement gate, helpers.

Fixture scoping:
    module:  _rebac_enforcement_required, unprivileged_client
    function: create_tuple (factory with per-test cleanup)
"""

from __future__ import annotations

import contextlib
import logging
import os
import uuid
from collections.abc import Callable, Generator
from dataclasses import dataclass

import httpx
import pytest

from tests.config import TestSettings
from tests.helpers.api_client import NexusClient, RpcResponse
from tests.helpers.zone_keys import create_zone_key

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-scoped enforcement gate (Decision #2A)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _rebac_enforcement_required(nexus: NexusClient) -> None:
    """Skip entire rebac module if the server doesn't enforce permissions."""
    # Check /health first (cheaper)
    health_resp = nexus.health()
    if health_resp.status_code == 200:
        data = health_resp.json()
        if data.get("enforce_permissions") is False:
            pytest.skip("Server has enforce_permissions=false")

    # Also check /api/v2/features if available
    try:
        feat_resp = nexus.features()
        if feat_resp.status_code == 200:
            feat = feat_resp.json()
            if "permissions" not in feat.get("enabled_bricks", []):
                pytest.skip("Server does not have permissions brick enabled")
    except (httpx.HTTPError, KeyError) as exc:
        logger.debug("Features endpoint unavailable (%s), proceeding optimistically", exc)


# ---------------------------------------------------------------------------
# Helper: extract boolean from rebac_check result (Decision #5A)
# ---------------------------------------------------------------------------


def _allowed(resp: RpcResponse) -> bool:
    """Extract the allowed boolean from a rebac_check response.

    Handles both direct bool results and dict results with 'allowed' key.
    Returns False if the response is an error.
    """
    if not resp.ok:
        return False
    if isinstance(resp.result, bool):
        return resp.result
    if isinstance(resp.result, dict):
        return bool(resp.result.get("allowed", False))
    return False


def _explain_allowed(resp: RpcResponse) -> bool:
    """Extract permission result from a rebac_explain response.

    Uses the explain endpoint's traced path (Python engine) rather than
    the cached top-level result. This correctly resolves tupleToUserset
    patterns including group inheritance, which the Rust-accelerated
    rebac_check may not resolve.

    Returns True only if the explain trace found a successful permission path.
    """
    if not resp.ok:
        return False
    if not isinstance(resp.result, dict):
        return False
    successful_path = resp.result.get("successful_path")
    return successful_path is not None and bool(successful_path.get("granted"))


# ---------------------------------------------------------------------------
# Unprivileged client context (avoids monkey-patching NexusClient)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UnprivilegedContext:
    """Zone-scoped client with associated user_id for test access."""

    client: NexusClient
    user_id: str


# ---------------------------------------------------------------------------
# Function-scoped tuple factory with cleanup (Decision #6A, #13A)
# ---------------------------------------------------------------------------

CreateTupleFn = Callable[..., RpcResponse]


@pytest.fixture
def create_tuple(
    nexus: NexusClient, settings: TestSettings
) -> Generator[CreateTupleFn, None, None]:
    """Factory fixture: create ReBAC tuples via RPC with teardown cleanup.

    Usage:
        resp = create_tuple(("user", "alice"), "direct_viewer", ("file", "/doc.txt"))
        assert resp.ok

    All tuples created through this factory are automatically deleted after the test.
    """
    created_tuple_ids: list[str] = []

    def _create(
        subject: tuple[str, str] | list[str],
        relation: str,
        object_: tuple[str, str] | list[str],
        *,
        zone_id: str | None = None,
        expires_at: str | None = None,
    ) -> RpcResponse:
        resp = nexus.rebac_create(
            subject,
            relation,
            object_,
            zone_id=zone_id or settings.zone,
            expires_at=expires_at,
        )
        if resp.ok and resp.result:
            tid = resp.result.get("tuple_id")
            if tid:
                created_tuple_ids.append(tid)
        return resp

    yield _create

    # Teardown: delete all tuples created during this test
    for tid in reversed(created_tuple_ids):
        with contextlib.suppress(Exception):
            nexus.rebac_delete(tid)


# ---------------------------------------------------------------------------
# Module-scoped unprivileged client (Decision #3B, #13A)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def unprivileged_client(
    nexus: NexusClient, settings: TestSettings
) -> Generator[UnprivilegedContext, None, None]:
    """Zone-scoped client with valid auth but ZERO ReBAC grants.

    Used by rebac/006 (write enforcement) to verify that operations
    are denied without explicit permission.
    """
    # Ensure database URL is available for direct key creation fallback.
    # Save and restore to avoid polluting other test modules.
    original_db_url = os.environ.get("NEXUS_DATABASE_URL")
    if not original_db_url and not os.environ.get("NEXUS_TEST_DATABASE_URL"):
        os.environ["NEXUS_DATABASE_URL"] = settings.database_url

    try:
        user_id = f"unpriv-{uuid.uuid4().hex[:8]}"
        raw_key = create_zone_key(
            nexus,
            settings.zone,
            name=f"rebac-unpriv-{uuid.uuid4().hex[:8]}",
            user_id=user_id,
        )
        client = nexus.for_zone(raw_key)
        yield UnprivilegedContext(client=client, user_id=user_id)
        client.http.close()
    finally:
        # Restore original env state
        if original_db_url is None:
            os.environ.pop("NEXUS_DATABASE_URL", None)
        else:
            os.environ["NEXUS_DATABASE_URL"] = original_db_url
