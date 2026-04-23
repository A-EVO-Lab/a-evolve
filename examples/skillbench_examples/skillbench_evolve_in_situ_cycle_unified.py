"""SkillBench evolution runner using the Unified Engine.

Thin counterpart to `skillbench_evolve_in_situ_cycle.py` that goes
through `EvolutionLoop + UnifiedEngine` instead of calling
`AEvolveEngine.evolve()` directly.

**Scope vs the legacy in-situ script:**

This runner covers the *engine-level* equivalent of SkillBench evolution
— evolving general skills in `workspace/skills/`, matching what
`AEvolveEngine.step()` does (which is what the legacy script's
`evolver.evolve(...)` also reduces to at the engine layer).

It does NOT replicate the following features of
`skillbench_evolve_in_situ_cycle.py` (which live at the orchestration
layer, outside the engine):

  - Pre-solve task-specific skill generation (``_generate_task_skill``)
  - Post-solve task-specific skill evolution (``_evolve_task_skill``)
  - Retry loops per task with task-specific skill injection
  - Parallel solve via ThreadPoolExecutor
  - Harbor execution mode

Those features require Phase 2:
  - A ``GenerateTaskSkill`` operator (post-solve task-skill evolution)
  - A ``SkillBenchEvolutionLoop(EvolutionLoop)`` subclass with a
    pre-solve hook (pre-generate)

For now, Phase 1 legacy script continues to work via
``from agent_evolve.algorithms.skillforge import AEvolveEngine`` —
the legacy class is frozen per DEC-1 and still implements general-skill
evolution correctly.

See ``docs/algorithms/unified-equivalence-audit.md`` for the per-axis
parity report between the legacy and unified paths.

Usage:
    python skillbench_evolve_in_situ_cycle_unified.py \\
        --cycles 3 --limit 5 --output results.jsonl
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agent_evolve.agents.skillbench import SkillBenchAgent
from agent_evolve.agents.skillbench.paths import (
    resolve_skillbench_seed_workspaces_root,
)
from agent_evolve.agents.skillbench.repo import SkillBenchSetupError
from agent_evolve.algorithms.unified import UnifiedEngine
from agent_evolve.benchmarks.skill_bench import SkillBenchBenchmark
from agent_evolve.config import EvolveConfig
from agent_evolve.engine.loop import EvolutionLoop
from agent_evolve.llm.bedrock import BedrockProvider

logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run SkillBench evolution via UnifiedEngine + EvolutionLoop"
    )
    p.add_argument("--cycles", type=int, default=3,
                   help="Number of evolution cycles to run")
    p.add_argument("--batch-size", type=int, default=2,
                   help="Tasks per cycle (passed to benchmark.get_tasks limit)")
    p.add_argument("--limit", type=int, default=None,
                   help="Cap on total tasks loaded from benchmark")
    p.add_argument("--mode", default="native", choices=["native", "harbor"],
                   help="SkillBench execution mode")
    p.add_argument("--use-skills", default="true",
                   help="Whether the agent uses skills at solve time (true|false)")
    p.add_argument("--model-id", default=os.environ.get(
        "SKILLBENCH_MODEL_ID",
        "us.anthropic.claude-opus-4-5-20251101-v1:0",
    ))
    p.add_argument("--region", default=os.environ.get(
        "AWS_REGION", "us-west-2"))
    p.add_argument("--max-tokens", type=int, default=64000)
    p.add_argument("--seed-workspace", default=None,
                   help="Path to seed workspace; defaults to bundled skillbench workspace")
    p.add_argument("--run-dir", default=None,
                   help="Workspace directory for this run (must not exist)")
    p.add_argument("--output", default="results.jsonl",
                   help="Where to write the per-cycle summary JSONL")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _prepare_workspace(args) -> Path:
    import shutil

    if args.run_dir:
        run_dir = Path(args.run_dir)
    else:
        from datetime import datetime
        stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        run_dir = REPO_ROOT / "logs" / f"unified_skillbench_{stamp}_pid{os.getpid()}"

    if run_dir.exists():
        raise FileExistsError(f"run-dir already exists: {run_dir}")
    run_dir.mkdir(parents=True)

    if args.seed_workspace:
        seed_src = Path(args.seed_workspace)
    else:
        seed_root = resolve_skillbench_seed_workspaces_root()
        seed_src = seed_root / "skillbench"
    if not seed_src.is_dir():
        raise FileNotFoundError(f"seed workspace not found: {seed_src}")

    ws_dir = run_dir / "workspace"
    shutil.copytree(seed_src, ws_dir)
    logger.info("Workspace prepared at %s (from seed %s)", ws_dir, seed_src)
    return ws_dir


def main() -> int:
    args = _parse_args()
    _setup_logging(args.verbose)

    ws_dir = _prepare_workspace(args)

    # Benchmark configuration — mirrors what the legacy in-situ script
    # does, minus the orchestration-layer flags this runner doesn't use.
    use_skills = args.use_skills.lower() in ("true", "1", "yes", "on")
    bench = SkillBenchBenchmark(
        use_skills=use_skills,
        execution_mode=args.mode,
    )
    logger.info("Capability: %s", bench.feedback_capability)

    # Agent — pulls from seed workspace.
    agent = SkillBenchAgent(workspace_dir=ws_dir)

    # LLM provider shared between solve and evolve operators.
    llm = BedrockProvider(model_id=args.model_id, region=args.region)

    # Engine with real Bedrock injected into LLMBashEvolve's state slot.
    config = EvolveConfig(
        batch_size=args.batch_size,
        max_cycles=args.cycles,
    )
    engine = UnifiedEngine(config, bench)
    engine._operator_state.setdefault("LLMBashEvolve", {})["llm_provider"] = llm
    engine._operator_state.setdefault("SkillCurator", {})["llm_provider"] = llm

    # Loop — standard EvolutionLoop, unified engine plugs in as the engine slot.
    loop = EvolutionLoop(agent=agent, benchmark=bench, engine=engine, config=config)

    logger.info(
        "Running %d cycles (batch=%d, model=%s, ws=%s)",
        args.cycles, args.batch_size, args.model_id, ws_dir,
    )
    result = loop.run(cycles=args.cycles)

    # Dump results.
    out_path = Path(args.output)
    if not out_path.is_absolute():
        out_path = ws_dir.parent / args.output
    with open(out_path, "w") as f:
        for cycle_idx, score in enumerate(result.score_history, 1):
            f.write(json.dumps({
                "cycle": cycle_idx,
                "score": score,
                "mutated": True,  # UnifiedEngine's mutated is per-cycle; summary is aggregate
            }) + "\n")

    metrics_path = out_path.with_suffix(".metrics.json")
    metrics_path.write_text(json.dumps({
        "cycles_completed": result.cycles_completed,
        "final_score": result.final_score,
        "score_history": list(result.score_history),
        "converged": result.converged,
        "engine": "UnifiedEngine",
        "recipe_source": "EvolutionLoop + RuleBasedController",
        "workspace": str(ws_dir),
    }, indent=2))

    logger.info("Done. cycles=%d final_score=%.4f", result.cycles_completed, result.final_score)
    logger.info("Results: %s", out_path)
    logger.info("Metrics: %s", metrics_path)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SkillBenchSetupError as exc:
        print(f"SkillBench setup error: {exc}", file=sys.stderr)
        sys.exit(2)
