"""ClaimReader — extract per-claim feedback for MCP-Atlas-style benchmarks.

Reference: ``agent_evolve/benchmarks/mcp_atlas/mcp_atlas.py`` lines 209-226,
which populates ``feedback.raw["per_claim"]`` with entries of the form
``{"claim", "outcome", "score", "justification"}``.

Output is canonicalized (sorted by source_task_id then claim text) so that
downstream LLM prompts are byte-stable.
"""

from __future__ import annotations

from typing import Any

from ..registry import register_reader


@register_reader("ClaimReader")
class ClaimReader:
    """Output keys:

        "per_task": list of {"task_id", "claims": [...]}
        "all_claims": flat list of {"task_id", "claim", "outcome", "score",
                                    "justification"}
        "n_claims": int
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
        per_task: list[dict[str, Any]] = []
        all_claims: list[dict[str, Any]] = []
        for obs in observations:
            task_id = getattr(obs.task, "id", "")
            raw = getattr(obs.feedback, "raw", {}) or {}
            claims_in = list(raw.get("per_claim", []) or [])
            task_claims: list[dict[str, Any]] = []
            for c in claims_in:
                entry = {
                    "task_id": task_id,
                    "claim": str(c.get("claim", "")),
                    "outcome": str(c.get("outcome", "not_fulfilled")),
                    "score": round(float(c.get("score", 0.0)), 4),
                    "justification": str(c.get("justification", "")),
                }
                task_claims.append(entry)
                all_claims.append(entry)
            task_claims.sort(key=lambda e: e["claim"])
            per_task.append({"task_id": task_id, "claims": task_claims})
        per_task.sort(key=lambda d: d["task_id"])
        all_claims.sort(key=lambda e: (e["task_id"], e["claim"]))
        return {
            "per_task": per_task,
            "all_claims": all_claims,
            "n_claims": len(all_claims),
        }
