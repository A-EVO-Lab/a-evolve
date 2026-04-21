"""ScoreCurveReader — expose the per-cycle score history.

Reads directly from :meth:`EvolutionHistory.get_score_curve`. The controller
uses this to compute stagnation / trend metrics without each operator
re-scanning history.
"""

from __future__ import annotations

from typing import Any

from ..registry import register_reader


@register_reader("ScoreCurveReader")
class ScoreCurveReader:
    """Output keys:

        "scores": list[float] rounded to 4 decimals
        "n_cycles": int
        "latest": float | None  (last element of scores, or None)
        "best": float | None    (max element of scores, or None)
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
        raw = list(history.get_score_curve()) if history is not None else []
        scores = [round(float(s), 4) for s in raw]
        if scores:
            latest: float | None = scores[-1]
            best: float | None = max(scores)
        else:
            latest = None
            best = None
        return {
            "scores": scores,
            "n_cycles": len(scores),
            "latest": latest,
            "best": best,
        }
