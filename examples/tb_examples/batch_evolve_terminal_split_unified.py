"""Terminal-Bench 2.0 — TRAIN/TEST SPLIT runner (unified engine).

Two-phase split runner, mirroring legacy ``run_evolution.sh`` but on the
unified engine path:

  Phase 1 — TRAIN: ``EvolutionLoop + UnifiedEngine`` walks the FIRST
            ``--evolve-limit`` tasks in the dataset, ``--batch-size``
            tasks per train batch.
            The workspace is mutated by the ``drafts`` recipe branch
            (matches ``AdaptiveSkillEngine.step()``).

  Phase 2 — TEST:  with the workspace already evolved, the bench
            cursor continues into the remaining tasks. ``agent.solve()``
            + ``bench.evaluate()`` are called directly — NO engine,
            NO mutation. ``--eval-limit`` caps how many test tasks
            (default: all remaining).

Re-uses the SAME ``TerminalAgent`` and workspace dir across both
phases, so the skills produced in Phase 1 are visible to the solver
during Phase 2.

Usage:
    python batch_evolve_terminal_split_unified.py RUN_NAME \\
        --evolve-limit 20 --eval-limit 30 --batch-size 5 --max-skills 6
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
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
        description="Terminal-Bench 2.0 — train/test split (UnifiedEngine + EvolutionLoop)"
    )
    p.add_argument("run_name", help="Name/tag for this run (becomes log dir)")
    # ── Train/test split knobs ─────────────────────────────────
    # Phase 1 walks the FIRST evolve-limit tasks in train batches of
    # batch-size, courtesy of the bench's stateful cursor in
    # Terminal2Benchmark.
    # Phase 2 then continues the cursor into the remaining tasks
    # for evaluate-only (no engine).
    p.add_argument("--evolve-limit", type=int, default=20, dest="evolve_limit",
                   help="Phase 1 (train): number of tasks to evolve on. "
                        "Must be divisible by --batch-size. "
                        "Train batches = evolve-limit / batch-size.")
    p.add_argument("--eval-limit", type=int, default=None, dest="eval_limit",
                   help="Phase 2 (test): cap on how many remaining tasks "
                        "to evaluate. Default: all remaining tasks in "
                        "the dataset after the train slice.")
    p.add_argument("--batch-size", type=int, default=5,
                   help="Tasks per Phase 1 train batch (must divide --evolve-limit).")
    p.add_argument("--train-parallel", type=int, default=6,
                   dest="train_parallel",
                   help="Phase 1 (train): max parallel workers within each "
                        "train batch. Effective parallelism is "
                        "min(train_parallel, batch_size). Default 6 "
                        "(matches legacy TB wrapper). Evolve is always serial.")
    p.add_argument("--test-parallel", type=int, default=5,
                   dest="test_parallel",
                   help="Phase 2 (test): number of test tasks to evaluate "
                        "in parallel. Default 5. Test has no evolve so this "
                        "is independent of --train-parallel.")
    p.add_argument("--parallel-backend", default="thread",
                   choices=["thread", "process", "benchmark"],
                   help="Phase 1 in-batch parallel backend (default thread for TB).")
    # Skill-budget cap (mirrors the legacy `batch_evolve_terminal.py
    # --max-skills`). Threaded through EvolveConfig.extra["max_skills"]
    # so SkillCurator's prompt builder (`agent_evolve.algorithms.skillforge
    # .prompts._build_*_instructions`) reads it and emits the
    # "SKILL BUDGET REACHED" guard text.
    p.add_argument("--max-skills", type=int, default=6,
                   help="Maximum total skills the evolver may keep in the "
                        "workspace (default 6; matches the split wrapper "
                        "default and legacy run_evolution.sh).")
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

    # ── Validate split parameters early (before any I/O) ──
    if args.batch_size <= 0:
        p.error(f"--batch-size must be > 0 (got {args.batch_size})")
    if args.evolve_limit < 0:
        p.error(f"--evolve-limit must be >= 0 (got {args.evolve_limit})")
    if args.eval_limit is not None and args.eval_limit < 0:
        p.error(f"--eval-limit must be >= 0 or omitted (got {args.eval_limit})")
    if args.train_parallel <= 0:
        p.error(f"--train-parallel must be > 0 (got {args.train_parallel})")
    if args.test_parallel <= 0:
        p.error(f"--test-parallel must be > 0 (got {args.test_parallel})")

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    log_dir = Path(args.log_dir) if args.log_dir else (
        REPO_ROOT / "logs" / f"unified_tb_{args.run_name}"
    )
    log_dir.mkdir(parents=True, exist_ok=True)

    # Benchmark. shuffle=False makes task order deterministic (dataset
    # order, first `limit` tasks). The TB baseline runner also defaults
    # to shuffle=False so evolve and baseline cells see the SAME tasks.
    bench_kwargs = {"shuffle": False}
    if args.challenges_dir:
        bench_kwargs["challenges_dir"] = args.challenges_dir
    bench = Terminal2Benchmark(**bench_kwargs)
    logger.info("Capability: %s", bench.feedback_capability)

    # ── Build authoritative ordered task list (legacy run_evolution.sh
    # semantics: full task list, first N for train, rest for test) ──
    # Terminal2Benchmark / SweVerifiedMiniBenchmark / McpAtlasBenchmark all
    # default to holdout_ratio=0.2 (with min n_holdout=1), so split="train"
    # already drops the first 20% of rows — NOT what legacy
    # run_evolution.sh does. We bypass that by reading from split="test"
    # (the unsplit full row list, see _do_split: ``self._cache["test"] =
    # rows``) and then redirecting "train" to the same list so
    # EvolutionLoop's get_tasks(split="train") sees the full ordered set.
    _ = bench.get_tasks(split="test", limit=10**9)  # triggers _do_split
    bench._cache["train"] = bench._cache["test"]    # redirect "train" → full list
    bench._cursor = 0
    all_tasks = bench.get_tasks(split="train", limit=10**9)
    bench._cursor = 0  # reset for Phase 1 EvolutionLoop walk

    # ── Validate split parameters ──
    if args.evolve_limit > len(all_tasks):
        raise SystemExit(
            f"--evolve-limit ({args.evolve_limit}) > available tasks "
            f"({len(all_tasks)})"
        )
    if args.evolve_limit % args.batch_size != 0:
        raise SystemExit(
            f"--evolve-limit ({args.evolve_limit}) must be divisible by "
            f"--batch-size ({args.batch_size}) so Phase 1's cursor walks "
            f"exactly evolve_limit tasks (no over-walk)."
        )

    # ── Slice train / test (explicit, NOT cursor-based) ──
    train_tasks = all_tasks[:args.evolve_limit]
    if args.eval_limit is None:
        test_tasks = all_tasks[args.evolve_limit:]
    else:
        test_tasks = all_tasks[args.evolve_limit:args.evolve_limit + args.eval_limit]
    logger.info(
        "Task split: %d train (idx 0..%d) + %d test (idx %d..%d) "
        "from %d total",
        len(train_tasks), args.evolve_limit - 1,
        len(test_tasks), args.evolve_limit, args.evolve_limit + len(test_tasks) - 1,
        len(all_tasks),
    )

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

    # ── Phase 1: TRAIN (evolve on first evolve-limit tasks) ──
    # Exact integer division; we asserted divisibility above. EvolutionLoop's
    # cursor starts at 0 so each train batch is a contiguous batch_size slice.
    phase1_batches = args.evolve_limit // args.batch_size

    config = EvolveConfig(
        batch_size=args.batch_size,
        max_cycles=phase1_batches,
        parallel_workers=max(1, args.train_parallel),
        parallel_backend=args.parallel_backend,
        evolver_model=evolver_model_id,
        trajectory_only=True,
        evolve_prompts=False,
        evolve_skills=True,
        evolve_memory=False,
        evolve_tools=False,
        # Disable EvolutionLoop's score-convergence early-stop: in a
        # train/test split we need ALL phase1_batches to fire (otherwise we
        # evolve on fewer tasks but still exclude the full evolve_limit
        # range from test, leaving a silent gap). egl_window > max_cycles
        # makes _is_score_converged's `len(scores) < window+1` guard
        # always True. The engine's NoVerify also won't request
        # step_result.stop, so train batches run end-to-end.
        egl_window=phase1_batches + 1,
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
        "[Phase 1 TRAIN] %d train batches × batch_size=%d → %d evolve tasks "
        "(solver=%s, solver_model=%s, evolver=%s)",
        phase1_batches, args.batch_size, args.evolve_limit,
        args.solver, args.model_id, evolver_model_id,
    )
    phase1_result = loop.run(cycles=phase1_batches)

    # Hard assert: Phase 1 must have completed all phase1_batches. If it
    # stopped early (engine-driven stop or convergence), train_tasks were
    # only partially evolved on but test_tasks still excludes the full
    # evolve_limit range — a silent miscount.
    if phase1_result.cycles_completed != phase1_batches:
        raise SystemExit(
            f"Phase 1 stopped early: completed "
            f"{phase1_result.cycles_completed}/{phase1_batches} train batches "
            f"(converged={phase1_result.converged}). With "
            f"egl_window={phase1_batches + 1} and NoVerify this should not "
            f"happen — check that the engine is not requesting "
            f"step_result.stop, and that EvolutionLoop wasn't recently "
            f"changed to add another exit path."
        )

    # ── Train per-task rows (legacy run_evolution.sh parity) ──
    # Read EvolutionLoop's batch_*.jsonl observations to emit one row per
    # train task, matching what legacy adaptive_evolve_all.py /
    # batch_evolve_terminal.py write to results.jsonl. Cycle scores are
    # still preserved in results.metrics.json -> phase1_train.score_history.
    train_path = log_dir / "results.train.jsonl"
    train_task_rows: list[dict] = []
    obs_dir = ws_dir / "evolution" / "observations"
    if obs_dir.is_dir():
        for batch_file in sorted(obs_dir.glob("batch_*.jsonl")):
            try:
                cycle = int(batch_file.stem.split("_")[1])
            except ValueError:
                cycle = 0
            with open(batch_file) as bf:
                for line in bf:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("_record_type") == "step_metadata":
                        continue
                    train_task_rows.append({
                        "phase": "train",
                        "cycle": cycle,
                        "task_id": rec.get("task_id", ""),
                        "score": float(rec.get("score", 0.0) or 0.0),
                        "success": bool(rec.get("success", False)),
                        "passed": bool(rec.get("success", False)),
                    })
    with open(train_path, "w") as f:
        for row in train_task_rows:
            f.write(json.dumps(row) + "\n")

    # ── Phase 2: TEST (eval on the explicit test_tasks list, no engine) ──
    # Walk the pre-sliced test_tasks directly — does NOT depend on the
    # bench cursor (which would otherwise wrap and re-eval train tasks).
    test_results: list[dict] = []

    logger.info(
        "[Phase 2 TEST] evaluate %d test tasks (no engine, evolved workspace, parallel=%d)",
        len(test_tasks), args.test_parallel,
    )

    def _eval_test_task(item):
        i, task = item
        rec: dict = {"task_id": task.id}
        try:
            traj = agent.solve(task)
            fb = bench.evaluate(task, traj)
            rec["score"] = float(fb.score)
            rec["success"] = bool(fb.success)
            rec["passed"] = bool(fb.success)
        except Exception as e:  # noqa: BLE001
            rec["score"] = 0.0
            rec["success"] = False
            rec["passed"] = False
            rec["error"] = str(e)[:300]
        return i, rec

    test_path = log_dir / "results.test.jsonl"
    indexed_test_tasks = list(enumerate(test_tasks, 1))
    if args.test_parallel > 1 and len(indexed_test_tasks) > 1:
        workers = min(args.test_parallel, len(indexed_test_tasks))
        logger.info("[Phase 2 TEST] using %d parallel worker(s)", workers)
        by_index: dict[int, dict] = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_eval_test_task, item): item[0] for item in indexed_test_tasks}
            for fut in as_completed(futures):
                i, rec = fut.result()
                by_index[i] = rec
                logger.info(
                    "  [test %d/%d] %s: passed=%s score=%.3f",
                    i, len(test_tasks), rec["task_id"], rec["success"], rec["score"],
                )
        test_results = [by_index[i] for i, _task in indexed_test_tasks if i in by_index]
    else:
        for item in indexed_test_tasks:
            i, rec = _eval_test_task(item)
            logger.info(
                "  [test %d/%d] %s: passed=%s score=%.3f",
                i, len(test_tasks), rec["task_id"], rec["success"], rec["score"],
            )
            test_results.append(rec)

    with open(test_path, "w") as test_fp:
        for rec in test_results:
            test_fp.write(json.dumps(rec) + "\n")

    # ── Aggregate metrics ──
    n_test = len(test_results)
    test_pass_rate = (
        sum(1 for r in test_results if r.get("passed")) / n_test if n_test else 0.0
    )
    test_mean_score = (
        sum(r.get("score", 0.0) for r in test_results) / n_test if n_test else 0.0
    )

    metrics = {
        "phase1_train": {
            "evolve_limit": args.evolve_limit,
            "batch_size": args.batch_size,
            "train_batches_completed": phase1_result.cycles_completed,
            "cycles_completed": phase1_result.cycles_completed,
            "final_score": phase1_result.final_score,
            "score_history": list(phase1_result.score_history),
            "converged": phase1_result.converged,
        },
        "phase2_test": {
            "eval_limit": args.eval_limit,
            "n_evaluated": n_test,
            "pass_rate": test_pass_rate,
            "mean_score": test_mean_score,
        },
        "final_score": test_pass_rate,  # train/test split: TEST pass rate is the headline number
        "engine": "UnifiedEngine",
        "legacy_settings": {
            "train_parallel": args.train_parallel,
            "test_parallel": args.test_parallel,
            "parallel_backend": args.parallel_backend,
            "solver": args.solver,
            "trajectory_only": True,
            "skills_only": True,
            "protect_skills": True,
            "max_skills": args.max_skills,
        },
        "recipe": "drafts (PassFailReader+DraftReader+TrajectoryCompressor | LLMBashEvolve)",
        "workspace": str(ws_dir),
    }
    (log_dir / "results.metrics.json").write_text(json.dumps(metrics, indent=2))

    # Back-compat path expected by EvolverBench / legacy parsers:
    # results.jsonl holds task-level rows for BOTH phases (mirroring
    # legacy run_evolution.sh shape). Each row carries `phase` so
    # downstream can split. Train batch scores are kept in metrics.
    combined_path = log_dir / "results.jsonl"
    with open(combined_path, "w") as f:
        for row in train_task_rows:
            f.write(json.dumps(row) + "\n")
        for rec in test_results:
            f.write(json.dumps({"phase": "test", **rec}) + "\n")

    logger.info(
        "Done. train_batches=%d  test_pass=%d/%d (%.1f%%)  test_mean_score=%.3f",
        phase1_result.cycles_completed,
        sum(1 for r in test_results if r.get("passed")), n_test,
        100.0 * test_pass_rate, test_mean_score,
    )
    logger.info("  Train: %s", train_path)
    logger.info("  Test:  %s", test_path)
    logger.info("  Combined: %s", combined_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
