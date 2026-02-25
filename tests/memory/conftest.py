"""Memory test fixtures — enforcement gate, synthetic data, factories.

Fixture scoping:
    module:  _memory_available (auto-skip gate), seeded_memories (read-only)
    function: store_memory (factory with per-test cleanup)
"""

from __future__ import annotations

import contextlib
import logging
import time
import uuid
from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from tests.helpers.api_client import NexusClient, RpcResponse
from tests.helpers.assertions import extract_memory_results

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

StoreMemoryFn = Callable[..., RpcResponse]


# ---------------------------------------------------------------------------
# Polling helper for search indexing delay
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PollResult:
    """Result of poll_memory_query with latency tracking."""

    results: list[dict]
    query_latency_ms: float  # Last successful query round-trip time
    via_fallback: bool  # True if results came from GET fallback


def _get_fallback(
    nexus: NexusClient,
    memory_ids: list[str],
    match_substring: str | None,
) -> tuple[list[dict], float]:
    """Fetch memories by ID (direct GET) as fallback when search is slow.

    Returns (results, latency_ms) tuple.
    """
    results: list[dict] = []
    t0 = time.monotonic()
    for mid in memory_ids:
        get_resp = nexus.memory_get(mid)
        if get_resp.ok and isinstance(get_resp.result, dict):
            mem = get_resp.result.get("memory", get_resp.result)
            if match_substring is None or match_substring in str(
                mem.get("content", "")
            ):
                results.append(mem)
    latency_ms = (time.monotonic() - t0) * 1000
    return results, latency_ms


def poll_memory_query(
    nexus: NexusClient,
    query: str,
    *,
    match_substring: str | None = None,
    memory_ids: list[str] | None = None,
    zone: str | None = None,
    limit: int = 50,
    timeout: float = 15.0,
    poll_interval: float = 1.0,
    get_fallback_after: float = 3.0,
) -> list[dict]:
    """Poll memory_query until results contain the expected content.

    Handles search indexing delay by retrying. Falls back to direct
    memory_get after ``get_fallback_after`` seconds if memory_ids are known.

    Returns:
        List of matching result dicts. May be empty if nothing found.
    """
    pr = poll_memory_query_with_latency(
        nexus, query,
        match_substring=match_substring,
        memory_ids=memory_ids,
        zone=zone,
        limit=limit,
        timeout=timeout,
        poll_interval=poll_interval,
        get_fallback_after=get_fallback_after,
    )
    return pr.results


def poll_memory_query_with_latency(
    nexus: NexusClient,
    query: str,
    *,
    match_substring: str | None = None,
    memory_ids: list[str] | None = None,
    zone: str | None = None,
    limit: int = 50,
    timeout: float = 15.0,
    poll_interval: float = 1.0,
    get_fallback_after: float = 3.0,
) -> PollResult:
    """Poll memory_query until results contain the expected content.

    Like poll_memory_query but returns PollResult with latency info.
    """
    start = time.monotonic()
    deadline = start + timeout
    results: list[dict] = []
    fallback_tried = False
    last_query_latency_ms = 0.0

    while time.monotonic() < deadline:
        q0 = time.monotonic()
        resp = nexus.memory_query(query, limit=limit, zone=zone)
        last_query_latency_ms = (time.monotonic() - q0) * 1000

        if resp.ok:
            results = extract_memory_results(resp)
            if match_substring is None and results:
                return PollResult(results, last_query_latency_ms, via_fallback=False)
            if match_substring is not None:
                matching = [
                    r for r in results
                    if match_substring in (
                        r.get("content", "") if isinstance(r, dict) else str(r)
                    )
                ]
                if matching:
                    return PollResult(results, last_query_latency_ms, via_fallback=False)

        # Early fallback: try direct GET after a few seconds of failed search
        elapsed = time.monotonic() - start
        if (
            not fallback_tried
            and memory_ids
            and elapsed >= get_fallback_after
        ):
            fallback_tried = True
            fallback_results, fb_latency = _get_fallback(
                nexus, memory_ids, match_substring,
            )
            if fallback_results:
                return PollResult(fallback_results, fb_latency, via_fallback=True)

        time.sleep(poll_interval)

    # Final fallback: try direct GET if not tried yet
    if memory_ids and not fallback_tried:
        fallback_results, fb_latency = _get_fallback(
            nexus, memory_ids, match_substring,
        )
        if fallback_results:
            return PollResult(fallback_results, fb_latency, via_fallback=True)

    return PollResult(results, last_query_latency_ms, via_fallback=False)


