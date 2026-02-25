"""Benchmark configuration loaded from environment variables.

All settings have sensible defaults for local development.
Override via environment variables (no prefix required).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class BenchmarkConfig:
    """Configuration for memory benchmark evaluation."""

    # Nexus connection (override via NEXUS_URL / NEXUS_API_KEY env vars)
    nexus_url: str = "http://localhost:2026"
    nexus_api_key: str = ""
    nexus_zone: str = "corp"

    # OpenAI LLM settings (for answer generation + judging)
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    judge_model: str = "gpt-4o"
    answer_model: str = "gpt-4o-mini"

    # Paths
    data_dir: str = "benchmarks/data"
    results_dir: str = "benchmarks/results"

    # Dataset selection
    datasets: tuple[str, ...] = ("locomo", "longmemeval", "tofu")
    locomo_subset: str = "all"  # "all" or specific conversation ID
    longmemeval_split: str = "S"  # "S" (small/CI) or "full"
    tofu_forget_pct: int = 10  # percentage of profiles to forget

    # Query settings
    memory_search_limit: int = 10
    answer_max_tokens: int = 100
    judge_max_tokens: int = 200

    @classmethod
    def from_env(cls) -> BenchmarkConfig:
        """Create config from environment variables."""
        return cls(
            nexus_url=os.environ.get("NEXUS_URL", "http://localhost:2026"),
            nexus_api_key=os.environ.get(
                "NEXUS_API_KEY", "sk-test-federation-e2e-admin-key"
            ),
            nexus_zone=os.environ.get("NEXUS_ZONE", "corp"),
            openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
            openai_base_url=os.environ.get(
                "OPENAI_BASE_URL", cls.openai_base_url
            ),
            judge_model=os.environ.get("BENCH_JUDGE_MODEL", cls.judge_model),
            answer_model=os.environ.get(
                "BENCH_ANSWER_MODEL", cls.answer_model
            ),
            data_dir=os.environ.get("BENCH_DATA_DIR", cls.data_dir),
            results_dir=os.environ.get("BENCH_RESULTS_DIR", cls.results_dir),
            datasets=tuple(
                os.environ.get("BENCH_DATASETS", "locomo,longmemeval,tofu")
                .split(",")
            ),
            locomo_subset=os.environ.get("BENCH_LOCOMO_SUBSET", cls.locomo_subset),
            longmemeval_split=os.environ.get(
                "BENCH_LONGMEMEVAL_SPLIT", cls.longmemeval_split
            ),
            tofu_forget_pct=int(
                os.environ.get("BENCH_TOFU_FORGET_PCT", str(cls.tofu_forget_pct))
            ),
        )
