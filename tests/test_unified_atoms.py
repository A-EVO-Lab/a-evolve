"""Unit tests for unified reader / operator / verifier atoms.

Covers AC-3 (protocol conformance, registration), AC-6 (per-atom state),
AC-8 (canonicalized reader outputs), and parts of AC-5 (scope enforcement).

LLM-backed atoms (``LLMJudgeReader``, ``LLMBashEvolve``, ``SkillCurator``,
``PruneSkills``) are exercised through their mock hooks so the suite runs
without provider credentials.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_evolve.algorithms.unified import (
    EvidenceContext,
    MutationReport,
    Plan,
    RegimeTag,
    Verdict,
    detect_regime,
)
from agent_evolve.algorithms.unified.registry import (
    OPERATORS,
    READERS,
    VERIFIERS,
    get_operator,
    get_reader,
    get_verifier,
)
from agent_evolve.algorithms.unified.controller import RuleBasedController
from agent_evolve.algorithms.unified.types import FeedbackCapability


# ── Fixtures ───────────────────────────────────────────────────


class _FakeFeedback:
    def __init__(self, success=False, score=0.0, detail="", raw=None):
        self.success = success
        self.score = score
        self.detail = detail
        self.raw = raw or {}


class _FakeTrajectory:
    def __init__(
        self,
        output: str = "",
        steps: list | None = None,
        conversation: list | None = None,
        skill_proposal: str = "",
    ):
        self.output = output
        self.steps = steps or []
        self.conversation = conversation or []
        self._skill_proposal = skill_proposal


class _FakeTask:
    def __init__(self, task_id: str, text: str = ""):
        self.id = task_id
        self.input = text


class _Obs:
    def __init__(self, task, trajectory, feedback):
        self.task = task
        self.trajectory = trajectory
        self.feedback = feedback


class _FakeWorkspace:
    """Minimal AgentWorkspace stand-in using a real tmp dir for I/O tests."""

    def __init__(self, root: Path):
        self.root = root
        self.memory_dir = root / "memory"
        self.prompts_dir = root / "prompts"
        self.skills_dir = root / "skills"
        self._drafts: list[dict[str, str]] = []

    # Skill lifecycle.
    def list_skills(self):
        out = []
        if self.skills_dir.exists():
            for d in sorted(self.skills_dir.iterdir()):
                if d.is_dir() and (d / "SKILL.md").exists():
                    out.append(SimpleNamespace(name=d.name))
        return out

    def read_skill(self, name: str) -> str:
        p = self.skills_dir / name / "SKILL.md"
        return p.read_text() if p.exists() else ""

    def write_skill(self, name: str, content: str) -> None:
        (self.skills_dir / name).mkdir(parents=True, exist_ok=True)
        (self.skills_dir / name / "SKILL.md").write_text(content)

    def delete_skill(self, name: str) -> None:
        import shutil
        d = self.skills_dir / name
        if d.exists():
            shutil.rmtree(d)

    # Prompt.
    def read_prompt(self) -> str:
        p = self.prompts_dir / "system.md"
        return p.read_text() if p.exists() else ""

    def write_prompt(self, content: str) -> None:
        self.prompts_dir.mkdir(parents=True, exist_ok=True)
        (self.prompts_dir / "system.md").write_text(content)

    # Memory.
    def add_memory(self, entry: dict, category: str = "episodic") -> None:
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        with open(self.memory_dir / f"{category}.jsonl", "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    # Fragments.
    def list_fragments(self):
        frag_dir = self.prompts_dir / "fragments"
        if not frag_dir.exists():
            return []
        return sorted(f.name for f in frag_dir.iterdir() if f.is_file())

    def read_fragment(self, name: str) -> str:
        p = self.prompts_dir / "fragments" / name
        return p.read_text() if p.exists() else ""

    # Drafts.
    def list_drafts(self):
        return list(self._drafts)

    def clear_drafts(self):
        self._drafts.clear()

    def set_drafts(self, drafts):
        self._drafts = list(drafts)


@pytest.fixture
def workspace(tmp_path):
    ws = _FakeWorkspace(tmp_path)
    ws.memory_dir.mkdir(parents=True, exist_ok=True)
    ws.prompts_dir.mkdir(parents=True, exist_ok=True)
    ws.skills_dir.mkdir(parents=True, exist_ok=True)
    return ws


# ── Readers ────────────────────────────────────────────────────


def test_pass_fail_reader_aggregates_and_rounds(workspace):
    obs = [
        _Obs(_FakeTask("a"), _FakeTrajectory(), _FakeFeedback(True, 1.0)),
        _Obs(_FakeTask("b"), _FakeTrajectory(), _FakeFeedback(False, 0.333333)),
    ]
    out = get_reader("PassFailReader").read(obs, workspace, None, None, EvidenceContext(), {})
    assert out["n_tasks"] == 2
    assert out["n_pass"] == 1
    assert out["pass_rate"] == 0.5
    assert out["per_task"][0]["task_id"] == "a"
    assert out["per_task"][1]["score"] == 0.3333


def test_draft_reader_sorts_by_name(workspace):
    workspace.set_drafts([{"name": "b", "content": "x"}, {"name": "a", "content": "y"}])
    out = get_reader("DraftReader").read([], workspace, None, None, EvidenceContext(), {})
    assert [d["name"] for d in out["drafts"]] == ["a", "b"]
    assert out["n_drafts"] == 2


def test_proposal_reader_parses_and_filters_none():
    obs = [
        _Obs(
            _FakeTask("t1"),
            _FakeTrajectory(
                skill_proposal="ACTION: NEW\nCONFIDENCE: HIGH\nTYPE: skill\nNAME: foo\nDESCRIPTION: bar\nCONTENT:\nbody\n"
            ),
            _FakeFeedback(),
        ),
        _Obs(_FakeTask("t2"), _FakeTrajectory(skill_proposal="ACTION: NONE"), _FakeFeedback()),
    ]
    out = get_reader("ProposalReader").read(obs, None, None, None, EvidenceContext(), {})
    assert out["n_proposals"] == 1
    p = out["proposals"][0]
    assert p["name"] == "foo"
    assert p["action"] == "NEW"
    assert p["confidence"] == "HIGH"
    assert "body" in p["content"]


def test_trajectory_compressor_is_deterministic():
    conv = [
        {"role": "assistant", "tool_calls": [{"function": "bash", "arguments": {"cmd": "ls"}}]},
        {"role": "tool", "content": "ok"},
        {"role": "assistant", "tool_calls": [{"function": "bash", "arguments": {"cmd": "pwd"}}]},
        {"role": "tool", "content": "ERROR: oops"},
    ]
    obs = [_Obs(_FakeTask("t"), _FakeTrajectory(conversation=conv), _FakeFeedback())]
    reader = get_reader("TrajectoryCompressor")
    a = reader.read(obs, None, None, None, EvidenceContext(), {})
    b = reader.read(obs, None, None, None, EvidenceContext(), {})
    assert a == b
    assert "Commands: 2" in a["per_task"][0]["compressed"]


def test_claim_reader_extracts_per_claim():
    obs = [
        _Obs(
            _FakeTask("t1"),
            _FakeTrajectory(),
            _FakeFeedback(
                raw={
                    "per_claim": [
                        {"claim": "x", "outcome": "fulfilled", "score": 1.0},
                        {"claim": "y", "outcome": "not_fulfilled", "score": 0.0, "justification": "z"},
                    ]
                }
            ),
        )
    ]
    out = get_reader("ClaimReader").read(obs, None, None, None, EvidenceContext(), {})
    assert out["n_claims"] == 2
    assert out["all_claims"][0]["claim"] == "x"  # sorted by task_id then claim


def test_claim_type_analyzer_ranks_weakest():
    ctx = EvidenceContext()
    ctx.entries["ClaimReader"] = {
        "all_claims": [
            {"task_id": "t", "claim": "calculate sum", "score": 0.0, "outcome": "not_fulfilled", "justification": "miss"},
            {"task_id": "t", "claim": "calculate diff", "score": 0.0, "outcome": "not_fulfilled", "justification": ""},
            {"task_id": "t", "claim": "provide fact", "score": 1.0, "outcome": "fulfilled", "justification": ""},
        ]
    }
    out = get_reader("ClaimTypeAnalyzer").read([], None, None, None, ctx, {})
    assert out["by_type"]["calculate"]["pass_rate"] == 0.0
    # weakest should have calculate first.
    assert out["weakest"][0][0] == "calculate"


def test_pattern_detector_multi_requirement_miss():
    obs = []
    for i in range(4):
        obs.append(
            _Obs(
                _FakeTask(f"t{i}", text="get X and also Y"),
                _FakeTrajectory(output="an output " * 20),
                _FakeFeedback(score=0.5),
            )
        )
    out = get_reader("PatternDetector").read(obs, None, None, None, EvidenceContext(), {})
    names = out["names"]
    assert "multi_requirement_miss" in names


def test_score_curve_reader_rounds_and_exposes_best():
    hist = SimpleNamespace(get_score_curve=lambda: [0.111111, 0.5, 0.6])
    out = get_reader("ScoreCurveReader").read([], None, hist, None, EvidenceContext(), {})
    assert out["scores"] == [0.1111, 0.5, 0.6]
    assert out["best"] == 0.6
    assert out["latest"] == 0.6
    assert out["n_cycles"] == 3


def test_llm_judge_reader_falls_back_when_provider_missing(monkeypatch):
    # Force ImportError on bedrock provider.
    import importlib
    import sys

    monkeypatch.setitem(sys.modules, "agent_evolve.llm.bedrock", None)
    reader = get_reader("LLMJudgeReader")
    out = reader.read(
        [_Obs(_FakeTask("t"), _FakeTrajectory(), _FakeFeedback())],
        None,
        None,
        None,
        EvidenceContext(),
        {},
    )
    assert out["per_task"][0]["score"] == -1


# ── Operators ──────────────────────────────────────────────────


def test_fix_hallucinations_writes_skill_and_prunes(workspace):
    # Seed 20 memory entries — expected to prune to 15 (default cap).
    for i in range(20):
        (workspace.memory_dir / "episodic.jsonl").parent.mkdir(parents=True, exist_ok=True)
        with open(workspace.memory_dir / "episodic.jsonl", "a") as f:
            f.write(json.dumps({"i": i}) + "\n")

    ctx = EvidenceContext()
    ctx.entries["PatternDetector"] = {
        "hallucination_map": {"wrong_name": "right_name"},
        "param_errors": [],
    }
    op = get_operator("FixHallucinations")
    report = op.apply(workspace, ctx, {"skills": "rw", "memory": "append"}, {})
    assert report.count >= 1
    skill_path = workspace.skills_dir / "tool-name-corrections" / "SKILL.md"
    assert skill_path.exists()
    # memory pruned.
    lines = (workspace.memory_dir / "episodic.jsonl").read_text().strip().splitlines()
    assert len(lines) == 15


def test_auto_seed_skills_seeds_on_threshold(workspace):
    ctx = EvidenceContext()
    ctx.entries["PatternDetector"] = {
        "patterns": [
            {
                "pattern_name": "multi_requirement_miss",
                "count": 5,
                "task_ids": ["a", "b", "c"],
                "description": "",
                "suggested_fix": "",
            }
        ]
    }
    ctx.entries["ClaimTypeAnalyzer"] = {"by_type": {}, "weakest": []}
    op = get_operator("AutoSeedSkills")
    report = op.apply(workspace, ctx, {"skills": "rw"}, {})
    assert "multi-requirement-handler" in report.details["seeded"]


def test_auto_seed_skills_rejects_without_write_scope(workspace):
    ctx = EvidenceContext()
    ctx.entries["PatternDetector"] = {"patterns": []}
    op = get_operator("AutoSeedSkills")
    report = op.apply(workspace, ctx, {"skills": "ro"}, {})
    assert report.count == 0


def test_sanity_check_removes_empty_skill(workspace):
    # Seed a skill with a too-short body.
    workspace.write_skill("tiny", "---\nname: tiny\ndescription: x\n---\n\ni\n")
    workspace.write_prompt("# Agent\n\n## Section\n")
    op = get_operator("SanityCheck")
    report = op.apply(
        workspace,
        EvidenceContext(),
        {"skills": "rw", "prompts": "rw"},
        {"seed_prompt": workspace.read_prompt()},
    )
    assert any("Removed empty skill" in f for f in report.details["fixes"])


def test_write_episodic_memory_accumulates_cycle(workspace):
    obs = [_Obs(_FakeTask("t1"), _FakeTrajectory(output="+++ b/a.py\n"), _FakeFeedback(score=0.5))]
    ctx = EvidenceContext()
    ctx.entries["__observations__"] = obs
    ctx.entries["PassFailReader"] = {"per_task": [{"task_id": "t1", "score": 0.5}]}
    op = get_operator("WriteEpisodicMemory")
    state: dict[str, Any] = {}
    op.apply(workspace, ctx, {"memory": "append"}, state)
    op.apply(workspace, ctx, {"memory": "append"}, state)
    # cycle_count should be 2 after two applies.
    assert state["cycle_count"] == 2
    lines = (workspace.memory_dir / "episodic.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    data = [json.loads(l) for l in lines]
    assert data[0]["cycle"] == 1 and data[1]["cycle"] == 2


def test_skill_curator_applies_accept_decision(workspace):
    ctx = EvidenceContext()
    ctx.entries["ProposalReader"] = {
        "proposals": [
            {
                "source_task_id": "t1",
                "name": "new_skill",
                "description": "d",
                "content": "body",
                "action": "NEW",
                "confidence": "HIGH",
                "target": "",
                "analysis": "",
                "raw": "",
            }
        ]
    }
    op = get_operator("SkillCurator")
    state = {"mock_curator": lambda prompt: "ACCEPT: new_skill\n"}
    report = op.apply(workspace, ctx, {"skills": "rw"}, state)
    assert report.count == 1
    assert (workspace.skills_dir / "new_skill" / "SKILL.md").exists()


def test_llm_bash_evolve_uses_mock_and_canonicalizes(workspace):
    seen_prompts: list[str] = []

    def mock(prompt: str) -> str:
        seen_prompts.append(prompt)
        return "ok"

    ctx = EvidenceContext()
    # Two readers with different insertion order — canonical JSON should
    # produce identical output.
    ctx.entries["ClaimReader"] = {"b": 2, "a": 1}
    ctx.entries["PatternDetector"] = {"names": ["z", "a"]}
    op = get_operator("LLMBashEvolve")
    state = {"mock": mock}
    op.apply(workspace, ctx, {"skills": "rw"}, state)
    op.apply(workspace, ctx, {"skills": "rw"}, state)
    assert len(seen_prompts) == 2

    # Normalize the mutable cycle number wherever it appears so what remains
    # is purely the canonicalized evidence serialization.
    import re

    def _canon(s: str) -> str:
        s = re.sub(r"Cycle #\d+", "Cycle #N", s)
        s = re.sub(r'"cycle": \d+', '"cycle": N', s)
        return s

    assert _canon(seen_prompts[0]) == _canon(seen_prompts[1])
    # And sort_keys produces alphabetical key order on the evidence.
    assert '"a": 1, "b": 2' in seen_prompts[0]


def test_prune_skills_noop_under_3_items(workspace):
    workspace.write_skill("s1", "---\nname: s1\ndescription: d\n---\n\nbody\n")
    op = get_operator("PruneSkills")
    report = op.apply(workspace, EvidenceContext(), {"skills": "rw", "prompts": "rw"}, {})
    assert report.count == 0


# ── Verifiers ──────────────────────────────────────────────────


def test_no_verify_always_accepts():
    v = get_verifier("NoVerify")
    verdict = v.check(None, EvidenceContext(), [], None, None, {})
    assert verdict.accept is True and verdict.rollback is False


def test_stagnation_rollback_triggers_after_window():
    v = get_verifier("StagnationRollback")
    state: dict[str, Any] = {
        "improvement_threshold": 0.02,
        "stagnation_window": 3,
        "best_pass_rate": 0.5,
    }
    # Feed three cycles with declining pass_rate; no improvement
    # but degradation is large enough to trigger.
    for pr in (0.3, 0.3, 0.3):
        ctx = EvidenceContext()
        ctx.entries["PassFailReader"] = {"pass_rate": pr}
        verdict = v.check(None, ctx, [], None, None, state)
    assert verdict.rollback is True


def test_stagnation_rollback_accepts_on_improvement():
    v = get_verifier("StagnationRollback")
    state: dict[str, Any] = {"best_pass_rate": 0.5}
    ctx = EvidenceContext()
    ctx.entries["PassFailReader"] = {"pass_rate": 0.6}
    verdict = v.check(None, ctx, [], None, None, state)
    assert verdict.accept is True
    assert state["best_pass_rate"] == 0.6


# ── Scope enforcement ──────────────────────────────────────────


def test_operator_scope_violation_raises(workspace):
    from agent_evolve.algorithms.unified.engine import _enforce_scope
    from agent_evolve.algorithms.unified.interfaces import ScopeViolationError

    op = get_operator("AutoSeedSkills")  # declares WRITES={'skills'}
    # scope.grants nothing -> violation
    with pytest.raises(ScopeViolationError):
        _enforce_scope(op, {"skills": "ro"}, "AutoSeedSkills")
    # With rw it should not raise.
    _enforce_scope(op, {"skills": "rw"}, "AutoSeedSkills")


def test_llm_bash_evolve_requires_at_least_one_grant():
    """LLMBashEvolve's WRITES is the maximum set — at least one must be granted."""
    from agent_evolve.algorithms.unified.engine import _enforce_scope
    from agent_evolve.algorithms.unified.interfaces import ScopeViolationError

    op = get_operator("LLMBashEvolve")
    # Empty scope -> violation.
    with pytest.raises(ScopeViolationError):
        _enforce_scope(op, {}, "LLMBashEvolve")
    # Just skills granted is sufficient.
    _enforce_scope(op, {"skills": "rw"}, "LLMBashEvolve")
    # Just memory granted is sufficient.
    _enforce_scope(op, {"memory": "append"}, "LLMBashEvolve")
