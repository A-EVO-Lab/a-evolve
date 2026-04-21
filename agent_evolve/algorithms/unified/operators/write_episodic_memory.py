"""WriteEpisodicMemory — append minimal per-task memory entries.

Reference: ``agent_evolve/algorithms/guided_synth/engine.py`` lines 240-276
(``_write_minimal_memory``). Independent reimplementation under ``unified/``.

Per-cycle state:
    ``state["_cycle_count"]`` — monotonically-increasing cycle counter
    used in the stored entries. Matches legacy ``self._cycle_count``
    (AC-6 plan text uses the underscore-prefixed name).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ..registry import register_operator
from ..types import MutationReport

logger = logging.getLogger(__name__)


@register_operator("WriteEpisodicMemory")
class WriteEpisodicMemory:
    """Writes one episodic-memory entry per observation.

    The entry is a compact dict (task_id, cycle, score, files_edited,
    approach_summary) appended to ``memory/episodic.jsonl``.
    """

    WRITES: frozenset[str] = frozenset({"memory"})

    def apply(
        self,
        workspace: Any,
        context: Any,
        scope: dict[str, Any],
        state: dict[str, Any],
    ) -> MutationReport:
        if scope.get("memory") not in ("rw", "append"):
            return MutationReport(operator_name="WriteEpisodicMemory", count=0)

        cycle = int(state.get("_cycle_count", 0)) + 1
        state["_cycle_count"] = cycle

        # We need the observations list; it is stashed in the context by the
        # engine under a well-known key. Fall back to pass-fail reader if the
        # engine did not publish it.
        observations = context.entries.get("__observations__", [])
        per_task = (
            context.entries.get("PassFailReader", {}) or {}
        ).get("per_task", [])
        score_by_task = {p["task_id"]: p["score"] for p in per_task}

        written = 0
        for obs in observations:
            task_id = getattr(obs.task, "id", "")
            agent_output = getattr(obs.trajectory, "output", "") or ""
            score = float(score_by_task.get(task_id, getattr(obs.feedback, "score", 0.0)))

            files_in_patch: list[str] = []
            if agent_output.strip():
                for m in re.finditer(
                    r"^(?:\+\+\+)\s+[ab]/(.+)$", agent_output, re.MULTILINE
                ):
                    if m.group(1) != "/dev/null":
                        files_in_patch.append(m.group(1))

            summary = (
                f"Cycle {cycle}: "
                f"Edited {len(files_in_patch)} file(s): "
                f"{', '.join(files_in_patch[:5])}. Score: {score}."
            )
            entry = {
                "task_id": task_id,
                "cycle": cycle,
                "score": round(score, 4),
                "files_edited": files_in_patch,
                "approach_summary": summary,
            }
            workspace.add_memory(entry, category="episodic")
            written += 1
            logger.info(
                "WriteEpisodicMemory: task=%s cycle=%d score=%.1f files=%s",
                task_id,
                cycle,
                score,
                files_in_patch[:3],
            )

        # Match legacy guided_synth semantics: memory appends do NOT count
        # toward ``StepResult.mutated``. Legacy defines mutated only via the
        # curated-skill diff (``len(applied_names) > 0``), treating episodic
        # memory writes as transient bookkeeping. We keep the actual row
        # count in ``details`` for observability, but set ``count=0`` so the
        # engine-level ``any(r.count > 0 for r in reports)`` rollup agrees
        # with the legacy ``mutated`` boolean.
        return MutationReport(
            operator_name="WriteEpisodicMemory",
            count=0,
            details={"cycle": cycle, "tasks_written": written},
        )
