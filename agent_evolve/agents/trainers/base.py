"""BaseTrainer -- evolvable agent that runs training via a pre-built docker image.

The trainer itself holds no training code. It is a thin orchestrator that:
  1. copies the current workspace into a per-cycle workdir
  2. runs ``docker run <image> train`` with standard mounts (`/workspace`, `/data`, `/out`)
  3. surfaces the resulting ckpt path via ``Trajectory.output``
  4. exposes ``run_inference()`` so training-benchmarks can drive the ``inference`` verb

See ``.claude/a-evolve-plan.md`` §3 for the full contract.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml

from ...contract.manifest import Manifest
from ...protocol.base_agent import BaseAgent
from ...types import Task, Trajectory

logger = logging.getLogger(__name__)


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


class BaseTrainer(BaseAgent):
    """Base class for docker-run-driven training agents.

    Subclasses typically only override ``_summarize_log`` to parse their
    framework's training output format.
    """

    def __init__(self, workspace_dir: str | Path, work_root: str | Path | None = None):
        # work_root must be set before super().__init__ because it calls reload_from_fs.
        workspace_path = Path(workspace_dir).resolve()
        # Sibling directory, NOT nested in workspace. Nested would cause
        # per-cycle workdirs to be picked up by the evolution git repo and
        # re-copied into later cycles' workdirs.
        default_root = workspace_path.parent / f"{workspace_path.name}_runs"
        self.work_root: Path = Path(work_root).resolve() if work_root else default_root

        # Populated in reload_from_fs.
        self.training_config: dict = {}
        self.data_config: dict = {}
        self.base_ckpt: dict = {}
        self.docker_image: str = ""
        self.train_args: list[str] = []
        self.inference_args: list[str] = []
        self.ckpt_rel_path: str = ""
        self.manifest_training: dict = {}

        super().__init__(workspace_dir)

    # ── File System Contract ─────────────────────────────────────────

    def reload_from_fs(self) -> None:
        super().reload_from_fs()
        root = self.workspace.root

        self.training_config = _load_yaml(root / "training_config.yaml")
        self.data_config = _load_yaml(root / "data_config.yaml")
        self.base_ckpt = _load_yaml(root / "base_ckpt.yaml")

        manifest_path = root / "manifest.yaml"
        if manifest_path.exists():
            m = Manifest.from_yaml(manifest_path)
            t = m.training or {}
            self.manifest_training = t
            self.docker_image = t.get("docker_image", "")
            self.train_args = list(t.get("train_args", []))
            self.inference_args = list(t.get("inference_args", []))
            self.ckpt_rel_path = t.get("ckpt_path", "")

    # ── Workdir preparation ──────────────────────────────────────────

    def _prepare_workdir(self, workdir: Path) -> None:
        """Copy the current workspace into ``workdir`` (excluding engine artifacts)."""
        if workdir.exists():
            shutil.rmtree(workdir)
        shutil.copytree(
            self.workspace.root,
            workdir,
            dirs_exist_ok=False,
            ignore=shutil.ignore_patterns("evolution", ".git", "__pycache__", "*.pyc"),
        )
        (workdir / "out").mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _data_dir() -> Path:
        return Path(os.environ.get("AEVOLVE_DATA_DIR", "./evolution_workdir/_data")).resolve()

    @staticmethod
    def _models_dir() -> Path | None:
        """Host path bind-mounted to /models in the container (for local base ckpts).

        Returns None when ``AEVOLVE_MODELS_DIR`` is unset, in which case the
        container can still pull from HF Hub via network.
        """
        val = os.environ.get("AEVOLVE_MODELS_DIR")
        if not val:
            return None
        p = Path(val)
        return p.resolve() if p.exists() else None

    # Env vars forwarded from host into the container when set. Tokens for
    # gated HF models, optional HF cache redirection.
    _FORWARDED_ENV: tuple[str, ...] = (
        "HF_TOKEN",
        "HUGGING_FACE_HUB_TOKEN",
        "HF_HOME",
        "TRANSFORMERS_CACHE",
    )

    @classmethod
    def _env_passthrough_args(cls) -> list[str]:
        args: list[str] = []
        for name in cls._FORWARDED_ENV:
            val = os.environ.get(name)
            if val:
                args.extend(["-e", f"{name}={val}"])
        return args

    @classmethod
    def _models_mount_args(cls) -> list[str]:
        """Mount models dir into the container.

        ``AEVOLVE_MODELS_DIR`` (e.g. ``/fsx/models``) → ``/models:ro``.
        If it contains symlinks whose targets are outside, set
        ``AEVOLVE_SHARED_FS_ROOT`` (e.g. ``/fsx``) to mount the whole
        shared filesystem so symlinks resolve inside the container.
        """
        args: list[str] = []
        mdir = cls._models_dir()
        if mdir is not None:
            args.extend(["-v", f"{mdir}:/models:ro"])
        shared = os.environ.get("AEVOLVE_SHARED_FS_ROOT")
        if shared:
            sp = Path(shared).resolve()
            if sp.exists():
                args.extend(["-v", f"{sp}:{sp}:ro"])
        return args

    def _image_driver_mount_args(self) -> list[str]:
        """Bind-mount the workspace's docker/inference.py over the baked-in copy.

        Lets us iterate on the inference driver without rebuilding the image.
        Only applied when ``<workspace>/docker/inference.py`` exists.
        """
        driver = self.workspace.root / "docker" / "inference.py"
        if not driver.exists():
            return []
        return ["-v", f"{driver.resolve()}:/opt/aevolve/inference.py:ro"]

    @property
    def _is_local(self) -> bool:
        """When ``docker_image`` is ``__local__``, run scripts directly on the host."""
        return self.docker_image == "__local__"

    @staticmethod
    def _local_python() -> str:
        return os.environ.get("AEVOLVE_PYTHON", "python")

    # ── Train verb ───────────────────────────────────────────────────

    def solve(self, task: Task) -> Trajectory:
        if not self.docker_image:
            raise RuntimeError(
                "manifest.training.docker_image is not set; cannot invoke train verb."
            )
        if not self.ckpt_rel_path:
            raise RuntimeError(
                "manifest.training.ckpt_path is not set; cannot locate trained ckpt."
            )

        workdir = self.work_root / f"cycle-{task.id}"
        self._prepare_workdir(workdir)
        out_dir = workdir / "out"
        data_dir = self._data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)

        log_path = workdir / "train.log"
        if self._is_local:
            cmd = [self._local_python(), str(workdir / "train.py")]
            env = {
                **os.environ,
                "AEVOLVE_WORKSPACE": str(workdir.resolve()),
                "AEVOLVE_OUT": str(out_dir.resolve()),
                "AEVOLVE_DATA": str(data_dir),
                "AEVOLVE_CYCLE": str(task.id),
                "AEVOLVE_VERB": "train",
            }
            logger.info("Running train verb (local): %s", " ".join(cmd))
            with open(log_path, "w") as log_file:
                subprocess.run(cmd, check=True, stdout=log_file, stderr=subprocess.STDOUT, env=env)
        else:
            cmd = [
                "docker", "run", "--rm", "--gpus", "all", "--shm-size=16g",
                "-v", f"{workdir.resolve()}:/workspace",
                "-v", f"{data_dir}:/data:ro",
                "-v", f"{out_dir.resolve()}:/out",
                *self._models_mount_args(),
                "-e", f"AEVOLVE_CYCLE={task.id}",
                "-e", "AEVOLVE_VERB=train",
                *self._env_passthrough_args(),
                self.docker_image,
                "train",
                *self.train_args,
            ]
            logger.info("Running train verb: %s", " ".join(cmd))
            with open(log_path, "w") as log_file:
                subprocess.run(cmd, check=True, stdout=log_file, stderr=subprocess.STDOUT)

        ckpt_path = out_dir / self.ckpt_rel_path
        return Trajectory(
            task_id=task.id,
            output=str(ckpt_path),
            steps=self._summarize_log(log_path),
        )

    # ── Inference verb ───────────────────────────────────────────────

    def run_inference(
        self,
        ckpt_dir: Path,
        config_path_in_workspace: str,
        mode: str,
        out_dir: Path,
    ) -> Path:
        """Invoke the image's ``inference`` verb. Returns ``out_dir``."""
        if not self.docker_image:
            raise RuntimeError(
                "manifest.training.docker_image is not set; cannot invoke inference verb."
            )
        out_dir.mkdir(parents=True, exist_ok=True)
        data_dir = self._data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)

        if self._is_local:
            driver = self.workspace.root / "docker" / "inference.py"
            if not driver.exists():
                raise FileNotFoundError(f"Local mode requires {driver}")
            cmd = [self._local_python(), str(driver), "--config", config_path_in_workspace]
            env = {
                **os.environ,
                "AEVOLVE_WORKSPACE": str(self.workspace.root.resolve()),
                "AEVOLVE_OUT": str(out_dir.resolve()),
                "AEVOLVE_DATA": str(data_dir),
                "AEVOLVE_CKPT": str(Path(ckpt_dir).resolve()),
                "AEVOLVE_VERB": "inference",
                "AEVOLVE_INFER_MODE": mode,
            }
            log_path = out_dir / f"inference_{mode}.log"
            logger.info("Running inference verb (local %s): %s", mode, " ".join(cmd))
            with open(log_path, "w") as log_file:
                subprocess.run(cmd, check=True, stdout=log_file, stderr=subprocess.STDOUT, env=env)
        else:
            cmd = [
                "docker", "run", "--rm", "--gpus", "all", "--shm-size=16g",
                "-v", f"{self.workspace.root.resolve()}:/workspace:ro",
                "-v", f"{data_dir}:/data:ro",
                "-v", f"{Path(ckpt_dir).resolve()}:/ckpt:ro",
                "-v", f"{out_dir.resolve()}:/out",
                *self._models_mount_args(),
                *self._image_driver_mount_args(),
                "-e", "AEVOLVE_VERB=inference",
                "-e", f"AEVOLVE_INFER_MODE={mode}",
                *self._env_passthrough_args(),
                self.docker_image,
                "inference",
                "--config", config_path_in_workspace,
                *self.inference_args,
            ]
            log_path = out_dir / f"inference_{mode}.log"
            logger.info("Running inference verb (%s): %s", mode, " ".join(cmd))
            with open(log_path, "w") as log_file:
                subprocess.run(cmd, check=True, stdout=log_file, stderr=subprocess.STDOUT)
        return out_dir

    # ── Log parsing (override in subclasses) ─────────────────────────

    _STEP_RE = re.compile(
        r"(?P<key>loss|lr|learning_rate|val_loss|val_bpb|bpb|step|epoch)\s*[:=]\s*"
        r"(?P<value>-?\d+(?:\.\d+)?(?:e-?\d+)?)",
        re.IGNORECASE,
    )

    def _summarize_log(self, log_path: Path, max_entries: int = 50) -> list[dict[str, Any]]:
        """Parse training log into a compact list of step dicts.

        Default implementation picks up lines containing common metrics
        (``loss``, ``lr``, ``step``, ``val_*``). Subclasses override for
        framework-specific formats.
        """
        if not log_path.exists():
            return []
        entries: list[dict[str, Any]] = []
        with open(log_path) as f:
            for line in f:
                matches = list(self._STEP_RE.finditer(line))
                if not matches:
                    continue
                entry: dict[str, Any] = {"type": "train_step"}
                for m in matches:
                    key = m.group("key").lower()
                    try:
                        entry[key] = float(m.group("value"))
                    except ValueError:
                        continue
                if any(k in entry for k in ("val_loss", "val_bpb", "bpb")):
                    entry["type"] = "eval"
                entries.append(entry)
        # Keep tail.
        return entries[-max_entries:]
