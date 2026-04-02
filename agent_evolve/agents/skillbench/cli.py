"""Dedicated CLI for SkillBench evolution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ...config import EvolveConfig
from .evolver import SkillBenchEvolver
from .paths import resolve_skillbench_seed_workspaces_root


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(
        f"Invalid boolean value: {value!r}. Use true/false."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent_evolve.agents.skillbench")
    parser.add_argument("--config", default=None, help="Path to YAML evolve config.")
    parser.add_argument(
        "--seed-workspace",
        default=str(resolve_skillbench_seed_workspaces_root() / "skillbench"),
        help="Seed workspace path (default: bundled skillbench workspace).",
    )
    parser.add_argument(
        "--work-dir",
        default="./evolution_workdir/skillbench",
        help="Evolution workspace path.",
    )
    parser.add_argument("--cycles", type=int, default=None, help="Number of evolution cycles.")
    parser.add_argument("--batch-size", type=int, default=None, help="Override config batch size.")
    parser.add_argument(
        "--holdout-ratio",
        type=float,
        default=None,
        help="Override holdout ratio.",
    )
    parser.add_argument("--mode", choices=["native", "harbor", "dual"], default="native")
    parser.add_argument(
        "--native-profile",
        choices=["strands", "terminus2", "terminus2_legacy"],
        default="terminus2",
    )
    parser.add_argument(
        "--score-mode",
        choices=["reward", "binary", "dual"],
        default="dual",
    )
    parser.add_argument("--retry-max", type=int, default=6)
    parser.add_argument("--retry-min-wait-sec", type=float, default=1.0)
    parser.add_argument("--retry-max-wait-sec", type=float, default=120.0)
    parser.add_argument(
        "--use-skills",
        type=_parse_bool,
        default=True,
        help="Use tasks with embedded skills (true/false).",
    )
    parser.add_argument("--tasks-dir", default=None)
    parser.add_argument("--tasks-dir-with-skills", default=None)
    parser.add_argument("--tasks-dir-without-skills", default=None)
    parser.add_argument("--split-seed", type=int, default=42)
    parser.add_argument("--task-filter", default=None)
    parser.add_argument("--category-filter", default=None)
    parser.add_argument("--difficulty-filter", default=None)
    parser.add_argument("--shuffle", type=_parse_bool, default=True)
    parser.add_argument("--model-id", default="us.anthropic.claude-sonnet-4-20250514-v1:0")
    parser.add_argument("--region", default="us-west-2")
    parser.add_argument("--max-tokens", type=int, default=16384)
    parser.add_argument("--harbor-repo", default=None)
    parser.add_argument("--harbor-config-template", default=None)
    parser.add_argument(
        "--harbor-agent-import-path",
        default=(
            "libs.terminus_agent.agents.terminus_2.harbor_terminus_2_skills:"
            "HarborTerminus2WithSkills"
        ),
    )
    parser.add_argument("--harbor-model-name", default="vertex_ai/claude-sonnet-4-5@20250929")
    parser.add_argument("--harbor-jobs-dir", default=None)
    parser.add_argument("--harbor-timeout-sec", type=int, default=1800)
    parser.add_argument("--harbor-uv-cmd", default="uv run harbor run")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    config = EvolveConfig.from_yaml(args.config) if args.config else EvolveConfig()
    if args.batch_size is not None:
        config.batch_size = int(args.batch_size)
    if args.holdout_ratio is not None:
        config.holdout_ratio = float(args.holdout_ratio)

    evolver = SkillBenchEvolver(
        config=config,
        seed_workspace=Path(args.seed_workspace),
        work_dir=Path(args.work_dir),
        model_id=args.model_id,
        region=args.region,
        max_tokens=args.max_tokens,
        tasks_dir=args.tasks_dir,
        tasks_with_skills_dir=args.tasks_dir_with_skills,
        tasks_without_skills_dir=args.tasks_dir_without_skills,
        task_filter=args.task_filter,
        category_filter=args.category_filter,
        difficulty_filter=args.difficulty_filter,
        shuffle=args.shuffle,
        use_skills=args.use_skills,
        split_seed=args.split_seed,
        execution_mode=args.mode,
        harbor_repo=args.harbor_repo,
        harbor_config_template=args.harbor_config_template,
        harbor_agent_import_path=args.harbor_agent_import_path,
        harbor_model_name=args.harbor_model_name,
        harbor_jobs_dir=args.harbor_jobs_dir,
        harbor_timeout_sec=args.harbor_timeout_sec,
        harbor_uv_cmd=args.harbor_uv_cmd,
        native_profile=args.native_profile,
        score_mode=args.score_mode,
        retry_max=args.retry_max,
        retry_min_wait_sec=args.retry_min_wait_sec,
        retry_max_wait_sec=args.retry_max_wait_sec,
    )
    result = evolver.run(cycles=args.cycles)
    print(
        json.dumps(
            {
                "cycles_completed": result.cycles_completed,
                "final_score": result.final_score,
                "converged": result.converged,
                "score_history": result.score_history,
                "details": result.details,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
