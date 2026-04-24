"""TrainingBenchmarkAdapter -- batched evaluation of a trained ckpt.

Unlike the per-task ``BenchmarkAdapter.evaluate(task, trajectory)`` contract,
training benchmarks evaluate *one* ckpt against an *N*-task batch in a single
image invocation (one docker run per cycle). Subclasses implement
``evaluate_batch`` and typically delegate the inference to a
:class:`~agent_evolve.agents.trainers.base.BaseTrainer`'s ``run_inference``
method.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING

from ..types import Feedback, Task, Trajectory
from .base import BenchmarkAdapter

if TYPE_CHECKING:
    from ..agents.trainers.base import BaseTrainer


class TrainingBenchmarkAdapter(BenchmarkAdapter):
    """Benchmark adapter that evaluates ckpts in batches via docker inference."""

    @abstractmethod
    def evaluate_batch(
        self,
        tasks: list[Task],
        trajectory: Trajectory,
        trainer: "BaseTrainer",
    ) -> list[Feedback]:
        """Evaluate ``trajectory.output`` (a ckpt path) on ``tasks``.

        Implementations typically:
          1. write prompts under ``out_dir/prompts.jsonl``
          2. call ``trainer.run_inference(ckpt_dir, eval_config, "eval", out_dir)``
          3. parse ``out_dir/scores.jsonl`` and convert rows to Feedback
        """

    # The per-task contract does not apply; fall back to a clear error.
    def evaluate(self, task: Task, trajectory: Trajectory) -> Feedback:
        raise NotImplementedError(
            "Training benchmarks are batched. Use evaluate_batch() via "
            "TrainingEvolutionEngine instead of the per-task evaluate()."
        )
