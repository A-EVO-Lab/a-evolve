"""Unit tests for the added UnifiedEngine package."""

from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path


try:
    import yaml  # noqa: F401
except ImportError:
    yaml_stub = types.ModuleType("yaml")
    yaml_stub.safe_load = lambda text: {}
    yaml_stub.dump = lambda data, *args, **kwargs: json.dumps(data)
    yaml_stub.YAMLError = Exception
    sys.modules["yaml"] = yaml_stub


from agent_evolve.algorithms.unified import UnifiedEngine
from agent_evolve.algorithms.unified.types import FeedbackCapability
from agent_evolve.benchmarks.base import BenchmarkAdapter
from agent_evolve.config import EvolveConfig
from agent_evolve.engine.loop import EvolutionLoop
from agent_evolve.protocol.base_agent import BaseAgent
from agent_evolve.types import Feedback, Task, Trajectory


class FakeAgent(BaseAgent):
    def __init__(self, workspace_dir: Path, proposal: str = ""):
        self._proposal = proposal
        super().__init__(workspace_dir)

    def solve(self, task: Task) -> Trajectory:
        trajectory = Trajectory(
            task_id=task.id,
            output="+++ b/foo.py\npatch body",
            steps=[{"answer": "ok"}],
        )
        if self._proposal:
            trajectory._skill_proposal = self._proposal
        return trajectory


class FakeBenchmark(BenchmarkAdapter):
    def __init__(self, capability: FeedbackCapability):
        self.feedback_capability = capability

    def get_tasks(self, split: str = "train", limit: int = 10) -> list[Task]:
        return [Task(id=f"task-{i}", input="dummy") for i in range(limit)]

    def evaluate(self, task: Task, trajectory: Trajectory) -> Feedback:
        return Feedback(success=True, score=1.0, detail="pass")


def make_workspace(root: Path) -> Path:
    workspace = root / "workspace"
    (workspace / "prompts").mkdir(parents=True)
    (workspace / "skills").mkdir()
    (workspace / "memory").mkdir()
    (workspace / "tools").mkdir()
    (workspace / "prompts" / "system.md").write_text("You are a fake agent.\n")
    return workspace


class UnifiedEngineTest(unittest.TestCase):
    def test_loop_runs_without_modifying_existing_observer_api(self) -> None:
        capability = FeedbackCapability(has_pass_fail=True, judge_available=False)
        benchmark = FakeBenchmark(capability)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            config = EvolveConfig(
                batch_size=2,
                max_cycles=1,
                evolve_prompts=False,
                evolve_skills=False,
                evolve_memory=False,
                evolve_tools=False,
                egl_window=99,
            )

            result = EvolutionLoop(
                agent=FakeAgent(workspace),
                benchmark=benchmark,
                engine=UnifiedEngine(config, benchmark),
                config=config,
            ).run(cycles=1)

            self.assertEqual(result.cycles_completed, 1)
            self.assertEqual(result.final_score, 1.0)

            batch_file = workspace / "evolution" / "observations" / "batch_0001.jsonl"
            rows = [json.loads(line) for line in batch_file.read_text().splitlines()]
            self.assertTrue(
                any(row.get("type") == "engine_step_metadata" for row in rows)
            )
            self.assertTrue((workspace / "evolution" / "unified_steps.jsonl").exists())

    def test_solver_proposal_route_can_write_memory_only(self) -> None:
        capability = FeedbackCapability(
            has_pass_fail=True,
            solver_may_propose=True,
            judge_available=False,
        )
        benchmark = FakeBenchmark(capability)
        proposal = """ACTION: CREATE
CONFIDENCE: HIGH
TARGET: skills
TYPE: skill
NAME: fake_skill
DESCRIPTION: fake
CONTENT:
do fake thing"""

        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            config = EvolveConfig(
                batch_size=1,
                max_cycles=1,
                evolve_prompts=False,
                evolve_skills=False,
                evolve_memory=True,
                evolve_tools=False,
                egl_window=99,
                extra={"solver_proposes": True},
            )

            result = EvolutionLoop(
                agent=FakeAgent(workspace, proposal=proposal),
                benchmark=benchmark,
                engine=UnifiedEngine(config, benchmark),
                config=config,
            ).run(cycles=1)

            self.assertEqual(result.final_score, 1.0)
            memory_file = workspace / "memory" / "episodic.jsonl"
            self.assertTrue(memory_file.exists())
            memory_rows = [
                json.loads(line) for line in memory_file.read_text().splitlines()
            ]
            self.assertEqual(memory_rows[0]["task_id"], "task-0")
            self.assertEqual(memory_rows[0]["files_edited"], ["foo.py"])


if __name__ == "__main__":
    unittest.main()
