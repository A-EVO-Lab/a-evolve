#!/usr/bin/env python3
"""Evolve MCP-Atlas agent with adaptive self-evolution.

Uses the AdaptiveEvolveEngine which adds:
- Per-claim feedback integration
- Task-type stratification
- Judge feedback mining
- Meta-evolution learning
- Graduated scope evolution
- Auto-skill seeding
- Code execution capability (execute_code tool)

Usage:
    uv run python examples/adaptive_evolve_all.py \
        --env-file .env \
        --docker-image ghcr.io/scaleapi/mcp-atlas:latest
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agent_evolve.benchmarks.mcp_atlas import McpAtlasBenchmark
from agent_evolve.agents.mcp import McpAgent
from agent_evolve.agents.mcp.key_registry import KeyRegistry
from agent_evolve.agents.mcp.docker_env import McpAtlasContainer, pull_image
from agent_evolve.agents.mcp.mcp_client import McpClientWrapper
from agent_evolve.agents.mcp.code_executor import create_code_executor_tool
from agent_evolve.config import EvolveConfig
from agent_evolve.engine.observer import Observer
from agent_evolve.types import Observation
from agent_evolve.algorithms.adaptive_evolve import (
    AdaptiveEvolveEngine,
    AdaptivePromptConfig,
)


class CodeExecMcpAgent(McpAgent):
    """McpAgent extended with the execute_code tool."""

    def solve(self, task, shared_client=None):
        """Override solve to inject the code executor tool."""
        from agent_evolve.agents.mcp.tools import create_tool_wrappers
        from agent_evolve.agents.mcp.docker_env import McpAtlasContainer
        from strands import Agent
        from strands.models import BedrockModel

        enabled_tools = task.metadata.get("enabled_tools", [])
        env_vars = {}
        if self.key_registry:
            server_names = task.metadata.get("mcp_server_names", [])
            env_vars = self.key_registry.get_keys_for_servers(server_names)

        effective_client = shared_client or self.shared_client
        client = effective_client or McpClientWrapper()

        # Discover tools
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

        logger = logging.getLogger("adaptive_evolve_all")
        logger.info("Solving %s with %d tools", task.id, len(tools))

        # Build agent
        # NOTE: stop_sequences=[] breaks models that don't support the
        # stopSequences field on Converse (gpt-oss-120b, qwen3-235b/32b,
        # minimax, kimi) — they return ValidationException. Omitting the
        # kwarg lets Bedrock use the default (no stop sequences) which
        # all models accept.
        model = BedrockModel(
            model_id=self.model_id,
            region_name=self.region,
            max_tokens=self.max_tokens,
            temperature=1.0,  # Default reasoning temperature
        )
        # Pass task.input for skill selection (filters skills by relevance)
        system_prompt = self._build_system_prompt(task_prompt=task.input)
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

        # Capture trajectory from the strands agent (not the response)
        steps = []
        try:
            from agent_evolve.agents.mcp.key_registry import redact_secrets
            import sys

            # Increase recursion limit temporarily to handle deep tool call chains
            old_recursion_limit = sys.getrecursionlimit()
            sys.setrecursionlimit(5000)

            try:
                for msg in agent.messages:
                    step = {"role": msg.get("role", "")}
                    for block in msg.get("content", []):
                        if "toolUse" in block:
                            tu = block["toolUse"]
                            # Safely extract and truncate input to prevent circular refs
                            tool_input = tu.get("input", {})
                            try:
                                # Test if serializable, truncate if too deep
                                json.dumps(tool_input, default=str)[:1000]
                                truncated_input = tool_input
                            except (RecursionError, ValueError):
                                truncated_input = {"_error": "input too complex to serialize"}

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
                                    truncated.append({"text": text[:5000] + ("... [truncated]" if len(text) > 5000 else "")})
                                else:
                                    # Safely convert to string to avoid circular refs
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
                # Restore original recursion limit
                sys.setrecursionlimit(old_recursion_limit)

        except Exception as e:
            logging.getLogger("adaptive_evolve_all").warning(
                "Failed to extract conversation: %s", e, exc_info=True
            )
            # Use empty steps on failure to prevent cascading errors
            steps = []

        from agent_evolve.types import Trajectory
        return Trajectory(task_id=task.id, output=output, steps=steps)


def main():
    p = argparse.ArgumentParser(description="Evolve MCP-Atlas agent with adaptive evolution")
    p.add_argument("--solver-model", type=str,
                    default="us.anthropic.claude-opus-4-6-v1")
    p.add_argument("--evolver-model", type=str,
                    default="us.anthropic.claude-opus-4-6-v1")
    p.add_argument("--judge-model", type=str,
                    default="us.anthropic.claude-sonnet-4-6")
    p.add_argument("--region", type=str, default="us-west-2")
    p.add_argument("--max-tokens", type=int, default=16384)
    p.add_argument("--docker-image", type=str, default=None)
    p.add_argument("--env-file", type=str, default=None)
    p.add_argument("--limit", type=int, default=500)
    p.add_argument("--batch-size", type=int, default=30)
    p.add_argument("--seed-workspace", type=str, default="seed_workspaces/mcp")
    p.add_argument("--work-dir", type=str, default="./evolution_workdir/mcp_adaptive")
    p.add_argument("--output-dir", type=str, default="results_adaptive_evolve")
    p.add_argument("--no-filter", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    for n in ("botocore", "urllib3", "httpcore", "httpx",
              "strands.models", "strands.tools", "strands.telemetry"):
        logging.getLogger(n).setLevel(logging.WARNING)
    log = logging.getLogger("adaptive_evolve_all")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Setup workspace (copy seed + patch for code execution) ──
    work_dir = Path(args.work_dir)
    seed_dir = Path(args.seed_workspace)
    if not work_dir.exists() and seed_dir.exists():
        work_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(seed_dir, work_dir)
        log.info("Copied seed workspace %s -> %s", seed_dir, work_dir)

    # Patch workspace for code execution BEFORE first solve.
    # Skip when MCP_SKIP_PREPARE_WORKSPACE=1 to run a blank-slate evolve
    # (raw 1309-byte seed prompt, empty skills/) — used in ablations that
    # measure the contribution of V3's prepare_workspace patch separately
    # from the evolution loop. Mirrors the gate added to
    # adaptive_evolve_baseline.py:202-213 so baseline + evolve stay in sync.
    import os as _os
    if _os.environ.get("MCP_SKIP_PREPARE_WORKSPACE") in ("1", "true", "True"):
        log.info("MCP_SKIP_PREPARE_WORKSPACE=1 — skipping prepare_workspace "
                 "(blank-slate evolve: raw seed prompt + empty skills)")
    else:
        AdaptiveEvolveEngine.prepare_workspace(work_dir)

    # ── Load benchmark & keys ────────────────────────────────
    # Use Bedrock for evaluation (not LiteLLM) when using Bedrock model IDs
    bm = McpAtlasBenchmark(
        shuffle=False,
        eval_model_id=args.judge_model,
        eval_region=args.region,
        use_litellm=False,  # Use Bedrock for us.anthropic.* model IDs
    )

    key_registry = None
    if args.env_file:
        key_registry = KeyRegistry(env_file_path=args.env_file)
        key_registry.load()
        log.info("Loaded %d API key(s)", len(key_registry.get_loaded_key_names()))

    tasks = bm.get_tasks(split="test", limit=args.limit, key_registry=key_registry)
    log.info("Tasks after key_registry filter: %d", len(tasks))

    # ── Resume support ───────────────────────────────────────
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

    # ── Start shared container ───────────────────────────────
    all_env_vars = {}
    if args.docker_image and key_registry:
        all_env_vars = {
            name: entry.value
            for name, entry in key_registry._keys.items()
            if entry.value
        }

    container = None
    shared_client = None

    if args.docker_image:
        if not pull_image(args.docker_image):
            log.error("Failed to pull image %s", args.docker_image)
            sys.exit(1)
        container = McpAtlasContainer(
            args.docker_image,
            container_name=os.environ.get("MCP_CONTAINER_NAME", "mcp-atlas-adaptive-evolve"),
            env_vars=all_env_vars,
        )
        log.info("Starting shared MCP-Atlas container ...")
        container.start()
        shared_client = McpClientWrapper(base_url=container.base_url)
        log.info("Shared container ready.")

    remaining = [t for t in tasks if t.id not in done_ids]
    log.info("Remaining tasks: %d", len(remaining))

    # ── Evolution setup ──────────────────────────────────────
    evolution_dir = work_dir / "evolution"
    evolution_dir.mkdir(parents=True, exist_ok=True)
    observer = Observer(evolution_dir)

    config = EvolveConfig(
        evolver_model=args.evolver_model,
        evolver_max_tokens=8000,
        evolve_prompts=True,
        evolve_skills=True,
        evolve_memory=True,
        extra={"region": args.region},
    )

    # Adaptive-specific configuration
    prompt_config = AdaptivePromptConfig(
        prompt_max_chars=4000,
        skill_max_chars=2000,
        max_skills=15,
        include_claim_details=True,
        include_judge_patterns=True,
        include_task_type_stats=True,
        include_evolution_history=True,
    )

    evolver = AdaptiveEvolveEngine(
        config=config,
        prompt_config=prompt_config,
        improvement_threshold=0.03,  # 3% improvement to reset stagnation (balanced for 30-task batches)
        stagnation_window=5,  # Rollback after 5 cycles without improvement (original)
    )
    log.info("Initialized AdaptiveEvolveEngine with per-claim analysis")

    # ── Main loop ────────────────────────────────────────────
    batches = [remaining[i:i + args.batch_size]
               for i in range(0, len(remaining), args.batch_size)]

    total_passed = 0
    total_failed = 0
    total_errors = 0
    evo_cycle = observer._batch_id

    try:
        with open(summary_path, "a", newline="") as csvfile:
            writer = csv.writer(csvfile)
            if write_header:
                writer.writerow(["task_id", "result", "score", "elapsed_s",
                                 "output_len", "detail", "evo_cycle"])

            for batch_idx, batch in enumerate(batches):
                log.info("=" * 70)
                log.info("BATCH %d/%d (%d tasks) | evo_cycle=%d",
                         batch_idx + 1, len(batches), len(batch), evo_cycle)
                log.info("=" * 70)

                batch_observations = []

                # ── Solve batch with code-exec-enabled agent ─
                agent = CodeExecMcpAgent(
                    workspace_dir=work_dir,
                    model_id=args.solver_model,
                    region=args.region,
                    max_tokens=args.max_tokens,
                    docker_image=None,
                    key_registry=key_registry,
                )

                for i, task in enumerate(batch, 1):
                    log.info("[%d/%d] Solving task %s ...", i, len(batch), task.id)
                    sid = task.id.replace("/", "_")

                    try:
                        t0 = time.time()
                        old_stdout = sys.stdout
                        sys.stdout = open(os.devnull, 'w')
                        try:
                            trajectory = agent.solve(task, shared_client=shared_client)
                        finally:
                            sys.stdout.close()
                            sys.stdout = old_stdout
                        elapsed = time.time() - t0

                        (out_dir / f"output_{sid}.txt").write_text(trajectory.output)
                        (out_dir / f"conversation_{sid}.json").write_text(
                            json.dumps(trajectory.steps, indent=2,
                                       ensure_ascii=False, default=str))

                        fb = bm.evaluate(task, trajectory)
                        result = "PASS" if fb.success else "FAIL"
                        writer.writerow([task.id, result, fb.score, f"{elapsed:.1f}",
                                         len(trajectory.output), fb.detail[:300],
                                         evo_cycle])
                        csvfile.flush()

                        if fb.success:
                            total_passed += 1
                        else:
                            total_failed += 1

                        log.info("[%d/%d] %s | Score: %.2f | Time: %.1fs",
                                 i, len(batch), result, fb.score, elapsed)

                        batch_observations.append(
                            Observation(task=task, trajectory=trajectory, feedback=fb))

                    except Exception as e:
                        total_errors += 1
                        log.error("[%d/%d] ERROR on task %s: %s",
                                  i, len(batch), task.id, e)
                        log.error(traceback.format_exc())
                        writer.writerow([task.id, "ERROR", 0, "0", 0,
                                         str(e)[:300], evo_cycle])
                        csvfile.flush()

                # ── Collect observations ─────────────────────
                if batch_observations:
                    observer.collect(batch_observations)
                    agent.export_to_fs()

                # ── Evolve with adaptive engine ──────────────
                if batch_observations:
                    log.info("Evolving (cycle %d, %d observations) ...",
                             evo_cycle, len(batch_observations))

                    # Convert Observation objects to dict format for adaptive_evolve
                    obs_dicts = []
                    for obs in batch_observations:
                        obs_dict = {
                            "task_id": obs.task.id,
                            "task_input": obs.task.input,
                            "input": obs.task.input,
                            "output": obs.trajectory.output,
                            "steps": obs.trajectory.steps,
                            "success": obs.feedback.success,
                            "score": obs.feedback.score,
                            "feedback": {
                                "detail": obs.feedback.detail,
                                "raw": obs.feedback.raw,
                            },
                        }
                        obs_dicts.append(obs_dict)

                    t1 = time.time()
                    try:
                        evolve_result = evolver.evolve(
                            workspace=agent.workspace,
                            observation_logs=obs_dicts,
                            evo_number=evo_cycle,
                        )
                        log.info("Evolved in %.1fs", time.time() - t1)
                        log.info("  Pass rate: %.1f%%", evolve_result.get("pass_rate", 0) * 100)
                        log.info("  Auto-fixes: %d", evolve_result.get("auto_fixes", 0))
                        log.info("  New skills: %d", evolve_result.get("new_skills", 0))
                        log.info("  Claim types analyzed: %d", evolve_result.get("claim_types_analyzed", 0))
                        log.info("  Task types analyzed: %d", evolve_result.get("task_types_analyzed", 0))

                        if evolve_result.get("weakest_claim_types"):
                            log.info("  Weakest claim types:")
                            for claim_type, pass_rate in evolve_result["weakest_claim_types"].items():
                                log.info("    - %s: %.0f%%", claim_type, pass_rate * 100)

                        if evolve_result.get("failure_patterns"):
                            log.info("  Failure patterns: %s", ", ".join(evolve_result["failure_patterns"]))

                        agent.reload_from_fs()
                    except Exception as e:
                        log.error("Evolution failed: %s", e)
                        log.error(traceback.format_exc())

                evo_cycle += 1

                batch_passed = sum(1 for o in batch_observations if o.feedback.success)
                batch_scores = [o.feedback.score for o in batch_observations]
                batch_avg = sum(batch_scores) / len(batch_scores) if batch_scores else 0
                log.info("Batch %d: %d/%d passed (%.1f%%), avg_score=%.3f",
                         batch_idx + 1, batch_passed, len(batch),
                         batch_passed / len(batch) * 100 if batch else 0, batch_avg)

    finally:
        if shared_client:
            shared_client.close()
        if container:
            container.stop()

    total = total_passed + total_failed + total_errors
    log.info("=" * 70)
    log.info("DONE: %d tasks | %d passed | %d failed | %d errors",
             total, total_passed, total_failed, total_errors)
    if total:
        log.info("Overall pass rate: %.1f%%", total_passed / total * 100)
    log.info("Results: %s", summary_path)
    log.info("Evolution workspace: %s", work_dir)

    sentinel_payload = json.dumps({
        "total": total,
        "passed": total_passed,
        "failed": total_failed,
        "errors": total_errors,
        "summary_csv": str(summary_path),
    }, indent=2)
    sentinel_tmp = complete_path.with_suffix(complete_path.suffix + ".tmp")
    sentinel_tmp.write_text(sentinel_payload)
    os.replace(sentinel_tmp, complete_path)
    log.info("Wrote completion sentinel: %s", complete_path)


if __name__ == "__main__":
    main()
