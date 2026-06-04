#!/usr/bin/env python3
"""EvolverBench Exp0 (RQ1 / builder-side) UNIFIED IN-SITU launcher.

Parallel to run_exp1_unified_insitu.py but designed for RQ1: vary the
builder/evolver across the larger EVOLVER_LONG_EXP0 pool (opus46, sonnet46,
haiku45, qwen235b, qwen32b, gptoss120b, qwen35_9b local), fix small solver
set. Routes evolve cells through UnifiedEngine in-situ entrypoints (same
shell wrappers as Exp1 unified) so cells are directly comparable with the
Exp1 unified-insitu sweep when (solver, evolver, benchmark) overlap.

Exp0 vs Exp1:
  Exp0 / RQ1 (this file): vary builder/evolver, fix small solver set.
      EVOLVER_MODELS = EVOLVER_LONG_EXP0 (7 builder choices + none).
      _region_picker.resolve(..., evolver_lookup=EVOLVER_LONG_EXP0).
  Exp1 / RQ2: vary solver, fix 3 working evolvers (opus46/sonnet46/qwen235b).
      Default _region_picker.EVOLVER_LONG (3 working evolvers).

  --evolver != none:
    swe      -> examples/swe_examples/run_swe_evolve_in-situ_unified.sh
    mcp      -> examples/mcp_examples/run_adaptive_evolve_in-situ_unified.sh
    sb       -> examples/skillbench_examples/run_skillbench_evolve_in-situ_unified.sh

  --evolver none (baselines shared with Exp1 unified launcher):
    swe      -> examples/swe_examples/run_solve_all.sh
    mcp      -> examples/mcp_examples/run_adaptive_evolve_baseline.sh
    sb       -> examples/skillbench_examples/run_skillbench_solve_all.sh

qwen35_9b note: regionless/local OpenAI-compatible evolver. Solver still
picks a Bedrock region; the evolver call is routed by the OpenAI-compatible
provider via EVOLVER_OPENAI_BASE_URL. Requires the unified-route runners to
honour the OpenAI path for the evolver-side LLMBashEvolve operator.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from _region_picker import (
    EVOLVER_LONG_EXP0,
    RegionResolveError,
    load_region_db,
    resolve as _resolve_region,
)


def _resolve_judge_id(region: str) -> str:
    """Pick sonnet 4.6's region-specific model_id (us./eu. prefix per JSON)."""
    db = load_region_db()
    for entry in db.get("Claude Sonnet 4.6", []):
        if entry.get("status") == "OK" and entry.get("region") == region:
            return entry["model_id"]
    raise RegionResolveError(
        f"Judge model 'Claude Sonnet 4.6' not OK in region {region}"
    )

logger = logging.getLogger("exp0_unified_insitu")

EXPERIMENT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = Path(
    os.environ.get("AEVOLVE_REPO_DIR")
    or EXPERIMENT_ROOT.parents[1]
).resolve()

SOLVER_MODELS: dict[str, str] = {
    "sonnet46": "us.anthropic.claude-sonnet-4-6",
    "opus46": "us.anthropic.claude-opus-4-6-v1",
    "haiku45": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "gptoss120b": "openai.gpt-oss-120b-1:0",
    "qwen235b": "qwen.qwen3-235b-a22b-2507-v1:0",
    "qwen32b": "qwen.qwen3-32b-v1:0",
    "minimax": "minimax.minimax-m2.5",
    "kimi": "moonshotai.kimi-k2.5",
}

# Exp0 evolver pool: superset of Exp1's 3-evolver pool. Keys (used by argparse
# metavar) are short-names; values are EVOLVER_LONG_EXP0 long-name keys (or
# the local qwen35_9b path) consumed only by the metavar / resolver lookup.
# Actual model_id resolution flows through _resolve_region(...,
# evolver_lookup=EVOLVER_LONG_EXP0) below.
EVOLVER_MODELS: dict[str, str] = dict(EVOLVER_LONG_EXP0)

