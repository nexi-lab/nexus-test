"""Nexus API client facade for E2E tests.

Provides three access layers:
    - RPC: JSON-RPC calls to /api/nfs/{method} (kernel file operations)
    - REST: HTTP calls to /api/v2/* (service/brick endpoints)
    - CLI: subprocess calls to the `nexus` CLI binary

All methods return immutable Pydantic models or httpx.Response objects.
No mutation of shared state — each call produces a new response.
"""

from __future__ import annotations

import base64
import subprocess
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Response models (immutable)
# ---------------------------------------------------------------------------


class RpcError(BaseModel):
    """JSON-RPC error object."""

    code: int
    message: str
    data: Any | None = None


class RpcResponse(BaseModel):
    """JSON-RPC 2.0 response envelope."""

    jsonrpc: str = "2.0"
    id: int | str | None = None
    result: Any | None = None
    error: RpcError | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def content_str(self) -> str:
        """Decode the result as a string.

        Handles the Nexus convention where file content is returned as:
            {"__type__": "bytes", "data": "<base64-encoded>"}
        or as a plain string.
        """
        if self.result is None:
            return ""
        if isinstance(self.result, str):
            return self.result
        if isinstance(self.result, dict):
            if self.result.get("__type__") == "bytes":
                return base64.b64decode(self.result["data"]).decode("utf-8")
            return self.result.get("content", "")
        return str(self.result)


@dataclass(frozen=True)
class CliResult:
    """Result from a CLI subprocess invocation."""

    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


# ---------------------------------------------------------------------------
# NexusClient
# ---------------------------------------------------------------------------


