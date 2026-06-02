"""BaseAgent -- the parent class all evolvable agents inherit from.

BaseAgent handles all file system contract operations:
  - Loading system prompt, skills, and memories from the workspace
  - Loading optional harness.py for scaffolding overrides
  - export_to_fs() / reload_from_fs() for the evolution loop
  - Subclasses only need to implement solve()
"""

from __future__ import annotations

import importlib.util
import logging
import types
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

    If the workspace contains a ``harness.py``, it is dynamically loaded
    and exposed as ``self.harness`` (a Python module).  Subclasses can
    check for hook functions via ``self.harness_hook("func_name")``.
    """

    def __init__(self, workspace_dir: str | Path):
        self.workspace = AgentWorkspace(workspace_dir)
        self.system_prompt: str = ""
        self.skills: list[SkillMeta] = []
        self.memories: list[dict] = []
        self.harness: types.ModuleType | None = None
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
        self.harness = self._load_harness()
        self._new_memories = []
        logger.info(
            "Reloaded from %s: prompt=%d chars, skills=%d, memories=%d, harness=%s",
            self.workspace.root,
            len(self.system_prompt),
            len(self.skills),
            len(self.memories),
            "loaded" if self.harness else "none",
        )

    def export_to_fs(self) -> None:
        """Write any accumulated in-memory state back to the workspace.

        By default this flushes new memories. Subclasses can override to
        export additional state (e.g. learned tool definitions).
        """
        if self._new_memories:
            logger.info("Exporting %d new memory(ies) to %s", len(self._new_memories), self.workspace.root)
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

    # ── Harness loading ────────────────────────────────────────────────

    def _load_harness(self) -> types.ModuleType | None:
        """Dynamically load harness.py from the workspace root.

        Returns the loaded module, or None if the file does not exist
        or fails to load.  A load failure is logged but does not crash
        the agent — it falls back to default behavior.
        """
        path = self.workspace.root / "harness.py"
        if not path.exists():
            return None
        try:
            spec = importlib.util.spec_from_file_location("workspace_harness", path)
            if spec is None or spec.loader is None:
                return None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            logger.info("Loaded harness.py from %s", path)
            return module
        except Exception as e:
            logger.warning("Failed to load harness.py: %s", e)
            return None

    def harness_hook(self, name: str):
        """Return a hook function from harness.py, or None if not defined.

        Usage in subclasses::

            hook = self.harness_hook("build_system_prompt")
            if hook:
                return hook(self.system_prompt, self.skills)
            # ... default implementation
        """
        if self.harness is None:
            return None
        return getattr(self.harness, name, None)

    # ── Abstract: subclasses implement this ──────────────────────────

    @abstractmethod
    def solve(self, task: Task) -> Trajectory:
        """Execute a single task and return the trajectory.

        Implementations can use self.system_prompt, self.skills,
        self.memories, and any additional state they manage.
        """
