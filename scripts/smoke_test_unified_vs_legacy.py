"""Smoke test: real Bedrock in the loop, UnifiedEngine vs AEvolveEngine over 3 cycles.

Runs two independent EvolutionLoop.run(cycles=3) instances:
  - Workspace A: UnifiedEngine(config, bench)
  - Workspace B: AEvolveEngine(config, llm=bedrock)

Both get:
  - The same real Bedrock LLM (Claude Haiku 4.5 via us-west-2)
  - The same deterministic agent that returns a fixed trajectory per task
  - The same 2-task mock benchmark with partial-score feedback
  - Fresh tmp workspaces seeded with a minimal prompt

After 3 cycles, compare:
  - Per-cycle skills written (list skill names per cycle)
  - history.jsonl (cycle/score/mutated tuples)
  - batch_*.jsonl byte-equal (modulo timestamps and unified_* trailer)

This extends the single-cycle hermetic replay test (test_unified_fullloop_replay.py)
to multi-cycle with a real LLM — the first evidence that unified matches legacy
under realistic LLM variability, not just deterministic mocks.

Usage:
    HUMANIZE_CODEX_BYPASS_SANDBOX=true python scripts/smoke_test_unified_vs_legacy.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

# Make repo importable when run directly.
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agent_evolve.algorithms.skillforge.engine import AEvolveEngine
from agent_evolve.algorithms.unified import FeedbackCapability, UnifiedEngine
from agent_evolve.benchmarks.base import BenchmarkAdapter
from agent_evolve.config import EvolveConfig
from agent_evolve.engine.loop import EvolutionLoop
from agent_evolve.llm.bedrock import BedrockProvider
from agent_evolve.protocol.base_agent import BaseAgent
from agent_evolve.types import Feedback, Task, Trajectory


MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
REGION = "us-west-2"


class _DeterministicAgent(BaseAgent):
    """Agent returns a trivial trajectory per task — no LLM on the agent side.

    The real LLM calls happen inside the engine's operators (LLMBashEvolve
    for unified; adaptive_skill._run_llm for legacy). That's the code path
    we actually want to exercise with a real provider.
    """

    def solve(self, task: Task) -> Trajectory:
        return Trajectory(
            task_id=task.id,
            output=f"Response for {task.id}",
            steps=[],
            conversation=[],
        )


class _TwoTaskBench(BenchmarkAdapter):
    """Minimal 2-task bench with partial-score feedback.

    Feedback is identical across both engines so the LLM sees the same
    input on both sides. The benchmark is deterministic."""

    TASKS = [
        Task(id="t1", input="Write a skill that explains prime numbers.", metadata={}),
        Task(id="t2", input="Write a skill that sorts a list.", metadata={}),
    ]

    def get_tasks(self, split: str = "train", limit: int = 10) -> list[Task]:
        return list(self.TASKS[:limit])

    def evaluate(self, task: Task, trajectory: Trajectory) -> Feedback:
        # Deterministic feedback — unified and legacy see identical inputs.
        return Feedback(success=True, score=0.75, detail="partial-synthetic", raw={})

    @property
    def feedback_capability(self) -> FeedbackCapability:
        return FeedbackCapability(
            has_pass_fail=True, has_partial_score=True, judge_available=True
        )


def _seed_workspace(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    prompts = root / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    (prompts / "system.md").write_text(
        "# Agent\n\nYou are an evolver that writes small helpful skills."
    )


def _list_skills(root: Path) -> list[str]:
    skills_dir = root / "skills"
    if not skills_dir.exists():
        return []
    return sorted(d.name for d in skills_dir.iterdir() if d.is_dir())


def _read_history(root: Path) -> list[dict]:
    path = root / "evolution" / "history.jsonl"
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def _read_batches(root: Path) -> list[list[dict]]:
    batch_dir = root / "evolution" / "observations"
    if not batch_dir.exists():
        return []
    out = []
    for bf in sorted(batch_dir.glob("batch_[0-9]*.jsonl")):
        records = [json.loads(l) for l in bf.read_text().splitlines() if l.strip()]
        # Strip step_metadata trailers (AC-7) and timestamps.
        pure = []
        for r in records:
            if r.get("_record_type") == "step_metadata":
                continue
            r.pop("timestamp", None)
            pure.append(r)
        out.append(pure)
    return out


def run_one(engine_name: str, root: Path, llm: BedrockProvider, cycles: int) -> dict:
    _seed_workspace(root)
    agent = _DeterministicAgent(root)
    bench = _TwoTaskBench()
    config = EvolveConfig(batch_size=2, max_cycles=cycles)

    if engine_name == "unified":
        engine = UnifiedEngine(config, bench)
        # Inject Bedrock into the LLMBashEvolve operator's state slot so the
        # recipe uses it instead of a mock.
        engine._operator_state.setdefault("LLMBashEvolve", {})["llm_provider"] = llm
    elif engine_name == "legacy":
        engine = AEvolveEngine(config, llm=llm)
    else:
        raise ValueError(engine_name)

    loop = EvolutionLoop(agent=agent, benchmark=bench, engine=engine, config=config)
    result = loop.run(cycles=cycles)

    return {
        "cycles_completed": result.cycles_completed,
        "final_score": round(result.final_score, 4),
        "score_history": [round(s, 4) for s in result.score_history],
        "skills_after": _list_skills(root),
        "history": _read_history(root),
        "batches": _read_batches(root),
    }


def main(cycles: int = 3) -> int:
    llm = BedrockProvider(model_id=MODEL_ID, region=REGION)

    with tempfile.TemporaryDirectory(prefix="smoke-unified-") as tmp:
        tmp_root = Path(tmp)
        print(f"Smoke test workspace: {tmp_root}")
        print(f"Model: {MODEL_ID}  cycles={cycles}")
        print("-" * 70)

        print(f"[1/2] Running UnifiedEngine × {cycles} cycles...")
        unified_root = tmp_root / "ws_unified"
        u = run_one("unified", unified_root, llm, cycles)
        print(f"  cycles_completed={u['cycles_completed']}  "
              f"final_score={u['final_score']}")
        print(f"  score_history={u['score_history']}")
        print(f"  skills: {u['skills_after']}")

        print(f"[2/2] Running AEvolveEngine × {cycles} cycles...")
        legacy_root = tmp_root / "ws_legacy"
        l = run_one("legacy", legacy_root, llm, cycles)
        print(f"  cycles_completed={l['cycles_completed']}  "
              f"final_score={l['final_score']}")
        print(f"  score_history={l['score_history']}")
        print(f"  skills: {l['skills_after']}")

        print("-" * 70)
        print("Per-axis comparison:")

        checks = [
            ("cycles_completed", u["cycles_completed"] == l["cycles_completed"]),
            ("final_score",      abs(u["final_score"] - l["final_score"]) < 1e-6),
            ("score_history",    u["score_history"] == l["score_history"]),
            # history.jsonl has same shape of (cycle, score, mutated) per entry.
            ("history_shape",    [(h["cycle"], round(h["score"], 4)) for h in u["history"]]
                                  == [(h["cycle"], round(h["score"], 4)) for h in l["history"]]),
            # observations/batch_*.jsonl byte-equal (modulo timestamps / unified_*).
            ("batches_equal",    u["batches"] == l["batches"]),
        ]
        for name, ok in checks:
            mark = "✓" if ok else "✗"
            print(f"  {mark} {name}")

        # Skills written — expected to differ in NAME under real LLM
        # variability (temp=0 helps, but Claude can still pick different
        # skill names for the same prompt). Report but don't require equal.
        print(f"  ≈ skills_after: unified={u['skills_after']}  "
              f"legacy={l['skills_after']}  "
              f"(may differ under real LLM; temperature=0 is a soft guard)")

        all_ok = all(ok for _, ok in checks)
        print("-" * 70)
        if all_ok:
            print("SMOKE TEST PASSED — unified and legacy engines produce equivalent "
                  "observable outcomes (loop progress, scores, batch JSONL) over "
                  f"{cycles} cycles under real Bedrock.")
            return 0
        print("SMOKE TEST FAILED — at least one axis diverged. See ✗ above.")
        return 1


if __name__ == "__main__":
    cycles_arg = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    sys.exit(main(cycles=cycles_arg))
