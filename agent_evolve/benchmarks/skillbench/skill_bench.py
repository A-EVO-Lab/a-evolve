"""SkillBench benchmark adapter.

Input:  Task description (from instruction.md in each SkillBench task)
Output: Files / artifacts produced inside the Docker container
Feedback: test.sh pass/fail via reward.txt (binary 0/1)
"""

from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Any, Literal

from ...agents.skillbench.repo import (
    SkillBenchPaths,
    resolve_skillbench_paths,
    validate_skillbench_paths,
)
from ...types import Feedback, Task, Trajectory
from ..base import BenchmarkAdapter

logger = logging.getLogger(__name__)

SkillBenchExecutionMode = Literal["native", "harbor", "dual"]
SkillBenchNativeProfile = Literal["strands", "terminus2", "terminus2_legacy"]
SkillBenchScoreMode = Literal["reward", "binary", "dual"]
SkillBenchFeedbackLevel = Literal["none", "score", "tests", "masked", "full"]


class SkillBenchBenchmark(BenchmarkAdapter):
    """SkillBench benchmark adapter.

    Loads tasks from a SkillBench ``tasks/`` directory on disk and
    evaluates by parsing the verification result embedded in the
    trajectory by :class:`SkillBenchAgent`.
    """

    def __init__(
        self,
        tasks_dir: str | None = None,
        tasks_with_skills_dir: str | None = None,
        tasks_without_skills_dir: str | None = None,
        task_filter: str | None = None,
        category_filter: str | None = None,
        difficulty_filter: str | None = None,
        shuffle: bool = True,
        holdout_ratio: float = 0.2,
        use_skills: bool = True,
        split_seed: int = 42,
        execution_mode: SkillBenchExecutionMode = "native",
        harbor_repo: str | None = None,
        harbor_config_template: str | None = None,
        harbor_agent_import_path: str = (
            "libs.terminus_agent.agents.terminus_2.harbor_terminus_2_skills:HarborTerminus2WithSkills"
        ),
        harbor_model_name: str = "vertex_ai/claude-sonnet-4-5@20250929",
        harbor_jobs_dir: str | None = None,
        harbor_timeout_sec: int = 1800,
        harbor_uv_cmd: str = "uv run harbor run",
        native_profile: SkillBenchNativeProfile = "terminus2",
        score_mode: SkillBenchScoreMode = "dual",
        retry_max: int = 6,
        retry_min_wait_sec: float = 1.0,
        retry_max_wait_sec: float = 120.0,
    ):
        self.task_filter = task_filter
        self.category_filter = category_filter
        self.difficulty_filter = difficulty_filter
        self.shuffle = shuffle
        self.holdout_ratio = holdout_ratio
        self.use_skills = use_skills
        self.split_seed = split_seed
        if execution_mode not in ("native", "harbor", "dual"):
            raise ValueError(
                f"Invalid execution_mode={execution_mode!r}; expected native|harbor|dual"
            )
        self.execution_mode: SkillBenchExecutionMode = execution_mode
        self.harbor_config_template = harbor_config_template
        self.harbor_agent_import_path = harbor_agent_import_path
        self.harbor_model_name = harbor_model_name
        self.harbor_jobs_dir = harbor_jobs_dir
        self.harbor_timeout_sec = harbor_timeout_sec
        self.harbor_uv_cmd = harbor_uv_cmd
        if native_profile not in ("strands", "terminus2", "terminus2_legacy"):
            raise ValueError(
                f"Invalid native_profile={native_profile!r}; expected strands|terminus2|terminus2_legacy"
            )
        if score_mode not in ("reward", "binary", "dual"):
            raise ValueError(
                f"Invalid score_mode={score_mode!r}; expected reward|binary|dual"
            )
        self.native_profile: SkillBenchNativeProfile = native_profile
        self.score_mode: SkillBenchScoreMode = score_mode
        self.retry_max = int(retry_max)
        self.retry_min_wait_sec = float(retry_min_wait_sec)
        self.retry_max_wait_sec = float(retry_max_wait_sec)

        resolved_paths = resolve_skillbench_paths(
            tasks_dir=tasks_dir,
            tasks_with_skills_dir=tasks_with_skills_dir,
            tasks_without_skills_dir=tasks_without_skills_dir,
            harbor_repo=harbor_repo,
        )
        validate_skillbench_paths(
            resolved_paths,
            use_skills=self.use_skills,
            execution_mode=self.execution_mode,
        )
        self.skillbench_paths: SkillBenchPaths = resolved_paths
        self.tasks_with_skills_dir = str(resolved_paths.tasks_with_skills_dir)
        self.tasks_without_skills_dir = str(resolved_paths.tasks_without_skills_dir)
        self.harbor_repo = str(resolved_paths.harbor_repo)
        self.tasks_dir = str(resolved_paths.selected_tasks_dir(use_skills=self.use_skills))
        self._cache: dict[str, list[dict[str, Any]]] = {}
        self._split_done = False

    # ── Public API ───────────────────────────────────────────────────

    def get_tasks(self, split: str = "train", limit: int = 10) -> list[Task]:
        """Return up to *limit* Task objects from the requested split.

        Each Task carries metadata needed by the SkillBenchAgent:
          - dockerfile_dir, test_sh_path, test_py_path, category,
            difficulty, timeouts, etc.
        """
        rows = self._load_split(split)
        tasks: list[Task] = []
        for row in rows[:limit]:
            tasks.append(Task(
                id=row["name"],
                input=row["prompt"],
                metadata={
                    "task_name": row["name"],
                    "task_dir": row["task_dir"],
                    "dockerfile_dir": row["dockerfile_dir"],
                    "test_sh_path": row["test_sh_path"],
                    "test_py_path": row.get("test_py_path"),
                    "category": row.get("category", "unknown"),
                    "difficulty": row.get("difficulty", "unknown"),
                    "agent_timeout_sec": row.get("agent_timeout_sec", 900),
                    "verifier_timeout_sec": row.get("verifier_timeout_sec", 900),
                    "build_timeout_sec": row.get("build_timeout_sec", 600),
                    "cpus": row.get("cpus", 1),
                    "memory": row.get("memory", "4g"),
                    "backend": row.get("backend", "native"),
                    "comparison_key": row.get("comparison_key", row["name"]),
                },
            ))
        return tasks

    def evaluate(self, task: Task, trajectory: Trajectory) -> Feedback:
        """Evaluate based on the verification result in the trajectory.

        The SkillBenchAgent runs test.sh inside the container during
        solve() and embeds the result in trajectory.steps.  We parse
        it here (same pattern as Terminal2Benchmark).
        """
        output = trajectory.output
        steps = trajectory.steps

        passed = False
        pass_binary = False
        reward_float = 0.0
        eval_output = output
        failure_class = "none"
        verifier_tail = ""

        if steps:
            last_step = steps[-1] if steps else {}
            passed = bool(last_step.get("passed", False))
            pass_binary = bool(last_step.get("pass_binary", passed))
            eval_output = last_step.get("eval_output", output)
            verifier_tail = str(last_step.get("verifier_tail", ""))
            failure_class = str(last_step.get("failure_class", "none"))
            maybe_reward = last_step.get("reward_float", last_step.get("score"))
            if isinstance(maybe_reward, (int, float)):
                reward_float = float(max(0.0, min(1.0, maybe_reward)))
            pass_binary = bool(last_step.get("pass_binary", reward_float >= 1.0 or passed))
            passed = pass_binary
            native_impl = last_step.get("native_impl")
            parser_warning = last_step.get("parser_warning")
            parse_error = last_step.get("parse_error")
            skills_loaded = last_step.get("skills_loaded")
            references_loaded = last_step.get("references_loaded")
            prompt_template_source = last_step.get("prompt_template_source")
            prompt_template_sha256 = last_step.get("prompt_template_sha256")
        else:
            native_impl = None
            parser_warning = None
            parse_error = None
            skills_loaded = None
            references_loaded = None
            prompt_template_source = None
            prompt_template_sha256 = None

        if not pass_binary and output.startswith("passed=True"):
            pass_binary = True
            passed = True

        if reward_float == 0.0 and pass_binary:
            reward_float = 1.0

        # Compute partial score from test counts when reward is binary 0/1.
        # This gives the evolver gradient signal (e.g., 34/37 = 0.919 instead of 0.0).
        test_info = self._extract_test_results(verifier_tail)
        partial_score = test_info.get("partial_score", 0.0)
        if reward_float == 0.0 and partial_score > 0.0:
            reward_float = partial_score

        if self.score_mode == "binary":
            score = 1.0 if pass_binary else 0.0
        else:
            score = reward_float

        # Front-load diagnostic info: the evolver truncates feedback_detail to
        # 300 chars (aevolve/prompts.py).  task_id, success, and score are already
        # passed as separate fields, so pack error signal into the first 300 chars.
        category = task.metadata.get("category", "unknown")
        difficulty = task.metadata.get("difficulty", "unknown")

        diag = self._extract_diagnostic(verifier_tail, eval_output)

        # Include compact task description so the evolver knows what was asked
        task_summary = task.input.split("\n")[0].strip()[:80]

        detail = (
            f"{failure_class} [{category}/{difficulty}] reward={reward_float:.3f}\n"
            f"Task: {task_summary}\n"
            f"{diag}\n"
            f"---\n"
            f"Eval output:\n{self._mask_assertion_values(str(eval_output)[:2000])}"
        )

        return Feedback(
            success=pass_binary,
            score=score,
            detail=detail,
            raw={
                "task_name": task.id,
                "passed": pass_binary,
                "pass_binary": pass_binary,
                "reward_float": reward_float,
                "score_mode": self.score_mode,
                "eval_output": str(eval_output)[:4000],
                "verifier_tail": verifier_tail[:4000],
                "failure_class": failure_class,
                "category": task.metadata.get("category", "unknown"),
                "difficulty": task.metadata.get("difficulty", "unknown"),
                "backend": self._extract_backend(trajectory),
                "raw_job_path": self._extract_raw_job_path(trajectory),
                "comparison_key": task.metadata.get("comparison_key", task.id),
                "native_impl": native_impl,
                "parser_warning": parser_warning,
                "parse_error": parse_error,
                "skills_loaded": skills_loaded,
                "references_loaded": references_loaded,
                "prompt_template_source": prompt_template_source,
                "prompt_template_sha256": prompt_template_sha256,
            },
        )

    def get_agent_runtime_config(self) -> dict[str, Any]:
        """Expose runtime config for SkillBench-local orchestration helpers."""
        return {
            "execution_mode": self.execution_mode,
            "harbor_repo": self.harbor_repo,
            "harbor_config_template": self.harbor_config_template,
            "harbor_agent_import_path": self.harbor_agent_import_path,
            "harbor_model_name": self.harbor_model_name,
            "harbor_jobs_dir": self.harbor_jobs_dir,
            "harbor_timeout_sec": self.harbor_timeout_sec,
            "harbor_uv_cmd": self.harbor_uv_cmd,
            "native_profile": self.native_profile,
            "score_mode": self.score_mode,
            "retry_max": self.retry_max,
            "retry_min_wait_sec": self.retry_min_wait_sec,
            "retry_max_wait_sec": self.retry_max_wait_sec,
            "tasks_with_skills_dir": self.tasks_with_skills_dir,
            "tasks_without_skills_dir": self.tasks_without_skills_dir,
            "use_skills": self.use_skills,
        }

    @staticmethod
    def _extract_test_results(verifier_tail: str) -> dict[str, Any]:
        """Parse test output to extract names and aggregate pass/fail counts.

        Supports multiple output formats:
        - pytest: ``test_foo PASSED`` / ``FAILED`` lines with ``::``
        - pytest summary: ``N failed, M passed``
        - Custom verifiers: ``[PASSED]``/``[FAILED]`` or ``Score: 0.91``
        - Custom verifiers: ``Failed: N``, ``Passed: N``
        - Custom verifiers: ``[FAILED] test_name`` or ``[PASSED] test_name``
        """
        import re as _re

        passed: list[str] = []
        failed: list[str] = []

        for line in verifier_tail.splitlines():
            stripped = line.strip()

            # --- Format 1: pytest style (test_foo::TestClass::test_name PASSED) ---
            if "::" in line:
                parts = line.split("::")[-1].split()
                if not parts:
                    continue
                raw_name = parts[0].rstrip("-")
                clean_name = _re.sub(r"\[.*?\]", "", raw_name).strip()
                if not clean_name:
                    continue
                if "PASSED" in line:
                    passed.append(clean_name)
                elif "FAILED" in line or "ERROR" in line:
                    failed.append(clean_name)
                continue

            # --- Format 2: custom verifier [FAILED]/[PASSED] lines ---
            m_bracket_fail = _re.match(r"\[FAILED\]\s*(.+)", stripped)
            if m_bracket_fail:
                name = m_bracket_fail.group(1).split(":")[0].split("-")[0].strip()
                if name:
                    failed.append(name)
                continue

            m_bracket_pass = _re.match(r"\[PASSED\]\s*(.+)", stripped)
            if m_bracket_pass:
                name = m_bracket_pass.group(1).split(":")[0].split("-")[0].strip()
                if name:
                    passed.append(name)
                continue

            # --- Format 3: "Failed tests: name1, name2" ---
            m_failed_tests = _re.match(r"Failed tests?:\s*(.+)", stripped, _re.IGNORECASE)
            if m_failed_tests:
                for name in _re.split(r"[,;]+", m_failed_tests.group(1)):
                    name = name.strip()
                    if name:
                        failed.append(name)
                continue

            # --- Format 4: "Passed tests: name1, name2" ---
            m_passed_tests = _re.match(r"Passed tests?:\s*(.+)", stripped, _re.IGNORECASE)
            if m_passed_tests:
                for name in _re.split(r"[,;]+", m_passed_tests.group(1)):
                    name = name.strip()
                    if name:
                        passed.append(name)
                continue

        # --- Aggregate counts ---
        # Try pytest summary line: "N failed, M passed" or "N passed, M failed"
        agg = _re.search(r"(\d+) failed.*?(\d+) passed", verifier_tail)
        agg_rev = _re.search(r"(\d+) passed.*?(\d+) failed", verifier_tail)
        # Try "N errors" for pytest ERROR results
        agg_errors = _re.search(r"(\d+) errors?", verifier_tail)
        # Try custom "Failed: N" / "Passed: N"
        custom_failed = _re.search(r"^Failed:\s*(\d+)", verifier_tail, _re.MULTILINE)
        custom_passed = _re.search(r"^Passed:\s*(\d+)", verifier_tail, _re.MULTILINE)
        # Try custom "Score: 0.XX" as a ratio hint
        custom_score = _re.search(r"^Score:\s*([\d.]+)", verifier_tail, _re.MULTILINE)

        n_passed = len(set(passed))
        n_failed = len(set(failed))

        # Prefer explicit aggregate counts over line-by-line parsing
        if agg:
            n_failed = max(n_failed, int(agg.group(1)))
            n_passed = max(n_passed, int(agg.group(2)))
        elif agg_rev:
            n_passed = max(n_passed, int(agg_rev.group(1)))
            n_failed = max(n_failed, int(agg_rev.group(2)))

        if agg_errors:
            n_failed = max(n_failed, int(agg_errors.group(1)))

        if custom_failed and n_failed == 0:
            n_failed = int(custom_failed.group(1))
        if custom_passed and n_passed == 0:
            n_passed = int(custom_passed.group(1))

        # Derive partial score ratio.
        # Prefer explicit "Score: X.XX" from custom verifiers (more accurate),
        # then fall back to passed/total ratio from test counts.
        total = n_passed + n_failed
        if custom_score:
            partial_score = min(1.0, max(0.0, float(custom_score.group(1))))
        elif total > 0:
            partial_score = n_passed / total
        else:
            partial_score = 0.0

        return {
            "passed_names": sorted(set(passed)),
            "failed_names": sorted(set(failed)),
            "n_passed": n_passed,
            "n_failed": n_failed,
            "partial_score": partial_score,
        }

    @staticmethod
    def build_evolver_feedback(
        task: Task,
        *,
        raw: dict[str, Any] | None,
        score: float,
        feedback_level: SkillBenchFeedbackLevel = "tests",
    ) -> str:
        """Build the sanitized feedback text shown to SkillBench evolvers."""
        raw = raw or {}
        failure_class = str(raw.get("failure_class", "unknown"))
        category = str(task.metadata.get("category", raw.get("category", "unknown")))
        difficulty = str(task.metadata.get("difficulty", raw.get("difficulty", "unknown")))
        reward_float = float(raw.get("reward_float", score) or 0.0)
        verifier_tail = str(raw.get("verifier_tail", ""))
        task_summary = task.input.split("\n")[0].strip()

        parts = [
            f"{failure_class} [{category}/{difficulty}]",
            f"Task: {task_summary}",
        ]

        if feedback_level in ("score", "tests", "masked", "full"):
            test_info = SkillBenchBenchmark._extract_test_results(verifier_tail)
            partial = test_info.get("partial_score", 0.0)
            n_passed = test_info["n_passed"]
            n_failed = test_info["n_failed"]
            total = n_passed + n_failed
            parts.append(f"Reward score: {reward_float:.3f}")
            parts.append(
                f"Tests: {n_failed} failed, {n_passed} passed"
                + (f" (partial score: {partial:.2f})" if total > 0 else "")
            )

            if feedback_level in ("tests", "masked", "full"):
                if test_info["failed_names"]:
                    parts.append("Failed tests:")
                    parts.extend(f"- {name}" for name in test_info["failed_names"])
                if test_info["passed_names"]:
                    parts.append("Passed tests:")
                    parts.extend(f"- {name}" for name in test_info["passed_names"])

        if feedback_level == "masked":
            parts.append("Verifier output (values masked):")
            parts.append(
                SkillBenchBenchmark._mask_assertion_values(verifier_tail)
            )

        if feedback_level == "full":
            parts.append("Verifier output:")
            parts.append(verifier_tail)

        return "\n".join(parts).strip()

    def _load_split(self, split: str) -> list[dict[str, Any]]:
        if not self._split_done:
            self._do_split()
        if split in self._cache:
            return self._cache[split]
        return self._cache.get("train", [])

    def _do_split(self) -> None:
        """Load all tasks from disk and partition into train + holdout."""
        from ...agents.skillbench.dataset import load_all_tasks

        selected_root = self.tasks_dir
        selected_path = Path(selected_root)
        if not selected_path.exists():
            logger.warning("SkillBench tasks path does not exist: %s", selected_root)
        all_tasks = load_all_tasks(self.tasks_dir)
        rows: list[dict[str, Any]] = []
        for t in all_tasks:
            if self.task_filter and self.task_filter not in t.name:
                continue
            if self.category_filter and t.metadata.get("category") != self.category_filter:
                continue
            if self.difficulty_filter and t.metadata.get("difficulty") != self.difficulty_filter:
                continue

            rows.append({
                "name": t.name,
                "prompt": t.prompt,
                "task_dir": t.metadata.get("task_dir", ""),
                "dockerfile_dir": t.dockerfile_dir,
                "test_sh_path": t.test_sh_path,
                "test_py_path": t.test_py_path,
                "category": t.metadata.get("category", "unknown"),
                "difficulty": t.metadata.get("difficulty", "unknown"),
                "agent_timeout_sec": t.metadata.get("agent_timeout_sec", 900),
                "verifier_timeout_sec": t.metadata.get("verifier_timeout_sec", 900),
                "build_timeout_sec": t.metadata.get("build_timeout_sec", 600),
                "cpus": t.metadata.get("cpus", 1),
                "memory": t.metadata.get("memory", "4g"),
                "backend": "native",
                "comparison_key": t.name,
            })

        if self.shuffle:
            random.Random(self.split_seed).shuffle(rows)

        n_holdout = max(1, int(len(rows) * self.holdout_ratio))
        self._cache["holdout"] = rows[:n_holdout]
        self._cache["train"] = rows[n_holdout:]
        if not self._cache["train"]:
            logger.warning(
                "SkillBench train split is empty (total tasks=%d, holdout=%d). "
                "All tasks were assigned to the holdout set.",
                len(rows), n_holdout,
            )
        self._cache["test"] = rows

        self._split_done = True
        logger.info(
            "Loaded %d SkillBench tasks from %s [use_skills=%s, mode=%s, native_profile=%s, score_mode=%s, split_seed=%s] (train=%d, holdout=%d)",
            len(rows),
            self.tasks_dir,
            self.use_skills,
            self.execution_mode,
            self.native_profile,
            self.score_mode,
            self.split_seed,
            len(self._cache["train"]),
            len(self._cache["holdout"]),
        )

    @staticmethod
    def _mask_assertion_values(text: str) -> str:
        """Mask numeric assertion values to prevent test leakage.

        Replaces patterns like:
          expected 593.345, got 2049.180 → expected <VALUE>, got <VALUE>
          assert 3457 == 2451 → assert <VALUE> == <VALUE>
          test_growth_values[B8-7444.4-Science] → test_growth_values[<PARAMS>]
        """
        import re as _re
        # Mask "expected X, got Y" patterns
        text = _re.sub(
            r"expected\s+[\d.eE+-]+", "expected <VALUE>", text, flags=_re.IGNORECASE
        )
        text = _re.sub(
            r"got\s+[\d.eE+-]+", "got <VALUE>", text, flags=_re.IGNORECASE
        )
        # Mask "assert X == Y" / "assert X <= Y" etc
        text = _re.sub(
            r"assert\s+[\d.eE+-]+\s*([=!<>]+)\s*[\d.eE+-]+",
            r"assert <VALUE> \1 <VALUE>", text, flags=_re.IGNORECASE
        )
        # Mask parametrized test values [B8-7444.4-Science Avg Budget]
        text = _re.sub(r"\[([^\]]*\d+[^\]]*)\]", "[<PARAMS>]", text)
        return text

    @staticmethod
    def _extract_diagnostic(verifier_tail: str, eval_output: str) -> str:
        """Extract the most informative error lines from verifier output.

        Returns a compact string (target <=240 chars) containing assertion
        messages, tracebacks, and FAILED lines.  Assertion values are MASKED
        to prevent test leakage (e.g. ``expected <VALUE>, got <VALUE>``).
        """
        raw = (verifier_tail or "").strip()
        if not raw:
            raw = str(eval_output or "").strip()
        if not raw:
            return "(no output)"

        # Filter noise (apt-get, blank lines, bash warnings)
        _noise = frozenset((
            "get:", "hit:", "fetched", "reading",
            "bash: cannot set terminal", "bash: no job control",
        ))
        lines: list[str] = []
        for ln in raw.splitlines():
            stripped = ln.strip()
            if not stripped:
                continue
            low = stripped.lower()
            if any(low.startswith(p) for p in _noise):
                continue
            lines.append(stripped)

        if not lines:
            return SkillBenchBenchmark._mask_assertion_values(raw[:240])

        # Prioritise: assertion/error lines first, then last N lines
        priority: list[str] = []
        rest: list[str] = []
        _diag_kw = ("assert", "error", "fail", "traceback", "expected", "!=")
        for ln in lines:
            low = ln.lower()
            if any(kw in low for kw in _diag_kw):
                priority.append(ln)
            else:
                rest.append(ln)

        selected = (priority + rest[-5:])[-8:]
        result = "\n".join(selected)[:240]
        return SkillBenchBenchmark._mask_assertion_values(result)

    @staticmethod
    def _extract_backend(trajectory: Trajectory) -> str:
        if not trajectory.steps:
            return "unknown"
        return str(trajectory.steps[-1].get("backend", "unknown"))

    @staticmethod
    def _extract_raw_job_path(trajectory: Trajectory) -> str | None:
        if not trajectory.steps:
            return None
        raw = trajectory.steps[-1].get("raw_job_path")
        if raw is None:
            return None
        return str(raw)
