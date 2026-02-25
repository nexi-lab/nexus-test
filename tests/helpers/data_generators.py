"""Test data generation and seeding helpers.

Provides utilities for:
    - Generating file trees of configurable depth/breadth
    - Loading benchmark data files from disk
    - Seeding a zone with test data for performance/stress tests
    - Collecting operation latencies for performance benchmarks
"""

from __future__ import annotations

import json
import logging
import statistics
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tests.helpers.api_client import NexusClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result models (immutable)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TreeStats:
    """Statistics from a generated file tree."""

    files_created: int
    dirs_created: int
    total_bytes: int
    base_path: str


@dataclass(frozen=True)
class SeedResult:
    """Result of a data seeding operation."""

    files_seeded: int
    zones_seeded: list[str]
    errors: list[str]


@dataclass(frozen=True)
class LatencyStats:
    """Percentile latency statistics (immutable)."""

    count: int
    min_ms: float
    max_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    mean_ms: float


class LatencyCollector:
    """Collect operation latencies via context manager.

    Usage:
        collector = LatencyCollector("memory_write")
        for _ in range(100):
            with collector.measure():
                do_operation()
        stats = collector.stats()
        assert stats.p95_ms < 50
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._samples_ns: list[int] = []

    @contextmanager
    def measure(self) -> Generator[None, None, None]:
        """Time a single operation using perf_counter_ns."""
        start = time.perf_counter_ns()
        yield
        self._samples_ns.append(time.perf_counter_ns() - start)

    def stats(self) -> LatencyStats:
        """Compute percentile statistics from collected samples.

        Raises:
            ValueError: If no samples have been collected.
        """
        if not self._samples_ns:
            raise ValueError(f"LatencyCollector({self.name!r}): no samples collected")

        ms = [ns / 1_000_000 for ns in self._samples_ns]
        sorted_ms = sorted(ms)
        n = len(sorted_ms)

        def _percentile(pct: float) -> float:
            idx = int(pct / 100 * (n - 1))
            return sorted_ms[min(idx, n - 1)]

        return LatencyStats(
            count=n,
            min_ms=sorted_ms[0],
            max_ms=sorted_ms[-1],
            p50_ms=_percentile(50),
            p95_ms=_percentile(95),
            p99_ms=_percentile(99),
            mean_ms=statistics.mean(ms),
        )


# ---------------------------------------------------------------------------
# Tree generation
# ---------------------------------------------------------------------------


def generate_tree(
    nexus: NexusClient,
    base_path: str,
    *,
    depth: int = 3,
    breadth: int = 3,
) -> TreeStats:
    """Generate a file tree of configurable depth and breadth.

    Creates directories ``depth`` levels deep, each with ``breadth``
    subdirectories and one file per directory.

    Args:
        nexus: NexusClient to use for file operations.
        base_path: Root path for the tree (e.g. /test-data/tree).
        depth: Number of directory levels to create.
        breadth: Number of subdirectories per level.

    Returns:
        TreeStats with counts of created files and directories.
    """
    files_created = 0
    dirs_created = 0
    total_bytes = 0

    def _build(path: str, current_depth: int) -> None:
        nonlocal files_created, dirs_created, total_bytes

        nexus.mkdir(path, parents=True)
        dirs_created += 1

        # Create a file in each directory
        content = f"file at depth {current_depth} in {path}"
        nexus.write_file(f"{path}/data.txt", content)
        files_created += 1
        total_bytes += len(content.encode())

        if current_depth < depth:
            for i in range(breadth):
                _build(f"{path}/dir_{i}", current_depth + 1)

    _build(base_path, 1)

    return TreeStats(
        files_created=files_created,
        dirs_created=dirs_created,
        total_bytes=total_bytes,
        base_path=base_path,
    )


# ---------------------------------------------------------------------------
# Benchmark file loading
# ---------------------------------------------------------------------------


def load_benchmark_files(benchmark_dir: str | Path) -> dict[str, list[Path]]:
    """Load benchmark data files from disk, grouped by extension.

    Scans the benchmark directory for files and groups them by suffix.
    Returns an empty dict if the directory doesn't exist or is empty.

    Args:
        benchmark_dir: Path to the benchmarks directory.

    Returns:
        Dict mapping extensions (e.g. ".txt", ".json") to lists of Paths.
    """
    root = Path(benchmark_dir).expanduser().resolve()
    if not root.is_dir():
        return {}

    result: dict[str, list[Path]] = {}
    for file_path in root.rglob("*"):
        if file_path.is_file():
            ext = file_path.suffix or ".noext"
            result.setdefault(ext, []).append(file_path)

    return result


# ---------------------------------------------------------------------------
# Zone seeding
# ---------------------------------------------------------------------------


def seed_herb_data(
    nexus: NexusClient,
    zone: str,
    benchmark_dir: str | Path,
    *,
    max_files: int = 100,
) -> SeedResult:
    """Seed a zone with benchmark data files.

    Reads files from ``benchmark_dir`` and writes them into the given zone
    under ``/seed-data/``. Stops after ``max_files``.

    Args:
        nexus: NexusClient to use for file operations.
        zone: Zone ID to seed data into.
        benchmark_dir: Path to benchmark data on disk.
        max_files: Maximum number of files to seed.

    Returns:
        SeedResult with count of seeded files and any errors.
    """
    files_by_ext = load_benchmark_files(benchmark_dir)
    if not files_by_ext:
        return SeedResult(files_seeded=0, zones_seeded=[], errors=[])

    seeded = 0
    errors: list[str] = []

    for _ext, paths in files_by_ext.items():
        for file_path in paths:
            if seeded >= max_files:
                break

            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                remote_path = f"/seed-data/{file_path.name}"
                resp = nexus.write_file(remote_path, content, zone=zone)
                if resp.ok:
                    seeded += 1
                else:
                    errors.append(f"Failed to write {remote_path}: {resp.error}")
            except Exception as exc:
                errors.append(f"Error reading {file_path}: {exc}")

        if seeded >= max_files:
            break

    return SeedResult(
        files_seeded=seeded,
        zones_seeded=[zone] if seeded > 0 else [],
        errors=errors,
    )


# ---------------------------------------------------------------------------
# HERB benchmark data loading
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HerbQuestion:
    """A single HERB QA benchmark question (immutable)."""

    product: str
    question: str
    ground_truth: tuple[str, ...] = ()
    citations: tuple[str, ...] = ()
    question_type: str = ""


def load_herb_qa(
    benchmark_dir: str | Path,
    *,
    max_questions: int = 0,
) -> list[HerbQuestion]:
    """Load HERB QA pairs from answerable.jsonl.

    If max_questions > 0, return only that many. Otherwise return all.
    """
    qa_path = Path(benchmark_dir).expanduser().resolve() / "qa" / "answerable.jsonl"
    if not qa_path.is_file():
        return []

    questions: list[HerbQuestion] = []
    with qa_path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning(
                    "Skipping malformed JSONL line %d in %s: %s", line_no, qa_path, exc
                )
                continue
            questions.append(
                HerbQuestion(
                    product=data.get("product", ""),
                    question=data.get("question", ""),
                    ground_truth=tuple(data.get("ground_truth", ())),
                    citations=tuple(data.get("citations", ())),
                    question_type=data.get("question_type", ""),
                )
            )
            if max_questions > 0 and len(questions) >= max_questions:
                break

    return questions


def seed_search_files(
    nexus: NexusClient,
    base_path: str,
    files: list[dict[str, str]],
    *,
    zone: str | None = None,
) -> list[dict[str, str]]:
    """Seed a set of files for search tests.

    Args:
        nexus: NexusClient to use.
        base_path: Directory prefix for all files.
        files: List of dicts with "name" and "content" keys.
        zone: Zone ID for triggering search refresh with zone-scoped paths.

    Returns:
        List of dicts with "path" and "content" for each successfully written file.
    """
    seeded: list[dict[str, str]] = []
    for f in files:
        path = f"{base_path}/{f['name']}"
        resp = nexus.write_file(path, f["content"])
        if resp.ok:
            seeded.append({"path": path, "content": f["content"]})
            # Trigger search index refresh with zone-scoped path
            nexus.search_refresh(path, zone=zone)
        else:
            logger.warning("Failed to seed %s: %s", path, resp.error)
    return seeded
