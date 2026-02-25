"""Memory test fixtures — enforcement gate, synthetic data, factories.

Fixture scoping:
    module:  _memory_available (auto-skip gate), seeded_memories (read-only),
             _enrichment_available (auto-skip gate for enrichment tests)
    class:   consolidation_memories, herb_memories, perf_zone
    function: store_memory (factory with per-test cleanup)
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
import uuid
from collections.abc import Callable, Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.helpers.api_client import EnrichmentFlags, NexusClient, RpcResponse
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
# Search availability probe (memory/002)
# ---------------------------------------------------------------------------


def _check_search(nexus: NexusClient) -> bool:
    """Probe whether the search endpoint returns results.

    Returns False if the server returns an error (e.g. SQL syntax bug in
    _keyword_search when ReBAC permissions are enabled) or if results are
    empty due to the search endpoint using global context instead of
    per-request auth context for permission checks.
    """
    # Store a probe memory, search for it, then clean up
    tag = uuid.uuid4().hex[:8]
    probe_content = f"search probe test {tag}"
    store_resp = nexus.memory_store(probe_content)
    if not store_resp.ok:
        return False

    mid = (store_resp.result or {}).get("memory_id")
    try:
        resp = nexus.memory_search(f"search probe {tag}")
        if not resp.ok:
            return False
        # Check that search actually returns results (not just 200 with empty list)
        raw = resp.result
        results = (
            raw.get("results", []) if isinstance(raw, dict)
            else raw if isinstance(raw, list)
            else []
        )
        return len(results) > 0
    finally:
        if mid:
            with contextlib.suppress(Exception):
                nexus.memory_delete(mid)


@pytest.fixture(scope="module")
def search_available(nexus: NexusClient) -> bool:
    """Check if the memory search endpoint is functional. Returns bool.

    The search endpoint has a known SQL syntax bug in _keyword_search
    that crashes when ReBAC permissions are enabled.
    """
    return _check_search(nexus)


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
        enrichment: EnrichmentFlags | None = None,
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
            enrichment=enrichment,
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


# ---------------------------------------------------------------------------
# Enrichment availability probe (for memory/007, 012, 013)
# ---------------------------------------------------------------------------

ENRICHMENT_PROBE_CONTENT = "Alice manages the Nexus infrastructure project at Acme Corp"


def _check_enrichment(nexus: NexusClient) -> bool:
    """Probe whether server enrichment pipeline is functional.

    Stores a test memory with entity extraction enabled,
    then checks if enrichment fields are populated.
    """
    probe_resp = nexus.memory_store(
        ENRICHMENT_PROBE_CONTENT,
        enrichment=EnrichmentFlags(extract_entities=True),
        metadata={"_enrichment_probe": True},
    )
    if not probe_resp.ok:
        return False

    mid = (probe_resp.result or {}).get("memory_id")
    if not mid:
        return False

    try:
        # Poll for enrichment with exponential backoff (max 10s)
        # Note: the GET endpoint returns entity_types/person_refs (not entities_json)
        # So we use the query endpoint which includes those fields.
        enriched = False
        for attempt in range(8):
            time.sleep(min(0.5 * (2 ** attempt), 3.0))
            # Use query to find our probe memory — query response includes enrichment fields
            query_resp = nexus.memory_query(ENRICHMENT_PROBE_CONTENT[:30])
            if query_resp.ok and query_resp.result is not None:
                # Handle both list (query fallback) and dict (search response)
                if isinstance(query_resp.result, dict):
                    _results = query_resp.result.get("results", [])
                elif isinstance(query_resp.result, list):
                    _results = query_resp.result
                else:
                    _results = []
                for mem in _results:
                    if mem.get("memory_id") == mid:
                        entity_types = mem.get("entity_types")
                        if entity_types:
                            enriched = True
                            break
                if enriched:
                    break
            # Fallback: also check GET in case API changes
            get_resp = nexus.memory_get(mid)
            if get_resp.ok and isinstance(get_resp.result, dict):
                entities = (
                    get_resp.result.get("entities_json")
                    or get_resp.result.get("entities")
                    or get_resp.result.get("entity_types")
                )
                if entities:
                    enriched = True
                    break
        return enriched
    finally:
        with contextlib.suppress(Exception):
            nexus.memory_delete(mid)


@pytest.fixture(scope="module")
def enrichment_available(nexus: NexusClient) -> bool:
    """Check if enrichment pipeline is available. Returns bool, does NOT skip.

    Tests that require enrichment should use this to conditionally skip.
    """
    return _check_enrichment(nexus)


# ---------------------------------------------------------------------------
# Consolidation availability probe (memory/004)
# ---------------------------------------------------------------------------


def _check_consolidation(nexus: NexusClient) -> bool:
    """Probe whether the consolidation endpoint is functional.

    Returns False if the server returns 500 (e.g. no LLM provider configured).
    """
    resp = nexus.memory_consolidate()
    return resp.ok


@pytest.fixture(scope="module")
def consolidation_available(nexus: NexusClient) -> bool:
    """Check if consolidation engine is available. Returns bool, does NOT skip.

    Tests that require consolidation should use this to conditionally skip.
    The consolidation engine needs an LLM provider (e.g. ANTHROPIC_API_KEY).
    """
    return _check_consolidation(nexus)



# ---------------------------------------------------------------------------
# Consolidation test data (memory/004, class-scoped)
# ---------------------------------------------------------------------------

CONSOLIDATION_KEY_FACTS = [
    "ACME Corp annual revenue reached $42M in fiscal year 2025",
    "Project Neptune launched successfully on March 15th 2025",
    "The engineering team grew from 50 to 85 engineers in Q2",
    "Customer satisfaction score improved to 4.8 out of 5.0",
    "New Tokyo office opened with 30 employees in April 2025",
]


def _generate_consolidation_memories(count: int) -> list[dict[str, str]]:
    """Generate `count` memories including key facts for consolidation testing."""
    memories = []

    # Embed key facts in the first N memories
    for fact in CONSOLIDATION_KEY_FACTS:
        memories.append({
            "content": fact,
            "metadata_category": "key_fact",
        })

    # Fill remaining with supporting details
    supporting_details = [
        "The marketing team ran 12 campaigns with average 3.2% conversion",
        "Infrastructure costs decreased by 15% after cloud migration",
        "Three new product lines were introduced in the analytics segment",
        "Employee retention rate improved to 94% from 88% last year",
        "The DevOps team reduced deployment time from 45 to 12 minutes",
        "Sales pipeline grew 35% quarter-over-quarter in Q3",
        "The security audit found zero critical vulnerabilities",
        "Customer onboarding time reduced from 14 days to 3 days",
        "Mobile app downloads exceeded 500K in the first month",
        "Partner ecosystem expanded to include 45 integration partners",
        "Data platform processes 2.5TB daily with 99.99% uptime",
        "Support team resolved 95% of tickets within SLA",
        "R&D investment increased to 22% of revenue",
        "Supply chain optimization saved $3.2M annually",
        "New compliance certifications: SOC2 Type II and ISO 27001",
        "Quarterly all-hands meeting attendance reached 98%",
        "Open source contributions increased by 200%",
        "Customer churn reduced to 2.1% monthly",
        "API response time p95 improved from 450ms to 120ms",
        "The board approved the Series C funding round of $80M",
        "Engineering blog posts generated 150K monthly views",
        "Internal hackathon produced 8 new feature prototypes",
        "Database query performance improved 3x after indexing",
        "CI/CD pipeline now runs 2400 tests in under 8 minutes",
        "Cross-team collaboration score increased to 4.5/5.0",
        "Machine learning model accuracy reached 96.2%",
        "Code review turnaround time averaged 4 hours",
        "User documentation coverage expanded to 95% of features",
        "Load testing confirmed 10K concurrent user support",
        "Monthly active users grew from 25K to 45K",
        "A/B testing framework processed 50M events per day",
        "Internationalization support added for 12 new languages",
        "Edge caching reduced global latency by 65%",
        "Monitoring alerts reduced false positives by 80%",
        "Team retrospectives identified 45 process improvements",
        "Knowledge base articles grew to 1200 entries",
        "Automated testing coverage reached 87% across all services",
        "Container orchestration migrated to Kubernetes 1.28",
        "GraphQL API adoption reached 60% of frontend teams",
        "Feature flag system manages 250 active experiments",
        "Service mesh reduced inter-service latency by 40%",
        "Data warehouse query time improved from 30s to 2s",
        "Incident response mean time to resolution: 23 minutes",
        "Technical debt sprint eliminated 150 legacy issues",
        "API versioning strategy adopted with zero breaking changes",
    ]

    for i in range(count - len(CONSOLIDATION_KEY_FACTS)):
        detail = supporting_details[i % len(supporting_details)]
        memories.append({
            "content": detail,
            "metadata_category": "supporting",
        })

    return memories[:count]


# ---------------------------------------------------------------------------
# HERB enterprise-context data loader (memory/003)
# ---------------------------------------------------------------------------

HERB_DATA_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "benchmarks"
    / "herb"
    / "enterprise-context"
)


def load_herb_records(max_records: int = 100) -> list[dict[str, Any]]:
    """Load HERB enterprise-context records from JSONL files.

    Returns at most `max_records` records combined from all files.
    """
    records: list[dict[str, Any]] = []
    for jsonl_file in sorted(HERB_DATA_DIR.glob("*.jsonl")):
        with jsonl_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
                    if len(records) >= max_records:
                        return records
    return records


# ---------------------------------------------------------------------------
# Wait-for-enrichment helper (Decision #8A — poll with retry)
# ---------------------------------------------------------------------------


def wait_for_enrichment(
    nexus: NexusClient,
    memory_id: str,
    *,
    field: str = "entity_types",
    timeout_seconds: float = 10.0,
    zone: str | None = None,
    content_hint: str | None = None,
) -> dict[str, Any] | None:
    """Poll until enrichment field is populated on a memory, or timeout.

    The GET /memories/{id} endpoint does NOT return enrichment fields
    (entities_json, entity_types, relationships_json, etc.).
    The query endpoint DOES return them. So we use both approaches:
    1. GET endpoint for fields like 'content' that it does return.
    2. Query endpoint for enrichment-specific fields.

    Args:
        nexus: API client.
        memory_id: Memory to check.
        field: Enrichment field to wait for (default: entity_types).
        timeout_seconds: Max wait time.
        zone: Optional zone.
        content_hint: Text to use when querying (helps find the memory via query).

    Returns:
        The full memory dict if enrichment found, None if timeout.
    """
    # Fields available from the GET endpoint
    _get_fields = {
        "content", "state", "memory_type", "importance", "importance_effective",
        "temporal_stability", "stability_confidence",
    }
    # Fields only available from the query endpoint
    _query_fields = {
        "entity_types", "person_refs", "entities_json", "relationships_json",
        "relationship_count", "temporal_refs_json",
    }

    use_query = field in _query_fields
    deadline = time.monotonic() + timeout_seconds
    delay = 0.5

    while time.monotonic() < deadline:
        # Try GET endpoint first (works for basic fields)
        resp = nexus.memory_get(memory_id, zone=zone)
        if resp.ok and isinstance(resp.result, dict):
            value = resp.result.get(field)
            if value:
                return resp.result

        # For enrichment fields, also try the query endpoint
        if use_query:
            # Use content_hint or a generic query to find the memory
            query_text = content_hint or memory_id
            query_resp = nexus.memory_query(query_text[:60], limit=20, zone=zone)
            if query_resp.ok and query_resp.result is not None:
                if isinstance(query_resp.result, dict):
                    _qr = query_resp.result.get("results", [])
                elif isinstance(query_resp.result, list):
                    _qr = query_resp.result
                else:
                    _qr = []
                for mem in _qr:
                    if mem.get("memory_id") == memory_id:
                        value = mem.get(field)
                        if value:
                            return mem

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(delay, remaining))
        delay = min(delay * 2, 3.0)
    return None
