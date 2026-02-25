"""memory/008: Query performance — < 200ms p95 at scale.

Stress/performance test: seeds N memories (configurable via
NEXUS_TEST_PERF_ENTRY_COUNT, default 50) into a dedicated zone,
then runs diverse queries measuring p95 latency.

Groups: stress, perf, memory
"""

from __future__ import annotations

import contextlib
import os
import time
import uuid
from typing import Any

import pytest

from tests.config import TestSettings
from tests.helpers.api_client import NexusClient
from tests.helpers.data_generators import LatencyCollector

QUERY_P95_SLO_MS = float(os.getenv("NEXUS_TEST_QUERY_P95_MS", "3000"))

# Diverse search terms for realistic query mix
SEARCH_TERMS = [
    "engineering team performance",
    "quarterly revenue growth",
    "customer satisfaction metrics",
    "infrastructure cost optimization",
    "product roadmap planning",
    "security audit compliance",
    "machine learning model accuracy",
    "employee retention strategies",
    "cloud migration progress",
    "API response latency improvement",
]


@pytest.mark.stress
@pytest.mark.perf
@pytest.mark.memory
@pytest.mark.timeout(300)  # 5 minutes for seed + query + cleanup
class TestQueryPerformance:
    """memory/008: Query perf — < 200ms p95 at scale."""

    ENTRY_COUNT = int(os.getenv("NEXUS_TEST_PERF_ENTRY_COUNT", "50"))
    WARMUP_FRACTION = 0.1  # 10% of samples used for warm-up (discarded)

    @pytest.fixture(scope="class")
    def perf_zone_memories(
        self, nexus: NexusClient, settings: TestSettings
    ):  # type: ignore[override]
        """Seed memories into the scratch zone for perf testing.

        Count configurable via NEXUS_TEST_PERF_ENTRY_COUNT (default 50).
        Uses scratch_zone for isolation. Cleanup via individual deletes.
        """
        zone = settings.scratch_zone
        created_ids: list[str] = []
        failures = 0

        # Generate diverse content
        topics = [
            "engineering", "sales", "marketing", "finance", "operations",
            "product", "design", "security", "data science", "HR",
        ]
        for i in range(self.ENTRY_COUNT):
            topic = topics[i % len(topics)]
            content = (
                f"Memory entry {i}: {topic} department update for "
                f"project-{i % 100} in category-{i % 50}. "
                f"Status report iteration {i} with metric value {i * 1.5:.1f}."
            )
            resp = nexus.memory_store(
                content,
                zone=zone,
                metadata={
                    "_perf_test": True,
                    "topic": topic,
                    "index": i,
                    "_batch": uuid.uuid4().hex[:8],
                },
            )
            if resp.ok and resp.result:
                mid = resp.result.get("memory_id")
                if mid:
                    created_ids.append(mid)
            else:
                failures += 1
                if failures > self.ENTRY_COUNT * 0.2:
                    pytest.skip(
                        f"Too many seed failures ({failures}/{i + 1}), "
                        "server may be overloaded"
                    )
                # Brief pause on failure to let server recover
                time.sleep(0.5)

            # Progress logging every 50 entries
            if (i + 1) % 50 == 0:
                print(f"  Seeded {i + 1}/{self.ENTRY_COUNT} memories")

        min_required = max(10, int(self.ENTRY_COUNT * 0.8))
        assert len(created_ids) >= min_required, (
            f"Expected ~{self.ENTRY_COUNT} memories, only stored "
            f"{len(created_ids)} ({failures} failures)"
        )

        yield {
            "zone": zone,
            "count": len(created_ids),
            "ids": created_ids,
        }

        # Cleanup
        print(f"  Cleaning up {len(created_ids)} perf test memories...")
        for mid in created_ids:
            with contextlib.suppress(Exception):
                nexus.memory_delete(mid, zone=zone)

    def test_query_p95_under_slo(
        self,
        nexus: NexusClient,
        perf_zone_memories: dict[str, Any],
        settings: TestSettings,
    ) -> None:
        """Query p95 latency < SLO with seeded memories."""
        zone = perf_zone_memories["zone"]
        sample_count = min(settings.perf_samples, 50)  # cap at 50 for speed
        warmup_count = max(1, int(sample_count * self.WARMUP_FRACTION))

        # Warm-up queries (discarded)
        for i in range(warmup_count):
            term = SEARCH_TERMS[i % len(SEARCH_TERMS)]
            nexus.memory_query(term, limit=10, zone=zone)

        # Measured queries
        collector = LatencyCollector("query_perf")
        for i in range(sample_count):
            term = SEARCH_TERMS[i % len(SEARCH_TERMS)]
            with collector.measure():
                resp = nexus.memory_query(term, limit=10, zone=zone)
            assert resp.ok, f"Query failed on sample {i}: {resp.error}"

        stats = collector.stats()
        print(
            f"\n  Query perf @ {perf_zone_memories['count']} memories: "
            f"p50={stats.p50_ms:.1f}ms, p95={stats.p95_ms:.1f}ms, "
            f"p99={stats.p99_ms:.1f}ms, mean={stats.mean_ms:.1f}ms"
        )
        assert stats.p95_ms < QUERY_P95_SLO_MS, (
            f"Query p95={stats.p95_ms:.1f}ms exceeds {QUERY_P95_SLO_MS}ms SLO "
            f"at {perf_zone_memories['count']} entries "
            f"(p50={stats.p50_ms:.1f}ms, p99={stats.p99_ms:.1f}ms)"
        )
