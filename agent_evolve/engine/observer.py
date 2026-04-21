"""Observer -- collects (task, trajectory, feedback) triples into structured logs.

Adapted from agentic-evolution/modules/observer/persistent_observer.py.
Writes JSONL batch files for the evolver to analyze.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from ..types import Observation

logger = logging.getLogger(__name__)


class Observer:
    """Collects observations and persists them as JSONL in the evolution/ directory."""

    def __init__(self, evolution_dir: str | Path):
        self.evolution_dir = Path(evolution_dir)
        self.observations_dir = self.evolution_dir / "observations"
        self.observations_dir.mkdir(parents=True, exist_ok=True)
        self._batch_id = self._next_batch_id()

    def collect(self, observations: list[Observation]) -> Path:
        """Write a batch of observations to a JSONL file. Returns the file path."""
        batch_file = self.observations_dir / f"batch_{self._batch_id:04d}.jsonl"
        with open(batch_file, "w") as f:
            for obs in observations:
                # Extract claim-level feedback from raw data
                claims = []
                if obs.feedback.raw and "per_claim" in obs.feedback.raw:
                    for claim_data in obs.feedback.raw["per_claim"]:
                        claims.append({
                            "claim": claim_data.get("claim", ""),
                            "outcome": claim_data.get("outcome", "not_fulfilled"),
                            "pass": claim_data.get("score", 0.0) >= 1.0,
                            "score": claim_data.get("score", 0.0),
                            "justification": claim_data.get("justification", ""),
                        })

                # Save in nested format for stratified engine
                record = {
                    # Keep flat fields for backward compatibility
                    "task_id": obs.task.id,
                    "task_input": obs.task.input,
                    "agent_output": obs.trajectory.output,
                    "steps": obs.trajectory.steps,
                    "conversation": obs.trajectory.conversation,
                    "success": obs.feedback.success,
                    "score": obs.feedback.score,
                    "feedback_detail": obs.feedback.detail,
                    "timestamp": datetime.now().isoformat(),

                    # Add nested structure for new engines
                    "task": {
                        "id": obs.task.id,
                        "input": obs.task.input,
                        "metadata": obs.task.metadata,
                    },
                    "trajectory": {
                        "output": obs.trajectory.output,
                        "steps": obs.trajectory.steps,
                    },
                    "feedback": {
                        "success": obs.feedback.success,
                        "score": obs.feedback.score,
                        "detail": obs.feedback.detail,
                        "claims": claims,
                        "raw": obs.feedback.raw,
                    },
                    "steps": obs.trajectory.steps,  # Keep for backward compat
                }
                f.write(json.dumps(record, default=str) + "\n")

        logger.info("Wrote %d observations to %s", len(observations), batch_file.name)
        self._batch_id += 1
        return batch_file

    def append_step_metadata(self, metadata: dict[str, Any]) -> Path | None:
        """Persist engine step metadata to a batch-paired JSONL file.

        Engines (e.g. ``UnifiedEngine``) call this from their ``step()``
        immediately after computing ``StepResult.metadata``. The
        ``unified_*`` keys are written to a sibling file
        ``batch_<N>_step.jsonl`` alongside ``batch_<N>.jsonl`` so the
        observation records stay pure (only produced by
        ``Observer.collect()``) while still living in the same
        ``observations/`` directory — satisfying AC-7's "Observer.collect()
        persists these to the batch JSONL" contract at the batch-pair
        level.

        The file naming (``batch_*_step.jsonl``) is a unified_* artifact
        by convention; AC-8 differential tests exclude it from
        legacy-vs-unified parity comparisons.

        Returns the step-metadata file path, or ``None`` if no batch
        file exists yet (e.g., engine called this before
        ``Observer.collect()`` ran).
        """
        batch_files = sorted(self.observations_dir.glob("batch_[0-9]*.jsonl"))
        # Filter out pre-existing step files so we pair with the latest
        # observation batch.
        batch_files = [
            bf for bf in batch_files if not bf.stem.endswith("_step")
        ]
        if not batch_files:
            logger.debug(
                "append_step_metadata: no batch_*.jsonl yet; skipping"
            )
            return None
        target_stem = batch_files[-1].stem  # e.g. "batch_0001"
        target = self.observations_dir / f"{target_stem}_step.jsonl"
        record = {"_record_type": "step_metadata", **metadata}
        with open(target, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
        logger.debug("Wrote step metadata to %s", target.name)
        return target

    def get_recent_logs(self, n_batches: int = 3) -> list[dict[str, Any]]:
        """Read the most recent N batches of observations."""
        batch_files = sorted(self.observations_dir.glob("batch_*.jsonl"))
        recent_files = batch_files[-n_batches:]
        records: list[dict[str, Any]] = []
        for bf in recent_files:
            with open(bf) as f:
                for line in f:
                    if line.strip():
                        records.append(json.loads(line))
        return records

    def get_summary_stats(self) -> dict[str, Any]:
        """Compute aggregate stats across all observations."""
        all_records = self.get_recent_logs(n_batches=9999)
        if not all_records:
            return {"total": 0, "success_rate": 0.0, "avg_score": 0.0}
        successes = sum(1 for r in all_records if r.get("success"))
        scores = [r.get("score", 0.0) for r in all_records]
        return {
            "total": len(all_records),
            "success_rate": successes / len(all_records),
            "avg_score": sum(scores) / len(scores),
        }

    def _next_batch_id(self) -> int:
        existing = list(self.observations_dir.glob("batch_*.jsonl"))
        if not existing:
            return 1
        ids = []
        for f in existing:
            try:
                ids.append(int(f.stem.split("_")[1]))
            except (IndexError, ValueError):
                continue
        return max(ids, default=0) + 1
