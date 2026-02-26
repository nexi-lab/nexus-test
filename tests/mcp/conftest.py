"""MCP module conftest — fixtures for MCP E2E tests.

Provides:
    - MCPClient: Thin wrapper around NexusClient for MCP JSON-RPC calls
    - mcp: Parametrized MCPClient fixture (local port 10100, remote port 10101)
    - mcp_server_url: Direct URL to the standalone MCP HTTP server (port 10102)
    - mcp_test_mount: Temporary MCP mount with cleanup

Ports (configurable via env):
    NEXUS_TEST_MCP_LOCAL_PORT   = 10100  (local nexus instance)
    NEXUS_TEST_MCP_REMOTE_PORT  = 10101  (remote nexus instance)
    NEXUS_TEST_MCP_SERVER_PORT  = 10102  (standalone MCP HTTP server)
"""

from __future__ import annotations

import contextlib
import os
import uuid
from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from tests.config import TestSettings
from tests.helpers.api_client import NexusClient, RpcResponse


# ---------------------------------------------------------------------------
# MCPClient — thin wrapper for MCP JSON-RPC methods
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MCPClient:
    """MCP API client wrapping NexusClient JSON-RPC calls.

    All methods return RpcResponse for flexible assertion.
    MCP methods are exposed via JSON-RPC at /api/nfs/{method}.
    """

    nexus: NexusClient

    # --- Mount management ---

    def mcp_list_mounts(
        self,
        *,
        tier: str | None = None,
        include_unmounted: bool = True,
    ) -> RpcResponse:
        """List MCP server mounts."""
        params: dict[str, Any] = {"include_unmounted": include_unmounted}
        if tier is not None:
            params["tier"] = tier
        return self.nexus.rpc("mcp_list_mounts", params)

    def mcp_list_tools(self, name: str) -> RpcResponse:
        """List tools from a specific MCP mount."""
        return self.nexus.rpc("mcp_list_tools", {"name": name})

    def mcp_mount(
        self,
        name: str,
        *,
        transport: str | None = None,
        command: str | None = None,
        url: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        description: str | None = None,
        tier: str | None = None,
    ) -> RpcResponse:
        """Mount an MCP server."""
        params: dict[str, Any] = {"name": name}
        if tier is not None:
            params["tier"] = tier
        if transport is not None:
            params["transport"] = transport
        if command is not None:
            params["command"] = command
        if url is not None:
            params["url"] = url
        if args is not None:
            params["args"] = args
        if env is not None:
            params["env"] = env
        if headers is not None:
            params["headers"] = headers
        if description is not None:
            params["description"] = description
        return self.nexus.rpc("mcp_mount", params)

    def mcp_unmount(self, name: str) -> RpcResponse:
        """Unmount an MCP server."""
        return self.nexus.rpc("mcp_unmount", {"name": name})

    def mcp_sync(self, name: str) -> RpcResponse:
        """Sync/refresh tools from an MCP server."""
        return self.nexus.rpc("mcp_sync", {"name": name})

    # --- Convenience: features check ---

    def features(self) -> httpx.Response:
        """GET /api/v2/features — enabled server features."""
        return self.nexus.features()

    def health(self) -> httpx.Response:
        """GET /health — basic health status."""
        return self.nexus.health()

    def health_detailed(self) -> httpx.Response:
        """GET /health/detailed — per-component health."""
        return self.nexus.health_detailed()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_mcp_client(
    base_url: str, api_key: str, *, timeout: float = 120.0
) -> tuple[httpx.Client, MCPClient]:
    """Create an httpx.Client + MCPClient for a given base URL."""
    http = httpx.Client(
        base_url=base_url,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=httpx.Timeout(timeout, connect=10.0),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    )
    nexus = NexusClient(http=http, base_url=base_url, api_key=api_key)
    return http, MCPClient(nexus=nexus)


# ---------------------------------------------------------------------------
# Port fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def _mcp_local_port() -> int:
    return int(os.getenv("NEXUS_TEST_MCP_LOCAL_PORT", "10100"))


@pytest.fixture(scope="session")
def _mcp_remote_port() -> int:
    return int(os.getenv("NEXUS_TEST_MCP_REMOTE_PORT", "10101"))


@pytest.fixture(scope="session")
def _mcp_server_port() -> int:
    return int(os.getenv("NEXUS_TEST_MCP_SERVER_PORT", "10102"))


# ---------------------------------------------------------------------------
# Parametrized MCPClient fixture
# ---------------------------------------------------------------------------


def _mcp_params() -> list[str]:
    """Build param list: always include 'local'; add 'remote' only when its
    port env-var is explicitly set so we don't pollute the report with
    expected skips when no remote server is running."""
    params = ["local"]
    if os.getenv("NEXUS_TEST_MCP_REMOTE_PORT"):
        params.append("remote")
    return params


