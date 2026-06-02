"""Unified operator atoms — mutate the workspace and return MutationReports.

Every module in this package is an **independent reimplementation** of logic
originally scattered across legacy engines. No ``import`` from
``agent_evolve/algorithms/adaptive_evolve/`` etc. is permitted here; legacy
source is a read-only specification only. A CI check enforces this.

Importing this package triggers registration of all operators.
"""

from . import (
    auto_seed_skills,
    fix_hallucinations,
    llm_bash_evolve,
    prune_skills,
    sanity_check,
    skill_curator,
    terminal_skill_evolve,
    write_episodic_memory,
)

__all__: list[str] = []
