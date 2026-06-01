"""Dataset loader for Terminal-Bench 2.0 challenges.

Reads eval.yaml + compose.yaml from the challenges directory to build
a list of tasks with their Docker images, prompts, test files, and metadata.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

# Default challenges directory -- override via TB2_CHALLENGES_DIR env var
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CHALLENGES_DIR = os.environ.get(
    "TB2_CHALLENGES_DIR",
    str(_PROJECT_ROOT / "agent_evolve" / "benchmarks" / "tb2" / "challenges"),
)

# Pinned source for auto-download
TB2_REPO = "https://github.com/UKGovernmentBEIS/inspect_evals.git"
TB2_COMMIT = "6e30b2de72e98dd5cc342eb9ba545ae27d2f63d7"
TB2_SUBPATH = "src/inspect_evals/terminal_bench_2/challenges"

DEFAULT_TAG = "20251031"


def ensure_challenges(challenges_dir: str | Path) -> Path:
    """Ensure the challenges directory exists and is populated.

    If the directory is empty or missing, downloads challenges from the
    pinned GitHub commit using git sparse checkout.
    """
    challenges_dir = Path(challenges_dir)
    if challenges_dir.exists() and any(challenges_dir.iterdir()):
        return challenges_dir

    logger.info("Challenges directory empty or missing: %s", challenges_dir)
    logger.info("Downloading from %s @ %s ...", TB2_REPO, TB2_COMMIT[:12])

    with tempfile.TemporaryDirectory() as tmp_dir:
        repo_dir = Path(tmp_dir) / "repo"
        subprocess.run(
            ["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse",
             TB2_REPO, str(repo_dir)],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "sparse-checkout", "set", TB2_SUBPATH],
            cwd=repo_dir, check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "checkout", TB2_COMMIT, "--", TB2_SUBPATH],
            cwd=repo_dir, check=True, capture_output=True, text=True,
        )

        src = repo_dir / TB2_SUBPATH
        challenges_dir.mkdir(parents=True, exist_ok=True)

        import shutil
        for item in src.iterdir():
            dest = challenges_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)

    count = sum(1 for d in challenges_dir.iterdir() if d.is_dir())
    logger.info("Downloaded %d challenges to %s", count, challenges_dir)
    return challenges_dir


@dataclass
class TB2Task:
    """A single Terminal-Bench 2.0 challenge."""

    name: str
    prompt: str
    docker_image: str
    test_sh_path: str
    test_py_path: Optional[str] = None
    files: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    timeout: int = 900


def load_task(challenge_dir: str | Path) -> TB2Task:
    """Load a single challenge from its directory."""
    challenge_dir = Path(challenge_dir)
    name = challenge_dir.name

    eval_path = challenge_dir / "eval.yaml"
    if not eval_path.exists():
        raise FileNotFoundError(f"No eval.yaml in {challenge_dir}")
    with open(eval_path) as f:
        eval_cfg = yaml.safe_load(f)

    compose_path = challenge_dir / "compose.yaml"
    if not compose_path.exists():
        raise FileNotFoundError(f"No compose.yaml in {challenge_dir}")
    with open(compose_path) as f:
        compose_cfg = yaml.safe_load(f)

    # Extract docker image from compose
    services = compose_cfg.get("services", {})
    default_svc = services.get("default", {})
    docker_image = default_svc.get("image", f"alexgshaw/{name}:{DEFAULT_TAG}")

    # Extract prompt from default variant
    variants = eval_cfg.get("variants", {})
    default_variant = variants.get("default", {})
    prompt = default_variant.get("prompt", "")

    # File mappings — resolve to absolute paths
    raw_files = eval_cfg.get("files", {})
    files = {}
    for container_path, local_rel in raw_files.items():
        local_abs = challenge_dir / local_rel
        files[container_path] = str(local_abs)

    # Test files
    test_sh = challenge_dir / "tests" / "test.sh"
    test_py = challenge_dir / "tests" / "test_outputs.py"

    metadata = eval_cfg.get("metadata", {})
    metadata["category"] = metadata.get("category", "unknown")
    metadata["difficulty"] = metadata.get("difficulty", "unknown")
    timeout = metadata.get("agent_timeout_sec", 900)

    return TB2Task(
        name=name,
        prompt=prompt.strip(),
        docker_image=docker_image,
        test_sh_path=str(test_sh) if test_sh.exists() else "",
        test_py_path=str(test_py) if test_py.exists() else None,
        files=files,
        metadata=metadata,
        timeout=timeout,
    )


def load_all_tasks(challenges_dir: str | None = None) -> list[TB2Task]:
    """Load all Terminal-Bench 2.0 challenges.

    Auto-downloads challenges from GitHub if the directory is empty.
    """
    challenges_dir_path = ensure_challenges(Path(challenges_dir or CHALLENGES_DIR))
    tasks = []
    for d in sorted(challenges_dir_path.iterdir()):
        if d.is_dir() and (d / "eval.yaml").exists():
            try:
                tasks.append(load_task(d))
            except Exception as e:
                print(f"WARNING: Failed to load {d.name}: {e}")
    return tasks


def get_task(name: str, challenges_dir: str | None = None) -> TB2Task:
    """Load a single challenge by name."""
    challenges_dir_path = Path(challenges_dir or CHALLENGES_DIR)
    challenge_dir = challenges_dir_path / name
    if not challenge_dir.exists():
        raise ValueError(f"Challenge '{name}' not found in {challenges_dir_path}")
    return load_task(challenge_dir)
