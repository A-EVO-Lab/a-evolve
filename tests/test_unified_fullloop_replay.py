"""Full-loop replay differential test (plan_v1.md AC-8 positive test).

AC-8 positive test spec (plan_v1.md:109):

    "Full-loop replay: ``EvolutionLoop(..., engine=UnifiedEngine(config,
    benchmark), ...).run(cycles=1)`` produces the same ``history.jsonl``,
    same ``batch_XXXX.jsonl`` content (modulo ``unified_*`` fields),
    same git tags as the legacy run"

This test runs the REAL ``EvolutionLoop.run(cycles=1)`` twice — once with
``UnifiedEngine`` and once with a legacy engine class (``AEvolveEngine``,
chosen because its recipe is the simplest: ``[PassFailReader,
TrajectoryCompressor] + [LLMBashEvolve]``). Both runs share a mock Agent
+ mock Benchmark so no LLM / network / heavy deps are touched.

Assertions:
1. ``history.jsonl`` exists in both evolution dirs and has the same
   (``cycle``, ``mutated``, ``score``) tuple across the two runs
2. ``observations/batch_0001.jsonl`` exists and is byte-equal (both sides
   — Observer.collect() is engine-independent, so this is the strongest
   form of "modulo ``unified_*`` fields" parity)
3. ``pre-evo-1`` and ``evo-1`` git tags are created in both repos
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# Legacy engine imports — allowed only inside the tests tree; the static
# import-ban audit (tests/test_unified_import_ban.py) exempts tests.
from agent_evolve.algorithms.skillforge.engine import AEvolveEngine
from agent_evolve.algorithms.unified import FeedbackCapability, UnifiedEngine
from agent_evolve.benchmarks.base import BenchmarkAdapter
from agent_evolve.config import EvolveConfig
from agent_evolve.engine.loop import EvolutionLoop
from agent_evolve.protocol.base_agent import BaseAgent
from agent_evolve.types import Feedback, Task, Trajectory


# ── Mocks ────────────────────────────────────────────────────


class _MockLLM:
    """Returns NO_PROPOSALS so no LLM-driven mutation fires.

    ``isinstance(self, BedrockProvider)`` is False, so legacy ``_run_llm``
    routes through ``self.llm.complete(messages, max_tokens)`` — avoiding
    any boto3/converse_loop dependency.
    """

    def complete(self, messages, max_tokens=None, temperature=None):
        resp = MagicMock()
        resp.content = "NO_PROPOSALS"
        resp.usage = {"input_tokens": 0, "output_tokens": 0}
        return resp


class _DeterministicAgent(BaseAgent):
    """Minimal BaseAgent that returns a fixed trajectory per task.

    Both loop runs must get byte-identical trajectories so Observer.collect()
    produces byte-identical batch JSONL. The trajectory content is trivial.
    """

    def solve(self, task: Task) -> Trajectory:
        return Trajectory(
            task_id=task.id,
            output="widget delivered",
            steps=[],
            conversation=[],
        )


class _MockBench(BenchmarkAdapter):
    """SkillBench-like profile: has_pass_fail + has_partial_score."""

    def get_tasks(self, split: str = "train", limit: int = 10) -> list[Task]:
        return [Task(id="t1", input="make a widget", metadata={})]

    def evaluate(self, task: Task, trajectory: Trajectory) -> Feedback:
        return Feedback(
            success=True, score=0.919, detail="34/37", raw={}
        )

    @property
    def feedback_capability(self) -> FeedbackCapability:
        return FeedbackCapability(
            has_pass_fail=True, has_partial_score=True, judge_available=True
        )


# ── Helpers ──────────────────────────────────────────────────


def _seed_workspace(root: Path) -> None:
    """Create a minimal workspace so BaseAgent + VersionControl can boot."""
    root.mkdir(parents=True, exist_ok=True)
    prompts = root / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    (prompts / "system.md").write_text("# Agent\n\n## Section\nhi\n")


def _git_tags(repo_root: Path) -> list[str]:
    import subprocess

    out = subprocess.run(
        ["git", "-C", str(repo_root), "tag"],
        check=True, capture_output=True, text=True,
    )
    return sorted(line.strip() for line in out.stdout.splitlines() if line.strip())


def _read_history(evolution_dir: Path) -> list[dict[str, Any]]:
    path = evolution_dir / "history.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text().splitlines() if line.strip()
    ]


def _read_batch(evolution_dir: Path, batch_id: int = 1) -> list[dict[str, Any]]:
    path = evolution_dir / "observations" / f"batch_{batch_id:04d}.jsonl"
    return [
        json.loads(line) for line in path.read_text().splitlines() if line.strip()
    ]


def _strip_volatile(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove fields that legitimately drift across runs (timestamps, etc.)."""
    cleaned: list[dict[str, Any]] = []
    for e in entries:
        c = {k: v for k, v in e.items() if k != "timestamp"}
        cleaned.append(c)
    return cleaned


# ── Test ─────────────────────────────────────────────────────


