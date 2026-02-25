"""Generate markdown + JSON comparison report."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from benchmarks.memory.baselines import (
    COGNEE_BASELINES,
    CONSOLIDATION_BASELINES,
    GRAPHRAG_BENCH_BASELINES,
    KNOWLEDGE_GRAPH_BASELINES,
    PUBLISHED_BASELINES,
    REFLECTIVE_MEMORY_BASELINES,
    RLM_BASELINES,
)
from benchmarks.memory.models import BenchmarkResult


def generate_report(
    results: list[BenchmarkResult],
    output_dir: str | Path,
) -> Path:
    """Generate a markdown comparison report with JSON sidecar.

    Args:
        results: List of BenchmarkResult (one per dataset).
        output_dir: Directory to write report files.

    Returns:
        Path to the generated markdown report.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    md = _build_markdown(results)
    md_path = out / "report.md"
    md_path.write_text(md, encoding="utf-8")

    js = _build_json(results)
    json_path = out / "report.json"
    json_path.write_text(
        json.dumps(js, indent=2, default=str), encoding="utf-8"
    )

    return md_path


def _build_markdown(results: list[BenchmarkResult]) -> str:
    """Build the markdown report string."""
    lines: list[str] = []
    lines.append("# Nexus Memory Benchmark Report")
    lines.append("")
    lines.append(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    lines.append("")

    # Summary table
    lines.append("## Summary")
    lines.append("")

    # Determine which systems to show in columns
    all_systems = {"Nexus"}
    for ds_baselines in PUBLISHED_BASELINES.values():
        all_systems.update(ds_baselines.keys())
    system_order = ["Nexus"] + sorted(all_systems - {"Nexus"})

    header = "| Dataset | " + " | ".join(system_order) + " |"
    sep = "|" + "|".join("---------" for _ in range(len(system_order) + 1)) + "|"
    lines.append(header)
    lines.append(sep)

    result_map = {r.dataset: r for r in results}

    for ds in ("locomo", "longmemeval", "tofu"):
        row = [ds.capitalize()]
        for system in system_order:
            if system == "Nexus":
                r = result_map.get(ds)
                if r:
                    if ds == "tofu":
                        # Show forget/retain ROUGE-L
                        forget = r.by_category.get("forget")
                        retain = r.by_category.get("retain")
                        f_val = f"{forget.accuracy:.1f}%" if forget else "—"
                        r_val = f"{retain.accuracy:.1f}%" if retain else "—"
                        row.append(f"F:{f_val} R:{r_val}")
                    else:
                        row.append(f"{r.accuracy:.1f}%")
                else:
                    row.append("—")
            else:
                baselines = PUBLISHED_BASELINES.get(ds, {}).get(system, {})
                overall = baselines.get("overall")
                if overall is not None:
                    row.append(f"{overall:.1f}%")
                elif ds == "tofu":
                    fr = baselines.get("forget_rouge")
                    rr = baselines.get("retain_rouge")
                    if fr is not None:
                        row.append(f"F:{fr:.2f} R:{rr:.2f}")
                    else:
                        row.append("—")
                else:
                    row.append("—")
        lines.append("| " + " | ".join(row) + " |")

    lines.append("")

    # Per-dataset breakdown
    for r in results:
        lines.append(f"## {r.dataset.capitalize()} Breakdown")
        lines.append("")
        lines.append(f"- **Overall accuracy**: {r.accuracy:.1f}% ({r.correct}/{r.total_questions})")
        lines.append("")

        if r.by_category:
            lines.append("| Category | Accuracy | Correct/Total |")
            lines.append("|----------|----------|---------------|")
            for cat_name, cat in sorted(r.by_category.items()):
                lines.append(
                    f"| {cat_name} | {cat.accuracy:.1f}% | {cat.correct}/{cat.total} |"
                )
            lines.append("")

        # Published baselines comparison
        ds_baselines = PUBLISHED_BASELINES.get(r.dataset, {})
        if ds_baselines:
            lines.append("### vs. Published Baselines")
            lines.append("")
            lines.append("| System | Overall |")
            lines.append("|--------|---------|")
            lines.append(f"| **Nexus** | **{r.accuracy:.1f}%** |")
            for system, vals in sorted(ds_baselines.items()):
                overall = vals.get("overall")
                if overall is not None:
                    lines.append(f"| {system} | {overall:.1f}% |")
            lines.append("")

        # Latency stats
        if r.latency_stats:
            ls = r.latency_stats
            lines.append("### Latency")
            lines.append("")
            lines.append(f"- p50: {ls.p50_ms:.0f}ms")
            lines.append(f"- p95: {ls.p95_ms:.0f}ms")
            lines.append(f"- p99: {ls.p99_ms:.0f}ms")
            lines.append(f"- mean: {ls.mean_ms:.0f}ms")
            lines.append(f"- samples: {ls.count}")
            lines.append("")

    # Extended comparisons: consolidation, knowledge graph, reflective, GraphRAG-Bench
    lines.extend(_consolidation_section())
    lines.extend(_knowledge_graph_section())
    lines.extend(_reflective_memory_section())
    lines.extend(_graphrag_bench_section())
    lines.extend(_cognee_section())
    lines.extend(_rlm_section())

    return "\n".join(lines)


def _consolidation_section() -> list[str]:
    """Consolidation baselines (memory/004: ACE consolidation)."""
    lines = [
        "## Consolidation Baselines (memory/004)",
        "",
        "| System | F1 | Compression | Build Time (s) | Tokens/Query |",
        "|--------|----|-------------|-----------------|--------------|",
    ]
    for system, vals in CONSOLIDATION_BASELINES.items():
        f1 = vals.get("f1")
        cr = vals.get("compression_ratio")
        ct = vals.get("construction_time_s")
        tq = vals.get("tokens_per_query")
        f1_s = f"{f1:.1f}" if f1 else "—"
        cr_s = f"{cr:.0f}x" if cr else "—"
        ct_s = f"{ct:.0f}" if ct else "—"
        tq_s = f"{tq:.0f}" if tq else "—"
        lines.append(f"| {system} | {f1_s} | {cr_s} | {ct_s} | {tq_s} |")
    lines.append("")
    return lines


def _knowledge_graph_section() -> list[str]:
    """Knowledge graph / entity extraction baselines (memory/007, memory/013)."""
    lines = [
        "## Knowledge Graph Baselines (memory/007, memory/013)",
        "",
        "| System | Metric | Value |",
        "|--------|--------|-------|",
    ]
    for system, vals in KNOWLEDGE_GRAPH_BASELINES.items():
        for metric, val in vals.items():
            if isinstance(val, float) and val < 1.0:
                lines.append(f"| {system} | {metric} | {val:.3f} |")
            else:
                lines.append(f"| {system} | {metric} | {val} |")
    lines.append("")
    return lines


def _reflective_memory_section() -> list[str]:
    """Reflective / self-improving memory baselines (RLM, memory/022)."""
    lines = [
        "## Reflective Memory Baselines (RLM / memory/022)",
        "",
        "| System | Metric | Value |",
        "|--------|--------|-------|",
    ]
    for system, vals in REFLECTIVE_MEMORY_BASELINES.items():
        for metric, val in vals.items():
            if isinstance(val, float) and val < 1.0:
                lines.append(f"| {system} | {metric} | {val:.3f} |")
            else:
                lines.append(f"| {system} | {metric} | {val} |")
    lines.append("")
    return lines


def _graphrag_bench_section() -> list[str]:
    """GraphRAG-Bench Novel dataset comparison."""
    lines = [
        "## GraphRAG-Bench (Novel Dataset, 16 Disciplines)",
        "",
        "| System | Fact Retr. | Complex Reason. | Summary | Creative | Evid. Recall |",
        "|--------|-----------|-----------------|---------|----------|-------------|",
    ]
    for system, vals in GRAPHRAG_BENCH_BASELINES.items():
        lines.append(
            f"| {system} "
            f"| {vals.get('fact_retrieval', 0):.1f}% "
            f"| {vals.get('complex_reasoning', 0):.1f}% "
            f"| {vals.get('summarization', 0):.1f}% "
            f"| {vals.get('creative', 0):.1f}% "
            f"| {vals.get('evidence_recall', 0):.1f}% |"
        )
    lines.append("")
    return lines


def _cognee_section() -> list[str]:
    """Cognee HotPotQA cross-system comparison."""
    lines = [
        "## Cognee / HotPotQA Comparison",
        "",
        "| System | Human-like Correctness | DeepEval EM |",
        "|--------|----------------------|-------------|",
    ]
    for system, vals in COGNEE_BASELINES.items():
        hlc = vals.get("human_like_correctness", 0)
        dem = vals.get("deepeval_em", 0)
        lines.append(f"| {system} | {hlc:.2f} | {dem:.2f} |")
    lines.append("")
    return lines


def _rlm_section() -> list[str]:
    """RLM (Recursive Language Model) baselines — Nexus feature."""
    lines = [
        "## RLM Baselines (Recursive Language Model — Nexus feature)",
        "",
        "Nexus implements RLM at `POST /api/v2/rlm/infer` for recursive context",
        "decomposition (10M+ tokens). Reference: arXiv:2512.24601.",
        "",
        "| System | Metric | Value |",
        "|--------|--------|-------|",
    ]
    for system, vals in RLM_BASELINES.items():
        for metric, val in vals.items():
            lines.append(f"| {system} | {metric} | {val} |")
    lines.append("")
    return lines


def _build_json(results: list[BenchmarkResult]) -> dict[str, Any]:
    """Build JSON sidecar for programmatic consumption."""
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "results": [
            {
                "dataset": r.dataset,
                "accuracy": r.accuracy,
                "total_questions": r.total_questions,
                "correct": r.correct,
                "by_category": {
                    cat: {
                        "accuracy": cr.accuracy,
                        "correct": cr.correct,
                        "total": cr.total,
                    }
                    for cat, cr in r.by_category.items()
                },
                "latency": (
                    {
                        "p50_ms": r.latency_stats.p50_ms,
                        "p95_ms": r.latency_stats.p95_ms,
                        "p99_ms": r.latency_stats.p99_ms,
                        "mean_ms": r.latency_stats.mean_ms,
                        "count": r.latency_stats.count,
                    }
                    if r.latency_stats
                    else None
                ),
            }
            for r in results
        ],
        "baselines": PUBLISHED_BASELINES,
        "consolidation_baselines": CONSOLIDATION_BASELINES,
        "knowledge_graph_baselines": KNOWLEDGE_GRAPH_BASELINES,
        "reflective_memory_baselines": REFLECTIVE_MEMORY_BASELINES,
        "graphrag_bench_baselines": GRAPHRAG_BENCH_BASELINES,
        "cognee_baselines": COGNEE_BASELINES,
        "rlm_baselines": RLM_BASELINES,
    }
