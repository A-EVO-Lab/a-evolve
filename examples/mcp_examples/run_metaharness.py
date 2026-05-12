#!/usr/bin/env python3
"""MetaHarness experiment on MCP-Atlas benchmark.

Implements the Meta-Harness search loop (Lee et al., 2026, arXiv:2603.28052)
adapted for MCP tool-calling tasks:

  Phase 0 — Baseline: solve all tasks with seed workspace (no evolution)
  Phase 1 — Evolution: N cycles of MetaHarness (proposer -> validate -> eval -> archive)
  Phase 2 — Final eval: solve all tasks with evolved workspace

Usage:
    # Full experiment (all 3 phases)
    uv run python examples/mcp_examples/run_metaharness.py \\
        --config examples/configs/metaharness_mcp.yaml \\
        --run-name opus_v1

    # Quick smoke test (2 tasks, 1 cycle)
    uv run python examples/mcp_examples/run_metaharness.py \\
        --config examples/configs/metaharness_mcp.yaml \\
        --run-name test --eval-sample-size 2 --max-cycles 1 --workers 1

    # Budget run (20-task subsample during search)
    uv run python examples/mcp_examples/run_metaharness.py \\
        --config examples/configs/metaharness_mcp.yaml \\
        --run-name budget_v1 --eval-sample-size 20
"""
from __future__ import annotations

import argparse
from typing import Callable
import json
import logging
import os
import shutil
import sys
import time

sys.setrecursionlimit(10000)
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
os.environ.setdefault("BYPASS_TOOL_CONSENT", "true")

from agent_evolve.agents.mcp_mh.agent import McpMHAgent
from agent_evolve.agents.mcp.key_registry import KeyRegistry
from agent_evolve.agents.mcp.docker_env import McpAtlasContainer, pull_image
from agent_evolve.agents.mcp.mcp_client import McpClientWrapper
from agent_evolve.algorithms.meta_harness import MetaHarnessEngine
from agent_evolve.benchmarks.mcp_atlas.mcp_atlas import McpAtlasBenchmark
from agent_evolve.config import EvolveConfig
from agent_evolve.engine.history import EvolutionHistory
from agent_evolve.engine.observer import Observer
from agent_evolve.engine.trial import TrialRunner
from agent_evolve.engine.versioning import VersionControl
from agent_evolve.types import CycleRecord, Feedback, Observation, Task, Trajectory

log = logging.getLogger("metaharness_mcp")


# ---------------------------------------------------------------------------
# Parallel TrialRunner — wraps TrialRunner with ThreadPoolExecutor
# ---------------------------------------------------------------------------