def test_full_loop_replay_parity(tmp_path):
    """AC-8 full-loop: EvolutionLoop.run(cycles=1) produces parity outputs.

    Strategy:
    - Build two independent workspaces (one for unified, one for legacy)
    - Seed both with the identical initial state (same prompt, empty skills)
    - Run EvolutionLoop with UnifiedEngine in workspace A
    - Run EvolutionLoop with AEvolveEngine in workspace B
    - Both engines receive the same mock LLM, so both take the no-op
      (NO_PROPOSALS) branch and neither mutates the workspace

    Parity claims:
    - batch_0001.jsonl byte-equal (Observer.collect() is engine-agnostic)
    - history.jsonl entries agree on (cycle, score, mutated)
    - both repos have the same git-tag set {evo-0, pre-evo-1, evo-1}
    """
    # ── Workspace A: UnifiedEngine ─────────────────────────────
    ws_a = tmp_path / "ws_unified"
    _seed_workspace(ws_a)
    agent_a = _DeterministicAgent(ws_a)
    bench_a = _MockBench()
    unified = UnifiedEngine(EvolveConfig(batch_size=1, max_cycles=1), bench_a)
    # Inject mock LLM into the LLMBashEvolve operator's state slot so
    # the no-op branch runs deterministically.
    unified._operator_state.setdefault("LLMBashEvolve", {})[
        "mock"
    ] = lambda prompt: "NO_PROPOSALS"

    loop_a = EvolutionLoop(
        agent=agent_a,
        benchmark=bench_a,
        engine=unified,
        config=EvolveConfig(batch_size=1, max_cycles=1),
    )
    result_a = loop_a.run(cycles=1)

    # ── Workspace B: legacy AEvolveEngine ──────────────────────
    ws_b = tmp_path / "ws_legacy"
    _seed_workspace(ws_b)
    agent_b = _DeterministicAgent(ws_b)
    bench_b = _MockBench()
    legacy = AEvolveEngine(EvolveConfig(batch_size=1, max_cycles=1), llm=_MockLLM())
    loop_b = EvolutionLoop(
        agent=agent_b,
        benchmark=bench_b,
        engine=legacy,
        config=EvolveConfig(batch_size=1, max_cycles=1),
    )
    result_b = loop_b.run(cycles=1)

    # ── Assertions ──────────────────────────────────────────────
    # 1. Loop result agreement.
    assert result_a.cycles_completed == result_b.cycles_completed == 1
    assert abs(result_a.final_score - result_b.final_score) < 1e-6

    # 2. batch_0001.jsonl byte-equal (modulo timestamps).
    evo_a = ws_a / "evolution"
    evo_b = ws_b / "evolution"
    batch_a = _strip_volatile(_read_batch(evo_a))
    batch_b = _strip_volatile(_read_batch(evo_b))
    assert batch_a == batch_b, (
        f"batch_0001.jsonl diverged between unified and legacy runs: "
        f"unified={batch_a!r} legacy={batch_b!r}"
    )
    # Exactly one observation was recorded in each.
    assert len(batch_a) == 1

    # 3. history.jsonl parity on the (cycle, score, mutated) triple.
    hist_a = _strip_volatile(_read_history(evo_a))
    hist_b = _strip_volatile(_read_history(evo_b))
    assert len(hist_a) == len(hist_b) == 1
    for field in ("cycle", "score", "mutated"):
        assert hist_a[0][field] == hist_b[0][field], (
            f"history.jsonl {field} diverged: "
            f"unified={hist_a[0][field]} legacy={hist_b[0][field]}"
        )

    # 4. Git tag set parity. Both runs create the same set of tags:
    #    {evo-0 (initial), pre-evo-1, evo-1}. The tag NAMES must match.
    tags_a = _git_tags(ws_a)
    tags_b = _git_tags(ws_b)
    assert tags_a == tags_b, (
        f"git tag sets diverged: unified={tags_a} legacy={tags_b}"
    )
    assert {"evo-0", "pre-evo-1", "evo-1"}.issubset(set(tags_a))


def test_full_loop_replay_metadata_excludes_unified_fields(tmp_path):
    """AC-8 "modulo unified_* fields" — verify the exclusion rule is well-defined.

    The unified engine's StepResult.metadata includes ``unified_regime``,
    ``unified_plan``, ``unified_reports``, ``unified_verdict``. A downstream
    consumer wanting "legacy-comparable" metadata is directed by AC-8 to
    strip any ``unified_*``-prefixed key. This test proves:
    - The CycleRecord.metadata on the unified run contains ONLY
      ``unified_*``-prefixed keys (so the exclusion is total)
    - When those keys are stripped, the remaining metadata dict is ``{}``
      which is consistent with Observer.collect()'s engine-independent
      batch JSONL
    """
    ws = tmp_path / "ws"
    _seed_workspace(ws)
    agent = _DeterministicAgent(ws)
    bench = _MockBench()
    unified = UnifiedEngine(EvolveConfig(batch_size=1, max_cycles=1), bench)
    unified._operator_state.setdefault("LLMBashEvolve", {})[
        "mock"
    ] = lambda prompt: "NO_PROPOSALS"

    loop = EvolutionLoop(
        agent=agent,
        benchmark=bench,
        engine=unified,
        config=EvolveConfig(batch_size=1, max_cycles=1),
    )
    loop.run(cycles=1)

    # The engine sidecar records the full StepResult.metadata.
    sidecar = ws / "evolution" / "unified_steps.jsonl"
    records = [
        json.loads(line) for line in sidecar.read_text().splitlines() if line.strip()
    ]
    assert len(records) == 1
    md_keys = set(records[0].keys()) - {"timestamp", "mutated"}
    # All remaining keys are unified_* — the exclusion rule is total.
    assert all(k.startswith("unified_") for k in md_keys), (
        f"sidecar contains non-unified_* keys: {sorted(md_keys)}"
    )
    stripped = {k: v for k, v in records[0].items() if not k.startswith("unified_")}
    # After stripping unified_*, only timestamp+mutated remain (neither is
    # part of StepResult.metadata — those are sidecar-level fields).
    assert set(stripped.keys()) == {"timestamp", "mutated"}