# ---------------------------------------------------------------------------
# Synthetic test data constants (Decision #7, #16)
# ---------------------------------------------------------------------------

TEMPORAL_MEMORIES = [
    {
        "content": "Q1 2025 revenue was $10M, driven by enterprise contracts",
        "timestamp": "2025-03-15T10:00:00Z",
        "metadata": {"category": "finance", "quarter": "Q1", "year": 2025},
    },
    {
        "content": "Q2 2025 revenue was $12M with 20% growth quarter-over-quarter",
        "timestamp": "2025-06-20T14:00:00Z",
        "metadata": {"category": "finance", "quarter": "Q2", "year": 2025},
    },
    {
        "content": "Q3 2025 revenue was $15M, highest quarter on record",
        "timestamp": "2025-09-20T14:00:00Z",
        "metadata": {"category": "finance", "quarter": "Q3", "year": 2025},
    },
    {
        "content": "Company moved to new office building in April 2025",
        "timestamp": "2025-04-01T09:00:00Z",
        "metadata": {"category": "operations", "event": "relocation"},
    },
    {
        "content": "Annual planning meeting held in January 2025",
        "timestamp": "2025-01-10T08:00:00Z",
        "metadata": {"category": "planning", "event": "annual_planning"},
    },
]

CONFLICT_MEMORIES = [
    {
        "content": "Project Alpha uses Python for its backend",
        "version": 1,
        "timestamp": "2025-01-01T00:00:00Z",
        "metadata": {"project": "alpha", "topic": "tech_stack"},
    },
    {
        "content": "Project Alpha uses Rust for its backend",
        "version": 2,
        "timestamp": "2025-06-01T00:00:00Z",
        "metadata": {"project": "alpha", "topic": "tech_stack"},
    },
]

MULTI_SESSION_MEMORIES = [
    {
        "session": 1,
        "content": "Alice joined the engineering team as a backend developer",
        "timestamp": "2025-01-15T09:00:00Z",
        "metadata": {"person": "alice", "event": "joined"},
    },
    {
        "session": 2,
        "content": "Alice was promoted to lead the backend team",
        "timestamp": "2025-04-01T10:00:00Z",
        "metadata": {"person": "alice", "event": "promoted"},
    },
    {
        "session": 3,
        "content": "Alice proposed the database migration to PostgreSQL",
        "timestamp": "2025-07-15T11:00:00Z",
        "metadata": {"person": "alice", "event": "proposal"},
    },
]

ENTITY_MEMORIES = [
    {
        "content": "Bob works in the engineering department as a senior developer",
        "entity": "bob",
        "timestamp": "2025-02-01T09:00:00Z",
        "metadata": {"person": "bob", "department": "engineering"},
    },
    {
        "content": "Carol works in the sales department as account manager",
        "entity": "carol",
        "timestamp": "2025-02-01T09:00:00Z",
        "metadata": {"person": "carol", "department": "sales"},
    },
    {
        "content": "Bob completed the API redesign project successfully",
        "entity": "bob",
        "timestamp": "2025-05-01T09:00:00Z",
        "metadata": {"person": "bob", "project": "api_redesign"},
    },
]


