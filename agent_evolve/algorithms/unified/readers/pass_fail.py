"""PassFailReader — extract basic pass/fail statistics from observations.

Produces a simple dict with per-observation success flags, scores, and a
batch pass_rate. This is the minimum evidence every recipe needs.
"""

from __future__ import annotations

from typing import Any

from ..registry import register_reader


@register_reader("PassFailReader")
class PassFailReader:
    """Reads ``obs.feedback.success`` / ``obs.feedback.score`` from observations.

    Output dict keys (canonicalized: sorted lists, fixed float precision):

        "per_task": list of {"task_id", "success", "score"} sorted by task_id
        "n_tasks": int
        "n_pass": int
        "n_fail": int
        "pass_rate": float with 4-decimal precision, or ``None`` if n_tasks==0
        "avg_score": float with 4-decimal precision, or ``None`` if n_tasks==0
    """

    def read(
        self,
        observations: list,
        workspace: Any,
        history: Any,
        config: Any,
        context: Any,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        per_task = []
        for obs in observations:
            task_id = getattr(obs.task, "id", "")
            fb = obs.feedback
            per_task.append(
                {
                    "task_id": task_id,
                    "success": bool(getattr(fb, "success", False)),
                    "score": round(float(getattr(fb, "score", 0.0)), 4),
                }
            )
        per_task.sort(key=lambda e: e["task_id"])
        n = len(per_task)
        n_pass = sum(1 for e in per_task if e["success"])
        n_fail = n - n_pass
        if n > 0:
            pass_rate = round(n_pass / n, 4)
            avg_score = round(sum(e["score"] for e in per_task) / n, 4)
        else:
            pass_rate = None
            avg_score = None
        return {
            "per_task": per_task,
            "n_tasks": n,
            "n_pass": n_pass,
            "n_fail": n_fail,
            "pass_rate": pass_rate,
            "avg_score": avg_score,
        }
