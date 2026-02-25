"""Aggregate judge results into accuracy metrics."""

from __future__ import annotations

import statistics
from collections import defaultdict

from benchmarks.memory.models import (
    Answer,
    BenchmarkResult,
    CategoryResult,
    JudgeResult,
    LatencyStats,
    Question,
)


def compute_metrics(
    dataset: str,
    questions: list[Question],
    results: list[JudgeResult],
    answers: list[Answer] | None = None,
    *,
    timestamp: str = "",
) -> BenchmarkResult:
    """Aggregate judge results into accuracy per category and overall.

    Args:
        dataset: Dataset name (locomo, longmemeval, tofu).
        questions: Original questions (for category mapping).
        results: Judge results to aggregate.
        answers: Optional answers (for latency stats).
        timestamp: ISO timestamp for the result.

    Returns:
        BenchmarkResult with overall and per-category accuracy.
    """
    question_map = {q.id: q for q in questions}
    result_map = {r.question_id: r for r in results}

    # Per-category tracking
    cat_correct: dict[str, int] = defaultdict(int)
    cat_total: dict[str, int] = defaultdict(int)

    total_correct = 0
    total_count = 0

    for q_id, judge in result_map.items():
        question = question_map.get(q_id)
        category = question.category if question else "unknown"

        cat_total[category] += 1
        total_count += 1

        if judge.correct:
            cat_correct[category] += 1
            total_correct += 1

    # Build category results
    by_category: dict[str, CategoryResult] = {}
    for cat in sorted(cat_total.keys()):
        total = cat_total[cat]
        correct = cat_correct[cat]
        by_category[cat] = CategoryResult(
            category=cat,
            total=total,
            correct=correct,
            accuracy=correct / total * 100 if total > 0 else 0.0,
        )

    # Latency stats
    latency_stats = _compute_latency(answers) if answers else None

    overall_accuracy = total_correct / total_count * 100 if total_count > 0 else 0.0

    return BenchmarkResult(
        dataset=dataset,
        total_questions=total_count,
        correct=total_correct,
        accuracy=overall_accuracy,
        by_category=by_category,
        latency_stats=latency_stats,
        timestamp=timestamp,
    )


def _compute_latency(answers: list[Answer]) -> LatencyStats | None:
    """Compute latency percentile stats from answer latencies."""
    latencies = [a.latency_ms for a in answers if a.latency_ms > 0]
    if not latencies:
        return None

    sorted_ms = sorted(latencies)
    n = len(sorted_ms)

    def _pct(p: float) -> float:
        idx = int(p / 100 * (n - 1))
        return sorted_ms[min(idx, n - 1)]

    return LatencyStats(
        count=n,
        min_ms=sorted_ms[0],
        max_ms=sorted_ms[-1],
        p50_ms=_pct(50),
        p95_ms=_pct(95),
        p99_ms=_pct(99),
        mean_ms=statistics.mean(latencies),
    )
