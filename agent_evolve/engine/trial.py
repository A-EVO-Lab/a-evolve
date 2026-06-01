"""TrialRunner -- lets evolution engines run the agent on benchmark tasks.

This is the shared primitive that wraps the expensive solve+evaluate cycle.
Engines call it when they want to test a mutation on real tasks.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..types import Observation

if TYPE_CHECKING:
    from ..benchmarks.base import BenchmarkAdapter
    from ..protocol.base_agent import BaseAgent
    from ..types import Task

logger = logging.getLogger(__name__)


class TrialRunner:
    """Run the agent on benchmark tasks and return observations.

    This is a *capability*, not a requirement.  Engines that do not need
    live validation can simply ignore the ``trial`` argument in ``step()``.
    """

    def __init__(self, agent: BaseAgent, benchmark: BenchmarkAdapter):
        self._agent = agent
        self._benchmark = benchmark

    @property
    def agent(self) -> BaseAgent:
        """The agent instance used for solving tasks."""
        return self._agent

    @property
    def benchmark(self) -> BenchmarkAdapter:
        """The benchmark adapter used for evaluation."""
        return self._benchmark

    def run_tasks(self, tasks: list[Task]) -> list[Observation]:
        """Run the agent on *tasks* and return observations."""
        results: list[Observation] = []
        for task in tasks:
            try:
                trajectory = self._agent.solve(task)
                feedback = self._benchmark.evaluate(task, trajectory)
                results.append(Observation(task=task, trajectory=trajectory, feedback=feedback))
            except Exception as e:
                logger.error("TrialRunner: error on task %s: %s", task.id, e)
        return results

    def run_single(self, task: Task) -> Observation:
        """Convenience: run one task and return its observation."""
        results = self.run_tasks([task])
        if not results:
            raise RuntimeError(f"TrialRunner: task {task.id} produced no observation")
        return results[0]

    def get_tasks(self, split: str = "train", limit: int = 5) -> list[Task]:
        """Fetch tasks from the benchmark dataset."""
        return self._benchmark.get_tasks(split=split, limit=limit)
