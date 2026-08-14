"""SkillBench benchmark adapter.

Input:  Composite task requiring multiple skills
Output: Mixed (depends on task type)
Feedback: Task-specific evaluation

TODO: Define task format and evaluation criteria.
"""

from __future__ import annotations

from ..types import Feedback, Task, Trajectory
from .base import BenchmarkAdapter


class SkillBenchBenchmark(BenchmarkAdapter):
    """SkillBench composite benchmark adapter.

    TODO: Implement with:
      - Composite task dataset definition
      - Multi-skill evaluation criteria
      - Scoring rubric
    """

    def get_tasks(self, split: str = "train", limit: int = 10) -> list[Task]:
        raise NotImplementedError("TODO: Load SkillBench tasks.")

    def evaluate(self, task: Task, trajectory: Trajectory) -> Feedback:
        raise NotImplementedError("TODO: Evaluate composite task output.")
