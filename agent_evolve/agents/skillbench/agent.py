"""SkillBench agent -- uses strands-agents with Bedrock Claude Sonnet.

Builds a Docker container for each SkillBench task, provides bash/python/
file/skill tools via ``docker exec``, and drives the solving loop through
a strands Agent.  After the agent finishes, verification is run
(test.sh -> reward.txt) and the result is embedded in the trajectory.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

try:
    from strands import Agent
    from strands.models import BedrockModel
    _STRANDS_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - optional runtime dependency
    Agent = Any  # type: ignore[assignment]
    BedrockModel = None  # type: ignore[assignment]
    _STRANDS_IMPORT_ERROR = exc

from ...protocol.base_agent import BaseAgent
from ...types import Task, Trajectory
from .repo import resolve_skillbench_paths
from .backends import (
    HarborSkillBenchBackend,
    NativeSkillBenchBackend,
    SkillBenchExecutionBackend,
)

logger = logging.getLogger(__name__)

os.environ.setdefault("BYPASS_TOOL_CONSENT", "true")
_REPO_ROOT = Path(__file__).resolve().parents[3]


class SkillBenchAgent(BaseAgent):
    """Reference agent for SkillBench tasks.

    Reads system prompt, skills, and memories from the workspace via
    BaseAgent, then for each task:
      1. Builds a Docker image from the task's Dockerfile
      2. Starts the container
      3. Creates strands tool wrappers bound to the container
      4. Runs the strands Agent to solve the task
      5. Runs verification (test.sh)
      6. Returns a Trajectory with the result
    """

    def __init__(
        self,
        workspace_dir: str | Path,
        model_id: str = "us.anthropic.claude-sonnet-4-20250514-v1:0",
        region: str = "us-west-2",
        max_tokens: int = 16384,
        tasks_dir: str | None = None,
        execution_mode: str = "native",
        harbor_repo: str | None = None,
        harbor_config_template: str | None = None,
        harbor_agent_import_path: str = (
            "libs.terminus_agent.agents.terminus_2.harbor_terminus_2_skills:HarborTerminus2WithSkills"
        ),
        harbor_model_name: str = "vertex_ai/claude-sonnet-4-5@20250929",
        harbor_jobs_dir: str | None = None,
        harbor_timeout_sec: int = 1800,
        harbor_uv_cmd: str = "uv run harbor run",
        native_profile: str = "terminus2",
        score_mode: str = "dual",
        retry_max: int = 6,
        retry_min_wait_sec: float = 1.0,
        retry_max_wait_sec: float = 120.0,
        write_episodic_memory: bool = False,
        skill_select_limit: int = 0,
    ):
        super().__init__(workspace_dir)
        self.skill_select_limit = skill_select_limit
        self.model_id = model_id
        self.region = region
        self.max_tokens = max_tokens
        self.tasks_dir = tasks_dir
        self.execution_mode = execution_mode
        self.harbor_repo = harbor_repo or os.environ.get("SKILLBENCH_HARBOR_REPO")
        self.harbor_config_template = harbor_config_template
        self.harbor_agent_import_path = harbor_agent_import_path
        self.harbor_model_name = harbor_model_name
        self.harbor_jobs_dir = (
            harbor_jobs_dir
            or os.environ.get("SKILLBENCH_HARBOR_JOBS_DIR")
            or str(_REPO_ROOT / "aevolve-skillbench-harbor-jobs")
        )
        self.harbor_timeout_sec = harbor_timeout_sec
        self.harbor_uv_cmd = harbor_uv_cmd
        self.native_profile = native_profile
        self.score_mode = score_mode
        self.retry_max = int(retry_max)
        self.retry_min_wait_sec = float(retry_min_wait_sec)
        self.retry_max_wait_sec = float(retry_max_wait_sec)
        self.write_episodic_memory = write_episodic_memory

    # ── Prompt assembly ──────────────────────────────────────────────

    def _build_system_prompt(self) -> str:
        """Assemble the full system prompt from workspace files."""
        parts = [self.system_prompt]

        if self.skills:
            parts.append("\n\n## Available Skills\n")
            parts.append(
                "You have specialized skills. Review them when facing relevant challenges.\n"
            )
            for skill in self.skills:
                parts.append(f"- **{skill.name}**: {skill.description}")
                content = self.get_skill_content(skill.name)
                if content:
                    body = content.split("---", 2)[-1].strip() if "---" in content else content
                    parts.append(f"\n{body}\n")

        if self.memories:
            parts.append("\n\n## Relevant Memories\n")
            for m in self.memories[-10:]:
                parts.append(f"- {m.get('content', '')}")

        return "\n".join(parts)

    def _build_terminus_system_prompt(self) -> str:
        """System prompt for terminus2 profiles: workspace prompt + memories.

        Skills are omitted here because the terminus2 template injects them
        via {skills_block} directly from the container's skill directories.
        """
        parts = [self.system_prompt]

        if self.memories:
            # Include insights from evolved memories — these help the solver
            # learn from past failures without needing full skill content.
            memory_lines: list[str] = []
            for m in self.memories[-15:]:
                insight = m.get("insight") or m.get("content", "")
                if insight:
                    memory_lines.append(f"- {insight}")
            if memory_lines:
                parts.append("\n\n## Lessons from Prior Tasks\n")
                parts.extend(memory_lines)

        return "\n".join(parts)

    def _build_strands_agent(self, tools: list) -> Agent:
        """Create a strands Agent with BedrockModel and container tools."""
        if BedrockModel is None:
            raise ModuleNotFoundError(
                "SkillBenchAgent requires optional dependency 'strands-agents' for native execution."
            ) from _STRANDS_IMPORT_ERROR
        model = BedrockModel(
            model_id=self.model_id,
            region_name=self.region,
            max_tokens=self.max_tokens,
        )
        return Agent(
            model=model,
            system_prompt=self._build_system_prompt(),
            tools=tools,
        )

    # ── Runtime configuration ───────────────────────────────────────

    def configure_from_benchmark(self, runtime_config: dict[str, Any]) -> None:
        """Allow SkillBench-local orchestration code to configure this agent."""
        if not runtime_config:
            return
        self.execution_mode = runtime_config.get("execution_mode", self.execution_mode)
        self.harbor_repo = runtime_config.get("harbor_repo") or self.harbor_repo
        self.harbor_config_template = (
            runtime_config.get("harbor_config_template")
            or self.harbor_config_template
        )
        self.harbor_agent_import_path = (
            runtime_config.get("harbor_agent_import_path")
            or self.harbor_agent_import_path
        )
        self.harbor_model_name = (
            runtime_config.get("harbor_model_name")
            or self.harbor_model_name
        )
        self.harbor_jobs_dir = (
            runtime_config.get("harbor_jobs_dir")
            or self.harbor_jobs_dir
        )
        self.harbor_timeout_sec = int(
            runtime_config.get("harbor_timeout_sec", self.harbor_timeout_sec)
        )
        self.harbor_uv_cmd = runtime_config.get("harbor_uv_cmd", self.harbor_uv_cmd)
        self.native_profile = runtime_config.get("native_profile", self.native_profile)
        self.score_mode = runtime_config.get("score_mode", self.score_mode)
        self.retry_max = int(runtime_config.get("retry_max", self.retry_max))
        self.retry_min_wait_sec = float(
            runtime_config.get("retry_min_wait_sec", self.retry_min_wait_sec)
        )
        self.retry_max_wait_sec = float(
            runtime_config.get("retry_max_wait_sec", self.retry_max_wait_sec)
        )

    def _get_backend(self, backend: str) -> SkillBenchExecutionBackend:
        if backend == "native":
            return NativeSkillBenchBackend(
                build_agent=self._build_strands_agent,
                remember=self.remember,
                model_id=self.model_id,
                region=self.region,
                max_tokens=self.max_tokens,
                base_system_prompt=self._build_terminus_system_prompt(),
                native_profile=self.native_profile,
                retry_max=self.retry_max,
                retry_min_wait_sec=self.retry_min_wait_sec,
                retry_max_wait_sec=self.retry_max_wait_sec,
                workspace_skills_dir=str(self.workspace.root / "skills"),
                task_skills_dir=(
                    str(self.workspace.root / "task_skills")
                    if getattr(self, "task_skills_enabled", True)
                    else None
                ),
                write_episodic_memory=self.write_episodic_memory,
                skill_select_limit=self.skill_select_limit,
            )
        if backend == "harbor":
            resolved_harbor_repo = self.harbor_repo
            if not resolved_harbor_repo:
                resolved_harbor_repo = str(resolve_skillbench_paths().harbor_repo)
            return HarborSkillBenchBackend(
                harbor_repo=resolved_harbor_repo,
                harbor_config_template=self.harbor_config_template,
                harbor_agent_import_path=self.harbor_agent_import_path,
                harbor_model_name=self.harbor_model_name,
                harbor_jobs_dir=self.harbor_jobs_dir,
                harbor_timeout_sec=self.harbor_timeout_sec,
                harbor_uv_cmd=self.harbor_uv_cmd,
                region=self.region,
            )
        raise ValueError(f"Unsupported backend: {backend}")

    # ── Solve ────────────────────────────────────────────────────────

    def solve_with_backend(self, task: Task, backend: str) -> Trajectory:
        """Solve a SkillBench task using a specific backend.

        Supported backend names:
          - native: strands + task Docker environment
          - harbor: Harbor-compatible CLI runner
        """
        runtime_backend = backend.strip().lower()
        execution_backend = self._get_backend(runtime_backend)
        return execution_backend.solve(task)

    def solve(self, task: Task) -> Trajectory:
        """Solve a SkillBench task using current execution mode."""
        runtime_backend = self.execution_mode
        if runtime_backend == "dual":
            # In dual mode, main training/gating path remains native.
            runtime_backend = "native"
        if runtime_backend not in ("native", "harbor"):
            logger.warning(
                "Unknown execution_mode=%s, defaulting to native", runtime_backend
            )
            runtime_backend = "native"
        return self.solve_with_backend(task, runtime_backend)