UNIFIED: dict[str, dict] = {
    "swe": {
        "evolve_path": PROJECT_ROOT / "examples/swe_examples/run_swe_evolve_in-situ_unified.sh",
        "baseline_path": PROJECT_ROOT / "examples/swe_examples/run_solve_all.sh",
        "seed_dir": "swe",
        "evolver_model_supported": True,
    },
    "mcp": {
        "evolve_path": PROJECT_ROOT / "examples/mcp_examples/run_adaptive_evolve_in-situ_unified.sh",
        "baseline_path": PROJECT_ROOT / "examples/mcp_examples/run_adaptive_evolve_baseline.sh",
        "seed_dir": "mcp",
        "evolver_model_supported": True,
    },
    "sb": {
        "evolve_path": PROJECT_ROOT / "examples/skillbench_examples/run_skillbench_evolve_in-situ_unified.sh",
        "baseline_path": PROJECT_ROOT / "examples/skillbench_examples/run_skillbench_solve_all.sh",
        "seed_dir": "skillbench-upstream-parity",
        "evolver_model_supported": True,
    },
}

EVOLVE_DEFAULTS: dict[str, dict] = {
    "swe": {"LIMIT": 500, "BATCH_SIZE": 20, "PARALLEL": 20, "MAX_TOKENS": 16384},
    "mcp": {"LIMIT": 500, "BATCH_SIZE": 30, "PARALLEL": 1, "MAX_TOKENS": 16384},
    "sb": {"LIMIT": None, "BATCH_SIZE": 1, "MAX_CYCLES": 2, "PARALLEL": 1, "MAX_TOKENS": 16384},
}

BASELINE_DEFAULTS: dict[str, dict] = {
    "swe":      {"MAX_TOKENS": 16384},
    "mcp": {"LIMIT": 500, "BATCH_SIZE": 30, "MAX_TOKENS": 16384},
    "sb": {"LIMIT": None, "BATCH_SIZE": 5, "MAX_TOKENS": 16384},
}

BASELINE_REPORT: dict[str, dict] = {
    "swe":      {"done_marker": "results.json", "score_kind": "swe_baseline_results_json"},
    "mcp":      {"done_marker": "RUN_COMPLETE.json", "score_kind": "mcp_baseline_summary_csv"},
    "sb":       {"done_marker": "summary.txt",  "score_kind": "sb_baseline_summary_txt"},
}

UNIFIED_EVOLVE_REPORT: dict[str, dict] = {
    "swe":      {"done_marker": "results.metrics.json", "score_kind": "pass_ratio_metrics_json"},
    "mcp":      {"done_marker": "results.metrics.json", "score_kind": "pass_ratio_metrics_json"},
    "sb":       {"done_marker": "results.metrics.json", "score_kind": "pass_ratio_metrics_json"},
}


def _load_env_file() -> None:
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _seed_workspace(bm: str) -> str:
    return str(PROJECT_ROOT / "seed_workspaces" / UNIFIED[bm]["seed_dir"])


def _mcp_env_file() -> str:
    mcp_env_file = os.environ.get("MCP_ENV_FILE")
    if mcp_env_file:
        return mcp_env_file
    default_env_file = PROJECT_ROOT / ".env"
    return str(default_env_file) if default_env_file.exists() else ".env"


def _retry_env() -> dict[str, str]:
    return {
        "BEDROCK_RETRY_MAX_ATTEMPTS": os.environ.get("BEDROCK_RETRY_MAX_ATTEMPTS", "15"),
        "BEDROCK_READ_TIMEOUT_SEC": os.environ.get("BEDROCK_READ_TIMEOUT_SEC", "600"),
        "BEDROCK_CONNECT_TIMEOUT_SEC": os.environ.get("BEDROCK_CONNECT_TIMEOUT_SEC", "30"),
    }


def _write_benchmark_report(
    cell_dir: Path,
    bm: str,
    route: str,
    *,
    region_strategy: str,
    region: str,
    solver_model_id: str,
    evolver_model_id: str,
) -> None:
    info = UNIFIED_EVOLVE_REPORT[bm] if route == "evolve" else BASELINE_REPORT[bm]
    content = (
        "# BENCHMARK_REPORT\n"
        f"benchmark: {bm}\n"
        f"route: unified_{route}\n"
        f"done_marker: {info['done_marker']}\n"
        f"score_kind: {info['score_kind']}\n"
        f"region_strategy: {region_strategy}\n"
        f"region: {region}\n"
        f"solver_model_id: {solver_model_id}\n"
        f"evolver_model_id: {evolver_model_id}\n"
    )
    (cell_dir / "BENCHMARK_REPORT.md").write_text(content)


