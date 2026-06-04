"""Regime detection — runtime evidence classifier.

Pure function: ``detect_regime(capability, observations, workspace, config)``.
Reads uniform fields from ``Observation`` / ``AgentWorkspace`` / ``EvolveConfig``
to produce a ``RegimeTag``. Respects ``config.trajectory_only`` and performs
observation-shape inference for feedback masking (same pattern as
``guided_synth/engine.py:466``), but does NOT import from any legacy engine
module.
"""

from __future__ import annotations

from typing import Any

from .types import FeedbackCapability, RegimeTag


def _is_masked_observation(obs: Any) -> bool:
    """Legacy masking pattern reproduced here (see guided_synth/engine.py:466).

    An observation is "masked" when its feedback has been zeroed by an outer
    script (e.g., SWE's ``--feedback none``).
    """
    fb = getattr(obs, "feedback", None)
    if fb is None:
        return True
    score = float(getattr(fb, "score", 0.0))
    detail = getattr(fb, "detail", "") or ""
    success = bool(getattr(fb, "success", False))
    return score == 0.0 and not detail and not success


def detect_regime(
    capability: FeedbackCapability,
    observations: list,
    workspace: Any,
    config: Any,
) -> RegimeTag:
    """Produce a RegimeTag from the observed evidence.

    Parameters
    ----------
    capability:
        Static declaration of what evidence the benchmark can physically
        provide. Used as a ceiling; runtime evidence can still be absent
        even when capability claims it is available.
    observations:
        Batch of ``Observation`` objects for this cycle.
    workspace:
        ``AgentWorkspace`` — used to see if the solver wrote drafts.
    config:
        ``EvolveConfig`` — inspected for ``trajectory_only``.
    """
    trajectory_only = bool(getattr(config, "trajectory_only", False))

    # Observation-shape inference: a batch is "shape-masked" if EVERY
    # observation's feedback looks zeroed. Mixed batches still expose the
    # feedback signal.
    obs_list = list(observations)
    if obs_list:
        shape_masked = all(_is_masked_observation(o) for o in obs_list)
    else:
        shape_masked = False

    feedback_masked = trajectory_only or shape_masked

    # Pass/fail + score signals.
    scores: list[float] = []
    any_success = False
    for o in obs_list:
        fb = getattr(o, "feedback", None)
        if fb is None:
            continue
        scores.append(float(getattr(fb, "score", 0.0)))
        any_success = any_success or bool(getattr(fb, "success", False))

    has_pass_fail = bool(capability.has_pass_fail) and not feedback_masked
    has_partial_score = bool(capability.has_partial_score) and not feedback_masked
    has_per_test = bool(capability.has_per_test) and not feedback_masked

    # per_claim: only count when runtime data is present.
    has_per_claim = False
    if bool(capability.has_per_claim) and not feedback_masked:
        for o in obs_list:
            raw = getattr(getattr(o, "feedback", None), "raw", {}) or {}
            if raw.get("per_claim"):
                has_per_claim = True
                break

    extra = getattr(config, "extra", {}) or {}
    allow_solver_proposals = bool(extra.get("solver_proposes", True))
    proposal_visible_when_masked = bool(
        extra.get("solver_proposals_visible_when_feedback_masked", False)
    )

    # Solver proposal: runtime evidence, not capability hint.
    # By default trajectory_only masks proposals for backward compatibility.
    # SWE's legacy v32g setting is the exception: feedback=none masks scores
    # but solver proposals are still a solver-authored artifact to curate.
    # Observation-shape masking does NOT mask proposals (orthogonal signal).
    has_solver_proposal = False
    if allow_solver_proposals and (not trajectory_only or proposal_visible_when_masked):
        for o in obs_list:
            proposal = getattr(getattr(o, "trajectory", None), "_skill_proposal", "")
            if proposal and "ACTION: NONE" not in proposal.upper():
                has_solver_proposal = True
                break

    # Drafts: always detectable at workspace level.
    try:
        has_drafts = len(list(workspace.list_drafts())) > 0
    except Exception:
        has_drafts = False

    has_binary_verifier = bool(capability.has_pass_fail) and not feedback_masked and any_success

    judge_available = bool(capability.judge_available)

    # pass_rate: only computed when the signal is visible.
    pass_rate: float | None
    if scores and not feedback_masked:
        pass_count = sum(1 for o in obs_list if bool(getattr(getattr(o, "feedback", None), "success", False)))
        pass_rate = round(pass_count / len(obs_list), 4) if obs_list else None
    else:
        pass_rate = None

    return RegimeTag(
        has_pass_fail=has_pass_fail,
        has_partial_score=has_partial_score,
        has_per_claim=has_per_claim,
        has_per_test=has_per_test,
        has_solver_proposal=has_solver_proposal,
        has_drafts=has_drafts,
        has_binary_verifier=has_binary_verifier,
        judge_available=judge_available,
        pass_rate=pass_rate,
        patterns=(),
    )


__all__ = ["detect_regime"]
