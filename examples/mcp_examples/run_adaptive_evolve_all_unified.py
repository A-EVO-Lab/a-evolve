"""MCP-Atlas evolution runner using the Unified Engine.

Thin counterpart to ``adaptive_evolve_all.py``. Where legacy uses
``AdaptiveEvolveEngine.evolve()`` with a custom batch loop, this
runner goes through ``EvolutionLoop + UnifiedEngine`` with the
``per_claim`` recipe branch (matches ``AdaptiveEvolveEngine.step()``).

**Axis parity with legacy:**

- Observation: same — ``Observation.feedback.raw["per_claim"]`` + hallucination hints
- Update pipeline (same order as legacy):
  ``[FixHallucinations, AutoSeedSkills, LLMBashEvolve, SanityCheck]``
- Verify: ``NoVerify`` (matches legacy ``step()`` path — stagnation gate only
  fires in legacy standalone ``evolve()`` API, not in loop)
- Output: ``prompts/system.md``, ``skills/<name>/SKILL.md``, ``memory/episodic.jsonl``
  (memory pruning nested in ``FixHallucinations`` to match legacy ordering)
- Scope: ``{prompts: rw, skills: rw, memory: append}``

See ``docs/algorithms/unified-equivalence-audit.md`` +
``docs/mcp-atlas-demo-unified.md`` for the full audit and usage guide.

Usage:
    python run_adaptive_evolve_all_unified.py \\
        --cycles 3 --batch-size 30 --output-dir logs/unified_mcp
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agent_evolve.agents.mcp import McpAgent
from agent_evolve.agents.mcp.docker_env import McpAtlasContainer, pull_image
from agent_evolve.agents.mcp.key_registry import KeyRegistry
from agent_evolve.agents.mcp.mcp_client import McpClientWrapper
from agent_evolve.algorithms.unified import UnifiedEngine
from agent_evolve.benchmarks.mcp_atlas import McpAtlasBenchmark
from agent_evolve.config import EvolveConfig
from agent_evolve.engine.loop import EvolutionLoop
from agent_evolve.llm.bedrock import BedrockProvider
from examples.mcp_examples.adaptive_evolve_all import CodeExecMcpAgent

logger = logging.getLogger(__name__)


def main() -> int:
    p = argparse.ArgumentParser(
        description="MCP-Atlas evolution via UnifiedEngine + EvolutionLoop"
    )
    # Unified pass / cycle knobs (mirrors swe / tb / sb unified runners).
    # When --passes or --cycle-per-batch is set, the script computes
    # max_cycles = passes × ⌈limit/batch_size⌉ × cycle_per_batch and
    # overrides --cycles. Otherwise --cycles is honoured (legacy default).
    p.add_argument("--passes", type=int, default=None,
                   help="Number of full sweeps of the dataset. If set "
                        "(or --cycle-per-batch is set), max_cycles is "
                        "computed as passes*⌈limit/batch⌉*cycle_per_batch.")
    p.add_argument("--cycle-per-batch", type=int, default=None, dest="cycle_per_batch",
                   help="In-batch retry multiplier (default: 1 when --passes is set).")
    p.add_argument("--cycles", type=int, default=None,
                   help="Direct EvolutionLoop max_cycles. "
                        "Overridden when --passes/--cycle-per-batch is set.")
    p.add_argument("--batch-size", type=int, default=30,
                   help="Tasks per cycle (passed to bench.get_tasks limit)")
    p.add_argument("--parallel", type=int, default=1,
                   help="Parallel workers within each batch (default 1; legacy MCP is serial).")
    p.add_argument("--parallel-backend", default="thread",
                   choices=["thread", "process", "benchmark"],
                   help="In-batch parallel backend (default thread for MCP).")
    p.add_argument("--limit", type=int, default=500,
                   help="Cap on total tasks loaded")
    p.add_argument("--solver-model", default="us.anthropic.claude-opus-4-6-v1",
                   help="Model for the MCP agent (solve side)")
    p.add_argument("--evolver-model", default=None,
                   help="Model for the evolver operators (defaults to solver-model)")
    p.add_argument("--region", default="us-west-2")
    p.add_argument("--max-tokens", type=int, default=16384)
    p.add_argument("--judge-model", "--eval-model-id", dest="judge_model",
                   default="us.anthropic.claude-sonnet-4-6",
                   help="Model for the MCP-Atlas LLM-as-judge evaluator")
    p.add_argument("--docker-image", type=str, default=None,
                   help="MCP-Atlas docker image; when set, uses one shared container.")
    p.add_argument("--dataset", default="ScaleAI/MCP-Atlas")
    p.add_argument("--seed-workspace", default=str(REPO_ROOT / "seed_workspaces" / "mcp"))
    p.add_argument("--output-dir", default=None,
                   help="Defaults to logs/unified_mcp_<timestamp>")
    p.add_argument("--env-file", default=None,
                   help="Path to .env file with MCP API keys. When set, the "
                        "KeyRegistry is loaded and the bench filters out "
                        "tasks whose required MCP servers don't have keys "
                        "(matches legacy adaptive_evolve_all.py:240-247).")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    for noisy in ("botocore", "urllib3", "httpcore", "httpx",
                  "strands.models", "strands.tools", "strands.telemetry"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    out_dir = Path(args.output_dir) if args.output_dir else (
        REPO_ROOT / "logs" / f"unified_mcp_{datetime.utcnow():%Y%m%d_%H%M%S}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    # Benchmark
    bench = McpAtlasBenchmark(
        dataset_name=args.dataset,
        shuffle=False,
        eval_model_id=args.judge_model,
        eval_region=args.region,
        use_litellm=False,
    )
    logger.info("Capability: %s", bench.feedback_capability)

    # ── Load MCP API keys (legacy parity: adaptive_evolve_all.py:240-247) ──
    # KeyRegistry.load() populates self._keys; without it, McpAgent.solve()
    # gets empty env_vars and most tasks fail with missing_key. We also
    # ask the bench to filter out tasks whose required MCP servers lack
    # keys, then snapshot the filtered cache so EvolutionLoop's later
    # batched get_tasks() calls (which don't pass key_registry) still see
    # the filtered set.
    key_registry: KeyRegistry | None = None
    if args.env_file:
        key_registry = KeyRegistry(env_file_path=args.env_file)
        key_registry.load()
        logger.info(
            "Loaded %d MCP API key(s) from %s",
            len(key_registry.get_loaded_key_names()), args.env_file,
        )
    # Legacy adaptive_evolve_all.py runs over split="test" with --limit and
    # optional key filtering. EvolutionLoop asks for split="train", so redirect
    # train to that same filtered ordered list before the loop starts.
    filtered_tasks = bench.get_tasks(
        split="test", limit=args.limit, key_registry=key_registry,
    )
    keep_ids = {t.id for t in filtered_tasks}
    before = len(bench._cache.get("test", []))
    bench._cache["train"] = [
        r for r in bench._cache.get("test", [])
        if (r.get("TASK") or r.get("task_id", "")) in keep_ids
    ][:args.limit]
    bench._cursor = 0
    logger.info("Tasks after key_registry filter: %d (was %d)", len(bench._cache["train"]), before)

    # Shared workspace
    ws_dir = out_dir / "workspace"
    seed_dir = Path(args.seed_workspace)
    if ws_dir.exists():
        shutil.rmtree(ws_dir)
    shutil.copytree(seed_dir, ws_dir)
    logger.info("Workspace: %s (from seed %s)", ws_dir, seed_dir)

    # LLM provider for evolver operators.
    evolver_model = args.evolver_model or args.solver_model
    llm = BedrockProvider(model_id=evolver_model, region=args.region)

    # Single env-var ablation switch. When MCP_BLANK_SKILL_ONLY_EVOLVE=1:
    #   - Only skills evolve (evolve_prompts=False, evolve_memory=False)
    #   - AutoSeedSkills is dropped from per_claim recipe (in controller.py)
    #   - improvement_threshold / stagnation_window are irrelevant under
    #     NoVerify but left in extra for legacy parity.
    blank_skill_only = os.environ.get("MCP_BLANK_SKILL_ONLY_EVOLVE") == "1"
    if blank_skill_only:
        logger.info(
            "MCP_BLANK_SKILL_ONLY_EVOLVE=1 — evolve_prompts=False, "
            "evolve_memory=False, AutoSeedSkills dropped in controller."
        )

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
        effective_cycles = max(1, math.ceil(len(bench._cache["train"]) / max(1, args.batch_size)))
        cycle_source = (
            f"legacy full sweep: ceil({len(bench._cache['train'])}/"
            f"{args.batch_size})"
        )

    config = EvolveConfig(
        batch_size=args.batch_size,
        max_cycles=effective_cycles,
        parallel_workers=max(1, args.parallel),
        parallel_backend=args.parallel_backend,
        evolver_model=evolver_model,
        evolver_max_tokens=8000,
        evolve_prompts=not blank_skill_only,
        evolve_skills=True,
        evolve_memory=not blank_skill_only,
        evolve_tools=False,
        extra={
            "region": args.region,
            "max_tokens": args.max_tokens,
            "legacy_profile": "mcp",
            "prompt_max_chars": 4000,
            "skill_max_chars": 2000,
            "max_skills": 15,
            "include_claim_details": True,
            "include_judge_patterns": True,
            "include_task_type_stats": True,
            "include_evolution_history": True,
            "improvement_threshold": 0.03,
            "stagnation_window": 5,
        },
    )

    all_env_vars = {}
    if args.docker_image and key_registry:
        all_env_vars = {
            name: entry.value
            for name, entry in key_registry._keys.items()
            if entry.value
        }

    container = None
    shared_client = None
    try:
        if args.docker_image:
            if not pull_image(args.docker_image):
                raise SystemExit(f"Failed to pull image {args.docker_image}")
            container = McpAtlasContainer(
                args.docker_image,
                container_name=os.environ.get("MCP_CONTAINER_NAME") or "mcp-atlas-unified-evolve",
                env_vars=all_env_vars,
            )
            container.start()
            shared_client = McpClientWrapper(base_url=container.base_url)
            logger.info("Shared MCP-Atlas container ready: %s", container.base_url)

        agent = CodeExecMcpAgent(
            workspace_dir=ws_dir,
            model_id=args.solver_model,
            region=args.region,
            max_tokens=args.max_tokens,
            docker_image=None,
            key_registry=key_registry,
            shared_client=shared_client,
        )
        engine = UnifiedEngine(config, bench)
        # LLMBashEvolve (the LLM-driven operator in the per_claim recipe)
        # reads state["llm_provider"] so operators don't implicitly construct
        # a new Bedrock client per step.
        engine._operator_state.setdefault("LLMBashEvolve", {})["llm_provider"] = llm

        loop = EvolutionLoop(agent=agent, benchmark=bench, engine=engine, config=config)

        logger.info(
            "Running %d cycles (%s) × batch_size=%d (limit=%d, solver=%s, evolver=%s)",
            effective_cycles, cycle_source, args.batch_size, args.limit,
            args.solver_model, evolver_model,
        )
        result = loop.run(cycles=effective_cycles)
    finally:
        if shared_client:
            shared_client.close()
        if container:
            container.stop()

    results_path = out_dir / "results.jsonl"
    with open(results_path, "w") as f:
        for cycle_idx, score in enumerate(result.score_history, 1):
            f.write(json.dumps({"cycle": cycle_idx, "score": score}) + "\n")

    (out_dir / "results.metrics.json").write_text(json.dumps({
        "cycles_completed": result.cycles_completed,
        "final_score": result.final_score,
        "score_history": list(result.score_history),
        "converged": result.converged,
        "engine": "UnifiedEngine",
        "legacy_settings": {
            "solver_model": args.solver_model,
            "evolver_model": evolver_model,
            "judge_model": args.judge_model,
            "use_litellm": False,
            "docker_image": args.docker_image,
            "limit": args.limit,
            "batch_size": args.batch_size,
            "parallel": args.parallel,
            "parallel_backend": args.parallel_backend,
            "evolver_max_tokens": 8000,
            "prompt_max_chars": 4000,
            "skill_max_chars": 2000,
            "max_skills": 15,
            "improvement_threshold": 0.03,
            "stagnation_window": 5,
        },
        "recipe": (
            "per_claim (PassFailReader+ClaimReader+PatternDetector+"
            "ClaimTypeAnalyzer+ScoreCurveReader | "
            "FixHallucinations+AutoSeedSkills+LLMBashEvolve+SanityCheck)"
        ),
        "workspace": str(ws_dir),
    }, indent=2))

    logger.info(
        "Done. cycles=%d final_score=%.4f. Results: %s",
        result.cycles_completed, result.final_score, results_path,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
