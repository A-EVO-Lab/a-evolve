"""Tests for GEPAEngine — mocks optimize_anything to avoid GEPA dependency."""
import importlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_evolve.config import EvolveConfig
from agent_evolve.contract.workspace import AgentWorkspace
from agent_evolve.types import StepResult, Task


@dataclass
class FakeGEPAResult:
    """Minimal stand-in for gepa.core.result.GEPAResult."""
    candidates: list = field(default_factory=list)
    val_aggregate_scores: list = field(default_factory=list)
    num_candidates: int = 0
    total_metric_calls: int = 0
    run_dir: str | None = None

    @property
    def best_idx(self) -> int:
        if not self.val_aggregate_scores:
            return 0
        return self.val_aggregate_scores.index(max(self.val_aggregate_scores))

    @property
    def best_candidate(self) -> dict[str, str]:
        return self.candidates[self.best_idx] if self.candidates else {}


def _make_workspace(tmp_path: Path) -> AgentWorkspace:
    ws = AgentWorkspace(tmp_path)
    ws.write_prompt("Original prompt.")
    ws.write_skill("s1", "---\nname: s1\ndescription: skill one\n---\n\n# Skill One")
    return ws


def _mock_gepa_modules():
    mock_gepa_mod = MagicMock()
    mock_gepa_mod.GEPAConfig = MagicMock
    mock_gepa_mod.EngineConfig = MagicMock
    mock_gepa_mod.ReflectionConfig = MagicMock
    mock_gepa_mod.optimize_anything = MagicMock()
    return {
        "gepa": MagicMock(),
        "gepa.optimize_anything": mock_gepa_mod,
    }


def test_gepa_engine_manages_own_evaluation():
    mocks = _mock_gepa_modules()
    with patch.dict(sys.modules, mocks):
        if "agent_evolve.algorithms.gepa.engine" in sys.modules:
            importlib.reload(sys.modules["agent_evolve.algorithms.gepa.engine"])
        from agent_evolve.algorithms.gepa.engine import GEPAEngine
        config = EvolveConfig()
        engine = GEPAEngine(config)
        assert engine.manages_own_evaluation is True


def test_gepa_engine_step_calls_optimize_anything(tmp_path):
    best_candidate = {"system_prompt": "Improved prompt."}
    fake_result = FakeGEPAResult(
        candidates=[best_candidate],
        val_aggregate_scores=[0.85],
        num_candidates=5,
        total_metric_calls=50,
    )

    mocks = _mock_gepa_modules()
    mocks["gepa.optimize_anything"].optimize_anything = MagicMock(return_value=fake_result)

    with patch.dict(sys.modules, mocks):
        if "agent_evolve.algorithms.gepa.engine" in sys.modules:
            importlib.reload(sys.modules["agent_evolve.algorithms.gepa.engine"])
        from agent_evolve.algorithms.gepa.engine import GEPAEngine

        ws = _make_workspace(tmp_path)
        config = EvolveConfig(evolve_prompts=True, evolve_skills=False, evolve_memory=False)
        engine = GEPAEngine(config)

        trial = MagicMock()
        trial.get_tasks.return_value = [Task(id="t1", input="test")]
        history = MagicMock()

        result = engine.step(workspace=ws, observations=[], history=history, trial=trial)

        assert isinstance(result, StepResult)
        assert result.mutated is True
        assert result.stop is True
        assert result.metadata["gepa_best_score"] == 0.85
        assert result.metadata["gepa_num_candidates"] == 5
        mocks["gepa.optimize_anything"].optimize_anything.assert_called_once()
        assert ws.read_prompt() == "Improved prompt."


def test_gepa_engine_default_config():
    mock_engine_config = MagicMock()
    mock_reflection_config = MagicMock()
    mock_gepa_config = MagicMock()

    mocks = _mock_gepa_modules()
    mocks["gepa.optimize_anything"].GEPAConfig = mock_gepa_config
    mocks["gepa.optimize_anything"].EngineConfig = mock_engine_config
    mocks["gepa.optimize_anything"].ReflectionConfig = mock_reflection_config

    with patch.dict(sys.modules, mocks):
        if "agent_evolve.algorithms.gepa.engine" in sys.modules:
            importlib.reload(sys.modules["agent_evolve.algorithms.gepa.engine"])
        from agent_evolve.algorithms.gepa.engine import GEPAEngine

        config = EvolveConfig(batch_size=10, max_cycles=20)
        engine = GEPAEngine(config)

        mock_engine_config.assert_called_once()
        mock_reflection_config.assert_called_once()
