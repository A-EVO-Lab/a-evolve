"""Pluggable solve-time *adaptation*.

Adaptation is the solve-time half of the harness lifecycle: given the
storage the evolver built (a path — today a git repo, tomorrow a SQL
file, vector index, or graph dir) and an incoming task, it projects that
unbounded store into the budget-bounded harness the solver actually runs
with. This is the operational form of the ``L_adapt`` term in the paper:
*organize the space at evolution time, select from it cheaply per task.*

The interface is deliberately tiny and split into two phases so it maps
onto the existing solve loop (route all tasks in parallel, then realize
one workspace per group):

  - ``select``      — per-task, READ-ONLY, parallel-safe. Returns an
                      opaque *selection key* (a grouping label). Tasks
                      sharing a key share one materialized workspace.
  - ``materialize`` — once per key. Mutates the solver workspace in place
                      to realize the harness for that key. All storage-
                      specific logic (git checkout, skill injection, tool
                      install, SQL query) lives here.

Two invariants every implementation must uphold:

  1. **No feedback.** Neither method receives reward/score/success. The
     selector conditions on the task only — never on outcomes. (See the
     evolver-privacy rule: ground truth must not leak into selection.)
  2. **Capacity.** ``materialize`` must leave the workspace within the
     harness capacity budget ``K``.

A concrete operator may put all its work in one method and stub the
other (e.g. a whole-store operator never selects; a pure-prompt operator
never materializes new files).
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from ...contract.workspace import AgentWorkspace
from ...types import Task


@runtime_checkable
class Adaptation(Protocol):
    """Solve-time projection of a storage path into a task-specific harness."""

    name: str

    def select(self, store_path: Path, task: Task) -> str:
        """Pick a selection key for ``task`` (read-only, no side effects)."""
        ...

    def materialize(
        self, store_path: Path, key: str, workspace: AgentWorkspace
    ) -> None:
        """Realize the harness for ``key`` by mutating ``workspace`` in place."""
        ...

    def prepare(self, workspace: AgentWorkspace) -> None:
        """Per-batch setup against the current store (default: no-op).

        Called once per batch before ``select``. Operators that maintain a
        derived structure over the evolved store (e.g. an embedding index)
        rebuild it here; routing/whole-store operators leave it a no-op.
        """
        ...

    def project(self, task_id: str) -> dict | None:
        """Per-task harness filter, or ``None`` to use the full harness.

        Returns ``{"skills": [...], "tools": [...], "memory": [...]}`` for
        operators that select a per-task subset (retrieval); ``None`` for
        operators whose granularity is the whole materialized workspace
        (whole-store, branch routing) — the default.
        """
        ...


class WholeStoreAdaptation:
    """Trivial operator: every task uses the store as-is (no routing).

    ``select`` always returns ``"main"`` and ``materialize`` is a no-op,
    so the solver runs against whatever the evolver last built. This is
    the default whenever navigation/routing is disabled, and the identity
    element of the adaptation interface — a behavioral no-op relative to
    the pre-refactor non-navigation path.
    """

    name = "whole_store"

    def select(self, store_path: Path, task: Task) -> str:  # noqa: ARG002
        return "main"

    def materialize(
        self, store_path: Path, key: str, workspace: AgentWorkspace
    ) -> None:
        # Nothing to do: the working tree already holds the harness.
        return None

    def prepare(self, workspace: AgentWorkspace) -> None:
        # No derived structure to build.
        return None

    def project(self, task_id: str) -> dict | None:
        # No per-task filtering: every task uses the whole store.
        return None