class ParallelMcpTrialRunner(TrialRunner):
    """TrialRunner that evaluates MCP tasks in parallel.

    Manages a shared MCP-Atlas Docker container and client so tasks
    don't each start their own container (expensive startup).
    """

    def __init__(
        self,
        agent: McpMHAgent,
        benchmark: McpAtlasBenchmark,
        max_workers: int = 5,
        docker_image: str = "ghcr.io/scaleapi/mcp-atlas:latest",
        key_registry: KeyRegistry | None = None,
    ):
        super().__init__(agent, benchmark)
        self.max_workers = max_workers
        self.docker_image = docker_image
        self.key_registry = key_registry

    def run_tasks(self, tasks: list[Task]) -> list[Observation]:
        if len(tasks) <= 1 or self.max_workers <= 1:
            return super().run_tasks(tasks)

        results: list[Observation] = []
        errors = 0

        # Start shared container
        env_vars = {}
        if self.key_registry:
            # Collect all keys for all servers across all tasks
            all_servers = set()
            for task in tasks:
                all_servers.update(task.metadata.get("mcp_server_names", []))
            env_vars = self.key_registry.get_keys_for_servers(list(all_servers))

        container = None
        client = None
        try:
            if self.docker_image:
                pull_image(self.docker_image)
                container = McpAtlasContainer(self.docker_image, env_vars=env_vars)
                container.start()
                client = McpClientWrapper(base_url=container.base_url)
                log.info("Shared MCP-Atlas container started at %s", container.base_url)

            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                futures = {
                    pool.submit(self._run_one, task, client): task
                    for task in tasks
                }
                for future in as_completed(futures):
                    task = futures[future]
                    try:
                        obs = future.result()
                        if obs is not None:
                            results.append(obs)
                    except Exception as e:
                        errors += 1
                        log.error("Task %s failed: %s", task.id, e)

        finally:
            if client:
                client.close()
            if container:
                try:
                    container.stop()
                except Exception:
                    pass

        if errors:
            log.warning("%d/%d tasks failed during parallel eval", errors, len(tasks))
        return results

    def _run_one(self, task: Task, shared_client: McpClientWrapper | None = None) -> Observation | None:
        try:
            trajectory = self._agent.solve(task, shared_client=shared_client)
            feedback = self._benchmark.evaluate(task, trajectory)
            return Observation(task=task, trajectory=trajectory, feedback=feedback)
        except Exception as e:
            log.error("Task %s error: %s", task.id, e)
            return None


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def compute_metrics(observations: list[Observation]) -> dict:
    total = len(observations)
    passed = sum(1 for o in observations if o.feedback.success)
    by_difficulty = defaultdict(lambda: {"total": 0, "passed": 0})
    by_category = defaultdict(lambda: {"total": 0, "passed": 0})

    scores = []
    for o in observations:
        diff = o.task.metadata.get("difficulty", "unknown")
        cat = o.task.metadata.get("category", "unknown")
        by_difficulty[diff]["total"] += 1
        by_category[cat]["total"] += 1
        scores.append(o.feedback.score)
        if o.feedback.success:
            by_difficulty[diff]["passed"] += 1
            by_category[cat]["passed"] += 1

    return {
        "total": total,
        "passed": passed,
        "pass_rate": passed / total if total > 0 else 0.0,
        "avg_score": sum(scores) / len(scores) if scores else 0.0,
        "by_difficulty": dict(by_difficulty),
        "by_category": dict(by_category),
    }


def print_metrics(label: str, metrics: dict) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print(f"{'=' * 60}")
    print(f"  Total: {metrics['total']}  Passed: {metrics['passed']}  "
          f"Rate: {metrics['pass_rate']:.1%}  Avg Score: {metrics['avg_score']:.3f}")

    if metrics["by_difficulty"]:
        print(f"\n  By Difficulty:")
        for diff in sorted(metrics["by_difficulty"]):
            d = metrics["by_difficulty"][diff]
            if d:
                rate = d["passed"] / d["total"] if d["total"] > 0 else 0.0
                print(f"    {diff:<15} {d['passed']}/{d['total']} ({rate:.1%})")

    if metrics["by_category"]:
        print(f"\n  By Category:")
        cats = sorted(metrics["by_category"].items(),
                      key=lambda x: x[1]["passed"] / max(x[1]["total"], 1),
                      reverse=True)
        for cat, d in cats[:20]:  # Top 20
            rate = d["passed"] / d["total"] if d["total"] > 0 else 0.0
            print(f"    {cat:<35} {d['passed']}/{d['total']} ({rate:.1%})")
    print()


# ---------------------------------------------------------------------------
# Phase 0 — Baseline evaluation
# ---------------------------------------------------------------------------

def run_baseline(
    agent: McpMHAgent,
    benchmark: McpAtlasBenchmark,
    trial: ParallelMcpTrialRunner,
    observer: Observer,
    versioning: VersionControl,
    history: EvolutionHistory,
    tasks: list[Task],
) -> list[Observation]:
    """Solve all tasks with the seed workspace. No evolution."""
    print(f"\n{'=' * 60}")
    print(f"  PHASE 0: Baseline Evaluation ({len(tasks)} tasks)")
    print(f"{'=' * 60}\n")

    t0 = time.time()
    observations = trial.run_tasks(tasks)
    elapsed = time.time() - t0

    batch_path = observer.collect(observations)

    score = sum(o.feedback.score for o in observations) / len(observations) if observations else 0.0
    print(f"Baseline: {score:.3f} ({sum(1 for o in observations if o.feedback.success)}"
          f"/{len(observations)} passed) in {elapsed:.0f}s")

    versioning.commit(
        message=f"baseline: score={score:.3f}",
        tag="baseline",
    )

    record = CycleRecord(
        cycle=0,
        score=score,
        mutated=False,
        engine_name="baseline",
        summary=f"Baseline evaluation: {score:.3f}",
        observation_batch=batch_path.name,
    )
    history.record_cycle(record)

    metrics = compute_metrics(observations)
    print_metrics("Baseline Results", metrics)

    return observations


