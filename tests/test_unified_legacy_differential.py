"""Co-run differential parity: UnifiedEngine vs real legacy engine classes.

Addresses Codex rounds 2 and 3. Each of the four legacy engine classes is
imported directly and run on a *shared* workspace + fixture with a mock
LLM provider. The suite asserts parity across the full ``StepResult``
contract — ``mutated``, ``summary`` (shape-normalised), and a normalized
subset of ``metadata`` — plus filesystem side effects.

Both **no-op** parity (the default ``NO_PROPOSALS`` case, kept from
Round 3 as a supplemental guard) and **positive-mutation** parity are
covered. For LLM-driven engines (``AdaptiveSkillEngine``, ``AEvolveEngine``)
a Bedrock-compatible mock provider is injected; its ``converse_loop``
runs a real bash command that writes a skill into the workspace, exactly
matching the legacy path that would invoke the ``workspace_bash`` tool.
That proves unified and legacy engines produce the same filesystem delta
under a real mutation, not only when the LLM declines to act.

Legacy engines scanned:
- ``agent_evolve.algorithms.adaptive_evolve.engine.AdaptiveEvolveEngine``
- ``agent_evolve.algorithms.adaptive_skill.engine.AdaptiveSkillEngine``
- ``agent_evolve.algorithms.guided_synth.engine.GuidedSynthesisEngine``
- ``agent_evolve.algorithms.skillforge.engine.AEvolveEngine``

Legacy imports live only in this test module — the static import-ban
(``tests/test_unified_import_ban.py``) still audits the unified
production tree as clean.

Hermeticity guarantees:
- No imports of ``strands`` / ``swebench`` / ``boto3`` / network clients
- ``_FakeBedrockProvider`` bypasses ``BedrockProvider.__init__`` via
  ``__new__`` so the ``boto3`` requirement never fires
- Full suite runs in <1 second
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


# ── Mock LLM providers ───────────────────────────────────────


class _MockLLM:
    """Plain LLM provider that both legacy and unified engines accept.

    ``isinstance(self, BedrockProvider)`` is False, so legacy ``_run_llm``
    paths fall through to ``self.llm.complete(messages, max_tokens)``.
    Used for guided_synth's curator and for adaptive_evolve's prompt call
    (which is a no-op under ``NO_PROPOSALS``).
    """

    def __init__(self, content: str = "NO_PROPOSALS"):
        self.content = content

    def complete(self, messages, max_tokens=None, temperature=None):
        resp = MagicMock()
        resp.content = self.content
        resp.usage = {}
        return resp


class _FakeBedrockProvider:
    """Bedrock-compatible stub that passes ``isinstance(..., BedrockProvider)``.

    Skips the real ``BedrockProvider.__init__`` (which requires ``boto3``)
    by being instantiated through ``__new__``. Legacy engines' ``_run_llm``
    route through this class' ``converse_loop`` method; unified
    ``LLMBashEvolve`` does the same when ``state['llm_provider']`` is set.

    ``converse_loop`` invokes the provided bash tool once per element of
    ``bash_commands`` so the mutation seen by the legacy engine is
    identical to the mutation the unified engine sees.
    """

    @classmethod
    def build(
        cls, bash_commands: list[str], response_content: str = "done"
    ) -> "Any":
        """Produce a real ``BedrockProvider`` subclass instance without running its __init__."""
        from agent_evolve.llm.bedrock import BedrockProvider

        obj = BedrockProvider.__new__(BedrockProvider)
        # Shadow instance attributes so the isinstance check still passes
        # and the legacy runtime has something to inspect.
        obj.model_id = "mock-bedrock"
        obj.region = "mock-region"
        obj.client = None
        obj._bash_commands = list(bash_commands)
        obj._response_content = response_content

        def converse_loop(
            system_prompt,
            user_message,
            tools,
            tool_executor,
            max_tokens=None,
            temperature=0.0,
        ):
            bash = tool_executor.get("workspace_bash")
            if bash is not None:
                for cmd in obj._bash_commands:
                    bash(cmd)
            resp = MagicMock()
            resp.content = obj._response_content
            resp.usage = {"input_tokens": 0, "output_tokens": 0}
            return resp

        def complete(messages, max_tokens=None, temperature=None):
            resp = MagicMock()
            resp.content = obj._response_content
            resp.usage = {"input_tokens": 0, "output_tokens": 0}
            return resp

        obj.converse_loop = converse_loop
        obj.complete = complete
        return obj


# ── StepResult comparison helpers ────────────────────────────


def _normalize_unified_metadata(md: dict, workspace: Any) -> dict:
    """Extract the common-signal view of a UnifiedEngine StepResult.metadata.

    Reads the per-operator reports and the workspace itself to compute the
    canonical observable signal dict. ``WriteEpisodicMemory`` returns
    ``count=0`` by design (memory appends don't influence
    ``StepResult.mutated`` in legacy guided_synth either); we detect it via
    ``details.tasks_written`` and also via the on-disk ``episodic.jsonl``.
    """
    reports = md.get("unified_reports", [])
    plan = md.get("unified_plan", {})
    verdict = md.get("unified_verdict", {})

    total_count = sum(int(r.get("count", 0)) for r in reports)
    mutating_ops: list[str] = []
    skills_added: list[str] = []
    skills_removed: list[str] = []
    memory_rows = 0
    for r in reports:
        name = r["operator_name"]
        details = r.get("details") or {}
        if int(r.get("count", 0)) > 0:
            mutating_ops.append(name)
        if name == "WriteEpisodicMemory":
            tasks_written = int(details.get("tasks_written", 0))
            memory_rows += tasks_written
            if tasks_written > 0 and name not in mutating_ops:
                mutating_ops.append(name)
                total_count += tasks_written
        for sk in details.get("skills_added", []) or []:
            skills_added.append(sk)
        for sk in details.get("skills_removed", []) or []:
            skills_removed.append(sk)
        for seeded in (details.get("seeded", []) or []):
            skills_added.append(seeded)
        for item in (details.get("applied", []) or []):
            # guided_synth SkillCurator reports applied as "accept:name".
            if isinstance(item, str) and ":" in item:
                skills_added.append(item.split(":", 1)[1])

    # Also derive memory rows from the filesystem (belt-and-braces).
    mem_path = Path(workspace.root) / "memory" / "episodic.jsonl"
    if mem_path.exists() and memory_rows == 0:
        lines = [l for l in mem_path.read_text().splitlines() if l.strip()]
        memory_rows = len(lines)

    skills_on_disk = {s.name for s in workspace.list_skills()}

    return {
        "total_mutations": total_count,
        "mutating_ops": sorted(set(mutating_ops)),
        "verdict_accept": bool(verdict.get("accept", True)),
        "verdict_rollback": bool(verdict.get("rollback", False)),
        "skills_on_disk": sorted(skills_on_disk),
        "memory_rows_written": memory_rows,
    }


def _normalize_legacy_metadata(
    md: dict,
    engine_name: str,
    workspace: Any,
    observations_count: int,
) -> dict:
    """Extract a comparable common-signal view of a legacy engine's outputs.

    Each legacy engine exposes different metadata keys. Rather than
    heuristically parsing metadata fields that overlap in legacy (e.g.,
    ``AdaptiveEvolveEngine.metadata["auto_fixes"]`` bundles both
    hallucination corrections AND auto-seeded skills), this helper reads
    *workspace* signals — the same observables the unified engine
    emits. Returned keys mirror :func:`_normalize_unified_metadata` so
    ``assert legacy == unified`` is meaningful.
    """
    total = 0
    mutating_ops: list[str] = []
    skills = {s.name for s in workspace.list_skills()}
    memory_rows = 0
    mem_path = Path(workspace.root) / "memory" / "episodic.jsonl"
    if mem_path.exists():
        lines = [l for l in mem_path.read_text().splitlines() if l.strip()]
        memory_rows = len(lines)

    if engine_name == "AdaptiveEvolveEngine":
        if "tool-name-corrections" in skills:
            mutating_ops.append("FixHallucinations")
            total += 1
        seeded = {
            n
            for n in skills
            if n in ("multi-requirement-handler", "entity-verification")
            or n.endswith("-handler")
        } - {"tool-name-corrections"}
        if seeded:
            mutating_ops.append("AutoSeedSkills")
            total += len(seeded)
        sanity_fixes = md.get("sanity_fixes", []) or []
        if isinstance(sanity_fixes, list) and len(sanity_fixes) > 0:
            mutating_ops.append("SanityCheck")
            total += len(sanity_fixes)
    elif engine_name in ("AdaptiveSkillEngine", "AEvolveEngine"):
        new_skills = int(md.get("new_skills", 0) or 0)
        if new_skills > 0:
            mutating_ops.append("LLMBashEvolve")
            total += new_skills
    elif engine_name == "GuidedSynthesisEngine":
        if memory_rows > 0:
            mutating_ops.append("WriteEpisodicMemory")
            total += observations_count
        applied = md.get("applied", [])
        if applied:
            mutating_ops.append("SkillCurator")
            total += len(applied) if isinstance(applied, list) else 1

    return {
        "total_mutations": total,
        "mutating_ops": sorted(mutating_ops),
        "verdict_accept": True,
        "verdict_rollback": False,
        "skills_on_disk": sorted(skills),
        "memory_rows_written": memory_rows,
    }


_LEGACY_SUMMARY_PATTERNS: dict[str, list[tuple[str, str]]] = {
    # (label, regex) — regex must have one capturing group returning a
    # non-negative integer. Labels match a key in
    # :func:`_extract_unified_summary_signals` so parity is asserted across
    # both engine families.
    "AdaptiveSkillEngine": [
        # Summary format: "A-Evolve: <N> new skills, <M> drafts reviewed"
        ("skills_added", r"(\d+) new skills"),
    ],
    "AEvolveEngine": [
        ("skills_added", r"(\d+) new skills"),
    ],
    "GuidedSynthesisEngine": [
        # Summary format: "guided-synth cycle N: curated P proposals, applied A: [...]"
        ("proposals_applied", r"applied (\d+)"),
    ],
    "AdaptiveEvolveEngine": [
        # Summary format: "AdaptiveEvolve: F auto-fixes, N new skills, P patterns detected"
        # legacy measures skills_before AFTER auto-seed (engine.py:211), so
        # `new skills` in the summary is strictly LLM-added (excluding auto-seed).
        # `auto-fixes` bundles hallucination corrections + auto-seed + sanity
        # fixes and is the closest analog to the unified total_mutations.
        ("total_mutations", r"(\d+) auto-fixes"),
    ],
}


def _extract_summary_signals(summary: str, patterns: list[tuple[str, str]]) -> dict[str, int]:
    """Pull numeric counts out of a human-readable summary string."""
    import re

    out: dict[str, int] = {}
    for label, pattern in patterns:
        m = re.search(pattern, summary)
        if m is not None:
            try:
                out[label] = int(m.group(1))
            except (ValueError, IndexError):
                pass
    return out


_UNIFIED_SUMMARY_PATTERNS: list[tuple[str, str]] = [
    # Matches "N skills_added" and "N skills_removed" from the unified
    # summary format produced by ``UnifiedEngine.step()``.
    ("skills_added", r"(\d+) skills_added"),
    ("skills_removed", r"(\d+) skills_removed"),
    ("total_mutations", r"(\d+) mutations"),
]


def _extract_unified_summary_signals(summary: str) -> dict[str, int]:
    return _extract_summary_signals(summary, _UNIFIED_SUMMARY_PATTERNS)


def _assert_step_result_parity(
    unified_result,
    legacy_result,
    legacy_engine_name: str,
    *,
    expect_mutation: bool,
    unified_workspace,
    legacy_workspace,
    observations_count: int,
) -> None:
    """Full StepResult parity check covering every field of StepResult.

    StepResult has four fields: ``mutated``, ``summary``, ``metadata``,
    ``stop``. This helper asserts parity on all four:

    1. ``mutated``: byte-equal boolean.
    2. ``stop``: byte-equal boolean (both engines leave it at False for
       normal step() calls; only GEPA opts into ``stop=True``).
    3. ``summary``: meaningful — both strings are non-empty AND numeric
       signals extracted via engine-specific regex agree between
       unified and legacy (e.g., ``N new skills`` in legacy == the
       ``N skills_added`` count in the unified summary).
    4. ``metadata``: normalized signal set agrees. Both helpers return the
       *same 6 keys* (``total_mutations``, ``mutating_ops``,
       ``skills_on_disk``, ``memory_rows_written``, ``verdict_accept``,
       ``verdict_rollback``) and a final full-dict assertion enforces key
       parity — any silent addition to either normalizer becomes a test
       failure. Legacy signals are read from workspace observables
       rather than heuristic metadata keys. The plan's ``recipe_operators``
       is covered separately in
       ``test_unified_metadata_records_expected_recipe_per_fixture``.
    """
    # Field 1: mutated
    assert unified_result.mutated == legacy_result.mutated, (
        f"mutated disagreement: unified={unified_result.mutated} "
        f"legacy={legacy_result.mutated}"
    )
    if expect_mutation:
        assert unified_result.mutated is True

    # Field 4 (first, so one side can't silently opt out of the loop):
    # stop — both sides must leave it at False for normal step() calls.
    assert unified_result.stop == legacy_result.stop, (
        f"stop disagreement: unified={unified_result.stop} "
        f"legacy={legacy_result.stop}"
    )
    assert unified_result.stop is False, (
        "step() should not set stop=True under any Phase 1 recipe "
        "(only GEPA's manages_own_evaluation engine opts into stop=True)"
    )

    # Field 2: summary — meaningful comparison via engine-specific regex.
    assert isinstance(unified_result.summary, str) and unified_result.summary.strip()
    assert isinstance(legacy_result.summary, str) and legacy_result.summary.strip()
    legacy_summary_signals = _extract_summary_signals(
        legacy_result.summary,
        _LEGACY_SUMMARY_PATTERNS.get(legacy_engine_name, []),
    )
    unified_summary_signals = _extract_unified_summary_signals(unified_result.summary)
    # For every signal that legacy extracted (e.g., "N new skills"),
    # the matching unified signal must agree.
    if "skills_added" in legacy_summary_signals:
        assert (
            legacy_summary_signals["skills_added"]
            == unified_summary_signals.get("skills_added", -1)
        ), (
            f"summary skills_added disagreement: "
            f"legacy={legacy_summary_signals['skills_added']} "
            f"unified={unified_summary_signals.get('skills_added')}"
            f"\n  legacy summary: {legacy_result.summary!r}"
            f"\n  unified summary: {unified_result.summary!r}"
        )
    if "proposals_applied" in legacy_summary_signals:
        # guided_synth's "applied N" maps to unified's skills_added count
        # via the SkillCurator report.
        assert (
            legacy_summary_signals["proposals_applied"]
            == unified_summary_signals.get("skills_added", -1)
        ), (
            f"summary proposals_applied disagreement: "
            f"legacy={legacy_summary_signals['proposals_applied']} "
            f"unified={unified_summary_signals.get('skills_added')}"
            f"\n  legacy summary: {legacy_result.summary!r}"
            f"\n  unified summary: {unified_result.summary!r}"
        )
    if "total_mutations" in legacy_summary_signals:
        # AdaptiveEvolveEngine's "N auto-fixes" bundles hallucination +
        # auto-seed + sanity counts; it is the closest analog to the
        # unified summary's "N mutations".
        assert (
            legacy_summary_signals["total_mutations"]
            == unified_summary_signals.get("total_mutations", -1)
        ), (
            f"summary total_mutations disagreement: "
            f"legacy={legacy_summary_signals['total_mutations']} "
            f"unified={unified_summary_signals.get('total_mutations')}"
            f"\n  legacy summary: {legacy_result.summary!r}"
            f"\n  unified summary: {unified_result.summary!r}"
        )

    # Field 3: metadata — normalized signals agreement.
    unified_signals = _normalize_unified_metadata(
        unified_result.metadata or {}, unified_workspace
    )
    legacy_signals = _normalize_legacy_metadata(
        legacy_result.metadata or {},
        legacy_engine_name,
        legacy_workspace,
        observations_count,
    )
    assert unified_signals["total_mutations"] == legacy_signals["total_mutations"], (
        f"total_mutations disagreement: unified={unified_signals['total_mutations']} "
        f"legacy={legacy_signals['total_mutations']} "
        f"(unified_reports={unified_result.metadata.get('unified_reports')}, "
        f"legacy_metadata={legacy_result.metadata})"
    )
    assert unified_signals["mutating_ops"] == legacy_signals["mutating_ops"], (
        f"mutating_ops disagreement: unified={unified_signals['mutating_ops']} "
        f"legacy={legacy_signals['mutating_ops']}"
    )
    assert unified_signals["skills_on_disk"] == legacy_signals["skills_on_disk"], (
        f"skills_on_disk disagreement: unified={unified_signals['skills_on_disk']} "
        f"legacy={legacy_signals['skills_on_disk']}"
    )
    assert unified_signals["memory_rows_written"] == legacy_signals["memory_rows_written"], (
        f"memory_rows_written disagreement: "
        f"unified={unified_signals['memory_rows_written']} "
        f"legacy={legacy_signals['memory_rows_written']}"
    )
    assert unified_signals["verdict_accept"] is True
    assert unified_signals["verdict_rollback"] is False
    # Final full-dict assertion — catches any silent drift in the
    # normalizer key set. If either helper starts returning a new key,
    # this fires even when the per-key asserts above don't.
    assert unified_signals == legacy_signals, (
        f"normalized metadata dicts differ in shape or values: "
        f"unified={unified_signals} legacy={legacy_signals}"
    )
    assert set(unified_signals.keys()) == {
        "total_mutations",
        "mutating_ops",
        "skills_on_disk",
        "memory_rows_written",
        "verdict_accept",
        "verdict_rollback",
    }, (
        f"normalized signal contract drift: expected exactly 6 keys, "
        f"got {sorted(unified_signals.keys())}"
    )


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


def test_adaptive_skill_terminal_parity_noop(tmp_path):
    """Terminal-Bench profile NO-OP parity: draft present, LLM declines to write.

    Supplemental guard — the primary LLM-driven positive-mutation parity
    test is ``test_adaptive_skill_terminal_parity_positive`` below.
    """
    cap = FeedbackCapability(
        has_pass_fail=True, solver_may_propose=True, judge_available=True
    )
    observations = _terminal_observations()

    ws_unified = _make_workspace(tmp_path / "u")
    ws_legacy = _make_workspace(tmp_path / "l")
    for ws in (ws_unified, ws_legacy):
        ws.drafts_dir.mkdir(parents=True, exist_ok=True)
        ws.write_draft("d1", "draft body")

    obs_dicts = [_obs_to_record(o) for o in observations]
    hist_u = _HistoryStub(obs_dicts, cycle=0)
    hist_l = _HistoryStub(obs_dicts, cycle=0)

    u_eng = UnifiedEngine(EvolveConfig(), _Bench(cap))
    _install_unified_mocks(u_eng)
    u_result = u_eng.step(ws_unified, observations, hist_u, trial=None)

    l_eng = AdaptiveSkillEngine(EvolveConfig(), llm=_MockLLM())
    l_result = l_eng.step(ws_legacy, observations, hist_l, trial=None)

    _assert_step_result_parity(
        u_result, l_result, "AdaptiveSkillEngine",
        expect_mutation=False,
        unified_workspace=ws_unified, legacy_workspace=ws_legacy,
        observations_count=len(observations),
    )
    assert _skill_names(ws_unified) == _skill_names(ws_legacy) == set()
    assert ws_unified.list_drafts() == ws_legacy.list_drafts() == []


def test_adaptive_skill_terminal_parity_positive(tmp_path):
    """Positive-mutation parity: LLM uses bash to write a skill.

    A Bedrock-compatible mock provider invokes the bash tool during
    ``converse_loop``. Both legacy ``AdaptiveSkillEngine._run_llm`` and
    unified ``LLMBashEvolve`` take the same converse_loop + bash path,
    producing an identical ``llm_written/SKILL.md`` mutation. This
    proves real mutation parity, not just no-op parity.
    """
    cap = FeedbackCapability(
        has_pass_fail=True, solver_may_propose=True, judge_available=True
    )
    observations = _terminal_observations()

    ws_unified = _make_workspace(tmp_path / "u")
    ws_legacy = _make_workspace(tmp_path / "l")
    for ws in (ws_unified, ws_legacy):
        ws.drafts_dir.mkdir(parents=True, exist_ok=True)
        ws.write_draft("d1", "draft body")

    # Shell command to write the same skill file in both workspaces.
    bash_cmd = (
        "mkdir -p skills/llm_written && "
        "printf '%s' "
        "'---\\nname: llm_written\\ndescription: written by mock LLM\\n---\\n\\n"
        "# Body\\nLLM-authored content\\n' "
        "> skills/llm_written/SKILL.md"
    )
    mock_provider_u = _FakeBedrockProvider.build([bash_cmd])
    mock_provider_l = _FakeBedrockProvider.build([bash_cmd])

    obs_dicts = [_obs_to_record(o) for o in observations]
    hist_u = _HistoryStub(obs_dicts, cycle=0)
    hist_l = _HistoryStub(obs_dicts, cycle=0)

    u_eng = UnifiedEngine(EvolveConfig(), _Bench(cap))
    # Inject the provider into the LLMBashEvolve operator's state slot.
    u_eng._operator_state.setdefault("LLMBashEvolve", {})[
        "llm_provider"
    ] = mock_provider_u
    u_result = u_eng.step(ws_unified, observations, hist_u, trial=None)

    l_eng = AdaptiveSkillEngine(EvolveConfig(), llm=mock_provider_l)
    l_result = l_eng.step(ws_legacy, observations, hist_l, trial=None)

    _assert_step_result_parity(
        u_result, l_result, "AdaptiveSkillEngine",
        expect_mutation=True,
        unified_workspace=ws_unified, legacy_workspace=ws_legacy,
        observations_count=len(observations),
    )
    # Both workspaces now contain the LLM-authored skill, byte-equal.
    assert _skill_names(ws_unified) == _skill_names(ws_legacy) == {"llm_written"}
    u_body = (ws_unified.skills_dir / "llm_written" / "SKILL.md").read_text()
    l_body = (ws_legacy.skills_dir / "llm_written" / "SKILL.md").read_text()
    assert u_body == l_body
    assert "LLM-authored content" in u_body


def test_skillforge_parity_noop(tmp_path):
    """SkillBench NO-OP parity: supplemental guard.

    The positive-mutation case is covered by
    ``test_skillforge_parity_positive`` below.
    """
    cap = FeedbackCapability(
        has_pass_fail=True, has_partial_score=True, judge_available=True
    )
    observations = _skillbench_observations()

    ws_unified = _make_workspace(tmp_path / "u")
    ws_legacy = _make_workspace(tmp_path / "l")

    obs_dicts = [_obs_to_record(o) for o in observations]
    hist_u = _HistoryStub(obs_dicts, cycle=0)
    hist_l = _HistoryStub(obs_dicts, cycle=0)

    u_eng = UnifiedEngine(EvolveConfig(), _Bench(cap))
    _install_unified_mocks(u_eng)
    u_result = u_eng.step(ws_unified, observations, hist_u, trial=None)

    l_eng = AEvolveEngine(EvolveConfig(), llm=_MockLLM())
    l_result = l_eng.step(ws_legacy, observations, hist_l, trial=None)

    _assert_step_result_parity(
        u_result, l_result, "AEvolveEngine",
        expect_mutation=False,
        unified_workspace=ws_unified, legacy_workspace=ws_legacy,
        observations_count=len(observations),
    )
    assert _skill_names(ws_unified) == _skill_names(ws_legacy) == set()


def test_skillforge_parity_positive(tmp_path):
    """SkillBench positive-mutation parity: LLM bash-writes a skill.

    Equivalent to the adaptive_skill positive-path test, but against the
    ``AEvolveEngine`` (skillforge) class. Both engines are near-duplicates,
    so they should behave identically under the same injected provider.
    """
    cap = FeedbackCapability(
        has_pass_fail=True, has_partial_score=True, judge_available=True
    )
    observations = _skillbench_observations()

    ws_unified = _make_workspace(tmp_path / "u")
    ws_legacy = _make_workspace(tmp_path / "l")

    bash_cmd = (
        "mkdir -p skills/sb_skill && "
        "printf '%s' "
        "'---\\nname: sb_skill\\ndescription: bench body\\n---\\n\\ncontent\\n' "
        "> skills/sb_skill/SKILL.md"
    )
    mock_provider_u = _FakeBedrockProvider.build([bash_cmd])
    mock_provider_l = _FakeBedrockProvider.build([bash_cmd])

    obs_dicts = [_obs_to_record(o) for o in observations]
    hist_u = _HistoryStub(obs_dicts, cycle=0)
    hist_l = _HistoryStub(obs_dicts, cycle=0)

    u_eng = UnifiedEngine(EvolveConfig(), _Bench(cap))
    u_eng._operator_state.setdefault("LLMBashEvolve", {})[
        "llm_provider"
    ] = mock_provider_u
    u_result = u_eng.step(ws_unified, observations, hist_u, trial=None)

    l_eng = AEvolveEngine(EvolveConfig(), llm=mock_provider_l)
    l_result = l_eng.step(ws_legacy, observations, hist_l, trial=None)

    _assert_step_result_parity(
        u_result, l_result, "AEvolveEngine",
        expect_mutation=True,
        unified_workspace=ws_unified, legacy_workspace=ws_legacy,
        observations_count=len(observations),
    )
    assert _skill_names(ws_unified) == _skill_names(ws_legacy) == {"sb_skill"}
    u_body = (ws_unified.skills_dir / "sb_skill" / "SKILL.md").read_text()
    l_body = (ws_legacy.skills_dir / "sb_skill" / "SKILL.md").read_text()
    assert u_body == l_body


def test_guided_synth_swe_parity_noop(tmp_path):
    """SWE profile NO-OP parity (curator SKIPs the proposal): supplemental guard.

    Even under NO_PROPOSALS, ``WriteEpisodicMemory`` still fires on both
    engines — so ``mutated`` is True on both sides and we assert episodic
    memory row parity. The positive curator ACCEPT case is covered by
    ``test_guided_synth_swe_parity_positive``.
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

    obs_dicts = [_obs_to_record(o) for o in observations]
    hist_u = _HistoryStub(obs_dicts, cycle=0)
    hist_l = _HistoryStub(obs_dicts, cycle=0)

    u_eng = UnifiedEngine(EvolveConfig(), _Bench(cap))
    _install_unified_mocks(u_eng)
    u_result = u_eng.step(ws_unified, observations, hist_u, trial=None)

    l_eng = GuidedSynthesisEngine(
        EvolveConfig(), llm=_MockLLM(content="NO_PROPOSALS")
    )
    l_result = l_eng.step(ws_legacy, observations, hist_l, trial=None)

    # WriteEpisodicMemory fires on both sides.
    u_mem = (ws_unified.memory_dir / "episodic.jsonl").read_text() if (ws_unified.memory_dir / "episodic.jsonl").exists() else ""
    l_mem = (ws_legacy.memory_dir / "episodic.jsonl").read_text() if (ws_legacy.memory_dir / "episodic.jsonl").exists() else ""
    assert u_mem.count("\n") == l_mem.count("\n") == 1

    u_entry = json.loads(u_mem.strip())
    l_entry = json.loads(l_mem.strip())
    assert u_entry["task_id"] == l_entry["task_id"] == "t1"
    assert u_entry["cycle"] == l_entry["cycle"] == 1
    assert u_entry["files_edited"] == l_entry["files_edited"] == ["a.py"]
    assert abs(float(u_entry["score"]) - float(l_entry["score"])) < 1e-6

    # With NO_PROPOSALS neither curator writes a skill — but memory counts.
    assert _skill_names(ws_unified) == _skill_names(ws_legacy) == set()
    # Both engines have mutated=False here: legacy guided_synth defines
    # mutated purely via curated-skill diff; unified's WriteEpisodicMemory
    # reports count=0 by design (see operator docstring) so its memory
    # appends do not flip mutated. Memory rows are still verified above.
    _assert_step_result_parity(
        u_result, l_result, "GuidedSynthesisEngine",
        expect_mutation=False,
        unified_workspace=ws_unified, legacy_workspace=ws_legacy,
        observations_count=len(observations),
    )


