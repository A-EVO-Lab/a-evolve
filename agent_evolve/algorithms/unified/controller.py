"""Rule-based controller that turns a (regime, capability, config) triple
into an executable recipe (``Plan``).

The rule table has exactly five mutually-exclusive branches plus a default
fallback, matching the plan's AC-4 layout:

1. per-claim feedback → MCP-Atlas-style rich recipe
2. solver proposals → guided_synth-style curator recipe
3. terminal legacy profile → TB-tuned trajectory recipe
4. drafts → adaptive_skill-style recipe with draft reader
5. trajectory-only (masked feedback, no drafts) → judge-backed recipe
6. default → minimal ``LLMBashEvolve`` recipe (SkillBench fits here)

All recipes use atoms registered in the three module-level registries;
the controller never emits a legacy-engine name.
"""

from __future__ import annotations

from typing import Any

from .types import FeedbackCapability, Plan, RegimeTag


def _extra(config: Any) -> dict[str, Any]:
    value = getattr(config, "extra", {}) or {}
    return value if isinstance(value, dict) else {}


def _flag(config: Any, name: str, default: bool = True) -> bool:
    return bool(getattr(config, name, default))


def _apply_scope(config: Any, scope: dict[str, str]) -> dict[str, str]:
    """Apply EvolveConfig mutation flags to a recipe's artifact scope."""
    out = dict(scope)
    if not _flag(config, "evolve_prompts", True):
        out["prompts"] = "ro"
    if not _flag(config, "evolve_skills", True):
        out["skills"] = "ro"
    if not _flag(config, "evolve_memory", True):
        out["memory"] = "ro"
    if not _flag(config, "evolve_tools", False):
        out["tools"] = "ro"
    return out


def _scope_grants(scope: dict[str, str], artifact: str) -> bool:
    return scope.get(artifact) in ("rw", "append")


_OP_WRITES: dict[str, tuple[str, ...]] = {
    "FixHallucinations": ("skills", "memory"),
    "AutoSeedSkills": ("skills",),
    "LLMBashEvolve": ("prompts", "skills", "memory", "tools"),
    "SanityCheck": ("prompts", "skills"),
    "WriteEpisodicMemory": ("memory",),
    "SkillCurator": ("skills",),
    "TerminalSkillEvolve": ("skills",),
}


def _filter_operators(operators: tuple[str, ...], scope: dict[str, str]) -> tuple[str, ...]:
    filtered: list[str] = []
    for name in operators:
        writes = _OP_WRITES.get(name)
        if writes and not any(_scope_grants(scope, w) for w in writes):
            continue
        filtered.append(name)
    return tuple(filtered)


def _plan(
    *,
    readers: tuple[str, ...],
    operators: tuple[str, ...],
    verifier: str,
    artifact_scope: dict[str, str],
    reason_trace: tuple[str, ...],
    config: Any,
) -> Plan:
    scoped = _apply_scope(config, artifact_scope)
    return Plan(
        readers=readers,
        operators=_filter_operators(operators, scoped),
        verifier=verifier,
        artifact_scope=scoped,
        reason_trace=reason_trace,
    )


class RuleBasedController:
    """Deterministic rule-based recipe dispatcher."""

    def plan(
        self,
        regime: RegimeTag,
        capability: FeedbackCapability,
        config: Any,
    ) -> Plan:
        extra = _extra(config)
        legacy_profile = str(extra.get("legacy_profile", "")).lower()

        if (
            legacy_profile == "swe"
            and regime.has_solver_proposal
            and capability.solver_may_propose
        ):
            return _plan(
                readers=("ProposalReader",),
                operators=("SkillCurator",),
                verifier="NoVerify",
                artifact_scope={
                    "skills": "rw",
                    "memory": "ro",
                    "prompts": "ro",
                    "tools": "ro",
                },
                reason_trace=("matched: swe legacy solver proposal curation",),
                config=config,
            )

        if legacy_profile == "swe" and not regime.has_solver_proposal:
            return _plan(
                readers=("PassFailReader", "TrajectoryCompressor"),
                operators=(),
                verifier="NoVerify",
                artifact_scope={
                    "skills": "ro",
                    "memory": "ro",
                    "prompts": "ro",
                    "tools": "ro",
                },
                reason_trace=("matched: swe legacy no solver proposals",),
                config=config,
            )

        if legacy_profile in {"tb", "terminal", "terminal-bench"}:
            return _plan(
                readers=("TerminalTrajectoryReader", "LLMJudgeReader"),
                operators=("TerminalSkillEvolve",),
                verifier="NoVerify",
                artifact_scope={"skills": "rw", "prompts": "ro", "memory": "ro", "tools": "ro"},
                reason_trace=("matched: terminal legacy profile",),
                config=config,
            )

        if regime.has_per_claim:
            return _plan(
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
                config=config,
            )

        if regime.has_solver_proposal and capability.solver_may_propose:
            return _plan(
                readers=("PassFailReader", "ProposalReader"),
                operators=("WriteEpisodicMemory", "SkillCurator"),
                verifier="NoVerify",
                artifact_scope={"skills": "rw", "memory": "append"},
                reason_trace=("matched: solver_proposal regime",),
                config=config,
            )

        if regime.has_drafts:
            return _plan(
                readers=("PassFailReader", "DraftReader", "TrajectoryCompressor"),
                operators=("LLMBashEvolve",),
                verifier="NoVerify",
                artifact_scope={"skills": "rw", "prompts": "rw"},
                reason_trace=("matched: drafts regime",),
                config=config,
            )

        trajectory_only = bool(getattr(config, "trajectory_only", False))
        if trajectory_only or not regime.has_binary_verifier:
            return _plan(
                readers=("TrajectoryCompressor", "LLMJudgeReader"),
                operators=("LLMBashEvolve",),
                verifier="NoVerify",
                artifact_scope={"skills": "rw"},
                reason_trace=("matched: trajectory_only regime",),
                config=config,
            )

        return _plan(
            readers=("PassFailReader", "TrajectoryCompressor"),
            operators=("LLMBashEvolve",),
            verifier="NoVerify",
            artifact_scope={"skills": "rw"},
            reason_trace=("default: minimal llm_bash recipe",),
            config=config,
        )


__all__ = ["RuleBasedController"]
