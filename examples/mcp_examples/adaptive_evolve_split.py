#!/usr/bin/env python3
"""MCP-Atlas legacy train/test split runner.

Two-phase split that mirrors SWE's evolve_sequential_split.py shape but uses
the legacy MCP ``AdaptiveEvolveEngine`` (not UnifiedEngine):

  Phase 1 (TRAIN): first ``--evolve-limit`` tasks, run in batches of
                   ``--batch-size`` with ``AdaptiveEvolveEngine`` evolving
                   the workspace after each batch.
  Phase 2 (TEST):  next ``--eval-limit`` tasks (or all remaining when unset),
                   solved once each with the evolved workspace, no further
                   evolution.

This file reuses the engine + container + key-registry setup from
``adaptive_evolve_all.py``; the per-batch solve logic is duplicated rather
than imported because the in-situ entrypoint inlines its main loop.

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
import multiprocessing as mp
import os
import pickle
import shutil
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock, get_ident as _thread_ident, local as _thread_local

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agent_evolve.config import EvolveConfig
from agent_evolve.engine.observer import Observer
from agent_evolve.types import Observation
from agent_evolve.algorithms.adaptive_evolve import (
    AdaptiveEvolveEngine,
    AdaptivePromptConfig,
)
from agent_evolve.benchmarks.mcp_atlas import McpAtlasBenchmark
from agent_evolve.agents.mcp.docker_env import McpAtlasContainer, pull_image
from agent_evolve.agents.mcp.mcp_client import McpClientWrapper
from agent_evolve.agents.mcp.key_registry import KeyRegistry

# Reuse the code-exec MCP agent variant from adaptive_evolve_all.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from adaptive_evolve_all import CodeExecMcpAgent  # noqa: E402

logger = logging.getLogger("adaptive_evolve_split")


def _record(task_id: str, success: bool, score: float, elapsed: float,
            output_len: int, detail: str, phase: str, evo_cycle: int,
            error: str = "") -> dict:
    return {
        "instance_id": task_id,
        "phase": phase,
        "success": bool(success),
        "score": float(score),
        "elapsed": round(elapsed, 1),
        "output_len": output_len,
        "detail": detail[:500],
        "evo_cycle": evo_cycle,
        "error": error[:300] if error else None,
    }


_io_lock = Lock()  # serialize per-task disk writes (output_*.txt / conversation_*.json)


def _run_agent_solve_direct(
    *,
    task,
    shared_base_url: str,
    work_dir: str | Path,
    solver_model: str,
    region: str,
    max_tokens: int,
    key_registry,
):
    """Run one MCP solve with a private agent/client pair."""
    client = McpClientWrapper(base_url=shared_base_url) if shared_base_url else McpClientWrapper()
    try:
        agent = CodeExecMcpAgent(
            workspace_dir=work_dir,
            model_id=solver_model,
            region=region,
            max_tokens=max_tokens,
            docker_image=None,
            key_registry=key_registry,
        )
        return agent.solve(task, shared_client=client)
    finally:
        try:
            client.close()
        except Exception:
            pass


def _solve_agent_child(payload_path: str, kwargs: dict) -> None:
    """Child-process entrypoint for a hard per-task timeout."""
    old_stdout, old_stderr = sys.stdout, sys.stderr
    try:
        with open(os.devnull, "w") as devnull:
            # Strands may print the model's final answer directly. Keep parent
            # logs readable; the parent writes output_*.txt/conversation_*.json.
            sys.stdout = devnull
            sys.stderr = devnull
            trajectory = _run_agent_solve_direct(**kwargs)
        payload = {"trajectory": trajectory}
    except BaseException as exc:  # propagate child failures to parent row
        payload = {
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr
    Path(payload_path).write_bytes(pickle.dumps(payload))


def _solve_with_process_timeout(
    *,
    task,
    sid: str,
    out_dir: Path,
    shared_base_url: str,
    work_dir: Path,
    solver_model: str,
    region: str,
    max_tokens: int,
    key_registry,
    task_timeout_sec: int,
):
    """Run one solve in a child process and terminate it on wall-clock timeout."""
    payload_path = (
        out_dir
        / f".solve_payload_{sid}_{os.getpid()}_{_thread_ident()}_{int(time.time() * 1000)}.pkl"
    )
    kwargs = {
        "task": task,
        "shared_base_url": shared_base_url,
        "work_dir": str(work_dir),
        "solver_model": solver_model,
        "region": region,
        "max_tokens": max_tokens,
        "key_registry": key_registry,
    }
    ctx = mp.get_context("spawn")
    proc = ctx.Process(target=_solve_agent_child, args=(str(payload_path), kwargs))
    proc.start()
    proc.join(task_timeout_sec)
    if proc.is_alive():
        proc.terminate()
        proc.join(5)
        if proc.is_alive():
            proc.kill()
            proc.join(5)
        try:
            payload_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise TimeoutError(f"task_timeout_after_{task_timeout_sec}s")

    try:
        if not payload_path.exists():
            raise RuntimeError(f"solve child exited without payload (exitcode={proc.exitcode})")
        payload = pickle.loads(payload_path.read_bytes())
    finally:
        try:
            payload_path.unlink(missing_ok=True)
        except Exception:
            pass

    if "error" in payload:
        raise RuntimeError(
            f"solve child failed: {payload['error']}\n{payload.get('traceback', '')}"
        )
    return payload["trajectory"]


def _make_worker_benchmark_factory(judge_model: str, region: str):
    """Returns a callable producing a thread-local McpAtlasBenchmark.

    Mirrors adaptive_evolve_baseline.py:272-284. McpAtlasBenchmark holds an
    internal asyncio loop and a lazy-init Bedrock client; sharing one
    instance across threads is technically unsafe (TOCTOU on
    `_bedrock_client`, possible loop reuse). One benchmark per thread keeps
    each thread's state isolated.
    """
    tls = _thread_local()

    def get_bm() -> McpAtlasBenchmark:
        bm = getattr(tls, "benchmark", None)
        if bm is None:
            bm = McpAtlasBenchmark(
                shuffle=False, eval_model_id=judge_model,
                eval_region=region, use_litellm=False,
            )
            tls.benchmark = bm
        return bm

    return get_bm


def _solve_one_task(
    *,
    task,
    worker_bm,
    out_dir: Path,
    shared_base_url: str,
    work_dir: Path,
    solver_model: str,
    region: str,
    max_tokens: int,
    key_registry,
    phase: str,
    evo_cycle: int,
    task_index: int,
    batch_len: int,
    task_timeout_sec: int | None,
    log,
) -> tuple[int, Observation | None, dict]:
    """Solve a single task in its own thread.

    Each thread creates its own `CodeExecMcpAgent` + `McpClientWrapper` and
    uses a thread-local `McpAtlasBenchmark` for evaluation
    (pattern lifted from `adaptive_evolve_baseline.py:286-326`). The
    MCP-Atlas docker container is the only shared resource, accessed via
    `shared_base_url`. Crucially, NO global ``sys.stdout`` redirect — that
    pattern is unsafe under threading because ``sys.stdout`` is process-global.

    Returns ``(task_index, obs, row)`` so the caller can re-sort batch results
    back into task order (``as_completed`` returns completion order, which
    breaks legacy parity for the evolver's observation log).
    """
    sid = task.id.replace("/", "_")
    log.info("[%s %d/%d] Solving %s ...", phase, task_index, batch_len, task.id)
    t0 = time.time()
    try:
        # Wall-clock timeout for the agent.solve() call. Threads cannot safely
        # kill a stuck Strands/MCP call, so the timed path runs the solve in a
        # child process and terminates that process on timeout.
        if task_timeout_sec and task_timeout_sec > 0:
            try:
                trajectory = _solve_with_process_timeout(
                    task=task,
                    sid=sid,
                    out_dir=out_dir,
                    shared_base_url=shared_base_url,
                    work_dir=work_dir,
                    solver_model=solver_model,
                    region=region,
                    max_tokens=max_tokens,
                    key_registry=key_registry,
                    task_timeout_sec=task_timeout_sec,
                )
            except TimeoutError:
                elapsed = time.time() - t0
                log.error("[%s %d/%d] TIMEOUT after %ds on %s",
                          phase, task_index, batch_len, task_timeout_sec, task.id)
                row = _record(task.id, False, 0.0, elapsed, 0, "", phase,
                              evo_cycle,
                              f"task_timeout_after_{task_timeout_sec}s")
                return task_index, None, row
        else:
            trajectory = _run_agent_solve_direct(
                task=task,
                shared_base_url=shared_base_url,
                work_dir=work_dir,
                solver_model=solver_model,
                region=region,
                max_tokens=max_tokens,
                key_registry=key_registry,
            )
        elapsed = time.time() - t0

        with _io_lock:
            (out_dir / f"output_{sid}.txt").write_text(trajectory.output)
            (out_dir / f"conversation_{sid}.json").write_text(
                json.dumps(trajectory.steps, indent=2, ensure_ascii=False, default=str))

        fb = worker_bm().evaluate(task, trajectory)
        log.info("[%s %d/%d] %s | score=%.2f | %.1fs",
                 phase, task_index, batch_len,
                 "PASS" if fb.success else "FAIL", fb.score, elapsed)

        row = _record(task.id, fb.success, fb.score, elapsed,
                      len(trajectory.output), fb.detail, phase, evo_cycle)
        obs = Observation(task=task, trajectory=trajectory, feedback=fb)
        return task_index, obs, row
    except Exception as e:
        elapsed = time.time() - t0
        log.error("[%s %d/%d] ERROR on %s: %s", phase, task_index, batch_len, task.id, e)
        log.error(traceback.format_exc())
        row = _record(task.id, False, 0.0, elapsed, 0, "", phase, evo_cycle, str(e))
        return task_index, None, row


def _solve_batch(
    *,
    batch_tasks,
    worker_bm,
    out_dir: Path,
    shared_base_url: str,
    work_dir: Path,
    solver_model: str,
    region: str,
    max_tokens: int,
    key_registry,
    phase: str,
    evo_cycle: int,
    parallel: int,
    task_timeout_sec: int | None,
    log,
) -> tuple[list, list]:
    """Solve a batch with ``parallel`` threads. Per-thread agent + client.

    Returns ``(batch_obs, rows)`` in original task order. ``as_completed``
    yields futures in completion order, which would change the observation
    sequence the evolver sees relative to the legacy serial path; we collect
    ``(task_index, obs, row)`` tuples and re-sort to preserve legacy parity.
    """
    workers = max(1, min(parallel, len(batch_tasks))) if batch_tasks else 1

    def _runner(task_index_task):
        i, task = task_index_task
        return _solve_one_task(
            task=task, worker_bm=worker_bm, out_dir=out_dir,
            shared_base_url=shared_base_url, work_dir=work_dir,
            solver_model=solver_model, region=region, max_tokens=max_tokens,
            key_registry=key_registry, phase=phase, evo_cycle=evo_cycle,
            task_index=i, batch_len=len(batch_tasks),
            task_timeout_sec=task_timeout_sec, log=log,
        )

    indexed = list(enumerate(batch_tasks, 1))
    collected: list[tuple[int, Observation | None, dict]] = []
    if workers == 1:
        for it in indexed:
            collected.append(_runner(it))
    else:
        log.info("[%s] solving %d tasks with %d threads", phase, len(batch_tasks), workers)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_runner, it) for it in indexed]
            for fut in as_completed(futures):
                collected.append(fut.result())

    # Re-sort to original task order so the evolver sees the legacy sequence.
    collected.sort(key=lambda t: t[0])
    batch_obs = [obs for _, obs, _ in collected if obs is not None]
    rows = [row for _, _, row in collected]
    return batch_obs, rows


def _evolve_after_batch(*, evolver, agent, observer, batch_obs, evo_cycle, log):
    """Same per-batch evolve-call shape as adaptive_evolve_all."""
    if not batch_obs:
        return
    observer.collect(batch_obs)
    agent.export_to_fs()
    obs_dicts = [{
        "task_id": o.task.id,
        "task_input": o.task.input,
        "input": o.task.input,
        # Both keys: in-situ legacy uses "output"; AdaptiveEvolveEngine.base_analysis
        # reads "agent_output". Provide both for safety.
        "output": o.trajectory.output,
        "agent_output": o.trajectory.output,
        "steps": o.trajectory.steps,
        "success": o.feedback.success,
        "score": o.feedback.score,
        # AdaptiveEvolveEngine.base_analysis reads the FLAT "feedback_detail"
        # string key (not a nested dict). Keep the nested "feedback" too in case
        # any downstream consumer wants it.
        "feedback_detail": o.feedback.detail,
        "feedback": {"detail": o.feedback.detail, "raw": o.feedback.raw},
    } for o in batch_obs]
    log.info("=== EVOLVE (cycle %d, %d obs) ===", evo_cycle, len(obs_dicts))
    t0 = time.time()
    try:
        result = evolver.evolve(workspace=agent.workspace, observation_logs=obs_dicts,
                                evo_number=evo_cycle)
        log.info("Evolved in %.1fs (pass=%.1f%% new_skills=%d)",
                 time.time() - t0, result.get("pass_rate", 0) * 100,
                 result.get("new_skills", 0))
        agent.reload_from_fs()
    except Exception as e:
        log.error("Evolution failed: %s", e)
        log.error(traceback.format_exc())


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--solver-model", default="us.anthropic.claude-opus-4-6-v1")
    p.add_argument("--evolver-model", default="us.anthropic.claude-opus-4-6-v1")
    p.add_argument("--judge-model", default="us.anthropic.claude-sonnet-4-6")
    p.add_argument("--region", default="us-west-2")
    p.add_argument("--max-tokens", type=int, default=16384)
    p.add_argument("--docker-image", default=None)
    p.add_argument("--env-file", default=None)
    p.add_argument("--external-container-url", default=None)
    p.add_argument("--limit", type=int, default=200,
                   help="Total task cap (train + test). Default 200.")
    p.add_argument("--evolve-limit", type=int, default=50,
                   help="Tasks for Phase 1 (train, evolve). Default 50.")
    p.add_argument("--eval-limit", type=int, default=None,
                   help="Tasks for Phase 2 (test). Default: all remaining up to --limit.")
    p.add_argument("--batch-size", type=int, default=5,
                   help="Train batch size (Phase 1). Default 5.")
    p.add_argument("--train-parallel", type=int, default=1,
                   help="Phase 1 parallel solve threads (effective = min(this, batch-size)). "
                        "Default 1 (legacy parity); shared MCP-Atlas container still rate-limits.")
    p.add_argument("--test-parallel", type=int, default=5,
                   help="Phase 2 parallel solve threads (no batch boundary). Default 5.")
    p.add_argument("--task-timeout-sec", type=int, default=1800,
                   help="Wall-clock timeout for a single agent.solve() call. "
                        "On timeout, the task is marked ERROR and the run "
                        "continues; the underlying thread may still be alive "
                        "in the background. Default 1800 (30 min) absorbs "
                        "Bedrock judge throttle storms. Set to 0 or negative "
                        "to disable.")
    p.add_argument("--seed-workspace", default="seed_workspaces/mcp")
    p.add_argument("--work-dir", default="./evolution_workdir/mcp_split")
    p.add_argument("--output-dir", required=True)
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    for n in ("botocore", "urllib3", "httpcore", "httpx",
              "strands.models", "strands.tools", "strands.telemetry"):
        logging.getLogger(n).setLevel(logging.WARNING)
    log = logging.getLogger("adaptive_evolve_split")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir = Path(args.work_dir)
    seed_dir = Path(args.seed_workspace)
    if not work_dir.exists() and seed_dir.exists():
        work_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(seed_dir, work_dir)
        log.info("Copied seed workspace %s -> %s", seed_dir, work_dir)
    # Same env-var gate as adaptive_evolve_all.py / adaptive_evolve_baseline.py.
    import os as _os
    if _os.environ.get("MCP_SKIP_PREPARE_WORKSPACE") in ("1", "true", "True"):
        log.info("MCP_SKIP_PREPARE_WORKSPACE=1 — skipping prepare_workspace "
                 "(blank-slate split: raw seed prompt + empty skills)")
    else:
        AdaptiveEvolveEngine.prepare_workspace(work_dir)

    # Benchmark + key registry
    bm = McpAtlasBenchmark(shuffle=False, eval_model_id=args.judge_model,
                           eval_region=args.region, use_litellm=False)
    key_registry = None
    if args.env_file:
        key_registry = KeyRegistry(env_file_path=args.env_file)
        key_registry.load()
        log.info("Loaded %d API key(s)", len(key_registry.get_loaded_key_names()))

    all_tasks = bm.get_tasks(split="test", limit=args.limit, key_registry=key_registry)
    train_tasks = all_tasks[: args.evolve_limit]
    if args.eval_limit is not None:
        test_tasks = all_tasks[args.evolve_limit: args.evolve_limit + args.eval_limit]
    else:
        test_tasks = all_tasks[args.evolve_limit:]
    log.info("Loaded %d total tasks; train=%d, test=%d",
             len(all_tasks), len(train_tasks), len(test_tasks))

    # Container + shared MCP-Atlas base URL.
    # external_container_url takes priority over docker_image so a caller can
    # reuse an already-warm container across runs (cold-start ~10-15+ min).
    # NOTE: in the parallel-solve world we no longer keep a process-wide
    # `shared_client`; each thread builds its own `McpClientWrapper(base_url)`
    # (matches adaptive_evolve_baseline.py:288-292). We just need the base URL
    # to hand to threads.
    container = None
    shared_base_url: str = ""
    if args.external_container_url:
        log.info("Using external MCP-Atlas container at %s", args.external_container_url)
        shared_base_url = args.external_container_url
    elif args.docker_image:
        all_env_vars = {}
        if key_registry:
            all_env_vars = {name: e.value for name, e in key_registry._keys.items() if e.value}
        if not pull_image(args.docker_image):
            log.error("Failed to pull image %s", args.docker_image)
            return 1
        container = McpAtlasContainer(
            args.docker_image,
            container_name=os.environ.get("MCP_CONTAINER_NAME", "mcp-atlas-split"),
            env_vars=all_env_vars,
        )
        log.info("Starting shared MCP-Atlas container ...")
        container.start()
        shared_base_url = container.base_url
        log.info("Shared container ready at %s", shared_base_url)

    # Engine setup (mirrors adaptive_evolve_all)
    evolution_dir = work_dir / "evolution"
    evolution_dir.mkdir(parents=True, exist_ok=True)
    observer = Observer(evolution_dir)
    config = EvolveConfig(
        evolver_model=args.evolver_model, evolver_max_tokens=8000,
        evolve_prompts=True, evolve_skills=True, evolve_memory=True,
        extra={"region": args.region},
    )
    prompt_config = AdaptivePromptConfig(
        prompt_max_chars=4000, skill_max_chars=2000, max_skills=15,
        include_claim_details=True, include_judge_patterns=True,
        include_task_type_stats=True, include_evolution_history=True,
    )
    evolver = AdaptiveEvolveEngine(
        config=config, prompt_config=prompt_config,
        improvement_threshold=0.03, stagnation_window=5,
    )
    log.info("Initialized AdaptiveEvolveEngine (legacy)")

    train_records: list[dict] = []
    test_records: list[dict] = []
    evo_cycle = 0
    agent = None

    train_parallel = max(1, args.train_parallel)
    test_parallel = max(1, args.test_parallel)
    # Worker threads each get their own thread-local McpAtlasBenchmark via this
    # factory; main thread keeps using `bm` for task loading + capability info.
    worker_bm = _make_worker_benchmark_factory(args.judge_model, args.region)
    # `state_agent` exists only to call evolver.evolve / export_to_fs / reload_from_fs
    # between batches. The actual per-task solving spawns its own thread-local
    # CodeExecMcpAgent inside _solve_batch (see _solve_one_task).
    state_agent = CodeExecMcpAgent(
        workspace_dir=work_dir, model_id=args.solver_model,
        region=args.region, max_tokens=args.max_tokens,
        docker_image=None, key_registry=key_registry,
    )

    try:
        # ── Phase 1: TRAIN (batched evolve) ──────────────────────────
        n_train_batches = (len(train_tasks) + args.batch_size - 1) // args.batch_size
        log.info("\n>>> Phase 1: TRAIN  (n=%d, batch=%d, train_parallel=%d)",
                 len(train_tasks), args.batch_size, train_parallel)
        for batch_idx in range(0, len(train_tasks), args.batch_size):
            batch = train_tasks[batch_idx: batch_idx + args.batch_size]
            batch_num = batch_idx // args.batch_size + 1
            log.info("=" * 70)
            log.info("Train batch %d/%d (tasks %d-%d) | evo_cycle=%d",
                     batch_num, n_train_batches,
                     batch_idx + 1, batch_idx + len(batch), evo_cycle)
            batch_obs, rows = _solve_batch(
                batch_tasks=batch, worker_bm=worker_bm, out_dir=out_dir,
                shared_base_url=shared_base_url, work_dir=work_dir,
                solver_model=args.solver_model, region=args.region,
                max_tokens=args.max_tokens, key_registry=key_registry,
                phase="train", evo_cycle=evo_cycle,
                parallel=train_parallel,
                task_timeout_sec=args.task_timeout_sec, log=log,
            )
            train_records.extend(rows)
            # Evolve is SERIAL — one call per batch, regardless of parallelism.
            _evolve_after_batch(evolver=evolver, agent=state_agent, observer=observer,
                                batch_obs=batch_obs, evo_cycle=evo_cycle, log=log)
            evo_cycle += 1
        train_pass = sum(1 for r in train_records if r["success"])
        log.info("--- Phase 1 done: TRAIN %d/%d (%.1f%%) ---",
                 train_pass, len(train_records),
                 100 * train_pass / max(len(train_records), 1))

        # ── Phase 2: TEST (no-evolve) ────────────────────────────────
        if test_tasks:
            log.info("\n>>> Phase 2: TEST  (n=%d, no-evolve, test_parallel=%d)",
                     len(test_tasks), test_parallel)
            _, rows = _solve_batch(
                batch_tasks=test_tasks, worker_bm=worker_bm, out_dir=out_dir,
                shared_base_url=shared_base_url, work_dir=work_dir,
                solver_model=args.solver_model, region=args.region,
                max_tokens=args.max_tokens, key_registry=key_registry,
                phase="test", evo_cycle=evo_cycle,
                parallel=test_parallel,
                task_timeout_sec=args.task_timeout_sec, log=log,
            )
            test_records.extend(rows)
            test_pass = sum(1 for r in test_records if r["success"])
            log.info("--- Phase 2 done: TEST %d/%d (%.1f%%) ---",
                     test_pass, len(test_records),
                     100 * test_pass / max(len(test_records), 1))
        else:
            log.info("\n>>> Phase 2: SKIPPED (no test tasks)")

    finally:
        if container:
            container.stop()

    # ── Persist outputs (split convention) ───────────────────────────
    train_path = out_dir / "results.train.jsonl"
    test_path = out_dir / "results.test.jsonl"
    combined_path = out_dir / "results.jsonl"
    metrics_path = out_dir / "results.metrics.json"

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
            "evolve_generations": evo_cycle,
        },
        "phase2_test": {
            "eval_limit": args.eval_limit,
            "n_tasks": len(test_records),
            "n_pass": test_pass,
            "pass_rate": test_pass / max(len(test_records), 1),
        },
        "pass_ratio": (test_pass / max(len(test_records), 1) if test_records else
                       train_pass / max(len(train_records), 1)),
        "engine": "AdaptiveEvolveEngine",
        "legacy_settings": {
            "solver_model": args.solver_model,
            "evolver_model": args.evolver_model,
            "judge_model": args.judge_model,
            "max_tokens": args.max_tokens,
            "batch_size": args.batch_size,
            "train_parallel": train_parallel,
            "test_parallel": test_parallel,
            "task_timeout_sec": args.task_timeout_sec,
            "docker_image": args.docker_image,
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
    print(f"  Evolutions: {evo_cycle}")
    print(f"{'='*60}")
    print(f"  Train:    {train_path}")
    print(f"  Test:     {test_path}")
    print(f"  Combined: {combined_path}")
    print(f"  Metrics:  {metrics_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
