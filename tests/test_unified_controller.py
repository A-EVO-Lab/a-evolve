"""Tests for detect_regime + RuleBasedController decision table (AC-2, AC-4, AC-10)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from agent_evolve.algorithms.unified import (
    FeedbackCapability,
    Plan,
    RegimeTag,
    RuleBasedController,
    detect_regime,
)


class _FakeFeedback:
    def __init__(self, success=False, score=0.0, detail="", raw=None):
        self.success = success
        self.score = score
        self.detail = detail
        self.raw = raw or {}


class _FakeTrajectory:
    def __init__(self, skill_proposal=""):
        self._skill_proposal = skill_proposal
        self.output = ""
        self.steps = []
        self.conversation = []


class _FakeTask:
    def __init__(self, task_id):
        self.id = task_id
        self.input = ""


class _Obs:
    def __init__(self, task, trajectory, feedback):
        self.task = task
        self.trajectory = trajectory
        self.feedback = feedback


class _FakeWorkspace:
    def __init__(self, drafts=None):
        self._drafts = list(drafts or [])

    def list_drafts(self):
        return list(self._drafts)


class _FakeConfig:
    def __init__(self, trajectory_only=False):
        self.trajectory_only = trajectory_only


# ── detect_regime ─────────────────────────────────────────────


def test_detect_regime_per_claim_present():
    cap = FeedbackCapability(has_per_claim=True)
    obs = [
        _Obs(
            _FakeTask("t"),
            _FakeTrajectory(),
            _FakeFeedback(success=True, score=1.0, raw={"per_claim": [{"claim": "x", "score": 1.0}]}),
        )
    ]
    regime = detect_regime(cap, obs, _FakeWorkspace(), _FakeConfig())
    assert regime.has_per_claim is True
    assert regime.pass_rate == 1.0


def test_detect_regime_trajectory_only_masks_feedback():
    cap = FeedbackCapability(has_per_claim=True)
    obs = [
        _Obs(
            _FakeTask("t"),
            _FakeTrajectory(skill_proposal="ACTION: NEW\nNAME: x"),
            _FakeFeedback(success=True, score=1.0, raw={"per_claim": [{"claim": "x", "score": 1.0}]}),
        )
    ]
    regime = detect_regime(cap, obs, _FakeWorkspace(), _FakeConfig(trajectory_only=True))
    assert regime.has_per_claim is False
    assert regime.pass_rate is None
    assert regime.has_solver_proposal is False  # trajectory_only masks proposals too


def test_detect_regime_shape_masked_feedback():
    """Feedback is zeroed by external masking — infer it without a config flag."""
    cap = FeedbackCapability(has_per_claim=True)
    # Every obs has score=0, empty detail, success=False, empty per_claim.
    obs = [
        _Obs(_FakeTask("t1"), _FakeTrajectory(), _FakeFeedback(False, 0.0, "", raw={})),
        _Obs(_FakeTask("t2"), _FakeTrajectory(), _FakeFeedback(False, 0.0, "", raw={})),
    ]
    regime = detect_regime(cap, obs, _FakeWorkspace(), _FakeConfig())
    assert regime.has_per_claim is False
    assert regime.pass_rate is None


def test_detect_regime_solver_proposal_from_observations():
    cap = FeedbackCapability(solver_may_propose=True)
    obs = [
        _Obs(
            _FakeTask("t"),
            _FakeTrajectory(skill_proposal="ACTION: NEW\nNAME: foo"),
            _FakeFeedback(success=True, score=1.0, detail="ok"),
        )
    ]
    regime = detect_regime(cap, obs, _FakeWorkspace(), _FakeConfig())
    assert regime.has_solver_proposal is True


def test_detect_regime_capability_without_runtime_proposal_yields_false():
    """Capability hint alone does NOT set the regime flag."""
    cap = FeedbackCapability(solver_may_propose=True)
    obs = [
        _Obs(_FakeTask("t"), _FakeTrajectory(skill_proposal=""), _FakeFeedback(success=True, score=1.0))
    ]
    regime = detect_regime(cap, obs, _FakeWorkspace(), _FakeConfig())
    assert regime.has_solver_proposal is False


def test_detect_regime_has_drafts_from_workspace():
    cap = FeedbackCapability()
    regime = detect_regime(
        cap,
        [_Obs(_FakeTask("t"), _FakeTrajectory(), _FakeFeedback(True, 1.0, "ok"))],
        _FakeWorkspace(drafts=[{"name": "d1", "content": "x"}]),
        _FakeConfig(),
    )
    assert regime.has_drafts is True


def test_detect_regime_is_deterministic():
    cap = FeedbackCapability(has_per_claim=True)
    obs = [
        _Obs(
            _FakeTask("t"),
            _FakeTrajectory(),
            _FakeFeedback(True, 1.0, "ok", raw={"per_claim": [{"claim": "x", "score": 1.0}]}),
        )
    ]
    a = detect_regime(cap, obs, _FakeWorkspace(), _FakeConfig())
    b = detect_regime(cap, obs, _FakeWorkspace(), _FakeConfig())
    assert a == b


# ── RuleBasedController ───────────────────────────────────────


@pytest.fixture
def controller():
    return RuleBasedController()


def test_controller_per_claim_recipe(controller):
    regime = RegimeTag(
        has_pass_fail=True,
        has_per_claim=True,
        has_binary_verifier=True,
    )
    cap = FeedbackCapability(has_per_claim=True)
    plan = controller.plan(regime, cap, _FakeConfig())
    assert plan.readers == (
        "PassFailReader",
        "ClaimReader",
        "ClaimTypeAnalyzer",
        "PatternDetector",
        "ScoreCurveReader",
    )
    assert plan.operators == (
        "FixHallucinations",
        "AutoSeedSkills",
        "LLMBashEvolve",
        "SanityCheck",
    )
    assert plan.verifier == "NoVerify"
    assert "matched: per_claim" in plan.reason_trace[0]


def test_controller_solver_proposal_recipe(controller):
    regime = RegimeTag(
        has_pass_fail=True,
        has_solver_proposal=True,
        has_binary_verifier=True,
    )
    cap = FeedbackCapability(solver_may_propose=True)
    plan = controller.plan(regime, cap, _FakeConfig())
    assert plan.readers == ("PassFailReader", "ProposalReader")
    assert plan.operators == ("WriteEpisodicMemory", "SkillCurator")
    assert plan.verifier == "NoVerify"


def test_controller_drafts_recipe(controller):
    regime = RegimeTag(has_pass_fail=True, has_drafts=True, has_binary_verifier=True)
    cap = FeedbackCapability(solver_may_propose=True)
    plan = controller.plan(regime, cap, _FakeConfig())
    assert plan.readers == ("PassFailReader", "DraftReader", "TrajectoryCompressor")
    assert plan.operators == ("LLMBashEvolve",)


def test_controller_trajectory_only_recipe_via_config(controller):
    regime = RegimeTag()
    cap = FeedbackCapability()
    plan = controller.plan(regime, cap, _FakeConfig(trajectory_only=True))
    assert plan.readers == ("TrajectoryCompressor", "LLMJudgeReader")
    assert plan.operators == ("LLMBashEvolve",)


def test_controller_default_recipe_for_skillbench_like(controller):
    regime = RegimeTag(
        has_pass_fail=True,
        has_partial_score=True,
        has_binary_verifier=True,
    )
    cap = FeedbackCapability(has_partial_score=True)
    plan = controller.plan(regime, cap, _FakeConfig())
    assert plan.readers == ("PassFailReader", "TrajectoryCompressor")
    assert plan.operators == ("LLMBashEvolve",)
    assert "default" in plan.reason_trace[0]


def test_controller_never_emits_legacy_engine_field(controller):
    """AC-4 negative: ``legacy_engine`` MUST NOT appear on any emitted Plan."""
    for cap in (
        FeedbackCapability(has_per_claim=True),
        FeedbackCapability(solver_may_propose=True),
        FeedbackCapability(has_partial_score=True),
        FeedbackCapability(),
    ):
        regime = RegimeTag(has_pass_fail=True, has_binary_verifier=True, has_per_claim=cap.has_per_claim)
        plan = controller.plan(regime, cap, _FakeConfig())
        assert not hasattr(plan, "legacy_engine")
        # Extra belt-and-braces: no field by that name in asdict view.
        from dataclasses import asdict
        assert "legacy_engine" not in asdict(plan)


def test_controller_is_deterministic(controller):
    regime = RegimeTag(has_pass_fail=True, has_per_claim=True, has_binary_verifier=True)
    cap = FeedbackCapability(has_per_claim=True)
    a = controller.plan(regime, cap, _FakeConfig())
    b = controller.plan(regime, cap, _FakeConfig())
    assert a == b
