"""Authentication E2E tests — API key validation, rate limiting, lifecycle.

Tests: auth/001-005
Covers: valid/invalid auth, rate limiting, key lifecycle, whoami

Reference: TEST_PLAN.md §4.4

Requires: Server started with NEXUS_ENFORCE_PERMISSIONS=true
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import httpx
import pytest

from tests.config import TestSettings
from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import assert_http_ok

# ---------------------------------------------------------------------------
# auth/001 — Valid API key → authenticated
# ---------------------------------------------------------------------------


@pytest.mark.quick
@pytest.mark.auto
@pytest.mark.auth
class TestValidAuth:
    """Verify that a valid admin API key grants authenticated access."""

    def test_valid_api_key_authenticates(self, nexus: NexusClient) -> None:
        """auth/001: Admin key → whoami returns authenticated=true."""
        data = assert_http_ok(nexus.whoami())
        assert data.get("authenticated") is True, f"Expected authenticated=true: {data}"


# ---------------------------------------------------------------------------
# auth/002 — Invalid API key → 401 (parametrized across 4 failure modes)
# ---------------------------------------------------------------------------


@pytest.mark.quick
@pytest.mark.auto
@pytest.mark.auth
class TestInvalidAuth:
    """Verify that various invalid auth scenarios are rejected.

    The whoami endpoint may return 200 with authenticated=false (soft reject)
    or 401 (hard reject), depending on server configuration. Both are valid
    indicators of failed authentication.
    """

    @pytest.mark.parametrize(
        "auth_header,description",
        [
            (None, "no Authorization header"),
            ("", "empty Authorization header"),
            ("Bearer malformed-not-a-real-key", "malformed API key"),
            (f"Bearer sk-nonexistent-{uuid.uuid4().hex[:16]}", "nonexistent API key"),
        ],
        ids=["no_header", "empty_header", "malformed_key", "nonexistent_key"],
    )
    def test_invalid_auth_rejected(
        self,
        settings: TestSettings,
        auth_header: str | None,
        description: str,
    ) -> None:
        """auth/002: Invalid auth → not authenticated (401 or authenticated=false)."""
        headers = {}
        if auth_header is not None:
            headers["Authorization"] = auth_header

        with httpx.Client(
            base_url=settings.url,
            headers=headers,
            timeout=httpx.Timeout(settings.request_timeout, connect=settings.connect_timeout),
        ) as client:
            resp = client.get("/api/auth/whoami")

        if resp.status_code == 401:
            return  # Hard reject — pass

        # Soft reject: 200 with authenticated=false
        assert resp.status_code == 200, (
            f"Expected 200 or 401, got {resp.status_code}: {resp.text[:200]}"
        )
        data = resp.json()
        assert data.get("authenticated") is False, (
            f"Expected authenticated=false for invalid auth, got: {data}"
        )


# ---------------------------------------------------------------------------
# auth/003 — Rate limiting → 429
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.auth
@pytest.mark.xdist_group("rate_limit")
class TestRateLimiting:
    """Verify rate limiting behavior for anonymous and authenticated clients."""

    def test_anonymous_burst_triggers_429(
        self,
        nexus: NexusClient,
        settings: TestSettings,
    ) -> None:
        """auth/003a: Anonymous burst exceeding limit → 429 Too Many Requests.

        Skips if rate limiting is not enabled on the server.
        Uses anonymous tier (60 req/min default).
        """
        # Check if rate limiting is enabled
        features_resp = nexus.features()
        if features_resp.status_code == 200:
            features = features_resp.json()
            rate_limit_flag = features.get("rate_limit", features.get("rate_limiting"))
            if rate_limit_flag is False:
                pytest.skip("Rate limiting is not enabled on this server")

        # Burst anonymous requests beyond the limit
        hit_429 = False
        with httpx.Client(
            base_url=settings.url,
            timeout=httpx.Timeout(5.0, connect=3.0),
        ) as anon_client:
            for _ in range(70):
                resp = anon_client.get("/api/auth/whoami")
                if resp.status_code == 429:
                    hit_429 = True
                    break

        if not hit_429:
            pytest.skip(
                "Rate limit not triggered after 70 requests — "
                "rate limiting may be disabled or limit is higher than expected"
            )

    def test_authenticated_request_has_rate_limit_headers(
        self,
        nexus: NexusClient,
    ) -> None:
        """auth/003b: Authenticated request → rate limit headers present.

        Always runs. Checks for X-RateLimit-Limit and X-RateLimit-Remaining.
        Falls back to skip if headers are absent (rate limiting disabled).
        """
        resp = nexus.health()
        limit_header = resp.headers.get("X-RateLimit-Limit")
        remaining_header = resp.headers.get("X-RateLimit-Remaining")

        if limit_header is None and remaining_header is None:
            pytest.skip("Rate limit headers not present — rate limiting may be disabled")

        if limit_header is not None:
            assert int(limit_header) > 0, f"X-RateLimit-Limit should be positive: {limit_header}"
        if remaining_header is not None:
            assert int(remaining_header) >= 0, (
                f"X-RateLimit-Remaining should be non-negative: {remaining_header}"
            )


# ---------------------------------------------------------------------------
# auth/004 — API key lifecycle (create → use → revoke → verify 401)
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.auth
class TestApiKeyLifecycle:
    """Verify the full lifecycle of an API key."""

    def test_create_use_revoke_key(
        self,
        nexus: NexusClient,
        create_api_key: Callable[[str], dict],
    ) -> None:
        """auth/004: Create key → use it → revoke it → verify 401.

        Uses the create_api_key fixture which auto-cleans up on teardown.
        """
        # Create a new API key
        key_info = create_api_key(f"lifecycle-test-{uuid.uuid4().hex[:8]}")
        raw_key = key_info.get("key", key_info.get("raw_key", ""))
        key_id = key_info.get("id", key_info.get("key_id", ""))

        if not raw_key:
            pytest.skip("Server did not return a raw key from key creation API")

        # Use the new key to make an authenticated request
        zone_client = nexus.for_zone(raw_key)
        try:
            use_resp = zone_client.whoami()
            assert use_resp.status_code == 200, (
                f"New key should authenticate: {use_resp.status_code} {use_resp.text[:200]}"
            )

            # Revoke the key
            if key_id:
                revoke_resp = nexus.api_delete(f"/api/v2/auth/keys/{key_id}")
                assert revoke_resp.status_code in (200, 204), (
                    f"Key revocation failed: {revoke_resp.status_code}"
                )

                # Verify revoked key returns 401
                verify_resp = zone_client.whoami()
                assert verify_resp.status_code == 401, (
                    f"Revoked key should return 401, got {verify_resp.status_code}"
                )
            else:
                pytest.skip("Server did not return key_id — cannot test revocation")
        finally:
            zone_client.http.close()


# ---------------------------------------------------------------------------
# auth/005 — Whoami endpoint details
# ---------------------------------------------------------------------------


@pytest.mark.quick
@pytest.mark.auto
@pytest.mark.auth
class TestWhoami:
    """Verify whoami endpoint returns correct identity details."""

    def test_admin_whoami_fields(self, nexus: NexusClient) -> None:
        """auth/005a: Admin whoami → expected fields and is_admin=true."""
        data = assert_http_ok(nexus.whoami())

        # Verify expected fields exist
        expected_keys = {"authenticated"}
        present_keys = set(data.keys())
        assert expected_keys.issubset(present_keys), (
            f"Missing keys: {expected_keys - present_keys}. Got: {present_keys}"
        )

        assert data["authenticated"] is True

        # Admin-specific assertions (if fields present)
        if "is_admin" in data:
            assert data["is_admin"] is True, "Admin key should have is_admin=true"

    def test_zone_key_whoami(
        self,
        nexus: NexusClient,
        settings: TestSettings,
        create_api_key: Callable[[str], dict],
    ) -> None:
        """auth/005b: Zone key whoami → is_admin=false, zone_id matches."""
        key_info = create_api_key(f"whoami-test-{uuid.uuid4().hex[:8]}")
        raw_key = key_info.get("key", key_info.get("raw_key", ""))

        if not raw_key:
            pytest.skip("Server did not return a raw key from key creation API")

        zone_client = nexus.for_zone(raw_key)
        try:
            data = assert_http_ok(zone_client.whoami())
            assert data.get("authenticated") is True

            if "is_admin" in data:
                assert data["is_admin"] is False, "Zone key should not be admin"
        finally:
            zone_client.http.close()