@dataclass
class NexusClient:
    """Unified client for Nexus API (JSON-RPC + REST + CLI).

    Args:
        http: Session-scoped httpx.Client with base_url and auth headers.
        base_url: The base URL (for CLI --url flag).
        api_key: The API key (for CLI --api-key flag).
    """

    http: httpx.Client
    base_url: str = ""
    api_key: str = ""
    _rpc_id: int = field(default=0, repr=False)

    def _next_id(self) -> int:
        self._rpc_id += 1
        return self._rpc_id

    # --- JSON-RPC layer ---

    def rpc(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        zone: str | None = None,
    ) -> RpcResponse:
        """Send a JSON-RPC 2.0 request to /api/nfs/{method}.

        Args:
            method: RPC method name (e.g., "read", "write", "glob").
            params: Method parameters.
            zone: Optional zone context (sent as X-Nexus-Zone-ID header).

        Returns:
            Parsed RpcResponse with result or error.

        Note:
            Does NOT raise on HTTP errors — instead wraps them as RPC errors.
            This allows tests to assert on error responses without try/except.
        """
        request_id = self._next_id()
        body = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": request_id,
        }
        headers = {}
        if zone:
            headers["X-Nexus-Zone-ID"] = zone
        resp = self.http.post(f"/api/nfs/{method}", json=body, headers=headers)

        # Handle HTTP-level errors (401, 403, 500, etc.) as RPC errors
        if resp.status_code != 200:
            detail = ""
            try:
                data = resp.json()
                detail = data.get("detail", data.get("message", str(data)))
            except (ValueError, KeyError):
                detail = resp.text
            return RpcResponse(
                id=request_id,
                error=RpcError(
                    code=-resp.status_code,
                    message=f"HTTP {resp.status_code}: {detail}",
                ),
            )

        return RpcResponse.model_validate(resp.json())

    # --- Convenience RPC methods (kernel file operations) ---

    def write_file(self, path: str, content: str, *, zone: str | None = None) -> RpcResponse:
        """Write a file via JSON-RPC."""
        return self.rpc("write", {"path": path, "content": content}, zone=zone)

    def read_file(self, path: str, *, zone: str | None = None) -> RpcResponse:
        """Read a file via JSON-RPC."""
        return self.rpc("read", {"path": path}, zone=zone)

    def delete_file(self, path: str, *, zone: str | None = None) -> RpcResponse:
        """Delete a file via JSON-RPC."""
        return self.rpc("delete", {"path": path}, zone=zone)

    def mkdir(self, path: str, *, parents: bool = False, zone: str | None = None) -> RpcResponse:
        """Create a directory via JSON-RPC."""
        params: dict[str, Any] = {"path": path}
        if parents:
            params["parents"] = True
        return self.rpc("mkdir", params, zone=zone)

    def list_dir(self, path: str, *, zone: str | None = None) -> RpcResponse:
        """List directory contents via JSON-RPC."""
        return self.rpc("list", {"path": path}, zone=zone)

    def glob(self, pattern: str, *, zone: str | None = None) -> RpcResponse:
        """Glob files via JSON-RPC."""
        params: dict[str, Any] = {"pattern": pattern}
        if zone:
            params["zone_id"] = zone
        return self.rpc("glob", params)

    def grep(self, pattern: str, path: str = "/", *, zone: str | None = None) -> RpcResponse:
        """Grep file contents via JSON-RPC."""
        params: dict[str, Any] = {"pattern": pattern, "path": path}
        if zone:
            params["zone_id"] = zone
        return self.rpc("grep", params)

    def rename(self, old_path: str, new_path: str, *, zone: str | None = None) -> RpcResponse:
        """Rename/move a file via JSON-RPC."""
        params: dict[str, Any] = {"old_path": old_path, "new_path": new_path}
        if zone:
            params["zone_id"] = zone
        return self.rpc("rename", params)

    def copy(self, source: str, destination: str, *, zone: str | None = None) -> RpcResponse:
        """Copy a file via JSON-RPC."""
        params: dict[str, Any] = {"src_path": source, "dst_path": destination}
        if zone:
            params["zone_id"] = zone
        return self.rpc("copy", params)

    def exists(self, path: str, *, zone: str | None = None) -> RpcResponse:
        """Check if a file/directory exists via JSON-RPC."""
        params: dict[str, Any] = {"path": path}
        if zone:
            params["zone_id"] = zone
        return self.rpc("exists", params)

    def get_metadata(self, path: str, *, zone: str | None = None) -> RpcResponse:
        """Get file metadata via JSON-RPC."""
        params: dict[str, Any] = {"path": path}
        if zone:
            params["zone_id"] = zone
        return self.rpc("get_metadata", params)

    def rmdir(self, path: str, *, recursive: bool = False, zone: str | None = None) -> RpcResponse:
        """Remove a directory via JSON-RPC."""
        params: dict[str, Any] = {"path": path}
        if recursive:
            params["recursive"] = True
        if zone:
            params["zone_id"] = zone
        return self.rpc("rmdir", params)

    # --- Admin RPC methods ---

    def admin_create_key(
        self,
        name: str,
        zone_id: str,
        *,
        user_id: str | None = None,
        is_admin: bool = False,
    ) -> RpcResponse:
        """Create an API key via admin RPC."""
        params: dict[str, Any] = {"name": name, "zone_id": zone_id}
        if user_id is not None:
            params["user_id"] = user_id
        params["is_admin"] = is_admin
        return self.rpc("admin_create_key", params)

    def add_mount(
        self,
        mount_point: str,
        backend_type: str,
        backend_config: dict[str, Any] | None = None,
        *,
        priority: int = 0,
        readonly: bool = False,
    ) -> RpcResponse:
        """Add a filesystem mount via JSON-RPC."""
        params: dict[str, Any] = {
            "mount_point": mount_point,
            "backend_type": backend_type,
            "backend_config": backend_config or {},
            "priority": priority,
            "readonly": readonly,
        }
        return self.rpc("add_mount", params)

    def list_mounts(self) -> RpcResponse:
        """List filesystem mounts via JSON-RPC."""
        return self.rpc("list_mounts")

    # --- ReBAC RPC methods ---

    def rebac_create(
        self,
        subject: tuple[str, str] | list[str],
        relation: str,
        object_: tuple[str, str] | list[str],
        *,
        zone_id: str | None = None,
        expires_at: str | None = None,
    ) -> RpcResponse:
        """Create a ReBAC relationship tuple via JSON-RPC."""
        params: dict[str, Any] = {
            "subject": list(subject),
            "relation": relation,
            "object": list(object_),
        }
        if zone_id is not None:
            params["zone_id"] = zone_id
        if expires_at is not None:
            params["expires_at"] = expires_at
        return self.rpc("rebac_create", params)

    def rebac_check(
        self,
        subject: tuple[str, str] | list[str],
        permission: str,
        object_: tuple[str, str] | list[str],
        *,
        zone_id: str | None = None,
        consistency_mode: str | None = None,
        min_revision: int | None = None,
    ) -> RpcResponse:
        """Check a ReBAC permission via JSON-RPC.

        Args:
            consistency_mode: "minimize_latency", "at_least_as_fresh", or "fully_consistent".
            min_revision: Minimum acceptable revision (for at_least_as_fresh mode).
        """
        params: dict[str, Any] = {
            "subject": list(subject),
            "permission": permission,
            "object": list(object_),
        }
        if zone_id is not None:
            params["zone_id"] = zone_id
        if consistency_mode is not None:
            params["consistency_mode"] = consistency_mode
        if min_revision is not None:
            params["min_revision"] = min_revision
        return self.rpc("rebac_check", params)

    def rebac_delete(self, tuple_id: str) -> RpcResponse:
        """Delete a ReBAC relationship tuple via JSON-RPC."""
        return self.rpc("rebac_delete", {"tuple_id": tuple_id})

    def rebac_list_tuples(
        self,
        *,
        subject: tuple[str, str] | list[str] | None = None,
        relation: str | None = None,
        object_: tuple[str, str] | list[str] | None = None,
    ) -> RpcResponse:
        """List ReBAC relationship tuples via JSON-RPC."""
        params: dict[str, Any] = {}
        if subject is not None:
            params["subject"] = list(subject)
        if relation is not None:
            params["relation"] = relation
        if object_ is not None:
            params["object"] = list(object_)
        return self.rpc("rebac_list_tuples", params)

    def rebac_explain(
        self,
        subject: tuple[str, str] | list[str],
        permission: str,
        object_: tuple[str, str] | list[str],
        *,
        zone_id: str | None = None,
    ) -> RpcResponse:
        """Explain a ReBAC permission check via JSON-RPC.

        Returns detailed trace of the permission resolution graph.
        Unlike rebac_check (which uses Rust acceleration), this uses
        the Python engine which fully resolves tupleToUserset patterns
        including group inheritance.
        """
        params: dict[str, Any] = {
            "subject": list(subject),
            "permission": permission,
            "object": list(object_),
        }
        if zone_id is not None:
            params["zone_id"] = zone_id
        return self.rpc("rebac_explain", params)

    def rebac_expand(
        self,
        permission: str,
        object_: tuple[str, str] | list[str],
        *,
        zone_id: str | None = None,
    ) -> RpcResponse:
        """Expand ReBAC permissions to find all subjects via JSON-RPC."""
        params: dict[str, Any] = {
            "permission": permission,
            "object": list(object_),
        }
        if zone_id is not None:
            params["zone_id"] = zone_id
        return self.rpc("rebac_expand", params)

    def rebac_check_batch(
        self,
        checks: list[dict[str, Any]],
    ) -> RpcResponse:
        """Batch check multiple ReBAC permissions via JSON-RPC.

        Args:
            checks: List of check requests, each with subject, permission, object,
                    and optional zone_id/consistency_mode/min_revision.
        """
        return self.rpc("rebac_check_batch", {"checks": checks})

    def rebac_list_objects(
        self,
        relation: str,
        subject: tuple[str, str] | list[str],
        *,
        zone_id: str | None = None,
    ) -> RpcResponse:
        """List objects a subject has a given relation to via JSON-RPC."""
        params: dict[str, Any] = {
            "relation": relation,
            "subject": list(subject),
        }
        if zone_id is not None:
            params["zone_id"] = zone_id
        return self.rpc("rebac_list_objects", params)

    # --- Zone client factory ---

    def for_zone(self, zone_api_key: str) -> NexusClient:
        """Create a new NexusClient using a zone-specific API key.

        The caller is responsible for closing the returned client's http session.
        """
        http = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {zone_api_key}"},
            timeout=self.http.timeout,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
        return NexusClient(http=http, base_url=self.base_url, api_key=zone_api_key)

    # --- Observability & Auth convenience methods ---

    def whoami(self) -> httpx.Response:
        """GET /api/auth/whoami — current identity."""
        return self.http.get("/api/auth/whoami")

    def health(self) -> httpx.Response:
        """GET /health — basic health status."""
        return self.http.get("/health")

    def health_detailed(self) -> httpx.Response:
        """GET /health/detailed — per-component health status."""
        return self.http.get("/health/detailed")

    def features(self) -> httpx.Response:
        """GET /api/v2/features — enabled server features."""
        return self.http.get("/api/v2/features")

    def metrics_raw(self) -> httpx.Response:
        """GET /metrics — raw Prometheus metrics text."""
        return self.http.get("/metrics")

    def operations(self) -> httpx.Response:
        """GET /api/v2/operations — recent operation log."""
        return self.http.get("/api/v2/operations")

    def probe_live(self) -> httpx.Response:
        """GET /healthz/live — Kubernetes liveness probe."""
        return self.http.get("/healthz/live")

    def probe_ready(self) -> httpx.Response:
        """GET /healthz/ready — Kubernetes readiness probe."""
        return self.http.get("/healthz/ready")

    def probe_startup(self) -> httpx.Response:
        """GET /healthz/startup — Kubernetes startup probe."""
        return self.http.get("/healthz/startup")

    # --- Zone REST API layer ---

    def create_zone(self, zone_id: str, *, name: str | None = None) -> httpx.Response:
        """Create a zone via REST API."""
        body = {"zone_id": zone_id, "name": name or f"Test Zone {zone_id}"}
        return self.http.post("/api/zones", json=body)

    def delete_zone(self, zone_id: str) -> httpx.Response:
        """Delete (deprovision) a zone via REST API."""
        return self.http.delete(f"/api/zones/{zone_id}")

    def get_zone(self, zone_id: str) -> httpx.Response:
        """Get zone details via REST API."""
        return self.http.get(f"/api/zones/{zone_id}")

    def list_zones(self) -> httpx.Response:
        """List all zones via REST API."""
        return self.http.get("/api/zones")

    # --- REST API layer ---

    def api_get(self, path: str, **kwargs: Any) -> httpx.Response:
        """GET request to a REST API endpoint."""
        return self.http.get(path, **kwargs)

    def api_post(self, path: str, **kwargs: Any) -> httpx.Response:
        """POST request to a REST API endpoint."""
        return self.http.post(path, **kwargs)

    def api_put(self, path: str, **kwargs: Any) -> httpx.Response:
        """PUT request to a REST API endpoint."""
        return self.http.put(path, **kwargs)

    def api_delete(self, path: str, **kwargs: Any) -> httpx.Response:
        """DELETE request to a REST API endpoint."""
        return self.http.delete(path, **kwargs)

    def api_patch(self, path: str, **kwargs: Any) -> httpx.Response:
        """PATCH request to a REST API endpoint."""
        return self.http.patch(path, **kwargs)

    def api_head(self, path: str, **kwargs: Any) -> httpx.Response:
        """HEAD request to a REST API endpoint."""
        return self.http.head(path, **kwargs)

    def api_options(self, path: str, **kwargs: Any) -> httpx.Response:
        """OPTIONS request to a REST API endpoint."""
        return self.http.request("OPTIONS", path, **kwargs)

    # --- CLI layer ---

    def cli(
        self,
        *args: str,
        timeout: float = 30.0,
        input_data: str | None = None,
    ) -> CliResult:
        """Run a `nexus` CLI command as a subprocess.

        Args:
            args: Command arguments (e.g., "ls", "/", "--zone", "corp").
            timeout: Subprocess timeout in seconds.
            input_data: Optional stdin data.

        Returns:
            CliResult with exit_code, stdout, stderr.
        """
        cmd = ["nexus", *args]

        # Add connection flags if not already present
        if "--url" not in args and self.base_url:
            cmd.extend(["--url", self.base_url])
        if "--api-key" not in args and self.api_key:
            cmd.extend(["--api-key", self.api_key])

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                input=input_data,
            )
            return CliResult(
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
            )
        except subprocess.TimeoutExpired:
            return CliResult(exit_code=-1, stdout="", stderr=f"Timeout after {timeout}s")
        except FileNotFoundError:
            return CliResult(exit_code=-1, stdout="", stderr="nexus CLI not found in PATH")
