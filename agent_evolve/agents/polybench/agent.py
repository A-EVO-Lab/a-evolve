"""PolyBench prediction-market agent -- uses strands-agents at runtime.

The framework layer (BaseAgent) loads prompts/skills/memory/tools from the
file system contract. This agent assembles them into a system prompt for
pure-reasoning prediction market analysis. No Docker containers needed.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ...protocol.base_agent import BaseAgent
from ...types import Task, Trajectory

logger = logging.getLogger(__name__)


class PolyBenchAgent(BaseAgent):
    """Agent for PolyBench prediction-market tasks.

    Reads system prompt, skills, memories, and tools from the workspace,
    then assembles them into a system prompt used by the backend's
    solve_one() to drive a strands Agent.

    Adds a ``tool_registry`` and ``skip_layers`` gating on top of the stock
    ``BaseAgent`` (loading a layer is skipped when its ``evolve_*`` flag is
    off); kept local so the shared ``BaseAgent`` contract is untouched.
    """

    def __init__(
        self,
        workspace_dir: str | Path,
        model_id: str | None = None,
        region: str | None = None,
        max_tokens: int = 16384,
        skip_layers: frozenset[str] = frozenset(),
    ):
        import os as _os
        self._skip_layers = skip_layers
        self.tool_registry: list[dict] = []
        super().__init__(workspace_dir)
        self.model_id = model_id or _os.environ.get("SOLVER_MODEL", "<solver-model-id>")
        self.region = region or _os.environ.get("AWS_REGION", "us-west-2")
        self.max_tokens = max_tokens

    def reload_from_fs(self) -> None:
        """Reload workspace state, honouring ``skip_layers`` + loading tools."""
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

    def _build_system_prompt(self) -> str:
        """Assemble the full system prompt from workspace files."""
        parts = [self.system_prompt]

        if self.skills:
            parts.append("\n\n## Available Skills\n")
            parts.append(
                "You have specialized skills. Review them when facing relevant challenges.\n"
            )
            for skill in self.skills:
                parts.append(f"- **{skill.name}**: {skill.description}")
                content = self.get_skill_content(skill.name)
                if content:
                    body = content.split("---", 2)[-1].strip() if "---" in content else content
                    parts.append(f"\n{body}\n")

        if self.tool_registry:
            parts.append("\n\n## Evolved Tools\n")
            parts.append(
                "Analysis tools available in the sandbox. Run them via the bash tool.\n"
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
            "PolyBenchAgent.solve() is not used directly. "
            "The polybench backend builds its own strands Agent in solve_one()."
        )
