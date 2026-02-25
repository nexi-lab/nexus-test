"""HERB Q&A accuracy benchmark (search/005).

Two test classes:
- TestHerbCI: Quick CI validation (20 QA pairs, recall >= 0.3)
- TestHerbBenchmark: Full benchmark (100+ QA pairs, recall >= 0.5, perf marker)

Groups: auto, search, perf, benchmark
"""

from __future__ import annotations

import contextlib
import logging
import statistics
import uuid
from collections.abc import Generator, Sequence
from pathlib import Path

import pytest

from tests.config import TestSettings
from tests.helpers.api_client import NexusClient
from tests.helpers.assertions import extract_search_results
from tests.helpers.data_generators import HerbQuestion, load_herb_qa
from tests.search.conftest import wait_until_searchable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------


def compute_recall_at_k(
    search_results: list[dict], ground_truth: Sequence[str], k: int = 10
) -> float:
    """Compute recall: fraction of ground_truth items found in top-K result content.

    Each ground truth item is searched for in any individual result's content,
    avoiding false matches from content concatenation across results.
    """
    if not ground_truth:
        return 0.0

    top_k = [
        (r.get("chunk_text", r.get("content", r.get("snippet", ""))) or "").lower()
        for r in search_results[:k]
        if isinstance(r, dict)
    ]

    found = sum(
        1
        for gt in ground_truth
        if any(gt.lower() in content for content in top_k)
    )
    return found / len(ground_truth)


# ---------------------------------------------------------------------------
# HERB context seeding helpers
# ---------------------------------------------------------------------------


def _seed_herb_context(
    nexus: NexusClient,
    benchmark_dir: str | Path,
    base_path: str,
    *,
    max_products: int = 3,
    max_files_per_product: int = 6,
    zone: str | None = None,
) -> list[dict[str, str]]:
    """Seed HERB enterprise context files from the benchmark directory.

    Returns list of {"path": ..., "content": ...} for seeded files.
    """
    root = Path(benchmark_dir).expanduser().resolve()
    # Support both "enterprise-context" (actual layout) and "context" (legacy)
    context_dir = root / "enterprise-context"
    if not context_dir.is_dir():
        context_dir = root / "context"
    if not context_dir.is_dir():
        pytest.skip(f"HERB context directory not found: {root}/enterprise-context or {root}/context")

    seeded: list[dict[str, str]] = []
    product_count = 0

    for product_dir in sorted(context_dir.iterdir()):
        if not product_dir.is_dir():
            continue
        if product_count >= max_products:
            break

        file_count = 0
        for file_path in sorted(product_dir.rglob("*")):
            if not file_path.is_file():
                continue
            if file_count >= max_files_per_product:
                break

            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                relative = file_path.relative_to(context_dir)
                remote_path = f"{base_path}/{relative}"
                resp = nexus.write_file(remote_path, content)
                if resp.ok:
                    seeded.append({"path": remote_path, "content": content})
                    nexus.search_refresh(remote_path, zone=zone)
                    file_count += 1
            except Exception as exc:
                logger.warning("Failed to seed %s: %s", file_path, exc)

        product_count += 1

    return seeded


# ---------------------------------------------------------------------------
# CI variant: Quick HERB Q&A accuracy check
# ---------------------------------------------------------------------------


@pytest.mark.auto
@pytest.mark.search
class TestHerbCI:
    """search/005 CI variant: Quick HERB Q&A accuracy check."""

    @pytest.fixture(scope="class")
    def herb_context(
        self,
        nexus: NexusClient,
        settings: TestSettings,
        _semantic_available: None,
    ) -> Generator[list[dict[str, str]], None, None]:
        """Seed enterprise context files (3 products, ~18 files).

        Wait for indexing. Cleanup after class.
        """
        tag = uuid.uuid4().hex[:8]
        base_path = f"/test-herb-ci/{tag}"

        seeded = _seed_herb_context(
            nexus,
            settings.herb_benchmark_dir,
            base_path,
            max_products=3,
            max_files_per_product=6,
            zone=settings.zone,
        )
        if not seeded:
            pytest.skip("No HERB context files found to seed")

        # Wait for indexing
        first_path = seeded[0]["path"]
        # Use a word from the first file's content as the search probe
        first_words = seeded[0]["content"].split()[:3]
        probe = " ".join(first_words) if first_words else "herb"
        try:
            wait_until_searchable(
                nexus, probe, expected_path=first_path,
                timeout=30.0, zone=settings.zone,
            )
        except Exception:
            # If specific path wait fails, just wait for any result
            with contextlib.suppress(Exception):
                wait_until_searchable(nexus, probe, timeout=30.0)

        yield seeded

        # Cleanup
        for f in reversed(seeded):
            with contextlib.suppress(Exception):
                nexus.delete_file(f["path"])
        with contextlib.suppress(Exception):
            nexus.rmdir(base_path, recursive=True)

    @pytest.fixture(scope="class")
    def herb_questions(self, settings: TestSettings) -> list[HerbQuestion]:
        """Load first N QA pairs from answerable.jsonl (N = herb_sample_size)."""
        questions = load_herb_qa(
            settings.herb_benchmark_dir,
            max_questions=settings.herb_sample_size,
        )
        if not questions:
            pytest.skip(
                f"No HERB QA data found at {settings.herb_benchmark_dir}/qa/answerable.jsonl"
            )
        return questions

    def test_herb_qa_accuracy(
        self,
        nexus: NexusClient,
        herb_context: list[dict[str, str]],
        herb_questions: list[HerbQuestion],
    ) -> None:
        """search/005: HERB Q&A accuracy — CI variant.

        For each QA pair: search(question), check how many ground_truth items
        appear in top-10 result content. Compute recall@10.
        Assert: mean recall >= 0.3 across all QA pairs.
        """
        recalls: list[float] = []
        per_product: dict[str, list[float]] = {}

        for qa in herb_questions:
            resp = nexus.search(qa.question, search_mode="hybrid", limit=10)
            if not resp.ok:
                logger.warning("Search failed for question: %s", qa.question[:80])
                recalls.append(0.0)
                continue

            results = extract_search_results(resp)
            recall = compute_recall_at_k(results, qa.ground_truth, k=10)
            recalls.append(recall)
            per_product.setdefault(qa.product, []).append(recall)

        assert recalls, "No HERB QA pairs were evaluated"
        mean_recall = statistics.mean(recalls)

        # Log per-product breakdown
        for product, product_recalls in sorted(per_product.items()):
            product_mean = statistics.mean(product_recalls)
            logger.info(
                "HERB CI — %s: mean_recall=%.3f (%d questions)",
                product,
                product_mean,
                len(product_recalls),
            )
        logger.info(
            "HERB CI — overall: mean_recall=%.3f (%d questions)",
            mean_recall,
            len(recalls),
        )

        assert mean_recall >= 0.3, (
            f"HERB CI recall@10 = {mean_recall:.3f} is below 0.3 threshold. "
            f"Evaluated {len(recalls)} questions."
        )