# ---------------------------------------------------------------------------
# Phase 1 — MetaHarness evolution
# ---------------------------------------------------------------------------

def run_evolution(
    engine: MetaHarnessEngine,
    agent: McpMHAgent,
    trial: ParallelMcpTrialRunner,
    observer: Observer,
    versioning: VersionControl,
    history: EvolutionHistory,
    observations: list[Observation],
    max_cycles: int,
    config: EvolveConfig,
    tasks: list[Task] | None = None,
    eval_factory: Callable | None = None,
    start_cycle: int = 1,
) -> float:
    """Run the MetaHarness search loop."""
    print(f"\n{'=' * 60}")
    print(f"  PHASE 1: MetaHarness Evolution (cycles {start_cycle}-{max_cycles}, "
          f"k={engine.num_candidates})")
    print(f"{'=' * 60}\n")

    score_history = history.get_score_curve()
    best_score = max(score_history) if score_history else 0.0

    for cycle in range(start_cycle, max_cycles + 1):
        cycle_t0 = time.time()
        print(f"\n--- Cycle {cycle}/{max_cycles} "
              f"(best so far: {best_score:.3f}) ---")

        step_result = engine.step(
            workspace=agent.workspace,
            observations=observations,
            history=history,
            trial=trial,
            tasks=tasks,
            eval_factory=eval_factory,
        )

        cycle_elapsed = time.time() - cycle_t0
        cycle_score = step_result.metadata.get("best_score", 0.0)
        best_score = max(best_score, cycle_score)

        tag = f"evo-{cycle}"
        if step_result.mutated:
            versioning.commit(
                message=f"evo-{cycle}: {step_result.summary}",
                tag=tag,
            )
        else:
            versioning.commit(
                message=f"evo-{cycle}: no mutation",
                tag=tag,
            )

        record = CycleRecord(
            cycle=cycle,
            score=cycle_score,
            mutated=step_result.mutated,
            engine_name="MetaHarnessEngine",
            summary=step_result.summary,
            metadata=step_result.metadata,
        )
        history.record_cycle(record)

        agent.reload_from_fs()
        engine.on_cycle_end(accepted=step_result.mutated, score=cycle_score)

        _append_history(agent.workspace.root / "evolution", cycle, cycle_score, step_result.mutated)
        _write_metrics(agent.workspace.root / "evolution", history.get_score_curve())

        print(f"Cycle {cycle}: score={cycle_score:.3f} mutated={step_result.mutated} "
              f"({cycle_elapsed:.0f}s) | {step_result.summary}")

    return best_score


# ---------------------------------------------------------------------------
# Phase 2 — Final evaluation
# ---------------------------------------------------------------------------

def _restore_best_candidate(work_dir: Path, agent: McpMHAgent) -> None:
    """Select the best candidate from the archive and restore its snapshot.

    Implements the paper's "return Pareto frontier" step: scan all candidates,
    compute the Pareto frontier across (score↑, cost↓), select the highest-
    scoring candidate on the frontier, and copy its snapshot/ files into the
    workspace so Phase 2 evaluates the best harness rather than the last one.
    """
    candidates_dir = work_dir / "evolution" / "candidates"
    if not candidates_dir.exists():
        log.warning("No candidates directory found at %s — skipping archive selection", candidates_dir)
        return

    # Load all candidate scores
    candidates = []
    for scores_path in sorted(candidates_dir.glob("*/scores.json")):
        try:
            data = json.loads(scores_path.read_text())
            if not data.get("valid", True):
                continue
            candidates.append({
                "label": scores_path.parent.name,
                "score": data.get("score", 0.0),
                "cost": data.get("cost", 0),
                "snapshot_dir": scores_path.parent / "snapshot",
            })
        except (json.JSONDecodeError, KeyError) as exc:
            log.warning("Skipping %s: %s", scores_path, exc)

    if not candidates:
        log.warning("No valid candidates found in archive — skipping")
        return

    # Compute Pareto frontier (maximize score, minimize cost)
    frontier = []
    for c in candidates:
        dominated = False
        for other in candidates:
            if other is c:
                continue
            if (other["score"] >= c["score"]
                    and other["cost"] <= c["cost"]
                    and (other["score"] > c["score"]
                         or other["cost"] < c["cost"])):
                dominated = True
                break
        if not dominated:
            frontier.append(c)

    best = max(frontier, key=lambda c: c["score"])
    snapshot_dir = best["snapshot_dir"]

    print(f"\n  Archive selection: {best['label']} "
          f"(score={best['score']:.3f}, cost={best['cost']}) "
          f"from {len(candidates)} candidates ({len(frontier)} on Pareto frontier)")

    if not snapshot_dir.exists():
        log.error("Snapshot directory %s does not exist", snapshot_dir)
        return

    # Restore snapshot files into workspace
    workspace_root = agent.workspace.root
    for item in snapshot_dir.iterdir():
        dest = workspace_root / item.name
        if item.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)
    log.info("Restored snapshot from %s to %s", snapshot_dir, workspace_root)

    # Reload agent state from the restored workspace
    agent.reload_from_fs()
    print(f"  Agent reloaded from best candidate's snapshot\n")


