#!/usr/bin/env python3
"""Run final evaluation N times on evolved workspace and report statistics.

Usage:
    # Evolved harness (from artifact)
    uv run python examples/mcp_examples/run_final_eval.py \
        --config examples/configs/metaharness_mcp.yaml \
        --workspace artifacts/mcp_mh_opus46 \
        --trials 5 --workers 20

    # Baseline (seed workspace)
    uv run python examples/mcp_examples/run_final_eval.py \
        --config examples/configs/metaharness_mcp.yaml \
        --workspace seed_workspaces/mcp_mh \
        --trials 5 --workers 20
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.setrecursionlimit(4000)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
os.environ.setdefault("BYPASS_TOOL_CONSENT", "true")

from agent_evolve.agents.mcp_mh.agent import McpMHAgent
from agent_evolve.agents.mcp.key_registry import KeyRegistry
from agent_evolve.agents.mcp.docker_env import McpAtlasContainer, pull_image
from agent_evolve.agents.mcp.mcp_client import McpClientWrapper
from agent_evolve.benchmarks.mcp_atlas.mcp_atlas import McpAtlasBenchmark
from agent_evolve.config import EvolveConfig
from agent_evolve.types import Observation, Task, Trajectory

log = logging.getLogger("final_eval")


def run_one_trial(
    trial_num: int,
    agent: McpMHAgent,
    benchmark: McpAtlasBenchmark,
    tasks: list[Task],
    workers: int,
    docker_image: str,
    key_registry: KeyRegistry,
) -> dict:
    """Run a single trial of all tasks and return metrics."""
    print(f"\n{'=' * 60}")
    print(f"  Trial {trial_num}")
    print(f"{'=' * 60}")

    # Collect env vars for all servers
    env_vars = {}
    if key_registry:
        all_servers = set()
        for task in tasks:
            all_servers.update(task.metadata.get("mcp_server_names", []))
        env_vars = key_registry.get_keys_for_servers(list(all_servers))

    container = None
    client = None
    observations: list[Observation] = []
    errors = 0

    try:
        pull_image(docker_image)
        container = McpAtlasContainer(docker_image, env_vars=env_vars)
        container.start()
        client = McpClientWrapper(base_url=container.base_url)
        log.info("Trial %d: container started at %s", trial_num, container.base_url)

        t0 = time.time()

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_solve_one, agent, benchmark, task, client): task
                for task in tasks
            }
            for future in as_completed(futures):
                task = futures[future]
                try:
                    obs = future.result()
                    if obs is not None:
                        observations.append(obs)
                except Exception as e:
                    errors += 1
                    log.error("Trial %d task %s failed: %s", trial_num, task.id, e)

        elapsed = time.time() - t0

    finally:
        if client:
            client.close()
        if container:
            try:
                container.stop()
            except Exception:
                pass

    total = len(observations)
    passed = sum(1 for o in observations if o.feedback.success)
    scores = [o.feedback.score for o in observations]
    avg_score = sum(scores) / len(scores) if scores else 0.0

    result = {
        "trial": trial_num,
        "total": total,
        "passed": passed,
        "pass_rate": passed / total if total else 0.0,
        "avg_score": avg_score,
        "errors": errors,
        "elapsed_sec": elapsed,
    }

    print(f"  Trial {trial_num}: {passed}/{total} passed ({result['pass_rate']:.1%}), "
          f"avg_score={avg_score:.3f}, time={elapsed:.0f}s")

    return result


def _solve_one(
    agent: McpMHAgent,
    benchmark: McpAtlasBenchmark,
    task: Task,
    client: McpClientWrapper,
) -> Observation | None:
    try:
        trajectory = agent.solve(task, shared_client=client)
        feedback = benchmark.evaluate(task, trajectory)
        return Observation(task=task, trajectory=trajectory, feedback=feedback)
    except Exception as e:
        log.error("Task %s error: %s", task.id, e)
        return None


def main():
    p = argparse.ArgumentParser(description="Run N-trial final evaluation")
    p.add_argument("--config", type=str, required=True)
    p.add_argument("--workspace", type=str, required=True,
                   help="Path to evolved workspace")
    p.add_argument("--trials", type=int, default=5,
                   help="Number of trials (default: 5)")
    p.add_argument("--workers", type=int, default=20)
    p.add_argument("--output", type=str, default=None,
                   help="Output JSON path (default: <workspace>/final_eval_Ntrials.json)")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("final_eval").setLevel(logging.INFO)
    for n in ("botocore", "urllib3", "httpcore", "httpx",
              "strands.models", "strands.tools", "strands.telemetry",
              "strands.tools.executors"):
        logging.getLogger(n).setLevel(logging.WARNING)

    config = EvolveConfig.from_yaml(args.config)
    solver_model = config.extra.get("solver_model", "us.anthropic.claude-opus-4-6-v1")
    solver_region = config.extra.get("solver_region", "us-west-2")
    solver_max_tokens = config.extra.get("solver_max_tokens", 16384)
    eval_model = config.extra.get("eval_model", "us.anthropic.claude-opus-4-6-v1")
    docker_image = config.extra.get("docker_image", "ghcr.io/scaleapi/mcp-atlas:latest")

    if config.extra.get("eval_use_litellm", True):
        os.environ.setdefault("EVAL_USE_LITELLM", "true")

    workspace_dir = Path(args.workspace)
    key_registry = KeyRegistry()

    agent = McpMHAgent(
        workspace_dir=workspace_dir,
        model_id=solver_model,
        region=solver_region,
        max_tokens=solver_max_tokens,
        docker_image=docker_image,
        key_registry=key_registry,
    )

    benchmark = McpAtlasBenchmark(
        holdout_ratio=0.0,
        shuffle=False,
        eval_model_id=eval_model,
        use_litellm=config.extra.get("eval_use_litellm", False),
    )

    all_tasks = benchmark.get_tasks(split="train", limit=10000, key_registry=key_registry)
    log.info("Loaded %d tasks", len(all_tasks))

    print(f"\n{'=' * 60}")
    print(f"  Final Evaluation: {args.trials} trials x {len(all_tasks)} tasks")
    print(f"  Workspace: {workspace_dir}")
    print(f"  Solver:    {solver_model}")
    print(f"  Judge:     {eval_model}")
    print(f"  Workers:   {args.workers}")
    print(f"{'=' * 60}")

    all_results = []
    global_t0 = time.time()

    for t in range(1, args.trials + 1):
        result = run_one_trial(
            t, agent, benchmark, all_tasks,
            args.workers, docker_image, key_registry,
        )
        all_results.append(result)

    total_elapsed = time.time() - global_t0

    # Aggregate
    pass_rates = [r["pass_rate"] for r in all_results]
    avg_scores = [r["avg_score"] for r in all_results]

    summary = {
        "num_trials": args.trials,
        "num_tasks": len(all_tasks),
        "per_trial": all_results,
        "pass_rate_mean": statistics.mean(pass_rates),
        "pass_rate_std": statistics.stdev(pass_rates) if len(pass_rates) > 1 else 0.0,
        "avg_score_mean": statistics.mean(avg_scores),
        "avg_score_std": statistics.stdev(avg_scores) if len(avg_scores) > 1 else 0.0,
        "total_wall_time_sec": total_elapsed,
        "solver_model": solver_model,
        "eval_model": eval_model,
        "workspace": str(workspace_dir),
    }

    print(f"\n{'=' * 60}")
    print(f"  FINAL RESULTS ({args.trials} trials)")
    print(f"{'=' * 60}")
    print(f"  Pass Rate: {summary['pass_rate_mean']:.1%} ± {summary['pass_rate_std']:.1%}")
    print(f"  Avg Score: {summary['avg_score_mean']:.3f} ± {summary['avg_score_std']:.3f}")
    per_trial = [f"{r['pass_rate']:.1%}" for r in all_results]
    print(f"  Per trial: {per_trial}")
    print(f"  Wall time: {total_elapsed / 3600:.1f} hours")
    print(f"{'=' * 60}")

    output_path = args.output or str(workspace_dir / f"final_eval_{args.trials}trials.json")
    Path(output_path).write_text(json.dumps(summary, indent=2))
    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    main()
