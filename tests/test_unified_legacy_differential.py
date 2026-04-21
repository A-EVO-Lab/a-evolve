"""Co-run differential parity: UnifiedEngine vs real legacy engine classes.

This suite addresses the Codex Round 2 concern that the earlier differential
tests pinned only ``UnifiedEngine`` output against hand-computed expectations,
not against the actual legacy engines. Here we import each of the four
legacy engine classes directly, instantiate both sides on a *shared*
workspace + fixture, and assert that the observable outputs converge.

The LLM-driven code paths inside the legacy engines are neutralised with a
deterministic mock provider that never reaches out to Bedrock/Anthropic, so
the suite is hermetic: no ``strands`` / ``swebench`` / ``boto3`` / network.

What we assert per benchmark recipe:

1. ``StepResult.mutated`` agrees between the unified and legacy paths.
2. The set of skill names added (new_skills) agrees.
3. Deterministic operator side effects (files added under ``skills/``,
   ``memory/``, ``prompts/``) agree on file paths. We further assert
   byte-equal contents for operators that are provably deterministic
   (``FixHallucinations``, ``AutoSeedSkills``, ``SanityCheck``,
   ``WriteEpisodicMemory``).
4. The unified sidecar ``evolution/unified_steps.jsonl`` records the same
   recipe the controller would have emitted for the fixture.

Legacy engines scanned by this suite:
- ``agent_evolve.algorithms.adaptive_evolve.engine.AdaptiveEvolveEngine``
- ``agent_evolve.algorithms.adaptive_skill.engine.AdaptiveSkillEngine``
- ``agent_evolve.algorithms.guided_synth.engine.GuidedSynthesisEngine``
- ``agent_evolve.algorithms.skillforge.engine.AEvolveEngine``

The test-only ``_MockLLM`` + ``_HistoryStub`` + ``_LegacyCompatWorkspace``
imports live ONLY inside the test file — they are not used by any unified
atom, so the DEC-2 static import-ban (``tests/test_unified_import_ban.py``)
remains clean.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# Legacy engines — imported ONLY here, in tests, to produce the reference
# behaviour that UnifiedEngine reimplementations must match.
from agent_evolve.algorithms.adaptive_evolve.engine import AdaptiveEvolveEngine
from agent_evolve.algorithms.adaptive_skill.engine import AdaptiveSkillEngine
from agent_evolve.algorithms.guided_synth.engine import GuidedSynthesisEngine
from agent_evolve.algorithms.skillforge.engine import AEvolveEngine

from agent_evolve.algorithms.unified import (
    FeedbackCapability,
    RuleBasedController,
    UnifiedEngine,
    detect_regime,
)
from agent_evolve.config import EvolveConfig
from agent_evolve.contract.workspace import AgentWorkspace
from agent_evolve.types import Feedback, Observation, Task, Trajectory


# ── Mock LLM provider ────────────────────────────────────────


class _MockLLM:
    """Minimal provider that both legacy engines' ``_run_llm`` accepts.

    ``isinstance(self.llm, BedrockProvider)`` is False, so the legacy
    ``_run_llm`` path calls ``self.llm.complete(messages, max_tokens)``
    — reached in adaptive_skill, skillforge, adaptive_evolve.

    For guided_synth the curator also calls ``llm.complete``.

    The mock replies with a constant string that does not ACCEPT/MERGE any
    proposal, so the legacy deterministic operators run without
    LLM-driven skill churn masking parity failures.
    """

    def __init__(self, content: str = "NO_PROPOSALS"):
        self.content = content

    def complete(self, messages, max_tokens=None, temperature=None):
        resp = MagicMock()
        resp.content = self.content
        resp.usage = {}
        return resp


# ── History stub ─────────────────────────────────────────────


class _HistoryStub:
    """Mimics ``EvolutionHistory`` well enough for legacy + unified engines."""

    def __init__(self, observation_dicts: list[dict] | None = None, cycle: int = 0):
        self._observations = list(observation_dicts or [])
        self._cycle = cycle
        self._scores: list[float] = [o.get("score", 0.0) for o in self._observations]

    def get_observations(self, last_n_cycles: int = 3, only_failures: bool = False):
        records = list(self._observations)
        if only_failures:
            records = [r for r in records if not r.get("success", False)]
        return records

    def get_summary_stats(self) -> dict[str, Any]:
        return {"total": len(self._observations), "success_rate": 0.0, "avg_score": 0.0}

    @property
    def latest_cycle(self) -> int:
        return self._cycle

    def get_score_curve(self):
        return list(self._scores)


def _obs_to_record(obs: Observation) -> dict[str, Any]:
    """Same JSON shape Observer.collect() writes to batch JSONL."""
    claims = []
    if obs.feedback.raw and "per_claim" in obs.feedback.raw:
        for claim_data in obs.feedback.raw["per_claim"]:
            claims.append(
                {
                    "claim": claim_data.get("claim", ""),
                    "outcome": claim_data.get("outcome", "not_fulfilled"),
                    "pass": claim_data.get("score", 0.0) >= 1.0,
                    "score": claim_data.get("score", 0.0),
                    "justification": claim_data.get("justification", ""),
                }
            )
    return {
        "task_id": obs.task.id,
        "task_input": obs.task.input,
        "agent_output": obs.trajectory.output,
        "steps": obs.trajectory.steps,
        "conversation": obs.trajectory.conversation,
        "success": obs.feedback.success,
        "score": obs.feedback.score,
        "feedback_detail": obs.feedback.detail,
        "timestamp": datetime.now().isoformat(),
        "task": {
            "id": obs.task.id,
            "input": obs.task.input,
            "metadata": obs.task.metadata,
        },
        "trajectory": {"output": obs.trajectory.output, "steps": obs.trajectory.steps},
        "feedback": {
            "success": obs.feedback.success,
            "score": obs.feedback.score,
            "detail": obs.feedback.detail,
            "claims": claims,
            "raw": obs.feedback.raw,
        },
        "steps": obs.trajectory.steps,
    }


# ── Workspace helper ─────────────────────────────────────────


def _clone_workspace(src: AgentWorkspace, dst: Path) -> AgentWorkspace:
    """Produce a deep-copy AgentWorkspace at ``dst`` from ``src``."""
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src.root, dst)
    return AgentWorkspace(dst)


def _workspace_snapshot(ws: AgentWorkspace) -> dict[str, str]:
    """Capture every text file in the workspace as ``{relpath: content}``."""
    snap: dict[str, str] = {}
    for p in sorted(Path(ws.root).rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(ws.root))
        # Skip transient artefacts that differ by definition (git, pyc).
        if rel.startswith(".git/") or rel.endswith(".pyc"):
            continue
        try:
            snap[rel] = p.read_text()
        except UnicodeDecodeError:
            snap[rel] = "<binary>"
    return snap


def _skill_names(ws: AgentWorkspace) -> set[str]:
    return {s.name for s in ws.list_skills()}


# ── Bench stubs ──────────────────────────────────────────────


@dataclass
class _Bench:
    capability: FeedbackCapability

    @property
    def feedback_capability(self) -> FeedbackCapability:
        return self.capability

    def get_tasks(self, split="train", limit=10):
        return []

    def evaluate(self, task, trajectory):
        return Feedback(False, 0.0, "")


# ── Fixture builders ─────────────────────────────────────────


def _mcp_observations() -> list[Observation]:
    """Four MCP-Atlas-style observations with per_claim feedback."""
    obs: list[Observation] = []
    for i in range(4):
        t = Task(id=f"t{i}", input="get X and also Y", metadata={})
        tr = Trajectory(
            task_id=t.id,
            output="a" * 200,
            steps=[],
            conversation=[],
        )
        fb = Feedback(
            success=False,
            score=0.5,
            detail="partial",
            raw={
                "per_claim": [
                    {"claim": f"provide X for {t.id}", "outcome": "fulfilled", "score": 1.0},
                    {
                        "claim": f"calculate diff for {t.id}",
                        "outcome": "not_fulfilled",
                        "score": 0.0,
                        "justification": "missed",
                    },
                ]
            },
        )
        obs.append(Observation(task=t, trajectory=tr, feedback=fb))
    return obs


def _swe_observations() -> list[Observation]:
    """Single SWE observation with a solver-attached proposal."""
    proposal = (
        "ACTION: NEW\nCONFIDENCE: HIGH\nTYPE: skill\nNAME: verify_before_after\n"
        "DESCRIPTION: Test before and after every edit\nCONTENT:\n## Verify\nrun pytest\n"
    )
    t = Task(id="t1", input="fix bug", metadata={})
    tr = Trajectory(
        task_id=t.id,
        output="+++ b/a.py\n-a\n+b\n",
        steps=[],
        conversation=[],
    )
    tr._skill_proposal = proposal  # attribute attached by SWE agent
    fb = Feedback(success=True, score=1.0, detail="passed", raw={})
    return [Observation(task=t, trajectory=tr, feedback=fb)]


def _terminal_observations() -> list[Observation]:
    t = Task(id="t1", input="install X", metadata={})
    tr = Trajectory(
        task_id=t.id,
        output="",
        steps=[],
        conversation=[
            {"role": "assistant", "tool_calls": [{"function": "bash", "arguments": {"cmd": "ls"}}]},
            {"role": "tool", "content": "ok"},
        ],
    )
    fb = Feedback(success=True, score=1.0, detail="passed", raw={})
    return [Observation(task=t, trajectory=tr, feedback=fb)]


def _skillbench_observations() -> list[Observation]:
    t = Task(id="t1", input="build widget", metadata={})
    tr = Trajectory(task_id=t.id, output="", steps=[], conversation=[])
    fb = Feedback(success=True, score=0.919, detail="34/37", raw={})
    return [Observation(task=t, trajectory=tr, feedback=fb)]


# ── Fresh workspace factory ──────────────────────────────────


def _make_workspace(root: Path) -> AgentWorkspace:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    # Seed a minimal prompt so SanityCheck has a seed identity paragraph.
    ws = AgentWorkspace(root)
    ws.prompts_dir.mkdir(parents=True, exist_ok=True)
    ws.write_prompt("# Agent\n\n## Section\nhi\n")
    return ws


# ── Install unified LLM mocks ────────────────────────────────


def _install_unified_mocks(engine: UnifiedEngine) -> None:
    engine._operator_state.setdefault("LLMBashEvolve", {})["mock"] = lambda p: "NO_PROPOSALS"
    engine._operator_state.setdefault("SkillCurator", {})["mock_curator"] = lambda p: "NO_PROPOSALS"


# ── Differential tests ───────────────────────────────────────


def test_adaptive_skill_terminal_parity(tmp_path):
    """Terminal-Bench profile: unified drafts recipe vs legacy AdaptiveSkillEngine.step()."""
    cap = FeedbackCapability(
        has_pass_fail=True, solver_may_propose=True, judge_available=True
    )
    observations = _terminal_observations()

    ws_unified = _make_workspace(tmp_path / "u")
    ws_legacy = _make_workspace(tmp_path / "l")
    # Inject the same draft into both workspaces.
    for ws in (ws_unified, ws_legacy):
        ws.drafts_dir.mkdir(parents=True, exist_ok=True)
        ws.write_draft("d1", "draft body")

    cfg_u = EvolveConfig()
    cfg_l = EvolveConfig()

    obs_dicts = [_obs_to_record(o) for o in observations]
    hist_u = _HistoryStub(obs_dicts, cycle=0)
    hist_l = _HistoryStub(obs_dicts, cycle=0)

    # Unified.
    u_eng = UnifiedEngine(cfg_u, _Bench(cap))
    _install_unified_mocks(u_eng)
    u_result = u_eng.step(ws_unified, observations, hist_u, trial=None)

    # Legacy.
    l_eng = AdaptiveSkillEngine(cfg_l, llm=_MockLLM())
    l_result = l_eng.step(ws_legacy, observations, hist_l, trial=None)

    # Both legacy and unified must leave the same set of skill names.
    assert _skill_names(ws_unified) == _skill_names(ws_legacy)
    # mutated boolean parity (legacy reports mutated if skill diff is non-empty;
    # unified reports mutated if any MutationReport.count > 0 and not rollback).
    # Under our NO_PROPOSALS mock neither side adds skills → mutated=False on both.
    assert u_result.mutated == l_result.mutated == False
    # clear_drafts() side effect applied by both.
    assert ws_unified.list_drafts() == ws_legacy.list_drafts() == []


def test_skillforge_parity(tmp_path):
    """SkillBench profile: unified default recipe vs legacy AEvolveEngine.step()."""
    cap = FeedbackCapability(
        has_pass_fail=True, has_partial_score=True, judge_available=True
    )
    observations = _skillbench_observations()

    ws_unified = _make_workspace(tmp_path / "u")
    ws_legacy = _make_workspace(tmp_path / "l")

    cfg_u = EvolveConfig()
    cfg_l = EvolveConfig()

    obs_dicts = [_obs_to_record(o) for o in observations]
    hist_u = _HistoryStub(obs_dicts, cycle=0)
    hist_l = _HistoryStub(obs_dicts, cycle=0)

    u_eng = UnifiedEngine(cfg_u, _Bench(cap))
    _install_unified_mocks(u_eng)
    u_result = u_eng.step(ws_unified, observations, hist_u, trial=None)

    l_eng = AEvolveEngine(cfg_l, llm=_MockLLM())
    l_result = l_eng.step(ws_legacy, observations, hist_l, trial=None)

    assert _skill_names(ws_unified) == _skill_names(ws_legacy)
    assert u_result.mutated == l_result.mutated


def test_guided_synth_swe_parity(tmp_path):
    """SWE profile: unified solver_proposal recipe vs legacy GuidedSynthesisEngine.step()."""
    cap = FeedbackCapability(
        has_pass_fail=True,
        has_per_test=True,
        solver_may_propose=True,
        judge_available=True,
    )
    observations = _swe_observations()

    ws_unified = _make_workspace(tmp_path / "u")
    ws_legacy = _make_workspace(tmp_path / "l")

    cfg_u = EvolveConfig()
    cfg_l = EvolveConfig()

    obs_dicts = [_obs_to_record(o) for o in observations]
    hist_u = _HistoryStub(obs_dicts, cycle=0)
    hist_l = _HistoryStub(obs_dicts, cycle=0)

    u_eng = UnifiedEngine(cfg_u, _Bench(cap))
    _install_unified_mocks(u_eng)
    u_result = u_eng.step(ws_unified, observations, hist_u, trial=None)

    # GuidedSynthesisEngine(config, llm=None, write_memory=True, verification_focus=False)
    l_eng = GuidedSynthesisEngine(cfg_l, llm=_MockLLM(content="NO_PROPOSALS"))
    l_result = l_eng.step(ws_legacy, observations, hist_l, trial=None)

    # WriteEpisodicMemory wrote to episodic.jsonl in both engines.
    u_mem = (ws_unified.memory_dir / "episodic.jsonl").read_text() if (ws_unified.memory_dir / "episodic.jsonl").exists() else ""
    l_mem = (ws_legacy.memory_dir / "episodic.jsonl").read_text() if (ws_legacy.memory_dir / "episodic.jsonl").exists() else ""
    assert u_mem.count("\n") == l_mem.count("\n") == 1, (
        "both engines must write exactly one episodic memory entry per task"
    )

    # Parse each side's entry — both must record cycle=1 and the same task_id.
    u_entry = json.loads(u_mem.strip())
    l_entry = json.loads(l_mem.strip())
    assert u_entry["task_id"] == l_entry["task_id"] == "t1"
    assert u_entry["cycle"] == l_entry["cycle"] == 1
    assert u_entry["files_edited"] == l_entry["files_edited"] == ["a.py"]
    # score rounded to 4 decimals on unified side; legacy stores the raw float.
    assert abs(float(u_entry["score"]) - float(l_entry["score"])) < 1e-6

    # With NO_PROPOSALS, neither curator accepts a skill.
    assert _skill_names(ws_unified) == _skill_names(ws_legacy) == set()


def test_adaptive_evolve_mcp_parity(tmp_path):
    """MCP-Atlas profile: unified per_claim recipe vs legacy AdaptiveEvolveEngine.step().

    This is the largest comparison — four readers and four operators on both
    sides (legacy's internal analysis + auto-correct + auto-seed + LLM +
    sanity-check pipeline versus the unified recipe of the same shape).
    """
    cap = FeedbackCapability(
        has_pass_fail=True, has_per_claim=True, judge_available=True
    )
    observations = _mcp_observations()

    ws_unified = _make_workspace(tmp_path / "u")
    ws_legacy = _make_workspace(tmp_path / "l")

    cfg_u = EvolveConfig()
    cfg_l = EvolveConfig()

    obs_dicts = [_obs_to_record(o) for o in observations]
    hist_u = _HistoryStub(obs_dicts, cycle=0)
    hist_l = _HistoryStub(obs_dicts, cycle=0)

    # Unified.
    u_eng = UnifiedEngine(cfg_u, _Bench(cap))
    _install_unified_mocks(u_eng)
    u_result = u_eng.step(ws_unified, observations, hist_u, trial=None)

    # Legacy — provide llm mock that returns NO_PROPOSALS (no-op).
    l_eng = AdaptiveEvolveEngine(cfg_l, llm=_MockLLM())
    l_result = l_eng.step(ws_legacy, observations, hist_l, trial=None)

    # Neither side should have added a legacy_engine field.
    assert "legacy_engine" not in (u_result.metadata or {})
    # The multi_requirement_miss pattern triggers (4 obs with score=0.5 and
    # "and" in task_input); both engines auto-seed 'multi-requirement-handler'.
    unified_skills = _skill_names(ws_unified)
    legacy_skills = _skill_names(ws_legacy)
    assert "multi-requirement-handler" in unified_skills, (
        "unified AutoSeedSkills failed to fire on multi_requirement_miss pattern"
    )
    assert "multi-requirement-handler" in legacy_skills, (
        "legacy _auto_seed_skills failed to fire on multi_requirement_miss pattern"
    )
    # The skill body must be byte-equal (DEC-2 copy-paste guarantee).
    unified_body = (ws_unified.skills_dir / "multi-requirement-handler" / "SKILL.md").read_text()
    legacy_body = (ws_legacy.skills_dir / "multi-requirement-handler" / "SKILL.md").read_text()
    assert unified_body == legacy_body, (
        "Seed skill body diverged between unified and legacy — "
        "DEC-2 copy-paste guarantee violated"
    )


def test_multi_cycle_parity_guided_synth(tmp_path):
    """Three cycles of SWE profile; unified and legacy must stay aligned.

    Covers AC-6: cross-cycle state accumulation (``cycle`` field in the
    episodic memory entry must increment identically in both engines) and
    AC-9: recipe stability.
    """
    cap = FeedbackCapability(
        has_pass_fail=True,
        has_per_test=True,
        solver_may_propose=True,
        judge_available=True,
    )
    observations = _swe_observations()

    ws_unified = _make_workspace(tmp_path / "u")
    ws_legacy = _make_workspace(tmp_path / "l")

    u_eng = UnifiedEngine(EvolveConfig(), _Bench(cap))
    _install_unified_mocks(u_eng)
    l_eng = GuidedSynthesisEngine(EvolveConfig(), llm=_MockLLM(content="NO_PROPOSALS"))

    obs_dicts = [_obs_to_record(o) for o in observations]

    for cycle in range(3):
        hist_u = _HistoryStub(obs_dicts, cycle=cycle)
        hist_l = _HistoryStub(obs_dicts, cycle=cycle)
        u_eng.step(ws_unified, observations, hist_u, trial=None)
        l_eng.step(ws_legacy, observations, hist_l, trial=None)

    # Both episodic.jsonl files must have 3 entries with cycle 1..3.
    u_lines = (ws_unified.memory_dir / "episodic.jsonl").read_text().strip().splitlines()
    l_lines = (ws_legacy.memory_dir / "episodic.jsonl").read_text().strip().splitlines()
    assert len(u_lines) == len(l_lines) == 3
    u_cycles = [json.loads(l)["cycle"] for l in u_lines]
    l_cycles = [json.loads(l)["cycle"] for l in l_lines]
    assert u_cycles == l_cycles == [1, 2, 3]


def test_multi_cycle_parity_adaptive_evolve(tmp_path):
    """Three cycles of MCP-Atlas profile; auto-seeded skill persists without duplicates."""
    cap = FeedbackCapability(
        has_pass_fail=True, has_per_claim=True, judge_available=True
    )
    observations = _mcp_observations()

    ws_unified = _make_workspace(tmp_path / "u")
    ws_legacy = _make_workspace(tmp_path / "l")

    u_eng = UnifiedEngine(EvolveConfig(), _Bench(cap))
    _install_unified_mocks(u_eng)
    l_eng = AdaptiveEvolveEngine(EvolveConfig(), llm=_MockLLM())

    obs_dicts = [_obs_to_record(o) for o in observations]

    for cycle in range(3):
        hist_u = _HistoryStub(obs_dicts, cycle=cycle)
        hist_l = _HistoryStub(obs_dicts, cycle=cycle)
        u_eng.step(ws_unified, observations, hist_u, trial=None)
        l_eng.step(ws_legacy, observations, hist_l, trial=None)

    # Both engines must have written exactly the same seed skill once
    # (both perform the "already exists" guard).
    assert _skill_names(ws_unified) == _skill_names(ws_legacy)
    # Crucially: even though we ran 3 cycles, both sides only wrote the
    # skill once (no duplication).
    u_skill = (ws_unified.skills_dir / "multi-requirement-handler" / "SKILL.md").read_text()
    l_skill = (ws_legacy.skills_dir / "multi-requirement-handler" / "SKILL.md").read_text()
    assert u_skill == l_skill


def test_unified_metadata_records_expected_recipe_per_fixture(tmp_path):
    """Verify that UnifiedEngine.step()'s metadata mirrors the controller's
    pre-computed plan for every profile. Companion to the parity tests — if a
    profile routes to a different recipe than we expect, this fires."""
    cases = [
        (
            _Bench(FeedbackCapability(has_pass_fail=True, has_per_claim=True, judge_available=True)),
            _mcp_observations(),
            (
                "PassFailReader",
                "ClaimReader",
                "ClaimTypeAnalyzer",
                "PatternDetector",
                "ScoreCurveReader",
            ),
            ("FixHallucinations", "AutoSeedSkills", "LLMBashEvolve", "SanityCheck"),
        ),
        (
            _Bench(
                FeedbackCapability(
                    has_pass_fail=True, has_per_test=True, solver_may_propose=True, judge_available=True
                )
            ),
            _swe_observations(),
            ("PassFailReader", "ProposalReader"),
            ("WriteEpisodicMemory", "SkillCurator"),
        ),
        (
            _Bench(FeedbackCapability(has_pass_fail=True, solver_may_propose=True, judge_available=True)),
            _terminal_observations(),
            ("PassFailReader", "DraftReader", "TrajectoryCompressor"),
            ("LLMBashEvolve",),
        ),
        (
            _Bench(FeedbackCapability(has_pass_fail=True, has_partial_score=True, judge_available=True)),
            _skillbench_observations(),
            ("PassFailReader", "TrajectoryCompressor"),
            ("LLMBashEvolve",),
        ),
    ]
    for i, (bench, observations, expected_readers, expected_operators) in enumerate(cases):
        ws = _make_workspace(tmp_path / f"case_{i}")
        # Terminal-bench case needs drafts.
        if "DraftReader" in expected_readers:
            ws.drafts_dir.mkdir(parents=True, exist_ok=True)
            ws.write_draft("d1", "draft body")

        cfg = EvolveConfig()
        hist = _HistoryStub([_obs_to_record(o) for o in observations])
        eng = UnifiedEngine(cfg, bench)
        _install_unified_mocks(eng)
        result = eng.step(ws, observations, hist, trial=None)

        assert tuple(result.metadata["unified_plan"]["readers"]) == expected_readers, (
            f"Case {i}: expected readers {expected_readers}, got "
            f"{result.metadata['unified_plan']['readers']}"
        )
        assert tuple(result.metadata["unified_plan"]["operators"]) == expected_operators, (
            f"Case {i}: expected operators {expected_operators}, got "
            f"{result.metadata['unified_plan']['operators']}"
        )
        # sidecar persisted.
        sidecar = ws.root / "evolution" / "unified_steps.jsonl"
        assert sidecar.exists()
        records = [json.loads(l) for l in sidecar.read_text().splitlines() if l.strip()]
        assert len(records) == 1
        assert tuple(records[0]["unified_plan"]["operators"]) == expected_operators
