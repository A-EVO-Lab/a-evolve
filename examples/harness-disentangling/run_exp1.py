#!/usr/bin/env python3
"""EvolverBench Exp1 launcher for the harness-disentangling artifact.

Pivot (2026-04-21): the axis under study is the **solver**, with a fixed
evolver pool of 3 models known to produce useful mutations. Routing:

  --evolver ∈ {opus46, sonnet46, qwen235b}  → unified split evolve route:
    swe → examples/swe_examples/run_swe_evolve_split_unified.sh
    mcp → examples/mcp_examples/run_adaptive_evolve_split_unified.sh
    sb  → examples/skillbench_examples/run_skillbench_evolve_split_unified.sh

  --evolver none                            → baseline route (no EvolutionEngine):
    swe → examples/swe_examples/run_solve_all.sh
    mcp → examples/mcp_examples/run_adaptive_evolve_baseline.sh
    sb  → examples/skillbench_examples/run_skillbench_solve_all.sh

User-facing knobs:
  --evolve-limit N           train/evolve task count for split evolve route.
  --eval-limit N             test task count after train slice. Omitted means
                             the wrapper evaluates all remaining tasks.
  --limit N                  total task cap where the wrapper supports one
                             (swe/mcp/sb).
  --batch-size N             train/evolve batch size.
  --cycle C / --cycles C     SkillBench split cycle-per-batch. Ignored for
                             swe/mcp split and for baseline route.
  --passes K                 deprecated compatibility knob; ignored by split
                             wrappers.

Environment:
  BEDROCK_API_KEY            ABSK key for Bedrock auth (or IAM creds)
  AEVOLVE_REPO_DIR           override repository path (default: current checkout)
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

from _region_picker import RegionResolveError, resolve as _resolve_region

logger = logging.getLogger("exp1")

# ── Paths ───────────────────────────────────────────────────────────────────

EXPERIMENT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = Path(
    os.environ.get("AEVOLVE_REPO_DIR")
    or EXPERIMENT_ROOT.parents[1]
).resolve()

# ── Solver pool (8 models, Exp1) ────────────────────────────────────────────

SOLVER_MODELS: dict[str, str] = {
    "sonnet46":   "us.anthropic.claude-sonnet-4-6",
    "opus46":     "us.anthropic.claude-opus-4-6-v1",
    "haiku45":    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "gptoss120b": "openai.gpt-oss-120b-1:0",
    "qwen235b":   "qwen.qwen3-235b-a22b-2507-v1:0",
    "qwen32b":    "qwen.qwen3-32b-v1:0",
    "minimax":    "minimax.minimax-m2.5",
    "kimi":       "moonshotai.kimi-k2.5",
}

# ── Evolver pool (3 working models; 'none' selects baseline route) ──────────

EVOLVER_MODELS: dict[str, str] = {
    "opus46":   "us.anthropic.claude-opus-4-6-v1",
    "sonnet46": "us.anthropic.claude-sonnet-4-6",
    "qwen235b": "qwen.qwen3-235b-a22b-2507-v1:0",
}

# ── Per-benchmark wrapper registry ──────────────────────────────────────────
# solver_env / evolver_env = env-var names consumed by the evolve wrapper
# output_env = env-var the evolve wrapper reads for its run-dir
# seed_dir = seed workspace (under PROJECT_ROOT/seed_workspaces)
# positional_run_name = True for wrappers whose first positional arg is RUN_NAME

WRAPPERS: dict[str, dict] = {
    "swe": {
        "evolve_wrapper":   PROJECT_ROOT / "examples/swe_examples/run_swe_evolve_split_unified.sh",
        "baseline_wrapper": PROJECT_ROOT / "examples/swe_examples/run_solve_all.sh",
        "solver_env":   "MODEL_ID",
        "evolver_env":  "EVOLVER_MODEL_ID",
        "output_env":   "OUTPUT_DIR",
        "seed_dir":     "swe",
        "positional_run_name": False,
    },
    "mcp": {
        "evolve_wrapper":   PROJECT_ROOT / "examples/mcp_examples/run_adaptive_evolve_split_unified.sh",
        "baseline_wrapper": PROJECT_ROOT / "examples/mcp_examples/run_adaptive_evolve_baseline.sh",
        "solver_env":   "SOLVER_MODEL",
        "evolver_env":  "EVOLVER_MODEL",
        "output_env":   "OUTPUT_DIR",
        "seed_dir":     "mcp",
        "positional_run_name": False,
    },
    "sb": {
        "evolve_wrapper":   PROJECT_ROOT / "examples/skillbench_examples/run_skillbench_evolve_split_unified.sh",
        "baseline_wrapper": PROJECT_ROOT / "examples/skillbench_examples/run_skillbench_solve_all.sh",
        "solver_env":   "MODEL_ID",
        "evolver_env":  "EVOLVER_MODEL_ID",
        "output_env":   "RUN_DIR",
        "seed_dir":     "skillbench-upstream-parity",
        "positional_run_name": False,
    },
}

# ── Route defaults ──────────────────────────────────────────────────────────
# Evolve defaults mirror the unified split wrapper defaults. Baseline defaults
# mirror the dedicated no-evolution wrapper defaults, except SkillBench is
# explicitly forced to USE_SKILLS=false by _build_env_baseline for the intended
# no-evolve baseline.

EVOLVE_DEFAULTS: dict[str, dict] = {
    "swe": {
        "EVOLVE_LIMIT": 50, "EVAL_LIMIT": None, "LIMIT": 200,
        "BATCH_SIZE": 5, "TRAIN_PARALLEL": 5, "TEST_PARALLEL": 16,
        "PARALLEL_BACKEND": "process", "cycle": 1, "MAX_TOKENS": 16384,
    },
    "mcp": {
        "EVOLVE_LIMIT": 50, "EVAL_LIMIT": None, "LIMIT": 200,
        "BATCH_SIZE": 5, "TRAIN_PARALLEL": 5, "TEST_PARALLEL": 5,
        "PARALLEL_BACKEND": "thread", "cycle": 1, "MAX_TOKENS": 16384,
    },
    "sb": {
        "EVOLVE_LIMIT": 20, "EVAL_LIMIT": None, "LIMIT": None,
        "BATCH_SIZE": 5, "TRAIN_PARALLEL": 5, "TEST_PARALLEL": 5,
        "PARALLEL_BACKEND": "thread", "cycle": 1, "MAX_TOKENS": 16384,
    },
}

BASELINE_DEFAULTS: dict[str, dict] = {
    "swe": {"LIMIT": 50,   "BATCH_SIZE": 5,  "MAX_TOKENS": 16384},
    "mcp": {"LIMIT": 500,  "BATCH_SIZE": 30, "MAX_TOKENS": 16384},
    "sb":  {"LIMIT": None, "BATCH_SIZE": 5,  "MAX_TOKENS": 16384},
}

# Backward-compatible import name for older helpers. New code should choose
# EVOLVE_DEFAULTS or BASELINE_DEFAULTS based on route.
BENCHMARK_DEFAULTS = EVOLVE_DEFAULTS

# ── Per-benchmark baseline done-marker + score-kind (for AC-4) ─────────────
# Used to generate BENCHMARK_REPORT.md sidecar in each cell. The sidecar is
# the SINGLE SOURCE OF TRUTH for phase1_single_seed.sh skip-logic and
# check_status.sh score reading — both consult each cell's sidecar rather
# than duplicating per-benchmark contracts inline.
#
# `score_kind` is a simple identifier that scripts/lib/read_score.sh (and
# equivalent Python helpers) dispatch on. Parsers live in those helpers —
# the sidecar only names which parser applies to this cell.

SCORE_KIND_EVOLVE              = "evolve_metrics_json"
SCORE_KIND_SWE_BASELINE        = "swe_baseline_results_json"
SCORE_KIND_MCP_BASELINE        = "mcp_baseline_summary_csv"
SCORE_KIND_SB_BASELINE         = "sb_baseline_summary_txt"

BASELINE_REPORT: dict[str, dict] = {
    "swe": {"done_marker": "results.json",     "score_kind": SCORE_KIND_SWE_BASELINE},
    # MCP done-marker is a separate completion sentinel so half-streamed
    # summary.csv is not treated as "cell complete" by the orchestrator.
    "mcp": {"done_marker": "RUN_COMPLETE.json", "score_kind": SCORE_KIND_MCP_BASELINE},
    "sb":  {"done_marker": "summary.txt",      "score_kind": SCORE_KIND_SB_BASELINE},
}

EVOLVE_REPORT = {
    "done_marker": "results.metrics.json",
    "score_kind":  SCORE_KIND_EVOLVE,
}


# ── Helpers ─────────────────────────────────────────────────────────────────


def _load_env_file() -> None:
    """Mirror scripts/*.sh: auto-source .env if present."""
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _cycle_per_batch(bm: str, cycle: int, cycles_override: int | None) -> int | None:
    """Return SkillBench split cycle-per-batch; other split wrappers do not use CYCLES."""
    if bm != "sb":
        return None
    return cycles_override if cycles_override is not None else cycle


def _write_benchmark_report(
    cell_dir: Path,
    bm: str,
    route: str,
    *,
    region_strategy: str = "single",
    region: str = "us-west-2",
    solver_model_id: str = "",
    evolver_model_id: str = "",
) -> None:
    """Write a sidecar BENCHMARK_REPORT.md naming done-marker + score kind +
    region-routing metadata (additive — older readers ignore extra lines).

    Authoritative for phase1_single_seed.sh skip-logic and check_status.sh
    score reading. Consumers parse `done_marker:` and `score_kind:` keys
    and dispatch to the matching parser in `scripts/lib/read_sidecar.sh`.

    The 4 routing fields below are additive; legacy cells without them are
    treated as `(single, us-west-2)` by phase1's skip-logic for backward
    compatibility with cells produced by the prior round.

    `evolver_model_id` is the empty string for baseline cells (`evolver=none`)
    and never aliases the solver id.
    """
    info = EVOLVE_REPORT if route == "evolve" else BASELINE_REPORT[bm]
    content = (
        f"# BENCHMARK_REPORT\n"
        f"benchmark: {bm}\n"
        f"route: {route}\n"
        f"done_marker: {info['done_marker']}\n"
        f"score_kind: {info['score_kind']}\n"
        f"region_strategy: {region_strategy}\n"
        f"region: {region}\n"
        f"solver_model_id: {solver_model_id}\n"
        f"evolver_model_id: {evolver_model_id}\n"
    )
    (cell_dir / "BENCHMARK_REPORT.md").write_text(content)


def _seed_workspace(bm: str) -> str:
    """Absolute path to the seed workspace for a benchmark."""
    return str(PROJECT_ROOT / "seed_workspaces" / WRAPPERS[bm]["seed_dir"])


def _mcp_env_file() -> str:
    mcp_env_file = os.environ.get("MCP_ENV_FILE")
    if mcp_env_file:
        return mcp_env_file
    default_env_file = PROJECT_ROOT / ".env"
    return str(default_env_file) if default_env_file.exists() else ".env"


def _retry_env() -> dict[str, str]:
    """Keep Bedrock retry defaults explicit while still honoring user overrides."""
    return {
        "BEDROCK_RETRY_MAX_ATTEMPTS": os.environ.get("BEDROCK_RETRY_MAX_ATTEMPTS", "15"),
        "BEDROCK_READ_TIMEOUT_SEC": os.environ.get("BEDROCK_READ_TIMEOUT_SEC", "600"),
        "BEDROCK_CONNECT_TIMEOUT_SEC": os.environ.get("BEDROCK_CONNECT_TIMEOUT_SEC", "30"),
    }


def _build_env_evolve(
    bm: str,
    solver_id: str,
    evolver_id: str,
    *,
    evolve_limit: int,
    eval_limit: int | None,
    batch_size: int,
    train_parallel: int,
    test_parallel: int,
    parallel_backend: str,
    cycle_per_batch: int | None,
    limit: int | None,
    max_tokens: int,
    region: str,
    output_dir: Path,
    seed: int,
) -> dict[str, str]:
    """Build the env-var dict for the evolve wrapper. Does NOT include os.environ.

    `eval_limit=None` means "all remaining" for split wrappers. `limit=None`
    means "no total cap" where a wrapper supports a total cap.
    """
    info = WRAPPERS[bm]
    env: dict[str, str] = {
        info["solver_env"]:   solver_id,
        info["evolver_env"]:  evolver_id,
        info["output_env"]:   str(output_dir),
        "EVOLVE_LIMIT":   str(evolve_limit),
        "EVAL_LIMIT":     str(eval_limit) if eval_limit is not None else "",
        "BATCH_SIZE":     str(batch_size),
        "TRAIN_PARALLEL": str(train_parallel),
        "TEST_PARALLEL":  str(test_parallel),
        "REGION":         region,
        "MAX_TOKENS":     str(max_tokens),
        "SEED_WORKSPACE": _seed_workspace(bm),
        # RUN_ID is only used to label wrapper logs + synthesize default
        # OUTPUT_DIR when one isn't set; we always override OUTPUT_DIR, so
        # the cell_dir name is sufficient.
        "RUN_ID":     output_dir.name,
        **_retry_env(),
    }
    if bm in {"swe", "mcp"}:
        env["PARALLEL_BACKEND"] = parallel_backend
    if limit is not None:
        env["LIMIT"] = str(limit)
    if bm == "sb":
        env.update({
            "CYCLES": str(cycle_per_batch if cycle_per_batch is not None else 1),
            "SPLIT_SEED": str(seed),
            "MODE": "native",
            "USE_SKILLS": "false",
            "FEEDBACK_LEVEL": "tests",
            "TASK_SKILL_MODE": "pre_generate",
            "SKILL_SELECT_LIMIT": "0",
        })
    elif bm == "swe":
        env.update({
            "FEEDBACK": "none",
            "SOLVER_PROPOSES": "false",
            "VERIFICATION_FOCUS": "false",
            "EFFICIENCY_PROMPT": "false",
            "MAX_STEPS": "0",
            "WINDOW_SIZE": "40",
            "DATASET": "MariusHobbhahn/swe-bench-verified-mini",
        })
    elif bm == "mcp":
        env.update({
            # Default judge follows the shell wrapper (sonnet-4-6); override
            # with MCP_JUDGE_MODEL env if a different judge is needed.
            "JUDGE_MODEL": os.environ.get(
                "MCP_JUDGE_MODEL", "us.anthropic.claude-sonnet-4-6"
            ),
            "DATASET": "ScaleAI/MCP-Atlas",
            "DOCKER_IMAGE": "ghcr.io/scaleapi/mcp-atlas:latest",
            "ENV_FILE": _mcp_env_file(),
            "MCP_CONTAINER_NAME": f"mcp-atlas-{output_dir.name}",
        })
    return env


def _build_env_baseline(bm: str, solver_id: str, region: str, max_tokens: int,
                        output_dir: Path, seed: int, batch_size: int,
                        limit: int | None, cell_dir: Path) -> dict[str, str]:
    """Build the env-var dict for the baseline wrapper. Per-benchmark surface.

    `limit=None` means "no limit" - emit `LIMIT=""` so the wrapper
    skips the `--limit` flag and the runner loads the entire dataset.
    """
    if bm == "mcp":
        return {
            "SOLVER_MODEL":   solver_id,
            "JUDGE_MODEL":    os.environ.get(
                "MCP_JUDGE_MODEL", "us.anthropic.claude-sonnet-4-6"
            ),
            "REGION":         region,
            "MAX_TOKENS":     str(max_tokens),
            "LIMIT":          str(limit) if limit is not None else "",
            "BATCH_SIZE":     str(batch_size),
            "WORKERS":        os.environ.get("MCP_WORKERS", "5"),
            "SEED_WORKSPACE": _seed_workspace("mcp"),
            "WORK_DIR":       str(output_dir / "workspace"),
            "OUTPUT_DIR":     str(output_dir),
            "DOCKER_IMAGE":   "ghcr.io/scaleapi/mcp-atlas:latest",
            "ENV_FILE":       _mcp_env_file(),
            "MCP_CONTAINER_NAME": f"mcp-atlas-{output_dir.name}",
            "RUN_ID":         output_dir.name,
            **_retry_env(),
        }

    # swe / sb share the same {MODEL_ID, REGION, MAX_TOKENS} surface.
    env: dict[str, str] = {
        "MODEL_ID":   solver_id,
        "REGION":     region,
        "MAX_TOKENS": str(max_tokens),
        "RUN_ID":     output_dir.name,
        **_retry_env(),
    }
    limit_str = str(limit) if limit is not None else ""
    if bm == "swe":
        env.update({"OUTPUT_DIR": str(output_dir), "LIMIT": limit_str})
    elif bm == "sb":
        env.update({
            "SPLIT_SEED":     str(seed),
            "RUN_DIR":        str(output_dir),
            "SEED_WORKSPACE": _seed_workspace("sb"),
            "USE_SKILLS":     "false",
        })
    return env


def _build_cmd(
    wrapper_path: Path,
    bm: str,
    route: str,
    output_dir: Path,
    wrapper_env: dict[str, str],
) -> list[str]:
    """Build the argv list for invoking the wrapper.

    The artifact wrappers are env-var driven with no positional args.
    """
    cmd = ["bash", str(wrapper_path)]
    return cmd


# ── Main ────────────────────────────────────────────────────────────────────


def main() -> int:
    _load_env_file()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    p = argparse.ArgumentParser(
        description="EvolverBench Exp1 - one (solver, evolver, benchmark, seed) run "
                    "against the harness-disentangling shell wrappers.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # --solver/--evolver: NO argparse `choices=` — short-name validation is
    # owned by the resolver (`_region_picker.resolve()`) so library callers
    # and the CLI both surface the same fail-fast error
    # ("Unknown model short-name: <X>; expected one of [...]"). The help
    # text below lists the canonical short-names for discoverability.
    p.add_argument("--solver", required=True,
                   metavar="{" + ",".join(sorted(SOLVER_MODELS)) + "}",
                   help="Solver short-name (axis under study). Validated by "
                        "the region resolver against model_region_availability.json.")
    p.add_argument("--evolver", required=True,
                   metavar="{" + ",".join(sorted(EVOLVER_MODELS) + ["none"]) + "}",
                   help="Evolver short-name. 'none' = baseline route "
                        "(dedicated wrapper). Validated by the region resolver.")
    p.add_argument("--benchmark", required=True, choices=sorted(WRAPPERS.keys()),
                   help="Benchmark: swe / mcp / sb.")
    p.add_argument("--seed", type=int, default=42,
                   help="Single-seed default (42). These adapters do not honour --seed "
                        "for task-order reshuffle; seed drives run-namespace + sb "
                        "split-seed only.")

    # --- User-facing knobs ---
    p.add_argument("--evolve-limit", type=int, default=None,
                   help="Split evolve train/evolve task count (default from EVOLVE_DEFAULTS).")
    p.add_argument("--eval-limit", type=int, default=None,
                   help="Split evolve test task count after train slice. Omit for all remaining.")
    p.add_argument("--train-parallel", type=int, default=None,
                   help="Split evolve Phase 1 max parallel solve workers.")
    p.add_argument("--test-parallel", type=int, default=None,
                   help="Split evolve Phase 2 parallel solve workers.")
    p.add_argument("--parallel-backend", choices=("thread", "process"), default=None,
                   help="Split evolve parallel backend where the wrapper supports it.")
    p.add_argument("--passes", type=int, default=None,
                   help="Deprecated compatibility knob. Ignored by unified split wrappers.")
    p.add_argument("--batch-size", type=int, default=None,
                   help="Train/evolve batch size (or baseline wrapper batch size where supported).")
    p.add_argument("--limit", type=int, default=None,
                   help="Total task cap where supported.")
    p.add_argument("--cycle", type=int, default=None,
                   help="SkillBench split cycle-per-batch. Ignored for swe/mcp.")
    p.add_argument("--cycles", type=int, default=None,
                   help="Alias/override for SkillBench split cycle-per-batch. Ignored for swe/mcp.")
    p.add_argument("--max-tokens", type=int, default=None,
                   help="Max tokens (default from route-specific defaults).")
    p.add_argument("--region", default=os.environ.get("AWS_DEFAULT_REGION", "us-west-2"),
                   help="Explicit region for --region-strategy single (default: us-west-2).")
    p.add_argument("--region-strategy", choices=("single", "hash"),
                   default=os.environ.get("REGION_STRATEGY", "single"),
                   help="single = honour --region exactly (current behaviour); "
                        "hash = deterministic per-cell pick from the verified "
                        "solver/evolver intersection (or solver-only for --evolver none). "
                        "Default: single. Reads REGION_STRATEGY env var if set.")
    p.add_argument("--output-root", default=str(EXPERIMENT_ROOT / "results" / "exp1_v3"),
                   help="Root directory for cell outputs.")
    p.add_argument("--dry-run", action="store_true",
                   help="1 task, 1 cycle — smoke-test the wrapper + env plumbing.")
    p.add_argument("--resolve-only", action="store_true",
                   help="Print resolved (region, solver_model_id, evolver_model_id) "
                        "and exit without launching the wrapper. Used by phase scripts "
                        "for strategy-aware skip-logic.")
    p.add_argument("--json", action="store_true",
                   help="With --resolve-only: emit JSON instead of plain text.")

    args = p.parse_args()

    bm = args.benchmark
    info = WRAPPERS[bm]
    route = "baseline" if args.evolver == "none" else "evolve"
    defaults = EVOLVE_DEFAULTS[bm] if route == "evolve" else BASELINE_DEFAULTS[bm]
    wrapper_key = "baseline_wrapper" if route == "baseline" else "evolve_wrapper"
    wrapper_path: Path = info[wrapper_key]

    # --- Resolve region + region-aware model_ids via the single-source-of-truth
    #     resolver (reads model_region_availability.json). Failures here are
    #     fail-fast: missing/malformed JSON, unknown short-name, FAIL region etc.
    #     For baseline route the resolver returns "" for evolver_model_id —
    #     we never alias the solver id into the evolver slot.
    try:
        region, solver_id, evolver_id = _resolve_region(
            args.region_strategy,
            args.solver,
            args.evolver,
            bm,
            args.seed,
            args.region if args.region_strategy == "single" else None,
        )
    except RegionResolveError as e:
        logger.error("Region resolution failed: %s", e)
        return 2

    # --- Resolve-only mode: emit the resolution and exit. Used by phase scripts
    #     to learn what region / model_ids a cell will use without launching.
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
            for k, v in payload.items():
                print(f"{k}: {v}")
        return 0

    if not wrapper_path.exists():
        logger.error("Wrapper missing: %s", wrapper_path)
        logger.error("Set AEVOLVE_REPO_DIR if the repository lives elsewhere (current: %s)",
                     PROJECT_ROOT)
        return 2

    # Resolve knobs, applying dry-run overrides last.
    cycle  = args.cycle  if args.cycle  is not None else defaults.get("cycle", 1)
    batch_size = args.batch_size if args.batch_size is not None else defaults["BATCH_SIZE"]
    limit      = args.limit      if args.limit      is not None else defaults["LIMIT"]
    max_tokens = args.max_tokens if args.max_tokens is not None else defaults["MAX_TOKENS"]
    evolve_limit = args.evolve_limit if args.evolve_limit is not None else defaults.get("EVOLVE_LIMIT")
    eval_limit = args.eval_limit if args.eval_limit is not None else defaults.get("EVAL_LIMIT")
    train_parallel = args.train_parallel if args.train_parallel is not None else defaults.get("TRAIN_PARALLEL", 1)
    test_parallel = args.test_parallel if args.test_parallel is not None else defaults.get("TEST_PARALLEL", 1)
    parallel_backend = args.parallel_backend or defaults.get("PARALLEL_BACKEND", "thread")

    if args.dry_run:
        cycle = 1
        batch_size = 1
        max_tokens = min(max_tokens, 4096)
        if route == "evolve":
            evolve_limit = 1
            eval_limit = 1
            if bm in {"swe", "mcp", "sb"}:
                limit = 2
            train_parallel = 1
            test_parallel = 1
            logger.info("DRY RUN: evolve_limit=1 eval_limit=1 batch_size=1 parallel=1")
        else:
            limit = 1
            logger.info("DRY RUN: baseline limit=1 batch_size=1")

    # Cell dir.
    run_name = f"{args.solver}_x_{args.evolver}_{bm}_s{args.seed}"
    if args.dry_run:
        run_name += "_dryrun"
    cell_dir = Path(args.output_root) / run_name
    cell_dir.mkdir(parents=True, exist_ok=True)

    # Build env. Use the resolver-output region / model_ids — NOT the raw
    # args.region / SOLVER_MODELS / EVOLVER_MODELS dicts — so cells routed
    # to non-default regions get the correct (Anthropic-prefixed) model ids.
    if route == "evolve":
        cycle_pb = _cycle_per_batch(bm, cycle, args.cycles)
        wrapper_env = _build_env_evolve(
            bm=bm,
            solver_id=solver_id,
            evolver_id=evolver_id,
            evolve_limit=evolve_limit,
            eval_limit=eval_limit,
            batch_size=batch_size,
            train_parallel=train_parallel,
            test_parallel=test_parallel,
            parallel_backend=parallel_backend,
            cycle_per_batch=cycle_pb,
            limit=limit,
            max_tokens=max_tokens,
            region=region,
            output_dir=cell_dir,
            seed=args.seed,
        )
    else:
        # Baseline: evolver_id from the resolver is the empty string. Pass
        # the solver id through the existing builder; the wrapper does not
        # consume an evolver model id on the baseline route.
        wrapper_env = _build_env_baseline(
            bm=bm, solver_id=solver_id, region=region, max_tokens=max_tokens,
            output_dir=cell_dir, seed=args.seed, batch_size=batch_size,
            limit=limit, cell_dir=cell_dir,
        )

    # Merge with parent env so PATH / BEDROCK_API_KEY / AWS_* / MCP_* survive.
    merged_env = {**os.environ, **wrapper_env}

    cmd = _build_cmd(wrapper_path, bm, route, cell_dir, wrapper_env)

    _write_benchmark_report(
        cell_dir, bm, route,
        region_strategy=args.region_strategy,
        region=region,
        solver_model_id=solver_id,
        evolver_model_id=evolver_id,
    )

    logger.info("=== EvolverBench Exp1 ===")
    logger.info("  solver:     %s (%s)", args.solver, solver_id)
    logger.info("  evolver:    %s (%s)",
                args.evolver,
                evolver_id if evolver_id else "<none — baseline route>")
    logger.info("  benchmark:  %s", bm)
    logger.info("  route:      %s", route)
    logger.info("  wrapper:    %s", wrapper_path)
    logger.info("  seed:       %d", args.seed)
    logger.info("  region:     %s  (strategy=%s)", region, args.region_strategy)
    if route == "evolve":
        logger.info(
            "  split:      evolve_limit=%s eval_limit=%s batch_size=%s "
            "train_parallel=%s test_parallel=%s limit=%s",
            wrapper_env.get("EVOLVE_LIMIT"),
            wrapper_env.get("EVAL_LIMIT") or "<all remaining>",
            batch_size,
            wrapper_env.get("TRAIN_PARALLEL"),
            wrapper_env.get("TEST_PARALLEL"),
            wrapper_env.get("LIMIT", "<none>"),
        )
        if bm == "sb":
            logger.info("  sb cycles:  cycle_per_batch=%s", wrapper_env.get("CYCLES"))
        if args.passes is not None and bm != "sb":
            logger.warning("--passes=%s is ignored by unified split wrappers", args.passes)
        if args.cycles is not None and bm != "sb":
            logger.warning("--cycles=%s is ignored for %s unified split", args.cycles, bm)
    else:
        logger.info("  baseline knobs: batch_size=%s limit=%s (evolve-only knobs ignored)",
                    batch_size, limit)
    logger.info("  cell_dir:   %s", cell_dir)
    logger.info("  env (delta): %s", {k: v for k, v in wrapper_env.items()
                                      if k not in os.environ})

    started = time.time()
    rc = subprocess.call(cmd, env=merged_env, cwd=str(PROJECT_ROOT))
    elapsed = time.time() - started

    logger.info("=== Done (rc=%d, %.1fs) — %s", rc, elapsed, cell_dir)
    if rc != 0:
        logger.error("Wrapper exited non-zero. See %s/evolve.log for details.", cell_dir)
    return rc


if __name__ == "__main__":
    sys.exit(main())
