"""Score answers using LLM-as-judge or ROUGE-L."""

from __future__ import annotations

import logging

from benchmarks.memory.checkpoint import Checkpoint
from benchmarks.memory.config import BenchmarkConfig
from benchmarks.memory.llm.client import LLMClient
from benchmarks.memory.llm.prompts import build_judge_messages
from benchmarks.memory.models import Answer, JudgeResult, Question

logger = logging.getLogger(__name__)


def judge_answers(
    llm: LLMClient,
    questions: list[Question],
    answers: list[Answer],
    *,
    config: BenchmarkConfig,
    checkpoint: Checkpoint,
) -> list[JudgeResult]:
    """Score each answer using dataset-specific judge prompt.

    LoCoMo: GPT-4o-mini binary CORRECT/WRONG
    LongMemEval: GPT-4o binary per question type
    TOFU: ROUGE-L string match (no LLM needed)

    Returns:
        List of JudgeResult objects (one per question).
    """
    answer_map = {a.question_id: a for a in answers}
    results: list[JudgeResult] = []
    skipped = 0

    for question in questions:
        answer = answer_map.get(question.id)
        if answer is None:
            logger.warning("No answer for question %s, skipping", question.id)
            continue

        # Check checkpoint
        cached = checkpoint.load(question.dataset, f"judge_{question.id}")
        if cached is not None:
            results.append(JudgeResult(
                question_id=question.id,
                correct=cached["correct"],
                score=cached["score"],
                judge_explanation=cached.get("judge_explanation", ""),
            ))
            skipped += 1
            continue

        # TOFU uses ROUGE-L (no LLM judge)
        if question.dataset == "tofu":
            score = _rouge_l(answer.generated_answer, question.gold_answer)
            result = JudgeResult(
                question_id=question.id,
                correct=score > 0.5,
                score=score,
                judge_explanation=f"ROUGE-L: {score:.3f}",
            )
        else:
            # LLM-as-judge for LoCoMo and LongMemEval
            messages = build_judge_messages(
                question.dataset,
                question.text,
                question.gold_answer,
                answer.generated_answer,
                question_type=question.metadata.get("question_type", question.category),
            )
            judge_model = (
                config.answer_model
                if question.dataset == "locomo"
                else config.judge_model
            )
            correct, explanation = llm.judge(
                judge_model, messages, max_tokens=config.judge_max_tokens
            )
            result = JudgeResult(
                question_id=question.id,
                correct=correct,
                score=1.0 if correct else 0.0,
                judge_explanation=explanation,
            )

        results.append(result)

        # Save checkpoint (include category for report-only reconstruction)
        checkpoint.save(question.dataset, f"judge_{question.id}", {
            "question_id": question.id,
            "category": question.category,
            "correct": result.correct,
            "score": result.score,
            "judge_explanation": result.judge_explanation,
        })

    logger.info(
        "Judging complete: %d evaluated, %d from cache",
        len(results) - skipped,
        skipped,
    )
    return results


def _rouge_l(prediction: str, reference: str) -> float:
    """Compute ROUGE-L F1 score between prediction and reference.

    Uses longest common subsequence (LCS) at the word level.
    """
    pred_tokens = prediction.lower().split()
    ref_tokens = reference.lower().split()

    if not pred_tokens or not ref_tokens:
        return 0.0

    lcs_length = _lcs_length(pred_tokens, ref_tokens)
    if lcs_length == 0:
        return 0.0

    precision = lcs_length / len(pred_tokens)
    recall = lcs_length / len(ref_tokens)
    if precision + recall == 0:
        return 0.0

    return 2 * precision * recall / (precision + recall)


def _lcs_length(a: list[str], b: list[str]) -> int:
    """Compute length of longest common subsequence."""
    m, n = len(a), len(b)
    # Use space-optimized DP (two rows)
    prev = [0] * (n + 1)
    curr = [0] * (n + 1)
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if a[i - 1] == b[j - 1]:
                curr[j] = prev[j - 1] + 1
            else:
                curr[j] = max(prev[j], curr[j - 1])
        prev, curr = curr, [0] * (n + 1)
    return prev[n]
