"""SkillBench public repo resolution and bootstrap helpers."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .paths import skillbench_default_cache_root, resolve_skillbench_relative_path

logger = logging.getLogger(__name__)

SKILLBENCH_PUBLIC_REPO = "https://github.com/benchflow-ai/skillsbench.git"
SKILLBENCH_PINNED_REF = "828bb921fb94dc065bfefd6bac4e8938be3f71e0"
SKILLBENCH_REPO_ENV = "SKILLBENCH_REPO_DIR"
SKILLBENCH_REF_ENV = "SKILLBENCH_REPO_REF"
SKILLBENCH_TASKS_ENV = "SKILLBENCH_TASKS_DIR"
SKILLBENCH_TASKS_NO_SKILLS_ENV = "SKILLBENCH_TASKS_NO_SKILLS_DIR"
SKILLBENCH_HARBOR_REPO_ENV = "SKILLBENCH_HARBOR_REPO"
SKILLBENCH_BOOTSTRAP_PATHS = (
    "tasks",
    "tasks-no-skills",
    "libs",
    "pyproject.toml",
    "uv.lock",
    ".python-version",
)


class SkillBenchSetupError(RuntimeError):
    """Raised when SkillBench data or repo setup is missing or invalid."""


@dataclass(frozen=True)
class SkillBenchPaths:
    """Resolved SkillBench filesystem paths."""

    repo_dir: Path
    tasks_with_skills_dir: Path
    tasks_without_skills_dir: Path
    harbor_repo: Path
    repo_ref: str
    source: str
    auto_bootstrapped: bool = False

    def selected_tasks_dir(self, *, use_skills: bool) -> Path:
        return self.tasks_with_skills_dir if use_skills else self.tasks_without_skills_dir


def resolve_skillbench_paths(
    *,
    tasks_dir: str | Path | None = None,
    tasks_with_skills_dir: str | Path | None = None,
    tasks_without_skills_dir: str | Path | None = None,
    harbor_repo: str | Path | None = None,
    ensure_repo: bool = False,
) -> SkillBenchPaths:
    """Resolve SkillBench paths from explicit args, env vars, or auto-bootstrap."""
    explicit_with = _resolve_optional_path(tasks_with_skills_dir or tasks_dir)
    explicit_without = _resolve_optional_path(tasks_without_skills_dir)
    explicit_harbor = _resolve_optional_path(harbor_repo)

    env_with = _resolve_optional_path(os.environ.get(SKILLBENCH_TASKS_ENV))
    env_without = _resolve_optional_path(os.environ.get(SKILLBENCH_TASKS_NO_SKILLS_ENV))
    env_harbor = _resolve_optional_path(os.environ.get(SKILLBENCH_HARBOR_REPO_ENV))
    env_repo = _resolve_optional_path(os.environ.get(SKILLBENCH_REPO_ENV))

    source = "explicit"
    base_repo = (
        explicit_harbor
        or env_harbor
        or env_repo
        or _infer_repo_root(explicit_with)
        or _infer_repo_root(explicit_without)
    )

    if base_repo is None:
        source = "bootstrap"
        base_repo = ensure_skillbench_repo(ref=os.environ.get(SKILLBENCH_REF_ENV))
        auto_bootstrapped = True
    else:
        auto_bootstrapped = False
        if env_repo and base_repo == env_repo and explicit_harbor is None and env_harbor is None:
            source = "env-repo"
        elif env_harbor and base_repo == env_harbor and explicit_harbor is None:
            source = "env-harbor"
        elif explicit_harbor:
            source = "explicit-harbor"
        elif explicit_with or explicit_without:
            source = "explicit-tasks"

    tasks_with = explicit_with or env_with or _candidate_task_dir(base_repo, "tasks")
    tasks_without = explicit_without or env_without or _candidate_task_dir(base_repo, "tasks-no-skills")
    effective_harbor = explicit_harbor or env_harbor or base_repo
    repo_ref = os.environ.get(SKILLBENCH_REF_ENV, SKILLBENCH_PINNED_REF)

    paths = SkillBenchPaths(
        repo_dir=base_repo.resolve(),
        tasks_with_skills_dir=tasks_with.resolve(),
        tasks_without_skills_dir=tasks_without.resolve(),
        harbor_repo=effective_harbor.resolve(),
        repo_ref=repo_ref,
        source=source,
        auto_bootstrapped=auto_bootstrapped,
    )

    if ensure_repo:
        validate_skillbench_paths(paths, use_skills=True, execution_mode="harbor", require_both_task_dirs=True)
    return paths


def validate_skillbench_paths(
    paths: SkillBenchPaths,
    *,
    use_skills: bool,
    execution_mode: str,
    require_both_task_dirs: bool = False,
) -> None:
    """Validate the resolved paths for the current execution mode."""
    selected = paths.selected_tasks_dir(use_skills=use_skills)
    if not selected.is_dir():
        label = "tasks/" if use_skills else "tasks-no-skills/"
        raise SkillBenchSetupError(
            f"SkillBench {label} directory not found at {selected}. "
            f"Set {SKILLBENCH_TASKS_ENV if use_skills else SKILLBENCH_TASKS_NO_SKILLS_ENV}, "
            f"set {SKILLBENCH_REPO_ENV}, or allow auto-bootstrap."
        )

    if require_both_task_dirs:
        if not paths.tasks_with_skills_dir.is_dir():
            raise SkillBenchSetupError(
                f"SkillBench tasks/ directory not found at {paths.tasks_with_skills_dir}. "
                f"Set {SKILLBENCH_TASKS_ENV} or use a complete SkillBench repo."
            )
        if not paths.tasks_without_skills_dir.is_dir():
            raise SkillBenchSetupError(
                f"SkillBench tasks-no-skills/ directory not found at {paths.tasks_without_skills_dir}. "
                f"Set {SKILLBENCH_TASKS_NO_SKILLS_ENV} or use a complete SkillBench repo."
            )

    if execution_mode in {"harbor", "dual"}:
        missing = [name for name in ("libs", "pyproject.toml") if not (paths.harbor_repo / name).exists()]
        if missing:
            joined = ", ".join(missing)
            raise SkillBenchSetupError(
                f"SkillBench harbor repo is incomplete at {paths.harbor_repo}; missing {joined}. "
                f"Set {SKILLBENCH_HARBOR_REPO_ENV} or {SKILLBENCH_REPO_ENV} to a full public SkillsBench repo."
            )


def ensure_skillbench_repo(
    *,
    repo_url: str = SKILLBENCH_PUBLIC_REPO,
    ref: str | None = None,
    cache_root: str | Path | None = None,
) -> Path:
    """Ensure a pinned public SkillsBench repo snapshot exists in cache."""
    resolved_ref = (ref or SKILLBENCH_PINNED_REF).strip()
    root = (
        Path(cache_root).expanduser().resolve()
        if cache_root
        else skillbench_default_cache_root() / "skillbench"
    )
    repo_dir = root / resolved_ref / "repo"

    if _is_complete_skillbench_repo(repo_dir):
        return repo_dir

    root.mkdir(parents=True, exist_ok=True)
    if shutil.which("git") is None:
        raise SkillBenchSetupError(
            "SkillBench auto-bootstrap requires 'git' in PATH. "
            f"Install git or set {SKILLBENCH_REPO_ENV}/{SKILLBENCH_TASKS_ENV} manually."
        )

    target_root = repo_dir.parent
    target_root.mkdir(parents=True, exist_ok=True)
    if repo_dir.exists():
        shutil.rmtree(repo_dir)

    staging_dir = Path(tempfile.mkdtemp(prefix="skillbench-bootstrap-", dir=str(target_root)))
    try:
        logger.info("Bootstrapping SkillBench repo from %s (ref %s) — this may take a moment on first run…", repo_url, resolved_ref)
        _run_git(["clone", "--filter=blob:none", "--sparse", repo_url, str(staging_dir)])
        _run_git(
            ["-C", str(staging_dir), "sparse-checkout", "set", "--skip-checks", *SKILLBENCH_BOOTSTRAP_PATHS]
        )
        _run_git(["-C", str(staging_dir), "checkout", resolved_ref])

        if not _is_complete_skillbench_repo(staging_dir):
            missing = ", ".join(_missing_repo_paths(staging_dir))
            raise SkillBenchSetupError(
                f"Downloaded SkillBench repo at {staging_dir} is incomplete; missing {missing}."
            )

        _write_bootstrap_metadata(staging_dir, repo_url=repo_url, ref=resolved_ref)
        shutil.move(str(staging_dir), str(repo_dir))
        logger.info("Bootstrapped SkillBench repo to %s @ %s", repo_dir, resolved_ref)
        return repo_dir
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        detail = stderr or stdout or str(exc)
        raise SkillBenchSetupError(
            f"Failed to bootstrap SkillBench from {repo_url} @ {resolved_ref}: {detail}"
        ) from exc
    finally:
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)


def _resolve_optional_path(value: str | Path | None) -> Path | None:
    return resolve_skillbench_relative_path(value)


def _infer_repo_root(path: Path | None) -> Path | None:
    if path is None:
        return None
    if path.name in {"tasks", "tasks-no-skills"}:
        parent = path.parent
        if parent.exists():
            return parent
    if (path / "tasks").exists() or (path / "tasks-no-skills").exists():
        return path
    return None


def _candidate_task_dir(repo_dir: Path, dirname: str) -> Path:
    return (repo_dir / dirname).resolve()


def _missing_repo_paths(repo_dir: Path) -> list[str]:
    return [rel for rel in SKILLBENCH_BOOTSTRAP_PATHS if not (repo_dir / rel).exists()]


def _is_complete_skillbench_repo(repo_dir: Path) -> bool:
    return repo_dir.is_dir() and not _missing_repo_paths(repo_dir)


def _run_git(args: list[str]) -> None:
    subprocess.run(["git", *args], check=True, capture_output=True, text=True)


def _write_bootstrap_metadata(repo_dir: Path, *, repo_url: str, ref: str) -> None:
    meta = {
        "repo_url": repo_url,
        "ref": ref,
        "paths": list(SKILLBENCH_BOOTSTRAP_PATHS),
    }
    (repo_dir / ".agent-evolve-skillbench-bootstrap.json").write_text(
        json.dumps(meta, indent=2) + "\n",
        encoding="utf-8",
    )
