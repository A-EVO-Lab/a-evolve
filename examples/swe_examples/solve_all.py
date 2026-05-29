#!/usr/bin/env python3
"""Solve all tasks in SWE-bench verified mini in parallel, simple agent (bash only)."""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

# Strands SDK uses recursive event_loop dispatch + recursive JSON telemetry
# serialization; Python's default limit (1000) is too shallow for long tool chains.
sys.setrecursionlimit(10000)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# SYSTEM_PROMPT = """\
# You are an expert software engineer, and you are interacting with a
# standard Ubuntu machine with bash commands and python tools.
# You will be given an issue to fix.
# Your objective is to modify the code on the file system to fix the issue.
# The repository code is already checked out to /testbed.
# You do NOT need to change branches or commit the fix.
# Once you are done, use your submit tool.
# """

SYSTEM_PROMPT = """\
You are an expert software engineer tasked with resolving GitHub issues by producing code patches.

## Approach

1. **Understand the issue**: Read the issue description carefully. Identify the root cause.
2. **Locate relevant code**: Use search tools to find the files and functions involved.
3. **Plan the fix**: Think step-by-step about what needs to change and why.
4. **Implement the fix**: Make minimal, precise edits. Avoid unnecessary changes.
5. **Verify**: Run existing tests to confirm the fix works and doesn't break anything.

## Guidelines

- Prefer small, focused patches over large rewrites.
- Always check for edge cases the issue description mentions.
- If the issue includes a reproduction script, use it to verify your fix.
- When in doubt, look at how similar patterns are handled elsewhere in the codebase.
"""


def _materialize_baseline_workspace(out_dir: Path, system_prompt: str) -> None:
    # Snapshot of what solve_all.py actually used: hardcoded SYSTEM_PROMPT + 2 tools.
    # NOT seed_workspaces/swe/ (that 4-tool workspace was never read by this runner).
    import shutil
    ws = out_dir / "workspace"
    (ws / "prompts").mkdir(parents=True, exist_ok=True)
    (ws / "tools").mkdir(parents=True, exist_ok=True)
    (ws / "prompts" / "system.md").write_text(system_prompt)
    seed_tools = Path(__file__).resolve().parent.parent.parent / "seed_workspaces" / "swe" / "tools"
    for t in ("bash.py", "submit.py"):
        src = seed_tools / t
        if src.is_file():
            shutil.copy(src, ws / "tools" / t)
    (ws / "manifest.yaml").write_text(
        "agent: solve_all_baseline\n"
        "tools:\n  - bash\n  - submit\n"
        "system_prompt: prompts/system.md\n"
        "skills: []\n"
    )


