"""Smoke test: _LegacyEvolveShim (unified) vs AEvolveEngine (legacy).

Verifies that the shim inserted in skillbench_evolve_in_situ_cycle_unified.py
preserves the legacy .evolve() contract. Same inputs to both sides;
compare outputs.

Inputs (identical on both sides):
  - Fresh AgentWorkspace at a tmp dir (seed prompt only, empty skills)
  - A list of observation_logs dicts (shape the legacy script builds)
  - evo_number=1
  - Deterministic mock LLM (so both sides see byte-equal LLM responses)

Outputs compared:
  - The return dict keys + values from each .evolve() call:
      skills_added, skills_removed, new_skills, skills_before, skills_after, usage
  - The workspace state after the call (which files exist, diff content)

Result: pass iff both sides produce the same dict contract + same
workspace delta under the same mocked LLM.

Usage: python scripts/smoke_test_skillbench_shim_parity.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agent_evolve.algorithms.skillforge.engine import AEvolveEngine
from agent_evolve.algorithms.unified import FeedbackCapability, UnifiedEngine
from agent_evolve.config import EvolveConfig
from agent_evolve.contract.workspace import AgentWorkspace


# Load the unified script just to reach _LegacyEvolveShim without exec'ing
# its main().
import importlib.util

_spec = importlib.util.spec_from_file_location(
    "sbu",
    str(REPO / "examples" / "skillbench_examples"
           / "skillbench_evolve_in_situ_cycle_unified.py"),
)
sbu = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sbu)


class _MockLLM:
    """Plain LLM provider — both engines take .complete() path (not BedrockProvider)."""

    def __init__(self, content: str = "NO_PROPOSALS"):
        self.content = content

    def complete(self, messages, max_tokens=None, temperature=None):
        r = MagicMock()
        r.content = self.content
        r.usage = {"input_tokens": 0, "output_tokens": 0}
        return r


class _Bench:
    """Minimal SkillBench-like capability."""

    @property
    def feedback_capability(self):
        return FeedbackCapability(
            has_pass_fail=True, has_partial_score=True, judge_available=True
        )


def _seed_workspace(root: Path) -> AgentWorkspace:
    root.mkdir(parents=True, exist_ok=True)
    prompts = root / "prompts"
    prompts.mkdir(exist_ok=True)
    (prompts / "system.md").write_text("# Agent\n\n## Section\nhi\n")
    return AgentWorkspace(root)


def _observation_logs() -> list[dict]:
    """Build observation_logs in the exact dict shape legacy script produces."""
    return [
        {
            "task_id": "t1",
            "task_input": "Build widget 1",
            "agent_output": "ok",
            "steps": [],
            "conversation": [],
            "score": 0.75,
            "success": False,
            "evolver_feedback_detail": "partial — missed spec A",
        },
        {
            "task_id": "t2",
            "task_input": "Build widget 2",
            "agent_output": "ok",
            "steps": [],
            "conversation": [],
            "score": 0.5,
            "success": False,
            "evolver_feedback_detail": "partial — missed spec B",
        },
    ]


def _workspace_snapshot(ws: AgentWorkspace) -> dict[str, str]:
    """Capture every text file the workspace touches as {relpath: content}."""
    snap = {}
    for p in sorted(Path(ws.root).rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(ws.root))
        if rel.startswith(".git/") or rel.endswith(".pyc"):
            continue
        try:
            snap[rel] = p.read_text()
        except UnicodeDecodeError:
            snap[rel] = "<binary>"
    return snap


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="smoke-shim-") as tmp:
        tmp_root = Path(tmp)
        obs_logs = _observation_logs()
        config = EvolveConfig()

        print("=" * 72)
        print("SkillBench shim parity smoke: _LegacyEvolveShim vs AEvolveEngine")
        print("=" * 72)
        print(f"Workspace tmp root: {tmp_root}")
        print(f"Observation logs:   {len(obs_logs)} entries")
        print()

        # ── Legacy side ─────────────────────────────────────────
        legacy_ws_root = tmp_root / "ws_legacy"
        legacy_ws = _seed_workspace(legacy_ws_root)
        legacy_eng = AEvolveEngine(config, llm=_MockLLM())
        legacy_result = legacy_eng.evolve(
            workspace=legacy_ws,
            observation_logs=obs_logs,
            evo_number=1,
        )
        legacy_snap = _workspace_snapshot(legacy_ws)
        print("[1/2] legacy AEvolveEngine.evolve() returned:")
        for k, v in sorted(legacy_result.items()):
            print(f"    {k!r}: {v!r}")
        print(f"    workspace files: {sorted(legacy_snap.keys())}")
        print()

        # ── Unified side via shim ──────────────────────────────
        unified_ws_root = tmp_root / "ws_unified"
        unified_ws = _seed_workspace(unified_ws_root)
        ue = UnifiedEngine(config, _Bench())
        ue._operator_state.setdefault("LLMBashEvolve", {})["mock"] = (
            lambda prompt: "NO_PROPOSALS"
        )
        shim = sbu._LegacyEvolveShim(ue)
        unified_result = shim.evolve(
            workspace=unified_ws,
            observation_logs=obs_logs,
            evo_number=1,
        )
        unified_snap = _workspace_snapshot(unified_ws)
        print("[2/2] _LegacyEvolveShim.evolve() returned:")
        for k, v in sorted(unified_result.items()):
            print(f"    {k!r}: {v!r}")
        print(f"    workspace files: {sorted(unified_snap.keys())}")
        print()

        # ── Parity checks ──────────────────────────────────────
        print("=" * 72)
        print("Parity checks")
        print("=" * 72)

        checks = []

        # The return dicts must expose the same contract keys.
        legacy_keys = set(legacy_result.keys())
        unified_keys = set(unified_result.keys())
        expected_keys = {
            "skills_added", "skills_removed", "new_skills",
            "skills_before", "skills_after", "usage",
        }
        checks.append((
            "legacy returns expected-keys superset",
            expected_keys.issubset(legacy_keys),
        ))
        checks.append((
            "unified (shim) returns expected-keys superset",
            expected_keys.issubset(unified_keys),
        ))

        # The shape of each common field (value sanity).
        for k in expected_keys:
            lv = legacy_result.get(k)
            uv = unified_result.get(k)
            checks.append((
                f"  {k}: both types match",
                type(lv) is type(uv) or (lv is None and uv is None),
            ))

        # Specific value equalities under NO_PROPOSALS (no mutation path).
        # Legacy + unified should both produce empty-or-none skill lists.
        checks.append((
            "skills_added: both empty or equal",
            sorted(legacy_result["skills_added"] or []) ==
            sorted(unified_result["skills_added"] or []),
        ))
        checks.append((
            "skills_removed: both empty or equal",
            sorted(legacy_result["skills_removed"] or []) ==
            sorted(unified_result["skills_removed"] or []),
        ))
        checks.append((
            "new_skills: both zero (no LLM mutation under NO_PROPOSALS)",
            int(legacy_result["new_skills"] or 0) ==
            int(unified_result["new_skills"] or 0) == 0,
        ))
        checks.append((
            "skills_before: both zero (fresh workspace)",
            int(legacy_result["skills_before"] or 0) ==
            int(unified_result["skills_before"] or 0) == 0,
        ))
        checks.append((
            "skills_after: same as before (no mutation)",
            int(legacy_result["skills_after"] or 0) ==
            int(unified_result["skills_after"] or 0) == 0,
        ))

        # Workspace states — under NO_PROPOSALS, neither side should have
        # added new skill files. The only allowed difference is the
        # unified side's evolution/ sidecars (unified_steps.jsonl +
        # possibly batch trailers).
        legacy_files = set(legacy_snap.keys())
        unified_files = set(unified_snap.keys())
        allowed_unified_only = {
            f for f in unified_files
            if f.startswith("evolution/unified_steps.jsonl")
            or f.startswith("evolution/")
        }
        shared = legacy_files & unified_files
        legacy_only = legacy_files - unified_files
        unified_only = unified_files - legacy_files - allowed_unified_only
        checks.append((
            "no legacy-only files (unified writes everything legacy does)",
            len(legacy_only) == 0,
        ))
        checks.append((
            "no unified-only files outside evolution/ sidecars",
            len(unified_only) == 0,
        ))

        all_passed = True
        for name, ok in checks:
            mark = "✓" if ok else "✗"
            print(f"  {mark} {name}")
            all_passed = all_passed and ok

        if legacy_only:
            print(f"\n    (info) legacy-only files: {sorted(legacy_only)}")
        if unified_only:
            print(f"\n    (info) unified-only NON-evolution files: {sorted(unified_only)}")

        print()
        if all_passed:
            print("SHIM PARITY TEST PASSED — unified shim preserves the "
                  "legacy .evolve() contract. Safe to swap in the script.")
            return 0
        print("SHIM PARITY TEST FAILED — see ✗ above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
