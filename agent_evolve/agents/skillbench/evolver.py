"""SkillBench-only evolver facade.

This keeps SkillBench orchestration out of the shared Agent Evolve API.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from ...algorithms.skillforge import AEvolveEngine
from ...config import EvolveConfig
from ...engine.base import EvolutionEngine
from ...types import EvolutionResult
from ...benchmarks.skill_bench import SkillBenchBenchmark
from .agent import SkillBenchAgent
from .loop import SkillBenchEvolutionLoop
from .paths import (
    resolve_skillbench_relative_path,
    resolve_skillbench_seed_workspaces_root,
)

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_SEED_WORKSPACE = resolve_skillbench_seed_workspaces_root() / "skillbench"
_DEFAULT_WORK_DIR = Path("./evolution_workdir/skillbench")


class SkillBenchEvolver:
    """Programmatic SkillBench evolution entrypoint."""

    def __init__(
        self,
        config: EvolveConfig | str | Path | None = None,
        engine: EvolutionEngine | None = None,
        benchmark: SkillBenchBenchmark | None = None,
        agent: SkillBenchAgent | None = None,
        seed_workspace: str | Path | None = None,
        work_dir: str | Path = _DEFAULT_WORK_DIR,
        model_id: str = "us.anthropic.claude-sonnet-4-20250514-v1:0",
        region: str = "us-west-2",
        max_tokens: int = 16384,
        tasks_dir: str | Path | None = None,
        tasks_with_skills_dir: str | Path | None = None,
        tasks_without_skills_dir: str | Path | None = None,
        task_filter: str | None = None,
        category_filter: str | None = None,
        difficulty_filter: str | None = None,
        shuffle: bool = True,
        use_skills: bool = True,
        split_seed: int = 42,
        execution_mode: str = "native",
        harbor_repo: str | Path | None = None,
        harbor_config_template: str | Path | None = None,
        harbor_agent_import_path: str = (
            "libs.terminus_agent.agents.terminus_2.harbor_terminus_2_skills:"
            "HarborTerminus2WithSkills"
        ),
        harbor_model_name: str = "vertex_ai/claude-sonnet-4-5@20250929",
        harbor_jobs_dir: str | Path | None = None,
        harbor_timeout_sec: int = 1800,
        harbor_uv_cmd: str = "uv run harbor run",
        native_profile: str = "terminus2",
        score_mode: str = "dual",
        retry_max: int = 6,
        retry_min_wait_sec: float = 1.0,
        retry_max_wait_sec: float = 120.0,
    ):
        self.config = self._resolve_config(config)

        if benchmark is None:
            benchmark = SkillBenchBenchmark(
                tasks_dir=self._stringify(tasks_dir),
                tasks_with_skills_dir=self._stringify(tasks_with_skills_dir),
                tasks_without_skills_dir=self._stringify(tasks_without_skills_dir),
                task_filter=task_filter,
                category_filter=category_filter,
                difficulty_filter=difficulty_filter,
                shuffle=shuffle,
                holdout_ratio=self.config.holdout_ratio,
                use_skills=use_skills,
                split_seed=split_seed,
                execution_mode=execution_mode,
                harbor_repo=self._stringify(harbor_repo),
                harbor_config_template=self._stringify(harbor_config_template),
                harbor_agent_import_path=harbor_agent_import_path,
                harbor_model_name=harbor_model_name,
                harbor_jobs_dir=self._stringify(harbor_jobs_dir),
                harbor_timeout_sec=harbor_timeout_sec,
                harbor_uv_cmd=harbor_uv_cmd,
                native_profile=native_profile,
                score_mode=score_mode,
                retry_max=retry_max,
                retry_min_wait_sec=retry_min_wait_sec,
                retry_max_wait_sec=retry_max_wait_sec,
            )
        self.benchmark = benchmark

        if agent is None:
            workspace_root = self._prepare_workspace(seed_workspace, work_dir)
            agent = SkillBenchAgent(
                workspace_dir=workspace_root,
                model_id=model_id,
                region=region,
                max_tokens=max_tokens,
                tasks_dir=self.benchmark.tasks_dir,
                execution_mode=self.benchmark.execution_mode,
                harbor_repo=self.benchmark.harbor_repo,
                harbor_config_template=self.benchmark.harbor_config_template,
                harbor_agent_import_path=self.benchmark.harbor_agent_import_path,
                harbor_model_name=self.benchmark.harbor_model_name,
                harbor_jobs_dir=self.benchmark.harbor_jobs_dir,
                harbor_timeout_sec=self.benchmark.harbor_timeout_sec,
                harbor_uv_cmd=self.benchmark.harbor_uv_cmd,
                native_profile=self.benchmark.native_profile,
                score_mode=self.benchmark.score_mode,
                retry_max=self.benchmark.retry_max,
                retry_min_wait_sec=self.benchmark.retry_min_wait_sec,
                retry_max_wait_sec=self.benchmark.retry_max_wait_sec,
            )
        self.agent = agent

        self.engine = engine or AEvolveEngine(self.config)
        self._loop = SkillBenchEvolutionLoop(
            self.agent,
            self.benchmark,
            self.engine,
            self.config,
        )

    def run(self, cycles: int | None = None) -> EvolutionResult:
        """Run SkillBench evolution."""
        return self._loop.run(cycles=cycles)

    @staticmethod
    def _resolve_config(config: EvolveConfig | str | Path | None) -> EvolveConfig:
        if config is None:
            return EvolveConfig()
        if isinstance(config, EvolveConfig):
            return config
        return EvolveConfig.from_yaml(config)

    @staticmethod
    def _stringify(path_value: str | Path | None) -> str | None:
        if path_value is None:
            return None
        return str(SkillBenchEvolver._resolve_path(path_value))

    @staticmethod
    def _resolve_path(path_value: str | Path) -> Path:
        return (
            resolve_skillbench_relative_path(path_value, repo_root=_REPO_ROOT)
            or Path(path_value).expanduser().resolve()
        )

    def _prepare_workspace(self, seed_workspace: str | Path | None, work_dir: str | Path) -> Path:
        seed_dir = self._resolve_path(seed_workspace or _DEFAULT_SEED_WORKSPACE)
        if not seed_dir.exists():
            raise ValueError(f"Seed workspace not found: {seed_dir}")

        if not (seed_dir / "manifest.yaml").exists():
            raise ValueError(f"Invalid SkillBench seed workspace: {seed_dir}")

        workspace_root = self._resolve_path(work_dir)
        if workspace_root.exists():
            return workspace_root

        workspace_root.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(seed_dir, workspace_root)
        logger.info("Copied SkillBench seed workspace %s -> %s", seed_dir, workspace_root)
        return workspace_root
