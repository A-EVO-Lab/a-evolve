"""Tools package for agent_evolve framework.

Provides utility helpers for agents.  The live FutureX search pipeline
lives inline in ``agent_evolve.agents.futurex.solver`` (Wikipedia
revision API + DDGS+htmldate + FRED); the shared date-filtering helper
is ``htmldate_search``.
"""

from .htmldate_search import htmldate_filtered_search, format_for_agent

__all__ = [
    "htmldate_filtered_search",
    "format_for_agent",
]
