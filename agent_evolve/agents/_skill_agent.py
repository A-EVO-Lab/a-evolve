"""Intermediate agent base adding ``skip_layers`` + a tool registry.

The benchmark agents (polybench / ctf_dojo / futurex) need two things on top
of the stock :class:`~agent_evolve.protocol.base_agent.BaseAgent`:

  * ``self.tool_registry`` — the workspace's evolved-tool registry, and
  * ``skip_layers`` — the ability to NOT load a harness layer (skills /
    memory / tools) when a benchmark's config disables it via
    ``evolve_*=False``.

Rather than change the shared ``BaseAgent`` contract, those live here as a
thin subclass that the benchmark agents extend. Behavior matches the V2
fork's ``BaseAgent`` exactly; it just keeps the addition out of the core
protocol so it stays mergeable as a pure addition.
"""
from __future__ import annotations

from pathlib import Path

from ..protocol.base_agent import BaseAgent, logger


class SkillLayerAgent(BaseAgent):
    """``BaseAgent`` + a tool registry and per-layer load gating."""

    def __init__(
        self,
        workspace_dir: str | Path,
        skip_layers: frozenset[str] = frozenset(),
    ):
        # Set our extra state BEFORE super().__init__, because BaseAgent.__init__
        # calls reload_from_fs() (overridden below) which reads both.
        self._skip_layers = skip_layers
        self.tool_registry: list[dict] = []
        super().__init__(workspace_dir)

    def reload_from_fs(self) -> None:
        """Reload agent state, honouring ``skip_layers`` and loading tools.

        Any named layer in ``skip_layers`` is left as an empty list (used when
        a benchmark's config disables that layer via ``evolve_*=False``).
        """
        skip = getattr(self, "_skip_layers", frozenset())
        self.system_prompt = self.workspace.read_prompt()
        self.skills = [] if "skills" in skip else self.workspace.list_skills()
        self.memories = (
            [] if "memory" in skip
            else self.workspace.read_all_memories(limit=200)
        )
        self.tool_registry = (
            [] if "tools" in skip else self.workspace.read_tool_registry()
        )
        self.harness = self._load_harness()
        self._new_memories = []
        logger.info(
            "Reloaded from %s: prompt=%d chars, skills=%d, memories=%d, "
            "tools=%d, harness=%s",
            self.workspace.root,
            len(self.system_prompt),
            len(self.skills),
            len(self.memories),
            len(self.tool_registry),
            "loaded" if self.harness else "none",
        )
