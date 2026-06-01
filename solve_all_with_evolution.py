#!/usr/bin/env python3
"""Solve all benchmark tasks with periodic evolution between batches.

Generic orchestrator that works with any benchmark backend. Every task gets
a trajectory + results.jsonl entry, matching the baseline output format.

Flow:
  1. Load all N tasks (deterministic order)
  2. For each batch of batch_size tasks:
     a. Solve all tasks in parallel (using current workspace state)
     b. Save outputs + results.jsonl per task
     c. Trigger one evolution cycle using batch observations
     d. Snapshot workspace, reload agent
  3. Write final results.json + evolved_workspace/

Usage:
    python solve_all_with_evolution.py --benchmark swe --batch-size 10
    python solve_all_with_evolution.py --benchmark mcp --batch-size 20
"""
from __future__ import annotations

import argparse
import importlib
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed, TimeoutError
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("BYPASS_TOOL_CONSENT", "true")


# ── Benchmark backend interface ───────────────────────────────────────
#
# Each backend module (backends/<name>.py) must expose:
#
#   setup(args) -> dict with keys:
#       "agent":      BaseAgent instance (for workspace + evolution)
#       "benchmark":  BenchmarkAdapter instance
#       "executor":   "process" | "thread"  (parallelism strategy)
#       "cleanup":    callable() or None
#
#   solve_one(task_dict: dict, args_dict: dict) -> dict
#       Result dict with at least: instance_id, success, score, detail, elapsed
#
#   build_prompts(agent, tasks) -> dict
#       Extra args to pass to solve_one (e.g. system_prompt, user_prompts)
#

def _load_backend(name: str):
    """Import a benchmark solver module by name.

    Canonical layout: ``agent_evolve/agents/<name>/solver.py`` exposes
    ``setup()``, ``build_prompts()``, ``solve_one()``.
    """
    return importlib.import_module(f"agent_evolve.agents.{name}.solver")


# ── Generic helpers ───────────────────────────────────────────────────

def _load_done_ids(out_dir: Path) -> tuple[set[str], list[dict]]:
    """Load already-completed results from results.jsonl for resume."""
    done_ids = set()
    existing = []
    jsonl = out_dir / "results.jsonl"
    if jsonl.exists():
        for line in jsonl.read_text().splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                done_ids.add(r["instance_id"])
                existing.append(r)
            except (json.JSONDecodeError, KeyError):
                pass
    return done_ids, existing


def _append_result(out_dir: Path, result: dict):
    with open(out_dir / "results.jsonl", "a") as f:
        f.write(json.dumps(result, default=str) + "\n")


def _enrich_from_partial(
    result: dict,
    out_dir: Path,
    task_id: str,
    *,
    cut_off_reason: str,
) -> None:
    """Merge a worker's partial trajectory snapshot into a cancelled record.

    Solvers persist ``out_dir/partial_{sid}.json`` after every tool call
    (see agent_evolve/agents/_partial_trajectory.py).  When a Future is
    cancelled by the batch deadline or per-task timeout, the on-disk
    snapshot has the real turn count + conversation at the moment of
    cancellation.  This helper reads it and merges those fields into
    the synthetic record so the evolver sees authentic liveness signal
    rather than a fabricated ``turns: 0``.

    Note: does NOT overwrite success/score/feedback fields.  The
    trajectory-only privacy gate in ``Observer.collect`` continues to
    strip those from the JSONL the evolver reads.
    """
    try:
        from agent_evolve.agents._partial_trajectory import read_partial_trajectory
        partial = read_partial_trajectory(out_dir, task_id)
    except Exception:
        partial = None
    result["status"] = "cut_off"
    result["cut_off_reason"] = cut_off_reason
    if partial:
        result["turns"] = int(partial.get("turns", 0))
        result["conversation"] = partial.get("conversation", []) or []
        result["partial_elapsed"] = float(partial.get("elapsed", 0.0))
        result["tool_timings"] = partial.get("tool_timings", []) or []
        result["detail"] = (
            f"Cut off mid-run ({cut_off_reason}): "
            f"{result['turns']} turn(s) completed in "
            f"{result['partial_elapsed']:.0f}s"
        )


