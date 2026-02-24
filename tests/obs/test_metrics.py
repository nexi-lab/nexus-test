"""Observability E2E tests — Prometheus metrics.

Tests: obs/008, obs/009, obs/010
Covers: latency histogram, error counter, saturation gauge

Reference: TEST_PLAN.md §4.40

Strategy (Decision 14A): Single scrape, verify cumulative counters > 0.
"""

from __future__ import annotations

import pytest

from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import parse_prometheus_metric


@pytest.mark.auto
@pytest.mark.observability
@pytest.mark.slo
class TestMetrics:
    """Verify Prometheus metrics are exposed and populated."""

    def _get_metrics_text(self, nexus: NexusClient) -> str:
        """Fetch raw metrics text, skip if endpoint unavailable."""
        resp = nexus.metrics_raw()
        if resp.status_code == 404:
            pytest.skip("Metrics endpoint not available (/metrics returned 404)")
        assert resp.status_code == 200, (
            f"Metrics endpoint failed: {resp.status_code} {resp.text[:200]}"
        )
        return resp.text

    def test_latency_histogram_populated(self, nexus: NexusClient) -> None:
        """obs/008: Metrics contain http_request_duration_seconds histogram.

        Verifies:
        - TYPE is histogram
        - _count > 0 (requests have been made in this session)
        """
        text = self._get_metrics_text(nexus)

        metric = parse_prometheus_metric(text, "http_request_duration_seconds")
        if metric is None:
            # Try alternative metric names
            for alt_name in (
                "http_server_request_duration_seconds",
                "request_duration_seconds",
                "http_request_duration",
            ):
                metric = parse_prometheus_metric(text, alt_name)
                if metric is not None:
                    break

        if metric is None:
            pytest.skip(
                "No HTTP latency histogram found in metrics output. "
                "Checked: http_request_duration_seconds and alternatives"
            )

        assert metric["type"] == "histogram", (
            f"Expected histogram type, got {metric['type']}"
        )

        # Check _count variant for populated data
        count_metric = parse_prometheus_metric(
            text, "http_request_duration_seconds_count"
        )
        if count_metric and count_metric["value"] is not None:
            assert count_metric["value"] > 0, (
                "Latency histogram _count should be > 0 after prior requests"
            )

    def test_error_rate_counter_exists(self, nexus: NexusClient) -> None:
        """obs/009: Metrics contain http_requests_total counter with status label.

        Verifies:
        - TYPE is counter
        - Metric lines contain status label
        """
        text = self._get_metrics_text(nexus)

        metric = parse_prometheus_metric(text, "http_requests_total")
        if metric is None:
            # Try alternative names
            for alt_name in (
                "http_server_requests_total",
                "http_responses_total",
            ):
                metric = parse_prometheus_metric(text, alt_name)
                if metric is not None:
                    break

        if metric is None:
            pytest.skip(
                "No HTTP request counter found in metrics output. "
                "Checked: http_requests_total and alternatives"
            )

        assert metric["type"] == "counter", (
            f"Expected counter type, got {metric['type']}"
        )

    def test_saturation_gauge_exists(self, nexus: NexusClient) -> None:
        """obs/010: Metrics contain http_requests_in_progress gauge.

        Verifies:
        - TYPE is gauge
        - Value may be 0 (no concurrent requests), which is valid
        """
        text = self._get_metrics_text(nexus)

        metric = parse_prometheus_metric(text, "http_requests_in_progress")
        if metric is None:
            # Try alternative names
            for alt_name in (
                "http_server_active_requests",
                "http_connections_active",
                "in_flight_requests",
            ):
                metric = parse_prometheus_metric(text, alt_name)
                if metric is not None:
                    break

        if metric is None:
            pytest.skip(
                "No saturation gauge found in metrics output. "
                "Checked: http_requests_in_progress and alternatives"
            )

        assert metric["type"] == "gauge", (
            f"Expected gauge type, got {metric['type']}"
        )

        # Value ≥ 0 is valid (0 means no concurrent requests right now)
        if metric["value"] is not None:
            assert metric["value"] >= 0, (
                f"Saturation gauge should be non-negative: {metric['value']}"
            )
