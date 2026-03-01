"""Versioning E2E test fixtures.

Provides convenience wrappers for version-related RPC calls.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.helpers.api_client import NexusClient, RpcResponse


@pytest.fixture
def version_client(nexus: NexusClient) -> VersionClient:
    """Versioning API facade."""
    return VersionClient(nexus)


class VersionClient:
    """Convenience wrapper for versioning RPC calls."""

    def __init__(self, nexus: NexusClient) -> None:
        self._nx = nexus

    # --- File operations (delegates to NexusClient) ---

    def write(self, path: str, content: str) -> RpcResponse:
        return self._nx.write_file(path, content)

    def read(self, path: str) -> RpcResponse:
        return self._nx.read_file(path)

    def delete(self, path: str) -> RpcResponse:
        return self._nx.delete_file(path)

    def rename(self, old: str, new: str) -> RpcResponse:
        return self._nx.rename(old, new)

    # --- Version RPC methods ---

    def list_versions(self, path: str) -> RpcResponse:
        return self._nx.rpc("list_versions", {"path": path})

    def get_version(self, path: str, version: int) -> RpcResponse:
        return self._nx.rpc("get_version", {"path": path, "version": version})

    def rollback(self, path: str, version: int) -> RpcResponse:
        return self._nx.rpc("rollback", {"path": path, "version": version})

    def diff_versions(
        self, path: str, v1: int, v2: int, mode: str = "metadata"
    ) -> RpcResponse:
        return self._nx.rpc(
            "diff_versions", {"path": path, "v1": v1, "v2": v2, "mode": mode}
        )

    # --- Operations REST API ---

    def list_operations(self, **params: Any) -> dict:
        resp = self._nx.http.get("/api/v2/operations", params=params)
        resp.raise_for_status()
        return resp.json()

    def agent_activity(self, agent_id: str, **params: Any) -> dict:
        resp = self._nx.http.get(
            f"/api/v2/operations/agents/{agent_id}/activity", params=params
        )
        resp.raise_for_status()
        return resp.json()