def solve_one_task(task_dict: dict, args_dict: dict) -> dict:
    """Solve a single task in its own process."""
    import json
    import logging
    import time
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from agent_evolve.agents.swe.env import SWEBenchContainer, pull_image

    # Load tools from seed_workspaces/swe/tools/ (no longer in agent_evolve.agents.swe.tools).
    # __file__ is examples/swe_examples/solve_all.py; three .parent levels reach
    # the V3 repo root (A-EVOLVE-V3/), where seed_workspaces/ lives.
    _tools_dir = Path(__file__).resolve().parent.parent.parent / "seed_workspaces" / "swe" / "tools"
    import importlib.util as _ilu

    def _load_tool_mod(name):
        _spec = _ilu.spec_from_file_location(name, _tools_dir / f"{name}.py")
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        return _mod

    _bash_mod = _load_tool_mod("bash")
    _submit_mod = _load_tool_mod("submit")
    bash = _bash_mod.bash
    submit = _submit_mod.submit

    def set_container_name(name):
        _bash_mod.reset(container_name=name)
        _submit_mod.reset(container_name=name)

    def reset_submit_state():
        _submit_mod.reset()

    was_submitted = _submit_mod.was_submitted
    get_submitted_patch = _submit_mod.get_submitted_patch
    from agent_evolve.types import Feedback, Task, Trajectory
    from agent_evolve.benchmarks.swe_verified_mini import SweVerifiedMiniBenchmark as SweVerifiedBenchmark
    from strands import Agent
    from strands.models import BedrockModel
    from strands.hooks.events import BeforeToolCallEvent, MessageAddedEvent
    import copy as _copy

    logging.basicConfig(
        level=logging.INFO,
        format=f"%(asctime)s [{task_dict['id']}] %(message)s",
    )
    for n in ("botocore", "urllib3", "httpcore", "httpx",
              "strands.models", "strands.tools", "strands.telemetry"):
        logging.getLogger(n).setLevel(logging.WARNING)
    log = logging.getLogger("worker")

    task = Task(
        id=task_dict["id"],
        input=task_dict["input"],
        metadata=task_dict["metadata"],
    )
    iid = task.id
    img = task.metadata["docker_image"]
    max_turns = args_dict["max_turns"]

    result = {
        "instance_id": iid,
        "success": False,
        "score": 0.0,
        "detail": "",
        "elapsed": 0.0,
        "patch_len": 0,
        "turns": 0,
        "error": None,
    }

    try:
        pull_image(img)
    except Exception as e:
        result["error"] = f"Image pull failed: {e}"
        result["detail"] = result["error"]
        log.error(result["error"])
        return result

    user_prompt = (
        f"Please solve the following coding issue:\n\n{task.input}"
    )

    # Turn limiter hook
    tool_call_count = [0]

    def turn_limiter(event: BeforeToolCallEvent):
        if was_submitted():
            event.cancel_tool = "Already submitted. No more tool calls."
            return
        tool_call_count[0] += 1
        if tool_call_count[0] > max_turns:
            event.cancel_tool = f"Turn limit reached ({max_turns}). Call submit now."

    import hashlib as _hashlib
    import uuid as _uuid
    _cell_tag = _hashlib.md5(str(args_dict.get("output_dir", "")).encode()).hexdigest()
    _per_call_tag = _uuid.uuid4().hex
    container_name = f"swe-solve-{iid.replace('/', '_').replace('__', '-')}-{_cell_tag}-{_per_call_tag}"
    ctr = SWEBenchContainer(img, container_name=container_name)
    try:
        ctr.start()
        set_container_name(ctr.container_name)
        reset_submit_state()
        log.info("Container: %s | Image: %s", ctr.container_name, img)

        model = BedrockModel(
            model_id=args_dict["model_id"],
            region_name=args_dict["region"],
            max_tokens=args_dict["max_tokens"],
        )

        agent_kwargs = dict(
            model=model,
            system_prompt=SYSTEM_PROMPT,
            tools=[bash, submit],
            callback_handler=None,
        )
        # Opt-in conversation-manager overrides. When neither flag is passed,
        # Agent() falls through to Strands' implicit default
        # (SlidingWindowConversationManager(window_size=40)) — bit-for-bit
        # identical to the historical baseline.
        pin = args_dict.get("pin_first_message", False)
        ws = args_dict.get("window_size")  # None if unset
        if pin or ws is not None:
            from strands.agent.conversation_manager.sliding_window_conversation_manager import (
                SlidingWindowConversationManager,
            )
            effective_ws = ws if ws is not None else 40
            if pin:
                from agent_evolve.agents.swe.conversation_manager import (
                    PinnedFirstMessageManager,
                )
                agent_kwargs["conversation_manager"] = PinnedFirstMessageManager(
                    window_size=effective_ws,
                )
            else:
                agent_kwargs["conversation_manager"] = SlidingWindowConversationManager(
                    window_size=effective_ws,
                )
        agent = Agent(**agent_kwargs)
        agent.hooks.add_callback(BeforeToolCallEvent, turn_limiter)

        # Pre-window full trajectory capture: SlidingWindowConversationManager
        # mutates agent.messages in place after each invocation, so the
        # post-call list may be windowed. MessageAddedEvent fires as each
        # message is appended, before manager trimming — deep-copy here gives
        # the paper-grade complete transcript.
        full_conversation: list = []

        def _message_collector(event: MessageAddedEvent):
            try:
                full_conversation.append(_copy.deepcopy(event.message))
            except Exception as conv_err:
                log.warning("MessageAddedEvent capture failed: %s", conv_err)

        agent.hooks.add_callback(MessageAddedEvent, _message_collector)

        log.info("Solving (max_turns=%d)...", max_turns)
        t0 = time.time()

        response = None
        try:
            response = agent(user_prompt)
        except Exception as e:
            log.warning("Agent error: %s", e)

        elapsed = time.time() - t0
        log.info("Done in %.1fs (%d tool calls)", elapsed, tool_call_count[0])

        patch = get_submitted_patch() or ctr.get_diff()
        result["elapsed"] = elapsed
        result["patch_len"] = len(patch)
        result["turns"] = tool_call_count[0]
        result["submitted"] = was_submitted()

        # Save patch
        sid = iid.replace("/", "_")
        out_dir = Path(args_dict["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        patches_dir = out_dir / "patches"
        patches_dir.mkdir(parents=True, exist_ok=True)
        (patches_dir / f"{sid}.diff").write_text(patch)

        # Save windowed conversation (post-manager) — comparable to evolve cells
        try:
            conv_dir = out_dir / "conversations"
            conv_dir.mkdir(parents=True, exist_ok=True)
            (conv_dir / f"{sid}.json").write_text(
                json.dumps(agent.messages, indent=2, default=str, ensure_ascii=False)
            )
        except Exception as conv_err:
            log.warning("Failed to save windowed conversation: %s", conv_err)

        # Save pre-window full transcript — paper-level complete trajectory
        try:
            conv_full_dir = out_dir / "conversations_full"
            conv_full_dir.mkdir(parents=True, exist_ok=True)
            (conv_full_dir / f"{sid}.json").write_text(
                json.dumps(full_conversation, indent=2, default=str, ensure_ascii=False)
            )
            log.info("Saved trajectories: windowed=%d msgs, full=%d msgs",
                     len(agent.messages), len(full_conversation))
        except Exception as conv_err:
            log.warning("Failed to save full conversation: %s", conv_err)

        # Per-task token usage (Bedrock-style camelCase keys)
        try:
            usage = None
            if response is not None and getattr(response, "metrics", None) is not None:
                usage = response.metrics.accumulated_usage
            if usage is None:
                usage = agent.event_loop_metrics.accumulated_usage
            result["cumulative_input_tokens"] = usage.get("inputTokens", 0)
            result["cumulative_output_tokens"] = usage.get("outputTokens", 0)
            result["cumulative_total_tokens"] = usage.get("totalTokens", 0)
        except Exception as usage_err:
            log.warning("Failed to read token usage: %s", usage_err)

        # Evaluate
        if args_dict["run_eval"]:
            bm = SweVerifiedBenchmark(dataset_name=args_dict["dataset"], shuffle=False)
            fb = bm.evaluate(task, Trajectory(task_id=task.id, output=patch))
            result["success"] = fb.success
            result["score"] = fb.score
            result["detail"] = fb.detail[:500]
            log.info("RESULT: %s | Score: %.2f | %s",
                     "PASS" if fb.success else "FAIL", fb.score, fb.detail[:200])
        else:
            result["detail"] = "Evaluation skipped"

    except Exception as e:
        result["error"] = str(e)
        result["detail"] = f"Worker error: {e}"
        log.error("Failed: %s", e, exc_info=True)
    finally:
        ctr.stop()

    return result


def main():
    p = argparse.ArgumentParser(description="Solve all SWE-bench tasks in parallel")
    p.add_argument("--dataset", type=str, default="MariusHobbhahn/swe-bench-verified-mini")
    p.add_argument("--model-id", type=str, default="us.anthropic.claude-opus-4-5-20251101-v1:0")
    p.add_argument("--region", type=str, default="us-west-2")
    p.add_argument("--max-tokens", type=int, default=16384)
    p.add_argument("--max-turns", type=int, default=150,
                   help="Max tool call rounds per task (default: 150)")
    p.add_argument("--workers", type=int, default=16,
                   help="Number of parallel workers (default: 16)")
    p.add_argument("--eval", "--no-eval", dest="run_eval",
                   action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--output-dir", type=str, default="swe_results",
                   help="Directory for patches and results")
    p.add_argument("--limit", type=int, default=500,
                   help="Max number of tasks to load")
    p.add_argument("--pin-first-message", "--no-pin-first-message",
                   dest="pin_first_message",
                   action=argparse.BooleanOptionalAction, default=False,
                   help="Pin the first user message (problem statement) so it "
                        "is never evicted by the sliding window. Defaults to "
                        "False (Strands default behaviour).")
    p.add_argument("--window-size", type=int, default=None,
                   help="Conversation-manager window size. Unset → Strands "
                        "default (40). Pass an int to override; the chosen "
                        "manager is PinnedFirstMessageManager when "
                        "--pin-first-message is set, otherwise "
                        "SlidingWindowConversationManager.")
    p.add_argument("--task-ids", type=str, default=None,
                   help="Optional comma-separated list (or @path/to/file with "
                        "one id per line) of task instance_ids to run. When "
                        "set, --limit is ignored and only the listed tasks "
                        "are processed. Used by surgical re-run of "
                        "docker-failed tasks.")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    log = logging.getLogger("main")

    # Materialize workspace/ snapshot early so it survives even if run is killed
    # mid-way (paper-level provenance for partial runs).
    _materialize_baseline_workspace(Path(args.output_dir), SYSTEM_PROMPT)

    from agent_evolve.benchmarks.swe_verified_mini import SweVerifiedMiniBenchmark as SweVerifiedBenchmark
    bm = SweVerifiedBenchmark(dataset_name=args.dataset, shuffle=False)
    tasks = bm.get_tasks(split="test", limit=args.limit)
    log.info("Loaded %d tasks from %s", len(tasks), args.dataset)

    if args.task_ids:
        if args.task_ids.startswith("@"):
            with open(args.task_ids[1:]) as f:
                wanted = {line.strip() for line in f if line.strip()}
        else:
            wanted = {x.strip() for x in args.task_ids.split(",") if x.strip()}
        tasks = [t for t in tasks if t.id in wanted]
        missing = wanted - {t.id for t in tasks}
        log.info("--task-ids filter: %d requested, %d found in dataset, %d missing",
                 len(wanted), len(tasks), len(missing))
        if missing:
            log.warning("Missing task ids (first 10): %s", sorted(missing)[:10])

    task_dicts = [{"id": t.id, "input": t.input, "metadata": t.metadata} for t in tasks]
    args_dict = {
        "model_id": args.model_id,
        "region": args.region,
        "max_tokens": args.max_tokens,
        "max_turns": args.max_turns,
        "run_eval": args.run_eval,
        "output_dir": args.output_dir,
        "dataset": args.dataset,
        "pin_first_message": args.pin_first_message,
        "window_size": args.window_size,
    }

    log.info("Starting %d workers for %d tasks...", args.workers, len(task_dicts))
    t0 = time.time()

    results = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(solve_one_task, td, args_dict): td["id"]
            for td in task_dicts
        }
        for future in as_completed(futures):
            iid = futures[future]
            try:
                r = future.result()
                results.append(r)
                passed_so_far = sum(1 for x in results if x["success"])
                status = "PASS" if r["success"] else "FAIL"
                log.info("[%d/%d] %s %s (%.1fs, score=%.2f) | running: %d/%d passed",
                         len(results), len(task_dicts), status, iid,
                         r["elapsed"], r["score"], passed_so_far, len(results))
            except Exception as e:
                log.error("[%d/%d] CRASH %s: %s",
                          len(results) + 1, len(task_dicts), iid, e)
                results.append({
                    "instance_id": iid, "success": False, "score": 0.0,
                    "detail": f"Process crashed: {e}", "elapsed": 0, "patch_len": 0,
                    "turns": 0, "error": str(e),
                })

    total_time = time.time() - t0
    passed = sum(1 for r in results if r["success"])
    total = len(results)

    # Save results (workspace/ was materialized earlier — see top of main())
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results_file = out_dir / "results.json"
    results_file.write_text(json.dumps(results, indent=2, ensure_ascii=False))

    # Summary
    print(f"\n{'='*70}")
    print(f"SUMMARY: {passed}/{total} passed ({100*passed/total:.1f}%) in {total_time:.0f}s")
    print(f"Results saved to {results_file}")
    print(f"{'='*70}")
    for r in sorted(results, key=lambda x: x["instance_id"]):
        status = "PASS" if r["success"] else "FAIL"
        err = f" | ERROR: {r['error'][:80]}" if r.get("error") else ""
        print(f"  {status} {r['instance_id']} | {r['elapsed']:.0f}s | score={r['score']}{err}")


if __name__ == "__main__":
    main()
