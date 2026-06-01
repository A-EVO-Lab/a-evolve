"""Shared data types for Agent Evolve."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Task:
    """A single evaluation task from a benchmark."""

    id: str
    input: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Trajectory:
    """The agent's execution trace for a single task."""

    task_id: str
    output: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    conversation: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Feedback:
    """Benchmark evaluation result for a trajectory."""

    success: bool
    score: float  # 0.0 ~ 1.0
    detail: str  # rich diagnostic text for the evolver
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Observation:
    """A bundled (task, trajectory, feedback) triple collected by the observer."""

    task: Task
    trajectory: Trajectory
    feedback: Feedback


@dataclass
class SkillMeta:
    """Lightweight metadata for a skill (loaded from SKILL.md frontmatter)."""

    name: str
    description: str
    path: str  # relative path within workspace


@dataclass
class StepResult:
    """Return type for EvolutionEngine.step()."""

    mutated: bool
    summary: str
    metadata: dict[str, Any] = field(default_factory=dict)
    stop: bool = False


@dataclass
class CycleRecord:
    """Record of a single evolution cycle."""

    cycle: int
    score: float
    mutated: bool
    engine_name: str = ""
    summary: str = ""
    observation_batch: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvolutionResult:
    """Summary returned after an evolution run completes."""

    cycles_completed: int
    final_score: float
    score_history: list[float] = field(default_factory=list)
    converged: bool = False
    details: dict[str, Any] = field(default_factory=dict)


# ── Navigation types (--navigation flag) ────────────────────────────────


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
