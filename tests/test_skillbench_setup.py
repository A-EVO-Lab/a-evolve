from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent_evolve.agents.skillbench.repo import (
    SKILLBENCH_BOOTSTRAP_PATHS,
    SKILLBENCH_HARBOR_REPO_ENV,
    SKILLBENCH_REPO_ENV,
    SKILLBENCH_TASKS_ENV,
    SKILLBENCH_TASKS_NO_SKILLS_ENV,
    SkillBenchPaths,
    SkillBenchSetupError,
    ensure_skillbench_repo,
    resolve_skillbench_paths,
    validate_skillbench_paths,
)
from agent_evolve.agents.skillbench.paths import resolve_skillbench_seed_workspaces_root
from agent_evolve.benchmarks.skill_bench import SkillBenchBenchmark


@pytest.fixture(autouse=True)
def clear_skillbench_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        SKILLBENCH_REPO_ENV,
        "SKILLBENCH_REPO_REF",
        SKILLBENCH_TASKS_ENV,
        SKILLBENCH_TASKS_NO_SKILLS_ENV,
        SKILLBENCH_HARBOR_REPO_ENV,
    ):
        monkeypatch.delenv(key, raising=False)


def _write_task(root: Path, task_name: str) -> None:
    task_dir = root / task_name
    (task_dir / "environment").mkdir(parents=True, exist_ok=True)
    (task_dir / "tests").mkdir(parents=True, exist_ok=True)
    (task_dir / "instruction.md").write_text("Solve the task.\n", encoding="utf-8")
    (task_dir / "environment" / "Dockerfile").write_text("FROM python:3.11-slim\n", encoding="utf-8")
    (task_dir / "tests" / "test.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (task_dir / "task.toml").write_text(
        "\n".join(
            [
                "[metadata]",
                f'id = "{task_name}"',
                'category = "spreadsheet"',
                'difficulty = "easy"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _make_repo_tree(root: Path) -> Path:
    (root / "tasks").mkdir(parents=True, exist_ok=True)
    (root / "tasks-no-skills").mkdir(parents=True, exist_ok=True)
    _write_task(root / "tasks", "task-with-skills")
    _write_task(root / "tasks-no-skills", "task-without-skills")
    (root / "libs" / "terminus_agent").mkdir(parents=True, exist_ok=True)
    (root / "libs" / "terminus_agent" / "README.md").write_text("terminus\n", encoding="utf-8")
    (root / "pyproject.toml").write_text('[project]\nname = "skillsbench"\n', encoding="utf-8")
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (root / ".python-version").write_text("3.12\n", encoding="utf-8")
    return root


def _init_git_repo(root: Path) -> str:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True, text=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_resolve_skillbench_paths_prefers_explicit_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    explicit_repo = _make_repo_tree(tmp_path / "explicit")
    env_repo = _make_repo_tree(tmp_path / "env-repo")
    monkeypatch.setenv(SKILLBENCH_REPO_ENV, str(env_repo))

    paths = resolve_skillbench_paths(
        tasks_with_skills_dir=explicit_repo / "tasks",
        tasks_without_skills_dir=explicit_repo / "tasks-no-skills",
        harbor_repo=explicit_repo,
    )

    assert paths.tasks_with_skills_dir == (explicit_repo / "tasks").resolve()
    assert paths.tasks_without_skills_dir == (explicit_repo / "tasks-no-skills").resolve()
    assert paths.harbor_repo == explicit_repo.resolve()
    assert paths.source == "explicit-harbor"


def test_resolve_skillbench_paths_derives_from_repo_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _make_repo_tree(tmp_path / "skillsbench")
    monkeypatch.setenv(SKILLBENCH_REPO_ENV, str(repo))

    paths = resolve_skillbench_paths()

    assert paths.repo_dir == repo.resolve()
    assert paths.tasks_with_skills_dir == (repo / "tasks").resolve()
    assert paths.tasks_without_skills_dir == (repo / "tasks-no-skills").resolve()
    assert paths.harbor_repo == repo.resolve()
    assert paths.source == "env-repo"


def test_validate_skillbench_paths_is_mode_aware(tmp_path: Path) -> None:
    tasks_with = tmp_path / "tasks"
    tasks_without = tmp_path / "tasks-no-skills"
    tasks_with.mkdir()
    tasks_without.mkdir()
    harbor_repo = tmp_path / "harbor"
    harbor_repo.mkdir()

    paths = SkillBenchPaths(
        repo_dir=tmp_path,
        tasks_with_skills_dir=tasks_with,
        tasks_without_skills_dir=tasks_without,
        harbor_repo=harbor_repo,
        repo_ref="local",
        source="test",
    )

    validate_skillbench_paths(paths, use_skills=False, execution_mode="native")
    with pytest.raises(SkillBenchSetupError):
        validate_skillbench_paths(paths, use_skills=False, execution_mode="harbor")


def test_ensure_skillbench_repo_bootstraps_snapshot(tmp_path: Path) -> None:
    source_repo = _make_repo_tree(tmp_path / "source")
    commit = _init_git_repo(source_repo)

    out = ensure_skillbench_repo(
        repo_url=str(source_repo),
        ref=commit,
        cache_root=tmp_path / "cache",
    )

    for rel in SKILLBENCH_BOOTSTRAP_PATHS:
        assert (out / rel).exists(), rel
    assert (out / ".agent-evolve-skillbench-bootstrap.json").exists()


def test_skillbench_benchmark_loads_from_repo_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _make_repo_tree(tmp_path / "skillsbench")
    monkeypatch.setenv(SKILLBENCH_REPO_ENV, str(repo))

    benchmark = SkillBenchBenchmark(use_skills=True, shuffle=False)
    tasks = benchmark.get_tasks(split="test", limit=10)

    assert tasks
    assert benchmark.tasks_dir == str((repo / "tasks").resolve())


def test_seed_workspaces_root_contains_skillbench_manifest() -> None:
    seed_root = resolve_skillbench_seed_workspaces_root()
    assert (seed_root / "skillbench" / "manifest.yaml").exists()