def run_final_eval(
    agent: McpMHAgent,
    trial: ParallelMcpTrialRunner,
    observer: Observer,
    tasks: list[Task],
) -> list[Observation]:
    """Solve all tasks with the evolved workspace."""
    print(f"\n{'=' * 60}")
    print(f"  PHASE 2: Final Evaluation ({len(tasks)} tasks)")
    print(f"{'=' * 60}\n")

    t0 = time.time()
    observations = trial.run_tasks(tasks)
    elapsed = time.time() - t0

    observer.collect(observations)

    score = sum(o.feedback.score for o in observations) / len(observations) if observations else 0.0
    print(f"Final: {score:.3f} ({sum(1 for o in observations if o.feedback.success)}"
          f"/{len(observations)} passed) in {elapsed:.0f}s")

    metrics = compute_metrics(observations)
    print_metrics("Final Evolved Results", metrics)

    return observations


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _append_history(evolution_dir: Path, cycle: int, score: float, mutated: bool) -> None:
    history_file = evolution_dir / "history.jsonl"
    entry = {
        "cycle": cycle,
        "score": score,
        "mutated": mutated,
        "timestamp": datetime.now().isoformat(),
    }
    with open(history_file, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _load_resume_state(evolution_dir: Path) -> tuple[int, list[float]]:
    """Load state from a previous run for resuming.

    Returns (last_completed_cycle, score_curve).
    Uses two sources: history.jsonl for recorded cycles, and candidates/
    directory as fallback (since history.jsonl may be incomplete).
    """
    # Source 1: history.jsonl
    history_scores: dict[int, float] = {}
    history_file = evolution_dir / "history.jsonl"
    if history_file.exists():
        for line in history_file.read_text().strip().split("\n"):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                history_scores[entry["cycle"]] = entry["score"]
            except (json.JSONDecodeError, KeyError):
                continue

    # Source 2: candidates directory (more reliable — always written by engine)
    candidates_dir = evolution_dir / "candidates"
    candidate_cycles: dict[int, float] = {}
    if candidates_dir.exists():
        for scores_path in candidates_dir.glob("*/scores.json"):
            label = scores_path.parent.name  # e.g. "cycle_003_cand_1"
            try:
                cycle_num = int(label.split("_")[1])
                data = json.loads(scores_path.read_text())
                score = data.get("score", 0.0)
                # Keep the best candidate score per cycle
                if cycle_num not in candidate_cycles or score > candidate_cycles[cycle_num]:
                    candidate_cycles[cycle_num] = score
            except (ValueError, json.JSONDecodeError, KeyError, IndexError):
                continue

    # Merge: use history for baseline (cycle 0), candidates for evolution cycles
    all_scores: dict[int, float] = {}
    all_scores.update(history_scores)
    for cycle_num, score in candidate_cycles.items():
        if cycle_num not in all_scores:
            all_scores[cycle_num] = score

    if not all_scores:
        return 0, []

    last_cycle = max(all_scores.keys())
    score_curve = [all_scores.get(i, 0.0) for i in range(min(all_scores.keys()), last_cycle + 1)]
    return last_cycle, score_curve


def _export_artifact(
    work_dir: Path,
    run_name: str,
    baseline_metrics: dict | None,
    final_metrics: dict | None,
    config_path: str,
    meta: dict,
) -> Path:
    """Export the best candidate's snapshot as a clean artifact folder.

    Outputs to artifacts/mcp_mh_<run_name>/ under the repo root.
    """
    # Find repo root (parent of examples/)
    repo_root = Path(__file__).resolve().parent.parent.parent
    artifact_dir = repo_root / "artifacts" / f"mcp_mh_{run_name}"

    # Clean previous artifact if any
    if artifact_dir.exists():
        shutil.rmtree(artifact_dir)
    artifact_dir.mkdir(parents=True)

    # Find best candidate from archive
    candidates_dir = work_dir / "evolution" / "candidates"
    best_label, best_score, best_cost, best_snapshot = None, -1.0, 0, None
    if candidates_dir.exists():
        for scores_path in candidates_dir.glob("*/scores.json"):
            try:
                data = json.loads(scores_path.read_text())
                if not data.get("valid", True):
                    continue
                score = data.get("score", 0.0)
                if score > best_score:
                    best_score = score
                    best_cost = data.get("cost", 0)
                    best_label = scores_path.parent.name
                    best_snapshot = scores_path.parent / "snapshot"
            except (json.JSONDecodeError, KeyError):
                continue

    if not best_snapshot or not best_snapshot.exists():
        log.warning("No valid snapshot found — artifact not created")
        return artifact_dir

    # Copy snapshot contents to artifact root
    for item in best_snapshot.iterdir():
        dest = artifact_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)

    # Write results.json
    results = {
        "best_candidate": best_label,
        "best_search_score": best_score,
        "best_search_cost": best_cost,
        "baseline": baseline_metrics,
        "final_eval": final_metrics,
        **meta,
    }
    (artifact_dir / "results.json").write_text(json.dumps(results, indent=2))

    print(f"\n  Artifact exported to {artifact_dir}")
    return artifact_dir


