"""Navigation-specific data types (--navigation / branch-routing).

These types are used only by the navigation engine and its templates, so
they live in the navigation package rather than the shared ``agent_evolve.types``
contract. Kept as a separate module so importing them does not pull in the
whole navigation engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FailureClassification:
    """Evolver's diagnosis of a single task failure.

    Used by F_Evaluate to classify failures as stationary (fix on main)
    or non-stationary (fix on a branch).
    """

    task_id: str
    type: str  # "stationary" or "non_stationary"
    confidence: float  # 0.0–1.0, evolver's confidence in classification
    branch: str = ""  # suggested branch name (non-stationary only)
    reason: str = ""  # human-readable explanation


@dataclass
class RoutingEntry:
    """Record of one task's routing decision + outcome.

    Accumulated in evolver_workspace/routing_log.jsonl to help the
    navigator learn which branches work for which task properties.
    """

    task_id: str
    properties: dict[str, Any]  # s(x): extracted task properties
    branch: str  # which branch was selected
    score: float  # task outcome (0.0 or 1.0)
    cycle: int = 0  # which evolution cycle


@dataclass
class BranchInfo:
    """Metadata for one branch in the strategy tree."""

    name: str  # git branch name (e.g., "branch/algebraic-reasoning")
    created_at_cycle: int = 0
    last_routed_cycle: int = 0  # last time a task was routed here
    total_tasks: int = 0
    total_passed: int = 0
    description: str = ""  # what this branch specializes in
    # Count of failed git checkouts for this branch during solve. When
    # this crosses the navigator's threshold, the branch is filtered out
    # of future routing (see NavigationEngine._viable_branches).
    failed_checkouts: int = 0


@dataclass
class StrategyTree:
    """The full strategy tree state.

    Tracks branches and routing history. Serialized to
    evolver_workspace/tree_state.json between cycles.
    """

    branches: list[BranchInfo] = field(default_factory=list)
    routing_log: list[RoutingEntry] = field(default_factory=list)

    def branch_names(self) -> list[str]:
        return [b.name for b in self.branches]

    def get_branch(self, name: str) -> BranchInfo | None:
        for b in self.branches:
            if b.name == name:
                return b
        return None

    def to_dict(self) -> dict:
        return {
            "branches": [
                {"name": b.name, "created_at_cycle": b.created_at_cycle,
                 "last_routed_cycle": b.last_routed_cycle,
                 "total_tasks": b.total_tasks, "total_passed": b.total_passed,
                 "description": b.description,
                 "failed_checkouts": getattr(b, "failed_checkouts", 0)}
                for b in self.branches
            ],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "StrategyTree":
        tree = cls()
        for b in d.get("branches", []):
            tree.branches.append(BranchInfo(**b))
        return tree
