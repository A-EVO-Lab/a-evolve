"""Unified reader atoms — produce evidence dicts for ``EvidenceContext``.

Every module in this package is an **independent reimplementation** of
logic originally found in a legacy engine module. No ``import`` from
``agent_evolve/algorithms/adaptive_evolve/``, ``adaptive_skill/``,
``guided_synth/``, or ``skillforge/`` is permitted here.

Readers register themselves at import time via the decorators from
``unified.registry``. Importing this package triggers the full set of
registrations.
"""

from . import (
    claim,
    claim_types,
    draft,
    judge,
    pass_fail,
    patterns,
    proposal,
    score_curve,
    terminal_trajectory,
    trajectory,
)

__all__: list[str] = []
