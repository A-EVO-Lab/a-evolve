#!/usr/bin/env python3
"""SWE legacy train/test split runner.

Two-phase split that mirrors TB's run_evolution.sh shape but uses the
legacy ``GuidedSynthesisEngine`` (not UnifiedEngine):

  Phase 1 (TRAIN): first ``--evolve-limit`` tasks, run in batches of
                   ``--batch-size`` with ``GuidedSynthesisEngine`` evolving
                   the workspace after each batch (and optionally a
                   solver-proposal curate step).
  Phase 2 (TEST):  remaining tasks (capped by ``--eval-limit`` or ``--limit``),
                   solved with the evolved workspace, ``no-evolve``.

This file is intentionally a thin orchestrator over ``evolve_sequential.py``
-- the per-task worker (``solve_one_task``) is imported from there so any
agent-side changes flow through automatically.

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
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_evolve.agents.swe.agent import SweAgent
from agent_evolve.algorithms.guided_synth.engine import GuidedSynthesisEngine
from agent_evolve.benchmarks.swe_verified_mini.benchmark import SweVerifiedMiniBenchmark
from agent_evolve.config import EvolveConfig
from agent_evolve.engine.observer import Observer
from agent_evolve.types import Feedback, Observation, Trajectory

# Reuse the per-task worker from the in-situ sequential script so any
# changes to solve_one_task flow through both entrypoints.
from evolve_sequential import solve_one_task  # noqa: E402

logger = logging.getLogger("evolve_seq_split")


def _record(r: dict, batch_num: int, evolve_generation: int, phase: str) -> dict:
    return {
        "instance_id": r["instance_id"],
        "phase": phase,
        "batch": batch_num,
        "success": r["success"],
        "score": r["score"],
        "elapsed": r.get("elapsed", 0),
        "patch_len": r.get("patch_len", 0),
        "error": r.get("error"),
        "evolve_generation": evolve_generation,
        "num_tool_calls": r.get("num_tool_calls", 0),
        "num_turns": r.get("num_turns", 0),
        "cumulative_input_tokens": r.get("cumulative_input_tokens", 0),
        "max_input_tokens_per_turn": r.get("max_input_tokens_per_turn", 0),
    }


def _save_patch(r: dict, output_dir: Path) -> None:
    if r.get("patch"):
        patch_path = output_dir / "patches" / f"{r['instance_id'].replace('/', '_')}.diff"
        patch_path.parent.mkdir(parents=True, exist_ok=True)
        patch_path.write_text(r["patch"])


def _solve_batch(
    batch_tasks,
    *,
    work_dir: Path,
    parallel: int,
    args,
) -> list[dict]:
    """Solve a single batch in parallel; returns raw worker dicts."""
    task_dicts = [{"id": t.id, "input": t.input, "metadata": t.metadata} for t in batch_tasks]
    batch_results: list[dict] = []
    with ProcessPoolExecutor(max_workers=parallel) as pool:
        futures = {
            pool.submit(
                solve_one_task, td, str(work_dir),
                args.model_id, args.region, args.max_tokens,
                args.max_steps, args.window_size,
                args.verification_focus,
                getattr(args, "efficiency_prompt", False),
            ): td["id"]
            for td in task_dicts
        }
        for fut in as_completed(futures):
            iid = futures[fut]
            try:
                r = fut.result()
                batch_results.append(r)
                status = "PASS" if r["success"] else "FAIL"
                print(f"  {status} {iid} ({r['elapsed']:.0f}s)")
            except Exception as e:
                print(f"  ERROR {iid}: {e}")
                batch_results.append({
                    "instance_id": iid,
                    "success": False,
                    "score": 0.0,
                    "error": str(e),
                    "elapsed": 0,
                    "patch_len": 0,
                    "patch": "",
                })
    return batch_results


def _evolve_after_batch(
    *,
    batch_tasks,
    batch_results: list[dict],
    work_dir: Path,
    output_dir: Path,
    args,
    evolver: GuidedSynthesisEngine,
    observer: Observer,
    evolve_count: int,
    label: str = "evolved",
) -> int:
    """Build observations from batch_results and run one evolve step."""
    observations: list[Observation] = []
    for r in batch_results:
        if r.get("patch") is None:
            continue
        task_obj = next((t for t in batch_tasks if t.id == r["instance_id"]), None)
        if not task_obj:
            continue
        traj = Trajectory(task_id=r["instance_id"], output=r.get("patch", ""), steps=[])
        if args.feedback == "none":
            fb = Feedback(success=False, score=0.0, detail="", raw={})
        else:
            fb = Feedback(
                success=r["success"],
                score=r["score"],
                detail=r.get("feedback_detail", ""),
                raw={},
            )
        observations.append(Observation(task=task_obj, trajectory=traj, feedback=fb))

    if not observations:
        return evolve_count

    evolve_count += 1
    logger.info("=== %s (gen %d, %d obs) ===", label.upper(), evolve_count, len(observations))

    agent = SweAgent(workspace_dir=work_dir, model_id=args.model_id,
                     region=args.region, max_tokens=args.max_tokens)
    agent.export_to_fs()
    observer.collect(observations)

    t_evo = time.time()
    try:
        evolver.evolve(
            workspace=agent.workspace,
            observation_logs=observations,
            evo_number=evolve_count,
        )
        logger.info("%s gen %d complete in %.1fs", label, evolve_count, time.time() - t_evo)
    except Exception as e:
        logger.error("%s gen %d failed: %s", label, evolve_count, e)

    agent_check = SweAgent(workspace_dir=work_dir, model_id=args.model_id,
                           region=args.region, max_tokens=args.max_tokens)
    prompt_len = len(agent_check._build_system_prompt())
    n_skills = len(agent_check.skills)
    print(f"  [{label} gen{evolve_count}] prompt={prompt_len} chars, skills={n_skills}")

    if args.solver_proposes and label == "evolved":
        # Re-attach solver proposals and run a curate pass.
        for r in batch_results:
            if r.get("patch") is None:
                continue
            for obs in observations:
                if obs.task.id == r["instance_id"]:
                    obs.trajectory._skill_proposal = r.get("skill_proposal", "")
                    break
        evolve_count = _curate_proposals(
            observations=observations,
            work_dir=work_dir,
            args=args,
            evolver=evolver,
            observer=observer,
            evolve_count=evolve_count,
        )
    return evolve_count


def _curate_proposals(
    *,
    observations,
    work_dir: Path,
    args,
    evolver: GuidedSynthesisEngine,
    observer: Observer,
    evolve_count: int,
) -> int:
    if not observations:
        return evolve_count
    evolve_count += 1
    logger.info("=== CURATING PROPOSALS (gen %d) ===", evolve_count)
    agent = SweAgent(workspace_dir=work_dir, model_id=args.model_id,
                     region=args.region, max_tokens=args.max_tokens)
    agent.export_to_fs()
    observer.collect(observations)
    try:
        evolver.evolve(
            workspace=agent.workspace,
            observation_logs=observations,
            evo_number=evolve_count,
        )
    except Exception as e:
        logger.error("Curation %d failed: %s", evolve_count, e)
    agent_check = SweAgent(workspace_dir=work_dir, model_id=args.model_id,
                           region=args.region, max_tokens=args.max_tokens)
    prompt_len = len(agent_check._build_system_prompt())
    n_skills = len(agent_check.skills)
    print(f"  [curated gen{evolve_count}] prompt={prompt_len} chars, skills={n_skills}")
    return evolve_count


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--evolve-limit", type=int, default=50,
                   help="Tasks for Phase 1 (train, evolve). Default 50.")
    p.add_argument("--eval-limit", type=int, default=None,
                   help="Tasks for Phase 2 (test). Default: all remaining up to --limit.")
    p.add_argument("--limit", type=int, default=200,
                   help="Total task cap (train + test). Default 200.")
    p.add_argument("--batch-size", type=int, default=20,
                   help="Train batch size (Phase 1). Default 20.")
    # Train: capped at batch-size automatically.
    p.add_argument("--train-parallel", type=int, default=20,
                   help="Phase 1 parallel workers (effective = min(this, batch-size)).")
    p.add_argument("--test-parallel", type=int, default=20,
                   help="Phase 2 parallel workers (one big run, no batches).")
    # Legacy alias.
    p.add_argument("--parallel", type=int, default=None,
                   help="Legacy alias: sets train-parallel if --train-parallel not given.")
    p.add_argument("--feedback", type=str, default="none",
                   choices=["none", "minimal"])
    p.add_argument("--solver-proposes", action="store_true")
    p.add_argument("--verification-focus", action="store_true")
    p.add_argument("--efficiency-prompt", action="store_true")
    p.add_argument("--model-id", type=str, default="us.anthropic.claude-opus-4-6-v1")
    p.add_argument("--region", type=str, default="us-west-2")
    p.add_argument("--max-tokens", type=int, default=16384)
    p.add_argument("--max-steps", type=int, default=140)
    p.add_argument("--window-size", type=int, default=70)
    p.add_argument("--seed-workspace", type=str, default="seed_workspaces/swe")
    p.add_argument("--output-dir", type=str, required=True)
    p.add_argument("--dataset", type=str, default="princeton-nlp/SWE-bench_Verified")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    # Reconcile parallel aliases.
    if args.parallel is not None:
        if args.train_parallel == 20:  # default; override via legacy
            args.train_parallel = args.parallel

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    for n in ("botocore", "urllib3", "httpcore", "httpx",
              "strands.models", "strands.tools", "strands.telemetry"):
        logging.getLogger(n).setLevel(logging.WARNING)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Slice tasks: train = first evolve_limit, test = next eval_limit (or rest).
    bm = SweVerifiedMiniBenchmark(dataset_name=args.dataset, shuffle=False)
    all_tasks = bm.get_tasks(split="test", limit=args.limit)
    train_tasks = all_tasks[: args.evolve_limit]
    if args.eval_limit is not None:
        test_tasks = all_tasks[args.evolve_limit: args.evolve_limit + args.eval_limit]
    else:
        test_tasks = all_tasks[args.evolve_limit:]
    logger.info("Loaded %d total tasks; train=%d, test=%d",
                len(all_tasks), len(train_tasks), len(test_tasks))

    # Init workspace once (persists across phases).
    work_dir = output_dir / "workspace"
    seed_dir = Path(args.seed_workspace)
    if work_dir.exists():
        shutil.rmtree(work_dir)
    shutil.copytree(seed_dir, work_dir)
    logger.info("Copied seed workspace %s -> %s", seed_dir, work_dir)

    # Phase 1 setup.
    evolution_dir = work_dir / "evolution"
    evolution_dir.mkdir(parents=True, exist_ok=True)
    observer = Observer(evolution_dir)
    config = EvolveConfig(evolver_model=args.model_id, extra={"region": args.region})
    evolver = GuidedSynthesisEngine(config, write_memory=False,
                                     verification_focus=args.verification_focus)

    n_train_batches = (len(train_tasks) + args.batch_size - 1) // args.batch_size if train_tasks else 0
    # Reserve interventions for both train evolve calls and curate pass.
    evolver.MAX_INTERVENTIONS = max(1, n_train_batches * 2)

    train_records: list[dict] = []
    test_records: list[dict] = []
    evolve_count = 0
    train_parallel = min(args.train_parallel, max(1, args.batch_size))

    # ------------------------------------------------------------------
    # Phase 1: TRAIN (evolve)
    # ------------------------------------------------------------------
    print(f"\n>>> Phase 1: TRAIN  (n={len(train_tasks)}, batch={args.batch_size}, parallel={train_parallel})\n")
    for batch_idx in range(0, len(train_tasks), args.batch_size):
        batch_tasks = train_tasks[batch_idx: batch_idx + args.batch_size]
        batch_num = batch_idx // args.batch_size + 1
        print(f"=== Train batch {batch_num}/{n_train_batches} (tasks {batch_idx+1}-{batch_idx+len(batch_tasks)}) ===")
        batch_results = _solve_batch(batch_tasks, work_dir=work_dir,
                                     parallel=train_parallel, args=args)
        for r in batch_results:
            train_records.append(_record(r, batch_num, evolve_count, "train"))
            _save_patch(r, output_dir)
        evolve_count = _evolve_after_batch(
            batch_tasks=batch_tasks,
            batch_results=batch_results,
            work_dir=work_dir,
            output_dir=output_dir,
            args=args,
            evolver=evolver,
            observer=observer,
            evolve_count=evolve_count,
            label="evolved",
        )
        passed = sum(1 for r in batch_results if r["success"])
        print(f"  Train batch {batch_num}: {passed}/{len(batch_results)}\n")

    train_pass = sum(1 for r in train_records if r["success"])
    print(f"--- Phase 1 done: TRAIN {train_pass}/{len(train_records)}"
          f" ({100*train_pass/max(len(train_records),1):.1f}%) ---")

    # ------------------------------------------------------------------
    # Phase 2: TEST (no evolve)
    # ------------------------------------------------------------------
    if test_tasks:
        print(f"\n>>> Phase 2: TEST   (n={len(test_tasks)}, parallel={args.test_parallel}, no-evolve)\n")
        # One big "batch" so the parallel pool spans the whole test set.
        test_results = _solve_batch(test_tasks, work_dir=work_dir,
                                    parallel=args.test_parallel, args=args)
        for r in test_results:
            test_records.append(_record(r, 0, evolve_count, "test"))
            _save_patch(r, output_dir)
        test_pass = sum(1 for r in test_records if r["success"])
        print(f"--- Phase 2 done: TEST  {test_pass}/{len(test_records)}"
              f" ({100*test_pass/max(len(test_records),1):.1f}%) ---")
    else:
        print("\n>>> Phase 2: SKIPPED (no test tasks)\n")

    # ------------------------------------------------------------------
    # Persist outputs.
    # ------------------------------------------------------------------
    train_path = output_dir / "results.train.jsonl"
    test_path = output_dir / "results.test.jsonl"
    combined_path = output_dir / "results.jsonl"
    metrics_path = output_dir / "results.metrics.json"

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
            "evolve_generations": evolve_count,
        },
        "phase2_test": {
            "eval_limit": args.eval_limit,
            "n_tasks": len(test_records),
            "n_pass": test_pass,
            "pass_rate": test_pass / max(len(test_records), 1),
        },
        # Final score = test pass rate (matches TB unified split convention).
        "pass_ratio": test_pass / max(len(test_records), 1) if test_records else
                      train_pass / max(len(train_records), 1),
        "engine": "GuidedSynthesisEngine",
        "legacy_settings": {
            "feedback": args.feedback,
            "solver_proposes": args.solver_proposes,
            "verification_focus": args.verification_focus,
            "efficiency_prompt": args.efficiency_prompt,
            "max_steps": args.max_steps,
            "window_size": args.window_size,
            "train_parallel": train_parallel,
            "test_parallel": args.test_parallel,
        },
        "dataset": args.dataset,
        "workspace": str(work_dir.resolve()),
    }
    metrics_path.write_text(json.dumps(metrics, indent=2))

    # Final console summary.
    print(f"\n{'='*60}")
    print(f"FINAL")
    print(f"  TRAIN: {train_pass}/{len(train_records)}"
          f" ({100*train_pass/max(len(train_records),1):.1f}%)")
    print(f"  TEST:  {test_pass}/{len(test_records)}"
          f" ({100*test_pass/max(len(test_records),1):.1f}%)")
    print(f"  Evolutions: {evolve_count}")
    print(f"{'='*60}")
    print(f"  Train:    {train_path}")
    print(f"  Test:     {test_path}")
    print(f"  Combined: {combined_path}")
    print(f"  Metrics:  {metrics_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
