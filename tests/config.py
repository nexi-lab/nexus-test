"""Test configuration via Pydantic Settings.

Loads from environment variables (prefix: NEXUS_TEST_) or .env.test file.
Fail-fast on missing required config.

Usage:
    settings = TestSettings()          # Auto-loads from env / .env.test
    settings = TestSettings(url="...")  # Override in tests
"""

from __future__ import annotations

import os

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TestSettings(BaseSettings):
    """Configuration for the E2E test suite.

    All values have sensible defaults for the local docker-compose dev cluster.
    Override via environment variables with NEXUS_TEST_ prefix or .env.test file.

    WARNING: Default credentials are for LOCAL DEV ONLY. Never point at production.
    """

    model_config = SettingsConfigDict(
        env_prefix="NEXUS_TEST_",
        env_file=".env.test",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Cluster endpoints (local dev defaults) ---
    url: str = "http://localhost:2026"
    url_follower: str = "http://localhost:2027"

    # --- Authentication (local dev default — override via NEXUS_TEST_API_KEY) ---
    api_key: str = "sk-test-federation-e2e-admin-key"

    # --- Zones ---
    zone: str = "corp"
    scratch_zone: str = "corp-eng"
    federation_zones: str = "corp,corp-eng,corp-sales,family"

    # --- Federation nodes ---
    fed_node_1: str = "http://localhost:2026"
    fed_node_2: str = "http://localhost:2027"

    # --- Infrastructure (local dev defaults) ---
    database_url: str = "postgresql://postgres:nexus@localhost:5432/nexus"
    dragonfly_url: str = "redis://localhost:6379"

    # --- Nexus repo (for Testcontainers compose file reference) ---
    nexus_repo_dir: str = "~/nexus"

    # --- Test data ---
    benchmark_dir: str = "~/nexus/benchmarks"
    perf_data_dir: str = "/tmp/nexus_perf_data"

    # --- Memory benchmark data ---
    memory_benchmark_dir: str = "~/nexus/benchmarks/memory"
    perf_samples: int = 100  # Override via NEXUS_TEST_PERF_SAMPLES

    # --- HERB benchmark data ---
    herb_sample_size: int = 20  # Override via NEXUS_TEST_HERB_SAMPLE_SIZE
    herb_benchmark_dir: str = "~/nexus/benchmarks/herb"  # Override: NEXUS_TEST_HERB_BENCHMARK_DIR

    # --- Timeouts (seconds) ---
    request_timeout: float = 30.0
    connect_timeout: float = 10.0
    cluster_wait_timeout: float = 120.0

    # --- Parallel execution ---
    worker_prefix: str = "test"

    @field_validator("url", "url_follower", "fed_node_1", "fed_node_2")
    @classmethod
    def reject_production_urls(cls, v: str) -> str:
        """Safety check: refuse to run tests against non-local URLs."""
        lower = v.lower()
        if any(keyword in lower for keyword in ("prod", "staging", ".cloud", ".io")):
            raise ValueError(f"Refusing to run tests against non-local URL: {v}")
        return v

    @field_validator(
        "nexus_repo_dir", "benchmark_dir", "memory_benchmark_dir", "herb_benchmark_dir",
    )
    @classmethod
    def expand_home(cls, v: str) -> str:
        """Expand ~ in paths."""
        if v.startswith("~"):
            return os.path.expanduser(v)
        return v

    @property
    def federation_zone_list(self) -> list[str]:
        """Parse comma-separated federation zones into a list."""
        return [z.strip() for z in self.federation_zones.split(",") if z.strip()]
