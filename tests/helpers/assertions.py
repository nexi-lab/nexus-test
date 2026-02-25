"""Reusable assertion helpers for Nexus E2E tests.

These helpers encapsulate common multi-step assertion patterns
to eliminate DRY violations across ~355 tests.

All helpers raise AssertionError with descriptive messages on failure.
"""

from __future__ import annotations

import contextlib
import re
from typing import Any

import httpx

from tests.helpers.api_client import CliResult, NexusClient, RpcResponse


def assert_rpc_success(response: RpcResponse) -> Any:
    """Assert that a JSON-RPC response succeeded and return the result.

    Raises:
        AssertionError: If the response contains an error.
    """
    assert response.ok, (
        f"Expected RPC success but got error: "
        f"code={response.error.code}, message={response.error.message}"
        + (f", data={response.error.data}" if response.error.data else "")
    )
    return response.result


def assert_rpc_error(
    response: RpcResponse,
    *,
    code: int | None = None,
    message_contains: str | None = None,
) -> RpcResponse:
    """Assert that a JSON-RPC response is an error.

    Args:
        response: The RPC response to check.
        code: Expected error code (optional).
        message_contains: Substring expected in the error message (optional).

    Returns:
        The response (for further inspection).
    """
    assert not response.ok, f"Expected RPC error but got success: result={response.result}"
    if code is not None:
        assert response.error.code == code, (
            f"Expected error code {code}, got {response.error.code}: {response.error.message}"
        )
    if message_contains is not None:
        assert message_contains in response.error.message, (
            f"Expected error message to contain {message_contains!r}, "
            f"got: {response.error.message!r}"
        )
    return response


def assert_file_roundtrip(
    nexus: NexusClient,
    path: str,
    content: str,
    *,
    zone: str | None = None,
) -> dict[str, Any]:
    """Write a file and verify it reads back identically.

    Args:
        nexus: The Nexus client.
        path: File path to write/read.
        content: Content to write.
        zone: Optional zone ID.

    Returns:
        The write response result (contains etag, etc.).
    """
    write_result = assert_rpc_success(nexus.write_file(path, content, zone=zone))
    read_resp = nexus.read_file(path, zone=zone)
    assert read_resp.ok, f"File roundtrip read failed for {path}: {read_resp.error}"
    assert read_resp.content_str == content, (
        f"File roundtrip failed for {path}: wrote {content!r}, read {read_resp.content_str!r}"
    )
    return write_result


def assert_file_not_found(
    nexus: NexusClient,
    path: str,
    *,
    zone: str | None = None,
) -> None:
    """Assert that reading a file returns a not-found error."""
    response = nexus.read_file(path, zone=zone)
    assert not response.ok, f"Expected file {path} to not exist, but read succeeded"


def assert_directory_contains(
    nexus: NexusClient,
    path: str,
    expected_names: set[str],
    *,
    zone: str | None = None,
) -> None:
    """Assert that a directory listing contains the expected entry names."""
    result = assert_rpc_success(nexus.list_dir(path, zone=zone))
    if isinstance(result, list):
        actual_names = {
            entry.get("name", entry) if isinstance(entry, dict) else entry for entry in result
        }
    elif isinstance(result, dict) and "entries" in result:
        actual_names = {
            entry.get("name", entry) if isinstance(entry, dict) else entry
            for entry in result["entries"]
        }
    else:
        actual_names = set()

    missing = expected_names - actual_names
    assert not missing, f"Directory {path} missing entries: {missing}. Found: {actual_names}"