# ── Main orchestrator ─────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Solve all benchmark tasks with periodic evolution")
    p.add_argument("--benchmark", type=str, required=True,
                   help="Benchmark name (matches agent_evolve/agents/<name>/solver.py)")
    p.add_argument("--model-id", type=str, default="us.anthropic.claude-sonnet-4-6")
    p.add_argument("--region", type=str, default="us-west-2")
    p.add_argument("--max-tokens", type=int, default=16384)
    p.add_argument("--max-turns", type=int, default=150)
    p.add_argument("--task-timeout", type=int, default=1800)
    p.add_argument("--workers", type=int, default=16)
    p.add_argument("--batch-size", type=int, default=None)
    p.add_argument("--eval", "--no-eval", dest="run_eval",
                   action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--output-dir", type=str, default=None)
    p.add_argument("--limit", type=int, default=0,
                   help="Max tasks (0 = all)")
    p.add_argument("--seed-workspace", type=str, default=None)
    p.add_argument("--split", type=str, default="test",
                   help="Dataset split (default: test; use 'online' for FutureX-Online)")
    p.add_argument("--config", type=str, default=None,
                   help="Evolution config YAML")
    p.add_argument("--trajectory-only", action="store_true", default=False,
                   help="Hide ground-truth labels from evolver (trajectory-only evolution)")
    p.add_argument("--temporal-reveal", action="store_true", default=False,
                   help="Reveal labels per-task iff task.resolution_date <= "
                        "batch creation-date watermark.  Orthogonal to "
                        "--trajectory-only; when set, per-task gate wins.")
    p.add_argument("--solver-temp", type=float, default=None,
                   help="Solver LLM temperature")
    p.add_argument("--evolver-temp", type=float, default=None,
                   help="Evolver LLM temperature (overrides config)")
    p.add_argument("--evolver-model", type=str, default=None,
                   help="Evolver Bedrock model-id (e.g. 'global.anthropic.claude-opus-4-6-v1'); "
                        "overrides the YAML config's evolver_model.")
    p.add_argument("--verbose", action="store_true", default=False,
                   help="Print evolver conversation in real-time during evolution")
    p.add_argument("--navigation", action="store_true", default=False,
                   help="Enable decoupled evolution with strategy tree navigation")
    p.add_argument("--branch-confidence", type=float, default=None,
                   help="Branch confidence threshold (overrides config; -1=never branch)")
    p.add_argument("--adaptation", type=str, default=None,
                   choices=["whole_store", "tree_routing", "retrieval", "agentic_filter"],
                   help="Solve-time adaptation operator. Default (unset) = legacy "
                        "behavior: tree_routing under --navigation, else whole_store.")
    p.add_argument("--no-infra-evo", action="store_true", default=False,
                   help="Disable infra/ evolution (changes will be reverted)")
    p.add_argument("--evolver-prompt", type=str, default=None,
                   help="Path to custom evolver system prompt (.md file)")
    p.add_argument("--stride", type=int, default=1,
                   help="Take every Nth task for temporal spread (smoke tests)")
    # Pass-through for benchmark-specific args
    p.add_argument("--dataset", type=str, default=None)
    p.add_argument("--docker-image", type=str, default=None)
    p.add_argument("--env-file", type=str, default=None)
    args = p.parse_args()

    # Defaults
    if args.output_dir is None:
        args.output_dir = f"results/{args.benchmark}_evolve_full"
    if args.seed_workspace is None:
        args.seed_workspace = f"experiments/{args.benchmark}/seed"
    if args.config is None:
        cfg_path = f"experiments/{args.benchmark}/configs/full_evo.yaml"
        args.config = cfg_path if Path(cfg_path).exists() else None

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    for n in ("botocore", "urllib3", "httpcore", "httpx",
              "strands.models", "strands.tools", "strands.telemetry"):
        logging.getLogger(n).setLevel(logging.WARNING)
    log = logging.getLogger("main")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Load backend ──────────────────────────────────────────────
    backend = _load_backend(args.benchmark)
    env = backend.setup(args)
    agent = env["agent"]
    benchmark = env["benchmark"]
    executor_type = env.get("executor", "process")
    cleanup_fn = env.get("cleanup")

    all_tasks = benchmark.get_tasks(split=args.split, limit=args.limit)
    if args.stride > 1:
        all_tasks = all_tasks[::args.stride]
    log.info("Loaded %d tasks", len(all_tasks))

    # Resume
    done_ids, existing_results = _load_done_ids(out_dir)
    if done_ids:
        log.info("Resuming: %d already done", len(done_ids))
    remaining = [t for t in all_tasks if t.id not in done_ids]
    log.info("Remaining: %d tasks", len(remaining))

    if not remaining:
        results = existing_results
    else:
        from agent_evolve.config import EvolveConfig
        from agent_evolve.engine.observer import Observer
        from agent_evolve.engine.versioning import VersionControl
        from agent_evolve.algorithms.aevolve import AEvolveEngine
        from agent_evolve.types import Feedback, Observation, Trajectory

        # Evolution infrastructure
        ws_dir = agent.workspace.root
        versioning = VersionControl(ws_dir)
        versioning.init()

        config = EvolveConfig.from_yaml(args.config) if args.config else EvolveConfig()
        if args.trajectory_only:
            config.trajectory_only = True
        if args.temporal_reveal:
            config.temporal_reveal = True
        if args.evolver_temp is not None:
            config.evolver_temperature = args.evolver_temp
        if args.evolver_model:
            config.evolver_model = args.evolver_model
        if args.branch_confidence is not None:
            config.branch_confidence_threshold = args.branch_confidence
        if args.no_infra_evo:
            config.evolve_infra = False
        if args.evolver_prompt:
            config.extra["evolver_system_prompt"] = Path(args.evolver_prompt).read_text()
        config.extra.setdefault("region", args.region)
        if args.verbose:
            config.extra["verbose"] = True

        # Observer writes to evolver workspace when structured_evolution
        # is active (separate workspace), else to solver workspace.
        orchestrator_type = config.extra.get("orchestrator", "")
        if orchestrator_type == "structured_evolution":
            from agent_evolve.algorithms.navigation.templates._evolution_workspace import (
                get_evolver_workspace_path,
            )
            evo_dir = get_evolver_workspace_path(ws_dir) / "evolution"
        else:
            evo_dir = ws_dir / "evolution"
        evo_dir.mkdir(parents=True, exist_ok=True)
        observer = Observer(
            evo_dir,
            trajectory_only=config.trajectory_only,
            temporal_reveal=config.temporal_reveal,
        )
        evo_trigger_threshold = config.extra.get("evo_trigger_threshold", 0.0)

        # Navigation (--navigation flag) + plan-driven (config: orchestrator)
        # The plan-driven evolution template can run with or without
        # navigation-based task routing:
        #   --navigation only            -> inline template, branching + routing
        #   orchestrator=plan_driven     -> orchestrated template (no routing
        #                                   unless --navigation is also set)
        #   --navigation + plan_driven   -> orchestrated template + routing
        navigation_enabled = args.navigation or config.navigation_enabled
        # Keep config in sync with CLI flag so downstream templates (which
        # only see config, not args) can check navigation_enabled.
        config.navigation_enabled = navigation_enabled
        orchestrator_type = config.extra.get("orchestrator", "")
        multi_agent = bool(orchestrator_type)
        if navigation_enabled or multi_agent:
            from agent_evolve.algorithms.navigation import NavigationEngine
            if orchestrator_type == "plan_driven":
                mode = "orchestrated"
                log.info("Evolution template: orchestrated (plan-driven)")
                evolver = NavigationEngine(config, mode=mode)
            elif orchestrator_type:
                # Dynamic template: import from templates/<name>.py
                import importlib
                mod = importlib.import_module(
                    f"agent_evolve.algorithms.navigation.templates.{orchestrator_type}")
                log.info("Evolution template: %s (custom)", orchestrator_type)
                evolver = NavigationEngine(config, mode="inline")
                evolver._template = mod.Template(evolver)
            else:
                mode = "inline"
                evolver = NavigationEngine(config, mode=mode)
            # Hand the engine a reference to the observer so its templates
            # can honour trajectory_only / temporal_reveal when composing
            # evolver/planner prompts (inline batch summaries + orchestrated
            # planner task summaries).
            evolver.observer = observer
        else:
            evolver = AEvolveEngine(config)
        strategy_tree = None
        routing_log_path = None
        if navigation_enabled or multi_agent:
            from agent_evolve.types import StrategyTree
            tree_state_path = out_dir / "tree_state.json"
            if tree_state_path.exists():
                strategy_tree = StrategyTree.from_dict(
                    json.loads(tree_state_path.read_text()))
                log.info("Loaded strategy tree: %d branches",
                         len(strategy_tree.branches))
            else:
                strategy_tree = StrategyTree()
            if navigation_enabled:
                routing_log_path = out_dir / "routing_log.jsonl"
                log.info("Navigation ENABLED (routing_log: %s)", routing_log_path)
            else:
                log.info("Multi-agent evolution (no task routing)")

        # Solve-time adaptation operator (pluggable; see protocol/adaptation_registry).
        # --adaptation unset => legacy behavior: tree_routing under navigation,
        # else whole_store. Retrieval operators (retrieval/agentic_filter) build a
        # catalog index lazily and carry a per-task harness filter (no workspace churn).
        from agent_evolve.protocol.adaptation_registry import build_adapter
        adapter = build_adapter(
            args.adaptation,
            evolver=evolver, strategy_tree=strategy_tree,
            navigation_enabled=navigation_enabled,
            workspace=agent.workspace, out_dir=out_dir, config=config,
        )
        log.info("Adaptation operator: %s", getattr(adapter, "name", type(adapter).__name__))
        # Retrieval operators (M1/M2) expose task_filter and need build_prompts
        # to emit raw harness pieces for worker-side per-task filtering. This
        # flag is read by build_prompts; absent for M0/M4 -> no pieces emitted.
        if hasattr(adapter, "task_filter"):
            agent._emit_harness_pieces = True

        # Batching
        batch_size = args.batch_size or config.batch_size

        # Dynamic freeze/start from percentage of total tasks
        total_tasks = len(all_tasks)
        freeze_pct = config.extra.get("evo_freeze_after_pct", 0)
        start_pct = config.extra.get("evo_start_after_pct", 0)
        if freeze_pct > 0:
            config.max_cycles = math.ceil(total_tasks * freeze_pct / 100 / batch_size)
            log.info("evo_freeze_after_pct=%.0f%% -> max_cycles=%d (from %d tasks, batch_size=%d)",
                     freeze_pct, config.max_cycles, total_tasks, batch_size)
        evo_start_cycle = 0
        if start_pct > 0:
            evo_start_cycle = math.ceil(total_tasks * start_pct / 100 / batch_size)
            log.info("evo_start_after_pct=%.0f%% -> evo_start_cycle=%d", start_pct, evo_start_cycle)

        completed_batches = len(done_ids) // batch_size
        evo_cycle = completed_batches
        batches = [remaining[i:i + batch_size]
                   for i in range(0, len(remaining), batch_size)]
        log.info("%d batches (batch_size=%d, max_cycles=%d)",
                 len(batches), batch_size, config.max_cycles)

        # Count completed evolution runs (for resume + max_cycles enforcement)
        evo_runs_done = 0
        hist_path = out_dir / "history.jsonl"
        if hist_path.exists():
            for line in hist_path.read_text().splitlines():
                try:
                    if not json.loads(line).get("skipped", True):
                        evo_runs_done += 1
                except (json.JSONDecodeError, KeyError):
                    pass
        if evo_runs_done:
            log.info("Resuming with %d evolution cycles already done", evo_runs_done)

        PoolClass = ProcessPoolExecutor if executor_type == "process" else ThreadPoolExecutor
        results = list(existing_results)
        t_start = time.time()

        for batch_idx, batch_tasks in enumerate(batches):
            batch_num = completed_batches + batch_idx + 1
            evo_cycle += 1
            log.info("=== Batch %d (%d tasks) | Evo cycle %d ===",
                     batch_num, len(batch_tasks), evo_cycle)

            # ── Route tasks via the adaptation operator ────────────
            # select() is the per-task, read-only decision (no feedback);
            # tasks sharing a key share one materialized workspace below.
            from collections import defaultdict
            # task_branches maps task_id → selection key for stats tracking
            task_branches: dict[str, str] = {}
            # branch_groups maps selection key → list of tasks
            branch_groups: dict[str, list] = defaultdict(list)

            if navigation_enabled:
                has_branches = (
                    strategy_tree is not None and bool(strategy_tree.branches)
                )
                if has_branches:
                    routing_workers = min(args.workers, 10)
                    with ThreadPoolExecutor(max_workers=routing_workers) as routing_pool:
                        keys = list(routing_pool.map(
                            lambda t: adapter.select(ws_dir, t), batch_tasks))
                    for t, branch in zip(batch_tasks, keys):
                        branch_groups[branch].append(t)
                        task_branches[t.id] = branch
                        log.info("  → %s routed to [%s]", t.id, branch)
                    # Update branch routing metadata
                    for b in strategy_tree.branches:
                        if b.name in branch_groups:
                            b.last_routed_cycle = evo_cycle
                else:
                    log.info("  All %d tasks → [main] (no branches yet)",
                             len(batch_tasks))
                    for t in batch_tasks:
                        branch_groups["main"].append(t)
                        task_branches[t.id] = "main"
            else:
                for t in batch_tasks:
                    branch_groups[adapter.select(ws_dir, t)].append(t)

            # ── Build prompts per branch and submit to pool ───────
            batch_results = []
            t_batch = time.time()

            pool = PoolClass(max_workers=args.workers)
            futures = {}
            for branch_name, branch_tasks in branch_groups.items():
                # Materialize this group's workspace via the adaptation
                # operator (git checkout for tree routing; no-op for the
                # whole-store default). The operator owns any fallback /
                # failed_checkout bookkeeping.
                if navigation_enabled:
                    try:
                        adapter.materialize(ws_dir, branch_name, agent.workspace)
                    except Exception:
                        pass
                    agent.reload_from_fs()

                # Build prompts from this branch's workspace state.
                # Capture everything now while on the branch — the main
                # checkout happens after all futures are submitted, so
                # solvers must not depend on live workspace state.
                prompt_args = backend.build_prompts(agent, branch_tasks)
                task_dicts = [{"id": t.id, "input": t.input, "metadata": t.metadata}
                              for t in branch_tasks]
                # Serialize infra files recursively so solvers can recreate
                # the full directory structure (supports multi-file imports).
                infra_dir = agent.workspace.root / "infra"
                infra_files = {}
                if infra_dir.exists():
                    for f in infra_dir.rglob("*.py"):
                        if "__pycache__" in str(f):
                            continue
                        try:
                            rel = str(f.relative_to(infra_dir))
                            infra_files[rel] = f.read_text()
                        except Exception:
                            pass
                args_dict = {
                    "model_id": args.model_id, "region": args.region,
                    "max_tokens": args.max_tokens, "max_turns": args.max_turns,
                    "task_timeout": args.task_timeout, "run_eval": args.run_eval,
                    "output_dir": str(out_dir), "batch_num": batch_num,
                    "evo_cycle": evo_cycle, "exp_tag": env.get("exp_tag", ""),
                    "solver_temperature": args.solver_temp or 0.0,
                    "workspace_root": str(agent.workspace.root),
                    "infra_files": infra_files,
                    **prompt_args,
                }
                # Per-task harness filter (retrieval operators M1/M2 only).
                # hasattr is False for whole_store / tree_routing, so M0/M4
                # add NO keys here and the worker takes the unfiltered path.
                if hasattr(adapter, "task_filter"):
                    tf = {t.id: adapter.task_filter(t.id) for t in branch_tasks}
                    if any(v is not None for v in tf.values()):
                        args_dict["task_filters"] = tf

                for td in task_dicts:
                    futures[pool.submit(backend.solve_one, td, args_dict)] = td["id"]

            # Return to main after submitting all branch groups, so live
            # workspace state is back on the root harness for the next cycle.
            if navigation_enabled:
                try:
                    adapter.materialize(ws_dir, "main", agent.workspace)
                    agent.reload_from_fs()
                except Exception:
                    pass

            # ── Collect results ───────────────────────────────────
            try:
                completed_ids = set()
                try:
                    import math as _math
                    _total_tasks = len(batch_tasks)
                    _rounds = _math.ceil(_total_tasks / max(args.workers, 1))
                    _batch_deadline = int(_rounds * args.task_timeout * 1.5)
                    for fut in as_completed(futures, timeout=_batch_deadline):
                        iid = futures[fut]
                        completed_ids.add(iid)
                        try:
                            r = fut.result(timeout=0)
                            # Flag tasks that exceeded the per-task budget
                            # even though they completed before the batch
                            # deadline.  Thread workers can't be killed
                            # mid-flight, so the batch deadline is the
                            # real safety net; this logs the overshoot.
                            elapsed = r.get("elapsed", 0)
                            if elapsed > args.task_timeout:
                                log.warning(
                                    "SLOW %s: %.0fs > %ds budget",
                                    iid, elapsed, args.task_timeout,
                                )
                        except TimeoutError:
                            log.error("TIMEOUT %s (>%ds)", iid, args.task_timeout)
                            r = {"instance_id": iid, "success": False, "score": 0.0,
                                 "detail": f"Timed out (>{args.task_timeout}s)",
                                 "elapsed": args.task_timeout, "error": "timeout",
                                 "batch_num": batch_num, "evo_cycle": evo_cycle}
                            _enrich_from_partial(r, out_dir, iid,
                                                 cut_off_reason="per_task_timeout")
                        except Exception as e:
                            log.error("CRASH %s: %s", iid, e)
                            r = {"instance_id": iid, "success": False, "score": 0.0,
                                 "detail": f"Crashed: {e}", "elapsed": 0, "error": str(e),
                                 "batch_num": batch_num, "evo_cycle": evo_cycle}

                        batch_results.append(r)
                        r_persist = {k: v for k, v in r.items()
                                     if k not in ("output", "conversation")}
                        results.append(r_persist)
                        _append_result(out_dir, r_persist)

                        passed = sum(1 for x in results if x.get("success"))
                        st = "PASS" if r.get("success") else "FAIL"
                        log.info("[%d/%d] %s %s (%.1fs) | total: %d/%d",
                                 len(results) - len(existing_results),
                                 len(remaining), st, iid,
                                 r.get("elapsed", 0), passed, len(results))
                except TimeoutError:
                    for fut, iid in futures.items():
                        if iid not in completed_ids:
                            fut.cancel()
                            log.error("HUNG %s — batch deadline exceeded", iid)
                            r = {"instance_id": iid, "success": False, "score": 0.0,
                                 "detail": "Hung past batch deadline",
                                 "elapsed": args.task_timeout, "error": "hung",
                                 "batch_num": batch_num, "evo_cycle": evo_cycle}
                            _enrich_from_partial(r, out_dir, iid,
                                                 cut_off_reason="batch_deadline")
                            batch_results.append(r)
                            results.append(r)
                            _append_result(out_dir, r)
            finally:
                # Force-kill the pool. pool.shutdown(wait=True) would hang
                # forever if any PROCESS worker is stuck, so kill process
                # workers first. Thread workers can't be killed, but each
                # htmldate call is wall-clocked (30s cap) so we can
                # safely wait for them to drain — avoiding a
                # shutdown-race that would otherwise surface as
                # "cannot schedule new futures after interpreter shutdown"
                # post-SUMMARY warnings.
                _hung_ids = set(futures.values()) - completed_ids if futures else set()
                if _hung_ids and executor_type == "process":
                    log.warning("Killing %d hung worker(s)", len(_hung_ids))
                    import os as _os, signal as _sig
                    for _child in getattr(pool, '_processes', {}).values():
                        try:
                            _os.kill(_child.pid, _sig.SIGKILL)
                        except (OSError, AttributeError):
                            pass
                    pool.shutdown(wait=False, cancel_futures=True)
                elif executor_type == "thread":
                    # Don't block on hung threads — a stuck model/tool
                    # call would prevent the run from ever advancing.
                    pool.shutdown(wait=not _hung_ids, cancel_futures=True)
                else:
                    pool.shutdown(wait=not _hung_ids, cancel_futures=True)

            # Clean up leaked Docker containers so the next batch starts clean.
            try:
                import subprocess as _sp
                for prefix in ["ctf-", "fx-"]:
                    _leaked = _sp.run(
                        ["docker", "ps", "-aq", "--filter", f"name={prefix}"],
                        capture_output=True, text=True, timeout=10,
                    )
                    for cid in _leaked.stdout.strip().splitlines():
                        if cid.strip():
                            _sp.run(["docker", "rm", "-f", cid.strip()],
                                    capture_output=True, timeout=10)
            except Exception:
                pass

            batch_elapsed = time.time() - t_batch
            batch_passed = sum(1 for r in batch_results if r.get("success"))
            batch_total = len(batch_results) or 1
            batch_scores = [r.get("score", 0.0) for r in batch_results]
            avg_score = sum(batch_scores) / len(batch_scores) if batch_scores else 0.0
            log.info("Batch %d: %d/%d passed (%.0f%%) avg_score=%.3f in %.0fs",
                     batch_num, batch_passed, batch_total,
                     100 * batch_passed / batch_total, avg_score, batch_elapsed)

            # ── Update branch stats from solve results ────────────
            if navigation_enabled and strategy_tree is not None and task_branches:
                # Regime properties (category/domain/year) per task — these
                # are the stratification keys the analyst uses to decide
                # branching. Persisting them on every routing entry lets the
                # evolver compute a full-history outcome×property table rather
                # than re-deriving regimes from a sample of trajectories.
                _meta_by_id = {t.id: (t.metadata or {}) for t in batch_tasks}

                def _regime_props(_iid):
                    _m = _meta_by_id.get(_iid, {})
                    return {
                        k: _m[k]
                        for k in ("category", "domain", "year")
                        if _m.get(k) not in (None, "")
                    }

                for r in batch_results:
                    iid = r["instance_id"]
                    branch_name = task_branches.get(iid, "main")
                    b = strategy_tree.get_branch(branch_name)
                    if b:
                        b.total_tasks += 1
                        if r.get("success"):
                            b.total_passed += 1
                    # Log routing entry
                    from agent_evolve.types import RoutingEntry
                    strategy_tree.routing_log.append(RoutingEntry(
                        task_id=iid,
                        properties=_regime_props(iid),
                        branch=branch_name,
                        score=r.get("score", 0.0),
                        cycle=evo_cycle,
                    ))
                # NOTE: routing log is PERSISTED below, after the reveal
                # state is updated, so `success` can be gated by
                # observer.is_revealed() (temporal-reveal leak safety).

            # ── Build observations ────────────────────────────────
            observations = []
            for r in batch_results:
                iid = r["instance_id"]
                task = next(t for t in batch_tasks if t.id == iid)
                output = r.get("output", "")
                if not output:
                    sid = iid.replace("/", "_")
                    p = out_dir / f"patch_{sid}.diff"
                    output = p.read_text() if p.exists() else ""
                conv = r.get("conversation", []) or []
                steps = [{
                    "conversation": conv,
                    "usage": {
                        "input_tokens": r.get("input_tokens", 0),
                        "output_tokens": r.get("output_tokens", 0),
                    },
                    "tool_call_count": r.get("turns", 0),
                    # Per-tool latency so the evolver can see which tools
                    # are actually slow (the real bottleneck) rather than
                    # inferring "too many turns" from trajectory lengths.
                    "tool_timings": r.get("tool_timings", []),
                    # Liveness metadata — surfaces to the evolver via
                    # build_evolution_prompt; not evaluation signal.
                    "status": r.get("status"),
                    "cut_off_reason": r.get("cut_off_reason"),
                    "partial_elapsed": r.get("partial_elapsed"),
                }]
                observations.append(Observation(
                    task=task,
                    trajectory=Trajectory(
                        task_id=iid, output=output,
                        steps=steps, conversation=conv,
                    ),
                    feedback=Feedback(
                        success=r.get("success", False),
                        score=r.get("score", 0.0),
                        detail=r.get("detail", ""),
                        raw=r,
                    ),
                ))
            # ── Evolution cycle ───────────────────────────────────
            agent.export_to_fs()
            # Temporal-reveal mode: the in-simulation "now" between this
            # batch and the next is the *earliest* creation_date of the
            # upcoming batch.  Using the earliest (min) is what prevents
            # evaluation leakage — any past task whose resolution comes
            # strictly before the next batch's first task was posed is
            # fair game; anything resolving later would leak into the
            # agent's reasoning for that first next-batch task.
            # If this is the last batch there's no evolution cycle
            # afterwards, so watermark doesn't matter.
            watermark = None
            if config.temporal_reveal:
                from agent_evolve.engine.observer import _parse_iso_tolerant
                next_idx = batch_idx + 1
                if next_idx < len(batches):
                    next_dates = []
                    for t in batches[next_idx]:
                        meta = t.metadata or {}
                        raw = meta.get("creation_date") or meta.get("timestamp")
                        dt = _parse_iso_tolerant(raw)
                        if dt is not None:
                            next_dates.append(dt)
                    if next_dates:
                        watermark = min(next_dates)
                observer.set_batch_timestamp(watermark)
            observer.collect(observations)
            # Cumulative reveal: any past task whose resolution_date has
            # now slipped under the watermark gets surfaced to the
            # evolver via the supplement file.  Logs a reveal-update
            # block showing newly-revealed tasks + the running total.
            if config.temporal_reveal:
                observer.update_reveal_state(cycle=evo_cycle, watermark=watermark)

            # Persist the routing log now that reveal state is current.
            # `success` is written ONLY for tasks whose label the evolver is
            # allowed to see (observer.is_revealed honours temporal-reveal /
            # trajectory-only) — the routing log feeds the analyst's branching
            # evidence, so an un-gated label here would leak future outcomes.
            if navigation_enabled and strategy_tree is not None and task_branches:
                with open(routing_log_path, "a") as f:
                    for r in batch_results:
                        iid = r["instance_id"]
                        entry = {
                            "task_id": iid,
                            "properties": _regime_props(iid),
                            "branch": task_branches.get(iid, "main"),
                            "cycle": evo_cycle,
                        }
                        if "success" in r and observer.is_revealed(iid):
                            entry["success"] = r["success"]
                        f.write(json.dumps(entry) + "\n")

            batch_score = batch_passed / batch_total
            pre_evo_msg = (f"pre-evo-{evo_cycle}: checkpoint"
                          if config.trajectory_only
                          else f"pre-evo-{evo_cycle}: score={batch_score:.3f}")
            versioning.commit(message=pre_evo_msg, tag=f"pre-evo-{evo_cycle}")
            t_evo = time.time()
            mutated, new_skills, evo_conversation, nav_trajectory = False, 0, [], []
            # negative threshold = never evolve (baseline mode)
            # max_cycles caps how many evolution cycles actually run
            # evo_start_cycle defers evolution until cycle N (late_start)
            skip_evo = (evo_trigger_threshold < 0
                        or (evo_trigger_threshold > 0 and batch_score >= evo_trigger_threshold)
                        or (config.max_cycles > 0 and evo_runs_done >= config.max_cycles)
                        or (evo_start_cycle > 0 and evo_cycle < evo_start_cycle))
            if skip_evo:
                reason = ("baseline" if evo_trigger_threshold < 0
                          else f"max_cycles ({evo_runs_done}/{config.max_cycles})"
                          if config.max_cycles > 0 and evo_runs_done >= config.max_cycles
                          else f"late_start (cycle {evo_cycle} < {evo_start_cycle})"
                          if evo_start_cycle > 0 and evo_cycle < evo_start_cycle
                          else f"score {batch_score:.2f} >= {evo_trigger_threshold:.2f}")
                log.info("Skipping evolution cycle %d (%s)", evo_cycle, reason)
            elif strategy_tree is not None:
                # Decoupled evolution: classify failures → deepen main / branch.
                # Entered when navigation is enabled or when plan-driven
                # multi-agent evolution was requested without routing.
                label = ("Navigation evolution"
                         if navigation_enabled else "Multi-agent evolution")
                log.info("%s cycle %d...", label, evo_cycle)
                # Enrich batch_results with task metadata so templates
                # like orthogonal_pair can dispatch based on difficulty/domain.
                _task_by_id = {t.id: t for t in batch_tasks}
                for r in batch_results:
                    t = _task_by_id.get(r.get("instance_id"))
                    if t and t.metadata:
                        r.setdefault("task_metadata", t.metadata)
                        for _mkey in ("difficulty_level", "domain", "category",
                                      "year", "event"):
                            if t.metadata.get(_mkey) is not None:
                                r.setdefault(_mkey, t.metadata[_mkey])
                # Write trajectory index for planner (lightweight summary)
                _idx_dir = evo_dir / "trajectories" / f"batch_{batch_num:04d}"
                _idx_dir.mkdir(parents=True, exist_ok=True)
                _idx_lines = ["task_id | category | year | turns | elapsed | outcome"]
                for r in batch_results:
                    _iid = r.get("instance_id", "")
                    _t = _task_by_id.get(_iid)
                    _cat = _t.metadata.get("category", "") if _t and _t.metadata else ""
                    _yr = _t.metadata.get("year", "") if _t and _t.metadata else ""
                    _turns = r.get("turns", 0)
                    _elapsed = f"{r.get('elapsed', 0):.0f}s"
                    if "success" in r:
                        _out = "PASS" if r["success"] else "FAIL"
                    else:
                        _out = "pending"
                    if r.get("max_turns_hit"):
                        _out += " (max turns)"
                    elif r.get("timed_out"):
                        _out += " (timeout)"
                    _idx_lines.append(f"{_iid} | {_cat} | {_yr} | {_turns} | {_elapsed} | {_out}")
                (_idx_dir / "index.txt").write_text("\n".join(_idx_lines) + "\n")
                try:
                    evo_result = evolver.evolve_with_navigation(
                        solver_workspace=agent.workspace,
                        batch_results=batch_results,
                        tree=strategy_tree,
                        evo_number=evo_cycle,
                        routing_log_path=routing_log_path,
                    )
                    mutated = evo_result.get("mutated", False)
                    plan = evo_result.get("plan", {})
                    nav_trajectory = evo_result.get("trajectory", [])
                    log.info("Evo: plan=%s, branches=%s",
                             plan.get("summary", ""),
                             evo_result.get("branches", []))
                except Exception as e:
                    log.error("%s failed: %s", label, e)
                # Persist strategy tree for resume
                (out_dir / "tree_state.json").write_text(
                    json.dumps(strategy_tree.to_dict(), indent=2))
                evo_runs_done += 1
            else:
                progress = (f"{evo_runs_done + 1}/{config.max_cycles}"
                            if config.max_cycles > 0 else str(evo_runs_done + 1))
                log.info("Evolution cycle %s...", progress)
                try:
                    evo_result = evolver.evolve(
                        workspace=agent.workspace,
                        observation_logs=observer.get_recent_logs(n_batches=1),
                        evo_number=evo_cycle,
                    )
                    mutated = evo_result.get("mutated", False)
                    new_skills = evo_result.get("new_skills", 0)
                    evo_conversation = evo_result.get("conversation", [])
                except Exception as e:
                    log.error("Evolution failed: %s", e)
                evo_runs_done += 1
            evo_elapsed = time.time() - t_evo

            if skip_evo:
                tag = (f"evo-{evo_cycle}: skipped"
                       if config.trajectory_only
                       else f"evo-{evo_cycle}: skipped (score={batch_score:.2f})")
            elif mutated:
                tag = f"evo-{evo_cycle}: {new_skills} new skills"
            else:
                tag = f"evo-{evo_cycle}: no mutation"
            versioning.commit(message=tag, tag=f"evo-{evo_cycle}")
            agent.reload_from_fs()
            log.info("Evo cycle %d: mutated=%s, %.0fs", evo_cycle, mutated, evo_elapsed)
            # History
            with open(out_dir / "history.jsonl", "a") as f:
                f.write(json.dumps({
                    "cycle": evo_cycle, "batch_num": batch_num,
                    "batch_score": batch_score, "avg_score": avg_score,
                    "batch_passed": batch_passed,
                    "batch_total": len(batch_results), "mutated": mutated,
                    "new_skills": new_skills, "skipped": skip_evo,
                    "evo_elapsed": evo_elapsed,
                    "cumulative_passed": sum(1 for x in results if x.get("success")),
                    "cumulative_total": len(results),
                }, default=str) + "\n")
            # Evolver trajectory — write to evolver workspace if it exists
            # (structured_evolution uses a sibling directory), else to evo_dir
            evo_traj_dir = evo_dir
            try:
                from agent_evolve.algorithms.navigation.templates._evolution_workspace import (
                    get_evolver_workspace_path,
                )
                ew = get_evolver_workspace_path(ws_dir)
                if ew.exists():
                    (ew / "evolution").mkdir(parents=True, exist_ok=True)
                    evo_traj_dir = ew / "evolution"
            except ImportError:
                pass
            if evo_conversation:
                traj_path = evo_traj_dir / f"evo_{evo_cycle}_trajectory.json"
                with open(traj_path, "w") as f:
                    json.dump(evo_conversation, f, indent=2, default=str)
            if nav_trajectory:
                traj_path = evo_traj_dir / f"nav_evo_{evo_cycle}_trajectory.json"
                with open(traj_path, "w") as f:
                    json.dump(nav_trajectory, f, indent=2, default=str)

        log.info("Done in %.0fs", time.time() - t_start)

    # ── Final output ──────────────────────────────────────────────
    (out_dir / "results.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False, default=str))

    ws_dir = out_dir / args.benchmark
    if ws_dir.exists():
        snap = out_dir / "evolved_workspace"
        if snap.exists():
            shutil.rmtree(snap)
        shutil.copytree(ws_dir, snap, ignore=shutil.ignore_patterns("evolution", ".git"))

    if cleanup_fn:
        cleanup_fn()

    passed = sum(1 for r in results if r.get("success"))
    total = len(results)
    scores = [r.get("score", 0.0) for r in results]
    avg_score = sum(scores) / len(scores) if scores else 0.0
    print(f"\n{'='*70}")
    pct = 100 * passed / total if total else 0.0
    print(f"SUMMARY: {passed}/{total} passed ({pct:.1f}%) avg_score={avg_score:.3f}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
    import sys
    sys.stdout.flush()
    sys.stderr.flush()
    import os
    os._exit(0)
