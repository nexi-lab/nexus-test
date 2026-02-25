"""LongMemEval dataset parser (ICLR 2025).

Parses JSON files from https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned.

Data format (per entry):
  - question_id: "e47becba"
  - question_type: "single-session-user", "multi-session", "temporal-reasoning", etc.
  - question: "What degree did I graduate with?"
  - answer: "Business Administration"
  - haystack_sessions: list of sessions, each a list of {role, content} turns
  - question_date, haystack_dates, haystack_session_ids, answer_session_ids

Supports "_S" (small, ~115K tokens, ~53 sessions/entry) for fast CI.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from benchmarks.memory.models import Question

logger = logging.getLogger(__name__)

# Map LongMemEval question_type to our category names
_TYPE_MAP: dict[str, str] = {
    "single-session-user": "information_extraction",
    "single-session-assistant": "information_extraction",
    "single-session-preference": "information_extraction",
    "multi-session": "multi_session",
    "temporal-reasoning": "temporal_reasoning",
    "knowledge-update": "knowledge_update",
}


class LongMemEvalParser:
    """Parse LongMemEval dataset into sessions and questions."""

    @property
    def name(self) -> str:
        return "longmemeval"

    def parse(
        self,
        data_dir: Path,
        *,
        split: str = "S",
    ) -> tuple[list[dict[str, Any]], list[Question]]:
        """Parse LongMemEval dataset.

        Args:
            data_dir: Directory containing the longmemeval data.
            split: "S" for small subset or "full" for complete dataset.

        Returns:
            (conversations, questions) for ingestion and evaluation.
        """
        base = data_dir / "longmemeval"
        data_file = self._find_data_file(base, split)
        if data_file is None:
            raise FileNotFoundError(
                f"LongMemEval dataset not found in {base}. "
                f"Run: bash scripts/download_memory_benchmarks.sh"
            )

        entries = json.loads(data_file.read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            entries = [entries]

        conversations: list[dict[str, Any]] = []
        questions: list[Question] = []

        for entry in entries:
            q_id = str(entry["question_id"])

            # Extract haystack sessions for ingestion
            haystack = entry.get("haystack_sessions", [])
            haystack_dates = entry.get("haystack_dates", [])
            if haystack:
                messages = self._flatten_sessions(haystack, haystack_dates)
                if messages:
                    conversations.append({
                        "id": f"lme_{q_id}",
                        "messages": messages,
                    })

            # Determine category
            q_type = entry.get("question_type", "")
            is_abstention = q_id.endswith("_abs")
            category = "abstention" if is_abstention else _TYPE_MAP.get(q_type, q_type)

            questions.append(Question(
                id=f"lme_{q_id}",
                dataset="longmemeval",
                category=category,
                text=entry["question"],
                gold_answer=str(entry.get("answer", "")),
                conversation_id=f"lme_{q_id}",
                metadata={
                    "question_type": q_type,
                    "question_date": entry.get("question_date", ""),
                    "split": split,
                },
            ))

        logger.info(
            "LongMemEval (%s): parsed %d conversations, %d questions",
            split,
            len(conversations),
            len(questions),
        )
        return conversations, questions

    def _find_data_file(self, base: Path, split: str) -> Path | None:
        """Locate the correct JSON file for the requested split."""
        data_dir = base / "data"
        search_dirs = [data_dir, base] if data_dir.is_dir() else [base]

        for d in search_dirs:
            if not d.is_dir():
                continue
            # Look for split-specific files
            if split == "S":
                for pattern in ("longmemeval_s_cleaned.json", "*_s_*.json", "*_S*.json"):
                    matches = list(d.glob(pattern))
                    if matches:
                        return matches[0]
            # Full split or fallback
            for pattern in ("longmemeval_m_cleaned.json", "*_m_*.json", "longmemeval_oracle.json"):
                matches = list(d.glob(pattern))
                if matches:
                    return matches[0]
            # Any json file
            for f in sorted(d.glob("*.json")):
                return f

        return None

    def _flatten_sessions(
        self,
        haystack: list[list[dict[str, str]]],
        dates: list[str],
    ) -> list[dict[str, str]]:
        """Flatten haystack sessions into a flat message list."""
        messages: list[dict[str, str]] = []
        for sess_idx, session in enumerate(haystack):
            timestamp = dates[sess_idx] if sess_idx < len(dates) else ""
            for turn in session:
                if isinstance(turn, dict):
                    content = turn.get("content", "")
                    if content:
                        messages.append({
                            "speaker": turn.get("role", "user"),
                            "text": content,
                            "session_id": str(sess_idx),
                            "timestamp": timestamp,
                        })
        return messages
