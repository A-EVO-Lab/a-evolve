"""Modules for the self-evolving framework."""

from .observer.observer import Observer
from .observer.persistent_observer import PersistentObserver
from .observer.appworld_observer import AppWorldObserver
from .proposer.proposer import Proposer
from .proposer.tool_enabled_proposer import ToolEnabledProposer
from .proposer.hybrid_tool_patch_proposer import HybridToolPatchProposer
from .updater.updater import Updater
from .updater.hybrid_tool_patch_updater import HybridToolPatchUpdater
from .updater.appworld_updater import AppWorldUpdater

# Meta-Evolver components for agentic evolution
from .evolver import (
    EvolutionState,
    MetaEvolver,
    EvolutionBudget,
    SkillLibrary,
    KnowledgeStore,
    Skill,
    KnowledgeEntry
)

__all__ = [
    'Observer',
    'PersistentObserver',
    'AppWorldObserver',
    'Proposer',
    'ToolEnabledProposer',
    'HybridToolPatchProposer',
    'Updater',
    'HybridToolPatchUpdater',
    'AppWorldUpdater',
    # Meta-Evolver
    'EvolutionState',
    'MetaEvolver',
    'EvolutionBudget',
    'SkillLibrary',
    'KnowledgeStore',
    'Skill',
    'KnowledgeEntry',
]
