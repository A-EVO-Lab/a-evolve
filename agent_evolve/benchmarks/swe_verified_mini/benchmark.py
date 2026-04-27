"""SWE-bench Verified Mini benchmark adapter.

Delegates eval script generation and test grading to the swebench package
instead of reimplementing them locally.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
from typing import Any

from swebench.harness.constants import (
    APPLY_PATCH_FAIL,
    RESET_FAILED,
    TESTS_ERROR,
    TESTS_TIMEOUT,
    SWEbenchInstance,
)
from swebench.harness.grading import MAP_REPO_TO_PARSER
from swebench.harness.test_spec.test_spec import TestSpec, make_test_spec

from ...types import Feedback, Observation, Task, Trajectory
from ..base import BenchmarkAdapter

logger = logging.getLogger(__name__)

DEFAULT_EVAL_TIMEOUT = 1800
ERROR_MARKERS = (APPLY_PATCH_FAIL, RESET_FAILED, TESTS_ERROR, TESTS_TIMEOUT,
                 "Failed to reset task environment")


class SweVerifiedMiniBenchmark(BenchmarkAdapter):
    """SWE-bench adapter using the swebench package for eval and grading."""

    @property
    def feedback_capability(self):
        from ...algorithms.unified.types import FeedbackCapability
        return FeedbackCapability(
            has_pass_fail=True,
            has_per_test=True,
            solver_may_propose=True,
            judge_available=True,
        )

    def __init__(
        self,
        dataset_name: str = "MariusHobbhahn/swe-bench-verified-mini",
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
        self._cursor: int = 0

    def get_tasks(self, split: str = "test", limit: int = 10) -> list[Task]:
        """Load SWE-bench tasks from HuggingFace.

        Supports both SWE-bench Verified (has 'version' field) and SWE-bench Live
        (needs version auto-detection, filters to supported repos).

        Stateful cursor pagination: successive calls return contiguous slices
        of the filtered rows, advance the cursor, and reset to 0 at end-of-sweep
        so the next call starts a fresh sweep. A single call never crosses a
        sweep boundary; the remainder batch at end-of-sweep may be shorter
        than ``limit``.
        """
        rows = self._load_split(split)
        tasks = []

        # Detect dataset type and filter accordingly
        has_version = False
        has_dockerhub_tag = False
        has_docker_image = False
        latest_versions: dict[str, str] = {}
        if rows:
            has_version = bool(rows[0].get("version"))
            has_dockerhub_tag = bool(rows[0].get("dockerhub_tag"))
            has_docker_image = "docker_image" in rows[0]

            # Live datasets (no version) — filter to supported repos + assign version
            if not has_version and not has_dockerhub_tag:
                from swebench import MAP_REPO_VERSION_TO_SPECS
                supported_repos = set(MAP_REPO_VERSION_TO_SPECS.keys())
                latest_versions = {
                    repo: sorted(versions.keys())[-1]
                    for repo, versions in MAP_REPO_VERSION_TO_SPECS.items()
                }
                rows = [r for r in rows if r.get("repo") in supported_repos]
                logger.info("Filtered to %d tasks from supported repos", len(rows))

            # Filter to tasks with Docker images (SWE-ReBench has None for some)
            if has_docker_image:
                before = len(rows)
                rows = [r for r in rows if r.get("docker_image") or r.get("image_name")]
                if len(rows) < before:
                    logger.info("Filtered to %d tasks with Docker images (from %d)", len(rows), before)

        n = len(rows)
        if n == 0:
            return tasks
        if self._cursor >= n:
            self._cursor = 0
        end = min(self._cursor + limit, n)
        batch_rows = rows[self._cursor:end]
        self._cursor = end
        if self._cursor >= n:
            self._cursor = 0

        for row in batch_rows:
            instance_id = row["instance_id"]

            # Derive version for Live tasks
            version = row.get("version", "")
            if not version and not has_version and not has_dockerhub_tag:
                version = latest_versions.get(row.get("repo", ""), "")

            # Derive docker image — check multiple possible field names
            docker_image = row.get("docker_image") or row.get("image_name") or ""
            if not docker_image:
                # SWE-bench Pro: dockerhub_tag needs registry prefix
                dh_tag = row.get("dockerhub_tag", "")
                if dh_tag and not dh_tag.startswith("swebench/"):
                    docker_image = f"jefzda/sweap-images:{dh_tag}"
                elif dh_tag:
                    docker_image = dh_tag
                else:
                    docker_image = _instance_to_docker_image(instance_id)

            tasks.append(Task(
                id=instance_id,
                input=row.get("problem_statement", ""),
                metadata={
                    "instance_id": instance_id,
                    "docker_image": docker_image,
                    "repo": row.get("repo", ""),
                    "base_commit": row.get("base_commit", ""),
                    "version": version,
                    "test_patch": row.get("test_patch", ""),
                    "hints_text": row.get("hints_text", ""),
                    "FAIL_TO_PASS": _parse_list_field(row, "FAIL_TO_PASS", "fail_to_pass"),
                    "PASS_TO_PASS": _parse_list_field(row, "PASS_TO_PASS", "pass_to_pass"),
                    "patch": row.get("patch", ""),
                    "created_at": row.get("created_at", ""),
                    "environment_setup_commit": row.get("environment_setup_commit", ""),
                },
            ))
        return tasks

    def solve_batch_parallel(self, tasks, agent, config) -> list[Observation] | None:
        """SWE legacy-compatible process backend.

        The old ``examples/swe_examples/evolve_sequential.py`` used a
        ProcessPoolExecutor where each worker reconstructed its own SweAgent
        and benchmark. Do the same here instead of trying to pickle/share the
        live agent, LLM client, or benchmark object across processes.
        """
        backend = str(getattr(config, "parallel_backend", "thread") or "thread").lower()
        if backend not in {"process", "benchmark"}:
            return None

        from concurrent.futures import ProcessPoolExecutor, as_completed

        workers = max(1, int(getattr(config, "parallel_workers", 1) or 1))
        if workers <= 1 or len(tasks) <= 1:
            return None

        workspace_dir = str(agent.workspace.root)
        model_id = getattr(agent, "model_id", "us.anthropic.claude-opus-4-6-v1")
        region = getattr(agent, "region", "us-west-2")
        max_tokens = int(getattr(agent, "max_tokens", 16384))
        max_steps = int(getattr(agent, "max_steps", 0))
        window_size = int(getattr(agent, "window_size", 40))
        verification_focus = bool(getattr(agent, "verification_focus", False))
        efficiency_prompt = bool(getattr(agent, "efficiency_prompt", False))

        payloads = [
            {
                "id": task.id,
                "input": task.input,
                "metadata": task.metadata,
            }
            for task in tasks
        ]

        by_index: dict[int, Observation] = {}
        with ProcessPoolExecutor(max_workers=min(workers, len(payloads))) as pool:
            futures = {
                pool.submit(
                    _solve_swe_task_process,
                    idx,
                    payload,
                    workspace_dir,
                    model_id,
                    region,
                    max_tokens,
                    max_steps,
                    window_size,
                    verification_focus,
                    efficiency_prompt,
                    self.dataset_name,
                    self.eval_timeout,
                ): idx
                for idx, payload in enumerate(payloads)
            }
            for fut in as_completed(futures):
                idx = futures[fut]
                try:
                    result_idx, obs = fut.result()
                except Exception as exc:  # noqa: BLE001
                    logger.error("SWE process worker failed for task %s: %s",
                                 tasks[idx].id, exc)
                    continue
                by_index[result_idx] = obs

        return [by_index[i] for i in sorted(by_index)]

    def evaluate(self, task: Task, trajectory: Trajectory) -> Feedback:
        """Evaluate a patch by applying it in Docker and grading via swebench."""
        patch = trajectory.output
        metadata = task.metadata
        instance_id = task.id

        if not patch.strip():
            return Feedback(
                success=False, score=0.0,
                detail=f"Empty patch for {instance_id}",
                raw={"instance_id": instance_id, "reason": "empty_patch"},
            )

        # Build SWEbenchInstance and use swebench's make_test_spec for eval script
        try:
            swe_instance = _build_swe_instance(task)
            test_spec = make_test_spec(swe_instance)
            eval_script = test_spec.eval_script
        except Exception as e:
            return Feedback(
                success=False, score=0.0,
                detail=f"Failed to generate eval script: {e}",
                raw={"instance_id": instance_id, "error": str(e)},
            )

        import uuid as _uuid
        container_name = f"swe-eval-{instance_id.replace('/', '_')}-{_uuid.uuid4().hex}"
        docker_image = metadata.get("docker_image", "")

        if not docker_image:
            return Feedback(
                success=False, score=0.0,
                detail=f"No docker_image for {instance_id}",
                raw={"instance_id": instance_id, "reason": "no_docker_image"},
            )

        try:
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)

            result = subprocess.run(
                ["docker", "run", "-d", "--name", container_name, docker_image, "sleep", "infinity"],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                return Feedback(
                    success=False, score=0.0,
                    detail=f"Container start failed: {result.stderr}",
                    raw={"instance_id": instance_id, "reason": "container_start_failed"},
                )

            def _exec(cmd: str, timeout: int = 120) -> tuple[str, str]:
                r = subprocess.run(
                    ["docker", "exec", "-w", "/testbed", container_name, "bash", "-c", cmd],
                    capture_output=True, text=True, timeout=timeout,
                )
                return r.stdout or "", r.stderr or ""

            # Apply model patch
            with tempfile.NamedTemporaryFile(mode="w", suffix=".diff", delete=False) as pf:
                pf.write(patch)
                patch_tmp = pf.name
            try:
                subprocess.run(
                    ["docker", "cp", patch_tmp, f"{container_name}:/tmp/model_patch.diff"],
                    capture_output=True, timeout=30,
                )
            finally:
                os.unlink(patch_tmp)
            _exec("git apply /tmp/model_patch.diff")

            # Write and run eval script
            with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as f:
                f.write(eval_script)
                tmp_path = f.name
            try:
                subprocess.run(
                    ["docker", "cp", tmp_path, f"{container_name}:/tmp/eval_script.sh"],
                    capture_output=True, timeout=30,
                )
            finally:
                os.unlink(tmp_path)

            stdout, stderr = _exec(
                "chmod +x /tmp/eval_script.sh && /tmp/eval_script.sh",
                timeout=self.eval_timeout,
            )

            # Grade using swebench parsers
            test_output = stdout + "\n" + stderr
            score, explanation = _grade_with_swebench(
                test_output=test_output,
                test_spec=test_spec,
                fail_to_pass=metadata.get("FAIL_TO_PASS", []),
                pass_to_pass=metadata.get("PASS_TO_PASS", []),
            )

            return Feedback(
                success=score == 1.0,
                score=score,
                detail=explanation,
                raw={"instance_id": instance_id},
            )

        except subprocess.TimeoutExpired:
            return Feedback(
                success=False, score=0.0,
                detail=f"Eval timed out after {self.eval_timeout}s",
                raw={"instance_id": instance_id, "reason": "timeout"},
            )
        except Exception as e:
            logger.error("Evaluation failed for %s: %s", instance_id, e)
            return Feedback(
                success=False, score=0.0,
                detail=f"Container error: {e}",
                raw={"instance_id": instance_id, "error": str(e)},
            )
        finally:
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)

    # ── Internals ────────────────────────────────────────────────────

    def _load_split(self, split: str) -> list[dict]:
        if not self._split_done:
            self._do_split()
        if split in self._cache:
            return self._cache[split]
        return self._cache.get("train", [])

    def _do_split(self) -> None:
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
        self._cache["test"] = rows

        self._split_done = True
        logger.info(
            "Loaded %d tasks from %s (train=%d, holdout=%d)",
            len(rows), self.dataset_name,
            len(self._cache["train"]), len(self._cache["holdout"]),
        )


# ── Helpers ──────────────────────────────────────────────────────────────

def _build_swe_instance(task: Task) -> SWEbenchInstance:
    """Build a SWEbenchInstance dict from our Task metadata."""
    m = task.metadata
    return SWEbenchInstance(
        repo=m["repo"],
        instance_id=task.id,
        base_commit=m["base_commit"],
        patch=m.get("patch", ""),
        test_patch=m["test_patch"],
        problem_statement=task.input,
        hints_text=m.get("hints_text", ""),
        created_at=m.get("created_at", ""),
        version=m["version"],
        FAIL_TO_PASS=json.dumps(m.get("FAIL_TO_PASS", [])),
        PASS_TO_PASS=json.dumps(m.get("PASS_TO_PASS", [])),
        environment_setup_commit=m.get("environment_setup_commit", ""),
    )


def _grade_with_swebench(
    test_output: str,
    test_spec: TestSpec,
    fail_to_pass: list[str],
    pass_to_pass: list[str],
) -> tuple[float, str]:
    """Grade test output using swebench's parsers. Returns (score, explanation)."""
    # Check error markers
    for marker in ERROR_MARKERS:
        if marker in test_output:
            return 0.0, f"Error marker found: {marker}\n\n{test_output[-2000:]}"

    parser = MAP_REPO_TO_PARSER.get(test_spec.repo)
    if parser is None:
        return 0.0, f"No parser for repo: {test_spec.repo}"

    try:
        parsed = parser(test_output, test_spec)
    except Exception as e:
        return 0.0, f"Parser error: {e}"

    fail_to_pass_results = {k: "FAILED" for k in fail_to_pass}
    pass_to_pass_results = {k: "FAILED" for k in pass_to_pass}

    for k, v in parsed.items():
        if k in fail_to_pass_results:
            fail_to_pass_results[k] = v
        elif k in pass_to_pass_results:
            pass_to_pass_results[k] = v

    passed_all = (
        all(v == "PASSED" for v in fail_to_pass_results.values())
        and all(v == "PASSED" for v in pass_to_pass_results.values())
    )
    score = 1.0 if passed_all else 0.0

    sorted_p2p = dict(sorted(pass_to_pass_results.items(), key=lambda x: x[1] == "PASSED"))
    sorted_f2p = dict(sorted(fail_to_pass_results.items(), key=lambda x: x[1] == "PASSED"))

    explanation = (
        f"PASS_TO_PASS:\n\n{json.dumps(sorted_p2p, indent=2)}"
        f"\n\nFAIL_TO_PASS:\n\n{json.dumps(sorted_f2p, indent=2)}\n\n"
    )
    return score, explanation


