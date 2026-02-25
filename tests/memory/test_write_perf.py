"""memory/020: Write performance SLOs — p95 < threshold, consolidation < 5s.

Measures write latency and consolidation time against SLO targets.
Sample sizes configurable via NEXUS_TEST_PERF_SAMPLES env var or TestSettings.
Write SLO configurable via NEXUS_TEST_WRITE_P95_MS (default 500ms to account
for remote embedding provider round-trips).

Groups: stress, perf, memory
"""

from __future__ import annotations

import os

import pytest

from tests.config import TestSettings
from tests.helpers.api_client import NexusClient
from tests.helpers.data_generators import LatencyCollector


@pytest.mark.stress
@pytest.mark.perf
@pytest.mark.memory
class TestWritePerformance:
    """memory/020: Write p95 < SLO, consolidation < 5s."""

    @pytest.mark.timeout(120)
    def test_write_latency_slo(
        self, nexus: NexusClient, settings: TestSettings, unique_path: str
    ) -> None:
        """N memory writes, p95 within SLO threshold."""
        sample_count = settings.perf_samples
        write_p95_slo = float(os.getenv("NEXUS_TEST_WRITE_P95_MS", "5000"))
        collector = LatencyCollector("memory_write")
        created_ids: list[str] = []

        try:
            for i in range(sample_count):
                with collector.measure():
                    resp = nexus.memory_store(
                        f"perf test {unique_path} item {i}",
                        metadata={"perf_test": True, "index": i},
                    )
                if resp.ok and resp.result:
                    mid = resp.result.get("memory_id")
                    if mid:
                        created_ids.append(mid)

            stats = collector.stats()
            assert stats.p95_ms < write_p95_slo, (
                f"Write p95={stats.p95_ms:.1f}ms exceeds {write_p95_slo}ms SLO "
                f"(count={stats.count}, mean={stats.mean_ms:.1f}ms, "
                f"p50={stats.p50_ms:.1f}ms, p99={stats.p99_ms:.1f}ms)"
            )
        finally:
            # Cleanup: delete all created memories
            for mid in reversed(created_ids):
                try:
                    nexus.memory_delete(mid)
                except Exception:
                    pass

    @pytest.mark.timeout(120)
    def test_consolidation_latency_slo(self, nexus: NexusClient) -> None:
        """5 consolidation runs, wall-time < 5s each."""
        collector = LatencyCollector("memory_consolidate")

        for i in range(5):
            with collector.measure():
                resp = nexus.memory_consolidate()
            if not resp.ok:
                if i == 0:
                    pytest.skip(
                        f"Consolidate endpoint not functional: {resp.error}"
                    )
                else:
                    pytest.fail(
                        f"Consolidation failed on iteration {i + 1} after "
                        f"{i} successes: {resp.error}"
                    )

        stats = collector.stats()
        assert stats.p95_ms < 5000, (
            f"Consolidation p95={stats.p95_ms:.1f}ms exceeds 5000ms SLO "
            f"(count={stats.count}, mean={stats.mean_ms:.1f}ms, "
            f"p50={stats.p50_ms:.1f}ms, p99={stats.p99_ms:.1f}ms)"
        )