def test_guided_synth_swe_parity_positive(tmp_path):
    """SWE POSITIVE parity: curator ACCEPTs the proposal and writes the skill.

    Both legacy ``_curate_proposals`` + ``_execute_curation`` and unified
    ``SkillCurator`` receive the same "ACCEPT: verify_before_after"
    decision from the mock LLM, and both write an identical SKILL.md.
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

    obs_dicts = [_obs_to_record(o) for o in observations]
    hist_u = _HistoryStub(obs_dicts, cycle=0)
    hist_l = _HistoryStub(obs_dicts, cycle=0)

    # Both engines get the same LLM that returns an ACCEPT.
    accept_content = "ACCEPT: verify_before_after\n"

    u_eng = UnifiedEngine(EvolveConfig(), _Bench(cap))
    # Keep the LLMBashEvolve NO_PROPOSALS mock for recipes that use it.
    _install_unified_mocks(u_eng)
    # Override the SkillCurator slot with a real provider hook that accepts.
    u_eng._operator_state.setdefault("SkillCurator", {})[
        "mock_curator"
    ] = lambda _: accept_content
    u_result = u_eng.step(ws_unified, observations, hist_u, trial=None)

    l_eng = GuidedSynthesisEngine(EvolveConfig(), llm=_MockLLM(content=accept_content))
    l_result = l_eng.step(ws_legacy, observations, hist_l, trial=None)

    # Both sides now have the curated skill.
    assert _skill_names(ws_unified) == _skill_names(ws_legacy) == {"verify_before_after"}
    u_body = (ws_unified.skills_dir / "verify_before_after" / "SKILL.md").read_text()
    l_body = (ws_legacy.skills_dir / "verify_before_after" / "SKILL.md").read_text()
    assert u_body == l_body, "curator-applied skill body diverged"

    # Both engines wrote exactly one episodic memory row + one skill.
    _assert_step_result_parity(
        u_result, l_result, "GuidedSynthesisEngine",
        expect_mutation=True,
        unified_workspace=ws_unified, legacy_workspace=ws_legacy,
        observations_count=len(observations),
    )


def test_adaptive_evolve_mcp_parity(tmp_path):
    """MCP-Atlas positive-path parity: AutoSeedSkills fires a byte-equal skill.

    Although the LLM call in both engines is a no-op under NO_PROPOSALS,
    the DETERMINISTIC phases produce real mutations:
    - FixHallucinations may write ``tool-name-corrections`` when the
      hallucination map is non-empty. Our fixture has no hallucinations,
      so this phase is quiet.
    - AutoSeedSkills writes ``multi-requirement-handler`` because the
      observations trigger the ``multi_requirement_miss`` pattern
      (4 obs with score=0.5 and "and" in task_input).
    - SanityCheck may run but typically does not remove anything on a
      fresh workspace.

    The assertion is that BOTH engines write ``multi-requirement-handler``
    with byte-equal content, and the normalized metadata agrees on the
    count of mutations.
    """
    cap = FeedbackCapability(
        has_pass_fail=True, has_per_claim=True, judge_available=True
    )
    observations = _mcp_observations()

    ws_unified = _make_workspace(tmp_path / "u")
    ws_legacy = _make_workspace(tmp_path / "l")

    obs_dicts = [_obs_to_record(o) for o in observations]
    hist_u = _HistoryStub(obs_dicts, cycle=0)
    hist_l = _HistoryStub(obs_dicts, cycle=0)

    u_eng = UnifiedEngine(EvolveConfig(), _Bench(cap))
    _install_unified_mocks(u_eng)
    u_result = u_eng.step(ws_unified, observations, hist_u, trial=None)

    l_eng = AdaptiveEvolveEngine(EvolveConfig(), llm=_MockLLM())
    l_result = l_eng.step(ws_legacy, observations, hist_l, trial=None)

    assert "legacy_engine" not in (u_result.metadata or {})

    unified_skills = _skill_names(ws_unified)
    legacy_skills = _skill_names(ws_legacy)
    assert "multi-requirement-handler" in unified_skills
    assert "multi-requirement-handler" in legacy_skills

    unified_body = (ws_unified.skills_dir / "multi-requirement-handler" / "SKILL.md").read_text()
    legacy_body = (ws_legacy.skills_dir / "multi-requirement-handler" / "SKILL.md").read_text()
    assert unified_body == legacy_body, (
        "Seed skill body diverged between unified and legacy — DEC-2 violated"
    )

    _assert_step_result_parity(
        u_result, l_result, "AdaptiveEvolveEngine",
        expect_mutation=True,
        unified_workspace=ws_unified, legacy_workspace=ws_legacy,
        observations_count=len(observations),
    )


def test_adaptive_evolve_mcp_parity_auto_seed_multiple(tmp_path):
    """Positive parity on a wider mutation surface.

    Force ``AutoSeedSkills`` to seed TWO skills — ``multi-requirement-handler``
    (because of the pattern) and ``calculate-handler`` (because
    ``ClaimTypeAnalyzer`` sees a weakest claim type of ``calculate``).
    Both unified and legacy must write both skills with byte-equal content.
    """
    cap = FeedbackCapability(
        has_pass_fail=True, has_per_claim=True, judge_available=True
    )
    # Observations crafted so "calculate" is the weakest claim type (pass_rate=0).
    observations = _mcp_observations()

    ws_unified = _make_workspace(tmp_path / "u")
    ws_legacy = _make_workspace(tmp_path / "l")

    obs_dicts = [_obs_to_record(o) for o in observations]
    hist_u = _HistoryStub(obs_dicts, cycle=0)
    hist_l = _HistoryStub(obs_dicts, cycle=0)

    u_eng = UnifiedEngine(EvolveConfig(), _Bench(cap))
    _install_unified_mocks(u_eng)
    u_eng.step(ws_unified, observations, hist_u, trial=None)

    l_eng = AdaptiveEvolveEngine(EvolveConfig(), llm=_MockLLM())
    l_eng.step(ws_legacy, observations, hist_l, trial=None)

    # Both should have seeded the calculate-handler skill.
    assert "calculate-handler" in _skill_names(ws_unified), (
        f"unified skills: {_skill_names(ws_unified)}"
    )
    assert "calculate-handler" in _skill_names(ws_legacy), (
        f"legacy skills: {_skill_names(ws_legacy)}"
    )
    u_body = (ws_unified.skills_dir / "calculate-handler" / "SKILL.md").read_text()
    l_body = (ws_legacy.skills_dir / "calculate-handler" / "SKILL.md").read_text()
    assert u_body == l_body, (
        "claim-type-handler skill body diverged between unified and legacy"
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
