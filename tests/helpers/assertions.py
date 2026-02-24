"""Reusable assertion helpers for Nexus E2E tests.

These helpers encapsulate common multi-step assertion patterns
to eliminate DRY violations across ~355 tests.

All helpers raise AssertionError with descriptive messages on failure.
"""

from __future__ import annotations

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
        f"File roundtrip failed for {path}: "
        f"wrote {content!r}, read {read_resp.content_str!r}"
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
        actual_names = {entry.get("name", entry) if isinstance(entry, dict) else entry for entry in result}
    elif isinstance(result, dict) and "entries" in result:
        actual_names = {
            entry.get("name", entry) if isinstance(entry, dict) else entry
            for entry in result["entries"]
        }
    else:
        actual_names = set()

    missing = expected_names - actual_names
    assert not missing, (
        f"Directory {path} missing entries: {missing}. "
        f"Found: {actual_names}"
    )


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
    assert resp.status_code == 200, (
        f"Expected HTTP 200, got {resp.status_code}: {resp.text[:500]}"
    )
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


def parse_prometheus_metric(
    text: str, metric_name: str
) -> dict[str, Any] | None:
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
    type_pattern = re.compile(
        rf"^#\s+TYPE\s+{re.escape(metric_name)}\s+(\w+)", re.MULTILINE
    )
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
        try:
            metric_value = float(value_match.group(1))
        except ValueError:
            pass

    if metric_type is None and metric_value is None:
        return None

    return {
        "type": metric_type or "untyped",
        "value": metric_value,
    }
