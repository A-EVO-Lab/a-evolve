"""EvolutionEngine -- the single abstraction for pluggable evolution algorithms.

Implement ``step()`` to create a new evolution strategy. The loop provides
shared primitives (workspace, observations, history, trial runner); your
algorithm decides what to do with them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..contract.workspace import AgentWorkspace
    from ..types import Observation, StepResult
    from .history import EvolutionHistory
    from .trial import TrialRunner


class EvolutionEngine(ABC):
    """Base class for all evolution algorithms.

    Subclasses implement ``step()`` — one evolution cycle.  The method
    receives the current workspace, this cycle's observations, a history
    query facade, and a trial runner for optional live validation.

    The engine is free to mutate the workspace however it sees fit.
    """

    @abstractmethod
    def step(
        self,
        workspace: AgentWorkspace,
        observations: list[Observation],
        history: EvolutionHistory,
        trial: TrialRunner,
    ) -> StepResult:
        """Run one evolution step.  Mutate *workspace* as needed.

        Args:
            workspace: The agent's file-system workspace (read/write).
            observations: Results from the current solve batch.
            history: Query interface over past observations and workspace versions.
            trial: Runner for live agent evaluation (use sparingly — expensive).

        Returns:
            A ``StepResult`` indicating whether the workspace was mutated.
        """

    @property
    def manages_own_evaluation(self) -> bool:
        """Whether this engine handles its own evaluation internally.

        When True, the loop skips its solve+evaluate batch and passes
        empty observations to step(). The engine is expected to use
        the TrialRunner for its own evaluation needs.
        """
        return False

    def on_cycle_end(self, accepted: bool, score: float) -> None:
        """Optional callback invoked after each cycle completes.

        Override to track accept/reject signals or adjust internal state.
        """
