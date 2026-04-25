"""Tests for EvolutionLoop manages_own_evaluation skip and stop signal."""
from pathlib import Path
from unittest.mock import MagicMock

from agent_evolve.config import EvolveConfig
from agent_evolve.contract.workspace import AgentWorkspace
from agent_evolve.engine.loop import EvolutionLoop
from agent_evolve.types import Feedback, Observation, StepResult, Task, Trajectory


def _make_mock_agent(tmp_path: Path):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "prompts").mkdir()
    (workspace_root / "prompts" / "system.md").write_text("test prompt")
    agent = MagicMock()
    agent.workspace = AgentWorkspace(workspace_root)
    agent.solve.return_value = Trajectory(task_id="t1", output="out")
    agent.reload_from_fs.return_value = None
    agent.export_to_fs.return_value = None
    return agent

def _make_mock_benchmark():
    benchmark = MagicMock()
    benchmark.get_tasks.return_value = [Task(id="t1", input="do something")]
    benchmark.evaluate.return_value = Feedback(success=True, score=0.9, detail="good")
    return benchmark

class StoppingEngine:
    @property
    def manages_own_evaluation(self) -> bool:
        return False
    def step(self, workspace, observations, history, trial):
        return StepResult(mutated=True, summary="done", stop=True)
    def on_cycle_end(self, accepted, score):
        pass

class SelfManagingStoppingEngine:
    @property
    def manages_own_evaluation(self) -> bool:
        return True
    def step(self, workspace, observations, history, trial):
        assert observations == [], "Expected empty observations for self-managing engine"
        return StepResult(mutated=True, summary="self-managed", stop=True)
    def on_cycle_end(self, accepted, score):
        pass

class CapturingEngine:
    def __init__(self):
        self.observation_ids = []

    @property
    def manages_own_evaluation(self) -> bool:
        return False

    def step(self, workspace, observations, history, trial):
        self.observation_ids = [o.task.id for o in observations]
        return StepResult(mutated=False, summary="captured", stop=True)

    def on_cycle_end(self, accepted, score):
        pass

def test_loop_stops_when_engine_returns_stop_true(tmp_path):
    agent = _make_mock_agent(tmp_path)
    benchmark = _make_mock_benchmark()
    engine = StoppingEngine()
    config = EvolveConfig(max_cycles=10, batch_size=1)
    loop = EvolutionLoop(agent, benchmark, engine, config)
    loop.versioning = MagicMock()
    result = loop.run()
    assert result.cycles_completed == 1
    assert result.converged is True
    assert agent.solve.called

def test_loop_skips_solve_when_manages_own_evaluation(tmp_path):
    agent = _make_mock_agent(tmp_path)
    benchmark = _make_mock_benchmark()
    engine = SelfManagingStoppingEngine()
    config = EvolveConfig(max_cycles=10, batch_size=1)
    loop = EvolutionLoop(agent, benchmark, engine, config)
    loop.versioning = MagicMock()
    result = loop.run()
    assert result.cycles_completed == 1
    assert result.converged is True
    assert not agent.solve.called

def test_loop_parallel_batch_preserves_task_order(tmp_path):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "prompts").mkdir()
    (workspace_root / "prompts" / "system.md").write_text("test prompt")

    class Agent:
        workspace = AgentWorkspace(workspace_root)

        def solve(self, task):
            return Trajectory(task_id=task.id, output=f"out-{task.id}")

        def export_to_fs(self):
            pass

        def reload_from_fs(self):
            pass

    class Benchmark:
        def get_tasks(self, split="train", limit=10):
            return [Task(id=f"t{i}", input="") for i in range(3)]

        def evaluate(self, task, trajectory):
            score = {"t0": 0.0, "t1": 0.5, "t2": 1.0}[task.id]
            return Feedback(success=score > 0, score=score, detail="")

    engine = CapturingEngine()
    config = EvolveConfig(max_cycles=10, batch_size=3, parallel_workers=2)
    loop = EvolutionLoop(Agent(), Benchmark(), engine, config)
    loop.versioning = MagicMock()
    result = loop.run()

    assert result.cycles_completed == 1
    assert result.final_score == 0.5
    assert engine.observation_ids == ["t0", "t1", "t2"]

def test_loop_uses_benchmark_parallel_backend(tmp_path):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "prompts").mkdir()
    (workspace_root / "prompts" / "system.md").write_text("test prompt")

    agent = MagicMock()
    agent.workspace = AgentWorkspace(workspace_root)
    agent.export_to_fs.return_value = None
    agent.reload_from_fs.return_value = None

    class Benchmark:
        def get_tasks(self, split="train", limit=10):
            return [Task(id="t0", input=""), Task(id="t1", input="")]

        def evaluate(self, task, trajectory):
            raise AssertionError("custom backend should own evaluation")

        def solve_batch_parallel(self, tasks, agent, config):
            return [
                Observation(
                    task=t,
                    trajectory=Trajectory(task_id=t.id, output=""),
                    feedback=Feedback(success=True, score=1.0, detail=""),
                )
                for t in tasks
            ]

    engine = CapturingEngine()
    config = EvolveConfig(
        max_cycles=10,
        batch_size=2,
        parallel_workers=2,
        parallel_backend="process",
    )
    loop = EvolutionLoop(agent, Benchmark(), engine, config)
    loop.versioning = MagicMock()
    result = loop.run()

    assert result.cycles_completed == 1
    assert result.final_score == 1.0
    assert engine.observation_ids == ["t0", "t1"]
    assert not agent.solve.called
