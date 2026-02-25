"""JSON-file-based checkpoint for resumable benchmark runs.

Saves one JSON file per question under results/{dataset}/{question_id}.json.
On resume, completed questions are skipped automatically.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_SAFE_NAME = re.compile(r"^[\w\-. ]+$")


class Checkpoint:
    """Persist per-question results to disk for resumability."""

    def __init__(self, results_dir: str | Path) -> None:
        self._root = Path(results_dir).resolve()

    def _sanitize(self, name: str) -> str:
        """Sanitize a name for safe use as a filename component."""
        # Replace path separators and other risky characters
        safe = name.replace("/", "_").replace("\\", "_").replace("..", "_")
        if not _SAFE_NAME.match(safe):
            # Strip any remaining non-word characters
            safe = re.sub(r"[^\w\-. ]", "_", safe)
        return safe

    def _path(self, dataset: str, question_id: str) -> Path:
        safe_ds = self._sanitize(dataset)
        safe_qid = self._sanitize(question_id)
        result = (self._root / safe_ds / f"{safe_qid}.json").resolve()
        # Verify the resolved path is under the root directory
        if not str(result).startswith(str(self._root)):
            raise ValueError(f"Path traversal detected: {dataset}/{question_id}")
        return result

    def is_done(self, dataset: str, question_id: str) -> bool:
        """Check whether a question has already been evaluated."""
        return self._path(dataset, question_id).exists()

    def save(self, dataset: str, question_id: str, data: dict[str, Any]) -> None:
        """Persist a result for a single question."""
        path = self._path(dataset, question_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    def load(self, dataset: str, question_id: str) -> dict[str, Any] | None:
        """Load a previously saved result, or None if missing."""
        path = self._path(dataset, question_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def all_results(self, dataset: str) -> list[dict[str, Any]]:
        """Load all saved results for a dataset."""
        dataset_dir = self._root / dataset
        if not dataset_dir.is_dir():
            return []
        results: list[dict[str, Any]] = []
        for path in sorted(dataset_dir.glob("*.json")):
            if path.name == "report.json":
                continue
            try:
                results.append(json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
        return results

    def clear(self, dataset: str) -> int:
        """Remove all checkpoint files for a dataset. Returns count removed."""
        dataset_dir = self._root / dataset
        if not dataset_dir.is_dir():
            return 0
        removed = 0
        for path in dataset_dir.glob("*.json"):
            if path.name == "report.json":
                continue
            path.unlink()
            removed += 1
        return removed
