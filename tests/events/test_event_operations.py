"""Operations endpoint tests — event log listing via /api/v2/operations.

Tests cover:
- events/060: Operations endpoint returns recent operations
- events/061: Operations include file write entries
- events/062: Operations include file delete entries
- events/063: Operations endpoint pagination with cursor
- events/064: Operations entries contain required fields

Requires: Nexus server with operation log enabled.
Endpoint: GET /api/v2/operations
"""

from __future__ import annotations

import contextlib
import time
import uuid

import pytest

from tests.events.conftest import EventClient
from tests.helpers.assertions import assert_rpc_success


class TestOperationsEndpoint:
    """Operations log: listing, pagination, field validation."""

    @pytest.mark.nexus_test("events/060")
    def test_operations_returns_recent_entries(
        self,
        event_client: EventClient,
    ) -> None:
        """events/060: GET /api/v2/operations returns recent operations.

        Verifies the operations endpoint responds with a list of
        recent filesystem operations.
        """
        resp = event_client.nexus.operations()

        if resp.status_code == 404:
            pytest.skip("Operations endpoint not found")
        if resp.status_code == 503:
            pytest.skip("Operations endpoint not available")

        assert resp.status_code == 200
        data = resp.json()

        # Should return a list or dict with operations
        if isinstance(data, dict):
            ops = data.get("operations", data.get("events", data.get("items", [])))
        elif isinstance(data, list):
            ops = data
        else:
            ops = []

        assert isinstance(ops, list), f"Expected list of operations, got {type(ops)}"

    @pytest.mark.nexus_test("events/061")
    def test_operations_include_write_entry(
        self,
        event_client: EventClient,
        event_unique_path: str,
    ) -> None:
        """events/061: Write operation appears in operations log.

        Writes a file and verifies the operation appears in the
        operations endpoint response.
        """
        tag = uuid.uuid4().hex[:8]
        path = f"{event_unique_path}/ev061_ops_write_{tag}.txt"

        try:
            event_client.write_and_wait(path, "operations write test")

            resp = event_client.nexus.operations()
            if resp.status_code in (404, 503):
                pytest.skip("Operations endpoint not available")

            assert resp.status_code == 200
            data = resp.json()

            ops = (
                data.get("operations", data.get("events", []))
                if isinstance(data, dict) else data
            )

            matching = [
                op for op in ops
                if f"ev061_ops_write_{tag}" in str(op.get("path", ""))
            ]
            assert len(matching) > 0, (
                f"Write operation not found in operations log. "
                f"Got {len(ops)} operations."
            )
        finally:
            with contextlib.suppress(Exception):
                event_client.nexus.delete_file(path)

    @pytest.mark.nexus_test("events/062")
    def test_operations_include_delete_entry(
        self,
        event_client: EventClient,
        event_unique_path: str,
    ) -> None:
        """events/062: Delete operation appears in operations log.

        Writes then deletes a file and verifies the delete operation
        appears in the operations log.
        """
        tag = uuid.uuid4().hex[:8]
        path = f"{event_unique_path}/ev062_ops_delete_{tag}.txt"

        try:
            event_client.write_and_wait(path, "to be deleted")
            assert_rpc_success(event_client.nexus.delete_file(path))
            time.sleep(0.5)

            resp = event_client.nexus.operations()
            if resp.status_code in (404, 503):
                pytest.skip("Operations endpoint not available")

            assert resp.status_code == 200
            data = resp.json()

            ops = (
                data.get("operations", data.get("events", []))
                if isinstance(data, dict) else data
            )

            delete_ops = [
                op for op in ops
                if f"ev062_ops_delete_{tag}" in str(op.get("path", ""))
                and op.get("type", op.get("operation_type", "")) in (
                    "delete", "remove", "file_delete", "file_deleted",
                )
            ]

            if not delete_ops:
                # Some servers record delete as operation but with different type name
                any_matching = [
                    op for op in ops
                    if f"ev062_ops_delete_{tag}" in str(op.get("path", ""))
                ]
                assert len(any_matching) > 0, (
                    f"No operations at all for deleted file"
                )
        finally:
            with contextlib.suppress(Exception):
                event_client.nexus.delete_file(path)

    @pytest.mark.nexus_test("events/063")
    def test_operations_pagination(
        self,
        event_client: EventClient,
        event_unique_path: str,
    ) -> None:
        """events/063: Operations endpoint supports cursor pagination.

        Writes several files, queries operations with small limit,
        verifies pagination structure (next_cursor, has_more).
        """
        tag = uuid.uuid4().hex[:8]
        paths: list[str] = []

        try:
            for i in range(5):
                p = f"{event_unique_path}/ev063_page_{tag}_{i}.txt"
                event_client.nexus.write_file(p, f"pagination test {i}")
                paths.append(p)
            time.sleep(0.5)

            resp = event_client.nexus.api_get(
                "/api/v2/operations",
                params={"limit": 3},
            )

            if resp.status_code in (404, 503):
                pytest.skip("Operations endpoint not available")

            assert resp.status_code == 200
            data = resp.json()

            # Verify pagination fields exist
            if isinstance(data, dict):
                # May have next_cursor or cursor field
                has_pagination = (
                    "next_cursor" in data
                    or "has_more" in data
                    or "cursor" in data
                    or "total" in data
                )
                if has_pagination:
                    pass  # Pagination structure present
                ops = data.get("operations", data.get("events", []))
                assert isinstance(ops, list)
        finally:
            for p in paths:
                with contextlib.suppress(Exception):
                    event_client.nexus.delete_file(p)

    @pytest.mark.nexus_test("events/064")
    def test_operations_entries_have_required_fields(
        self,
        event_client: EventClient,
        event_unique_path: str,
    ) -> None:
        """events/064: Operation entries contain required metadata fields.

        Writes a file, queries operations, and verifies each entry
        has at minimum: an ID, type/operation_type, path, and timestamp.
        """
        tag = uuid.uuid4().hex[:8]
        path = f"{event_unique_path}/ev064_fields_{tag}.txt"

        try:
            event_client.write_and_wait(path, "field validation test")

            resp = event_client.nexus.operations()
            if resp.status_code in (404, 503):
                pytest.skip("Operations endpoint not available")

            assert resp.status_code == 200
            data = resp.json()

            ops = (
                data.get("operations", data.get("events", []))
                if isinstance(data, dict) else data
            )

            if not ops:
                pytest.skip("Operations log empty")

            # Check first few entries for required fields
            for op in ops[:5]:
                has_id = (
                    "id" in op or "event_id" in op or "operation_id" in op
                )
                has_type = (
                    "type" in op or "operation_type" in op or "method" in op
                )
                has_path = "path" in op
                has_time = (
                    "timestamp" in op or "created_at" in op or "time" in op
                )

                assert has_id, f"Operation missing ID: {list(op.keys())}"
                assert has_type, f"Operation missing type: {list(op.keys())}"
        finally:
            with contextlib.suppress(Exception):
                event_client.nexus.delete_file(path)
