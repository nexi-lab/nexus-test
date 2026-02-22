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

    def rpc(self, method: str, params: dict[str, Any] | None = None) -> RpcResponse:
        """Send a JSON-RPC 2.0 request to /api/nfs/{method}.

        Args:
            method: RPC method name (e.g., "read", "write", "glob").
            params: Method parameters.

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
        resp = self.http.post(f"/api/nfs/{method}", json=body)

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
        params: dict[str, Any] = {"path": path, "content": content}
        if zone:
            params["zone_id"] = zone
        return self.rpc("write", params)

    def read_file(self, path: str, *, zone: str | None = None) -> RpcResponse:
        """Read a file via JSON-RPC."""
        params: dict[str, Any] = {"path": path}
        if zone:
            params["zone_id"] = zone
        return self.rpc("read", params)

    def delete_file(self, path: str, *, zone: str | None = None) -> RpcResponse:
        """Delete a file via JSON-RPC."""
        params: dict[str, Any] = {"path": path}
        if zone:
            params["zone_id"] = zone
        return self.rpc("delete", params)

    def mkdir(self, path: str, *, parents: bool = False, zone: str | None = None) -> RpcResponse:
        """Create a directory via JSON-RPC."""
        params: dict[str, Any] = {"path": path}
        if parents:
            params["parents"] = True
        if zone:
            params["zone_id"] = zone
        return self.rpc("mkdir", params)

    def list_dir(self, path: str, *, zone: str | None = None) -> RpcResponse:
        """List directory contents via JSON-RPC."""
        params: dict[str, Any] = {"path": path}
        if zone:
            params["zone_id"] = zone
        return self.rpc("list", params)

    def glob(self, pattern: str, *, zone: str | None = None) -> RpcResponse:
        """Glob files via JSON-RPC."""
        params: dict[str, Any] = {"pattern": pattern}
        if zone:
            params["zone_id"] = zone
        return self.rpc("glob", params)

    def grep(
        self, pattern: str, path: str = "/", *, zone: str | None = None
    ) -> RpcResponse:
        """Grep file contents via JSON-RPC."""
        params: dict[str, Any] = {"pattern": pattern, "path": path}
        if zone:
            params["zone_id"] = zone
        return self.rpc("grep", params)

    def rename(
        self, old_path: str, new_path: str, *, zone: str | None = None
    ) -> RpcResponse:
        """Rename/move a file via JSON-RPC."""
        params: dict[str, Any] = {"old_path": old_path, "new_path": new_path}
        if zone:
            params["zone_id"] = zone
        return self.rpc("rename", params)

    def copy(
        self, source: str, destination: str, *, zone: str | None = None
    ) -> RpcResponse:
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
