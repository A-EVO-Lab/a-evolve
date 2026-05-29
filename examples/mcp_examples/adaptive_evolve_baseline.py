#!/usr/bin/env python3
"""Baseline: Solve MCP-Atlas tasks WITHOUT evolution.

This is a control group to compare against adaptive_evolve.
Uses the same agent, same models, same code executor, but NO evolution between batches.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import shutil
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

# Strands SDK uses recursive event_loop dispatch + recursive JSON telemetry
# serialization; Python's default limit (1000) is too shallow for long tool
# chains. Raise process-wide so agent.solve() is protected — the scoped block
# further down in this file does not cover agent.solve().
sys.setrecursionlimit(10000)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agent_evolve.benchmarks.mcp_atlas import McpAtlasBenchmark
from agent_evolve.agents.mcp import McpAgent
from agent_evolve.agents.mcp.key_registry import KeyRegistry
from agent_evolve.agents.mcp.docker_env import McpAtlasContainer, pull_image
from agent_evolve.agents.mcp.mcp_client import McpClientWrapper
from agent_evolve.agents.mcp.code_executor import create_code_executor_tool


# ─────────────────────────────────────────────────────────────────────────────
# MCP workspace preparation (self-contained).
#
# Patches the MCP seed workspace with code-execution guidance before the first
# solve. Idempotent. Inlined here so the baseline does not depend on the legacy
# adaptive_evolve engine (which is not part of this release).
# ─────────────────────────────────────────────────────────────────────────────
_SYSTEM_PROMPT_PATCH = """
## Code Execution

You have an `execute_code` tool that runs Python code with access to all MCP tools via `call_tool(name, args)`.

**Use `execute_code` when:**
- A task requires searching, iterating, or trying multiple values
- You need to chain 3+ tool calls where output feeds into the next
- You need to filter or aggregate large result sets
- A tool call fails and you want to retry with variations

**Use direct tool calls when:**
- The task needs only 1-2 simple tool calls with known parameters
- You need to reason carefully about each intermediate result

Inside `execute_code`, use `print()` to return results. Available: `json`, `re`, `math`, `datetime`.
"""

_CODE_EXEC_SEED_SKILL = """\
---
name: code-execution-patterns
description: When and how to use execute_code for efficient MCP tool orchestration
---

# Code Execution Patterns

Use `execute_code` to write Python when a task involves:

## When to use code execution
- **Search/iteration**: Trying multiple IDs, queries, or parameter values
- **Chaining 3+ tools**: Output of one feeds into the next
- **Filtering large results**: Process data before returning to context
- **Retries with variations**: Same tool with different parameters
- **Aggregation**: Combining results from multiple tool calls

## When to use direct tool calls
- Simple 1-2 tool tasks with known parameters
- Tasks where you need to reason about each result before the next call

## Pattern: Search loop
```python
for candidate in candidates:
    result = call_tool("search_tool", {"query": candidate})
    data = json.loads(result)
    if data.get("found"):
        print(json.dumps(data))
        break
```

## Pattern: Tool chaining
```python
# Get data -> transform -> store result
result1 = call_tool("get_data", {"id": "123"})
data = json.loads(result1)
processed = [x["name"] for x in data if x["active"]]
print(json.dumps(processed))
```