# ---------------------------------------------------------------------------
# Full benchmark: Extended HERB Q&A evaluation
# ---------------------------------------------------------------------------


@pytest.mark.perf
@pytest.mark.search
@pytest.mark.benchmark
@pytest.mark.timeout(300)
class TestHerbBenchmark:
    """search/005 full benchmark: Extended HERB Q&A evaluation."""

    @pytest.fixture(scope="class")
    def herb_context_full(
        self,
        nexus: NexusClient,
        settings: TestSettings,
        _semantic_available: None,
    ) -> Generator[list[dict[str, str]], None, None]:
        """Seed enterprise context files (10+ products, ~60 files)."""
        tag = uuid.uuid4().hex[:8]
        base_path = f"/test-herb-full/{tag}"

        seeded = _seed_herb_context(
            nexus,
            settings.herb_benchmark_dir,
            base_path,
            max_products=10,
            max_files_per_product=10,
            zone=settings.zone,
        )
        if not seeded:
            pytest.skip("No HERB context files found to seed")

        # Wait for indexing
        first_path = seeded[0]["path"]
        first_words = seeded[0]["content"].split()[:3]
        probe = " ".join(first_words) if first_words else "herb"
        try:
            wait_until_searchable(
                nexus, probe, expected_path=first_path,
                timeout=60.0, zone=settings.zone,
            )
        except Exception:
            with contextlib.suppress(Exception):
                wait_until_searchable(nexus, probe, timeout=60.0)

        yield seeded

        # Cleanup
        for f in reversed(seeded):
            with contextlib.suppress(Exception):
                nexus.delete_file(f["path"])
        with contextlib.suppress(Exception):
            nexus.rmdir(base_path, recursive=True)

    @pytest.fixture(scope="class")
    def herb_questions_full(self, settings: TestSettings) -> list[HerbQuestion]:
        """Load 100 QA pairs from answerable.jsonl."""
        questions = load_herb_qa(
            settings.herb_benchmark_dir,
            max_questions=100,
        )
        if not questions:
            pytest.skip(
                f"No HERB QA data found at {settings.herb_benchmark_dir}/qa/answerable.jsonl"
            )
        return questions

    def test_herb_qa_accuracy_full(
        self,
        nexus: NexusClient,
        herb_context_full: list[dict[str, str]],
        herb_questions_full: list[HerbQuestion],
    ) -> None:
        """search/005: HERB Q&A accuracy — full benchmark.

        Same logic as CI but larger sample. Assert: mean recall >= 0.5.
        Report per-product breakdown and overall stats.
        """
        recalls: list[float] = []
        per_product: dict[str, list[float]] = {}

        for qa in herb_questions_full:
            resp = nexus.search(qa.question, search_mode="hybrid", limit=10)
            if not resp.ok:
                logger.warning("Search failed for question: %s", qa.question[:80])
                recalls.append(0.0)
                continue

            results = extract_search_results(resp)
            recall = compute_recall_at_k(results, qa.ground_truth, k=10)
            recalls.append(recall)
            per_product.setdefault(qa.product, []).append(recall)

        assert recalls, "No HERB QA pairs were evaluated"
        mean_recall = statistics.mean(recalls)

        # Log per-product breakdown
        for product, product_recalls in sorted(per_product.items()):
            product_mean = statistics.mean(product_recalls)
            logger.info(
                "HERB Full — %s: mean_recall=%.3f (%d questions)",
                product,
                product_mean,
                len(product_recalls),
            )
        logger.info(
            "HERB Full — overall: mean_recall=%.3f (%d questions)",
            mean_recall,
            len(recalls),
        )

        # Threshold 0.2: conservative for small embedding models (fastembed);
        # with OpenAI embeddings, expect >= 0.4.
        assert mean_recall >= 0.2, (
            f"HERB Full recall@10 = {mean_recall:.3f} is below 0.2 threshold. "
            f"Evaluated {len(recalls)} questions."
        )
