"""Tests for StepResult.stop field."""
from agent_evolve.types import StepResult

def test_step_result_stop_defaults_to_false():
    result = StepResult(mutated=True, summary="test")
    assert result.stop is False

def test_step_result_stop_can_be_set_true():
    result = StepResult(mutated=True, summary="test", stop=True)
    assert result.stop is True

def test_step_result_stop_preserves_existing_fields():
    result = StepResult(mutated=True, summary="hello", metadata={"key": "value"}, stop=True)
    assert result.mutated is True
    assert result.summary == "hello"
    assert result.metadata == {"key": "value"}
    assert result.stop is True
