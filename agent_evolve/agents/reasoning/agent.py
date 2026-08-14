"""Reasoning / search agent for logic and knowledge tasks.

TODO: Implement solve() -- chain-of-thought reasoning.
      Implementer should add search tools, reasoning strategies, etc. as needed.
"""

from __future__ import annotations

from ...protocol.base_agent import BaseAgent
from ...types import Task, Trajectory


class ReasoningAgent(BaseAgent):
    """Reference agent for HLE and logic reasoning tasks.

    Uses self.system_prompt, self.skills, and self.memories
    to solve logic puzzles via chain-of-thought reasoning.

    TODO: Implement solve() with:
      - Chain-of-thought prompting
      - Search / retrieval tools (optional)
      - Answer extraction and formatting
    """

    def solve(self, task: Task) -> Trajectory:
        raise NotImplementedError(
            "TODO: Implement chain-of-thought reasoning logic. "
            "Add search tools, reasoning strategies alongside this file as needed."
        )
