"""Unified evolution engine with atomic Readers / Operators / Verifiers.

The unified framework is a standalone, physically decoupled reimplementation
of behaviors previously scattered across four legacy engines
(``adaptive_evolve``, ``adaptive_skill``, ``guided_synth``, ``skillforge``).

Modules in this package MUST NOT ``import`` anything from the four legacy
engine packages above. Legacy source is a read-only specification only.
A CI grep check enforces this boundary.
"""

from .controller import RuleBasedController
from .engine import UnifiedEngine
from .regimes import detect_regime
from .types import (
    ArtifactMode,
    EvidenceContext,
    FeedbackCapability,
    MutationReport,
    Plan,
    RegimeTag,
    Verdict,
)

__all__ = [
    "ArtifactMode",
    "EvidenceContext",
    "FeedbackCapability",
    "MutationReport",
    "Plan",
    "RegimeTag",
    "RuleBasedController",
    "UnifiedEngine",
    "Verdict",
    "detect_regime",
]
