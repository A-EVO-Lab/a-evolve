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
