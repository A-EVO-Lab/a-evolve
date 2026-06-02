"""ClaimTypeAnalyzer — classify claims into semantic types and rank weakness.

Reference: ``agent_evolve/algorithms/adaptive_evolve/analyzer.py`` lines 191-255
(``ClaimAnalyzer``). Independent reimplementation under ``unified/``.

Consumes the output of ``ClaimReader`` (placed into the EvidenceContext
under the key ``"ClaimReader"``). Produces per-type pass-rate statistics
and a sorted list of the weakest types, used by ``AutoSeedSkills`` to
decide which targeted skills to seed.
"""

from __future__ import annotations

from typing import Any

from ..registry import register_reader


_CLAIM_TYPES: dict[str, tuple[str, ...]] = {
    # Information retrieval
    "provide_fact": ("provide", "what is", "get", "return", "show"),
    "calculate": ("difference", "sum", "calculate", "how many", "count"),
    "compare": ("compare", "difference between", "versus", " vs"),
    "aggregate": ("total", "all", "list all", "every"),
    # Entity operations
    "identify_entity": ("identify", "find", "which", "who is"),
    "entity_property": (
        "status",
        "date",
        "name",
        "owner",
        "created",
        "updated",
    ),
    # Multi-step
    "chain": ("then", "after", "using", "next"),
    "conditional": ("if", "when", "where", "in case"),
}


def _classify(text: str) -> str:
    low = text.lower()
    for ct, keywords in _CLAIM_TYPES.items():
        if any(kw in low for kw in keywords):
            return ct
    return "other"


@register_reader("ClaimTypeAnalyzer")
class ClaimTypeAnalyzer:
    """Output keys:

        "by_type": dict[str, {"total", "fulfilled", "partial", "failed",
                              "pass_rate", "examples"}] sorted by type name
        "weakest": list of (type, pass_rate) tuples sorted by pass_rate asc
                   — top 3 returned at most
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
        claim_reader_out = context.entries.get("ClaimReader", {})
        all_claims = list(claim_reader_out.get("all_claims", []))
        stats: dict[str, dict[str, Any]] = {}
        for c in all_claims:
            ctype = _classify(c.get("claim", ""))
            s = stats.setdefault(
                ctype,
                {
                    "total": 0,
                    "fulfilled": 0,
                    "partial": 0,
                    "failed": 0,
                    "examples": [],
                },
            )
            s["total"] += 1
            sc = float(c.get("score", 0.0))
            if sc >= 1.0:
                s["fulfilled"] += 1
            elif sc >= 0.5:
                s["partial"] += 1
            else:
                s["failed"] += 1
                if len(s["examples"]) < 3:
                    s["examples"].append(
                        {
                            "task_id": c.get("task_id", ""),
                            "claim": c.get("claim", ""),
                            "outcome": c.get("outcome", "not_fulfilled"),
                            "justification": c.get("justification", ""),
                        }
                    )
        for s in stats.values():
            s["pass_rate"] = (
                round(
                    (s["fulfilled"] + 0.5 * s["partial"]) / s["total"], 4
                )
                if s["total"] > 0
                else 0.0
            )
        by_type = {k: stats[k] for k in sorted(stats)}
        weakest = sorted(
            ((t, s["pass_rate"]) for t, s in stats.items()),
            key=lambda e: (e[1], e[0]),
        )[:3]
        return {"by_type": by_type, "weakest": weakest}