def assert_cli_success(result: CliResult) -> str:
    """Assert CLI command succeeded and return stdout."""
    assert result.ok, (
        f"CLI command failed (exit code {result.exit_code}):\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    return result.stdout


def assert_cli_error(result: CliResult, *, stderr_contains: str | None = None) -> CliResult:
    """Assert CLI command failed."""
    assert not result.ok, f"Expected CLI error but got success:\nstdout: {result.stdout}"
    if stderr_contains is not None:
        assert stderr_contains in result.stderr, (
            f"Expected stderr to contain {stderr_contains!r}, got: {result.stderr!r}"
        )
    return result


def assert_permission_denied(response: RpcResponse) -> RpcResponse:
    """Assert that an RPC response indicates a permission denial.

    Checks for HTTP 403, or error messages containing "forbidden", "denied",
    or "permission". Tolerant of different server response formats.

    Returns:
        The response (for further inspection).
    """
    assert not response.ok, f"Expected permission denied but got success: result={response.result}"
    error_code = response.error.code
    error_msg = response.error.message.lower()
    is_permission_error = (
        abs(error_code) == 403
        or "forbidden" in error_msg
        or "denied" in error_msg
        or "permission" in error_msg
    )
    assert is_permission_error, (
        f"Expected permission denied error, got: "
        f"code={error_code}, message={response.error.message!r}"
    )
    return response


def extract_memory_results(response: RpcResponse) -> list[dict]:
    """Extract the results list from a memory query/search response.

    Handles various response shapes:
    - list of dicts
    - dict with "results" or "memories" key
    - single dict result

    Returns:
        List of result dicts (may be empty).
    """
    results = response.result
    if isinstance(results, dict):
        results = results.get("results", results.get("memories", []))
    if not isinstance(results, list):
        results = [results] if results else []
    return results


def assert_memory_stored(response: RpcResponse) -> dict:
    """Assert memory_store succeeded and return result with memory_id.

    Raises:
        AssertionError: If the response is an error or missing memory_id.
    """
    assert response.ok, (
        f"Expected memory_store success but got error: "
        f"code={response.error.code}, message={response.error.message}"
    )
    result = response.result
    assert isinstance(result, dict), f"Expected dict result, got {type(result)}"
    assert "memory_id" in result, f"Result missing memory_id: {result}"
    assert result["memory_id"], "memory_id should be non-empty"
    return result


def assert_memory_not_found(
    nexus: NexusClient, memory_id: str, *, zone: str | None = None
) -> None:
    """Assert a specific memory no longer exists by trying to fetch its versions."""
    resp = nexus.memory_get_versions(memory_id, zone=zone)
    if resp.ok and resp.result:
        assert not resp.result, f"Memory {memory_id} should not exist but got: {resp.result}"


def assert_memory_query_contains(
    response: RpcResponse, *, content_substring: str
) -> dict:
    """Assert memory query result contains expected content.

    Returns:
        The first matching result dict.

    Raises:
        AssertionError: If the query failed or no result contains the substring.
    """
    assert response.ok, (
        f"Expected memory_query success but got error: "
        f"code={response.error.code}, message={response.error.message}"
    )
    results = extract_memory_results(response)

    for item in results:
        content = item.get("content", "") if isinstance(item, dict) else str(item)
        if content_substring in content:
            return item

    all_content = [
        (r.get("content", "") if isinstance(r, dict) else str(r)) for r in results
    ]
    raise AssertionError(
        f"No memory result contains {content_substring!r}. "
        f"Got {len(results)} results: {all_content[:5]}"
    )


def assert_memory_purged(
    nexus: NexusClient, entity: str, *, zone: str | None = None
) -> None:
    """Assert entity purged from store + search index + knowledge graph.

    Triple-verify: query (store), search (index), graph query (knowledge graph).
    """
    # 1. Query store
    query_resp = nexus.memory_query(entity, zone=zone)
    if query_resp.ok:
        results = extract_memory_results(query_resp)
        matching = [
            r for r in results
            if entity.lower() in (r.get("content", "") if isinstance(r, dict) else str(r)).lower()
        ]
        assert not matching, f"Entity {entity!r} still in store: {matching[:3]}"

    # 2. Search index
    search_resp = nexus.memory_search(entity, zone=zone)
    if search_resp.ok:
        results = extract_memory_results(search_resp)
        matching = [
            r for r in results
            if entity.lower() in (r.get("content", "") if isinstance(r, dict) else str(r)).lower()
        ]
        assert not matching, f"Entity {entity!r} still in search index: {matching[:3]}"

    # 3. Knowledge graph
    graph_resp = nexus.memory_graph_query(entity, zone=zone)
    if graph_resp.status_code == 200:
        data = graph_resp.json()
        nodes = data.get("nodes", data.get("entities", []))
        assert not nodes, f"Entity {entity!r} still in knowledge graph: {nodes[:3]}"


def assert_health_ok(nexus: NexusClient) -> dict[str, Any]:
    """Assert that the /health endpoint returns a healthy response."""
    resp = nexus.api_get("/health")
    assert resp.status_code == 200, f"Health check failed: {resp.status_code} {resp.text}"
    data = resp.json()
    status = data.get("status", "").lower()
    assert status in {"healthy", "ok"}, f"Unhealthy status: {data}"
    return data


def assert_http_ok(resp: httpx.Response) -> dict[str, Any]:
    """Assert that an HTTP response is 200 and return the JSON body.

    Args:
        resp: The httpx response to check.

    Returns:
        Parsed JSON body as a dict.

    Raises:
        AssertionError: If the status is not 200.
    """
    assert resp.status_code == 200, f"Expected HTTP 200, got {resp.status_code}: {resp.text[:500]}"
    return resp.json()


def extract_paths(result: Any) -> list[str]:
    """Extract file paths from an RPC result.

    Handles various result formats:
    - list of strings
    - list of dicts with "path" keys
    - dict with "matches" or "entries" list

    Returns:
        List of path strings.
    """
    if result is None:
        return []
    if isinstance(result, list):
        paths = []
        for item in result:
            if isinstance(item, str):
                paths.append(item)
            elif isinstance(item, dict):
                paths.append(item.get("path", item.get("name", str(item))))
        return paths
    if isinstance(result, dict):
        for key in ("matches", "entries", "paths", "files"):
            if key in result:
                return extract_paths(result[key])
    return []


def parse_prometheus_metric(text: str, metric_name: str) -> dict[str, Any] | None:
    """Parse a single metric from Prometheus text exposition format.

    Args:
        text: Raw metrics text from /metrics endpoint.
        metric_name: The metric name to find (e.g., "http_requests_total").

    Returns:
        Dict with "type" and "value" keys, or None if not found.
        - type: "counter", "gauge", "histogram", "summary", "untyped"
        - value: float or None if no sample line found
    """
    metric_type: str | None = None
    metric_value: float | None = None

    # Find TYPE declaration
    type_pattern = re.compile(rf"^#\s+TYPE\s+{re.escape(metric_name)}\s+(\w+)", re.MULTILINE)
    type_match = type_pattern.search(text)
    if type_match:
        metric_type = type_match.group(1).lower()

    # Find a sample value line.
    # Match: metric_name{labels} value  OR  metric_name value
    # Avoid matching suffixes like _bucket, _sum, _count unless explicitly requested.
    value_pattern = re.compile(
        rf"^{re.escape(metric_name)}(?:\{{[^}}]*\}})?\s+([\d.eE+-]+(?:e[+-]?\d+)?)",
        re.MULTILINE,
    )
    value_match = value_pattern.search(text)
    if value_match:
        with contextlib.suppress(ValueError):
            metric_value = float(value_match.group(1))

    if metric_type is None and metric_value is None:
        return None

    return {
        "type": metric_type or "untyped",
        "value": metric_value,
    }
