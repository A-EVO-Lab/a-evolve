"""
FutureX prediction agent package.

This package provides specialized agents for temporal prediction tasks with
time-bounded web search capabilities and domain-specific expertise.
"""

from .agent import FutureXAgent, create_futurex_agent

__all__ = [
    "FutureXAgent",
    "create_futurex_agent"
]