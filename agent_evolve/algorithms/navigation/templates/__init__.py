"""Pluggable evolution templates for NavigationEngine.

Each template implements a concrete evolution strategy (inline, orchestrated,
etc.) behind the common ``EvolutionTemplate`` interface.  The engine
dispatches to ``template.execute()`` without knowing which mode is active.
"""

from .base import EvolutionTemplate
from .inline import InlineTemplate
from .orchestrated import OrchestratedTemplate

__all__ = [
    "EvolutionTemplate",
    "InlineTemplate",
    "OrchestratedTemplate",
]
