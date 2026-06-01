"""A-Evolve -- LLM-driven workspace mutation algorithm.

The original evolution strategy: an LLM analyzes observation logs, reviews
draft skills, and mutates the agent workspace (prompts, skills, memory)
via bash tool access.
"""

from .engine import AEvolveEngine
from .navigation import NavigationEngine
from .prompts import DEFAULT_EVOLVER_SYSTEM_PROMPT
from .tools import BASH_TOOL_SPEC, make_workspace_bash

__all__ = [
    "AEvolveEngine",
    "NavigationEngine",
    "DEFAULT_EVOLVER_SYSTEM_PROMPT",
    "BASH_TOOL_SPEC",
    "make_workspace_bash",
]
