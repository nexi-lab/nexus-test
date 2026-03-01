"""OAuth E2E tests — validate OAuth RPC endpoints post brick refactor.

Tests: oauth/001-006
Covers: oauth_list_providers, oauth_list_credentials, oauth_get_auth_url,
        oauth_revoke_credential, oauth_test_credential, RPC discovery

Reference: Refactor that eliminated services/oauth/ wrapper and exposed
OAuthCredentialService directly with @rpc_expose decorators.

Requires: Server started with authentication enabled.
"""

from __future__ import annotations

import pytest

from tests.helpers.api_client import NexusClient


# ---------------------------------------------------------------------------
# oauth/001 — oauth_list_providers returns a list (may be empty without config)
# ---------------------------------------------------------------------------


@pytest.mark.quick
@pytest.mark.auto
@pytest.mark.auth
class TestOAuthListProviders:
    """Verify oauth_list_providers RPC endpoint works."""

    def test_list_providers_returns_list(self, nexus: NexusClient) -> None:
        """oauth/001: oauth_list_providers → returns a list (possibly empty).

        Without OAuth configuration the list is expected to be empty,
        but the endpoint should still respond without errors.
        """
        resp = nexus.rpc("oauth_list_providers")
        assert resp.error is None, f"RPC error: {resp.error}"
        assert isinstance(resp.result, list), (
            f"Expected list, got {type(resp.result)}: {resp.result}"
        )


# ---------------------------------------------------------------------------
# oauth/002 — oauth_list_credentials returns a list
# ---------------------------------------------------------------------------


@pytest.mark.quick
@pytest.mark.auto
@pytest.mark.auth
class TestOAuthListCredentials:
    """Verify oauth_list_credentials RPC endpoint works."""

    def test_list_credentials_returns_list(self, nexus: NexusClient) -> None:
        """oauth/002: oauth_list_credentials → returns a list.

        Without any stored credentials this should return an empty list.
        """
        resp = nexus.rpc("oauth_list_credentials")
        assert resp.error is None, f"RPC error: {resp.error}"
        assert isinstance(resp.result, list), (
            f"Expected list, got {type(resp.result)}: {resp.result}"
        )

    def test_list_credentials_with_provider_filter(self, nexus: NexusClient) -> None:
        """oauth/002b: oauth_list_credentials with provider filter → returns list."""
        resp = nexus.rpc(
            "oauth_list_credentials",
            {"provider": "github"},
        )
        assert resp.error is None, f"RPC error: {resp.error}"
        assert isinstance(resp.result, list)


# ---------------------------------------------------------------------------
# oauth/003 — oauth_get_auth_url validation
# ---------------------------------------------------------------------------


@pytest.mark.quick
@pytest.mark.auto
@pytest.mark.auth
class TestOAuthGetAuthUrl:
    """Verify oauth_get_auth_url RPC endpoint validates inputs."""

    def test_get_auth_url_unknown_provider(self, nexus: NexusClient) -> None:
        """oauth/003: oauth_get_auth_url with unknown provider → error.

        Without OAuth config, any provider name should result in an error
        (provider not found / not configured).
        """
        resp = nexus.rpc(
            "oauth_get_auth_url",
            {"provider": "nonexistent-provider-xyz"},
        )
        # Should get an error (provider not found)
        assert resp.error is not None, (
            f"Expected error for unknown provider, got: {resp.result}"
        )


# ---------------------------------------------------------------------------
# oauth/004 — oauth_revoke_credential validation
# ---------------------------------------------------------------------------


@pytest.mark.quick
@pytest.mark.auto
@pytest.mark.auth
class TestOAuthRevokeCredential:
    """Verify oauth_revoke_credential RPC endpoint validates inputs."""

    def test_revoke_nonexistent_credential(self, nexus: NexusClient) -> None:
        """oauth/004: oauth_revoke_credential for nonexistent → error.

        Revoking a credential that doesn't exist should return an error
        (not found or not configured).
        """
        resp = nexus.rpc(
            "oauth_revoke_credential",
            {"provider": "github", "user_email": "nobody@example.com"},
        )
        # Should get an error (not found or provider not configured)
        assert resp.error is not None, (
            f"Expected error for nonexistent credential, got: {resp.result}"
        )


# ---------------------------------------------------------------------------
# oauth/005 — oauth_test_credential validation
# ---------------------------------------------------------------------------


@pytest.mark.quick
@pytest.mark.auto
@pytest.mark.auth
class TestOAuthTestCredential:
    """Verify oauth_test_credential RPC endpoint validates inputs."""

    def test_test_nonexistent_credential(self, nexus: NexusClient) -> None:
        """oauth/005: oauth_test_credential for nonexistent → valid=False.

        Testing a credential that doesn't exist should return valid=False
        (the method returns a result dict, not an RPC error).
        """
        resp = nexus.rpc(
            "oauth_test_credential",
            {"provider": "github", "user_email": "nobody@example.com"},
        )
        if resp.error is not None:
            # RPC-level error is also acceptable (provider not configured)
            return

        # Method returned a result — it should indicate invalid
        assert isinstance(resp.result, dict), f"Expected dict result, got: {resp.result}"
        assert resp.result.get("valid") is False, (
            f"Expected valid=False for nonexistent credential, got: {resp.result}"
        )


# ---------------------------------------------------------------------------
# oauth/006 — RPC discovery: all 6 OAuth methods are discoverable
# ---------------------------------------------------------------------------


@pytest.mark.quick
@pytest.mark.auto
@pytest.mark.auth
class TestOAuthRpcDiscovery:
    """Verify all 6 OAuth RPC methods are discoverable on the server."""

    EXPECTED_METHODS = [
        "oauth_list_providers",
        "oauth_get_auth_url",
        "oauth_exchange_code",
        "oauth_list_credentials",
        "oauth_revoke_credential",
        "oauth_test_credential",
    ]

    def test_oauth_methods_in_rpc_list(self, nexus: NexusClient) -> None:
        """oauth/006: All 6 OAuth methods present in rpc_list.

        The @rpc_expose decorators on OAuthCredentialService should make
        all methods discoverable via the RPC listing endpoint.
        """
        resp = nexus.rpc("rpc_list")
        if resp.error is not None:
            pytest.skip(f"rpc_list not available: {resp.error}")

        # rpc_list returns a list of method descriptors
        result = resp.result
        if isinstance(result, dict):
            method_names = set(result.keys())
        elif isinstance(result, list):
            method_names = {
                m.get("name", m) if isinstance(m, dict) else str(m)
                for m in result
            }
        else:
            pytest.fail(f"Unexpected rpc_list result type: {type(result)}")

        missing = [m for m in self.EXPECTED_METHODS if m not in method_names]
        assert not missing, (
            f"Missing OAuth RPC methods: {missing}. "
            f"Available methods containing 'oauth': "
            f"{[m for m in method_names if 'oauth' in m.lower()]}"
        )
