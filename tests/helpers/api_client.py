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
# Enrichment flags (mirrors server-side EnrichmentFlags)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EnrichmentFlags:
    """Enrichment pipeline flags for memory storage.

    Controls which enrichment steps run on the server side.
    Default values match server defaults (steps 1-3,5 on; 4,6-8 off).
    """

    generate_embedding: bool = True
    extract_entities: bool = True
    extract_temporal: bool = True
    extract_relationships: bool = False  # Opt-in (expensive)
    classify_stability: bool = True
    detect_evolution: bool = False  # Opt-in (expensive)
    resolve_coreferences: bool = False  # Opt-in (write-time)
    resolve_temporal: bool = False  # Opt-in (write-time)
    store_to_graph: bool = False  # Opt-in

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

    def edit_file(
        self,
        path: str,
        edits: list[list[str]] | list[dict[str, Any]],
        *,
        if_match: str | None = None,
        fuzzy_threshold: float = 0.85,
        preview: bool = False,
        zone: str | None = None,
    ) -> RpcResponse:
        """Apply surgical search/replace edits to a file via JSON-RPC."""
        params: dict[str, Any] = {"path": path, "edits": edits}
        if if_match is not None:
            params["if_match"] = if_match
        if fuzzy_threshold != 0.85:
            params["fuzzy_threshold"] = fuzzy_threshold
        if preview:
            params["preview"] = True
        return self.rpc("edit", params, zone=zone)

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

    # --- Memory REST methods ---

    def _rest_to_rpc(
        self, resp: httpx.Response, *, request_id: int = 0
    ) -> RpcResponse:
        """Convert an httpx.Response into an RpcResponse envelope."""
        if resp.status_code in (200, 201):
            try:
                data = resp.json()
            except Exception:
                data = resp.text
            return RpcResponse(id=request_id, result=data)
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

    def memory_store(
        self,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
        zone: str | None = None,
        timestamp: str | None = None,
        enrichment: EnrichmentFlags | None = None,
        generate_embedding: bool = True,
    ) -> RpcResponse:
        """Store a memory via REST POST /api/v2/memories.

        Args:
            content: Memory content text.
            metadata: Optional metadata dict.
            zone: Optional zone context (sent as X-Nexus-Zone-ID header).
            timestamp: Optional valid_at timestamp (ISO-8601).
            enrichment: Optional enrichment pipeline flags. If None, server defaults apply.
        """
        body: dict[str, Any] = {"content": content}
        if metadata is not None:
            body["metadata"] = metadata
        if timestamp is not None:
            body["valid_at"] = timestamp
        if enrichment is not None:
            body["generate_embedding"] = enrichment.generate_embedding
            body["extract_entities"] = enrichment.extract_entities
            body["extract_temporal"] = enrichment.extract_temporal
            body["extract_relationships"] = enrichment.extract_relationships
            body["classify_stability"] = enrichment.classify_stability
            body["detect_evolution"] = enrichment.detect_evolution
            body["resolve_coreferences"] = enrichment.resolve_coreferences
            body["resolve_temporal"] = enrichment.resolve_temporal
            body["store_to_graph"] = enrichment.store_to_graph
        elif not generate_embedding:
            body["generate_embedding"] = False
        headers: dict[str, str] = {}
        if zone:
            headers["X-Nexus-Zone-ID"] = zone
        resp = self.http.post("/api/v2/memories", json=body, headers=headers)
        result = self._rest_to_rpc(resp)
        # Normalise: ensure result dict always has 'memory_id' key
        if result.ok and isinstance(result.result, dict):
            result = RpcResponse(
                id=result.id,
                result={"memory_id": result.result.get("memory_id"), **result.result},
            )
        return result

    def memory_query(
        self,
        query: str,
        *,
        limit: int = 10,
        zone: str | None = None,
        time_start: str | None = None,
        time_end: str | None = None,
    ) -> RpcResponse:
        """Query memories via REST POST /api/v2/memories/query.

        Falls back to listing + client-side filter when the semantic
        search endpoint is unavailable.
        """
        headers: dict[str, str] = {}
        if zone:
            headers["X-Nexus-Zone-ID"] = zone

        # Try semantic search first
        search_body: dict[str, Any] = {"query": query, "limit": limit}
        if time_start is not None:
            search_body["after"] = time_start
        if time_end is not None:
            search_body["before"] = time_end
        resp = self.http.post(
            "/api/v2/memories/search", json=search_body, headers=headers
        )
        if resp.status_code in (200, 201):
            search_rpc = self._rest_to_rpc(resp)
            # Extract results — search endpoint may return empty due to
            # server-side permission context bug (uses global context, not
            # per-request auth). Fall through to query endpoint as fallback.
            search_results = (
                search_rpc.result.get("results", [])
                if isinstance(search_rpc.result, dict)
                else search_rpc.result if isinstance(search_rpc.result, list)
                else []
            )
            if search_results:
                # Normalize: return results as a list (not wrapped dict)
                return RpcResponse(id=search_rpc.id, result=search_results)

        # Fallback: list memories via query endpoint and filter client-side
        query_body: dict[str, Any] = {"query": query, "limit": limit}
        if time_start is not None:
            query_body["after"] = time_start
        if time_end is not None:
            query_body["before"] = time_end
        resp = self.http.post(
            "/api/v2/memories/query", json=query_body, headers=headers
        )
        rpc_resp = self._rest_to_rpc(resp)
        if not rpc_resp.ok:
            return rpc_resp

        # Normalize: extract the results list from the response
        raw_result = rpc_resp.result
        if isinstance(raw_result, dict):
            all_results = raw_result.get("results", [])
        elif isinstance(raw_result, list):
            all_results = raw_result
        else:
            all_results = []

        # Client-side relevance filter
        query_lower = query.lower()
        filtered = [
            r for r in all_results
            if query_lower in (r.get("content", "") or "").lower()
        ]
        # Return normalized list as result (not nested dict)
        return RpcResponse(
            id=rpc_resp.id,
            result=filtered if filtered else all_results,
        )

    def memory_delete(self, memory_id: str, *, zone: str | None = None) -> RpcResponse:
        """Delete a memory via REST DELETE /api/v2/memories/{id}."""
        headers: dict[str, str] = {}
        if zone:
            headers["X-Nexus-Zone-ID"] = zone
        resp = self.http.delete(f"/api/v2/memories/{memory_id}", headers=headers)
        return self._rest_to_rpc(resp)

    def memory_get(self, memory_id: str, *, zone: str | None = None) -> RpcResponse:
        """Get a single memory by ID via REST GET /api/v2/memories/{id}.

        The server wraps the response in ``{"memory": {...}}``.
        This method unwraps it so ``result`` is the memory dict directly.
        """
        headers: dict[str, str] = {}
        if zone:
            headers["X-Nexus-Zone-ID"] = zone
        resp = self.http.get(f"/api/v2/memories/{memory_id}", headers=headers)
        rpc = self._rest_to_rpc(resp)
        # Unwrap {"memory": {...}} envelope if present
        if rpc.ok and isinstance(rpc.result, dict) and "memory" in rpc.result:
            rpc = RpcResponse(id=rpc.id, result=rpc.result["memory"])
        return rpc

    def memory_approve(self, memory_id: str, *, zone: str | None = None) -> RpcResponse:
        """Activate a memory (inactive -> active) via REST PUT state change."""
        headers: dict[str, str] = {}
        if zone:
            headers["X-Nexus-Zone-ID"] = zone
        resp = self.http.put(
            f"/api/v2/memories/{memory_id}",
            json={"state": "active"},
            headers=headers,
        )
        return self._rest_to_rpc(resp)

    def memory_deactivate(self, memory_id: str, *, zone: str | None = None) -> RpcResponse:
        """Deactivate a memory (active -> inactive) via REST PUT state change."""
        headers: dict[str, str] = {}
        if zone:
            headers["X-Nexus-Zone-ID"] = zone
        resp = self.http.put(
            f"/api/v2/memories/{memory_id}",
            json={"state": "inactive"},
            headers=headers,
        )
        return self._rest_to_rpc(resp)

    def memory_search(
        self,
        query: str,
        *,
        semantic: bool = True,
        zone: str | None = None,
    ) -> RpcResponse:
        """Search memories via REST POST /api/v2/memories/search."""
        body: dict[str, Any] = {"query": query}
        if not semantic:
            body["search_mode"] = "keyword"
        headers: dict[str, str] = {}
        if zone:
            headers["X-Nexus-Zone-ID"] = zone
        resp = self.http.post("/api/v2/memories/search", json=body, headers=headers)
        return self._rest_to_rpc(resp)

    def memory_consolidate(self, *, zone: str | None = None) -> RpcResponse:
        """Trigger memory consolidation via REST POST /api/v2/consolidate."""
        headers: dict[str, str] = {}
        if zone:
            headers["X-Nexus-Zone-ID"] = zone
        resp = self.http.post("/api/v2/consolidate", json={}, headers=headers)
        return self._rest_to_rpc(resp)

    def memory_forget_entity(
        self, entity_id: str, *, zone: str | None = None
    ) -> RpcResponse:
        """Forget all memories related to an entity (GDPR-style purge).

        Deletes all memories whose content mentions the entity.
        """
        # Query for memories mentioning this entity, then delete each
        query_resp = self.memory_query(entity_id, limit=100, zone=zone)
        if not query_resp.ok:
            return query_resp
        results = query_resp.result if isinstance(query_resp.result, list) else []
        deleted_ids: list[str] = []
        for mem in results:
            mid = mem.get("memory_id", "")
            if mid:
                del_resp = self.memory_delete(mid, zone=zone)
                if del_resp.ok:
                    deleted_ids.append(mid)
        return RpcResponse(
            result={"deleted_count": len(deleted_ids), "deleted_ids": deleted_ids},
        )

    def memory_get_versions(
        self, memory_id: str, *, zone: str | None = None
    ) -> RpcResponse:
        """Get version history for a memory via REST GET /api/v2/memories/{id}/history."""
        headers: dict[str, str] = {}
        if zone:
            headers["X-Nexus-Zone-ID"] = zone
        resp = self.http.get(
            f"/api/v2/memories/{memory_id}/history", headers=headers
        )
        return self._rest_to_rpc(resp)

    def memory_graph_query(self, entity: str, *, zone: str | None = None) -> httpx.Response:
        """GET /api/v2/graph/search?name={entity} — knowledge graph lookup by name."""
        headers: dict[str, str] = {}
        if zone:
            headers["X-Nexus-Zone-ID"] = zone
        return self.http.get(
            "/api/v2/graph/search",
            params={"name": entity, "fuzzy": "true"},
            headers=headers,
        )

    # --- Search daemon REST methods ---

    def search_query(
        self,
        query: str,
        *,
        search_type: str = "hybrid",
        limit: int = 10,
        path: str | None = None,
        alpha: float = 0.5,
        zone: str | None = None,
    ) -> httpx.Response:
        """GET /api/v2/search/query — search daemon query."""
        params: dict[str, Any] = {"q": query, "type": search_type, "limit": limit}
        if path is not None:
            params["path"] = path
        if alpha != 0.5:
            params["alpha"] = alpha
        headers: dict[str, str] = {}
        if zone:
            headers["X-Nexus-Zone-ID"] = zone
        return self.http.get("/api/v2/search/query", params=params, headers=headers)

    def search_expand(self, query: str) -> httpx.Response:
        """POST /api/v2/search/expand?q={query} — LLM query expansion."""
        return self.http.post("/api/v2/search/expand", params={"q": query})

    def memory_update(
        self,
        memory_id: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
        zone: str | None = None,
    ) -> RpcResponse:
        """Update a memory via REST PUT /api/v2/memories/{id}."""
        body: dict[str, Any] = {"content": content}
        if metadata is not None:
            body["metadata"] = metadata
        headers: dict[str, str] = {}
        if zone:
            headers["X-Nexus-Zone-ID"] = zone
        resp = self.http.put(
            f"/api/v2/memories/{memory_id}", json=body, headers=headers
        )
        return self._rest_to_rpc(resp)

    def memory_invalidate(
        self, memory_id: str, *, zone: str | None = None
    ) -> RpcResponse:
        """Invalidate a memory by setting state to 'inactive' via PUT."""
        headers: dict[str, str] = {}
        if zone:
            headers["X-Nexus-Zone-ID"] = zone
        resp = self.http.put(
            f"/api/v2/memories/{memory_id}",
            json={"state": "inactive"},
            headers=headers,
        )
        return self._rest_to_rpc(resp)

    def memory_revalidate(
        self, memory_id: str, *, zone: str | None = None
    ) -> RpcResponse:
        """Revalidate a memory by setting state to 'active' via PUT."""
        headers: dict[str, str] = {}
        if zone:
            headers["X-Nexus-Zone-ID"] = zone
        resp = self.http.put(
            f"/api/v2/memories/{memory_id}",
            json={"state": "active"},
            headers=headers,
        )
        return self._rest_to_rpc(resp)

    def memory_lineage(
        self, memory_id: str, *, zone: str | None = None
    ) -> RpcResponse:
        """Get lineage chain via GET /api/v2/memories/{id}/lineage."""
        headers: dict[str, str] = {}
        if zone:
            headers["X-Nexus-Zone-ID"] = zone
        resp = self.http.get(
            f"/api/v2/memories/{memory_id}/lineage", headers=headers
        )
        return self._rest_to_rpc(resp)

    def memory_diff(
        self, memory_id: str, v1: int, v2: int, *, zone: str | None = None
    ) -> RpcResponse:
        """Diff two memory versions via GET /api/v2/memories/{id}/diff."""
        headers: dict[str, str] = {}
        if zone:
            headers["X-Nexus-Zone-ID"] = zone
        resp = self.http.get(
            f"/api/v2/memories/{memory_id}/diff",
            params={"v1": v1, "v2": v2},
            headers=headers,
        )
        return self._rest_to_rpc(resp)

    # --- Search REST methods ---

    def search(
        self,
        query: str,
        *,
        path: str | None = None,
        limit: int = 10,
        search_mode: str = "hybrid",
        alpha: float = 0.5,
        fusion_method: str = "rrf",
        zone: str | None = None,
    ) -> RpcResponse:
        """Search via REST GET /api/v2/search/query."""
        params: dict[str, Any] = {
            "q": query,
            "type": search_mode,
            "limit": limit,
            "alpha": alpha,
            "fusion": fusion_method,
        }
        if path:
            params["path"] = path
        headers: dict[str, str] = {}
        if zone:
            headers["X-Nexus-Zone-ID"] = zone
        resp = self.http.get("/api/v2/search/query", params=params, headers=headers)
        return self._rest_to_rpc(resp)

    def search_zoekt(
        self,
        query: str,
        *,
        limit: int = 100,
        zone: str | None = None,
    ) -> RpcResponse:
        """Search via Zoekt trigram index (keyword mode auto-triggers Zoekt)."""
        return self.search(
            query, limit=limit, search_mode="keyword", zone=zone,
        )

    def search_health(self) -> httpx.Response:
        """GET /api/v2/search/health — search subsystem health."""
        return self.http.get("/api/v2/search/health")

    def search_stats(self) -> httpx.Response:
        """GET /api/v2/search/stats — search daemon statistics."""
        return self.http.get("/api/v2/search/stats")

    def search_refresh(
        self, path: str, *, change_type: str = "create", zone: str | None = None,
    ) -> httpx.Response:
        """POST /api/v2/search/refresh — notify daemon of file change.

        The daemon requires zone-scoped paths (/zone/{zone_id}/...) for indexing.
        If ``zone`` is provided and the path is not already zone-scoped,
        the path is automatically prefixed with /zone/{zone}/.
        """
        if zone and not path.startswith("/zone/"):
            if not path.startswith("/"):
                path = f"/{path}"
            path = f"/zone/{zone}{path}"
        return self.http.post(
            "/api/v2/search/refresh",
            params={"path": path, "change_type": change_type},
        )

    # --- IPC REST methods ---

    def ipc_provision(self, agent_id: str) -> RpcResponse:
        """Provision IPC directories for an agent via POST /api/v2/ipc/provision/{agent_id}."""
        resp = self.http.post(f"/api/v2/ipc/provision/{agent_id}")
        return self._rest_to_rpc(resp)

    def ipc_send(
        self,
        sender: str,
        recipient: str,
        payload: dict[str, Any] | None = None,
        *,
        msg_type: str = "task",
        ttl_seconds: int | None = None,
        correlation_id: str | None = None,
    ) -> RpcResponse:
        """Send an IPC message via POST /api/v2/ipc/send."""
        body: dict[str, Any] = {
            "sender": sender,
            "recipient": recipient,
            "type": msg_type,
            "payload": payload or {},
        }
        if ttl_seconds is not None:
            body["ttl_seconds"] = ttl_seconds
        if correlation_id is not None:
            body["correlation_id"] = correlation_id
        resp = self.http.post("/api/v2/ipc/send", json=body)
        return self._rest_to_rpc(resp)

    def ipc_inbox(self, agent_id: str) -> RpcResponse:
        """List messages in an agent's inbox via GET /api/v2/ipc/inbox/{agent_id}."""
        resp = self.http.get(f"/api/v2/ipc/inbox/{agent_id}")
        return self._rest_to_rpc(resp)

    def ipc_inbox_count(self, agent_id: str) -> RpcResponse:
        """Count messages in an agent's inbox via GET /api/v2/ipc/inbox/{agent_id}/count."""
        resp = self.http.get(f"/api/v2/ipc/inbox/{agent_id}/count")
        return self._rest_to_rpc(resp)

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
