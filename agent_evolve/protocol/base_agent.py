"""BaseAgent -- the parent class all evolvable agents inherit from.

BaseAgent handles all file system contract operations:
  - Loading system prompt, skills, and memories from the workspace
  - export_to_fs() / reload_from_fs() for the evolution loop
  - Subclasses only need to implement solve()
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path

from ..contract.workspace import AgentWorkspace
from ..types import SkillMeta, Task, Trajectory

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Base class for all evolvable agents.

    Provides default file system contract support. Subclasses override
    ``solve()`` with their task-specific logic and can freely use
    ``self.system_prompt``, ``self.skills``, and ``self.memories``.
    """

    def __init__(self, workspace_dir: str | Path):
        self.workspace = AgentWorkspace(workspace_dir)
        self.system_prompt: str = ""
        self.skills: list[SkillMeta] = []
        self.memories: list[dict] = []
        self._new_memories: list[dict] = []

        self.reload_from_fs()

    # ── File System Contract ─────────────────────────────────────────

    def reload_from_fs(self) -> None:
        """Reload agent state from the workspace directory.

        Called at init and after each evolution cycle.
        """
        self.system_prompt = self.workspace.read_prompt()
        self.skills = self.workspace.list_skills()
        self.memories = self.workspace.read_all_memories(limit=200)
        self._new_memories = []
        logger.info(
            "Reloaded from %s: prompt=%d chars, skills=%d, memories=%d",
            self.workspace.root,
            len(self.system_prompt),
            len(self.skills),
            len(self.memories),
        )

    def export_to_fs(self) -> None:
        """Write any accumulated in-memory state back to the workspace.

        By default this flushes new memories. Subclasses can override to
        export additional state (e.g. learned tool definitions).
        """
        for mem in self._new_memories:
            self.workspace.add_memory(mem, category=mem.pop("_category", "episodic"))
        self._new_memories = []

    # ── Memory helpers for subclasses ────────────────────────────────

    def remember(self, content: str, category: str = "episodic", **extra) -> None:
        """Buffer a new memory entry (flushed on export_to_fs)."""
        entry = {"content": content, "_category": category, **extra}
        self._new_memories.append(entry)

    def get_skill_content(self, name: str) -> str:
        """Load the full SKILL.md content for a skill by name."""
        return self.workspace.read_skill(name)

    # ── Abstract: subclasses implement this ──────────────────────────

    @abstractmethod
    def solve(self, task: Task) -> Trajectory:
        """Execute a single task and return the trajectory.

        Implementations can use self.system_prompt, self.skills,
        self.memories, and any additional state they manage.
        """
