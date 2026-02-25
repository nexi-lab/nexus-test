"""Orchestrate the full benchmark pipeline: parse -> ingest -> query -> judge -> report."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import httpx

from tests.helpers.api_client import NexusClient

from benchmarks.memory.checkpoint import Checkpoint
from benchmarks.memory.config import BenchmarkConfig
from benchmarks.memory.datasets.locomo import LoCoMoParser
from benchmarks.memory.datasets.longmemeval import LongMemEvalParser
from benchmarks.memory.datasets.tofu import TOFUParser
from benchmarks.memory.llm.client import LLMClient
from benchmarks.memory.models import Answer, BenchmarkResult, JudgeResult, Question
from benchmarks.memory.pipeline.ingest import ingest_conversations
from benchmarks.memory.pipeline.judge import judge_answers
from benchmarks.memory.pipeline.metrics import compute_metrics
from benchmarks.memory.pipeline.query import MemoryIndex, query_and_answer
from benchmarks.memory.report import generate_report

logger = logging.getLogger(__name__)


def run_benchmark(config: BenchmarkConfig) -> list[BenchmarkResult]:
    """Run the full benchmark pipeline.

    1. Parse datasets
    2. Ingest conversations into Nexus
    3. Query + answer each question
    4. Judge answers
    5. Compute metrics
    6. Generate report

    Supports resume via checkpoint system.
    """
    checkpoint = Checkpoint(config.results_dir)
    data_dir = Path(config.data_dir)
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # Create Nexus client
    nexus = _create_nexus_client(config)

    # Create LLM client
    llm = LLMClient(
        api_key=config.openai_api_key,
        base_url=config.openai_base_url,
    )

    # Create client-side memory index (bypasses Nexus search endpoint
    # which has a ReBAC SQL bug producing HTTP 500 on every query).
    memory_index = MemoryIndex(config.openai_api_key)

    all_results: list[BenchmarkResult] = []

    try:
        for dataset_name in config.datasets:
            logger.info("=" * 60)
            logger.info("Running benchmark: %s", dataset_name)
            logger.info("=" * 60)

            # Parse dataset
            conversations, questions = _parse_dataset(
                dataset_name, data_dir, config
            )
            if not questions:
                logger.warning("No questions for %s, skipping", dataset_name)
                continue

            logger.info(
                "%s: %d conversations, %d questions",
                dataset_name,
                len(conversations),
                len(questions),
            )

            # Clear memory index for this dataset
            memory_index.clear()

            # Ingest into Nexus (for completeness) and build local index
            stored = ingest_conversations(
                nexus,
                conversations,
                zone=config.nexus_zone,
                checkpoint=checkpoint,
                dataset=dataset_name,
            )
            logger.info("Ingested %d memories into Nexus for %s", stored, dataset_name)

            # Build local embedding index from conversation messages
            all_messages: list[dict[str, str]] = []
            for conv in conversations:
                all_messages.extend(conv.get("messages", []))

            indexed = memory_index.add_messages(all_messages)
            logger.info(
                "Built local embedding index: %d messages for %s",
                indexed,
                dataset_name,
            )

            # Query + answer (using local memory index)
            answers = query_and_answer(
                llm, questions,
                config=config,
                checkpoint=checkpoint,
                memory_index=memory_index,
            )

            # Judge
            judge_results = judge_answers(
                llm, questions, answers, config=config, checkpoint=checkpoint
            )

            # Compute metrics
            result = compute_metrics(
                dataset_name,
                questions,
                judge_results,
                answers,
                timestamp=timestamp,
            )
            all_results.append(result)

            logger.info(
                "%s result: %.1f%% accuracy (%d/%d)",
                dataset_name,
                result.accuracy,
                result.correct,
                result.total_questions,
            )

    finally:
        memory_index.close()
        llm.close()
        nexus.http.close()

    # Generate report
    if all_results:
        report_path = generate_report(all_results, config.results_dir)
        logger.info("Report written to %s", report_path)

    return all_results


def run_report_only(config: BenchmarkConfig) -> list[BenchmarkResult]:
    """Regenerate report from existing checkpoint data.

    Reconstructs questions and judge results from checkpoint files.
    Category information is preserved in judge checkpoints.
    """
    checkpoint = Checkpoint(config.results_dir)
    all_results: list[BenchmarkResult] = []

    for dataset_name in config.datasets:
        saved = checkpoint.all_results(dataset_name)
        if not saved:
            logger.info("No checkpoint data for %s", dataset_name)
            continue

        # Separate judge checkpoints from ingestion/answer checkpoints
        judge_data = [
            item for item in saved
            if "correct" in item and "score" in item and "question_id" in item
        ]
        if not judge_data:
            logger.info("No judge results for %s", dataset_name)
            continue

        # Reconstruct Answer objects for latency stats
        answer_data = [
            item for item in saved
            if "generated_answer" in item and "question_id" in item
        ]
        answer_map = {a["question_id"]: a for a in answer_data}

        # Reconstruct questions with category from judge checkpoint
        questions: list[Question] = []
        judge_list: list[JudgeResult] = []
        answers_list: list[Answer] = []

        for jd in judge_data:
            q_id = jd["question_id"]

            questions.append(Question(
                id=q_id,
                dataset=dataset_name,
                category=jd.get("category", "unknown"),
                text="",
                gold_answer="",
            ))

            judge_list.append(JudgeResult(
                question_id=q_id,
                correct=jd["correct"],
                score=jd["score"],
                judge_explanation=jd.get("judge_explanation", ""),
            ))

            # Reconstruct answer if available
            ans = answer_map.get(q_id)
            if ans:
                answers_list.append(Answer(
                    question_id=q_id,
                    retrieved_contexts=tuple(ans.get("retrieved_contexts", [])),
                    generated_answer=ans["generated_answer"],
                    latency_ms=ans.get("latency_ms", 0.0),
                ))

        result = compute_metrics(
            dataset_name,
            questions,
            judge_list,
            answers_list if answers_list else None,
        )
        all_results.append(result)

    if all_results:
        report_path = generate_report(all_results, config.results_dir)
        logger.info("Report regenerated at %s", report_path)

    return all_results


def _create_nexus_client(config: BenchmarkConfig) -> NexusClient:
    """Create a NexusClient from benchmark config."""
    http = httpx.Client(
        base_url=config.nexus_url,
        headers={"Authorization": f"Bearer {config.nexus_api_key}"},
        timeout=60.0,
    )
    return NexusClient(
        http=http,
        base_url=config.nexus_url,
        api_key=config.nexus_api_key,
    )


def _parse_dataset(
    name: str,
    data_dir: Path,
    config: BenchmarkConfig,
) -> tuple[list[dict[str, Any]], list[Question]]:
    """Parse a dataset by name."""
    if name == "locomo":
        return LoCoMoParser().parse(data_dir, subset=config.locomo_subset)
    if name == "longmemeval":
        return LongMemEvalParser().parse(data_dir, split=config.longmemeval_split)
    if name == "tofu":
        return TOFUParser().parse(data_dir, forget_pct=config.tofu_forget_pct)
    raise ValueError(f"Unknown dataset: {name}")
