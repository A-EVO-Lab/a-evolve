"""Abstract base class for evolution templates."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ....contract.workspace import AgentWorkspace
    from ....engine.versioning import VersionControl
    from ....types import StrategyTree


class EvolutionTemplate(ABC):
    """Interface for pluggable evolution execution strategies.

    Subclasses implement a concrete evolution mode (inline, orchestrated,
    custom) and are injected into ``NavigationEngine`` at construction.
    The engine calls ``execute()`` during ``evolve_with_navigation()``
    without knowing which strategy is active.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier for this template (e.g. 'inline', 'orchestrated')."""

    @abstractmethod
    def execute(
        self,
        vc: VersionControl,
        solver_workspace: AgentWorkspace,
        batch_results: list[dict[str, Any]],
        tree: StrategyTree,
        evo_number: int,
        routing_log_path: Path | None,
    ) -> dict[str, Any]:
        """Run one evolution cycle.

        Returns a dict with at least:
          - evo_number: int
          - mutated: bool
          - plan: dict
          - branches: list[str]
          - trajectory: list[dict]
        """
