"""SWE-bench Verified evolution runner using the Unified Engine.

Thin counterpart to ``evolve_sequential.py``. Where legacy uses
``GuidedSynthesisEngine.evolve()`` with a custom batch loop, this
runner goes through ``EvolutionLoop + UnifiedEngine`` with the
``solver_proposal`` recipe (matches ``GuidedSynthesisEngine(write_memory=False)``).

**Axis parity with legacy:**

- Observation: same — ``trajectory._skill_proposal`` attached by SWE agent
- Update pipeline: ``[SkillCurator]`` over ``ProposalReader`` output (operator
  equivalent of ``_curate_proposals`` + ``_execute_curation``)
- Verify: ``NoVerify`` (matches ``GuidedSynthesisEngine.step()`` path)
- Output: ``skills/<curated_name>/SKILL.md``
- Scope: ``{skills: rw, memory/prompts/tools: ro}``

See ``docs/algorithms/unified-equivalence-audit.md`` for the full audit.

Usage:
    python evolve_sequential_unified.py \\
        --cycles 3 --limit 5 --output-dir logs/unified_swe
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

from agent_evolve.agents.swe.agent import SweAgent
from agent_evolve.algorithms.unified import UnifiedEngine
from agent_evolve.benchmarks.swe_verified_mini.benchmark import SweVerifiedMiniBenchmark
from agent_evolve.config import EvolveConfig
from agent_evolve.engine.loop import EvolutionLoop
from agent_evolve.llm.bedrock import BedrockProvider

logger = logging.getLogger(__name__)


def main() -> int:
    p = argparse.ArgumentParser(
        description="SWE-bench Verified evolution via UnifiedEngine + EvolutionLoop"
    )
    # Unified pass / cycle knobs (mirrors mcp / tb / sb unified runners).
    #   --passes K           how many full sweeps of the dataset (outer loop).
    #   --cycle-per-batch C  in-batch retry multiplier (default 1 = legacy
    #                        single-attempt-per-batch). C>1 currently
    #                        increases total EvolutionLoop cycles (cursor
    #                        still advances each iteration); true
    #                        retry-same-batch semantics would require a
    #                        core EvolutionLoop change which is out of
    #                        scope at this layer.
    # When EITHER --passes or --cycle-per-batch is set explicitly, the
    # script computes max_cycles = passes * ⌈limit/batch_size⌉ * cycle_per_batch
    # and that value wins over --cycles. Otherwise --cycles is honored as
    # the legacy direct knob (default 3 preserved for back-compat).
    p.add_argument("--passes", type=int, default=None,
                   help="Number of full sweeps of the dataset. If set "
                        "(or --cycle-per-batch is set), max_cycles is "
                        "computed as passes*⌈limit/batch⌉*cycle_per_batch.")
    p.add_argument("--cycle-per-batch", type=int, default=None, dest="cycle_per_batch",
                   help="In-batch retry multiplier (default: 1 when --passes is set).")
    p.add_argument("--cycles", type=int, default=None,
                   help="Direct EvolutionLoop max_cycles. If omitted, the "
                        "runner uses one legacy full sweep. Overridden when "
                        "--passes/--cycle-per-batch is set.")
    p.add_argument("--batch-size", type=int, default=5,
                   help="Tasks per cycle (passed to bench.get_tasks limit)")
    p.add_argument("--limit", type=int, default=50,
                   help="Cap on total tasks loaded from benchmark")
    p.add_argument("--parallel", type=int, default=5,
                   help="Parallel workers within each batch (default 5).")
    p.add_argument("--parallel-backend", default="process",
                   choices=["thread", "process", "benchmark"],
                   help="In-batch parallel backend (default process; matches legacy SWE).")
    p.add_argument("--feedback", type=str, default="minimal",
                   choices=["none", "minimal"],
                   help="Feedback to evolver: none masks scores; minimal includes scores.")
    p.add_argument("--solver-proposes", action="store_true",
                   help="Enable legacy V11 solver-proposed skill curation.")
    p.add_argument("--verification-focus", action="store_true",
                   help="Solver/curator focus on verification skills.")
    p.add_argument("--efficiency-prompt", action="store_true",
                   help="Add hypothesis-first efficiency constraints to solver prompt.")
    p.add_argument("--model-id", default="us.anthropic.claude-opus-4-6-v1",
                   help="Solver model id")
    p.add_argument("--evolver-model-id", default=None,
                   help="Evolver model id (defaults to --model-id)")
    p.add_argument("--region", default="us-west-2")
    p.add_argument("--max-tokens", type=int, default=16384)
    p.add_argument("--max-steps", type=int, default=0,
                   help="Max tool calls per task (0=unlimited)")
    p.add_argument("--window-size", type=int, default=40,
                   help="Sliding window size for conversation memory")
    p.add_argument("--dataset", default="MariusHobbhahn/swe-bench-verified-mini")
    p.add_argument("--seed-workspace", default=str(REPO_ROOT / "seed_workspaces" / "swe"))
    p.add_argument("--output-dir", default=None,
                   help="Defaults to logs/unified_swe_<timestamp>")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    for noisy in ("botocore", "urllib3", "httpcore", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # Output dir
    out_dir = Path(args.output_dir) if args.output_dir else (
        REPO_ROOT / "logs" / f"unified_swe_{datetime.utcnow():%Y%m%d_%H%M%S}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    # Benchmark + agent
    bench = SweVerifiedMiniBenchmark(dataset_name=args.dataset, shuffle=False)
    logger.info("Capability: %s", bench.feedback_capability)

    # Shared workspace (copied from seed)
    ws_dir = out_dir / "workspace"
    seed_dir = Path(args.seed_workspace)
    if ws_dir.exists():
        shutil.rmtree(ws_dir)
    shutil.copytree(seed_dir, ws_dir)
    logger.info("Workspace: %s (from seed %s)", ws_dir, seed_dir)

    agent = SweAgent(
        workspace_dir=ws_dir,
        model_id=args.model_id,
        region=args.region,
        max_tokens=args.max_tokens,
        max_steps=args.max_steps,
        window_size=args.window_size,
        verification_focus=args.verification_focus,
        efficiency_prompt=args.efficiency_prompt,
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
        trajectory_only=args.feedback == "none",
        evolve_prompts=False,
        evolve_skills=bool(args.solver_proposes),
        evolve_memory=False,
        evolve_tools=False,
        extra={
            "region": args.region,
            "max_tokens": args.max_tokens,
            "legacy_profile": "swe",
            "solver_proposes": bool(args.solver_proposes),
            "solver_proposals_visible_when_feedback_masked": bool(args.solver_proposes),
            "write_memory": False,
            "verification_focus": bool(args.verification_focus),
        },
    )
    engine = UnifiedEngine(config, bench)
    # Inject LLM so SkillCurator (and any LLMBashEvolve fallback path) can reach it.
    engine._operator_state.setdefault("SkillCurator", {})["llm_provider"] = llm
    engine._operator_state.setdefault("LLMBashEvolve", {})["llm_provider"] = llm

    loop = EvolutionLoop(agent=agent, benchmark=bench, engine=engine, config=config)

    logger.info(
        "Running %d cycles (%s) × batch_size=%d (limit=%d, solver=%s, evolver=%s)",
        effective_cycles, cycle_source, args.batch_size,
        args.limit, args.model_id, evolver_model_id,
    )
    result = loop.run(cycles=effective_cycles)

    # Write results
    results_path = out_dir / "results.jsonl"
    with open(results_path, "w") as f:
        for cycle_idx, score in enumerate(result.score_history, 1):
            f.write(json.dumps({
                "cycle": cycle_idx,
                "score": score,
            }) + "\n")

    metrics_path = out_dir / "results.metrics.json"
    metrics_path.write_text(json.dumps({
        "cycles_completed": result.cycles_completed,
        "final_score": result.final_score,
        "score_history": list(result.score_history),
        "converged": result.converged,
        "engine": "UnifiedEngine",
        "recipe": (
            "solver_proposal (SkillCurator only; write_memory=False)"
            if args.solver_proposes
            else "swe legacy no-op (solver_proposes=False)"
        ),
        "legacy_settings": {
            "feedback": args.feedback,
            "solver_proposes": args.solver_proposes,
            "write_memory": False,
            "parallel": args.parallel,
            "parallel_backend": args.parallel_backend,
            "max_steps": args.max_steps,
            "window_size": args.window_size,
        },
        "workspace": str(ws_dir),
    }, indent=2))

    logger.info(
        "Done. cycles=%d final_score=%.4f. Results: %s",
        result.cycles_completed, result.final_score, results_path,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
