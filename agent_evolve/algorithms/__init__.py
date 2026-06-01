"""Built-in evolution algorithm implementations.

Canonical engines in the released version:
  - ``aevolve``         -- V2's LLM-driven workspace mutation (default).
  - ``navigation``      -- branch routing + holistic analysis (peer).

The comparison-method baselines (``gepa``, ``meta_harness``, ``skillos``,
``continual_harness``, ``mas_adaptive_skill``) and the ``skillforge``
upstream alias are not shipped in the released version; only the core
method is retained.
"""

from .aevolve import AEvolveEngine
from .navigation import NavigationEngine

__all__ = [
    "AEvolveEngine",
    "NavigationEngine",
]
