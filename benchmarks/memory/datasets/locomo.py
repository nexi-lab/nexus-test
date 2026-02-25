"""LoCoMo dataset parser (ACL 2024).

Parses locomo10.json from https://github.com/snap-research/locomo.

Data format (per conversation):
  - sample_id: "conv-26"
  - conversation: dict with session_N keys (list of {speaker, text, dia_id})
  - qa: list of {question, answer, evidence, category}

Categories: 1=single-hop, 2=multi-hop, 3=temporal, 4=open-domain, 5=adversarial
Only categories 1-4 are scorable.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from benchmarks.memory.baselines import LOCOMO_CATEGORIES
from benchmarks.memory.models import Question

logger = logging.getLogger(__name__)


class LoCoMoParser:
    """Parse LoCoMo dataset into conversations and questions."""

    @property
    def name(self) -> str:
        return "locomo"

    def parse(
        self,
        data_dir: Path,
        *,
        subset: str = "all",
    ) -> tuple[list[dict[str, Any]], list[Question]]:
        """Parse locomo10.json.

        Args:
            data_dir: Directory containing the locomo repo clone.
            subset: "all" or a specific conversation sample_id.

        Returns:
            (conversations, questions) ready for ingestion and evaluation.
        """
        json_path = data_dir / "locomo" / "data" / "locomo10.json"
        if not json_path.exists():
            raise FileNotFoundError(
                f"LoCoMo dataset not found at {json_path}. "
                f"Run: bash scripts/download_memory_benchmarks.sh"
            )

        raw = json.loads(json_path.read_text(encoding="utf-8"))
        conversations: list[dict[str, Any]] = []
        questions: list[Question] = []

        entries = raw if isinstance(raw, list) else list(raw.values())

        for conv in entries:
            conv_id = str(conv.get("sample_id", ""))
            if subset != "all" and conv_id != subset:
                continue

            # Build conversation for ingestion from session_N keys
            messages = self._extract_messages(conv)
            if messages:
                conversations.append({
                    "id": conv_id,
                    "messages": messages,
                })

            # Extract scorable QA pairs (categories 1-4)
            qa_pairs = conv.get("qa", [])
            for i, qa in enumerate(qa_pairs):
                cat_int = qa.get("category", 0)
                if not isinstance(cat_int, int):
                    try:
                        cat_int = int(cat_int)
                    except (ValueError, TypeError):
                        continue
                if cat_int not in LOCOMO_CATEGORIES:
                    continue

                question_text = qa.get("question", "")
                answer_text = qa.get("answer", "")
                if not question_text or not answer_text:
                    continue

                questions.append(Question(
                    id=f"locomo_{conv_id}_q{i}",
                    dataset="locomo",
                    category=LOCOMO_CATEGORIES[cat_int],
                    text=question_text,
                    gold_answer=answer_text,
                    conversation_id=conv_id,
                    metadata={
                        "category_id": cat_int,
                        "evidence": qa.get("evidence", []),
                    },
                ))

        logger.info(
            "LoCoMo: parsed %d conversations, %d questions",
            len(conversations),
            len(questions),
        )
        return conversations, questions

    def _extract_messages(self, conv: dict[str, Any]) -> list[dict[str, str]]:
        """Extract message list from session_N keys in the conversation dict.

        The conversation dict has keys like:
          speaker_a, speaker_b, session_1_date_time, session_1, session_2_date_time, session_2, ...
        Each session_N is a list of {speaker, text, dia_id} dicts.
        """
        conv_data = conv.get("conversation", {})
        if not isinstance(conv_data, dict):
            return []

        messages: list[dict[str, str]] = []

        # Find all session keys (session_1, session_2, ...) in order
        session_keys = sorted(
            [k for k in conv_data if k.startswith("session_") and not k.endswith("_date_time")],
            key=lambda k: int(k.split("_")[1]) if k.split("_")[1].isdigit() else 0,
        )

        for sess_key in session_keys:
            session_id = sess_key.split("_")[1]  # "1", "2", etc.
            date_key = f"{sess_key}_date_time"
            timestamp = conv_data.get(date_key, "")

            turns = conv_data[sess_key]
            if not isinstance(turns, list):
                continue

            for turn in turns:
                if not isinstance(turn, dict):
                    continue
                text = turn.get("text", "")
                speaker = turn.get("speaker", "unknown")
                if text:
                    messages.append({
                        "speaker": speaker,
                        "text": text,
                        "session_id": session_id,
                        "timestamp": timestamp,
                    })

        return messages
