"""EvolutionLoop -- thin orchestrator that wires shared primitives to an engine.

The loop handles the expensive shared work that every engine needs:
  Solve -> Observe -> Snapshot -> engine.step() -> Snapshot -> Reload

The engine decides *how* to evolve; the loop decides *when* and provides
the infrastructure (versioning, observation logging, trial runner).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import EvolveConfig
from ..types import CycleRecord, EvolutionResult, Observation
from .history import EvolutionHistory
from .observer import Observer
from .trial import TrialRunner
from .versioning import VersionControl

if TYPE_CHECKING:
    from ..benchmarks.base import BenchmarkAdapter
    from ..protocol.base_agent import BaseAgent
    from .base import EvolutionEngine

logger = logging.getLogger(__name__)


def _is_score_converged(
    scores: list[float], window: int = 3, epsilon: float = 0.01
) -> bool:
    """Generic convergence: score hasn't improved by more than *epsilon* in *window* cycles."""
    if len(scores) < window + 1:
        return False
    recent = scores[-window:]
    baseline = scores[-(window + 1)]
    return all(abs(s - baseline) < epsilon for s in recent)


class EvolutionLoop:
    """Orchestrates the full evolution loop with a pluggable engine."""

    def __init__(
        self,
        agent: BaseAgent,
        benchmark: BenchmarkAdapter,
        engine: EvolutionEngine,
        config: EvolveConfig | None = None,
    ):
        self.agent = agent
        self.benchmark = benchmark
        self.engine = engine
        self.config = config or EvolveConfig()

        workspace_root = self.agent.workspace.root
        evolution_dir = workspace_root / "evolution"
        evolution_dir.mkdir(parents=True, exist_ok=True)

        self.observer = Observer(evolution_dir)
        self.versioning = VersionControl(workspace_root)
        self.history = EvolutionHistory(self.observer, self.versioning)
        self.trial = TrialRunner(self.agent, self.benchmark)

    def run(self, cycles: int | None = None) -> EvolutionResult:
        """Run the evolution loop for the specified number of cycles."""
        max_cycles = cycles or self.config.max_cycles
        evolution_dir = self.agent.workspace.root / "evolution"

        self.versioning.init()

        score_history: list[float] = []

        for cycle in range(max_cycles):
            cycle_num = cycle + 1
            logger.info("=== Evolution Cycle %d/%d ===", cycle_num, max_cycles)

            # 1. SOLVE + 2. OBSERVE
            if self.engine.manages_own_evaluation:
                observations: list[Observation] = []
                self.agent.export_to_fs()
                batch_path = self.observer.collect(observations)
                cycle_score = 0.0
            else:
                tasks = self.benchmark.get_tasks(split="train", limit=self.config.batch_size)
                observations: list[Observation] = []

                for task in tasks:
                    try:
                        trajectory = self.agent.solve(task)
                        feedback = self.benchmark.evaluate(task, trajectory)
                        observations.append(Observation(task=task, trajectory=trajectory, feedback=feedback))
                    except Exception as e:
                        logger.error("Error solving task %s: %s", task.id, e)

                self.agent.export_to_fs()
                batch_path = self.observer.collect(observations)

                cycle_score = (
                    sum(o.feedback.score for o in observations) / len(observations)
                    if observations
                    else 0.0
                )
            score_history.append(cycle_score)
            logger.info("Cycle %d score: %.3f", cycle_num, cycle_score)

            # 3. PRE-EVOLVE SNAPSHOT
            self.versioning.commit(
                message=f"pre-evo-{cycle_num}: score={cycle_score:.3f}",
                tag=f"pre-evo-{cycle_num}",
            )

            # 4. ENGINE STEP
            step_result = self.engine.step(
                workspace=self.agent.workspace,
                observations=observations,
                history=self.history,
                trial=self.trial,
            )

            # 5. POST-EVOLVE SNAPSHOT
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

            # 6. RECORD CYCLE
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

            # 7. RELOAD
            self.agent.reload_from_fs()
            self.engine.on_cycle_end(accepted=step_result.mutated, score=cycle_score)

            # 7b. STOP CHECK
            if step_result.stop:
                logger.info("Engine requested early stop after cycle %d.", cycle_num)
                self._append_history(evolution_dir, cycle_num, cycle_score, step_result.mutated)
                self._write_metrics(evolution_dir, score_history)
                return EvolutionResult(
                    cycles_completed=cycle_num,
                    final_score=cycle_score,
                    score_history=score_history,
                    converged=True,
                )

            # 8. LOGGING
            self._append_history(evolution_dir, cycle_num, cycle_score, step_result.mutated)
            self._write_metrics(evolution_dir, score_history)

            # 9. CONVERGENCE CHECK
            if _is_score_converged(score_history, window=self.config.egl_window):
                logger.info("Score converged after %d cycles.", cycle_num)
                return EvolutionResult(
                    cycles_completed=cycle_num,
                    final_score=cycle_score,
                    score_history=score_history,
                    converged=True,
                )

        return EvolutionResult(
            cycles_completed=max_cycles,
            final_score=score_history[-1] if score_history else 0.0,
            score_history=score_history,
            converged=False,
        )

    # ── Internal helpers ──────────────────────────────────────

    def _append_history(
        self, evolution_dir: Path, cycle: int, score: float, mutated: bool
    ) -> None:
        history_file = evolution_dir / "history.jsonl"
        entry = {
            "cycle": cycle,
            "score": score,
            "mutated": mutated,
            "timestamp": datetime.now().isoformat(),
        }
        with open(history_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def _write_metrics(self, evolution_dir: Path, scores: list[float]) -> None:
        metrics_file = evolution_dir / "metrics.json"
        metrics = {
            "cycles_completed": len(scores),
            "latest_score": scores[-1] if scores else 0.0,
            "best_score": max(scores) if scores else 0.0,
            "avg_score": sum(scores) / len(scores) if scores else 0.0,
        }
        metrics_file.write_text(json.dumps(metrics, indent=2))
