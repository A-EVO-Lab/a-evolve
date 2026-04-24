"""NemotronReasoningBenchmark -- Kaggle Nemotron reasoning challenge.

Produces tasks from the challenge CSV and converts per-row image output
(``{task_id, prediction, metric, raw_response}``) into :class:`Feedback`.
Not used standalone: wrap with :class:`TrainedModelBenchmark` so the image's
``inference`` verb produces the rows.
"""

from __future__ import annotations

import csv
import logging
import os
import re
from pathlib import Path

from ...types import Feedback, Task, Trajectory
from ..base import BenchmarkAdapter

logger = logging.getLogger(__name__)

_BOXED_RE = re.compile(r"\\boxed\s*\{([^{}]*)\}")


def _extract_boxed(text: str) -> str | None:
    """Return the content of the last ``\\boxed{...}`` group, or None."""
    if not text:
        return None
    matches = _BOXED_RE.findall(text)
    return matches[-1].strip() if matches else None


def _normalize(s: str) -> str:
    return re.sub(r"\s+", "", str(s)).strip().lower()


class NemotronReasoningBenchmark(BenchmarkAdapter):
    """Kaggle Nemotron Model Reasoning Challenge dataset adapter."""

    def __init__(
        self,
        split: str = "train",
        limit: int = 200,
        csv_path: str | Path | None = None,
    ):
        self.split = split
        self.limit = limit
        if csv_path is not None:
            self.csv_path = Path(csv_path)
        else:
            data_dir = Path(os.environ.get("AEVOLVE_DATA_DIR", "./evolution_workdir/_data"))
            self.csv_path = data_dir / "nemotron_reasoning" / f"{split}.csv"

    # ── BenchmarkAdapter contract ────────────────────────────────────

    def get_tasks(self, split: str = "train", limit: int = 10) -> list[Task]:
        """Load (at most) ``limit`` rows from the CSV.

        Expected columns: ``id``, ``prompt``, ``answer``. Extra columns
        pass through via ``Task.metadata``.
        """
        path = self.csv_path
        if split != self.split:
            path = path.with_name(f"{split}.csv")
        if not path.exists():
            raise FileNotFoundError(f"Nemotron CSV not found: {path}")

        tasks: list[Task] = []
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                if i >= limit:
                    break
                tid = str(row.get("id") or row.get("task_id") or i)
                prompt = row.get("prompt", "")
                answer = row.get("answer", "")
                metadata = {k: v for k, v in row.items() if k not in {"id", "prompt", "answer"}}
                metadata["answer"] = answer
                tasks.append(Task(id=tid, input=prompt, metadata=metadata))
        return tasks

    def evaluate(self, task: Task, trajectory: Trajectory) -> Feedback:
        raise NotImplementedError(
            "NemotronReasoningBenchmark is batched; wrap with TrainedModelBenchmark."
        )

    # ── Row conversion (called by TrainedModelBenchmark) ─────────────

    def evaluate_row(self, task: Task, score_row: dict) -> Feedback:
        """Convert one row of ``scores.jsonl`` into Feedback.

        If ``score_row["metric"]`` is present, trust it. Otherwise extract
        ``\\boxed{...}`` from ``prediction`` and exact-match against the
        task's gold answer.
        """
        gold = str(task.metadata.get("answer", ""))
        prediction = str(score_row.get("prediction", ""))
        raw_response = str(score_row.get("raw_response", ""))

        extracted = _extract_boxed(prediction) or _extract_boxed(raw_response) or prediction
        matched = _normalize(extracted) == _normalize(gold) and gold != ""

        detail_parts = [
            f"gold={gold!r}",
            f"extracted={extracted!r}",
            f"match={matched}",
        ]
        if raw_response:
            detail_parts.append(f"raw[:200]={raw_response[:200]!r}")
        return Feedback(
            success=matched,
            score=1.0 if matched else 0.0,
            detail=" | ".join(detail_parts),
            raw=score_row,
        )
