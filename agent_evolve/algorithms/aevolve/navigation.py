"""Legacy back-compat path for NavigationEngine.

NavigationEngine is now a peer package at
``agent_evolve.algorithms.navigation``.  Legacy callers that imported
from ``agent_evolve.algorithms.aevolve.navigation`` continue to work
via this re-export.
"""

from ..navigation.engine import NavigationEngine  # noqa: F401

__all__ = ["NavigationEngine"]
