"""Navigation — git branching + task routing + pluggable evolution templates.

Navigation = branching.  Git branches isolate strategies; the navigator
routes each task to the best-equipped branch.  Evolution is a pluggable
``EvolutionTemplate``; two are shipped (inline, orchestrated) and any
custom Activity can be wrapped as a third.

Promoted from ``aevolve/navigation.py`` to a peer-package so it stands
alongside ``aevolve``, ``mas_adaptive_skill``, ``meta_harness``, ``gepa``.
"""

from .adaptation import TreeRoutingAdaptation
from .engine import NavigationEngine
from .prompts import NAVIGATE_SYSTEM_PROMPT, build_navigate_prompt
from .templates.base import EvolutionTemplate
from .templates.inline import InlineTemplate, build_branching_section
from .templates.orchestrated import (
    ANALYZE_PLAN_SYSTEM_PROMPT,
    PLANNER_SYSTEM_PROMPT_FLAT,
    PLANNER_SYSTEM_PROMPT_NAV,
    OrchestratedTemplate,
    build_analyze_plan_prompt,
    build_planner_prompt,
)

__all__ = [
    # Engine
    "NavigationEngine",
    # Adaptation (solve-time)
    "TreeRoutingAdaptation",
    # Templates
    "EvolutionTemplate",
    "InlineTemplate",
    "OrchestratedTemplate",
    # Routing prompt (shared)
    "NAVIGATE_SYSTEM_PROMPT",
    "build_navigate_prompt",
    # Template-specific prompts (re-exported for convenience)
    "ANALYZE_PLAN_SYSTEM_PROMPT",
    "PLANNER_SYSTEM_PROMPT_NAV",
    "PLANNER_SYSTEM_PROMPT_FLAT",
    "build_analyze_plan_prompt",
    "build_planner_prompt",
    "build_branching_section",
]
