#!/usr/bin/env python3
"""SkillBench legacy train/test split runner.

Two-phase split that mirrors SWE's evolve_sequential_split.py shape but uses
the legacy SkillBench ``AEvolveEngine`` (from agent_evolve.algorithms.skillforge,
NOT UnifiedEngine):

  Phase 1 (TRAIN): first ``--evolve-limit`` tasks, run in batches of
                   ``--batch-size`` with ``AEvolveEngine`` evolving the
                   workspace after each batch (one evolve call per batch).
  Phase 2 (TEST):  next ``--eval-limit`` tasks (or all remaining when unset),
                   solved ONCE each with the evolved workspace, no further
                   evolution and no per-task retry.

This file references ``skillbench_evolve_in_situ_cycle.py`` for setup
patterns (workspace init, agent + benchmark + AEvolveEngine instantiation,
SBTask -> Task conversion, parallel batch solve) but drops the in-situ
"retry-until-pass" semantics in favor of an explicit train/test split.

Scope:
  - native mode only (harbor mode not wired here; rerun with the harbor
    in-situ-cycle script if needed)
  - no distillation, no task-skill pre-generation, no success-mode promotion
  - default skill-select-limit=0 (inject all skills)

Outputs (compatible with EvolverBench's pass_ratio_metrics_json parser):
  - results.train.jsonl
  - results.test.jsonl
  - results.jsonl              (combined)
  - results.metrics.json       (final pass_ratio + per-phase summary)
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Strands SDK uses recursive event_loop dispatch + recursive JSON telemetry
# serialization; Python's default limit (1000) is too shallow for long tool chains.
sys.setrecursionlimit(10000)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agent_evolve.agents.skillbench import SkillBenchAgent
from agent_evolve.agents.skillbench.dataset import load_all_tasks
from agent_evolve.agents.skillbench.repo import resolve_skillbench_paths
from agent_evolve.benchmarks.skill_bench import SkillBenchBenchmark
from agent_evolve.config import EvolveConfig
from agent_evolve.algorithms.skillforge import AEvolveEngine
from agent_evolve.engine.observer import Observer
from agent_evolve.types import Observation, Task

logger = logging.getLogger("skillbench_evolve_split")


def _to_task(sb_task) -> Task:
    """Convert SBTask -> generic Task (mirrors in-situ-cycle helper)."""
    return Task(
        id=sb_task.name,
        input=sb_task.prompt,
        metadata={
            "task_name": sb_task.name,
            "task_dir": sb_task.metadata.get("task_dir", ""),
            "dockerfile_dir": sb_task.dockerfile_dir,
            "test_sh_path": sb_task.test_sh_path,
            "test_py_path": sb_task.test_py_path,
            "category": sb_task.metadata.get("category", "unknown"),
            "difficulty": sb_task.metadata.get("difficulty", "unknown"),
            "agent_timeout_sec": sb_task.metadata.get("agent_timeout_sec", 900),
            "verifier_timeout_sec": sb_task.metadata.get("verifier_timeout_sec", 900),
            "build_timeout_sec": sb_task.metadata.get("build_timeout_sec", 600),
            "cpus": sb_task.metadata.get("cpus", 1),
            "memory": sb_task.metadata.get("memory", "4g"),
            "backend": "native",
            "comparison_key": sb_task.name,
        },
    )


def _record(task: Task, success: bool, score: float, elapsed: float,
            phase: str, evo_cycle: int, error: str = "") -> dict:
    return {
        "instance_id": task.id,
        "phase": phase,
        "success": bool(success),
        "score": float(score),
        "elapsed": round(elapsed, 1),
        "evo_cycle": evo_cycle,
        "category": task.metadata.get("category", "unknown"),
        "difficulty": task.metadata.get("difficulty", "unknown"),
        "error": error[:300] if error else None,
    }


def _solve_one(*, sb_task, task, agent, bm, log) -> dict:
    """Solve one task once. Returns dict with feedback + trajectory + error."""
    t0 = time.time()
    try:
        trajectory = agent.solve(task)
        feedback = bm.evaluate(task, trajectory)
        elapsed = time.time() - t0
        log.info("  %s %s | score=%.2f | %.1fs",
                 "PASS" if feedback.success else "FAIL",
                 task.id, feedback.score, elapsed)
        return {"sb_task": sb_task, "task": task, "trajectory": trajectory,
                "feedback": feedback, "elapsed": elapsed, "error": None}
    except Exception as e:
        elapsed = time.time() - t0
        log.error("  ERROR %s: %s", task.id, e)
        return {"sb_task": sb_task, "task": task, "trajectory": None,
                "feedback": None, "elapsed": elapsed, "error": str(e)}


def _solve_batch_parallel(*, batch, agent, bm, max_workers, log) -> list[dict]:
    """Solve a batch in parallel via ThreadPoolExecutor."""
    results = []
    if max_workers <= 1:
        for sb_task in batch:
            task = _to_task(sb_task)
            results.append(_solve_one(sb_task=sb_task, task=task,
                                      agent=agent, bm=bm, log=log))
        return results

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_solve_one, sb_task=sb, task=_to_task(sb),
                        agent=agent, bm=bm, log=log): sb
            for sb in batch
        }
        for fut in as_completed(futures):
            results.append(fut.result())
    return results


def _evolve_after_batch(*, evolver, agent, observer, batch_results,
                        evo_counter, feedback_level, log) -> None:
    """Build observations from batch and call AEvolveEngine.evolve()."""
    observations: list[Observation] = []
    evo_logs: list[dict] = []
    for r in batch_results:
        if r.get("trajectory") is None or r.get("feedback") is None:
            continue
        observations.append(Observation(
            task=r["task"], trajectory=r["trajectory"], feedback=r["feedback"],
        ))
        # Build evolver-facing log entry, gating success/score by feedback_level
        # to prevent leakage when the user requests masked or none feedback.
        entry = {
            "task_id": r["task"].id,
            "task_input": r["task"].input,
            "agent_output": r["trajectory"].output,
            "steps": r["trajectory"].steps,
        }
        if feedback_level in ("score", "tests", "masked", "full"):
            entry["score"] = r["feedback"].score
        if feedback_level in ("tests", "masked", "full"):
            entry["success"] = r["feedback"].success
        # In-situ-cycle.py always sets evolver_feedback_detail via a helper; keep
        # parity here so the default feedback_level="tests" still surfaces detail
        # to the evolver. The full raw detail is fine for "tests" and above.
        if feedback_level in ("tests", "masked", "full"):
            entry["evolver_feedback_detail"] = r["feedback"].detail
        evo_logs.append(entry)

    if not observations:
        log.info("  (no observations to evolve from)")
        return

    observer.collect(observations)
    log.info("=== EVOLVE (cycle %d, %d obs) ===", evo_counter, len(observations))
    t0 = time.time()
    try:
        result = evolver.evolve(workspace=agent.workspace,
                                observation_logs=evo_logs,
                                evo_number=evo_counter)
        elapsed = time.time() - t0
        log.info("Evolved in %.1fs (skills: %d -> %d, +%d new)",
                 elapsed, result.get("skills_before", 0),
                 result.get("skills_after", 0), result.get("new_skills", 0))
        agent.reload_from_fs()
    except Exception as e:
        log.error("Evolution failed: %s", e)
        agent.reload_from_fs()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    # Split / batch
    p.add_argument("--evolve-limit", type=int, default=20,
                   help="Tasks for Phase 1 (train). Default 20.")
    p.add_argument("--eval-limit", type=int, default=None,
                   help="Tasks for Phase 2 (test). Default: all remaining up to --limit.")
    p.add_argument("--limit", type=int, default=None,
                   help="Total task cap (train + test). Default: all tasks.")
    p.add_argument("--batch-size", type=int, default=5,
                   help="Train batch size (Phase 1). Default 5.")
    p.add_argument("--train-parallel", type=int, default=None,
                   help="Phase 1 parallel workers (effective = min(this, batch-size)). "
                        "Defaults to --max-workers if --train-parallel is unset.")
    p.add_argument("--test-parallel", type=int, default=None,
                   help="Phase 2 parallel workers (one big run, no batches). "
                        "Defaults to --max-workers if --test-parallel is unset.")
    p.add_argument("--max-workers", type=int, default=1,
                   help="Legacy alias: backstop for both --train-parallel and "
                        "--test-parallel when those are unset. Default 1.")

    # SkillBench
    p.add_argument("--use-skills", default="false",
                   help="Use skills task variant (true|false). Default false.")
    p.add_argument("--tasks-dir-with-skills", default=None)
    p.add_argument("--tasks-dir-without-skills", default=None)
    p.add_argument("--split-seed", type=int, default=42)
    p.add_argument("--category", default=None)
    p.add_argument("--difficulty", default=None)
    p.add_argument("--native-profile", default="terminus2")
    p.add_argument("--score-mode", default="dual")
    p.add_argument("--feedback-level", default="tests",
                   choices=["none", "score", "tests", "masked", "full"])

    # Model
    p.add_argument("--model-id", default="us.anthropic.claude-opus-4-6-v1")
    p.add_argument("--evolver-model-id", default=None,
                   help="Defaults to --model-id when unset.")
    p.add_argument("--region", default="us-west-2")
    p.add_argument("--max-tokens", type=int, default=16384)

    # Retry tuning (passed to benchmark + agent)
    p.add_argument("--retry-max", type=int, default=6)
    p.add_argument("--retry-min-wait-sec", type=float, default=1.0)
    p.add_argument("--retry-max-wait-sec", type=float, default=120.0)

    # Evolve scope
    p.add_argument("--evolve-skills", default="true")
    p.add_argument("--evolve-memory", default="false")
    p.add_argument("--evolve-prompts", default="false")
    p.add_argument("--evolve-tools", default="false")

    # Workspace / output
    p.add_argument("--seed-workspace", default=None,
                   help="Defaults to seed_workspaces/skillbench under repo root.")
    p.add_argument("--work-dir", default=None,
                   help="Defaults to <output-dir>/workspace.")
    p.add_argument("--run-dir", required=True,
                   help="Run dir; results.* files land here.")

    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    def _bool(v): return str(v).strip().lower() in ("1", "true", "yes", "y", "on")

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    for n in ("botocore", "urllib3", "httpcore", "httpx",
              "strands.models", "strands.tools", "strands.telemetry"):
        logging.getLogger(n).setLevel(logging.WARNING)
    log = logger

    run_dir = Path(args.run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)

    # ── Resolve workspace + tasks paths ──
    repo_root = Path(__file__).resolve().parent.parent.parent
    seed_dir = Path(args.seed_workspace) if args.seed_workspace else (
        repo_root / "seed_workspaces" / "skillbench")
    work_dir = Path(args.work_dir) if args.work_dir else (run_dir / "workspace")

    if not work_dir.exists() and seed_dir.exists():
        work_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(seed_dir, work_dir)
        log.info("Copied seed workspace %s -> %s", seed_dir, work_dir)

    # Resolve tasks via the shared helper (mirrors in-situ-cycle).
    resolved = resolve_skillbench_paths(
        tasks_with_skills_dir=args.tasks_dir_with_skills,
        tasks_without_skills_dir=args.tasks_dir_without_skills,
    )
    use_skills = _bool(args.use_skills)
    tasks_dir = resolved.tasks_with_skills_dir if use_skills else resolved.tasks_without_skills_dir

    # ── Load + slice tasks ──
    all_sb_tasks = load_all_tasks(tasks_dir=str(tasks_dir))
    if args.category:
        all_sb_tasks = [t for t in all_sb_tasks
                        if t.metadata.get("category") == args.category]
    if args.difficulty:
        all_sb_tasks = [t for t in all_sb_tasks
                        if t.metadata.get("difficulty") == args.difficulty]
    if args.limit is not None:
        all_sb_tasks = all_sb_tasks[: args.limit]
    train_sb = all_sb_tasks[: args.evolve_limit]
    if args.eval_limit is not None:
        test_sb = all_sb_tasks[args.evolve_limit: args.evolve_limit + args.eval_limit]
    else:
        test_sb = all_sb_tasks[args.evolve_limit:]
    log.info("Loaded %d tasks (use_skills=%s); train=%d, test=%d",
             len(all_sb_tasks), use_skills, len(train_sb), len(test_sb))

    # ── Benchmark + agent + evolver setup ──
    bm = SkillBenchBenchmark(
        tasks_with_skills_dir=str(resolved.tasks_with_skills_dir),
        tasks_without_skills_dir=str(resolved.tasks_without_skills_dir),
        use_skills=use_skills,
        split_seed=args.split_seed,
        execution_mode="native",
        shuffle=False,
        native_profile=args.native_profile,
        score_mode=args.score_mode,
        retry_max=args.retry_max,
        retry_min_wait_sec=args.retry_min_wait_sec,
        retry_max_wait_sec=args.retry_max_wait_sec,
    )
    agent = SkillBenchAgent(
        workspace_dir=work_dir,
        model_id=args.model_id,
        region=args.region,
        max_tokens=args.max_tokens,
        tasks_dir=str(tasks_dir),
        execution_mode="native",
        native_profile=args.native_profile,
        score_mode=args.score_mode,
        retry_max=args.retry_max,
        retry_min_wait_sec=args.retry_min_wait_sec,
        retry_max_wait_sec=args.retry_max_wait_sec,
        write_episodic_memory=_bool(args.evolve_memory),
    )
    runtime_config = bm.get_agent_runtime_config()
    runtime_config["native_profile"] = args.native_profile
    runtime_config["score_mode"] = args.score_mode
    runtime_config["task_skills_enabled"] = False  # split mode skips task-skill pre-gen
    agent.configure_from_benchmark(runtime_config)

    evolution_dir = work_dir / "evolution"
    evolution_dir.mkdir(parents=True, exist_ok=True)
    observer = Observer(evolution_dir)

    config = EvolveConfig(
        evolver_model=args.evolver_model_id or args.model_id,
        extra={"region": args.region},
        evolve_skills=_bool(args.evolve_skills),
        evolve_memory=_bool(args.evolve_memory),
        evolve_prompts=_bool(args.evolve_prompts),
        evolve_tools=_bool(args.evolve_tools),
    )
    evolver = AEvolveEngine(config)
    log.info("Initialized AEvolveEngine (legacy SkillBench)")

    train_records: list[dict] = []
    test_records: list[dict] = []
    evo_counter = 0
    backstop = max(1, args.max_workers)
    train_parallel = max(1, args.train_parallel) if args.train_parallel is not None else backstop
    test_parallel = max(1, args.test_parallel) if args.test_parallel is not None else backstop

    # Thread-safety guard: SkillBenchAgent.remember() appends to a plain list
    # without a lock, so concurrent solves that also write episodic memory race.
    # Default config (parallel=1 OR evolve_memory=false) is safe; reject the
    # unsafe combo upfront with a clear error.
    if (train_parallel > 1 or test_parallel > 1) and _bool(args.evolve_memory):
        log.error(
            "Unsafe combination: train_parallel=%d, test_parallel=%d (>1) with "
            "--evolve-memory=true. SkillBenchAgent.remember() is not thread-safe. "
            "Set both parallel knobs to 1, or set --evolve-memory false.",
            train_parallel, test_parallel,
        )
        return 2

    # ── Phase 1: TRAIN (batched evolve) ──
    n_train_batches = (len(train_sb) + args.batch_size - 1) // args.batch_size if train_sb else 0
    train_workers = min(train_parallel, max(1, args.batch_size))
    print(f"\n>>> Phase 1: TRAIN  (n={len(train_sb)}, batch={args.batch_size}, "
          f"train_parallel={train_parallel}, effective={train_workers})\n")
    for batch_idx in range(0, len(train_sb), args.batch_size):
        batch = train_sb[batch_idx: batch_idx + args.batch_size]
        batch_num = batch_idx // args.batch_size + 1
        print(f"=== Train batch {batch_num}/{n_train_batches} (tasks {batch_idx+1}-{batch_idx+len(batch)}) ===")
        results = _solve_batch_parallel(batch=batch, agent=agent, bm=bm,
                                        max_workers=train_workers, log=log)
        for r in results:
            if r["feedback"] is not None:
                train_records.append(_record(r["task"], r["feedback"].success,
                                             r["feedback"].score, r["elapsed"],
                                             "train", evo_counter))
            else:
                train_records.append(_record(r["task"], False, 0.0, r["elapsed"],
                                             "train", evo_counter, r.get("error", "")))
        _evolve_after_batch(evolver=evolver, agent=agent, observer=observer,
                            batch_results=results, evo_counter=evo_counter,
                            feedback_level=args.feedback_level, log=log)
        evo_counter += 1
        passed = sum(1 for r in results if r.get("feedback") and r["feedback"].success)
        print(f"  Train batch {batch_num}: {passed}/{len(results)}\n")

    train_pass = sum(1 for r in train_records if r["success"])
    print(f"--- Phase 1 done: TRAIN {train_pass}/{len(train_records)} "
          f"({100*train_pass/max(len(train_records),1):.1f}%) ---")

    # ── Phase 2: TEST (no-evolve) ──
    if test_sb:
        # No batch-evolve boundary; can run with higher parallelism than train.
        print(f"\n>>> Phase 2: TEST   (n={len(test_sb)}, test_parallel={test_parallel}, no-evolve)\n")
        results = _solve_batch_parallel(batch=test_sb, agent=agent, bm=bm,
                                        max_workers=test_parallel, log=log)
        for r in results:
            if r["feedback"] is not None:
                test_records.append(_record(r["task"], r["feedback"].success,
                                            r["feedback"].score, r["elapsed"],
                                            "test", evo_counter))
            else:
                test_records.append(_record(r["task"], False, 0.0, r["elapsed"],
                                            "test", evo_counter, r.get("error", "")))
        test_pass = sum(1 for r in test_records if r["success"])
        print(f"--- Phase 2 done: TEST  {test_pass}/{len(test_records)} "
              f"({100*test_pass/max(len(test_records),1):.1f}%) ---")
    else:
        print("\n>>> Phase 2: SKIPPED (no test tasks)\n")

    # ── Persist outputs (split convention) ──
    train_path = run_dir / "results.train.jsonl"
    test_path = run_dir / "results.test.jsonl"
    combined_path = run_dir / "results.jsonl"
    metrics_path = run_dir / "results.metrics.json"

    with train_path.open("w") as f:
        for r in train_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with test_path.open("w") as f:
        for r in test_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with combined_path.open("w") as f:
        for r in train_records + test_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    train_pass = sum(1 for r in train_records if r["success"])
    test_pass = sum(1 for r in test_records if r["success"])
    metrics = {
        "phase1_train": {
            "evolve_limit": args.evolve_limit,
            "batch_size": args.batch_size,
            "n_tasks": len(train_records),
            "n_pass": train_pass,
            "pass_rate": train_pass / max(len(train_records), 1),
            "evolve_generations": evo_counter,
        },
        "phase2_test": {
            "eval_limit": args.eval_limit,
            "n_tasks": len(test_records),
            "n_pass": test_pass,
            "pass_rate": test_pass / max(len(test_records), 1),
        },
        "pass_ratio": (test_pass / max(len(test_records), 1) if test_records else
                       train_pass / max(len(train_records), 1)),
        "engine": "AEvolveEngine",
        "legacy_settings": {
            "use_skills": use_skills,
            "native_profile": args.native_profile,
            "score_mode": args.score_mode,
            "feedback_level": args.feedback_level,
            "evolve_skills": _bool(args.evolve_skills),
            "evolve_memory": _bool(args.evolve_memory),
            "evolve_prompts": _bool(args.evolve_prompts),
            "evolve_tools": _bool(args.evolve_tools),
            "train_parallel": train_parallel,
            "test_parallel": test_parallel,
            "model_id": args.model_id,
            "evolver_model_id": args.evolver_model_id or args.model_id,
        },
        "workspace": str(work_dir.resolve()),
    }
    metrics_path.write_text(json.dumps(metrics, indent=2))

    print(f"\n{'='*60}")
    print(f"FINAL")
    print(f"  TRAIN: {train_pass}/{len(train_records)}"
          f" ({100*train_pass/max(len(train_records),1):.1f}%)")
    print(f"  TEST:  {test_pass}/{len(test_records)}"
          f" ({100*test_pass/max(len(test_records),1):.1f}%)")
    print(f"  Evolutions: {evo_counter}")
    print(f"{'='*60}")
    print(f"  Train:    {train_path}")
    print(f"  Test:     {test_path}")
    print(f"  Combined: {combined_path}")
    print(f"  Metrics:  {metrics_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
