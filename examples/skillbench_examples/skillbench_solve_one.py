#!/usr/bin/env python3
"""Solve one SkillBench task, log conversation as JSON."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agent_evolve.agents.skillbench import SkillBenchAgent
from agent_evolve.agents.skillbench.artifacts import export_skillbench_artifacts
from agent_evolve.agents.skillbench.paths import (
    resolve_skillbench_relative_path as resolve_runtime_path,
    resolve_skillbench_seed_workspaces_root,
)
from agent_evolve.agents.skillbench.repo import (
    SkillBenchSetupError,
    resolve_skillbench_paths,
)
from agent_evolve.benchmarks.skill_bench import SkillBenchBenchmark

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"Invalid boolean value: {value!r}. Use true/false.")


def _resolve_repo_relative_path(path_value: str | None) -> Path | None:
    """Resolve a path robustly when scripts are run from different cwd values."""
    return resolve_runtime_path(path_value, repo_root=REPO_ROOT)


def main():
    p = argparse.ArgumentParser(description="Solve a single SkillBench task")
    p.add_argument("--task-id", type=str, default=None,
                    help="Task ID to solve (default: first task)")
    p.add_argument("--mode", type=str, default="native",
                    choices=["native", "harbor"],
                    help="Execution backend mode (native|harbor).")
    p.add_argument("--native-profile", type=str, default="terminus2",
                    choices=["strands", "terminus2", "terminus2_legacy"],
                    help="Native execution profile (strands|terminus2|terminus2_legacy).")
    p.add_argument("--score-mode", type=str, default="dual",
                    choices=["reward", "binary", "dual"],
                    help="Scoring mode for feedback aggregation.")
    p.add_argument("--retry-max", type=int, default=6,
                    help="Max retries for retryable native failures.")
    p.add_argument("--retry-min-wait-sec", type=float, default=1.0,
                    help="Native retry min backoff seconds.")
    p.add_argument("--retry-max-wait-sec", type=float, default=120.0,
                    help="Native retry max backoff seconds.")
    p.add_argument("--tasks-dir", type=str, default=None,
                    help="Path to SkillBench tasks/ directory")
    p.add_argument("--use-skills", type=_parse_bool, default=True,
                    help="Use tasks with skills (true/false).")
    p.add_argument("--tasks-dir-with-skills", type=str, default=None,
                    help="Path to SkillBench tasks/ directory (with skills).")
    p.add_argument("--tasks-dir-without-skills", type=str, default=None,
                    help="Path to SkillBench tasks-no-skills/ directory.")
    p.add_argument("--split-seed", type=int, default=42,
                    help="Deterministic seed for task split/order.")
    p.add_argument("--model-id", type=str,
                    default="us.anthropic.claude-opus-4-6-v1",
                    help="Available models: us.anthropic.claude-sonnet-4-20250514-v1:0, us.anthropic.claude-opus-4-6-v1")
    p.add_argument("--region", type=str, default="us-west-2")
    p.add_argument("--max-tokens", type=int, default=16384)
    p.add_argument("--category", type=str, default=None,
                    help="Filter tasks by category")
    p.add_argument("--difficulty", type=str, default=None,
                    help="Filter tasks by difficulty (easy/medium/hard)")
    p.add_argument("--seed-workspace", type=str,
                    default=str(resolve_skillbench_seed_workspaces_root() / "skillbench"),
                    help="Seed workspace directory (default: bundled skillbench workspace)")
    p.add_argument("--artifacts-dir", type=str, default=".",
                    help="Directory for output_sb_* and conversation_sb_* artifacts.")
    p.add_argument("--provider", type=str, default="bedrock",
                    choices=["bedrock", "anthropic"],
                    help="LLM provider (default: bedrock)")
    p.add_argument("--harbor-repo", type=str, default=None,
                    help="Path to Harbor-capable SkillsBench repository.")
    p.add_argument("--harbor-config-template", type=str, default=None,
                    help="Optional Harbor YAML config template.")
    p.add_argument(
        "--harbor-agent-import-path",
        type=str,
        default=(
            "libs.terminus_agent.agents.terminus_2.harbor_terminus_2_skills:"
            "HarborTerminus2WithSkills"
        ),
        help="Harbor agent import path (<module>:<Class>).",
    )
    p.add_argument("--harbor-model-name", type=str, default=None,
                    help="Harbor model name (defaults to --model-id).")
    p.add_argument("--harbor-jobs-dir", type=str,
                    default="/tmp/aevolve-skillbench-harbor-jobs",
                    help="Directory for Harbor job artifacts.")
    p.add_argument("--harbor-timeout-sec", type=int, default=1800,
                    help="Harbor run timeout in seconds.")
    p.add_argument("--harbor-uv-cmd", type=str, default="uv run harbor run",
                    help="Harbor run command prefix.")
    p.add_argument("-v", "--verbose", action="store_true",
                    help="Enable debug logging")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    for n in ("botocore", "urllib3", "httpcore", "httpx",
              "strands.models", "strands.tools", "strands.telemetry"):
        logging.getLogger(n).setLevel(logging.WARNING)
    log = logging.getLogger("skillbench_solve")
    if args.provider != "bedrock":
        log.warning(
            "--provider=%s is kept for compatibility but does not drive backend/provider routing in this script.",
            args.provider,
        )

    harbor_config_template = _resolve_repo_relative_path(args.harbor_config_template)
    if args.harbor_config_template and (
        harbor_config_template is None or not harbor_config_template.exists()
    ):
        print(f"harbor config template not found: {args.harbor_config_template}")
        sys.exit(1)

    harbor_jobs_dir = _resolve_repo_relative_path(args.harbor_jobs_dir)
    if harbor_jobs_dir is None:
        print(f"Invalid harbor jobs dir: {args.harbor_jobs_dir}")
        sys.exit(1)

    effective_harbor_model = args.harbor_model_name or args.model_id
    try:
        resolved_skillbench = resolve_skillbench_paths(
            tasks_dir=args.tasks_dir,
            tasks_with_skills_dir=args.tasks_dir_with_skills,
            tasks_without_skills_dir=args.tasks_dir_without_skills,
            harbor_repo=args.harbor_repo,
        )
    except SkillBenchSetupError as exc:
        print(str(exc))
        sys.exit(1)

    tasks_with_skills_dir = resolved_skillbench.tasks_with_skills_dir
    tasks_without_skills_dir = resolved_skillbench.tasks_without_skills_dir
    harbor_repo = resolved_skillbench.harbor_repo

    bm = SkillBenchBenchmark(
        tasks_with_skills_dir=(
            str(tasks_with_skills_dir) if tasks_with_skills_dir else None
        ),
        tasks_without_skills_dir=(
            str(tasks_without_skills_dir) if tasks_without_skills_dir else None
        ),
        use_skills=args.use_skills,
        split_seed=args.split_seed,
        execution_mode=args.mode,
        category_filter=args.category,
        difficulty_filter=args.difficulty,
        shuffle=False,
        harbor_repo=str(harbor_repo) if harbor_repo else None,
        harbor_config_template=(
            str(harbor_config_template) if harbor_config_template else None
        ),
        harbor_agent_import_path=args.harbor_agent_import_path,
        harbor_model_name=effective_harbor_model,
        harbor_jobs_dir=str(harbor_jobs_dir),
        harbor_timeout_sec=args.harbor_timeout_sec,
        harbor_uv_cmd=args.harbor_uv_cmd,
        native_profile=args.native_profile,
        score_mode=args.score_mode,
        retry_max=args.retry_max,
        retry_min_wait_sec=args.retry_min_wait_sec,
        retry_max_wait_sec=args.retry_max_wait_sec,
    )

    tasks = bm.get_tasks(split="test", limit=500)
    if not tasks:
        print("No tasks found. Check --tasks-dir and filters.")
        sys.exit(1)

    if args.task_id:
        task = next((t for t in tasks if t.id == args.task_id), None)
    else:
        task = tasks[0]

    if not task:
        print(f"Task not found: {args.task_id}")
        print(f"Available tasks ({len(tasks)}):")
        for t in tasks[:20]:
            print(f"  {t.id} [{t.metadata.get('category')}] [{t.metadata.get('difficulty')}]")
        sys.exit(1)

    log.info("Task: %s [%s / %s]", task.id,
             task.metadata.get("category"), task.metadata.get("difficulty"))
    log.info(
        "Using benchmark tasks dir: %s [mode=%s, use_skills=%s, split_seed=%s]",
        bm.tasks_dir,
        args.mode,
        args.use_skills,
        args.split_seed,
    )
    log.info(
        "SkillBench source: %s | repo=%s | ref=%s",
        resolved_skillbench.source,
        resolved_skillbench.repo_dir,
        resolved_skillbench.repo_ref,
    )
    log.info(
        "Native profile: %s | Score mode: %s | Retry: max=%d wait=[%.1f, %.1f]",
        args.native_profile,
        args.score_mode,
        args.retry_max,
        args.retry_min_wait_sec,
        args.retry_max_wait_sec,
    )
    if args.mode == "harbor":
        log.info(
            "Harbor config: repo=%s, agent=%s, model=%s, jobs_dir=%s",
            harbor_repo,
            args.harbor_agent_import_path,
            effective_harbor_model,
            harbor_jobs_dir,
        )

    workspace_dir = _resolve_repo_relative_path(args.seed_workspace)
    if workspace_dir is None:
        print("Invalid --seed-workspace: None")
        sys.exit(1)

    if not workspace_dir.exists():
        print(
            f"Seed workspace not found: {workspace_dir}\n"
            "Tip: run from repo root or pass --seed-workspace "
            f"'{resolve_skillbench_seed_workspaces_root() / 'skillbench'}'"
        )
        sys.exit(1)

    prompt_path = workspace_dir / "prompts" / "system.md"
    if not prompt_path.exists() or not prompt_path.read_text().strip():
        print(
            f"Invalid workspace prompt: {prompt_path}\n"
            "prompts/system.md must exist and be non-empty."
        )
        sys.exit(1)

    log.info("Using workspace: %s", workspace_dir)

    agent = SkillBenchAgent(
        workspace_dir=workspace_dir,
        model_id=args.model_id,
        region=args.region,
        max_tokens=args.max_tokens,
        tasks_dir=bm.tasks_dir,
        execution_mode=args.mode,
        harbor_repo=str(harbor_repo) if harbor_repo else None,
        harbor_config_template=(
            str(harbor_config_template) if harbor_config_template else None
        ),
        harbor_agent_import_path=args.harbor_agent_import_path,
        harbor_model_name=effective_harbor_model,
        harbor_jobs_dir=str(harbor_jobs_dir),
        harbor_timeout_sec=args.harbor_timeout_sec,
        harbor_uv_cmd=args.harbor_uv_cmd,
        native_profile=args.native_profile,
        score_mode=args.score_mode,
        retry_max=args.retry_max,
        retry_min_wait_sec=args.retry_min_wait_sec,
        retry_max_wait_sec=args.retry_max_wait_sec,
    )
    runtime_config = bm.get_agent_runtime_config()
    runtime_config["harbor_model_name"] = effective_harbor_model
    runtime_config["native_profile"] = args.native_profile
    runtime_config["score_mode"] = args.score_mode
    runtime_config["retry_max"] = args.retry_max
    runtime_config["retry_min_wait_sec"] = args.retry_min_wait_sec
    runtime_config["retry_max_wait_sec"] = args.retry_max_wait_sec
    agent.configure_from_benchmark(runtime_config)

    log.info("Solving...")
    t0 = time.time()
    trajectory = agent.solve(task)
    elapsed = time.time() - t0
    log.info("Done in %.1fs", elapsed)
    fb = bm.evaluate(task, trajectory)

    artifacts_dir = _resolve_repo_relative_path(args.artifacts_dir)
    if artifacts_dir is None:
        print("Invalid --artifacts-dir: None")
        sys.exit(1)
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    artifact_paths = export_skillbench_artifacts(
        artifacts_dir=artifacts_dir,
        task_id=task.id,
        mode=args.mode,
        native_profile=args.native_profile,
        model_id=args.model_id,
        region=args.region,
        max_tokens=args.max_tokens,
        use_skills=args.use_skills,
        split_seed=args.split_seed,
        trajectory=trajectory,
        feedback=fb,
        elapsed=elapsed,
        run_id=os.environ.get("SKILLBENCH_RUN_ID"),
    )
    log.info(
        "Saved %s and %s",
        artifact_paths.output_path,
        artifact_paths.conversation_path,
    )
    if artifact_paths.official_like_trial_dir is not None:
        log.info(
            "Saved official-like trajectory artifacts under %s",
            artifact_paths.official_like_trial_dir,
        )

    print(f"\n{'=' * 70}")
    print(f"RESULT: {'PASS' if fb.success else 'FAIL'} | Score: {fb.score} | "
          f"Time: {elapsed:.1f}s | Output: {len(trajectory.output)}ch")
    reward_float = fb.raw.get("reward_float")
    pass_binary = fb.raw.get("pass_binary")
    failure_class = fb.raw.get("failure_class")
    native_impl = fb.raw.get("native_impl")
    prompt_template_sha256 = fb.raw.get("prompt_template_sha256")
    print(
        "Metrics: "
        f"score_mode={args.score_mode} reward_float={reward_float} "
        f"pass_binary={pass_binary} failure_class={failure_class} "
        f"native_impl={native_impl} prompt_template_sha256={prompt_template_sha256}"
    )
    print(f"Eval: {fb.detail[:500]}")


if __name__ == "__main__":
    main()
