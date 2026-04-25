"""Terminal-Bench 2.0 evolution runner using the Unified Engine.

Thin counterpart to ``batch_evolve_terminal.py``. Where legacy uses
``AdaptiveSkillEngine.evolve()`` with a custom batch loop, this runner
goes through ``EvolutionLoop + UnifiedEngine`` with the ``drafts``
recipe branch (matches ``AdaptiveSkillEngine.step()``).

**Axis parity with legacy:**

- Observation: same — workspace drafts in ``skills/_drafts/`` + pass/fail feedback
- Update pipeline: ``[LLMBashEvolve]`` (operator equivalent of ``_run_llm`` with bash tool)
- Verify: ``NoVerify``
- Output: ``skills/<name>/SKILL.md`` + may touch ``prompts/system.md``
- Scope: ``{skills: rw, prompts: rw}`` (no memory writes)

**AC-9 drift awareness:** Drafts may be consumed by ``LLMBashEvolve``
between cycles; if cycle N has drafts but cycle N+1 doesn't, the
controller re-routes to the ``default`` recipe and emits a warning
(both plans printed). This matches legacy behavior (once drafts are
exhausted, legacy also stops producing new skills via the drafts path).

Usage:
    python batch_evolve_terminal_unified.py RUN_NAME --cycles 3
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import shutil
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agent_evolve.agents.terminal.agent import TerminalAgent
from agent_evolve.algorithms.unified import UnifiedEngine
from agent_evolve.benchmarks.tb2.terminal2 import Terminal2Benchmark
from agent_evolve.config import EvolveConfig
from agent_evolve.engine.loop import EvolutionLoop
from agent_evolve.llm.bedrock import BedrockProvider

logger = logging.getLogger(__name__)


def main() -> int:
    p = argparse.ArgumentParser(
        description="Terminal-Bench 2.0 evolution via UnifiedEngine + EvolutionLoop"
    )
    p.add_argument("run_name", help="Name/tag for this run (becomes log dir)")
    # Unified pass / cycle knobs (mirrors swe / mcp / sb unified runners).
    # When --passes or --cycle-per-batch is set, the script computes
    # max_cycles = passes × ⌈limit/batch_size⌉ × cycle_per_batch and
    # overrides --cycles. Otherwise --cycles is honoured (legacy default).
    p.add_argument("--passes", type=int, default=None,
                   help="Number of full sweeps of the dataset. If set "
                        "(or --cycle-per-batch is set), max_cycles is "
                        "computed as passes*⌈limit/batch⌉*cycle_per_batch.")
    p.add_argument("--cycle-per-batch", type=int, default=None, dest="cycle_per_batch",
                   help="In-batch retry multiplier (default: 1 when --passes is set).")
    p.add_argument("--cycles", type=int, default=None, help="Direct EvolutionLoop "
                   "max_cycles. If omitted, the runner uses one full sweep. "
                   "Overridden when --passes/--cycle-per-batch is set.")
    p.add_argument("--batch-size", type=int, default=5,
                   help="Tasks per cycle (passed to bench.get_tasks limit)")
    p.add_argument("--parallel", type=int, default=6,
                   help="Parallel workers within each batch (default 6; matches TB wrapper)")
    p.add_argument("--parallel-backend", default="thread",
                   choices=["thread", "process", "benchmark"],
                   help="In-batch parallel backend (default thread for TB).")
    p.add_argument("--limit", type=int, default=20,
                   help="Max tasks from benchmark")
    # Skill-budget cap (mirrors the legacy `batch_evolve_terminal.py
    # --max-skills`). Threaded through EvolveConfig.extra["max_skills"]
    # so SkillCurator's prompt builder (`agent_evolve.algorithms.skillforge
    # .prompts._build_*_instructions`) reads it and emits the
    # "SKILL BUDGET REACHED" guard text.
    p.add_argument("--max-skills", type=int, default=6,
                   help="Maximum total skills the evolver may keep in the "
                        "workspace (default 6; matches run_evolution.sh).")
    p.add_argument("--model-id", default="us.anthropic.claude-opus-4-6-v1",
                   help="Solver model id")
    p.add_argument("--solver", default="react", choices=["react", "strands"],
                   help="Terminal-Bench solver (default react; matches legacy TB).")
    p.add_argument("--evolver-model-id", default=None,
                   help="Evolver model id (defaults to --model-id)")
    p.add_argument("--region", default="us-west-2")
    p.add_argument("--max-tokens", type=int, default=16384)
    p.add_argument("--challenges-dir", default=None,
                   help="Path to TB2 challenges dir (otherwise defaults from env)")
    p.add_argument("--seed-workspace", default=str(REPO_ROOT / "seed_workspaces" / "terminal"))
    p.add_argument("--log-dir", default=None, help="Defaults to logs/unified_tb_<run_name>")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    log_dir = Path(args.log_dir) if args.log_dir else (
        REPO_ROOT / "logs" / f"unified_tb_{args.run_name}"
    )
    log_dir.mkdir(parents=True, exist_ok=True)

    # Benchmark. shuffle=False makes the task order deterministic (dataset
    # order, first `limit` tasks). The TB baseline runner (batch_evolve_terminal.py)
    # also defaults to shuffle=False, so evolve and baseline cells see the
    # SAME 50 tasks — required for a fair lift = with_evo - no_evo
    # comparison. The Terminal2Benchmark adapter defaults shuffle=True and
    # does not seed random, so leaving the default would have evolve and
    # baseline see disjoint random samples.
    bench_kwargs = {"shuffle": False}
    if args.challenges_dir:
        bench_kwargs["challenges_dir"] = args.challenges_dir
    bench = Terminal2Benchmark(**bench_kwargs)
    logger.info("Capability: %s", bench.feedback_capability)

    # Shared workspace (copied from seed)
    ws_dir = log_dir / "workspace"
    seed_dir = Path(args.seed_workspace)
    if ws_dir.exists():
        shutil.rmtree(ws_dir)
    if seed_dir.is_dir():
        shutil.copytree(seed_dir, ws_dir)
        logger.info("Workspace: %s (from seed %s)", ws_dir, seed_dir)
    else:
        ws_dir.mkdir(parents=True, exist_ok=True)
        (ws_dir / "prompts").mkdir()
        (ws_dir / "prompts" / "system.md").write_text("# Agent\n\n")
        logger.info("Workspace: %s (fresh — seed dir %s missing)", ws_dir, seed_dir)

    agent = TerminalAgent(
        workspace_dir=ws_dir,
        model_id=args.model_id,
        region=args.region,
        max_tokens=args.max_tokens,
        solver=args.solver,
    )

    evolver_model_id = args.evolver_model_id or args.model_id
    llm = BedrockProvider(model_id=evolver_model_id, region=args.region)

    # Resolve effective max_cycles. If --passes or --cycle-per-batch is
    # set explicitly, the unified formula wins; otherwise honour --cycles.
    if args.passes is not None or args.cycle_per_batch is not None:
        passes = args.passes if args.passes is not None else 1
        cpb = args.cycle_per_batch if args.cycle_per_batch is not None else 1
        batches_per_pass = max(1, math.ceil(args.limit / max(1, args.batch_size)))
        effective_cycles = passes * batches_per_pass * cpb
        cycle_source = (
            f"passes={passes} × ⌈{args.limit}/{args.batch_size}⌉={batches_per_pass} "
            f"× cycle_per_batch={cpb}"
        )
    elif args.cycles is not None:
        effective_cycles = args.cycles
        cycle_source = f"--cycles={args.cycles}"
    else:
        effective_cycles = max(1, math.ceil(args.limit / max(1, args.batch_size)))
        cycle_source = f"legacy full sweep: ceil({args.limit}/{args.batch_size})"

    config = EvolveConfig(
        batch_size=args.batch_size,
        max_cycles=effective_cycles,
        parallel_workers=max(1, args.parallel),
        parallel_backend=args.parallel_backend,
        evolver_model=evolver_model_id,
        trajectory_only=True,
        evolve_prompts=False,
        evolve_skills=True,
        evolve_memory=False,
        evolve_tools=False,
        extra={
            "region": args.region,
            "max_tokens": args.max_tokens,
            "max_skills": args.max_skills,
            "legacy_profile": "tb",
            "skills_only": True,
            "protect_skills": True,
            "prompt_only": False,
            "solver_proposed": False,
        },
    )
    engine = UnifiedEngine(config, bench)
    engine._operator_state.setdefault("LLMBashEvolve", {})["llm_provider"] = llm

    loop = EvolutionLoop(agent=agent, benchmark=bench, engine=engine, config=config)

    logger.info(
        "Running %d cycles (%s) × batch_size=%d on %d total tasks "
        "(solver=%s, solver_model=%s, evolver=%s)",
        effective_cycles, cycle_source, args.batch_size, args.limit,
        args.solver, args.model_id, evolver_model_id,
    )
    result = loop.run(cycles=effective_cycles)

    results_path = log_dir / "results.jsonl"
    with open(results_path, "w") as f:
        for cycle_idx, score in enumerate(result.score_history, 1):
            f.write(json.dumps({"cycle": cycle_idx, "score": score}) + "\n")

    (log_dir / "results.metrics.json").write_text(json.dumps({
        "cycles_completed": result.cycles_completed,
        "final_score": result.final_score,
        "score_history": list(result.score_history),
        "converged": result.converged,
        "engine": "UnifiedEngine",
        "legacy_settings": {
            "parallel": args.parallel,
            "parallel_backend": args.parallel_backend,
            "solver": args.solver,
            "trajectory_only": True,
            "skills_only": True,
            "protect_skills": True,
            "max_skills": args.max_skills,
        },
        "recipe": "drafts (PassFailReader+DraftReader+TrajectoryCompressor | LLMBashEvolve)",
        "workspace": str(ws_dir),
    }, indent=2))

    logger.info("Done. cycles=%d final_score=%.4f", result.cycles_completed, result.final_score)
    logger.info("Results: %s", results_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
