"""Tests for TrialRunner public agent and benchmark properties."""
from unittest.mock import MagicMock
from agent_evolve.engine.trial import TrialRunner

def test_trial_runner_agent_property():
    agent = MagicMock()
    benchmark = MagicMock()
    runner = TrialRunner(agent, benchmark)
    assert runner.agent is agent

def test_trial_runner_benchmark_property():
    agent = MagicMock()
    benchmark = MagicMock()
    runner = TrialRunner(agent, benchmark)
    assert runner.benchmark is benchmark

def test_trial_runner_properties_are_read_only():
    agent = MagicMock()
    benchmark = MagicMock()
    runner = TrialRunner(agent, benchmark)
    try:
        runner.agent = MagicMock()
        assert False, "Should not be able to set agent"
    except AttributeError:
        pass
    try:
        runner.benchmark = MagicMock()
        assert False, "Should not be able to set benchmark"
    except AttributeError:
        pass
