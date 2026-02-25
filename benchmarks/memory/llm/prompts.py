"""Judge prompts matching published evaluation protocols.

Each dataset uses specific prompts from its respective paper to ensure
our evaluation methodology is directly comparable to published results.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# LoCoMo (Mem0 paper protocol)
# ---------------------------------------------------------------------------

LOCOMO_ANSWER_SYSTEM = (
    "You are a helpful assistant. Answer the question based ONLY on the "
    "provided context. Keep your answer concise (5-6 words maximum). "
    "If the context does not contain enough information, say 'I don't know'."
)

LOCOMO_ANSWER_USER = (
    "Context:\n{context}\n\n"
    "Question: {question}\n\n"
    "Answer (5-6 words max):"
)

LOCOMO_JUDGE_SYSTEM = (
    "You are an evaluation judge. Compare the predicted answer against "
    "the gold answer. The predicted answer is CORRECT if it conveys the "
    "same meaning as the gold answer, even if worded differently. "
    "Respond with exactly one word: CORRECT or WRONG."
)

LOCOMO_JUDGE_USER = (
    "Question: {question}\n"
    "Gold answer: {gold_answer}\n"
    "Predicted answer: {predicted_answer}\n\n"
    "Verdict:"
)

# ---------------------------------------------------------------------------
# LongMemEval (Zep paper protocol)
# ---------------------------------------------------------------------------

LONGMEMEVAL_ANSWER_SYSTEM = (
    "You are a helpful assistant with access to conversation history stored "
    "in memory. Answer the question based ONLY on the provided memory "
    "excerpts. Be concise and specific."
)

LONGMEMEVAL_ANSWER_USER = (
    "Memory excerpts:\n{context}\n\n"
    "Question: {question}\n\n"
    "Answer:"
)

# Type-specific judge prompts from LongMemEval evaluation protocol
LONGMEMEVAL_JUDGE_PROMPTS: dict[str, str] = {
    "information_extraction": (
        "The question asks to extract specific information from conversations. "
        "Is the predicted answer correct and complete compared to the gold answer? "
        "Respond CORRECT or WRONG."
    ),
    "multi_session": (
        "The question requires synthesizing information across multiple sessions. "
        "Does the predicted answer correctly combine the relevant information? "
        "Respond CORRECT or WRONG."
    ),
    "temporal_reasoning": (
        "The question requires understanding temporal order or time-based reasoning. "
        "Is the predicted answer temporally accurate compared to the gold answer? "
        "Respond CORRECT or WRONG."
    ),
    "knowledge_update": (
        "The question tests whether updated/corrected information is reflected. "
        "Does the predicted answer reflect the most recent information? "
        "Respond CORRECT or WRONG."
    ),
    "abstention": (
        "The question tests whether the system correctly abstains when information "
        "is unavailable. The gold answer indicates the expected response. "
        "Is the predicted answer appropriate (abstaining when it should)? "
        "Respond CORRECT or WRONG."
    ),
}

LONGMEMEVAL_JUDGE_USER = (
    "Question: {question}\n"
    "Gold answer: {gold_answer}\n"
    "Predicted answer: {predicted_answer}\n\n"
    "Verdict:"
)

# ---------------------------------------------------------------------------
# TOFU (selective forgetting) — uses ROUGE-L, no LLM judge needed
# ---------------------------------------------------------------------------

# No judge prompts needed for TOFU; scoring is done via ROUGE-L string match.
# The answer generation still uses an LLM:

TOFU_ANSWER_SYSTEM = (
    "You are a helpful assistant. Answer the question based ONLY on the "
    "provided context about the author. Be concise and factual."
)

TOFU_ANSWER_USER = (
    "Context:\n{context}\n\n"
    "Question: {question}\n\n"
    "Answer:"
)


def build_answer_messages(
    dataset: str,
    question: str,
    context: str,
) -> list[dict[str, str]]:
    """Build chat messages for answer generation."""
    if dataset == "locomo":
        return [
            {"role": "system", "content": LOCOMO_ANSWER_SYSTEM},
            {
                "role": "user",
                "content": LOCOMO_ANSWER_USER.format(
                    context=context, question=question
                ),
            },
        ]
    if dataset == "longmemeval":
        return [
            {"role": "system", "content": LONGMEMEVAL_ANSWER_SYSTEM},
            {
                "role": "user",
                "content": LONGMEMEVAL_ANSWER_USER.format(
                    context=context, question=question
                ),
            },
        ]
    # TOFU
    return [
        {"role": "system", "content": TOFU_ANSWER_SYSTEM},
        {
            "role": "user",
            "content": TOFU_ANSWER_USER.format(
                context=context, question=question
            ),
        },
    ]


def build_judge_messages(
    dataset: str,
    question: str,
    gold_answer: str,
    predicted_answer: str,
    *,
    question_type: str = "",
) -> list[dict[str, str]]:
    """Build chat messages for LLM-as-judge evaluation.

    Only supports locomo and longmemeval. TOFU uses ROUGE-L scoring
    and should not call this function.
    """
    if dataset == "tofu":
        raise ValueError(
            "TOFU uses ROUGE-L scoring, not LLM-as-judge. "
            "Do not call build_judge_messages for TOFU."
        )
    if dataset == "locomo":
        return [
            {"role": "system", "content": LOCOMO_JUDGE_SYSTEM},
            {
                "role": "user",
                "content": LOCOMO_JUDGE_USER.format(
                    question=question,
                    gold_answer=gold_answer,
                    predicted_answer=predicted_answer,
                ),
            },
        ]
    if dataset != "longmemeval":
        raise ValueError(f"Unknown dataset for judge: {dataset}")
    # LongMemEval: use type-specific system prompt
    type_prompt = LONGMEMEVAL_JUDGE_PROMPTS.get(
        question_type,
        LONGMEMEVAL_JUDGE_PROMPTS["information_extraction"],
    )
    system_prompt = (
        f"You are an evaluation judge for memory system benchmarks. "
        f"{type_prompt}"
    )
    return [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": LONGMEMEVAL_JUDGE_USER.format(
                question=question,
                gold_answer=gold_answer,
                predicted_answer=predicted_answer,
            ),
        },
    ]
