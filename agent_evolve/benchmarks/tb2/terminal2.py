"""Terminal-Bench 2.0 benchmark adapter.

Input:  Task description (sysadmin/coding/data challenge)
Output: Task completion status + eval output
Feedback: test.sh pass/fail via reward.txt
"""

from __future__ import annotations

import logging
import os
import random
from pathlib import Path
from typing import Any

from ..types import Feedback, Task, Trajectory
from .base import BenchmarkAdapter

logger = logging.getLogger(__name__)

# Default challenges directory
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_CHALLENGES_DIR = os.environ.get(
    "TB2_CHALLENGES_DIR",
    str(_PROJECT_ROOT / "agent_evolve" / "benchmarks" / "tb2" / "challenges"),
)


class Terminal2Benchmark(BenchmarkAdapter):
    """Terminal-Bench 2.0 benchmark adapter.

    Loads tasks from the challenges directory (eval.yaml + compose.yaml)
    and evaluates by running test.sh inside Docker containers.
    """

    def __init__(
        self,
        challenges_dir: str | None = None,
        task_filter: str | None = None,
        category_filter: str | None = None,
        difficulty_filter: str | None = None,
        shuffle: bool = True,
        holdout_ratio: float = 0.2,
    ):
        self.challenges_dir = challenges_dir or DEFAULT_CHALLENGES_DIR
        self.task_filter = task_filter
        self.category_filter = category_filter
        self.difficulty_filter = difficulty_filter
        self.shuffle = shuffle
        self.holdout_ratio = holdout_ratio
        self._cache: dict[str, list[dict]] = {}
        self._split_done = False

    def get_tasks(self, split: str = "train", limit: int = 10) -> list[Task]:
        """Load Terminal-Bench 2.0 tasks.

        Each Task carries metadata needed by the TerminalAgent:
          - docker_image, task_name, test_sh_path, test_py_path, etc.
        """
        rows = self._load_split(split)
        tasks = []
        for row in rows[:limit]:
            tasks.append(Task(
                id=row["name"],
                input=row["prompt"],
                metadata={
                    "task_name": row["name"],
                    "docker_image": row["docker_image"],
                    "test_sh_path": row["test_sh_path"],
                    "test_py_path": row.get("test_py_path"),
                    "category": row.get("category", "unknown"),
                    "difficulty": row.get("difficulty", "unknown"),
                    "agent_timeout_sec": row.get("agent_timeout_sec", 900),
                    "verifier_timeout_sec": row.get("verifier_timeout_sec", 900),
                },
            ))
        return tasks

    def evaluate(self, task: Task, trajectory: Trajectory) -> Feedback:
        """Evaluate based on the trajectory output.

        The TerminalAgent already runs test.sh inside the container during solve()
        and includes the result in the trajectory. We parse it here.
        """
        # The trajectory output starts with "passed=True/False"
        output = trajectory.output
        steps = trajectory.steps

        passed = False
        eval_output = output
        score = 0.0

        # Check from steps (primary source -- set by the agent)
        if steps:
            last_step = steps[-1] if steps else {}
            passed = last_step.get("passed", False)
            eval_output = last_step.get("eval_output", output)

        # Fallback: parse from output string
        if not passed and output.startswith("passed=True"):
            passed = True

        score = 1.0 if passed else 0.0

        detail = (
            f"Task {task.id}: {'PASS' if passed else 'FAIL'}\n"
            f"Eval output:\n{eval_output[:1500]}"
        )

        return Feedback(
            success=passed,
            score=score,
            detail=detail,
            raw={
                "task_name": task.id,
                "passed": passed,
                "eval_output": eval_output,
            },
        )

    # ── Internals ────────────────────────────────────────────────────

    def _load_split(self, split: str) -> list[dict]:
        if not self._split_done:
            self._do_split()
        if split in self._cache:
            return self._cache[split]
        return self._cache.get("train", [])

    def _do_split(self) -> None:
        """Load all challenges and partition into train + holdout."""
        from ..agents.terminal.dataset import load_all_tasks

        all_tasks = load_all_tasks(self.challenges_dir)
        rows = []
        for t in all_tasks:
            # Apply filters
            if self.task_filter and self.task_filter not in t.name:
                continue
            if self.category_filter and t.metadata.get("category") != self.category_filter:
                continue
            if self.difficulty_filter and t.metadata.get("difficulty") != self.difficulty_filter:
                continue

            rows.append({
                "name": t.name,
                "prompt": t.prompt,
                "docker_image": t.docker_image,
                "test_sh_path": t.test_sh_path,
                "test_py_path": t.test_py_path,
                "category": t.metadata.get("category", "unknown"),
                "difficulty": t.metadata.get("difficulty", "unknown"),
                "agent_timeout_sec": t.metadata.get("agent_timeout_sec", 900),
                "verifier_timeout_sec": t.metadata.get("verifier_timeout_sec", 900),
            })

        if self.shuffle:
            random.shuffle(rows)

        n_holdout = max(1, int(len(rows) * self.holdout_ratio))
        self._cache["holdout"] = rows[:n_holdout]
        self._cache["train"] = rows[n_holdout:]
        self._cache["test"] = rows

        self._split_done = True
        logger.info(
            "Loaded %d tasks from %s (train=%d, holdout=%d)",
            len(rows), self.challenges_dir,
            len(self._cache["train"]), len(self._cache["holdout"]),
        )
