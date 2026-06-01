"""Agent Evolve -- evolve any agent through a file system contract."""

__version__ = "0.1.0"

from .api import Evolver
from .benchmarks.base import BenchmarkAdapter
from .config import EvolveConfig
from .contract.manifest import Manifest
from .contract.workspace import AgentWorkspace
from .engine.base import EvolutionEngine
from .engine.history import EvolutionHistory
from .engine.trial import TrialRunner
from .protocol.base_agent import BaseAgent
from .types import (
    CycleRecord,
    EvolutionResult,
    Feedback,
    Observation,
    SkillMeta,
    StepResult,
    Task,
    Trajectory,
)

__all__ = [
    "Evolver",
    "EvolutionEngine",
    "EvolutionHistory",
    "TrialRunner",
    "BaseAgent",
    "BenchmarkAdapter",
    "AgentWorkspace",
    "Manifest",
    "EvolveConfig",
    "Task",
    "Trajectory",
    "Feedback",
    "Observation",
    "SkillMeta",
    "StepResult",
    "CycleRecord",
    "EvolutionResult",
]
