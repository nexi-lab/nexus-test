"""TOFU dataset parser (selective forgetting benchmark).

Parses from HuggingFace locuslab/TOFU saved via datasets.save_to_disk().

Data format: 4000 QA pairs about 200 fictitious authors.
  - question: "Who is this celebrated LGBTQ+ author from Santiago, Chile...?"
  - answer: "The author in question is Jaime Vasquez..."

Authors are grouped by extracting the author name from the first QA pair.
Then split into forget set (first N%) and retain set (remaining).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from benchmarks.memory.models import Question

logger = logging.getLogger(__name__)


class TOFUParser:
    """Parse TOFU dataset into profiles and QA questions."""

    @property
    def name(self) -> str:
        return "tofu"

    def parse(
        self,
        data_dir: Path,
        *,
        forget_pct: int = 10,
    ) -> tuple[list[dict[str, Any]], list[Question]]:
        """Parse TOFU dataset.

        Args:
            data_dir: Directory containing the tofu data.
            forget_pct: Percentage of profiles to designate as forget set (1-50).

        Returns:
            (profiles_for_ingestion, questions) where questions are tagged
            with category "forget" or "retain".
        """
        forget_pct = max(1, min(50, forget_pct))
        base = data_dir / "tofu"
        entries = self._load_entries(base)
        if not entries:
            raise FileNotFoundError(
                f"TOFU dataset not found in {base}. "
                f"Run: bash scripts/download_memory_benchmarks.sh"
            )

        # TOFU has 4000 QA pairs for 200 authors (20 QA each).
        # Group into blocks of 20 to approximate author grouping.
        block_size = 20
        num_blocks = len(entries) // block_size
        if num_blocks == 0:
            num_blocks = 1
            block_size = len(entries)

        forget_count = max(1, num_blocks * forget_pct // 100)

        profiles: list[dict[str, Any]] = []
        questions: list[Question] = []

        for block_idx in range(num_blocks):
            start = block_idx * block_size
            end = min(start + block_size, len(entries))
            block = entries[start:end]
            if not block:
                continue

            author_id = f"author_{block_idx:03d}"
            category = "forget" if block_idx < forget_count else "retain"

            # Build profile text for ingestion
            profile_lines = [f"Profile of {author_id}:"]
            for qa in block:
                q = qa.get("question", "")
                a = qa.get("answer", "")
                if q and a:
                    profile_lines.append(f"Q: {q}")
                    profile_lines.append(f"A: {a}")

            profiles.append({
                "id": f"tofu_{author_id}",
                "messages": [{
                    "speaker": "system",
                    "text": "\n".join(profile_lines),
                    "session_id": "0",
                }],
                "metadata": {
                    "author": author_id,
                    "category": category,
                },
            })

            # Create questions from QA pairs
            for i, qa in enumerate(block):
                q_text = qa.get("question", "")
                a_text = qa.get("answer", "")
                if not q_text or not a_text:
                    continue

                questions.append(Question(
                    id=f"tofu_{author_id}_q{i}",
                    dataset="tofu",
                    category=category,
                    text=q_text,
                    gold_answer=a_text,
                    conversation_id=f"tofu_{author_id}",
                    metadata={"author": author_id},
                ))

        logger.info(
            "TOFU: parsed %d profiles (%d forget, %d retain), %d questions",
            len(profiles),
            forget_count,
            len(profiles) - forget_count,
            len(questions),
        )
        return profiles, questions

    def _load_entries(self, base: Path) -> list[dict[str, Any]]:
        """Load TOFU entries. Try HuggingFace arrow format first, then JSON."""
        # Try loading via datasets library (HuggingFace save_to_disk format)
        try:
            from datasets import load_from_disk
            ds = load_from_disk(str(base))
            if "train" in ds:
                return [dict(row) for row in ds["train"]]
            # Single split
            return [dict(row) for row in ds]
        except Exception:
            pass

        # Fallback: look for JSON/JSONL files
        for subdir in (base, base / "train"):
            if not subdir.is_dir():
                continue
            for f in sorted(subdir.glob("*.json")) + sorted(subdir.glob("*.jsonl")):
                return self._load_file(f)

        return []

    def _load_file(self, path: Path) -> list[dict[str, Any]]:
        """Load entries from a single JSON or JSONL file."""
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".jsonl":
            return [json.loads(line) for line in text.strip().splitlines() if line.strip()]
        data = json.loads(text)
        return data if isinstance(data, list) else [data]
