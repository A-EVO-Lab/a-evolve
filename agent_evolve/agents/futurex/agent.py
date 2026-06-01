"""FutureX prediction agent (workspace plumbing only).

The live solve path for FutureX runs inside
``agent_evolve.agents.futurex.solver.solve_one``, which builds its own
Strands ``Agent`` with ``web_search`` / ``bash`` / ``submit`` tools per
task.  This ``FutureXAgent`` class exists so the workspace manifest
(``experiments/futurex/seed/manifest.yaml``) has a concrete
``BaseAgent`` subclass to point at, and so the solver can reuse
``BaseAgent``'s FS contract (prompt / skills / memory / tool_registry
loading + ``skip_layers`` gating).

The old ``solve_task`` / ``_solve_with_search`` / ``_synthesize_*``
methods that consumed ``agent_evolve.tools.time_bounded_search`` have
been removed — that search abstraction was unused by the live pipeline
and its module was deleted.  If a standalone agent-invoked search flow
is ever needed, plumb it through the same ``htmldate_filtered_search``
used by ``solver.py``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from agent_evolve.config import EvolveConfig
from agent_evolve.protocol.base_agent import BaseAgent
from agent_evolve.types import Task, Trajectory

logger = logging.getLogger(__name__)


class FutureXAgent(BaseAgent):
    """FutureX agent — reference implementation.

    Only used for workspace plumbing; ``solve()`` is unreachable under
    the production pipeline (``solver.solve_one`` builds its own Strands
    agent per task).
    """

    def __init__(
        self,
        workspace_dir: str | Path,
        config: EvolveConfig,
        skip_layers: frozenset[str] = frozenset(),
    ):
        super().__init__(workspace_dir, skip_layers=skip_layers)
        self.config = config

    def _build_system_prompt(self) -> str:
        """Assemble system prompt from workspace layers.

        Called by ``solver.build_prompts`` to feed the per-task
        Strands ``Agent``.
        """
        parts = [self.system_prompt]

        if self.skills:
            parts.append("\n\n## Available Skills\n")
            for skill in self.skills:
                parts.append(f"- **{skill.name}**: {skill.description}")
                content = self.get_skill_content(skill.name)
                if content:
                    body = content.split("---", 2)[-1].strip() if "---" in content else content
                    parts.append(f"\n{body}\n")

        if self.tool_registry:
            parts.append("\n\n## Evolved Tools\n")
            builtin_search = getattr(self.config, "extra", {}).get(
                "builtin_search", "strict"
            )
            if builtin_search == "off":
                parts.append(
                    "You have no built-in web_search tool. The scripts at /tools/ "
                    "are your only path to external data — invoke them via the "
                    "bash tool.\n"
                )
            else:
                parts.append(
                    "Analysis tools available at /tools/ in the sandbox. "
                    "Run them via the bash tool.\n"
                )
            for t in self.tool_registry:
                name = t.get("name", "")
                desc = t.get("description", "")
                parts.append(f"- **{name}**: {desc}")
                parts.append(f"  Usage: `python3 /tools/{name}.py`")

        if self.memories:
            parts.append("\n\n## Relevant Memories\n")
            for m in self.memories[-10:]:
                parts.append(f"- {m.get('content', '')}")

        return "\n".join(parts)

    def solve(self, task: Task) -> Trajectory:
        raise NotImplementedError(
            "FutureXAgent.solve() is not used directly. "
            "The futurex solver builds its own Strands Agent in solve_one()."
        )


def create_futurex_agent(
    workspace_dir: str, config: EvolveConfig,
) -> FutureXAgent:
    """Convenience factory matching the other benchmark agents."""
    return FutureXAgent(workspace_dir, config)
