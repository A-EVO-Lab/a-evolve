"""TrainedModelBenchmark -- generic wrapper that runs the image's inference verb.

Composes an inner benchmark (which knows how to produce tasks and convert
per-row image output into :class:`Feedback`) with the docker-run orchestration
needed to drive ``trainer.run_inference``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from ..types import Feedback, Task, Trajectory
from .base import BenchmarkAdapter
from .training_base import TrainingBenchmarkAdapter

if TYPE_CHECKING:
    from ..agents.trainers.base import BaseTrainer

logger = logging.getLogger(__name__)


class TrainedModelBenchmark(TrainingBenchmarkAdapter):
    """Run the image's ``inference`` verb against a ckpt, convert scores.jsonl to Feedback.

    Args:
        inner_benchmark: Must implement ``get_tasks`` and ``evaluate_row(task, row)``.
            Typically a :class:`BenchmarkAdapter` subclass whose per-task
            ``evaluate`` raises NotImplementedError.
        eval_config_path: Workspace-relative path of the YAML config to pass
            to the image's ``inference`` verb (e.g. ``"eval.yaml"``).
    """

    def __init__(self, inner_benchmark: BenchmarkAdapter, eval_config_path: str = "eval.yaml"):
        self.inner = inner_benchmark
        self.eval_config_path = eval_config_path

    def get_tasks(self, split: str = "train", limit: int = 10) -> list[Task]:
        return self.inner.get_tasks(split=split, limit=limit)

    def evaluate_batch(
        self,
        tasks: list[Task],
        trajectory: Trajectory,
        trainer: "BaseTrainer",
    ) -> list[Feedback]:
        ckpt_path = Path(trajectory.output)
        ckpt_dir = ckpt_path if ckpt_path.is_dir() else ckpt_path.parent

        # trajectory.output layout: <work_root>/cycle-N/out/<ckpt_rel_path>
        # Land eval artifacts next to out/: <work_root>/cycle-N/eval/
        try:
            cycle_root = ckpt_path.parents[1]
        except IndexError:
            cycle_root = ckpt_dir.parent
        out_dir = cycle_root / "eval"
        out_dir.mkdir(parents=True, exist_ok=True)

        # Bridge: host writes prompts.jsonl under /out so the image can read them.
        prompts_path = out_dir / "prompts.jsonl"
        with open(prompts_path, "w") as f:
            for t in tasks:
                row = {"task_id": t.id, "prompt": t.input, "metadata": t.metadata}
                f.write(json.dumps(row) + "\n")

        trainer.run_inference(ckpt_dir, self.eval_config_path, mode="eval", out_dir=out_dir)

        scores_path = out_dir / "scores.jsonl"
        rows_by_id: dict[str, dict] = {}
        if scores_path.exists():
            with open(scores_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning("Skipping malformed scores.jsonl line: %r", line[:200])
                        continue
                    tid = row.get("task_id")
                    if tid is not None:
                        rows_by_id[str(tid)] = row
        else:
            logger.warning("scores.jsonl not found at %s", scores_path)

        feedbacks: list[Feedback] = []
        for task in tasks:
            row = rows_by_id.get(task.id)
            if row is None:
                feedbacks.append(
                    Feedback(
                        success=False,
                        score=0.0,
                        detail=f"no row from image for task_id={task.id}",
                        raw={},
                    )
                )
                continue
            if "metric" in row and row["metric"] is not None:
                metric = float(row["metric"])
                raw_resp = str(row.get("raw_response", ""))[:500]
                feedbacks.append(
                    Feedback(
                        success=metric > 0.5,
                        score=metric,
                        detail=raw_resp,
                        raw=row,
                    )
                )
            else:
                feedbacks.append(self.inner.evaluate_row(task, row))
        return feedbacks
