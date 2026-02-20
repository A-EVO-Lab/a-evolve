"""
Evolver Module - Agentic Evolution for Adaptive Intelligence.

This module provides the Meta-Evolver framework for interaction-time evolution:

- EvolutionState: Persistent state St = {Tt, Mt, Kt}
- MetaEvolver: Stateful agent for diagnosis, planning, and verification
- SkillLibrary: Library of reusable skills (Mt)
- KnowledgeStore: Knowledge with provenance (Kt)
"""

from .evolution_state import (
    EvolutionState,
    SkillLibrary,
    KnowledgeStore,
    Skill,
    KnowledgeEntry,
    StateSnapshot
)

from .meta_evolver import (
    MetaEvolver,
    EvolutionBudget,
    UpdateAction,
    ACTION_CREATE_FEATURE_TOOL,
    ACTION_EVOLVE_FEATURE_TOOL,
    ACTION_ADD_PATCH,
    ACTION_ADD_SKILL,
    ACTION_EVOLVE_SKILL,
    ACTION_SKIP
)

__all__ = [
    'EvolutionState',
    'SkillLibrary',
    'KnowledgeStore',
    'Skill',
    'KnowledgeEntry',
    'StateSnapshot',
    'MetaEvolver',
    'EvolutionBudget',
    'UpdateAction'
]
