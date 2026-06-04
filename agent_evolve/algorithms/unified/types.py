"""Value types for the unified evolution action space.

All types here are immutable dataclasses. Mutable runtime state — the per-atom
``state`` dict passed by ``UnifiedEngine`` and the ``EvidenceContext.entries``
accumulated by readers — lives in dict containers rather than dataclass fields,
keeping the dataclass boundary clean and auditable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


ArtifactMode = Literal["ro", "rw", "append", "none"]
"""Per-artifact write permission level used in ``Plan.artifact_scope``.

``ro`` = read-only, ``rw`` = read+write, ``append`` = append-only (e.g., JSONL
memory), ``none`` = hidden. Operators that attempt to write outside their
declared scope must raise ``ScopeViolationError`` (see engine module).
"""


@dataclass(frozen=True)
class FeedbackCapability:
    """What evidence a benchmark can physically provide to an evolver.

    Frozen so a benchmark's capability cannot drift mid-trial. Each field is a
    static declaration by the benchmark adapter; runtime availability of the
    same evidence is captured separately in :class:`RegimeTag`.
    """

    has_pass_fail: bool = True
    has_partial_score: bool = False
    has_per_claim: bool = False
    has_per_test: bool = False
    solver_may_propose: bool = False
    judge_available: bool = True


@dataclass(frozen=True)
class RegimeTag:
    """Runtime-observed evidence profile for a single evolution cycle.

    Produced by ``detect_regime()`` from the current observations / workspace /
    config. ``pass_rate`` is ``None`` when the pass/fail signal has been masked
    (``config.trajectory_only`` or observation-shape inference) unless a judge
    proxy is available.
    """

    has_pass_fail: bool = False
    has_partial_score: bool = False
    has_per_claim: bool = False
    has_per_test: bool = False
    has_solver_proposal: bool = False
    has_drafts: bool = False
    has_binary_verifier: bool = False
    judge_available: bool = False
    pass_rate: float | None = None
    patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
class Plan:
    """A concrete execution recipe emitted by the controller.

    Field order is the execution order: readers run first, then operators,
    then the single verifier. ``legacy_engine`` is intentionally absent —
    Phase 1 forbids engine delegation.
    """

    readers: tuple[str, ...]
    operators: tuple[str, ...]
    verifier: str
    artifact_scope: dict[str, ArtifactMode]
    reason_trace: tuple[str, ...] = ()


@dataclass
class EvidenceContext:
    """Mutable container accumulated by readers and consumed by operators.

    ``entries`` is keyed by the reader's registered name. Readers write to
    their own slot; downstream readers and operators read freely. Operators
    MUST NOT write to ``EvidenceContext`` — shared mutations flow through the
    workspace file system instead.
    """

    entries: dict[str, Any] = field(default_factory=dict)


@dataclass
class MutationReport:
    """Summary of a single operator invocation.

    ``count`` is the number of distinct mutations the operator performed
    (skill writes, memory appends, prompt edits, etc.). ``details`` is a
    free-form dict for operator-specific diagnostics persisted into
    ``StepResult.metadata``.
    """

    operator_name: str
    count: int = 0
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class Verdict:
    """Outcome of a verifier.

    ``accept=True`` means the verifier approves the cycle's mutations.
    ``rollback=True`` instructs ``UnifiedEngine`` to revert the workspace to
    the pre-step snapshot; ``accept`` is ignored in that case.
    """

    accept: bool = True
    rollback: bool = False
    reason: str = ""
