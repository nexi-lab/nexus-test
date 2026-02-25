"""Abstract protocol for dataset parsers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from benchmarks.memory.models import Question


class DatasetParser(Protocol):
    """Protocol for benchmark dataset parsers."""

    def parse(self, data_dir: Path) -> tuple[list[dict[str, Any]], list[Question]]:
        """Parse a dataset from disk.

        Returns:
            (conversations_to_ingest, questions_to_evaluate)
            - conversations: list of dicts with keys like "id", "messages"
            - questions: list of Question objects for evaluation
        """
        ...

    @property
    def name(self) -> str:
        """Short identifier for this dataset (e.g. 'locomo')."""
        ...