def _python_cmd() -> list[str]:
    if os.environ.get("VIRTUAL_ENV"):
        return [sys.executable]
    return ["env", "UV_CACHE_DIR=/tmp/uv_cache", "uv", "run", "python"]


def _common_env(run_name: str, region: str) -> dict[str, str]:
    return {
        "RUN_ID": run_name,
        "REGION": region,
        **_retry_env(),
    }


def _build_baseline(
    bm: str,
    solver_id: str,
    region: str,
    max_tokens: int,
    batch_size: int,
    limit: int | None,
    cell_dir: Path,
    run_name: str,
    seed: int,
) -> tuple[list[str], dict[str, str], Path | None]:
    env = _common_env(run_name, region)
    limit_str = str(limit) if limit is not None else ""
    path = UNIFIED[bm]["baseline_path"]

    if bm == "mcp":
        judge_id = os.environ.get("MCP_JUDGE_MODEL") or _resolve_judge_id(region)
        env.update({
            "SOLVER_MODEL": solver_id,
            "JUDGE_MODEL": judge_id,
            "MAX_TOKENS": str(max_tokens),
            "LIMIT": limit_str,
            "BATCH_SIZE": str(batch_size),
            "WORKERS": os.environ.get("MCP_WORKERS", "5"),
            "SEED_WORKSPACE": _seed_workspace("mcp"),
            "WORK_DIR": str(cell_dir / "workspace"),
            "OUTPUT_DIR": str(cell_dir),
            "DOCKER_IMAGE": "ghcr.io/scaleapi/mcp-atlas:latest",
            "ENV_FILE": _mcp_env_file(),
            "MCP_CONTAINER_NAME": f"mcp-atlas-{os.environ.get('MCP_CONTAINER_TAG', '')}{'-' if os.environ.get('MCP_CONTAINER_TAG') else ''}{run_name}",
        })
        return ["bash", str(path)], env, None

    env.update({
        "MODEL_ID": solver_id,
        "MAX_TOKENS": str(max_tokens),
    })
    if bm == "swe":
        env.update({
            "OUTPUT_DIR": str(cell_dir),
            "RUN_ID": run_name,
            "WORKERS": os.environ.get("SWE_WORKERS", "20"),
        })
        return ["bash", str(path)], env, None
    if bm == "sb":
        env.update({
            "RUN_DIR": str(cell_dir),
            "SEED_WORKSPACE": _seed_workspace("sb"),
            "SPLIT_SEED": str(seed),
            "USE_SKILLS": "false",
        })
        return ["bash", str(path)], env, None
    raise ValueError(f"unknown benchmark: {bm}")


