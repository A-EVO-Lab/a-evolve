"""Rule-based controller that turns a (regime, capability, config) triple
into an executable recipe (``Plan``).

The rule table has exactly five mutually-exclusive branches plus a default
fallback, matching the plan's AC-4 layout:

1. per-claim feedback → MCP-Atlas-style rich recipe
2. solver proposals → guided_synth-style curator recipe
3. drafts → adaptive_skill-style recipe with draft reader
4. trajectory-only (masked feedback, no drafts) → judge-backed recipe
5. default → minimal ``LLMBashEvolve`` recipe (SkillBench fits here)

All recipes use atoms registered in the three module-level registries;
the controller never emits a legacy-engine name.
"""

from __future__ import annotations

from typing import Any

from .types import FeedbackCapability, Plan, RegimeTag


class RuleBasedController:
    """Deterministic rule-based recipe dispatcher."""

    def plan(
        self,
        regime: RegimeTag,
        capability: FeedbackCapability,
        config: Any,
    ) -> Plan:
        if regime.has_per_claim:
            return Plan(
                readers=(
                    "PassFailReader",
                    "ClaimReader",
                    "PatternDetector",
                    "ClaimTypeAnalyzer",
                    "ScoreCurveReader",
                ),
                operators=(
                    "FixHallucinations",
                    "AutoSeedSkills",
                    "LLMBashEvolve",
                    "SanityCheck",
                ),
                verifier="NoVerify",
                artifact_scope={"prompts": "rw", "skills": "rw", "memory": "append"},
                reason_trace=("matched: per_claim regime",),
            )

        if regime.has_solver_proposal and capability.solver_may_propose:
            return Plan(
                readers=("PassFailReader", "ProposalReader"),
                operators=("WriteEpisodicMemory", "SkillCurator"),
                verifier="NoVerify",
                artifact_scope={"skills": "rw", "memory": "append"},
                reason_trace=("matched: solver_proposal regime",),
            )

        if regime.has_drafts:
            return Plan(
                readers=("PassFailReader", "DraftReader", "TrajectoryCompressor"),
                operators=("LLMBashEvolve",),
                verifier="NoVerify",
                artifact_scope={"skills": "rw", "prompts": "rw"},
                reason_trace=("matched: drafts regime",),
            )

        trajectory_only = bool(getattr(config, "trajectory_only", False))
        if trajectory_only or not regime.has_binary_verifier:
            return Plan(
                readers=("TrajectoryCompressor", "LLMJudgeReader"),
                operators=("LLMBashEvolve",),
                verifier="NoVerify",
                artifact_scope={"skills": "rw"},
                reason_trace=("matched: trajectory_only regime",),
            )

        return Plan(
            readers=("PassFailReader", "TrajectoryCompressor"),
            operators=("LLMBashEvolve",),
            verifier="NoVerify",
            artifact_scope={"skills": "rw"},
            reason_trace=("default: minimal llm_bash recipe",),
        )


__all__ = ["RuleBasedController"]
