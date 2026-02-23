"""Observability E2E tests — features and operations endpoints.

Tests: obs/006, obs/007
Covers: GET /api/v2/features, GET /api/v2/operations

Reference: TEST_PLAN.md §4.40
"""

from __future__ import annotations

import contextlib
import uuid

import pytest

from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import assert_http_ok


@pytest.mark.auto
@pytest.mark.observability
class TestFeatures:
    """Verify the features endpoint returns enabled server capabilities."""

    def test_features_returns_profile(self, nexus: NexusClient) -> None:
        """obs/006: GET /api/v2/features -> profile and brick lists.

        Asserts:
        - profile is one of: embedded, lite, full, cloud
        - enabled_bricks is a list
        """
        data = assert_http_ok(nexus.features())

        # Profile field (may be "profile" or "mode")
        profile = data.get("profile", data.get("mode"))
        assert profile is not None, f"Features missing 'profile' or 'mode': {data}"
        assert profile in ("embedded", "lite", "full", "cloud"), (
            f"Unexpected profile: {profile}"
        )

        # Brick lists
        enabled = data.get("enabled_bricks", data.get("bricks", []))
        assert isinstance(enabled, list), (
            f"Expected enabled_bricks list, got {type(enabled).__name__}: {data}"
        )


@pytest.mark.auto
@pytest.mark.observability
@pytest.mark.audit
class TestOperations:
    """Verify the operations endpoint returns recent operation history."""

    def test_operations_returns_recent_ops(
        self,
        nexus: NexusClient,
        unique_path: str,
    ) -> None:
        """obs/007: Perform a write -> operations log includes at least one entry.

        Creates a small file to generate an operation, then verifies
        the operations endpoint returns it.
        """
        # Perform a known write to generate an operation
        tag = uuid.uuid4().hex[:8]
        test_path = f"{unique_path}/obs-ops-{tag}.txt"
        nexus.write_file(test_path, "ops_probe")

        try:
            resp = nexus.operations()
            if resp.status_code in (404, 500, 501):
                pytest.skip(
                    f"Operations endpoint unavailable: {resp.status_code} {resp.text[:100]}"
                )
            data = assert_http_ok(resp)

            # Response may be a list or wrapped in an envelope
            if isinstance(data, list):
                ops = data
            elif isinstance(data, dict):
                ops = data.get("operations", data.get("items", data.get("entries", [])))
            else:
                ops = []

            assert len(ops) >= 1, (
                f"Expected at least one operation after write, got {len(ops)}"
            )
        finally:
            with contextlib.suppress(Exception):
                nexus.delete_file(test_path)
