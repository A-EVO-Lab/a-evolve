"""Helpers for exporting SkillBench solve artifacts."""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from ...types import Feedback, Trajectory


@dataclass
class SkillBenchArtifactPaths:
    output_path: Path
    conversation_path: Path
    official_like_trial_dir: Path | None = None
    official_trajectory_path: Path | None = None


def _safe_component(value: str, default: str) -> str:
    slug = re.sub(r"[^a-z0-9_.-]+", "-", value.strip().lower()).strip("-.")
    return slug or default


def _resolve_run_id(run_id: str | None) -> str:
    if run_id:
        return run_id
    env_run_id = os.environ.get("SKILLBENCH_RUN_ID")
    if env_run_id:
        return env_run_id
    return f"{time.strftime('%Y%m%d_%H%M%S')}-{uuid.uuid4().hex[:8]}"


def export_skillbench_artifacts(
    *,
    artifacts_dir: Path,
    task_id: str,
    mode: str,
    native_profile: str,
    model_id: str,
    region: str,
    max_tokens: int,
    use_skills: bool,
    split_seed: int,
    trajectory: Trajectory,
    feedback: Feedback,
    elapsed: float,
    run_id: str | None = None,
    cycle: int | None = None,
) -> SkillBenchArtifactPaths:
    """Write solve artifacts in the legacy SkillBench layout."""

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    sid = task_id.replace("/", "_")
    cycle_suffix = f"_cycle-{cycle}" if cycle is not None else ""
    output_path = artifacts_dir / f"output_sb_{sid}{cycle_suffix}_{mode}.txt"
    conversation_path = artifacts_dir / f"conversation_sb_{sid}{cycle_suffix}_{mode}.json"

    step = trajectory.steps[-1] if trajectory.steps and isinstance(trajectory.steps[-1], dict) else None
    official_like_trial_dir: Path | None = None
    official_trajectory_path: Path | None = None

    if mode == "native" and native_profile == "terminus2" and step is not None:
        episode_trace = step.get("episode_trace")
        if isinstance(episode_trace, list):
            run_id_raw = _resolve_run_id(run_id)
            task_component = _safe_component(task_id, "task")
            run_component = _safe_component(run_id_raw, "run")
            trial_name = f"{task_component}__{run_component}"
            if cycle is not None:
                trial_name = f"{trial_name}-cycle-{cycle}"

            official_like_trial_dir = artifacts_dir / "official_like" / trial_name
            official_agent_dir = official_like_trial_dir / "agent"
            official_agent_dir.mkdir(parents=True, exist_ok=True)

            official_trajectory_path = official_agent_dir / "trajectory.json"
            official_trajectory_path.write_text(
                json.dumps(episode_trace, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )

            for idx, row in enumerate(episode_trace):
                if isinstance(row, dict):
                    raw_episode = row.get("episode", idx)
                    prompt_text = str(row.get("prompt", ""))
                    response_text = str(row.get("response", ""))
                else:
                    raw_episode = idx
                    prompt_text = ""
                    response_text = str(row)
                try:
                    episode_number = int(raw_episode)
                except (TypeError, ValueError):
                    episode_number = idx
                episode_dir = official_agent_dir / f"episode-{episode_number}"
                episode_dir.mkdir(parents=True, exist_ok=True)
                (episode_dir / "prompt.txt").write_text(prompt_text, encoding="utf-8")
                (episode_dir / "response.txt").write_text(response_text, encoding="utf-8")

            official_config = {
                "task_id": task_id,
                "mode": mode,
                "native_profile": native_profile,
                "model_id": model_id,
                "region": region,
                "max_tokens": max_tokens,
                "use_skills": use_skills,
                "split_seed": split_seed,
                "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            (official_like_trial_dir / "config.json").write_text(
                json.dumps(official_config, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )

            raw = feedback.raw or {}
            official_result = {
                "task_id": task_id,
                "status": "pass" if feedback.success else "fail",
                "reward_float": raw.get("reward_float"),
                "pass_binary": raw.get("pass_binary"),
                "failure_class": raw.get("failure_class"),
                "score": feedback.score,
                "duration_sec": elapsed,
                "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            (official_like_trial_dir / "result.json").write_text(
                json.dumps(official_result, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )

            step["episode_count"] = len(episode_trace)
            step["episode_trace_path"] = str(official_trajectory_path)
            # Keep conversation artifacts compact; full episode details are in official_like/.
            step.pop("episode_trace", None)

    output_path.write_text(trajectory.output, encoding="utf-8")
    conversation_path.write_text(
        json.dumps(trajectory.steps, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    return SkillBenchArtifactPaths(
        output_path=output_path,
        conversation_path=conversation_path,
        official_like_trial_dir=official_like_trial_dir,
        official_trajectory_path=official_trajectory_path,
    )