def _build_evolve(
    bm: str,
    solver_id: str,
    evolver_id: str,
    region: str,
    *,
    limit: int | None,
    evolve_limit: int | None,
    eval_limit: int | None,
    batch_size: int,
    train_parallel: int | None,
    test_parallel: int | None,
    cycle: int | None,
    max_tokens: int,
    cell_dir: Path,
    run_name: str,
    seed: int,
) -> tuple[list[str], dict[str, str], Path | None]:
    env = _common_env(run_name, region)
    path = UNIFIED[bm]["evolve_path"]

    if bm == "swe":
        env.update({
            "MODEL_ID": solver_id,
            "EVOLVER_MODEL_ID": evolver_id,
            "OUTPUT_DIR": str(cell_dir),
            "RUN_ID": run_name,
            "MAX_TOKENS": str(max_tokens),
        })
        if limit is not None:
            env["LIMIT"] = str(limit)
        if batch_size:
            env["BATCH_SIZE"] = str(batch_size)
        if train_parallel is not None:
            env["PARALLEL"] = str(train_parallel)
        return ["bash", str(path)], env, None

    if bm == "mcp":
        judge_id = os.environ.get("MCP_JUDGE_MODEL") or _resolve_judge_id(region)
        env.update({
            "SOLVER_MODEL": solver_id,
            "EVOLVER_MODEL": evolver_id,
            "JUDGE_MODEL": judge_id,
            "MAX_TOKENS": str(max_tokens),
            "OUTPUT_DIR": str(cell_dir),
            "RUN_ID": run_name,
            "MCP_CONTAINER_NAME": f"mcp-atlas-{os.environ.get('MCP_CONTAINER_TAG', '')}{'-' if os.environ.get('MCP_CONTAINER_TAG') else ''}{run_name}",
            "SEED_WORKSPACE": _seed_workspace("mcp"),
            "DOCKER_IMAGE": "ghcr.io/scaleapi/mcp-atlas:latest",
            "ENV_FILE": _mcp_env_file(),
        })
        if limit is not None:
            env["LIMIT"] = str(limit)
        if batch_size:
            env["BATCH_SIZE"] = str(batch_size)
        if train_parallel is not None:
            env["PARALLEL"] = str(train_parallel)
        return ["bash", str(path)], env, None

    if bm == "sb":
        env.update({
            "MODEL_ID": solver_id,
            "EVOLVER_MODEL_ID": evolver_id,
            "RUN_DIR": str(cell_dir),
            "RUN_ID": run_name,
            "MAX_TOKENS": str(max_tokens),
            "SEED_WORKSPACE": _seed_workspace("sb"),
        })
        if limit is not None:
            env["LIMIT"] = str(limit)
        if batch_size:
            env["BATCH_SIZE"] = str(batch_size)
        if train_parallel is not None:
            env["MAX_WORKERS"] = str(train_parallel)
        if cycle is not None:
            env["MAX_CYCLES"] = str(cycle)
        return ["bash", str(path)], env, None

    raise ValueError(f"unknown benchmark: {bm}")


