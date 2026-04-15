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
