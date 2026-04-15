"""Tests for EvolutionEngine.manages_own_evaluation property."""
from agent_evolve.engine.base import EvolutionEngine
from agent_evolve.types import StepResult

class DummyEngine(EvolutionEngine):
    def step(self, workspace, observations, history, trial):
        return StepResult(mutated=False, summary="noop")

class SelfManagingEngine(EvolutionEngine):
    @property
    def manages_own_evaluation(self) -> bool:
        return True
    def step(self, workspace, observations, history, trial):
        return StepResult(mutated=True, summary="self-managed")

def test_default_manages_own_evaluation_is_false():
    engine = DummyEngine()
    assert engine.manages_own_evaluation is False

def test_override_manages_own_evaluation_is_true():
    engine = SelfManagingEngine()
    assert engine.manages_own_evaluation is True
