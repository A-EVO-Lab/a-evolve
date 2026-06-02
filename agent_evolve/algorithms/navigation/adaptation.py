"""Tree-routing adaptation operator.

Wraps the existing ``NavigationEngine.navigate`` (agentic branch routing)
and git-branch checkout in the :class:`Adaptation` interface, so the solve
loop no longer needs to know that the storage is a git tree.

This is a faithful extraction — ``select`` delegates to the unchanged
``navigate`` and ``materialize`` ports the per-branch checkout (including
the ``failed_checkouts`` bookkeeping) verbatim from the old solve loop.
Tree routing is now just *one* implementation of the adaptation seam;
other operators (flat skill retrieval, SQL, bash-on-demand) drop in as
sibling classes without touching the harness.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from ...engine.versioning import VersionControl
from ...types import Task

if TYPE_CHECKING:
    from ...contract.workspace import AgentWorkspace
    from ...types import StrategyTree
    from .engine import NavigationEngine

logger = logging.getLogger(__name__)


class TreeRoutingAdaptation:
    """Agentic branch routing over a git-backed harness tree.

    ``select`` builds the routing context from the task and asks the
    navigator LLM for a branch; ``materialize`` checks that branch out
    into the solver workspace, falling back to ``main`` (and recording a
    failed checkout) if it cannot.
    """

    name = "tree_routing"

    def __init__(self, engine: NavigationEngine, tree: StrategyTree):
        self._engine = engine
        self._tree = tree

    def select(self, store_path: Path, task: Task) -> str:
        # Mirror the routing context the solve loop used to build inline.
        nav_lines = [f"Task ID: {task.id}"]
        for key in ("category", "event", "challenge", "year"):
            if key in task.metadata:
                nav_lines.append(f"{key}: {task.metadata[key]}")
        nav_lines.append(f"\n{task.input}")
        task_context = "\n".join(nav_lines)
        return self._engine.navigate(
            task_context, self._tree, workspace_root=store_path
        )

    def materialize(
        self, store_path: Path, key: str, workspace: AgentWorkspace
    ) -> None:
        vc = VersionControl(store_path)
        if key == "main":
            vc.checkout_branch("main")
            return
        try:
            vc.checkout_branch(key)
        except Exception as e:
            logger.warning("Branch %s checkout failed, using main: %s", key, e)
            b = self._tree.get_branch(key)
            if b is not None:
                b.failed_checkouts = getattr(b, "failed_checkouts", 0) + 1
            vc.checkout_branch("main")

    def prepare(self, workspace) -> None:
        # Routing reads the live git tree directly; nothing to pre-build.
        return None

    def project(self, task_id: str) -> dict | None:
        # Branch routing's granularity is the whole branch, not a per-task
        # item subset: the harness is realized by ``materialize`` (checkout).
        return None
