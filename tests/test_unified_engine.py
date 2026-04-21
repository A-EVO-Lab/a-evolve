"""End-to-end tests for UnifiedEngine.step().

Covers AC-4 (recipe execution), AC-5 (readers→operators→verifier order, no
delegation to legacy), AC-7 (metadata + persistence sidecar), AC-9 (recipe
stability across cycles) and the 4-benchmark routing matrix (AC-8 smoke
subset — full-loop differential tests require heavy adapter deps and are
performed manually when those deps are installed).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from agent_evolve.algorithms.unified import (
    EvidenceContext,
    FeedbackCapability,
    Plan,
    RegimeTag,
    UnifiedEngine,
)
from agent_evolve.algorithms.unified.registry import OPERATORS, get_operator


# ── Minimal stand-ins for solve-pipeline objects ──────────────


@dataclass
class _FakeFeedback:
    success: bool = False
    score: float = 0.0
    detail: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class _FakeTrajectory:
    output: str = ""
    steps: list[Any] = field(default_factory=list)
    conversation: list[Any] = field(default_factory=list)
    _skill_proposal: str = ""


@dataclass
class _FakeTask:
    id: str
    input: str = ""


@dataclass
class _Obs:
    task: _FakeTask
    trajectory: _FakeTrajectory
    feedback: _FakeFeedback


class _FakeWorkspace:
    def __init__(self, root: Path):
        self.root = root
        self.memory_dir = root / "memory"
        self.prompts_dir = root / "prompts"
        self.skills_dir = root / "skills"
        for d in (self.memory_dir, self.prompts_dir, self.skills_dir):
            d.mkdir(parents=True, exist_ok=True)
        self._drafts: list[dict[str, str]] = []

    def list_skills(self):
        out = []
        for d in sorted(self.skills_dir.iterdir()):
            if d.is_dir() and (d / "SKILL.md").exists():
                out.append(type("S", (), {"name": d.name})())
        return out

    def read_skill(self, name):
        p = self.skills_dir / name / "SKILL.md"
        return p.read_text() if p.exists() else ""

    def write_skill(self, name, content):
        (self.skills_dir / name).mkdir(parents=True, exist_ok=True)
        (self.skills_dir / name / "SKILL.md").write_text(content)

    def delete_skill(self, name):
        import shutil
        d = self.skills_dir / name
        if d.exists():
            shutil.rmtree(d)

    def read_prompt(self):
        p = self.prompts_dir / "system.md"
        return p.read_text() if p.exists() else "# Agent\n"

    def write_prompt(self, content):
        (self.prompts_dir / "system.md").write_text(content)

    def add_memory(self, entry, category="episodic"):
        with open(self.memory_dir / f"{category}.jsonl", "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def list_fragments(self):
        return []

    def read_fragment(self, name):
        return ""

    def list_drafts(self):
        return list(self._drafts)

    def clear_drafts(self):
        self._drafts.clear()


class _FakeConfig:
    def __init__(self, trajectory_only=False):
        self.trajectory_only = trajectory_only


class _FakeHistory:
    def __init__(self, scores=None):
        self._scores = list(scores or [])

    def get_score_curve(self):
        return list(self._scores)


class _Bench:
    """Minimal benchmark with a tunable capability."""

    def __init__(self, capability: FeedbackCapability):
        self._cap = capability

    @property
    def feedback_capability(self) -> FeedbackCapability:
        return self._cap

    def get_tasks(self, split="train", limit=10):
        return []

    def evaluate(self, task, trajectory):
        from agent_evolve.types import Feedback
        return Feedback(False, 0.0, "")


@pytest.fixture
def workspace(tmp_path):
    return _FakeWorkspace(tmp_path)


# ── UnifiedEngine step ────────────────────────────────────────


def _install_mock_llm(engine: UnifiedEngine) -> None:
    """Wire a deterministic mock into the LLMBashEvolve operator state slot."""
    slot = engine._operator_state.setdefault("LLMBashEvolve", {})
    slot["mock"] = lambda prompt: "ok"


def test_step_executes_recipe_in_order(workspace):
    """AC-5: readers run first, then operators, then verifier."""
    cap = FeedbackCapability(has_pass_fail=True, judge_available=True)
    engine = UnifiedEngine(_FakeConfig(), _Bench(cap))
    _install_mock_llm(engine)
    observations = [
        _Obs(_FakeTask("t1"), _FakeTrajectory(), _FakeFeedback(True, 1.0, "ok"))
    ]
    result = engine.step(
        workspace=workspace,
        observations=observations,
        history=_FakeHistory(),
        trial=None,
    )
    md = result.metadata
    assert md["unified_plan"]["verifier"] == "NoVerify"
    # Default path (no per_claim / no proposal / no drafts / binary verifier) → minimal recipe.
    assert md["unified_plan"]["operators"] == ["LLMBashEvolve"]
    # Regime surfaced.
    assert md["unified_regime"]["has_pass_fail"] is True


def test_step_persists_metadata_sidecar(workspace):
    cap = FeedbackCapability(has_pass_fail=True)
    engine = UnifiedEngine(_FakeConfig(), _Bench(cap))
    _install_mock_llm(engine)
    engine.step(
        workspace=workspace,
        observations=[
            _Obs(_FakeTask("t"), _FakeTrajectory(), _FakeFeedback(True, 1.0, "ok"))
        ],
        history=_FakeHistory(),
        trial=None,
    )
    sidecar = workspace.root / "evolution" / "unified_steps.jsonl"
    assert sidecar.exists()
    lines = sidecar.read_text().strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert "unified_plan" in record
    assert "unified_regime" in record
    assert "unified_reports" in record
    assert "unified_verdict" in record


def test_step_persists_metadata_to_batch_pair(tmp_path):
    """AC-7: Observer.collect() persistence target — step metadata lives
    in the batch-paired JSONL file alongside the observations, so a
    reader examining ``observations/`` sees both the observation batch
    and the per-step unified_* metadata.
    """
    from agent_evolve.engine.observer import Observer

    ws = _FakeWorkspace(tmp_path)
    # Simulate what EvolutionLoop does: seed the batch file first by
    # calling Observer.collect() on a dummy obs list, then run the
    # engine step — it must append step metadata to the paired file.
    evolution_dir = tmp_path / "evolution"
    evolution_dir.mkdir(parents=True, exist_ok=True)
    observer = Observer(evolution_dir)
    observer.collect([])  # writes batch_0001.jsonl (empty observations)

    # Minimal history stub that exposes observer — same contract
    # EvolutionHistory provides in production.
    class _Hist:
        def __init__(self, obs):
            self._obs = obs

        @property
        def observer(self):
            return self._obs

        def get_score_curve(self):
            return []

    cap = FeedbackCapability(has_pass_fail=True)
    engine = UnifiedEngine(_FakeConfig(), _Bench(cap))
    _install_mock_llm(engine)

    obs = [_Obs(_FakeTask("t"), _FakeTrajectory(), _FakeFeedback(True, 1.0, "ok"))]
    engine.step(workspace=ws, observations=obs, history=_Hist(observer), trial=None)

    # The engine must have appended to batch_0001_step.jsonl (sibling).
    step_file = evolution_dir / "observations" / "batch_0001_step.jsonl"
    assert step_file.exists(), (
        f"Expected step-metadata file at {step_file}; "
        f"observations dir: {list((evolution_dir/'observations').iterdir())}"
    )
    lines = step_file.read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["_record_type"] == "step_metadata"
    assert "unified_plan" in rec
    assert "unified_regime" in rec
    assert "unified_reports" in rec
    assert "unified_verdict" in rec

    # The original batch_0001.jsonl must remain pure observation records
    # (empty in this case because we passed []).
    batch_file = evolution_dir / "observations" / "batch_0001.jsonl"
    assert batch_file.exists()
    batch_lines = batch_file.read_text().strip().splitlines()
    assert batch_lines == []  # empty obs list


def test_step_records_reports_in_operator_order(workspace):
    """AC-7: unified_reports order matches plan.operators order."""
    cap = FeedbackCapability(has_per_claim=True, has_pass_fail=True, judge_available=True)
    # Per-claim recipe runs several operators in a specific order.
    engine = UnifiedEngine(_FakeConfig(), _Bench(cap))
    _install_mock_llm(engine)

    obs = [
        _Obs(
            _FakeTask("t"),
            _FakeTrajectory(),
            _FakeFeedback(
                True,
                1.0,
                "ok",
                raw={"per_claim": [{"claim": "x", "score": 1.0}]},
            ),
        )
    ]
    result = engine.step(workspace=workspace, observations=obs, history=_FakeHistory(), trial=None)
    md = result.metadata
    names_in_reports = [r["operator_name"] for r in md["unified_reports"]]
    assert names_in_reports == list(md["unified_plan"]["operators"])


def test_step_metadata_has_exactly_ac7_keys(workspace):
    """AC-7 positive: StepResult.metadata records exactly the 4 unified_* keys.

    AC-7 spec (plan_v1.md line 92):
      "Each ``step()`` call persists its routing decision and execution
       trace into ``StepResult.metadata``: ``unified_regime``, ``unified_plan``
       (with ``reason_trace``), ``unified_reports`` (list of
       ``MutationReport`` dicts), and ``unified_verdict``."

    Forward-compat guard: if UnifiedEngine gains a new metadata key, this
    fires so AC-7 stays in lockstep. Downstream differential tests rely on
    exactly this key set to determine "unified_* fields" for AC-8 exclusion.
    """
    cap = FeedbackCapability(has_pass_fail=True)
    engine = UnifiedEngine(_FakeConfig(), _Bench(cap))
    _install_mock_llm(engine)
    obs = [_Obs(_FakeTask("t"), _FakeTrajectory(), _FakeFeedback(True, 1.0, "ok"))]
    r = engine.step(workspace=workspace, observations=obs, history=_FakeHistory(), trial=None)

    expected_keys = {
        "unified_regime",
        "unified_plan",
        "unified_reports",
        "unified_verdict",
    }
    assert set(r.metadata.keys()) == expected_keys, (
        f"StepResult.metadata keys drifted from AC-7 spec: "
        f"expected {expected_keys}, got {sorted(r.metadata.keys())}"
    )
    # All keys are prefixed unified_* so AC-8's "excluding the new
    # unified_* fields" rule correctly strips to {} here (these are all
    # additive; there are no legacy-compat keys to preserve because the
    # legacy engines each emit different, non-overlapping key sets and
    # no shared base contract exists at the metadata level).
    assert all(k.startswith("unified_") for k in r.metadata), (
        "AC-8 requires metadata additions to be prefixed unified_* so they "
        "can be excluded by the batch-entry comparison"
    )


def test_step_calls_history_rollback_when_verdict_requests(workspace):
    """AC-5: when a verifier returns Verdict(rollback=True), the engine
    must actually call ``history.rollback_workspace()`` — not just log.

    Uses a spy history that records the rollback call and a recipe
    that pins a custom verifier returning rollback=True.
    """
    from agent_evolve.algorithms.unified.registry import (
        register_verifier, VERIFIERS,
    )
    from agent_evolve.algorithms.unified.types import Verdict

    # Register a test-only verifier that always requests rollback.
    class _AlwaysRollback:
        def check(self, workspace, context, reports, trial, history, state):
            return Verdict(accept=False, rollback=True, reason="test-rollback")

    # Register under a unique name so we can also swap back.
    if "TestRollbackVerifier" not in VERIFIERS:
        register_verifier("TestRollbackVerifier")(_AlwaysRollback)

    # Build a fake history that spies on rollback_workspace calls.
    class _RollbackSpyHistory:
        def __init__(self):
            self.rollback_calls: list[str] = []

        def get_score_curve(self):
            return []

        def rollback_workspace(self, ref: str = "HEAD~1") -> None:
            self.rollback_calls.append(ref)

    # Patch UnifiedEngine to use our test verifier: inject a plan that
    # names TestRollbackVerifier. Easiest path is to patch the
    # controller's plan() to return the desired plan.
    from agent_evolve.algorithms.unified.types import Plan

    cap = FeedbackCapability(has_pass_fail=True)
    engine = UnifiedEngine(_FakeConfig(), _Bench(cap))
    engine.controller.plan = lambda regime, capability, config: Plan(
        readers=("PassFailReader",),
        operators=(),
        verifier="TestRollbackVerifier",
        artifact_scope={"skills": "rw"},
        reason_trace=("test-forced-rollback",),
    )

    spy = _RollbackSpyHistory()
    obs = [_Obs(_FakeTask("t"), _FakeTrajectory(), _FakeFeedback(True, 1.0, "ok"))]
    engine.step(workspace=workspace, observations=obs, history=spy, trial=None)

    # AC-5 invariant: rollback was actually executed.
    assert spy.rollback_calls == ["HEAD~1"], (
        f"Expected one rollback call to HEAD~1; got {spy.rollback_calls}"
    )


def test_step_is_recipe_stable_across_cycles(workspace):
    """AC-9: Plan byte-equal across 3 cycles on identical capability/config."""
    cap = FeedbackCapability(has_pass_fail=True)
    engine = UnifiedEngine(_FakeConfig(), _Bench(cap))
    _install_mock_llm(engine)
    obs = [_Obs(_FakeTask("t"), _FakeTrajectory(), _FakeFeedback(True, 1.0, "ok"))]
    plans = []
    for _ in range(3):
        r = engine.step(workspace=workspace, observations=obs, history=_FakeHistory(), trial=None)
        plans.append(r.metadata["unified_plan"])
    assert plans[0] == plans[1] == plans[2]


def test_step_does_not_import_legacy_engines(workspace):
    """AC-5 negative: UnifiedEngine module must not touch legacy engine modules.

    Complements the static import-ban test by exercising a live step() call
    with ``sys.modules`` monitoring.
    """
    import sys
    banned = ("adaptive_evolve", "adaptive_skill", "guided_synth", "skillforge")
    # Snapshot currently imported legacy engine packages.
    before = {
        m for m in sys.modules
        if any(f"agent_evolve.algorithms.{p}" in m for p in banned)
    }
    cap = FeedbackCapability(has_pass_fail=True)
    engine = UnifiedEngine(_FakeConfig(), _Bench(cap))
    _install_mock_llm(engine)
    engine.step(
        workspace=workspace,
        observations=[_Obs(_FakeTask("t"), _FakeTrajectory(), _FakeFeedback(True, 1.0, "ok"))],
        history=_FakeHistory(),
        trial=None,
    )
    after = {
        m for m in sys.modules
        if any(f"agent_evolve.algorithms.{p}" in m for p in banned)
    }
    new_legacy = after - before
    assert not new_legacy, f"UnifiedEngine.step triggered legacy engine imports: {new_legacy}"


# ── 4-benchmark routing matrix ────────────────────────────────


def _install_all_op_mocks(engine: UnifiedEngine) -> None:
    """Install mocks for every LLM operator so recipes from every regime run."""
    engine._operator_state.setdefault("LLMBashEvolve", {})["mock"] = lambda p: "ok"
    engine._operator_state.setdefault("SkillCurator", {})[
        "mock_curator"
    ] = lambda p: "ACCEPT: foo\n"
    engine._reader_state.setdefault("LLMJudgeReader", {})  # fallback path kicks in without provider


@pytest.mark.parametrize(
    "name,capability,obs_builder,expected_ops",
    [
        (
            "mcp-atlas",
            FeedbackCapability(has_pass_fail=True, has_per_claim=True, judge_available=True),
            lambda: [
                _Obs(
                    _FakeTask("t"),
                    _FakeTrajectory(),
                    _FakeFeedback(
                        True,
                        1.0,
                        "ok",
                        raw={"per_claim": [{"claim": "x", "score": 1.0}]},
                    ),
                )
            ],
            ["FixHallucinations", "AutoSeedSkills", "LLMBashEvolve", "SanityCheck"],
        ),
        (
            "swe-verified",
            FeedbackCapability(has_pass_fail=True, has_per_test=True, solver_may_propose=True, judge_available=True),
            lambda: [
                _Obs(
                    _FakeTask("t"),
                    _FakeTrajectory(
                        _skill_proposal=(
                            "ACTION: NEW\nCONFIDENCE: HIGH\nTYPE: skill\nNAME: foo\n"
                            "DESCRIPTION: d\nCONTENT:\nbody\n"
                        )
                    ),
                    _FakeFeedback(True, 1.0, "ok"),
                )
            ],
            ["WriteEpisodicMemory", "SkillCurator"],
        ),
        (
            "terminal-bench",
            FeedbackCapability(has_pass_fail=True, solver_may_propose=True, judge_available=True),
            lambda: [
                _Obs(
                    _FakeTask("t"),
                    _FakeTrajectory(conversation=[{"role": "assistant", "tool_calls": []}]),
                    _FakeFeedback(True, 1.0, "ok"),
                )
            ],
            ["LLMBashEvolve"],
        ),
        (
            "skill-bench",
            FeedbackCapability(has_pass_fail=True, has_partial_score=True, judge_available=True),
            lambda: [
                _Obs(_FakeTask("t"), _FakeTrajectory(), _FakeFeedback(True, 1.0, "ok"))
            ],
            ["LLMBashEvolve"],
        ),
    ],
)
def test_four_benchmark_routing(name, capability, obs_builder, expected_ops, workspace):
    """AC-8 subset: each of the 4 benchmark capability profiles routes to the expected recipe."""
    observations = obs_builder()
    # Terminal benchmark detection requires drafts, so simulate that.
    if name == "terminal-bench":
        workspace._drafts = [{"name": "d1", "content": "x"}]
    engine = UnifiedEngine(_FakeConfig(), _Bench(capability))
    _install_all_op_mocks(engine)
    result = engine.step(
        workspace=workspace,
        observations=observations,
        history=_FakeHistory(),
        trial=None,
    )
    ops = list(result.metadata["unified_plan"]["operators"])
    assert ops == expected_ops, (
        f"Benchmark {name} routed to unexpected operators: got {ops}, expected {expected_ops}"
    )
