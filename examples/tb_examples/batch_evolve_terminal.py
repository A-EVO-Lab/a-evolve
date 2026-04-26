#!/usr/bin/env python3
"""Batch runner for Terminal-Bench 2.0 with evolution — solve tasks in batches,
then evolve the agent workspace after each batch.

Each task gets its own log file under --log-dir/<task_name>/.
Results are appended to a JSONL file for metrics computation.
Supports resume: already-completed tasks (in the output file) are skipped.

The evolution loop:
  1. Solve a batch of N tasks in parallel
  2. Collect observations from the batch
  3. Run AdaptiveSkillEngine to mutate the workspace (skills, prompts, memory)
  4. Reload workspace and repeat with the next batch

Usage:
    # Solve all tasks, evolve once at the end
    uv run python examples/batch_evolve_terminal.py --workers 4 --solver react

    # Solve in batches of 10, evolve after each batch
    uv run python examples/batch_evolve_terminal.py --workers 4 --batch-size 10 --solver react

    # Quick test: 3 tasks, evolve after
    uv run python examples/batch_evolve_terminal.py --limit 3 --solver react

    # Resume a previous run
    uv run python examples/batch_evolve_terminal.py --workers 4 --solver react
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["BYPASS_TOOL_CONSENT"] = "true"
sys.setrecursionlimit(4000)

from agent_evolve.agents.terminal.agent import TerminalAgent, _extract_conversation
from agent_evolve.agents.terminal.dataset import load_all_tasks, TB2Task
from agent_evolve.agents.terminal.docker_env import TB2Container, pull_image
from agent_evolve.algorithms.adaptive_skill import AdaptiveSkillEngine
from agent_evolve.algorithms.mas_adaptive_skill import MasAdaptiveSkillEngine
from agent_evolve.config import EvolveConfig
from agent_evolve.engine.observer import Observer
from agent_evolve.types import Feedback, Observation, Task, Trajectory
from strands import tool

_write_lock = threading.Lock()
log = logging.getLogger("batch_evolve")


def _write_result(path: str, result: dict) -> None:
    with _write_lock:
        with open(path, "a") as f:
            f.write(json.dumps(result, default=str) + "\n")


def _print_main(msg: str) -> None:
    with _write_lock:
        sys.stdout.write(msg + "\n")
        sys.stdout.flush()


def _setup_task_logger(task_name: str, log_dir: str) -> tuple[logging.Logger, Path]:
    task_log_dir = Path(log_dir) / task_name
    task_log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = task_log_dir / f"evolve_{timestamp}.log"

    logger = logging.getLogger(f"task.{task_name}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S"
    )
    fh = logging.FileHandler(log_file, mode="w")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    logger.info("Log file: %s", log_file)
    return logger, task_log_dir


# ── Per-task solve (returns result dict for observation collection) ────


def _solve_one_task(
    task: TB2Task,
    model_id: str,
    region: str,
    max_tokens: int,
    log_dir: str,
    output_file: str,
    errors_file: str,
    solver: str = "react",
    system_prompt_text: str | None = None,
    skill_agent=None,
    propose_skill: bool = False,
) -> dict:
    """Solve a single task. Returns a result dict with all info needed for
    both JSONL output and observation collection."""
    task_name = task.name
    task_log = None
    task_log_dir = None
    t0 = time.time()
    container = None

    try:
        task_log, task_log_dir = _setup_task_logger(task_name, log_dir)
        task_log.info("=" * 60)
        task_log.info("Task:       %s", task_name)
        task_log.info("Image:      %s", task.docker_image)
        task_log.info("Difficulty: %s", task.metadata.get("difficulty", "?"))
        task_log.info("Category:   %s", task.metadata.get("category", "?"))
        task_log.info("Timeout:    %ds", task.metadata.get("agent_timeout_sec", 900))
        task_log.info("Model:      %s", model_id)
        task_log.info("Solver:     %s", solver)
        task_log.info("=" * 60)

        if not pull_image(task.docker_image):
            raise RuntimeError(f"Failed to pull image {task.docker_image}")

        user_prompt = f"{task.prompt}\n"
        system_prompt = system_prompt_text or Path("seed_workspaces/terminal/prompts/system.md").read_text()
        skills_content = skill_agent.get_skills_content() if skill_agent else {}
        ws_tool_specs = skill_agent.get_tool_specs() if skill_agent else None
        ws_tool_executors = skill_agent.load_tool_executors() if skill_agent else None

        container = TB2Container(task.docker_image)
        container.start()
        task_log.info("Container started: %s", container.container_name)

        task_container_name = container.container_name
        _container_dead = False

        @tool
        def task_bash(cmd: str) -> str:
            """Use this function to execute bash commands.

            Args:
                cmd: The bash command to execute.

            Returns:
                The output of the command.
            """
            nonlocal _container_dead
            if _container_dead:
                raise RuntimeError("Container has been killed — task is over.")
            cmd_preview = cmd[:200] + ("..." if len(cmd) > 200 else "")
            task_log.info("[bash] $ %s", cmd_preview)
            t_start = time.time()
            try:
                docker_cmd = ["docker", "exec", task_container_name, "bash", "--login", "-c", cmd]
                result = subprocess.run(docker_cmd, capture_output=True, text=True, timeout=60)
                output = ""
                if result.stderr:
                    output = f"{result.stderr}\n"
                output = f"{output}{result.stdout}"
                if "No such container" in output or "is not running" in output:
                    _container_dead = True
                    raise RuntimeError("Container has been killed — task is over.")
                if not output.strip():
                    output = "(no output)"
                if len(output) > 15000:
                    output = output[:7000] + "\n\n... [truncated] ...\n\n" + output[-7000:]
                elapsed_t = time.time() - t_start
                task_log.info("[bash] done (%.1fs, %d chars)", elapsed_t, len(output))
                return output
            except subprocess.TimeoutExpired:
                task_log.warning("[bash] TIMEOUT after 60s")
                return "ERROR: Command timed out after 60 seconds."
            except RuntimeError:
                raise
            except Exception as e:
                if "No such container" in str(e):
                    _container_dead = True
                    raise RuntimeError("Container has been killed — task is over.")
                task_log.error("[bash] ERROR: %s", e)
                return f"ERROR: {e}"

        @tool
        def task_python(code: str) -> str:
            """Use the python function to execute Python code.

            Args:
                code: The python code to execute.

            Returns:
                The output of the Python code.
            """
            nonlocal _container_dead
            if _container_dead:
                raise RuntimeError("Container has been killed — task is over.")
            code_preview = code[:200] + ("..." if len(code) > 200 else "")
            task_log.info("[python] >>> %s", code_preview.replace("\n", "\\n"))
            t_start = time.time()
            try:
                docker_cmd = ["docker", "exec", "-i", task_container_name, "bash", "--login", "-c", "python3 -"]
                result = subprocess.run(docker_cmd, capture_output=True, text=True, timeout=60, input=code)
                output = ""
                if result.stderr:
                    output = f"{result.stderr}\n"
                output = f"{output}{result.stdout}"
                if "No such container" in output or "is not running" in output:
                    _container_dead = True
                    raise RuntimeError("Container has been killed — task is over.")
                if not output.strip():
                    output = "(no output)"
                if len(output) > 15000:
                    output = output[:7000] + "\n\n... [truncated] ...\n\n" + output[-7000:]
                elapsed_t = time.time() - t_start
                task_log.info("[python] done (%.1fs, %d chars)", elapsed_t, len(output))
                return output
            except subprocess.TimeoutExpired:
                task_log.warning("[python] TIMEOUT after 60s")
                return "ERROR: Command timed out after 60 seconds."
            except RuntimeError:
                raise
            except Exception as e:
                if "No such container" in str(e):
                    _container_dead = True
                    raise RuntimeError("Container has been killed — task is over.")
                task_log.error("[python] ERROR: %s", e)
                return f"ERROR: {e}"

        @tool
        def task_submit(answer: str) -> str:
            """Submit an answer for evaluation.

            Args:
                answer: Submitted answer.

            Returns:
                The submitted answer.
            """
            task_log.info("[submit] %s", answer)
            return f"Task submitted successfully: {answer}. Execution will now stop."

        # ── Solve ──────────────────────────────────────────────────
        task_log.info("--- Agent solving started (solver=%s) ---", solver)
        solve_t0 = time.time()
        timeout_sec = task.metadata.get("agent_timeout_sec", 900)

        conversation = []
        usage = {}
        skill_draft = None

        if solver == "react":
            from agent_evolve.agents.terminal.react_solver import (
                react_solve, extract_conversation,
            )
            react_result = react_solve(
                task_prompt=user_prompt,
                container_name=task_container_name,
                model_id=model_id,
                region=region,
                max_tokens=max_tokens,
                timeout_sec=timeout_sec,
                log=task_log,
                system_prompt=system_prompt,
                propose_skill=propose_skill,
                skills=skills_content,
                tool_specs=ws_tool_specs,
                tool_executors=ws_tool_executors,
            )
            usage = {
                "input_tokens": react_result.total_input_tokens,
                "output_tokens": react_result.total_output_tokens,
                "total_tokens": react_result.total_input_tokens + react_result.total_output_tokens,
            }
            conversation = extract_conversation(react_result.messages)
            skill_draft = react_result.skill_draft  # v15: solver-proposed skill
        else:
            from strands import Agent
            from strands.models import BedrockModel

            bedrock_model = BedrockModel(
                model_id=model_id,
                region_name=region,
                max_tokens=max_tokens,
            )
            tools = [task_bash, task_python, task_submit]
            agent = Agent(
                model=bedrock_model,
                system_prompt=system_prompt,
                tools=tools,
            )
            task_log.info("Tools: bash, python, submit")

            response = _run_agent_with_timeout(agent, user_prompt, timeout_sec, task_log, container=container)

            if response:
                try:
                    u = response.metrics.accumulated_usage
                    usage = {
                        "input_tokens": u.get("inputTokens", 0),
                        "output_tokens": u.get("outputTokens", 0),
                        "total_tokens": u.get("totalTokens", 0),
                    }
                except Exception:
                    pass

            try:
                conversation = _extract_conversation(agent.messages)
            except Exception:
                pass

        solve_elapsed = time.time() - solve_t0
        task_log.info("--- Agent solving finished in %.1fs ---", solve_elapsed)

        # ── Evaluate ───────────────────────────────────────────────
        passed = False
        eval_output = ""

        if task.test_sh_path and os.path.exists(task.test_sh_path):
            if not container._running:
                task_log.info("Container was killed (timeout). Restarting for evaluation...")
                container.start()

            task_log.info("Copying eval files into container for evaluation...")
            container.exec("mkdir -p /tests /logs/verifier")

            if task.files:
                for container_path, local_path in task.files.items():
                    if os.path.exists(local_path):
                        try:
                            container.copy_to(local_path, container_path)
                        except Exception as e:
                            task_log.warning("Failed to copy %s -> %s: %s", local_path, container_path, e)
                    else:
                        task_log.warning("Eval file not found: %s", local_path)
            else:
                if task.test_sh_path and os.path.exists(task.test_sh_path):
                    container.copy_to(task.test_sh_path, "/tests/test.sh")
                if task.test_py_path and os.path.exists(task.test_py_path):
                    container.copy_to(task.test_py_path, "/tests/test_outputs.py")

            task_log.info("--- Evaluation started ---")
            eval_t0 = time.time()
            verifier_timeout = task.metadata.get("verifier_timeout_sec", 900)
            passed, eval_output = container.run_tests_with_retry(
                task.test_sh_path, timeout=verifier_timeout, max_retries=3
            )
            eval_elapsed = time.time() - eval_t0
            task_log.info("--- Evaluation finished in %.1fs: %s ---",
                         eval_elapsed, "PASS" if passed else "FAIL")
        else:
            task_log.warning("No test.sh found, skipping evaluation")

        container.stop()
        container = None

        # Save per-task artifacts
        if task_log_dir:
            (task_log_dir / "result.txt").write_text(f"passed={passed}\n{eval_output}")
            (task_log_dir / "conversation.json").write_text(
                json.dumps(conversation, indent=2, ensure_ascii=False, default=str)
            )

        elapsed = time.time() - t0
        task_log.info("RESULT: %s | Time: %.1fs", "PASS" if passed else "FAIL", elapsed)

        result = {
            "task_name": task_name,
            "passed": passed,
            "eval_output": eval_output[-2000:] if len(eval_output) > 2000 else eval_output,
            "model_name_or_path": model_id,
            "usage": usage,
            "solve_time": solve_elapsed,
            "total_time": elapsed,
            "conversation_turns": len(conversation),
            "conversation": conversation,
            "metadata": task.metadata,
            "status": "passed" if passed else "failed",
            "skill_draft": skill_draft,
        }
        _write_result(output_file, result)
        return result

    except Exception as e:
        elapsed = time.time() - t0
        err_msg = str(e)[:500]
        if task_log:
            task_log.error("FATAL ERROR: %s", err_msg)

        _write_result(errors_file, {"task_name": task_name, "error": err_msg})
        result = {
            "task_name": task_name,
            "passed": False,
            "eval_output": f"ERROR: {err_msg}",
            "model_name_or_path": model_id,
            "usage": {},
            "solve_time": 0,
            "total_time": elapsed,
            "conversation_turns": 0,
            "conversation": [],
            "metadata": task.metadata,
            "status": "error",
        }
        _write_result(output_file, result)
        return result

    finally:
        if container is not None:
            try:
                container.stop()
            except Exception:
                pass


def _run_agent_with_timeout(agent, prompt: str, timeout_sec: int, task_log, container=None):
    import concurrent.futures

    def _run():
        return agent(prompt)

    for attempt in range(3):
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(_run)
        try:
            response = future.result(timeout=timeout_sec)
            executor.shutdown(wait=False)
            return response
        except concurrent.futures.TimeoutError:
            task_log.warning("Agent timed out after %ds — force-killing container.", timeout_sec)
            if container is not None:
                try:
                    container.stop()
                except Exception:
                    pass
            executor.shutdown(wait=False)
            return None
        except Exception as e:
            err_str = str(e)
            transient_keywords = [
                "ThrottlingException", "Too many tokens",
                "internalServerException", "ServiceUnavailableException",
                "ModelTimeoutException",
            ]
            if any(kw in err_str for kw in transient_keywords) and attempt < 2:
                wait = 2 ** attempt * 5
                task_log.warning("Transient API error (attempt %d/3): %s. Retrying in %ds...",
                                 attempt + 1, err_str[:150], wait)
                executor.shutdown(wait=False)
                time.sleep(wait)
                continue
            else:
                task_log.error("Agent error: %s", err_str[:300])
                executor.shutdown(wait=False)
                return None
    return None


# ── Metrics ──────────────────────────────────────────────────────────


def load_results(results_path: str) -> list[dict]:
    seen = {}
    with open(results_path) as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                seen[r.get("task_name", "")] = r
    return list(seen.values())


def compute_metrics(results: list[dict]) -> dict:
    total = len(results)
    passed = sum(1 for r in results if r.get("passed", False))
    failed = total - passed

    by_category = defaultdict(lambda: {"total": 0, "passed": 0, "failed": 0})
    by_difficulty = defaultdict(lambda: {"total": 0, "passed": 0, "failed": 0})
    per_task = []

    for r in results:
        meta = r.get("metadata", {})
        cat = meta.get("category", "unknown")
        diff = meta.get("difficulty", "unknown")
        p = r.get("passed", False)

        by_category[cat]["total"] += 1
        by_difficulty[diff]["total"] += 1
        if p:
            by_category[cat]["passed"] += 1
            by_difficulty[diff]["passed"] += 1
        else:
            by_category[cat]["failed"] += 1
            by_difficulty[diff]["failed"] += 1

        per_task.append({
            "task_name": r.get("task_name", "?"),
            "passed": p,
            "category": cat,
            "difficulty": diff,
            "solve_time": r.get("solve_time", 0),
            "total_time": r.get("total_time", 0),
            "tokens": r.get("usage", {}).get("total_tokens", 0),
        })

    for group in [by_category, by_difficulty]:
        for v in group.values():
            v["pass_ratio"] = v["passed"] / v["total"] if v["total"] > 0 else 0.0

    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_ratio": passed / total if total > 0 else 0.0,
        "by_category": dict(by_category),
        "by_difficulty": dict(by_difficulty),
        "per_task": per_task,
    }


def print_metrics(metrics: dict) -> None:
    print(f"\n{'='*70}")
    print(f"  Terminal-Bench 2.0 Results (with Evolution)")
    print(f"{'='*70}")
    print(f"  Total tasks:  {metrics['total']}")
    print(f"  Passed:       {metrics['passed']}")
    print(f"  Failed:       {metrics['failed']}")
    print(f"  Pass ratio:   {metrics['pass_ratio']:.1%}")
    print(f"{'='*70}")

    if metrics["by_difficulty"]:
        print(f"\n  By Difficulty:")
        for diff in ["easy", "medium", "hard"]:
            if diff in metrics["by_difficulty"]:
                d = metrics["by_difficulty"][diff]
                print(f"    {diff:<10} {d['passed']}/{d['total']} ({d['pass_ratio']:.1%})")

    if metrics["by_category"]:
        print(f"\n  By Category:")
        cats = sorted(
            metrics["by_category"].items(),
            key=lambda x: x[1]["pass_ratio"],
            reverse=True,
        )
        for cat, d in cats:
            print(f"    {cat:<35} {d['passed']}/{d['total']} ({d['pass_ratio']:.1%})")

    print(f"\n  Per-Task Details:")
    for t in sorted(metrics["per_task"], key=lambda x: x["task_name"]):
        status = "PASS" if t["passed"] else "FAIL"
        time_str = f"{t['total_time']:.0f}s" if t["total_time"] else "?"
        print(f"    {status}  {t['task_name']:<40} {t['difficulty']:<10} "
              f"{t['category']:<25} {time_str}")

    times = [t["total_time"] for t in metrics["per_task"] if t["total_time"] > 0]
    if times:
        print(f"\n  Timing:")
        print(f"    Total wall time:  {sum(times):.0f}s")
        print(f"    Avg per task:     {sum(times)/len(times):.0f}s")
        print(f"    Min:              {min(times):.0f}s")
        print(f"    Max:              {max(times):.0f}s")

    print()


# ── Evolution helpers ────────────────────────────────────────────────


def _run_evolve_cycle(
    agent: TerminalAgent,
    observer: Observer,
    batch_results: list[dict],
    evo_number: int,
    config: EvolveConfig,
    evolver_name: str = "adaptive_skill",
) -> dict:
    """Collect observations from batch results and run one evolution cycle."""
    observations = []
    for r in batch_results:
        task_obj = Task(
            id=r["task_name"],
            input="",
            metadata=r.get("metadata", {}),
        )
        trajectory = Trajectory(
            task_id=r["task_name"],
            output=r.get("eval_output", ""),
            steps=[{
                "passed": r.get("passed", False),
                "eval_output": r.get("eval_output", ""),
                "usage": r.get("usage", {}),
            }],
            conversation=r.get("conversation", []),
        )
        feedback = Feedback(
            success=r.get("passed", False),
            score=1.0 if r.get("passed", False) else 0.0,
            detail=r.get("eval_output", ""),
        )
        observations.append(Observation(task=task_obj, trajectory=trajectory, feedback=feedback))

    observer.collect(observations)
    agent.export_to_fs()

    recent_logs = observer.get_recent_logs(n_batches=1)

    if evolver_name == "mas_adaptive_skill":
        evolver = MasAdaptiveSkillEngine(config)
    else:
        evolver = AdaptiveSkillEngine(config)
    evolve_result = evolver.evolve(
        workspace=agent.workspace,
        observation_logs=recent_logs,
        evo_number=evo_number,
    )

    agent.reload_from_fs()
    return evolve_result


def _cleanup_containers():
    try:
        result = subprocess.run(
            ["docker", "ps", "-q", "--filter", "name=tb2-"],
            capture_output=True, text=True, timeout=10,
        )
        container_ids = result.stdout.strip().split()
        for cid in container_ids:
            if cid:
                subprocess.run(["docker", "rm", "-f", cid], capture_output=True, timeout=10)
        if container_ids:
            print(f"Cleaned up {len(container_ids)} leaked container(s)")
    except Exception:
        pass


# ── Main ─────────────────────────────────────────────────────────────


def main():
    p = argparse.ArgumentParser(
        description="Terminal-Bench 2.0 Batch Runner with Evolution"
    )
    # Task selection
    p.add_argument("--challenges-dir", type=str, default=None,
                   help="Path to challenges directory (overrides TB2_CHALLENGES_DIR)")
    p.add_argument("--tasks", type=str, default=None,
                   help="Comma-separated list of specific task names to run")
    p.add_argument("--exclude", type=str, default=None,
                   help="Comma-separated list of task names to skip")
    p.add_argument("--category", type=str, default=None,
                   help="Filter by category")
    p.add_argument("--difficulty", type=str, default=None,
                   help="Filter by difficulty (easy/medium/hard)")
    p.add_argument("--limit", type=int, default=None,
                   help="Max tasks to run")
    p.add_argument("--shuffle", action="store_true", default=False,
                   help="Shuffle task order")

    # Model / solver
    p.add_argument("--model-id", type=str,
                   default="us.anthropic.claude-opus-4-6-v1",
                   help="Bedrock model ID")
    p.add_argument("--evolver-model-id", type=str, default=None,
                   help="Bedrock model ID for the evolver (default: same as --model-id)")
    p.add_argument("--region", type=str, default="us-west-2", help="AWS region")
    p.add_argument("--max-tokens", type=int, default=16384,
                   help="Max tokens per model response")
    p.add_argument("--solver", type=str, default="react", choices=["react", "strands"],
                   help="Solver: 'react' (standalone ReAct, recommended) or 'strands'")
    # Execution
    p.add_argument("--workers", type=int, default=2,
                   help="Number of parallel workers for solving")
    p.add_argument("--batch-size", type=int, default=None,
                   help="Tasks per batch before evolving (default: all at once)")
    p.add_argument("--no-evolve", action="store_true", default=False,
                   help="Disable evolution — solve with the current workspace only (for evaluation phase)")
    p.add_argument("--trajectory-only", action="store_true", default=False,
                   help="Evolution sees only agent trajectories — no pass/fail, score, or test output")
    p.add_argument("--max-skills", type=int, default=5,
                   help="Maximum number of skills the evolver can create (default: 5)")
    p.add_argument("--propose-skill", action="store_true", default=False,
                   help="v15: solver proposes skill drafts after solving; evolver curates them")
    p.add_argument("--prompt-only", action="store_true", default=False,
                   help="v17: evolve only the system prompt (no skills/memory), strategy-focused")
    p.add_argument("--skills-only", action="store_true", default=False,
                   help="v32: evolve only skills (no prompt/memory/tools changes)")
    p.add_argument("--protect-skills", action="store_true", default=False,
                   help="v39: protect existing skills from modification, evolver can only ADD new skills")
    p.add_argument("--no-skills", action="store_true", default=False,
                   help="Remove all skills from workspace (vanilla baseline, no skill guidance)")
    p.add_argument("--evolver", type=str, default="adaptive_skill",
                   choices=["adaptive_skill", "mas_adaptive_skill"],
                   help="Evolution algorithm: 'adaptive_skill' (single-agent) or "
                        "'mas_adaptive_skill' (multi-agent system)")

    # Evolution
    p.add_argument("--seed-workspace", type=str, default="seed_workspaces/terminal",
                   help="Seed workspace to copy if work-dir doesn't exist")
    p.add_argument("--work-dir", type=str, default="./evolution_workdir/terminal",
                   help="Evolution workspace directory")

    # Output
    p.add_argument("--output", type=str, default="tb2_evolve_results.jsonl",
                   help="Output JSONL file for results")
    p.add_argument("--errors", type=str, default="tb2_evolve_errors.jsonl",
                   help="Output JSONL file for errors")
    p.add_argument("--log-dir", type=str, default="logs/batch_evolve",
                   help="Base directory for per-task logs")
    p.add_argument("--metrics-only", action="store_true", default=False,
                   help="Only compute metrics from existing results file")
    args = p.parse_args()

    # Quiet noisy libraries
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
                        datefmt="%H:%M:%S")
    for n in ("botocore", "urllib3", "httpcore", "httpx",
              "strands.models", "strands.tools", "strands.telemetry"):
        logging.getLogger(n).setLevel(logging.WARNING)

    # ── Metrics-only mode ─────────────────────────────────────────
    if args.metrics_only:
        if not Path(args.output).exists():
            print(f"ERROR: Results file not found: {args.output}")
            sys.exit(1)
        results = load_results(args.output)
        if not results:
            print("No results found.")
            sys.exit(1)
        metrics = compute_metrics(results)
        print_metrics(metrics)
        metrics_file = Path(args.output).with_suffix(".metrics.json")
        metrics_file.write_text(json.dumps(metrics, indent=2))
        print(f"Metrics saved to {metrics_file}")
        return

    # ── Load and filter tasks ─────────────────────────────────────
    tasks = load_all_tasks(args.challenges_dir)
    print(f"Loaded {len(tasks)} tasks")

    if args.tasks:
        task_names = set(n.strip() for n in args.tasks.split(","))
        tasks = [t for t in tasks if t.name in task_names]
        print(f"Filtered to {len(tasks)} specified tasks")

    if args.exclude:
        exclude_names = set(n.strip() for n in args.exclude.split(","))
        tasks = [t for t in tasks if t.name not in exclude_names]
        print(f"Excluded {len(exclude_names)} tasks, {len(tasks)} remaining")

    if args.category:
        tasks = [t for t in tasks if t.metadata.get("category") == args.category]
    if args.difficulty:
        tasks = [t for t in tasks if t.metadata.get("difficulty") == args.difficulty]

    # Resume support
    completed = set()
    if Path(args.output).exists():
        with open(args.output) as f:
            for line in f:
                if line.strip():
                    try:
                        completed.add(json.loads(line)["task_name"])
                    except Exception:
                        pass
    tasks = [t for t in tasks if t.name not in completed]
    print(f"Remaining: {len(tasks)} tasks ({len(completed)} already done)")

    if args.shuffle:
        import random
        random.shuffle(tasks)
    if args.limit:
        tasks = tasks[:args.limit]

    if not tasks:
        print("No tasks to run.")
        if completed and Path(args.output).exists():
            results = load_results(args.output)
            if results:
                metrics = compute_metrics(results)
                print_metrics(metrics)
        return

    # ── Prepare workspace ─────────────────────────────────────────
    work_dir = Path(args.work_dir)
    seed_dir = Path(args.seed_workspace)
    if not work_dir.exists() and seed_dir.exists():
        work_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(seed_dir, work_dir)
        log.info("Copied seed workspace %s -> %s", seed_dir, work_dir)

    if args.no_skills:
        skills_dir = work_dir / "skills"
        if skills_dir.exists():
            shutil.rmtree(skills_dir)
            skills_dir.mkdir()
            log.info("Removed all skills from workspace (--no-skills)")

    agent = TerminalAgent(
        workspace_dir=work_dir,
        model_id=args.model_id,
        region=args.region,
        max_tokens=args.max_tokens,
    )

    evolution_dir = work_dir / "evolution"
    evolution_dir.mkdir(parents=True, exist_ok=True)
    observer = Observer(evolution_dir)

    config = EvolveConfig(
        evolver_model=args.evolver_model_id or args.model_id,
        trajectory_only=args.trajectory_only,
        evolve_prompts=not args.skills_only and not args.prompt_only,
        evolve_skills=not args.prompt_only,
        evolve_memory=not args.prompt_only and not args.skills_only,
        extra={
            "region": args.region,
            "max_skills": args.max_skills,
            "solver_proposed": args.propose_skill,
            "prompt_only": args.prompt_only,
            "skills_only": args.skills_only,
            "protect_skills": args.protect_skills,
        },
    )

    # Build prompts from workspace (includes evolved skills/memories)
    # Pass None to enable per-task skill selection in _solve_one_task
    system_prompt_text = agent._build_system_prompt()
    # Also store the agent for per-task prompt building
    _task_agent = agent

    # ── Split into batches ────────────────────────────────────────
    batch_size = args.batch_size or len(tasks)
    batches = [tasks[i:i + batch_size] for i in range(0, len(tasks), batch_size)]

    Path(args.log_dir).mkdir(parents=True, exist_ok=True)
    print(f"Per-task logs: {args.log_dir}/<task_name>/")
    print(f"Results file:  {args.output}")
    print(f"Workspace:     {work_dir}")
    print(f"Batches:       {len(batches)} (batch_size={batch_size})")
    print(f"Running {len(tasks)} tasks with {args.workers} workers\n")

    # ── Batch loop ────────────────────────────────────────────────
    total_passed = total_failed = total_errored = 0
    evo_number = 0
    global_start = time.time()

    for batch_idx, batch in enumerate(batches):
        batch_start = time.time()
        print(f"\n{'='*70}")
        print(f"  BATCH {batch_idx + 1}/{len(batches)} — {len(batch)} tasks")
        print(f"  Skills: {len(agent.skills)} | Memories: {len(agent.memories)}")
        print(f"{'='*70}\n")

        # Solve batch in parallel
        batch_results = []
        pool = ThreadPoolExecutor(max_workers=args.workers)
        # Only propose skills during evolution phase (not --no-evolve eval)
        _propose = args.propose_skill and not args.no_evolve
        futures = {
            pool.submit(
                _solve_one_task,
                t,
                args.model_id,
                args.region,
                args.max_tokens,
                args.log_dir,
                args.output,
                args.errors,
                args.solver,
                system_prompt_text,
                _task_agent,
                _propose,
            ): t
            for t in batch
        }

        try:
            for future in as_completed(futures):
                result = future.result()
                batch_results.append(result)

                status = result.get("status", "error")
                name = result["task_name"]
                elapsed = result.get("total_time", 0)

                if status == "passed":
                    total_passed += 1
                elif status == "failed":
                    total_failed += 1
                else:
                    total_errored += 1

                total = total_passed + total_failed + total_errored
                wall = time.time() - global_start
                rate = f"{total_passed/(total_passed+total_failed)*100:.0f}%" if (total_passed + total_failed) > 0 else "N/A"
                _print_main(
                    f"[{total}/{len(tasks)}] {name}: {status} ({elapsed:.0f}s)  "
                    f"| pass={total_passed} fail={total_failed} err={total_errored} "
                    f"| rate={rate} [wall: {wall:.0f}s]"
                )
        except KeyboardInterrupt:
            print("\n\nInterrupted! Cancelling remaining tasks...")
            for f in futures:
                f.cancel()
            pool.shutdown(wait=False, cancel_futures=True)
            _cleanup_containers()
            break
        else:
            pool.shutdown(wait=True)

        batch_elapsed = time.time() - batch_start
        batch_passed = sum(1 for r in batch_results if r.get("passed"))
        print(f"\nBatch {batch_idx + 1} done in {batch_elapsed:.0f}s — "
              f"{batch_passed}/{len(batch_results)} passed")

        # ── Evolve after this batch ───────────────────────────────
        if not args.no_evolve:
            # v15: Write solver-proposed skill drafts to workspace before evolving
            if args.propose_skill:
                from agent_evolve.contract.workspace import AgentWorkspace
                ws = AgentWorkspace(args.work_dir)
                ws.clear_drafts()
                draft_count = 0
                for r in batch_results:
                    draft = r.get("skill_draft")
                    if draft:
                        ws.write_draft(r["task_name"], draft)
                        draft_count += 1
                if draft_count:
                    print(f"  Solver proposed {draft_count} skill drafts")

            evo_number += 1
            print(f"\n--- Evolution cycle {evo_number} ---")
            evo_t0 = time.time()

            try:
                evolve_result = _run_evolve_cycle(
                    agent, observer, batch_results, evo_number, config,
                    evolver_name=args.evolver,
                )
                evo_elapsed = time.time() - evo_t0
                new_skills = evolve_result.get("new_skills", 0)
                skills_before = evolve_result.get("skills_before", 0)
                skills_after = evolve_result.get("skills_after", 0)
                print(f"Evolution done in {evo_elapsed:.0f}s — "
                      f"{new_skills} new skills (skills: {skills_before} -> {skills_after})")

                # Rebuild prompts with evolved workspace
                system_prompt_text = agent._build_system_prompt()
                log.info("Workspace reloaded: %d skills, %d memories",
                         len(agent.skills), len(agent.memories))

            except Exception as e:
                evo_elapsed = time.time() - evo_t0
                print(f"Evolution FAILED in {evo_elapsed:.0f}s: {str(e)[:200]}")
                log.error("Evolution error: %s", str(e)[:500])
        else:
            print("\n--- Evolution skipped (--no-evolve) ---")

    # ── Final summary ─────────────────────────────────────────────
    wall = time.time() - global_start
    print(f"\n{'='*70}")
    print(f"All done in {wall:.0f}s")
    total_done = total_passed + total_failed + total_errored
    print(f"Passed: {total_passed}/{total_done}")
    if total_passed + total_failed > 0:
        print(f"Accuracy: {total_passed/(total_passed+total_failed)*100:.1f}%")
    print(f"Evolution cycles: {evo_number}")

    if Path(args.output).exists():
        results = load_results(args.output)
        if results:
            metrics = compute_metrics(results)
            print_metrics(metrics)

            metrics_file = Path(args.output).with_suffix(".metrics.json")
            metrics_file.write_text(json.dumps(metrics, indent=2))
            print(f"Metrics saved to {metrics_file}")


if __name__ == "__main__":
    main()
