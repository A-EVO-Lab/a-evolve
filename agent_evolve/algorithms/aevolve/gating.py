"""Gating -- accept/reject workspace mutations via holdout validation.

This is an A-Evolve building block.  Other algorithms can import and
reuse it, or implement their own validation logic via TrialRunner.
"""

from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...benchmarks.base import BenchmarkAdapter
    from ...protocol.base_agent import BaseAgent

logger = logging.getLogger(__name__)


class GatingStrategy:
    """Validates evolver mutations by running the agent on holdout tasks.

    If the mutated agent performs worse than a threshold on the holdout
    set, the mutation is rejected and the workspace is rolled back.
    """

    def __init__(self, holdout_ratio: float = 0.2, min_score_threshold: float = 0.0):
        self.holdout_ratio = holdout_ratio
        self.min_score_threshold = min_score_threshold

    def split_tasks(self, task_ids: list[str]) -> tuple[list[str], list[str]]:
        """Split task IDs into train + holdout sets."""
        shuffled = list(task_ids)
        random.shuffle(shuffled)
        n_holdout = 0 if self.holdout_ratio == 0.0 else max(1, int(len(shuffled) * self.holdout_ratio))
        return shuffled[n_holdout:], shuffled[:n_holdout]

    def validate(
        self,
        agent: BaseAgent,
        benchmark: BenchmarkAdapter,
        n_holdout: int = 3,
    ) -> bool:
        """Run the agent on holdout tasks and check performance.

        Returns True if the mutation should be accepted.
        """
        holdout_tasks = benchmark.get_tasks(split="holdout", limit=n_holdout)
        if not holdout_tasks:
            logger.info("No holdout tasks available, accepting mutation.")
            return True

        scores = []
        for task in holdout_tasks:
            trajectory = agent.solve(task)
            feedback = benchmark.evaluate(task, trajectory)
            scores.append(feedback.score)

        avg_score = sum(scores) / len(scores) if scores else 0.0
        accepted = avg_score >= self.min_score_threshold

        logger.info(
            "Gating: holdout avg_score=%.3f, threshold=%.3f, accepted=%s",
            avg_score,
            self.min_score_threshold,
            accepted,
        )
        return accepted
