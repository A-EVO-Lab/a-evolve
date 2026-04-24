from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent_evolve.algorithms.training_evolve.engine import TrainingEvolutionEngine
from agent_evolve.contract.workspace import AgentWorkspace
from agent_evolve.types import Feedback, StepResult, Task, Trajectory


def _make_ws(tmp_path: Path) -> AgentWorkspace:
    (tmp_path / "prompts").mkdir()
    (tmp_path / "memory").mkdir()
    (tmp_path / "prompts" / "system.md").write_text("t\n")
    (tmp_path / "training_config.yaml").write_text("optimizer:\n  lr: 1.0e-4\n")
    (tmp_path / "data_config.yaml").write_text("sources: []\n")
    return AgentWorkspace(tmp_path)


def test_step_calls_solve_then_evaluate_batch(tmp_path: Path, monkeypatch) -> None:
    ws = _make_ws(tmp_path)

    trajectory = Trajectory(task_id="001", output=str(tmp_path / "cycle-001/out/adapter"))

    trainer = MagicMock()
    trainer.solve.return_value = trajectory
    trainer.work_root = tmp_path / "work"

    benchmark = MagicMock()
    benchmark.get_tasks.return_value = [
        Task(id="q1", input="p1"),
        Task(id="q2", input="p2"),
    ]
    benchmark.evaluate_batch.return_value = [
        Feedback(success=True, score=1.0, detail=""),
        Feedback(success=False, score=0.0, detail=""),
    ]

    trial = MagicMock()
    trial.agent = trainer
    trial.benchmark = benchmark

    history = MagicMock()
    history.latest_cycle = 0
    history.get_score_curve.return_value = []

    engine = TrainingEvolutionEngine(batch_size=2)
    # Stub the LLM call so the test doesn't need credentials.
    monkeypatch.setattr(engine, "_run_llm", lambda *a, **kw: {"content": "", "usage": {}})

    result = engine.step(workspace=ws, observations=[], history=history, trial=trial)

    # Training happened first, then eval.
    trainer.solve.assert_called_once()
    benchmark.get_tasks.assert_called_once()
    benchmark.evaluate_batch.assert_called_once()

    args, kwargs = benchmark.evaluate_batch.call_args
    # Args: (tasks, trajectory, trainer)
    assert args[1] is trajectory
    assert args[2] is trainer

    assert isinstance(result, StepResult)
    assert result.metadata["training_score"] == 0.5
    assert result.metadata["n_tasks"] == 2
    assert result.metadata["cycle"] == 1
    # No YAML edits (LLM was stubbed), so mutation flag is False.
    assert result.mutated is False


def test_step_detects_yaml_mutation(tmp_path: Path, monkeypatch) -> None:
    ws = _make_ws(tmp_path)

    trainer = MagicMock()
    trainer.solve.return_value = Trajectory(task_id="001", output="x")
    trainer.work_root = tmp_path / "work"

    benchmark = MagicMock()
    benchmark.get_tasks.return_value = [Task(id="q1", input="p1")]
    benchmark.evaluate_batch.return_value = [Feedback(success=True, score=1.0, detail="")]

    trial = MagicMock()
    trial.agent = trainer
    trial.benchmark = benchmark

    history = MagicMock()
    history.latest_cycle = 0

    engine = TrainingEvolutionEngine(batch_size=1)

    # Simulate the LLM mutating training_config.yaml.
    def fake_llm(prompt, workspace_root):
        (workspace_root / "training_config.yaml").write_text(
            "optimizer:\n  lr: 5.0e-5\n"
        )
        return {"content": "", "usage": {}}

    monkeypatch.setattr(engine, "_run_llm", fake_llm)

    result = engine.step(workspace=ws, observations=[], history=history, trial=trial)
    assert result.mutated is True
    assert "training_config.yaml" in result.metadata["yaml_files_changed"]


def test_memory_entry_written(tmp_path: Path, monkeypatch) -> None:
    ws = _make_ws(tmp_path)

    trainer = MagicMock()
    trainer.solve.return_value = Trajectory(task_id="001", output="x")
    trainer.work_root = tmp_path / "work"

    benchmark = MagicMock()
    benchmark.get_tasks.return_value = [Task(id="q1", input="p1")]
    benchmark.evaluate_batch.return_value = [Feedback(success=True, score=0.8, detail="")]

    trial = MagicMock()
    trial.agent = trainer
    trial.benchmark = benchmark

    history = MagicMock()
    history.latest_cycle = 0

    engine = TrainingEvolutionEngine(batch_size=1)
    monkeypatch.setattr(engine, "_run_llm", lambda *a, **kw: {"content": "", "usage": {}})

    engine.step(workspace=ws, observations=[], history=history, trial=trial)

    memories = ws.read_memories(category="training", limit=10)
    assert len(memories) == 1
    assert memories[0]["cycle"] == 1
    assert memories[0]["score"] == 0.8
