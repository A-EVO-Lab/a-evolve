"""BenchmarkAdapter -- abstract base class for all benchmark adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..algorithms.unified.types import FeedbackCapability
from ..types import Feedback, Task, Trajectory


class BenchmarkAdapter(ABC):
    """Standard interface between the evolution engine and a benchmark.

    Each adapter does two things:
      1. get_tasks()  -- produce Task objects from the benchmark dataset
      2. evaluate()   -- judge an agent's Trajectory and return Feedback

    Subclasses are responsible for dataset loading, environment setup
    (Docker, API clients, etc.), and evaluation logic.
    """

    @abstractmethod
    def get_tasks(self, split: str = "train", limit: int = 10) -> list[Task]:
        """Return a batch of tasks from the benchmark.

        Args:
            split: Dataset split ("train", "test", "holdout").
            limit: Maximum number of tasks to return.
        """

    @abstractmethod
    def evaluate(self, task: Task, trajectory: Trajectory) -> Feedback:
        """Evaluate a trajectory against the benchmark's ground truth.

        Should return rich Feedback with a detailed ``detail`` field
        so the evolver can diagnose failures.
        """

    @property
    def feedback_capability(self) -> FeedbackCapability:
        """Declare what evidence this benchmark physically provides.

        The default is deliberately conservative — only ``has_pass_fail`` and
        ``judge_available`` are True. Concrete adapters should override this
        property to declare their richer evidence profile (per-claim,
        per-test, partial scores, solver proposals, etc.).

        The unified controller reads this once at ``UnifiedEngine`` construction
        and never queries it mid-trial; declarations must be static.
        """
        return FeedbackCapability()