# ---------------------------------------------------------------------------
# Module-scoped enforcement gate (Decision #1)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _memory_available(nexus: NexusClient) -> None:
    """Skip memory tests if memory brick is not enabled on the server."""
    # Check /api/v2/features for "memory" in enabled_bricks
    try:
        feat_resp = nexus.features()
        if feat_resp.status_code == 200:
            feat = feat_resp.json()
            enabled = feat.get("enabled_bricks", [])
            if isinstance(enabled, list) and "memory" not in enabled:
                pytest.skip("Server does not have memory brick enabled")
    except (httpx.HTTPError, KeyError) as exc:
        logger.debug("Features endpoint unavailable (%s), trying probe", exc)

    # Fallback: try a minimal memory_store call to verify the endpoint exists
    probe_content = f"__memory_probe_{uuid.uuid4().hex[:8]}"
    probe_resp = nexus.memory_store(probe_content)
    if not probe_resp.ok:
        error_msg = probe_resp.error.message.lower() if probe_resp.error else ""
        if "not found" in error_msg or "unknown method" in error_msg:
            pytest.skip("Memory RPC methods not available on this server")

    # Clean up probe memory
    if probe_resp.ok and probe_resp.result:
        mid = probe_resp.result.get("memory_id")
        if mid:
            with contextlib.suppress(Exception):
                nexus.memory_delete(mid)


# ---------------------------------------------------------------------------
# Function-scoped factory with cleanup (Decision #8, mirrors create_tuple)
# ---------------------------------------------------------------------------


@pytest.fixture
def store_memory(nexus: NexusClient) -> Generator[StoreMemoryFn, None, None]:
    """Factory fixture: store memories via RPC with teardown cleanup.

    Usage:
        resp = store_memory("Q1 revenue was $10M", metadata={"quarter": "Q1"})
        assert resp.ok

    All memories created through this factory are automatically deleted after the test.
    """
    created_ids: list[str] = []

    def _store(
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
        zone: str | None = None,
        timestamp: str | None = None,
    ) -> RpcResponse:
        # UUID tag in metadata for xdist safety (Decision #14)
        # Content is stored verbatim so content-based assertions work.
        isolation_tag = uuid.uuid4().hex[:8]
        enriched_metadata = {**(metadata or {}), "_test_isolation": isolation_tag}
        resp = nexus.memory_store(
            content,
            metadata=enriched_metadata,
            zone=zone,
            timestamp=timestamp,
        )
        if resp.ok and resp.result:
            mid = resp.result.get("memory_id")
            if mid:
                created_ids.append(mid)
        return resp

    yield _store

    # Teardown: delete all memories created during this test
    for mid in reversed(created_ids):
        with contextlib.suppress(Exception):
            nexus.memory_delete(mid)


# ---------------------------------------------------------------------------
# Module-scoped read-only seed (Decision #8, #13)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def seeded_memories(
    nexus: NexusClient,
) -> Generator[list[dict[str, Any]], None, None]:
    """Pre-seed temporal memories for query tests. DO NOT MUTATE.

    Returns a list of dicts with memory_id and original data.
    Cleaned up after the module completes.
    """
    tag = uuid.uuid4().hex[:8]
    seeded: list[dict[str, Any]] = []

    for mem in TEMPORAL_MEMORIES:
        enriched_metadata = {**(mem.get("metadata") or {}), "_seed_tag": tag}
        resp = nexus.memory_store(
            mem["content"],
            metadata=enriched_metadata,
            timestamp=mem.get("timestamp"),
        )
        assert resp.ok, f"Failed to seed memory: {resp.error}"
        result = resp.result or {}
        seeded.append({
            "memory_id": result.get("memory_id"),
            "content": mem["content"],
            "timestamp": mem.get("timestamp"),
            "metadata": enriched_metadata,
            "tag": tag,
        })

    assert len(seeded) == len(TEMPORAL_MEMORIES), (
        f"Expected {len(TEMPORAL_MEMORIES)} seeded memories, got {len(seeded)}"
    )

    yield seeded

    # Cleanup: delete all seeded memories
    for mem_info in reversed(seeded):
        mid = mem_info.get("memory_id")
        if mid:
            with contextlib.suppress(Exception):
                nexus.memory_delete(mid)
