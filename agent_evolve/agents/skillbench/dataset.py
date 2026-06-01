"""SkillBench dataset loader.

Scans a SkillBench ``tasks/`` directory on disk, parses each task's
``task.toml`` metadata and ``instruction.md`` prompt, and returns
lightweight dataclass objects the benchmark adapter can convert to
:class:`~agent_evolve.types.Task`.
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .repo import resolve_skillbench_paths, validate_skillbench_paths

logger = logging.getLogger(__name__)


@dataclass
class SBTask:
    """A single SkillBench task loaded from disk."""

    name: str
    prompt: str
    dockerfile_dir: str
    test_sh_path: str
    test_py_path: str | None
    metadata: dict[str, Any] = field(default_factory=dict)


def load_all_tasks(tasks_dir: str | None = None) -> list[SBTask]:
    """Scan *tasks_dir* and return one :class:`SBTask` per valid task folder.

    A valid task folder must contain ``instruction.md`` and
    ``environment/Dockerfile``.
    """
    if tasks_dir is None:
        resolved = resolve_skillbench_paths()
        validate_skillbench_paths(resolved, use_skills=True, execution_mode="native")
        root = resolved.tasks_with_skills_dir
    else:
        root = Path(tasks_dir).expanduser().resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"SkillBench tasks directory does not exist: {root}")

    tasks: list[SBTask] = []
    for task_dir in sorted(root.iterdir()):
        if not task_dir.is_dir():
            continue

        instruction_path = task_dir / "instruction.md"
        dockerfile_dir = task_dir / "environment"
        if not instruction_path.exists() or not dockerfile_dir.exists():
            continue

        prompt = instruction_path.read_text(encoding="utf-8").strip()
        meta = _parse_task_toml(task_dir / "task.toml")

        test_sh = task_dir / "tests" / "test.sh"
        test_py = task_dir / "tests" / "test_outputs.py"

        tasks.append(SBTask(
            name=meta.get("id") or task_dir.name,
            prompt=prompt,
            dockerfile_dir=str(dockerfile_dir),
            test_sh_path=str(test_sh) if test_sh.exists() else "",
            test_py_path=str(test_py) if test_py.exists() else None,
            metadata={
                "task_dir": str(task_dir),
                "category": meta.get("category", "unknown"),
                "difficulty": meta.get("difficulty", "unknown"),
                "tags": meta.get("tags", []),
                "agent_timeout_sec": meta.get("agent_timeout_sec", 900),
                "verifier_timeout_sec": meta.get("verifier_timeout_sec", 900),
                "build_timeout_sec": meta.get("build_timeout_sec", 600),
                "cpus": meta.get("cpus", 1),
                "memory": meta.get("memory", "4G"),
            },
        ))

    logger.info("Loaded %d tasks from %s", len(tasks), root)
    return tasks


def get_task(tasks_dir: str | None, task_id: str) -> SBTask | None:
    """Load a single task by ID (folder name)."""
    all_tasks = load_all_tasks(tasks_dir)
    return next((t for t in all_tasks if t.name == task_id), None)


def _parse_task_toml(path: Path) -> dict[str, Any]:
    """Parse a ``task.toml`` file and flatten useful fields."""
    if not path.exists():
        return {}

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except Exception as e:
        logger.warning("Failed to parse %s: %s", path, e)
        return {}

    meta_section = data.get("metadata", {})
    verifier = data.get("verifier", {})
    agent = data.get("agent", {})
    env = data.get("environment", {})

    return {
        "id": meta_section.get("id", ""),
        "difficulty": meta_section.get("difficulty", "unknown"),
        "category": meta_section.get("category", "unknown"),
        "tags": meta_section.get("tags", []),
        "agent_timeout_sec": agent.get("timeout_sec", 900),
        "verifier_timeout_sec": verifier.get("timeout_sec", 900),
        "build_timeout_sec": env.get("build_timeout_sec", 600),
        "cpus": env.get("cpus", 1),
        "memory": env.get("memory", env.get("memory_mb", "4096")),
    }
