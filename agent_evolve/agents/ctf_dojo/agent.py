"""CTF-Dojo cybersecurity agent.

Assembles system prompts for CTF challenge solving tasks.
Challenge files are at /challenge/, evolved tools at /tools/ in the sandbox.
"""
from __future__ import annotations

import logging
from pathlib import Path

from .._skill_agent import SkillLayerAgent
from ...types import Task, Trajectory

logger = logging.getLogger(__name__)


class CtfDojoAgent(SkillLayerAgent):
    """Agent for CTF-Dojo cybersecurity challenge tasks.

    Reads system prompt, skills, memories, and tools from the workspace
    via BaseAgent.  The backend's solve_one() drives the actual strands
    Agent; this class manages workspace state and prompt assembly.
    """

    def __init__(
        self,
        workspace_dir: str | Path,
        model_id: str = "<solver-model-id>",
        region: str = "us-west-2",
        max_tokens: int = 16384,
        skip_layers: frozenset[str] = frozenset(),
    ):
        super().__init__(workspace_dir, skip_layers=skip_layers)
        self.model_id = model_id
        self.region = region
        self.max_tokens = max_tokens

    def _build_system_prompt(self) -> str:
        """Assemble system prompt with evolved layers."""
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
            "CtfDojoAgent.solve() is not used directly. "
            "The ctf_dojo backend builds its own strands Agent in solve_one()."
        )