def _write_metrics(evolution_dir: Path, scores: list[float]) -> None:
    metrics_file = evolution_dir / "metrics.json"
    metrics = {
        "cycles_completed": len(scores),
        "latest_score": scores[-1] if scores else 0.0,
        "best_score": max(scores) if scores else 0.0,
        "avg_score": sum(scores) / len(scores) if scores else 0.0,
    }
    metrics_file.write_text(json.dumps(metrics, indent=2))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(
        description="MetaHarness experiment on MCP-Atlas benchmark"
    )
    p.add_argument("--config", type=str, required=True,
                   help="Path to YAML config (e.g. examples/configs/metaharness_mcp.yaml)")
    p.add_argument("--run-name", type=str, required=True,
                   help="Experiment run name (used for work-dir naming)")
    p.add_argument("--phase", type=str, default="all",
                   choices=["0", "1", "2", "all"],
                   help="Which phase to run (default: all)")
    p.add_argument("--seed-workspace", type=str, default="seed_workspaces/mcp_mh",
                   help="Seed workspace to copy")
    p.add_argument("--work-dir", type=str, default=None,
                   help="Workspace directory (default: evolution_workdir/mcp_mh_<run-name>)")

    # Overrides
    p.add_argument("--workers", type=int, default=None,
                   help="Override solve_workers from config")
    p.add_argument("--max-cycles", type=int, default=None,
                   help="Override max_cycles from config")
    p.add_argument("--eval-sample-size", type=int, default=None,
                   help="Override eval_sample_size (0=all tasks, >0=subsample)")
    p.add_argument("--task-limit", type=int, default=None,
                   help="Limit total number of tasks loaded (for smoke tests)")
    p.add_argument("--solver-model", type=str, default=None,
                   help="Override solver model")
    p.add_argument("--docker-image", type=str, default=None,
                   help="Override MCP-Atlas Docker image")
    p.add_argument("--no-key-filter", action="store_true",
                   help="Don't filter tasks by available API keys")
    p.add_argument("--resume", action="store_true",
                   help="Resume experiment from last completed cycle (skip baseline, continue evolution)")
    args = p.parse_args()

    # Logging — console shows only experiment-level progress (WARNING+),
    # full debug log goes to evolution/experiment.log
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Our own loggers stay at INFO on console
    for name in ("metaharness_mcp", "agent_evolve.algorithms.meta_harness"):
        logging.getLogger(name).setLevel(logging.INFO)
    # Suppress noisy libraries
    for n in ("botocore", "urllib3", "httpcore", "httpx",
              "strands.models", "strands.tools", "strands.telemetry",
              "strands.tools.executors"):
        logging.getLogger(n).setLevel(logging.WARNING)

    # Load config
    config = EvolveConfig.from_yaml(args.config)

    # Apply CLI overrides
    workers = args.workers or config.extra.get("solve_workers", 5)
    max_cycles = args.max_cycles or config.max_cycles
    solver_model = args.solver_model or config.extra.get(
        "solver_model", "us.anthropic.claude-sonnet-4-20250514-v1:0"
    )
    solver_region = config.extra.get("solver_region", "us-west-2")
    solver_max_tokens = config.extra.get("solver_max_tokens", 16384)
    docker_image = args.docker_image or config.extra.get(
        "docker_image", "ghcr.io/scaleapi/mcp-atlas:latest"
    )

    if args.eval_sample_size is not None:
        config.extra["eval_sample_size"] = args.eval_sample_size

    # Set eval LLM env vars from config
    eval_model = config.extra.get("eval_model", "gemini/gemini-2.5-pro")
    if config.extra.get("eval_use_litellm", True):
        os.environ.setdefault("EVAL_USE_LITELLM", "true")

    # Work directory
    work_dir = Path(args.work_dir) if args.work_dir else Path(f"evolution_workdir/mcp_mh_{args.run_name}")
    seed_dir = Path(args.seed_workspace)

    if not work_dir.exists() and seed_dir.exists():
        work_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(seed_dir, work_dir)
        log.info("Copied seed workspace %s -> %s", seed_dir, work_dir)

    # Key registry
    key_registry = KeyRegistry()

    # Initialize components
    agent = McpMHAgent(
        workspace_dir=work_dir,
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
        use_litellm=config.extra.get("eval_use_litellm", True),
    )

    trial = ParallelMcpTrialRunner(
        agent, benchmark,
        max_workers=workers,
        docker_image=docker_image,
        key_registry=key_registry,
    )

    engine = MetaHarnessEngine(config)

    evolution_dir = work_dir / "evolution"
    evolution_dir.mkdir(parents=True, exist_ok=True)

    # File handler — full debug log to evolution/experiment.log
    file_handler = logging.FileHandler(evolution_dir / "experiment.log")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S",
    ))
    logging.getLogger().addHandler(file_handler)

    observer = Observer(evolution_dir)
    versioning = VersionControl(work_dir)
    versioning.init()
    history = EvolutionHistory(observer, versioning)

    # Load tasks
    task_limit = args.task_limit or 10000
    if args.no_key_filter:
        all_tasks = benchmark.get_tasks(split="train", limit=task_limit)
    else:
        all_tasks = benchmark.get_tasks(split="train", limit=task_limit, key_registry=key_registry)

    log.info("Loaded %d runnable tasks (key_filter=%s)", len(all_tasks), not args.no_key_filter)

    print(f"\n{'=' * 60}")
    print(f"  MetaHarness MCP Experiment: {args.run_name}")
    print(f"  Workspace:    {work_dir}")
    print(f"  Tasks:        {len(all_tasks)}")
    print(f"  Max cycles:   {max_cycles}")
    print(f"  Candidates/cycle: {engine.num_candidates}")
    print(f"  Eval sample:  {config.extra.get('eval_sample_size', 0) or 'all'}")
    print(f"  Solver:       {solver_model}")
    print(f"  Proposer:     {engine.model}")
    print(f"  Eval judge:   {eval_model}")
    print(f"  Docker:       {docker_image}")
    print(f"  Workers:      {workers}")
    print(f"  Phase:        {args.phase}")
    print(f"{'=' * 60}")

    run_phases = args.phase
    global_t0 = time.time()
    observations: list[Observation] = []
    start_cycle = 1

    # Resume: load state from previous run
    if args.resume:
        last_cycle, prev_scores = _load_resume_state(evolution_dir)
        if last_cycle > 0:
            start_cycle = last_cycle + 1
            # Rebuild history cycle records so engines see prior scores
            for i, score in enumerate(prev_scores):
                history.record_cycle(CycleRecord(
                    cycle=i if i == 0 else i,  # cycle 0 = baseline
                    score=score,
                    mutated=i > 0,
                    engine_name="baseline" if i == 0 else "MetaHarnessEngine",
                    summary=f"[resumed] cycle {i}: score={score:.3f}",
                ))
            print(f"\n  RESUMING from cycle {start_cycle} "
                  f"(loaded {len(prev_scores)} prior scores, "
                  f"best={max(prev_scores):.3f})")
        else:
            print("\n  --resume: no prior state found, starting fresh")

    try:
        # Phase 0: Baseline (skip if resuming with prior state)
        if run_phases in ("0", "all") and start_cycle <= 1:
            observations = run_baseline(
                agent, benchmark, trial, observer, versioning, history, all_tasks,
            )

        # Phase 1: Evolution
        if run_phases in ("1", "all") and start_cycle <= max_cycles:
            def _eval_factory(workspace_path: Path) -> ParallelMcpTrialRunner:
                eval_agent = McpMHAgent(
                    workspace_dir=workspace_path,
                    model_id=solver_model,
                    region=solver_region,
                    max_tokens=solver_max_tokens,
                    docker_image=docker_image,
                    key_registry=key_registry,
                )
                return ParallelMcpTrialRunner(
                    eval_agent, benchmark,
                    max_workers=workers,
                    docker_image=docker_image,
                    key_registry=key_registry,
                )

            best_score = run_evolution(
                engine, agent, trial, observer, versioning, history,
                observations, max_cycles, config, tasks=all_tasks,
                eval_factory=_eval_factory,
                start_cycle=start_cycle,
            )

        # Select best candidate from archive before final eval (paper: "return Pareto frontier")
        if run_phases in ("2", "all"):
            _restore_best_candidate(work_dir, agent)

            final_obs = run_final_eval(agent, trial, observer, all_tasks)
            final_metrics = compute_metrics(final_obs)

            baseline_scores = history.get_score_curve()
            baseline_metrics = None
            if baseline_scores:
                baseline = baseline_scores[0]
                final_score = sum(o.feedback.score for o in final_obs) / len(final_obs) if final_obs else 0.0
                print(f"\n  Baseline: {baseline:.3f} -> Final: {final_score:.3f} "
                      f"(delta: {final_score - baseline:+.3f})")

            # Compute baseline metrics from first observation batch
            baseline_batch = evolution_dir / "observations" / "batch_0001.jsonl"
            if baseline_batch.exists():
                bl_obs_raw = [json.loads(l) for l in baseline_batch.read_text().strip().split("\n") if l.strip()]
                bl_total = len(bl_obs_raw)
                bl_passed = sum(1 for o in bl_obs_raw if o.get("success", False))
                bl_avg = sum(o.get("score", 0) for o in bl_obs_raw) / bl_total if bl_total else 0
                baseline_metrics = {"total": bl_total, "passed": bl_passed,
                                    "pass_rate": bl_passed / bl_total if bl_total else 0,
                                    "avg_score": bl_avg}

            # Export clean artifact
            _export_artifact(
                work_dir=work_dir,
                run_name=args.run_name,
                baseline_metrics=baseline_metrics,
                final_metrics=final_metrics,
                config_path=args.config,
                meta={
                    "solver_model": solver_model,
                    "proposer_model": engine.model,
                    "eval_model": eval_model,
                    "max_cycles": max_cycles,
                    "num_candidates": engine.num_candidates,
                    "num_tasks": len(all_tasks),
                    "timestamp": datetime.now().isoformat(),
                },
            )

    except KeyboardInterrupt:
        print("\n\nInterrupted! Cleaning up...")

    total_elapsed = time.time() - global_t0
    print(f"\nTotal wall time: {total_elapsed / 3600:.1f} hours")

    # Write final summary
    summary_path = evolution_dir / "experiment_summary.json"
    summary = {
        "run_name": args.run_name,
        "config": args.config,
        "solver_model": solver_model,
        "proposer_model": engine.model,
        "eval_model": eval_model,
        "max_cycles": max_cycles,
        "num_candidates": engine.num_candidates,
        "eval_sample_size": config.extra.get("eval_sample_size", 0),
        "num_tasks": len(all_tasks),
        "score_history": history.get_score_curve(),
        "total_wall_time_sec": total_elapsed,
        "timestamp": datetime.now().isoformat(),
    }
    summary_path.write_text(json.dumps(summary, indent=2))
    print(f"Summary saved to {summary_path}")


if __name__ == "__main__":
    main()