@pytest.fixture(
    scope="session",
    params=_mcp_params(),
)
def mcp(
    request: pytest.FixtureRequest,
    settings: TestSettings,
    _mcp_local_port: int,
    _mcp_remote_port: int,
) -> Generator[MCPClient]:
    """Session-scoped MCPClient parametrized for local (and optionally remote).

    - local:  http://localhost:{NEXUS_TEST_MCP_LOCAL_PORT}  (default 10100)
    - remote: http://localhost:{NEXUS_TEST_MCP_REMOTE_PORT} (only when env-var set)

    Skips automatically if the target server is unreachable.
    """
    if request.param == "local":
        base_url = f"http://localhost:{_mcp_local_port}"
    else:
        base_url = f"http://localhost:{_mcp_remote_port}"

    http, client = _build_mcp_client(
        base_url,
        settings.api_key,
        timeout=settings.request_timeout,
    )

    # Health check — skip if server is not reachable
    try:
        resp = http.get("/health", timeout=5.0)
        resp.raise_for_status()
    except Exception as exc:
        http.close()
        pytest.skip(
            f"MCP nexus server not reachable at {base_url} ({request.param}): {exc}"
        )

    yield client

    http.close()


# ---------------------------------------------------------------------------
# Standalone MCP HTTP server fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def mcp_server_url(_mcp_server_port: int) -> str:
    """URL for the standalone MCP HTTP server (FastMCP).

    Default: http://localhost:10102
    Override via NEXUS_TEST_MCP_SERVER_PORT env var.
    """
    return f"http://localhost:{_mcp_server_port}"


@pytest.fixture(scope="module")
def _mcp_server_available(mcp_server_url: str) -> None:
    """Skip MCP server tests if the standalone MCP server is not reachable.

    FastMCP SSE servers expose /sse (not /health), so we probe that endpoint.
    SSE is a streaming response, so we use a short read timeout and stream mode
    to avoid hanging — we just need the initial response headers (200 OK).
    """
    try:
        with httpx.stream(
            "GET",
            f"{mcp_server_url}/sse",
            timeout=httpx.Timeout(5.0, connect=3.0, read=2.0),
        ) as resp:
            if resp.status_code != 200:
                pytest.skip(f"MCP server not reachable at {mcp_server_url}: HTTP {resp.status_code}")
    except Exception as exc:
        pytest.skip(f"MCP server not reachable at {mcp_server_url}: {exc}")


# ---------------------------------------------------------------------------
# MCP availability gate (via features endpoint)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _mcp_available(mcp: MCPClient) -> None:
    """Skip MCP tests if MCP functionality is not available.

    Probes the features endpoint and checks for mcp brick, then
    falls back to probing mcp_list_mounts directly.
    """
    try:
        features_resp = mcp.features()
        if features_resp.status_code == 200:
            data = features_resp.json()
            features = data if isinstance(data, dict) else {}
            bricks = features.get("bricks", features.get("features", {}))
            if isinstance(bricks, dict) and bricks.get("mcp") is False:
                pytest.skip("MCP brick is disabled on this server")
            return
    except Exception:
        pass

    # Fallback: probe mcp_list_mounts
    resp = mcp.mcp_list_mounts()
    if not resp.ok:
        error_msg = resp.error.message.lower() if resp.error else ""
        if any(kw in error_msg for kw in ("not found", "not available", "disabled", "unknown method")):
            pytest.skip(f"MCP not available: {resp.error.message}")


# ---------------------------------------------------------------------------
# Test mount fixture (temporary MCP mount with cleanup)
# ---------------------------------------------------------------------------


@pytest.fixture
def make_mcp_mount(
    mcp: MCPClient,
) -> Generator[Callable[..., RpcResponse]]:
    """Factory fixture: mount an MCP server and auto-unmount after test.

    Usage:
        def test_something(make_mcp_mount):
            resp = make_mcp_mount("my-server", command="echo hello")
            assert resp.ok
    """
    mounted_names: list[str] = []

    def _mount(
        name: str | None = None,
        *,
        command: str | None = None,
        url: str | None = None,
        **kwargs: Any,
    ) -> RpcResponse:
        mount_name = name or f"test-mount-{uuid.uuid4().hex[:8]}"
        resp = mcp.mcp_mount(mount_name, command=command, url=url, **kwargs)
        if resp.ok:
            mounted_names.append(mount_name)
        return resp

    yield _mount

    # Cleanup: unmount all mounts created during this test
    for mount_name in reversed(mounted_names):
        with contextlib.suppress(Exception):
            mcp.mcp_unmount(mount_name)
