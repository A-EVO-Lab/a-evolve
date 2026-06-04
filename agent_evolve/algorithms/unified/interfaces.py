"""Atomic action-space protocols: Reader, Operator, Verifier.

Every unified atom implements exactly one of these protocols. The uniform
signatures let :class:`UnifiedEngine` drive any recipe without knowing what
each atom does internally.

Signature rationale:
- ``context`` appears on ``Reader`` too (not only operators), so a downstream
  reader can see what upstream readers produced in the same cycle. Readers
  MUST treat ``context`` as read-only.
- ``state`` is present on every atom from day 1, even trivial stateless
  atoms, so Phase 2 can introduce stateful variants without signature churn.
- ``Operator.apply`` receives ``scope`` so it can refuse writes outside its
  allowed artifact set (raises ``ScopeViolationError``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agent_evolve.config import EvolveConfig
    from agent_evolve.contract.workspace import AgentWorkspace
    from agent_evolve.engine.history import EvolutionHistory
    from agent_evolve.engine.trial import TrialRunner
    from agent_evolve.types import Observation
    from .types import ArtifactMode, EvidenceContext, MutationReport, Verdict


class ScopeViolationError(RuntimeError):
    """Raised when an operator attempts to mutate an artifact outside its scope."""


@runtime_checkable
class Reader(Protocol):
    """Produces a dict of evidence from observations / workspace / history.

    The returned dict is stored in ``EvidenceContext.entries`` under the
    reader's registered name. Downstream readers and operators look it up
    by that name.
    """

    def read(
        self,
        observations: list["Observation"],
        workspace: "AgentWorkspace",
        history: "EvolutionHistory",
        config: "EvolveConfig",
        context: "EvidenceContext",
        state: dict[str, Any],
    ) -> dict[str, Any]:
        ...


@runtime_checkable
class Operator(Protocol):
    """Mutates the workspace and returns a MutationReport."""

    def apply(
        self,
        workspace: "AgentWorkspace",
        context: "EvidenceContext",
        scope: dict[str, "ArtifactMode"],
        state: dict[str, Any],
    ) -> "MutationReport":
        ...


@runtime_checkable
class Verifier(Protocol):
    """Inspects reports + workspace and returns a Verdict.

    A verifier MAY instruct a rollback (``Verdict.rollback=True``). Verifier
    state can track rolling metrics such as ``_best_pass_rate`` across cycles.
    """

    def check(
        self,
        workspace: "AgentWorkspace",
        context: "EvidenceContext",
        reports: list["MutationReport"],
        trial: "TrialRunner",
        history: "EvolutionHistory",
        state: dict[str, Any],
    ) -> "Verdict":
        ...


__all__ = [
    "Operator",
    "Reader",
    "ScopeViolationError",
    "Verifier",
]