def _solve_swe_task_process(
    idx: int,
    task_payload: dict[str, Any],
    workspace_dir: str,
    model_id: str,
    region: str,
    max_tokens: int,
    max_steps: int,
    window_size: int,
    verification_focus: bool,
    efficiency_prompt: bool,
    dataset_name: str,
    eval_timeout: int,
) -> tuple[int, Observation]:
    """Process-pool worker used by ``SweVerifiedMiniBenchmark``."""
    task = Task(
        id=task_payload["id"],
        input=task_payload.get("input", ""),
        metadata=task_payload.get("metadata", {}),
    )
    try:
        from ...agents.swe.agent import SweAgent

        agent = SweAgent(
            workspace_dir=workspace_dir,
            model_id=model_id,
            region=region,
            max_tokens=max_tokens,
            max_steps=max_steps,
            window_size=window_size,
            verification_focus=verification_focus,
            efficiency_prompt=efficiency_prompt,
        )
        benchmark = SweVerifiedMiniBenchmark(
            dataset_name=dataset_name,
            shuffle=False,
            eval_timeout=eval_timeout,
        )
        trajectory = agent.solve(task)
        feedback = benchmark.evaluate(task, trajectory)
    except Exception as exc:  # noqa: BLE001
        trajectory = Trajectory(
            task_id=task.id,
            output="",
            steps=[{"error": str(exc)[:1000]}],
        )
        feedback = Feedback(
            success=False,
            score=0.0,
            detail=str(exc)[:1000],
            raw={"instance_id": task.id, "error": str(exc)[:1000]},
        )
    return idx, Observation(task=task, trajectory=trajectory, feedback=feedback)


def _parse_list_field(row: dict, *keys: str) -> list:
    """Parse a list field that may be stored as list, JSON string, or under different key names."""
    for key in keys:
        val = row.get(key)
        if val is not None:
            if isinstance(val, list):
                return val
            if isinstance(val, str) and val.strip():
                try:
                    return json.loads(val)
                except json.JSONDecodeError:
                    # Handle mixed quotes or other non-standard JSON
                    import ast
                    try:
                        return ast.literal_eval(val)
                    except Exception:
                        return [val]
    return []


def _instance_to_docker_image(instance_id: str) -> str:
    parts = instance_id.split("__")
    if len(parts) != 2:
        raise ValueError(f"Invalid instance_id format: {instance_id}")
    return f"swebench/sweb.eval.x86_64.{parts[0]}_1776_{parts[1]}"
