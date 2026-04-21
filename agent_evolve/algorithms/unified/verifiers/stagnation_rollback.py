"""StagnationRollback — roll back to best-known state after stagnation.

Reference: ``agent_evolve/algorithms/adaptive_evolve/engine.py`` lines 525-566
(``_check_stagnation_gate``). Independent reimplementation under
``unified/``.

Not part of the Phase 1 loop-path recipes (matches legacy ``step()``
which omits this gate; it only fires in the standalone ``evolve()`` API).
Registered for future recipes and direct invocation via a recipe that
opts in.

State keys (persisted across cycles):
    ``state["_best_pass_rate"]`` — float (AC-6 plan name)
    ``state["_best_tag"]`` — str   ← e.g., "pre-evo-3"
    ``state["_cycles_without_improvement"]`` — int
    ``state["improvement_threshold"]`` — float (default 0.02)
    ``state["stagnation_window"]`` — int (default 5)
"""

from __future__ import annotations

import logging
from typing import Any

from ..registry import register_verifier
from ..types import Verdict

logger = logging.getLogger(__name__)


@register_verifier("StagnationRollback")
class StagnationRollback:
    def check(
        self,
        workspace: Any,
        context: Any,
        reports: list,
        trial: Any,
        history: Any,
        state: dict[str, Any],
    ) -> Verdict:
        threshold = float(state.get("improvement_threshold", 0.02))
        window = int(state.get("stagnation_window", 5))

        pass_fail = (getattr(context, "entries", {}) or {}).get("PassFailReader", {})
        current = pass_fail.get("pass_rate")
        if current is None:
            return Verdict(accept=True, rollback=False, reason="no pass_rate to gate on")

        best = float(state.get("_best_pass_rate", 0.0))
        improvement = current - best
        if improvement >= threshold:
            state["_best_pass_rate"] = current
            state["_cycles_without_improvement"] = 0
            # Best tag defaults to the current pre-evo tag if one is
            # present in history; otherwise leave unchanged.
            state["_best_tag"] = state.get("_best_tag", "")
            return Verdict(accept=True, rollback=False, reason=f"improved +{improvement:.3f}")

        waits = int(state.get("_cycles_without_improvement", 0)) + 1
        state["_cycles_without_improvement"] = waits
        if waits >= window:
            degradation = best - current
            if degradation > 0.05 or best < 0.90:
                best_tag = state.get("_best_tag") or ""
                logger.warning(
                    "Stagnation: %d cycles w/o improvement; best=%.3f current=%.3f; rollback → %s",
                    waits, best, current, best_tag,
                )
                state["_cycles_without_improvement"] = 0
                return Verdict(
                    accept=False,
                    rollback=True,
                    reason=(
                        f"stagnation {waits} cycles; best={best:.3f} "
                        f"current={current:.3f}"
                    ),
                )
        return Verdict(
            accept=True,
            rollback=False,
            reason=f"no improvement but waits={waits}/{window}",
        )