def main() -> int:
    _load_env_file()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    p = argparse.ArgumentParser(
        description="EvolverBench Exp0 unified in-situ - one (solver, evolver, benchmark, seed) run.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--solver", required=True,
                   metavar="{" + ",".join(sorted(SOLVER_MODELS)) + "}",
                   help="Solver short-name.")
    p.add_argument("--evolver", required=True,
                   metavar="{" + ",".join(sorted(EVOLVER_MODELS) + ["none"]) + "}",
                   help="Evolver short-name (Exp0 pool). 'none' = no-evolution baseline.")
    p.add_argument("--benchmark", required=True, choices=sorted(UNIFIED),
                   help="Benchmark: swe / mcp / sb.")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--limit", type=int, default=None,
                   help="Task cap for supported wrappers.")
    p.add_argument("--evolve-limit", type=int, default=None,
                   help="Accepted for interface parity with split launchers.")
    p.add_argument("--eval-limit", type=int, default=None,
                   help="Accepted for interface parity with split launchers.")
    p.add_argument("--batch-size", type=int, default=None)
    p.add_argument("--train-parallel", type=int, default=None,
                   help="Parallel worker count where supported.")
    p.add_argument("--test-parallel", type=int, default=None,
                   help="Accepted for interface parity; in-situ wrappers ignore it.")
    p.add_argument("--cycle", type=int, default=None,
                   help="SkillBench max cycles.")
    p.add_argument("--cycles", type=int, default=None,
                   help="Alias for --cycle.")
    p.add_argument("--max-tokens", type=int, default=None)
    p.add_argument("--region", default=os.environ.get("AWS_DEFAULT_REGION", "us-west-2"))
    p.add_argument("--region-strategy", choices=("single", "hash"),
                   default=os.environ.get("REGION_STRATEGY", "single"))
    p.add_argument("--output-root", default=str(EXPERIMENT_ROOT / "results" / "exp0_unified_insitu"))
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--resolve-only", action="store_true")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    bm = args.benchmark
    route = "baseline" if args.evolver == "none" else "evolve"
    defaults = EVOLVE_DEFAULTS[bm] if route == "evolve" else BASELINE_DEFAULTS[bm]

    try:
        region, solver_id, evolver_id = _resolve_region(
            args.region_strategy,
            args.solver,
            args.evolver,
            bm,
            args.seed,
            args.region if args.region_strategy == "single" else None,
            evolver_lookup=EVOLVER_LONG_EXP0,
        )
    except RegionResolveError as exc:
        logger.error("Region resolution failed: %s", exc)
        return 2

    if args.resolve_only:
        payload = {
            "region_strategy": args.region_strategy,
            "region": region,
            "solver_model_id": solver_id,
            "evolver_model_id": evolver_id,
            "route": route,
            "benchmark": bm,
            "seed": args.seed,
            "solver": args.solver,
            "evolver": args.evolver,
        }
        if args.json:
            print(json.dumps(payload))
        else:
            for key, value in payload.items():
                print(f"{key}: {value}")
        return 0

    wrapper_path = UNIFIED[bm]["baseline_path"] if route == "baseline" else UNIFIED[bm]["evolve_path"]
    if not wrapper_path.exists():
        logger.error("Unified entrypoint missing: %s", wrapper_path)
        return 2

    limit = args.limit if args.limit is not None else defaults.get("LIMIT")
    batch_size = args.batch_size if args.batch_size is not None else defaults.get("BATCH_SIZE", 5)
    max_tokens = args.max_tokens if args.max_tokens is not None else defaults["MAX_TOKENS"]
    cycle = args.cycles if args.cycles is not None else args.cycle
    if cycle is None:
        cycle = defaults.get("MAX_CYCLES")

    evolve_limit = args.evolve_limit if args.evolve_limit is not None else defaults.get("EVOLVE_LIMIT")
    eval_limit = args.eval_limit if args.eval_limit is not None else defaults.get("EVAL_LIMIT")
    train_parallel = args.train_parallel if args.train_parallel is not None else defaults.get("PARALLEL")
    test_parallel = args.test_parallel if args.test_parallel is not None else defaults.get("TEST_PARALLEL")

    if args.dry_run:
        batch_size = 1
        max_tokens = min(max_tokens, 4096)
        limit = 1
        train_parallel = 1
        if bm == "sb":
            cycle = 1
        if bm == "swe":
            os.environ.setdefault("LIMIT", "2")
            os.environ.setdefault("BATCH_SIZE", "1")
            os.environ.setdefault("PARALLEL", "1")
        logger.info("DRY RUN enabled")

    run_name = f"{args.solver}_x_{args.evolver}_{bm}_s{args.seed}"
    if args.dry_run:
        run_name += "_dryrun"
    cell_dir = Path(args.output_root) / run_name
    cell_dir.mkdir(parents=True, exist_ok=True)

    if route == "evolve":
        cmd, env_delta, post_copy = _build_evolve(
            bm, solver_id, evolver_id or solver_id, region,
            limit=limit,
            evolve_limit=evolve_limit,
            eval_limit=eval_limit,
            batch_size=batch_size,
            train_parallel=train_parallel,
            test_parallel=test_parallel,
            cycle=cycle,
            max_tokens=max_tokens,
            cell_dir=cell_dir,
            run_name=run_name,
            seed=args.seed,
        )
        if not UNIFIED[bm]["evolver_model_supported"] and args.evolver != "none":
            logger.warning(
                "%s unified evolve does not expose a separate evolver model; "
                "evolution uses the solver model (%s).",
                bm, solver_id,
            )
    else:
        cmd, env_delta, post_copy = _build_baseline(
            bm, solver_id, region, max_tokens, batch_size, limit,
            cell_dir, run_name, args.seed,
        )

    _write_benchmark_report(
        cell_dir, bm, route,
        region_strategy=args.region_strategy,
        region=region,
        solver_model_id=solver_id,
        evolver_model_id=evolver_id,
    )

    logger.info("=== EvolverBench Exp0 unified in-situ ===")
    logger.info("  solver:     %s (%s)", args.solver, solver_id)
    logger.info("  evolver:    %s (%s)", args.evolver, evolver_id or "<none/bound-to-solver>")
    logger.info("  benchmark:  %s", bm)
    logger.info("  route:      unified_%s", route)
    logger.info("  entrypoint: %s", wrapper_path)
    logger.info("  region:     %s (strategy=%s)", region, args.region_strategy)
    logger.info("  cell_dir:   %s", cell_dir)
    logger.info("  env(delta): %s", {k: v for k, v in env_delta.items() if k not in os.environ})

    started = time.time()
    rc = subprocess.call(cmd, env={**os.environ, **env_delta}, cwd=str(PROJECT_ROOT))
    elapsed = time.time() - started

    logger.info("=== Done (rc=%d, %.1fs) - %s", rc, elapsed, cell_dir)
    return rc


if __name__ == "__main__":
    sys.exit(main())