## Pattern: Retry with fallbacks
```python
queries = ["exact match", "fuzzy match", "broad match"]
for q in queries:
    result = call_tool("search", {"query": q})
    if "found" in result:
        print(result)
        break
```
"""


def _prepare_mcp_workspace(workspace_root: Path) -> None:
    """Patch an MCP seed workspace before first solve. Idempotent."""
    prompt_path = workspace_root / "prompts" / "system.md"
    if prompt_path.exists():
        current = prompt_path.read_text()
        if "execute_code" not in current:
            prompt_path.write_text(current.rstrip() + "\n" + _SYSTEM_PROMPT_PATCH)
            log.info("Patched system prompt with code execution guidance")

    skill_dir = workspace_root / "skills" / "code-execution-patterns"
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.exists():
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file.write_text(_CODE_EXEC_SEED_SKILL)
        log.info("Seeded code-execution-patterns skill")


# ─────────────────────────────────────────────────────────────────────────────
# Per-task max-tool-use interrupt
#
# The baseline runs ThreadPoolExecutor(max_workers=5) so MULTIPLE solves run
# concurrently in one process. The unified runner's module-level dict design
# WOULD RACE here. Per-Agent-instance state is used instead via
# event.agent._evolverbench_state. threading.local() carries (limit, task_id)
# from the worker thread into strands.Agent.__init__ (same thread), which
# copies it onto the instance. AfterToolCallEvent callbacks may fire on a
# different Strands-managed thread but only read/write event.agent's own dict.
# ─────────────────────────────────────────────────────────────────────────────

class TaskInterruptedException(Exception):
    """Raised from an AfterToolCallEvent hook when a single solve() call
    exceeds --max-tool-uses-per-task. Carries (task_id, count, limit) for
    downstream reporting."""

    def __init__(self, task_id: str, count: int, limit: int):
        super().__init__(
            f"Task {task_id} hit max_tool_uses={limit} (count={count})"
        )
        self.task_id = task_id
        self.count = count
        self.limit = limit


# Shared collector for interrupted tasks. List.append is atomic in CPython;
# concurrent workers can safely append without a lock.
_interrupted_records: list[dict] = []

# Per-thread carrier: set by solve_and_evaluate on the worker thread before
# BaselineCodeExecAgent.solve() is called, read by patched __init__ on the
# SAME thread, then copied onto the Agent instance. Cleared in finally.
_pending_state = threading.local()


def _install_strands_interrupt_hook() -> None:
    """Monkey-patch strands.Agent.__init__ to register an AfterToolCallEvent
    hook on every Agent built in this process. Idempotent — guarded by
    _evolverbench_patched flag so double-import / test re-use is safe.

    Design for concurrent baseline solves (ThreadPoolExecutor max_workers=5):
    - _pending_state (threading.local) is set on the executor worker thread.
    - strands.Agent.__init__ is called on that SAME thread → reads
      _pending_state.next_solve and copies it onto self._evolverbench_state.
    - AfterToolCallEvent callbacks may fire on a Strands-managed thread but
      only read/write event.agent._evolverbench_state (per-instance → no
      cross-solve races, no shared mutable counters across concurrent workers).
    """
    from strands import Agent as _StrandsAgent
    from strands.hooks import AfterToolCallEvent

    if getattr(_StrandsAgent.__init__, "_evolverbench_patched", False):
        return

    _orig_init = _StrandsAgent.__init__

    def _after_tool_callback(event: AfterToolCallEvent) -> None:
        # Per-agent-instance state — safe for concurrent solves.
        state = getattr(event.agent, "_evolverbench_state", None)
        if state is None:
            return
        limit = state.get("limit", 0)
        if limit <= 0 or state.get("interrupted", False):
            return
        state["count"] += 1
        if state["count"] >= limit:
            state["interrupted"] = True
            # Dual-mechanism stop (mirrors unified runner):
            # (1) stop_event_loop — strands event_loop checks this flag after
            #     each cycle; prevents further model calls even if the
            #     TaskInterruptedException is swallowed by the executor.
            # (2) raise — propagates out of the hook for the common case.
            try:
                request_state = event.invocation_state.setdefault("request_state", {})
                request_state["stop_event_loop"] = True
            except Exception:  # noqa: BLE001
                pass
            raise TaskInterruptedException(
                state["task_id"],
                state["count"],
                limit,
            )

    def _patched_init(self, *args, **kwargs):
        _orig_init(self, *args, **kwargs)
        # Read the per-thread carrier set by solve_and_evaluate on this thread.
        next_solve = getattr(_pending_state, "next_solve", None)
        if next_solve is not None:
            limit, task_id = next_solve
            self._evolverbench_state = {
                "count": 0,
                "limit": limit,
                "task_id": task_id,
                "interrupted": False,
            }
        else:
            # Agent built outside solve_and_evaluate (e.g., judge model).
            self._evolverbench_state = {"limit": 0}
        # Register after Agent's own hook setup so our callback fires last.
        self.hooks.add_callback(AfterToolCallEvent, _after_tool_callback)

    _patched_init._evolverbench_patched = True  # type: ignore[attr-defined]
    _StrandsAgent.__init__ = _patched_init


class BaselineCodeExecAgent(McpAgent):
    """McpAgent with code executor (no evolution)."""

    def solve(self, task, shared_client=None):
        """Standard solve with code executor."""
        from agent_evolve.agents.mcp.tools import create_tool_wrappers
        from agent_evolve.llm._bedrock_config import bedrock_boto_config
        from strands import Agent
        from strands.models import BedrockModel

        enabled_tools = task.metadata.get("enabled_tools", [])
        env_vars = {}
        if self.key_registry:
            server_names = task.metadata.get("mcp_server_names", [])
            env_vars = self.key_registry.get_keys_for_servers(server_names)

        effective_client = shared_client or self.shared_client
        client = effective_client or McpClientWrapper()

        all_tools = client.list_tools()
        if not all_tools:
            from agent_evolve.types import Trajectory
            return Trajectory(task_id=task.id, output="", steps=[{"error": "No tools"}])

        if enabled_tools:
            enabled_set = set(
                t["name"] if isinstance(t, dict) else str(t) for t in enabled_tools
            )
            filtered = [t for t in all_tools if t.get("name") in enabled_set]
        else:
            filtered = all_tools

        if not filtered:
            from agent_evolve.types import Trajectory
            return Trajectory(task_id=task.id, output="", steps=[{"error": "No matching tools"}])

        # Create standard tool wrappers + code executor
        tools = create_tool_wrappers(filtered, client)
        code_exec_tool = create_code_executor_tool(client, filtered)
        tools.append(code_exec_tool)

        logger = logging.getLogger("adaptive_baseline")
        logger.info("Solving %s with %d tools (BASELINE - NO EVOLUTION)", task.id, len(tools))

        model = BedrockModel(
            model_id=self.model_id,
            region_name=self.region,
            max_tokens=self.max_tokens,
            temperature=1.0,
            boto_client_config=bedrock_boto_config(),
        )

        system_prompt = self._build_system_prompt(task_prompt=task.input)
        # NOTE: if _install_strands_interrupt_hook() is active, the patched
        # __init__ reads _pending_state.next_solve (set by solve_and_evaluate
        # on this same thread) and copies it to self._evolverbench_state.
        agent = Agent(model=model, system_prompt=system_prompt, tools=tools)

        response = agent(task.input)
        output = str(response)

        usage = {}
        try:
            u = response.metrics.accumulated_usage
            usage = {
                "input_tokens": u.get("inputTokens", 0),
                "output_tokens": u.get("outputTokens", 0),
                "total_tokens": u.get("totalTokens", 0),
            }
        except Exception:
            pass

        steps = []
        try:
            from agent_evolve.agents.mcp.key_registry import redact_secrets
            old_limit = sys.getrecursionlimit()
            sys.setrecursionlimit(max(old_limit, 10000))

            try:
                for msg in agent.messages:
                    step = {"role": msg.get("role", "")}
                    for block in msg.get("content", []):
                        if "toolUse" in block:
                            tu = block["toolUse"]
                            tool_input = tu.get("input", {})
                            try:
                                json.dumps(tool_input, default=str)[:1000]
                                truncated_input = tool_input
                            except (RecursionError, ValueError):
                                truncated_input = {"_error": "input too complex"}

                            step.setdefault("tool_calls", []).append({
                                "tool": tu.get("name", ""),
                                "input": truncated_input,
                                "toolUseId": tu.get("toolUseId", ""),
                            })
                        elif "toolResult" in block:
                            tr = block["toolResult"]
                            result_content = tr.get("content", [])
                            truncated = []
                            for item in (result_content if isinstance(result_content, list) else [result_content]):
                                if isinstance(item, dict) and "text" in item:
                                    text = item["text"]
                                    truncated.append({"text": text[:5000] + ("..." if len(text) > 5000 else "")})
                                else:
                                    try:
                                        truncated.append({"text": str(item)[:5000]})
                                    except:
                                        truncated.append({"text": "[unserializable]"})
                            step.setdefault("tool_results", []).append({
                                "toolUseId": tr.get("toolUseId", ""),
                                "status": tr.get("status", ""),
                                "content": truncated,
                            })
                        elif "text" in block:
                            step["text"] = block["text"][:5000]
                    steps.append(step)

                if env_vars:
                    steps = json.loads(redact_secrets(json.dumps(steps, default=str), env_vars))
                    output = redact_secrets(output, env_vars)
            finally:
                sys.setrecursionlimit(old_limit)

        except Exception as e:
            logging.getLogger("adaptive_baseline").warning(
                "Failed to extract conversation: %s", e, exc_info=True
            )
            steps = []

        from agent_evolve.types import Trajectory
        return Trajectory(task_id=task.id, output=output, steps=steps)


def main():
    p = argparse.ArgumentParser(description="Baseline: Solve tasks WITHOUT evolution")
    p.add_argument("--solver-model", type=str, default="us.anthropic.claude-opus-4-6-v1")
    p.add_argument("--judge-model", type=str, default="us.anthropic.claude-sonnet-4-6")
    p.add_argument("--region", type=str, default="us-west-2")
    p.add_argument("--max-tokens", type=int, default=16384)
    p.add_argument("--docker-image", type=str, default=None)
    p.add_argument("--external-container-url", type=str, default=None,
                   help="Use external container (e.g., http://localhost:1984)")
    p.add_argument("--env-file", type=str, default=None)
    p.add_argument("--limit", type=int, default=500)
    p.add_argument("--batch-size", type=int, default=30)
    p.add_argument("--workers", type=int, default=5,
                   help="Maximum number of tasks to solve/evaluate concurrently within each batch")
    p.add_argument("--seed-workspace", type=str, default="seed_workspaces/mcp")
    p.add_argument("--work-dir", type=str, default="./evolution_workdir/adaptive_baseline")
    p.add_argument("--output-dir", type=str, default="results_adaptive_evolve_baseline")
    p.add_argument(
        "--max-tool-uses-per-task",
        type=int,
        default=int(os.environ.get("MAX_TOOL_USES_PER_TASK", "0")),
        help=(
            "Maximum AfterToolCallEvent firings allowed in a single solve() call. "
            "0 = disabled (default). When a task hits the limit it is recorded as "
            "INTERRUPTED in summary.csv and interrupted_tasks.jsonl."
        ),
    )
    args = p.parse_args()
    if args.batch_size <= 0:
        p.error("--batch-size must be positive")
    if args.workers <= 0:
        p.error("--workers must be positive")

    # Install the interrupt hook if the cap is enabled. Idempotent.
    if args.max_tool_uses_per_task > 0:
        _install_strands_interrupt_hook()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    for n in ("botocore", "urllib3", "httpcore", "httpx",
              "strands.models", "strands.tools", "strands.telemetry"):
        logging.getLogger(n).setLevel(logging.WARNING)
    log = logging.getLogger("adaptive_baseline")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    work_dir = Path(args.work_dir)
    seed_dir = Path(args.seed_workspace)
    if not work_dir.exists() and seed_dir.exists():
        work_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(seed_dir, work_dir)
        log.info("Copied seed workspace %s -> %s", seed_dir, work_dir)

    # Prepare workspace with code execution support (same as adaptive_evolve).
    # Skip when MCP_SKIP_PREPARE_WORKSPACE=1 to run a blank-slate baseline
    # (raw 1309-byte seed prompt, empty skills/) — used for ablations that
    # measure the contribution of V3's prepare_workspace patch separately
    # from the evolution loop.
    import os as _os
    if _os.environ.get("MCP_SKIP_PREPARE_WORKSPACE") in ("1", "true", "True"):
        log.info("MCP_SKIP_PREPARE_WORKSPACE=1 — skipping prepare_workspace "
                 "(blank-slate baseline: raw seed prompt + empty skills)")
    else:
        _prepare_mcp_workspace(Path(work_dir))

    bm = McpAtlasBenchmark(
        shuffle=False,
        eval_model_id=args.judge_model,
        eval_region=args.region,
        use_litellm=False,
    )

    key_registry = None
    if args.env_file:
        key_registry = KeyRegistry(env_file_path=args.env_file)
        key_registry.load()
        log.info("Loaded %d API key(s)", len(key_registry.get_loaded_key_names()))

    tasks = bm.get_tasks(split="test", limit=args.limit, key_registry=key_registry)
    log.info("Tasks after key_registry filter: %d", len(tasks))

    summary_path = out_dir / "summary.csv"
    complete_path = out_dir / "RUN_COMPLETE.json"
    done_ids = set()
    if summary_path.exists():
        try:
            with open(summary_path) as f:
                for row in csv.DictReader(f):
                    tid = row.get("task_id")
                    if tid:
                        done_ids.add(tid)
        except Exception as e:
            log.warning(
                "summary.csv resume read encountered %s; using partial done_ids (%d)",
                e, len(done_ids),
            )
        log.info("Resuming: %d tasks already completed", len(done_ids))

    write_header = not summary_path.exists()

    all_env_vars = {}
    if args.docker_image and key_registry:
        all_env_vars = {
            name: entry.value
            for name, entry in key_registry._keys.items()
            if entry.value
        }

    container = None
    shared_base_url = None

    if args.external_container_url:
        # Use external container
        log.info("Connecting to external container at %s", args.external_container_url)
        shared_base_url = args.external_container_url.rstrip("/")
        log.info("Connected to external container.")
    elif args.docker_image:
        if not pull_image(args.docker_image):
            log.error("Failed to pull image %s", args.docker_image)
            sys.exit(1)
        container = McpAtlasContainer(
            args.docker_image,
            container_name=os.environ.get("MCP_CONTAINER_NAME", "mcp-atlas-adaptive-baseline"),
            env_vars=all_env_vars,
        )
        log.info("Starting shared MCP-Atlas container ...")
        container.start()
        shared_base_url = container.base_url.rstrip("/")
        log.info("Shared container ready.")

    remaining = [t for t in tasks if t.id not in done_ids]
    log.info("Remaining tasks: %d", len(remaining))

    batches = [remaining[i:i + args.batch_size]
               for i in range(0, len(remaining), args.batch_size)]

    total_passed = 0
    total_failed = 0
    total_errors = 0
    total_interrupted = 0
    thread_local = threading.local()

    # Capture max_tool_uses_per_task as a local so each thread closure
    # gets its own reference (not a live read of args).
    max_tool_uses = args.max_tool_uses_per_task

    def worker_benchmark() -> McpAtlasBenchmark:
        benchmark = getattr(thread_local, "benchmark", None)
        if benchmark is None:
            benchmark = McpAtlasBenchmark(
                shuffle=False,
                eval_model_id=args.judge_model,
                eval_region=args.region,
                use_litellm=False,
            )
            thread_local.benchmark = benchmark
        return benchmark

    def solve_and_evaluate(task, task_index: int, batch_len: int) -> dict:
        sid = task.id.replace("/", "_")
        client = (
            McpClientWrapper(base_url=shared_base_url)
            if shared_base_url
            else McpClientWrapper()
        )
        try:
            log.info("[%d/%d] Solving task %s ...", task_index, batch_len, task.id)
            # Set the per-thread carrier so that strands.Agent.__init__
            # (called inside agent.solve()) can copy (limit, task_id) onto
            # the new Agent instance. Cleared in finally on this same thread.
            if max_tool_uses > 0:
                _pending_state.next_solve = (max_tool_uses, task.id)
            agent = BaselineCodeExecAgent(
                workspace_dir=work_dir,
                model_id=args.solver_model,
                region=args.region,
                max_tokens=args.max_tokens,
                docker_image=None,
                key_registry=key_registry,
            )
            t0 = time.time()
            try:
                trajectory = agent.solve(task, shared_client=client)
            except TaskInterruptedException as exc:
                # Record and return an interrupted sentinel — write_result
                # will emit an INTERRUPTED CSV row so done_ids includes this
                # task on resume and it won't be retried.
                _interrupted_records.append({
                    "task_id": exc.task_id,
                    "tool_use_count": exc.count,
                    "limit": exc.limit,
                    "reason": "max_tool_uses",
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                })
                log.warning(
                    "[%d] Task %s INTERRUPTED: tool_use count %d reached limit %d",
                    task_index, exc.task_id, exc.count, exc.limit,
                )
                return {
                    "task_id": task.id,
                    "sid": sid,
                    "task_index": task_index,
                    "interrupted": True,
                    "tool_use_count": exc.count,
                    "limit": exc.limit,
                }
            elapsed = time.time() - t0
            feedback = worker_benchmark().evaluate(task, trajectory)
            result = "PASS" if feedback.success else "FAIL"
            return {
                "task_id": task.id,
                "sid": sid,
                "task_index": task_index,
                "trajectory": trajectory,
                "feedback": feedback,
                "result": result,
                "elapsed": elapsed,
            }
        except Exception as e:
            return {
                "task_id": task.id,
                "sid": sid,
                "task_index": task_index,
                "error": str(e),
                "traceback": traceback.format_exc(),
            }
        finally:
            if max_tool_uses > 0:
                _pending_state.next_solve = None
            client.close()

    def write_result(record: dict) -> tuple[int, int, int, int]:
        """Returns (passed, failed, errors, interrupted) increment tuple."""
        task_id = record["task_id"]
        task_index = record["task_index"]

        if record.get("interrupted"):
            count = record["tool_use_count"]
            limit = record["limit"]
            detail = f"MAX_TOOL_USES_PER_TASK limit hit at count={count}/{limit}"
            log.warning("[%d] INTERRUPTED task %s: %s", task_index, task_id, detail)
            writer.writerow([task_id, "INTERRUPTED", 0, "0", 0, detail[:300]])
            csvfile.flush()
            return 0, 0, 0, 1

        if "error" in record:
            log.error("[%d] ERROR on task %s: %s",
                      task_index, task_id, record["error"])
            log.error(record["traceback"])
            writer.writerow([task_id, "ERROR", 0, "0", 0, record["error"][:300]])
            csvfile.flush()
            return 0, 0, 1, 0

        trajectory = record["trajectory"]
        feedback = record["feedback"]
        result = record["result"]
        elapsed = record["elapsed"]
        sid = record["sid"]

        (out_dir / f"output_{sid}.txt").write_text(trajectory.output)
        (out_dir / f"conversation_{sid}.json").write_text(
            json.dumps(trajectory.steps, indent=2,
                       ensure_ascii=False, default=str))

        writer.writerow([task_id, result, feedback.score, f"{elapsed:.1f}",
                         len(trajectory.output), feedback.detail[:300]])
        csvfile.flush()

        log.info("[%d] %s | %s | Score: %.2f | Time: %.1fs",
                 task_index, task_id, result, feedback.score, elapsed)
        if feedback.success:
            return 1, 0, 0, 0
        return 0, 1, 0, 0

    try:
        with open(summary_path, "a", newline="") as csvfile:
            writer = csv.writer(csvfile)
            if write_header:
                writer.writerow(["task_id", "result", "score", "elapsed_s",
                                 "output_len", "detail"])

            for batch_idx, batch in enumerate(batches):
                log.info("=" * 70)
                log.info("BATCH %d/%d (%d tasks) | NO EVOLUTION",
                         batch_idx + 1, len(batches), len(batch))
                log.info("=" * 70)
                batch_workers = min(args.workers, len(batch))
                log.info("Batch workers: %d", batch_workers)

                if batch_workers == 1:
                    for i, task in enumerate(batch, 1):
                        passed, failed, errors, interrupted = write_result(
                            solve_and_evaluate(task, i, len(batch))
                        )
                        total_passed += passed
                        total_failed += failed
                        total_errors += errors
                        total_interrupted += interrupted
                else:
                    with ThreadPoolExecutor(max_workers=batch_workers) as executor:
                        futures = [
                            executor.submit(solve_and_evaluate, task, i, len(batch))
                            for i, task in enumerate(batch, 1)
                        ]
                        for future in as_completed(futures):
                            passed, failed, errors, interrupted = write_result(future.result())
                            total_passed += passed
                            total_failed += failed
                            total_errors += errors
                            total_interrupted += interrupted

                batch_passed = total_passed
                log.info("Batch %d completed: %d passed so far",
                         batch_idx + 1, batch_passed)

    finally:
        if container:
            container.stop()

    # Write interrupted-task ledger (before Results log, after batch loop).
    if _interrupted_records:
        interrupted_path = out_dir / "interrupted_tasks.jsonl"
        with open(interrupted_path, "w") as f:
            for rec in _interrupted_records:
                f.write(json.dumps(rec) + "\n")
        log.info("Wrote interrupted task ledger (%d entries): %s",
                 len(_interrupted_records), interrupted_path)

    total = total_passed + total_failed + total_errors + total_interrupted
    log.info("=" * 70)
    log.info("DONE: %d tasks | %d passed | %d failed | %d errors | %d interrupted",
             total, total_passed, total_failed, total_errors, total_interrupted)
    if total:
        log.info("Overall pass rate: %.1f%%", total_passed / total * 100)
    log.info("Results: %s", summary_path)

    sentinel_payload = json.dumps({
        "total": total,
        "passed": total_passed,
        "failed": total_failed,
        "errors": total_errors,
        "interrupted_count": len(_interrupted_records),
        "summary_csv": str(summary_path),
    }, indent=2)
    sentinel_tmp = complete_path.with_suffix(complete_path.suffix + ".tmp")
    sentinel_tmp.write_text(sentinel_payload)
    os.replace(sentinel_tmp, complete_path)
    log.info("Wrote completion sentinel: %s", complete_path)


if __name__ == "__main__":
    main()
