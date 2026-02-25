"""Immutable data models for benchmark evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Question:
    """A benchmark evaluation question."""

    id: str
    dataset: str  # "locomo", "longmemeval", "tofu"
    category: str  # e.g. "single-hop", "temporal", "forget"
    text: str
    gold_answer: str
    conversation_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Answer:
    """Generated answer for a benchmark question."""

    question_id: str
    retrieved_contexts: tuple[str, ...]  # memory contents retrieved
    generated_answer: str
    latency_ms: float


@dataclass(frozen=True)
class JudgeResult:
    """LLM judge evaluation result."""

    question_id: str
    correct: bool
    score: float  # 0.0-1.0 (binary, ROUGE-L, or F1)
    judge_explanation: str


@dataclass(frozen=True)
class LatencyStats:
    """Latency percentile statistics."""

    count: int
    min_ms: float
    max_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    mean_ms: float


@dataclass(frozen=True)
class CategoryResult:
    """Accuracy result for a single category."""

    category: str
    total: int
    correct: int
    accuracy: float


@dataclass(frozen=True)
class BenchmarkResult:
    """Aggregated benchmark result for one dataset."""

    dataset: str
    total_questions: int
    correct: int
    accuracy: float
    by_category: dict[str, CategoryResult]
    latency_stats: LatencyStats | None = None
    timestamp: str = ""
