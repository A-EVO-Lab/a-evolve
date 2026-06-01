"""SkillBench-specific evolution loop.

Keeps dual-comparison and comparison artifact logic out of the shared
`agent_evolve.engine.loop` implementation.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime
from pathlib import Path

from ...config import EvolveConfig
from ...engine.loop import EvolutionLoop
from ...types import CycleRecord, EvolutionResult, Feedback, Observation, Task

logger = logging.getLogger(__name__)


class SkillBenchEvolutionLoop(EvolutionLoop):
    """Evolution loop with SkillBench-only dual comparison support."""

    def __init__(self, agent, benchmark, engine, config: EvolveConfig | None = None):
        super().__init__(agent, benchmark, engine, config)

    def run(self, cycles: int | None = None) -> EvolutionResult:
        """Run the evolution loop and emit dual-mode comparison artifacts."""
        max_cycles = cycles or self.config.max_cycles
        evolution_dir = self.agent.workspace.root / "evolution"
        comparisons_dir = evolution_dir / "comparisons"
        comparisons_dir.mkdir(parents=True, exist_ok=True)

        self.versioning.init()

        score_history: list[float] = []
        comparison_history: list[dict] = []

        for cycle in range(max_cycles):
            cycle_num = cycle + 1
            logger.info("=== Evolution Cycle %d/%d ===", cycle_num, max_cycles)

            tasks = self.benchmark.get_tasks(split="train", limit=self.config.batch_size)
            observations: list[Observation] = []
            cycle_comparisons: list[dict] = []

            for task in tasks:
                try:
                    trajectory = self.agent.solve(task)
                    feedback = self.benchmark.evaluate(task, trajectory)
                    observations.append(
                        Observation(task=task, trajectory=trajectory, feedback=feedback)
                    )
                    dual_row = self._run_dual_comparison_if_enabled(task, feedback)
                    if dual_row:
                        cycle_comparisons.append(dual_row)
                except Exception as exc:
                    logger.error("Error solving task %s: %s", task.id, exc)

            self.agent.export_to_fs()
            batch_path = self.observer.collect(observations)

            cycle_score = (
                sum(o.feedback.score for o in observations) / len(observations)
                if observations
                else 0.0
            )
            score_history.append(cycle_score)
            logger.info("Cycle %d score: %.3f", cycle_num, cycle_score)

            if cycle_comparisons:
                comparison_summary = self._write_comparison_artifact(
                    comparisons_dir=comparisons_dir,
                    cycle=cycle_num,
                    rows=cycle_comparisons,
                )
                comparison_history.append(comparison_summary)
                logger.info(
                    "Cycle %d comparison: native_pass=%.3f harbor_pass=%.3f",
                    cycle_num,
                    comparison_summary["native"]["pass_rate"],
                    comparison_summary["harbor"]["pass_rate"],
                )

            self.versioning.commit(
                message=f"pre-evo-{cycle_num}: score={cycle_score:.3f}",
                tag=f"pre-evo-{cycle_num}",
            )

            step_result = self.engine.step(
                workspace=self.agent.workspace,
                observations=observations,
                history=self.history,
                trial=self.trial,
            )

            if step_result.mutated:
                self.versioning.commit(
                    message=f"evo-{cycle_num}: {step_result.summary}",
                    tag=f"evo-{cycle_num}",
                )
            else:
                self.versioning.commit(
                    message=f"evo-{cycle_num}: no mutation",
                    tag=f"evo-{cycle_num}",
                )

            record = CycleRecord(
                cycle=cycle_num,
                score=cycle_score,
                mutated=step_result.mutated,
                engine_name=self.engine.__class__.__name__,
                summary=step_result.summary,
                observation_batch=batch_path.name,
                metadata=step_result.metadata,
            )
            self.history.record_cycle(record)

            self.agent.reload_from_fs()
            self.engine.on_cycle_end(accepted=step_result.mutated, score=cycle_score)

            self._append_history(evolution_dir, cycle_num, cycle_score, step_result.mutated)
            self._write_metrics(evolution_dir, score_history)

            if self._is_converged(score_history):
                logger.info("Score converged after %d cycles.", cycle_num)
                return EvolutionResult(
                    cycles_completed=cycle_num,
                    final_score=cycle_score,
                    score_history=score_history,
                    converged=True,
                    details={"comparison_history": comparison_history},
                )

        return EvolutionResult(
            cycles_completed=max_cycles,
            final_score=score_history[-1] if score_history else 0.0,
            score_history=score_history,
            converged=False,
            details={"comparison_history": comparison_history},
        )

    def _is_converged(self, scores: list[float]) -> bool:
        window = self.config.egl_window
        epsilon = 0.01
        if len(scores) < window + 1:
            return False
        recent = scores[-window:]
        baseline = scores[-(window + 1)]
        return all(abs(score - baseline) < epsilon for score in recent)

    def _run_dual_comparison_if_enabled(
        self,
        task: Task,
        native_feedback: Feedback,
    ) -> dict | None:
        execution_mode = getattr(self.benchmark, "execution_mode", "native")
        if execution_mode != "dual":
            return None

        solve_with_backend = getattr(self.agent, "solve_with_backend", None)
        if not callable(solve_with_backend):
            logger.warning(
                "Benchmark is dual mode but agent does not support solve_with_backend(); skipping comparisons."
            )
            return None

        try:
            harbor_trajectory = solve_with_backend(task, "harbor")
            harbor_feedback = self.benchmark.evaluate(task, harbor_trajectory)
            return {
                "task_id": task.id,
                "comparison_key": task.metadata.get("comparison_key", task.id),
                "native": {
                    "success": native_feedback.success,
                    "score": native_feedback.score,
                    "category": task.metadata.get("category", "unknown"),
                    "detail": native_feedback.detail[:500],
                },
                "harbor": {
                    "success": harbor_feedback.success,
                    "score": harbor_feedback.score,
                    "category": task.metadata.get("category", "unknown"),
                    "detail": harbor_feedback.detail[:500],
                    "raw_job_path": harbor_feedback.raw.get("raw_job_path"),
                },
            }
        except Exception as exc:
            logger.error("Dual comparison failed for task %s: %s", task.id, exc)
            return {
                "task_id": task.id,
                "comparison_key": task.metadata.get("comparison_key", task.id),
                "native": {
                    "success": native_feedback.success,
                    "score": native_feedback.score,
                    "category": task.metadata.get("category", "unknown"),
                    "detail": native_feedback.detail[:500],
                },
                "harbor": {
                    "success": False,
                    "score": 0.0,
                    "category": task.metadata.get("category", "unknown"),
                    "detail": f"comparison_error: {exc}",
                    "raw_job_path": None,
                },
            }

    @staticmethod
    def _write_comparison_artifact(
        comparisons_dir: Path,
        cycle: int,
        rows: list[dict],
    ) -> dict:
        native_scores = [row["native"]["score"] for row in rows]
        harbor_scores = [row["harbor"]["score"] for row in rows]
        native_successes = [1 if row["native"]["success"] else 0 for row in rows]
        harbor_successes = [1 if row["harbor"]["success"] else 0 for row in rows]

        native_failure_categories = Counter(
            row["native"]["category"] for row in rows if not row["native"]["success"]
        )
        harbor_failure_categories = Counter(
            row["harbor"]["category"] for row in rows if not row["harbor"]["success"]
        )

        per_task_diff = []
        for row in rows:
            per_task_diff.append(
                {
                    "task_id": row["task_id"],
                    "comparison_key": row["comparison_key"],
                    "native_score": row["native"]["score"],
                    "harbor_score": row["harbor"]["score"],
                    "score_diff": row["native"]["score"] - row["harbor"]["score"],
                    "native_success": row["native"]["success"],
                    "harbor_success": row["harbor"]["success"],
                    "native_category": row["native"]["category"],
                    "harbor_category": row["harbor"]["category"],
                    "harbor_raw_job_path": row["harbor"].get("raw_job_path"),
                }
            )

        summary = {
            "cycle": cycle,
            "n_tasks": len(rows),
            "native": {
                "pass_rate": sum(native_successes) / len(rows) if rows else 0.0,
                "avg_score": sum(native_scores) / len(rows) if rows else 0.0,
                "failure_category_distribution": dict(native_failure_categories),
            },
            "harbor": {
                "pass_rate": sum(harbor_successes) / len(rows) if rows else 0.0,
                "avg_score": sum(harbor_scores) / len(rows) if rows else 0.0,
                "failure_category_distribution": dict(harbor_failure_categories),
            },
            "per_task_diff": per_task_diff,
            "created_at": datetime.now().isoformat(),
        }

        out_path = comparisons_dir / f"cycle-{cycle:04d}.json"
        out_path.write_text(json.dumps(summary, indent=2))
        return summary
