#!/usr/bin/env python3
"""In-situ batched evolution: solve tasks in batches, evolve between batches.

The agent solves all 50 swe_verified_mini tasks in batches of N (default 5).
Each batch runs in parallel with a shared workspace snapshot. After each batch,
the evolver analyzes results and generates interventions that carry forward
to the next batch.

Two feedback modes:
  - none:    evolver sees trajectories but NOT whether the solver succeeded
  - minimal: evolver sees trajectories AND whether the solver succeeded (score)

Usage:
    python evolve_sequential.py --batch-size 5 --parallel 5 --feedback none --output-dir logs/seq_none
    python evolve_sequential.py --batch-size 5 --parallel 5 --feedback minimal --output-dir logs/seq_minimal
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

# Strands SDK uses recursive event_loop dispatch + recursive JSON telemetry
# serialization; Python's default limit (1000) is too shallow for long tool chains.
sys.setrecursionlimit(10000)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agent_evolve.agents.swe.agent import SweAgent
from agent_evolve.algorithms.guided_synth.engine import GuidedSynthesisEngine
from agent_evolve.benchmarks.swe_verified_mini.benchmark import SweVerifiedMiniBenchmark
from agent_evolve.config import EvolveConfig
from agent_evolve.engine.observer import Observer
from agent_evolve.types import Feedback, Observation, Trajectory

logger = logging.getLogger("evolve_seq")


def solve_one_task(
    task_dict: dict,
    workspace_dir: str,
    model_id: str,
    region: str,
    max_tokens: int,
    max_steps: int = 0,
    window_size: int = 40,
    verification_focus: bool = False,
    efficiency_prompt: bool = False,
    verify_fix_prompt: bool = True,
    solver_proposes: bool = True,
) -> dict:
    """Solve a single task in its own process. Workspace is read-only during batch."""
    import logging
    import time
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from agent_evolve.agents.swe.agent import SweAgent
    from agent_evolve.benchmarks.swe_verified_mini.benchmark import SweVerifiedMiniBenchmark
    from agent_evolve.types import Task

    logging.basicConfig(
        level=logging.INFO,
        format=f"%(asctime)s [{task_dict['id']}] %(message)s",
    )
    for n in ("botocore", "urllib3", "httpcore", "httpx",
              "strands.models", "strands.tools", "strands.telemetry"):
        logging.getLogger(n).setLevel(logging.WARNING)
    log = logging.getLogger("worker")

    task = Task(id=task_dict["id"], input=task_dict["input"], metadata=task_dict["metadata"])
    instance_id = task.id

    # Create agent from shared workspace (read-only during parallel solve)
    agent = SweAgent(
        workspace_dir=workspace_dir,
        model_id=model_id,
        region=region,
        max_tokens=max_tokens,
        max_steps=max_steps,
        window_size=window_size,
        verification_focus=verification_focus,
        efficiency_prompt=efficiency_prompt,
        verify_fix_prompt=verify_fix_prompt,
        solver_proposes=solver_proposes,
    )

    bm = SweVerifiedMiniBenchmark(shuffle=False)

    t0 = time.time()
    trajectory = None
    for attempt in range(3):
        try:
            trajectory = agent.solve(task)
            break
        except Exception as e:
            err_str = str(e)
            transient = any(k in err_str for k in (
                "internalServerException", "ThrottlingException",
                "timed out", "Read timed out", "ServiceUnavailableException",
            ))
            if transient and attempt < 2:
                wait = 30 * (attempt + 1)
                log.warning("solve attempt %d failed (transient), retrying in %ds: %s",
                            attempt + 1, wait, e)
                time.sleep(wait)
                continue
            log.error("solve failed after %d attempt(s): %s", attempt + 1, e)
            return {
                "instance_id": instance_id,
                "success": False,
                "score": 0.0,
                "error": err_str,
                "elapsed": round(time.time() - t0, 1),
                "patch_len": 0,
                "patch": "",
            }

    if trajectory is None:
        return {
            "instance_id": instance_id,
            "success": False,
            "score": 0.0,
            "error": "no trajectory",
            "elapsed": round(time.time() - t0, 1),
            "patch_len": 0,
            "patch": "",
        }

    elapsed = time.time() - t0
    feedback = bm.evaluate(task, trajectory)
    log.info("%s score=%.1f elapsed=%.1fs patch=%dch",
             "PASS" if feedback.success else "FAIL",
             feedback.score, elapsed, len(trajectory.output))

    # Extract per-turn stats from trajectory
    num_tool_calls = len([s for s in trajectory.steps if isinstance(s, dict) and "tool" in s])
    usage_step = next((s for s in trajectory.steps if isinstance(s, dict) and "usage" in s), {})
    usage = usage_step.get("usage", {}) if isinstance(usage_step, dict) else {}
    per_turn = usage_step.get("per_turn_usage", []) if isinstance(usage_step, dict) else []
    max_input = usage_step.get("max_input_tokens_per_turn", 0) if isinstance(usage_step, dict) else 0

    log.info("stats: %d tool calls, %d turns, max_input=%d, cumulative_input=%d",
             num_tool_calls, len(per_turn), max_input, usage.get("input_tokens", 0))

    # Save full conversation to file
    conversation = getattr(trajectory, "_conversation", None)
    conversation_data = None
    if conversation:
        try:
            import json as _json
            safe_id = instance_id.replace("/", "_")
            conv_dir = Path(workspace_dir).parent / "conversations"
            conv_dir.mkdir(parents=True, exist_ok=True)
            conv_path = conv_dir / f"{safe_id}.json"
            conv_path.write_text(_json.dumps(conversation, indent=2, default=str, ensure_ascii=False))
            log.info("Saved conversation to %s (%d messages)", conv_path, len(conversation))
        except Exception as conv_err:
            log.warning("Failed to save conversation: %s", conv_err)

    return {
        "instance_id": instance_id,
        "success": feedback.success,
        "score": feedback.score,
        "elapsed": round(elapsed, 1),
        "patch_len": len(trajectory.output),
        "patch": trajectory.output,
        "skill_proposal": getattr(trajectory, "_skill_proposal", ""),
        "feedback_detail": feedback.detail,
        "num_tool_calls": num_tool_calls,
        "num_turns": len(per_turn),
        "cumulative_input_tokens": usage.get("input_tokens", 0),
        "cumulative_output_tokens": usage.get("output_tokens", 0),
        "max_input_tokens_per_turn": max_input,
    }


def main():
    p = argparse.ArgumentParser(description="Batched in-situ evolution")
    p.add_argument("--batch-size", type=int, default=5,
                   help="Tasks per batch (default: 5). Evolver runs after each batch.")
    p.add_argument("--parallel", type=int, default=5,
                   help="Parallel workers within each batch (default: 5)")
    p.add_argument("--feedback", type=str, default="minimal",
                   choices=["none", "minimal"],
                   help="Feedback to evolver: none=no scores, minimal=scores included")
    p.add_argument("--no-evolve", action="store_true",
                   help="Baseline mode: solve all tasks without any evolution")
    p.add_argument("--solver-proposes", action="store_true",
                   help="V11: solver proposes skills after each task, applied between batches")
    p.add_argument("--verification-focus", action="store_true",
                   help="V21: solver only proposes verification skills, evolver only curates verification")
    p.add_argument("--efficiency-prompt", action="store_true",
                   help="V22: add hypothesis-first efficiency constraints to system prompt")
    p.add_argument("--verify-fix-prompt", default=True,
                   action=argparse.BooleanOptionalAction,
                   help="Append the '## Verify Your Fix' section to the system prompt. "
                        "Default: on (preserves prior unconditional behaviour). "
                        "Use --no-verify-fix-prompt to drop it for a tighter baseline.")
    p.add_argument("--model-id", type=str, default="us.anthropic.claude-opus-4-6-v1")
    p.add_argument("--evolver-model-id", type=str, default=None,
                   help="Evolver model id; defaults to --model-id when omitted.")
    p.add_argument("--region", type=str, default="us-west-2")
    p.add_argument("--max-tokens", type=int, default=16384)
    p.add_argument("--max-steps", type=int, default=0,
                   help="Max tool calls per task (0=unlimited, 80 recommended)")
    p.add_argument("--window-size", type=int, default=40,
                   help="Sliding window size for conversation memory (default: 40)")
    p.add_argument("--seed-workspace", type=str, default="seed_workspaces/swe")
    p.add_argument("--output-dir", type=str, default="logs/seq_evolve")
    p.add_argument("--dataset", type=str, default="MariusHobbhahn/swe-bench-verified-mini",
                   help="HuggingFace dataset name (default: swe-bench-verified-mini)")
    p.add_argument("--limit", type=int, default=50,
                   help="Max tasks to solve (default: 50)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    for n in ("botocore", "urllib3", "httpcore", "httpx",
              "strands.models", "strands.tools", "strands.telemetry"):
        logging.getLogger(n).setLevel(logging.WARNING)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load all tasks
    bm = SweVerifiedMiniBenchmark(dataset_name=args.dataset, shuffle=False)
    tasks = bm.get_tasks(split="test", limit=args.limit)
    logger.info("Loaded %d tasks", len(tasks))

    # Initialize shared workspace
    work_dir = output_dir / "workspace"
    seed_dir = Path(args.seed_workspace)
    if work_dir.exists():
        shutil.rmtree(work_dir)
    shutil.copytree(seed_dir, work_dir)
    logger.info("Copied seed workspace %s → %s", seed_dir, work_dir)

    # Setup evolution
    evolution_dir = work_dir / "evolution"
    evolution_dir.mkdir(parents=True, exist_ok=True)
    observer = Observer(evolution_dir)

    evolver_model_id = args.evolver_model_id or args.model_id
    config = EvolveConfig(evolver_model=evolver_model_id, extra={"region": args.region})
    evolver = GuidedSynthesisEngine(config, write_memory=False,
                                     verification_focus=getattr(args, 'verification_focus', False))
    n_batches = (len(tasks) + args.batch_size - 1) // args.batch_size
    evolver.MAX_INTERVENTIONS = n_batches  # one intervention per batch

    # Track results
    all_results = []
    evolve_count = 0

    # Split tasks into batches
    batches = []
    for i in range(0, len(tasks), args.batch_size):
        batches.append(tasks[i:i + args.batch_size])

    print(f"Solving {len(tasks)} tasks in {len(batches)} batches of {args.batch_size}")
    print(f"Parallel workers: {args.parallel}")
    print(f"Feedback mode: {args.feedback}")
    print(f"Output: {output_dir.resolve()}")
    print()

    for batch_idx, batch_tasks in enumerate(batches):
        batch_num = batch_idx + 1
        task_start = batch_idx * args.batch_size + 1
        task_end = task_start + len(batch_tasks) - 1
        print(f"=== Batch {batch_num}/{len(batches)} (tasks {task_start}-{task_end}) ===")

        # Prepare task dicts for parallel execution
        task_dicts = [{"id": t.id, "input": t.input, "metadata": t.metadata} for t in batch_tasks]

        # Solve batch in parallel
        batch_results = []
        with ProcessPoolExecutor(max_workers=args.parallel) as pool:
            futures = {
                pool.submit(
                    solve_one_task, td, str(work_dir),
                    args.model_id, args.region, args.max_tokens,
                    args.max_steps, args.window_size,
                    args.verification_focus,
                    getattr(args, 'efficiency_prompt', False),
                    getattr(args, 'verify_fix_prompt', True),
                    getattr(args, 'solver_proposes', True),
                ): td["id"]
                for td in task_dicts
            }
            for fut in as_completed(futures):
                iid = futures[fut]
                try:
                    r = fut.result()
                    batch_results.append(r)
                    status = "PASS" if r["success"] else "FAIL"
                    passed_total = sum(1 for x in all_results if x["success"]) + sum(1 for x in batch_results if x["success"])
                    done_total = len(all_results) + len(batch_results)
                    print(f"  {status} {iid} ({r['elapsed']:.0f}s) | running: {passed_total}/{done_total}")
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

        # Record results
        for r in batch_results:
            record = {
                "instance_id": r["instance_id"],
                "batch": batch_num,
                "success": r["success"],
                "score": r["score"],
                "elapsed": r.get("elapsed", 0),
                "patch_len": r.get("patch_len", 0),
                "error": r.get("error"),
                "evolve_generation": evolve_count,
                "num_tool_calls": r.get("num_tool_calls", 0),
                "num_turns": r.get("num_turns", 0),
                "cumulative_input_tokens": r.get("cumulative_input_tokens", 0),
                "max_input_tokens_per_turn": r.get("max_input_tokens_per_turn", 0),
            }
            all_results.append(record)

        # Save patches
        for r in batch_results:
            if r.get("patch"):
                patch_path = output_dir / "patches" / f"{r['instance_id'].replace('/', '_')}.diff"
                patch_path.parent.mkdir(parents=True, exist_ok=True)
                patch_path.write_text(r["patch"])

        # Evolve after this batch
        observations = []
        for r in batch_results:
            if r.get("patch") is not None:
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

        if observations and not args.no_evolve:
            evolve_count += 1
            logger.info("=== EVOLVING (generation %d, %d observations) ===",
                        evolve_count, len(observations))

            # Use a fresh agent to export workspace state
            agent = SweAgent(workspace_dir=work_dir, model_id=args.model_id,
                             region=args.region, max_tokens=args.max_tokens)
            agent.export_to_fs()
            observer.collect(observations)

            t_evo = time.time()
            try:
                evo_result = evolver.evolve(
                    workspace=agent.workspace,
                    observation_logs=observations,
                    evo_number=evolve_count,
                )
                logger.info("Evolution %d complete in %.1fs", evolve_count, time.time() - t_evo)
            except Exception as e:
                logger.error("Evolution %d failed: %s", evolve_count, e)

            # Log workspace state
            agent_check = SweAgent(workspace_dir=work_dir, model_id=args.model_id,
                                   region=args.region, max_tokens=args.max_tokens)
            prompt_len = len(agent_check._build_system_prompt())
            n_skills = len(agent_check.skills)
            print(f"  [evolved gen{evolve_count}] prompt={prompt_len} chars, skills={n_skills}")

        # V11: Solver proposes skills → evolver curates (accept/reject/merge)
        if args.solver_proposes and not args.no_evolve:
            # Attach proposals to trajectory objects for evolver
            for r in batch_results:
                proposal = r.get("skill_proposal", "")
                if r.get("patch") is not None:
                    task_obj = next((t for t in batch_tasks if t.id == r["instance_id"]), None)
                    if not task_obj:
                        continue
                    # Find or create the observation's trajectory
                    for obs in observations:
                        if obs.task.id == r["instance_id"]:
                            obs.trajectory._skill_proposal = proposal
                            break

            if observations:
                evolve_count += 1
                logger.info("=== CURATING PROPOSALS (generation %d) ===", evolve_count)
                agent = SweAgent(workspace_dir=work_dir, model_id=args.model_id,
                                 region=args.region, max_tokens=args.max_tokens)
                agent.export_to_fs()
                observer.collect(observations)

                t_evo = time.time()
                try:
                    evo_result = evolver.evolve(
                        workspace=agent.workspace,
                        observation_logs=observations,
                        evo_number=evolve_count,
                    )
                    logger.info("Curation %d complete in %.1fs: %s",
                                evolve_count, time.time() - t_evo, evo_result)
                except Exception as e:
                    logger.error("Curation %d failed: %s", evolve_count, e)

                agent_check = SweAgent(workspace_dir=work_dir, model_id=args.model_id,
                                       region=args.region, max_tokens=args.max_tokens)
                prompt_len = len(agent_check._build_system_prompt())
                n_skills = len(agent_check.skills)
                print(f"  [curated gen{evolve_count}] prompt={prompt_len} chars, skills={n_skills}")

        batch_passed = sum(1 for r in batch_results if r["success"])
        print(f"  Batch {batch_num}: {batch_passed}/{len(batch_results)}")
        print()

    # Final summary
    passed = sum(1 for r in all_results if r["success"])
    total = len(all_results)

    print(f"{'='*60}")
    print(f"FINAL: {passed}/{total} resolved ({100*passed/total:.1f}%)")
    print(f"Feedback mode: {args.feedback}")
    print(f"Batch size: {args.batch_size}, Parallel: {args.parallel}")
    print(f"Evolutions run: {evolve_count}")
    print(f"{'='*60}")

    for r in all_results:
        mark = "✓" if r["success"] else "✗"
        gen = f"gen{r['evolve_generation']}"
        calls = r.get("num_tool_calls", 0)
        turns = r.get("num_turns", 0)
        cum_in = r.get("cumulative_input_tokens", 0)
        max_in = r.get("max_input_tokens_per_turn", 0)
        print(f"  {mark} {r['instance_id']} ({r['elapsed']:.0f}s) [{gen}] "
              f"calls={calls} turns={turns} cum_in={cum_in:,d} max_in={max_in:,d}")

    # Performance by generation
    print(f"\nPerformance by evolution generation:")
    for gen in range(evolve_count + 1):
        gen_results = [r for r in all_results if r["evolve_generation"] == gen]
        gen_passed = sum(1 for r in gen_results if r["success"])
        if gen_results:
            print(f"  gen{gen}: {gen_passed}/{len(gen_results)} ({100*gen_passed/len(gen_results):.0f}%)")

    # Save results
    results_path = output_dir / "results.json"
    results_path.write_text(json.dumps(all_results, indent=2, ensure_ascii=False))
    print(f"\nResults saved to {results_path}")

    # Print metrics dashboard
    try:
        from metrics import compute_metrics, print_dashboard
        print(f"\n{'='*60}")
        print("METRICS DASHBOARD")
        print(f"{'='*60}")
        print_dashboard([output_dir])
    except Exception as e:
        logger.warning("Failed to print metrics dashboard: %s", e)


if __name__ == "__main__":
    main()
