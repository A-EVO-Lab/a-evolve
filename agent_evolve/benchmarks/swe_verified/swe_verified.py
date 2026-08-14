"""SWE-bench Verified benchmark adapter.

Ported from CodeDojo/swe-agent/swe_agent/dataset.py and docker_env.py.

Input:  GitHub Issue description
Output: Unified diff patch
Feedback: Unit test pass/fail with error details
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
from dataclasses import asdict
from typing import Any

from ...types import Feedback, Task, Trajectory
from ..base import BenchmarkAdapter
from .constants import DEFAULT_EVAL_TIMEOUT
from .eval_script import generate_eval_script
from .test_grader import grade_test_output
logger = logging.getLogger(__name__)


class SweVerifiedBenchmark(BenchmarkAdapter):
    """SWE-bench Verified benchmark adapter.

    Loads tasks from HuggingFace ``princeton-nlp/SWE-bench_Verified``
    (or any SWE-bench variant) and evaluates patches by applying them
    in Docker containers and running the original test suite.
    """

    def __init__(
        self,
        dataset_name: str = "princeton-nlp/SWE-bench_Verified",
        repo_filter: str | None = None,
        shuffle: bool = True,
        holdout_ratio: float = 0.2,
        eval_timeout: int = DEFAULT_EVAL_TIMEOUT,
    ):
        self.dataset_name = dataset_name
        self.repo_filter = repo_filter
        self.shuffle = shuffle
        self.holdout_ratio = holdout_ratio
        self.eval_timeout = eval_timeout
        self._cache: dict[str, list[dict]] = {}
        self._split_done = False

    def get_tasks(self, split: str = "test", limit: int = 10) -> list[Task]:
        """Load SWE-bench tasks from HuggingFace.

        Each Task carries metadata needed by the SweAgent:
          - docker_image: the swebench eval Docker image
          - instance_id: unique SWE-bench instance identifier
          - base_commit, repo, version, etc.
        """
        rows = self._load_split(split)
        tasks = []
        for row in rows[:limit]:
            instance_id = row["instance_id"]
            docker_image = _instance_to_docker_image(instance_id)
            tasks.append(Task(
                id=instance_id,
                input=row.get("problem_statement", ""),
                metadata={
                    "instance_id": instance_id,
                    "docker_image": docker_image,
                    "repo": row.get("repo", ""),
                    "base_commit": row.get("base_commit", ""),
                    "version": row.get("version", ""),
                    "test_patch": row.get("test_patch", ""),
                    "hints_text": row.get("hints_text", ""),
                    "FAIL_TO_PASS": json.loads(row.get("FAIL_TO_PASS", "[]")),
                    "PASS_TO_PASS": json.loads(row.get("PASS_TO_PASS", "[]")),
                    "patch": row.get("patch", ""),
                    "created_at": row.get("created_at", ""),
                    "environment_setup_commit": row.get("environment_setup_commit", ""),
                },
            ))
        return tasks

    def evaluate(self, task: Task, trajectory: Trajectory) -> Feedback:
        """Evaluate a patch using the self-contained evaluation pipeline.

        1. Validate patch is non-empty
        2. Generate eval script via ``generate_eval_script()``
        3. Start Docker container from SWE-bench eval image
        4. Copy model patch into container and apply via ``git apply``
        5. Write eval script to container and execute with configurable timeout
        6. Capture stdout+stderr
        7. Grade via ``grade_test_output()``
        8. Return ``Feedback`` mapped from ``GradeResult``
        """
        patch = trajectory.output
        metadata = task.metadata
        instance_id = task.id

        if not patch.strip():
            return Feedback(
                success=False,
                score=0.0,
                detail=f"Empty patch for {instance_id}",
                raw={"instance_id": instance_id, "reason": "empty_patch"},
            )

        try:
            eval_script = generate_eval_script(
                test_patch=metadata["test_patch"],
                repo=metadata["repo"],
                version=metadata["version"],
                base_commit=metadata["base_commit"],
            )
        except KeyError as e:
            return Feedback(
                success=False,
                score=0.0,
                detail=f"Missing repo spec: {e}",
                raw={"instance_id": instance_id, "reason": "missing_spec", "error": str(e)},
            )

        container_name = f"swe-eval-{instance_id.replace('/', '_')}"
        docker_image = metadata.get("docker_image", "")

        if not docker_image:
            return Feedback(
                success=False,
                score=0.0,
                detail=f"No docker_image for {instance_id}",
                raw={"instance_id": instance_id, "reason": "no_docker_image"},
            )

        try:
            # Clean up any existing container
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)

            # Start container
            result = subprocess.run(
                ["docker", "run", "-d", "--name", container_name, docker_image, "sleep", "infinity"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode != 0:
                return Feedback(
                    success=False,
                    score=0.0,
                    detail=f"Container start failed: {result.stderr}",
                    raw={"instance_id": instance_id, "reason": "container_start_failed"},
                )

            def _exec(cmd: str, timeout: int = 120) -> tuple[str, str]:
                r = subprocess.run(
                    ["docker", "exec", "-w", "/testbed", container_name, "bash", "-c", cmd],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                return r.stdout or "", r.stderr or ""

            # Apply model patch via docker cp (avoids heredoc/shell escaping issues)
            with tempfile.NamedTemporaryFile(mode="w", suffix=".diff", delete=False) as pf:
                pf.write(patch)
                patch_tmp = pf.name
            try:
                subprocess.run(
                    ["docker", "cp", patch_tmp, f"{container_name}:/tmp/model_patch.diff"],
                    capture_output=True,
                    timeout=30,
                )
            finally:
                os.unlink(patch_tmp)
            _exec("git apply /tmp/model_patch.diff")

            # Write eval script to container via docker cp
            with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as f:
                f.write(eval_script)
                tmp_path = f.name
            try:
                subprocess.run(
                    ["docker", "cp", tmp_path, f"{container_name}:/tmp/eval_script.sh"],
                    capture_output=True,
                    timeout=30,
                )
            finally:
                os.unlink(tmp_path)

            stdout, stderr = _exec(
                "chmod +x /tmp/eval_script.sh && /tmp/eval_script.sh",
                timeout=self.eval_timeout,
            )

            # Grade
            test_output = stdout + "\n" + stderr
            grade_result = grade_test_output(
                test_output=test_output,
                repo=metadata["repo"],
                fail_to_pass=metadata.get("FAIL_TO_PASS", []),
                pass_to_pass=metadata.get("PASS_TO_PASS", []),
            )

            return Feedback(
                success=grade_result.passed,
                score=grade_result.score,
                detail=grade_result.explanation,
                raw={"grade_result": asdict(grade_result), "instance_id": instance_id},
            )

        except subprocess.TimeoutExpired:
            return Feedback(
                success=False,
                score=0.0,
                detail=f"Eval timed out after {self.eval_timeout}s",
                raw={"instance_id": instance_id, "reason": "timeout"},
            )
        except Exception as e:
            logger.error("Evaluation failed for %s: %s", instance_id, e)
            return Feedback(
                success=False,
                score=0.0,
                detail=f"Container error: {e}",
                raw={"instance_id": instance_id, "error": str(e)},
            )
        finally:
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)

    # ── Internals ────────────────────────────────────────────────────

    def _load_split(self, split: str) -> list[dict]:
        """Load and cache a dataset split from HuggingFace.

        SWE-bench Verified only has a ``test`` split, so we load it once
        and partition into train/holdout ourselves.  Any split name the
        engine asks for (train, holdout, test) is mapped accordingly.
        """
        if not self._split_done:
            self._do_split()

        # Map requested split to our internal partitions
        if split in self._cache:
            return self._cache[split]

        # Fallback: anything unknown maps to train
        return self._cache.get("train", [])

    def _do_split(self) -> None:
        """Load the single HF split and partition into train + holdout."""
        from datasets import load_dataset
        import random

        ds = load_dataset(self.dataset_name, split="test")
        rows = [dict(row) for row in ds]

        if self.repo_filter:
            rows = [r for r in rows if self.repo_filter in r.get("repo", "")]

        if self.shuffle:
            random.shuffle(rows)

        n_holdout = max(1, int(len(rows) * self.holdout_ratio))
        self._cache["holdout"] = rows[:n_holdout]
        self._cache["train"] = rows[n_holdout:]
        self._cache["test"] = rows  # full set if anyone asks

        self._split_done = True
        logger.info(
            "Loaded %d tasks from %s (train=%d, holdout=%d)",
            len(rows), self.dataset_name,
            len(self._cache["train"]), len(self._cache["holdout"]),
        )



def _instance_to_docker_image(instance_id: str) -> str:
    """Convert SWE-bench instance_id to Docker image name.

    e.g. astropy__astropy-12907 -> swebench/sweb.eval.x86_64.astropy_1776_astropy-12907
    """
    parts = instance_id.split("__")
    if len(parts) != 2:
        raise ValueError(f"Invalid instance_id format: {instance_id}")
    owner = parts[0]
    repo_issue = parts[1]
    return f"swebench/sweb.eval.x86_64.{owner}_1776_{repo_issue}"


def _docker_image_to_instance_id(image_name: str) -> str:
    """Extract SWE-bench instance_id from a docker image name."""
    image_name = image_name.split(":")[0]
    m = re.match(r"^(?:swebench/)?sweb\.eval\.x86_64\.([^_]+)_\d+_(.+)$", image_name)
    if m:
        return f"{m.group(1)}__{m.group(2)}"
    raise ValueError(f"Cannot extract instance_id from: {image_name}")


