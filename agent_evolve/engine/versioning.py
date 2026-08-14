"""Git-based version control for agent workspaces.

Ported from CodeDojo/swe-agent/swe_agent/evolve/state_repo.py and adapted
to work with the AgentWorkspace file system contract.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


class VersionControl:
    """Manages git history on an agent workspace directory."""

    def __init__(self, workspace_root: str | Path):
        self.root = Path(workspace_root).resolve()

    def init(self) -> None:
        """Initialize a git repo in the workspace (idempotent)."""
        if not (self.root / ".git").exists():
            self._git("init")
            self._git("config", "user.email", "evolver@agent-evolve")
            self._git("config", "user.name", "Agent Evolve")

        self._git("add", "-A")
        try:
            self._git("commit", "-m", "Initial workspace state")
            self._git("tag", "evo-0")
        except RuntimeError:
            pass  # already committed

    def commit(self, message: str, tag: str | None = None) -> None:
        self._git("add", "-A")
        try:
            self._git("commit", "-m", message)
        except RuntimeError:
            pass  # nothing to commit
        if tag:
            self._git("tag", "-f", tag)

    def rollback(self, ref: str = "HEAD~1") -> None:
        """Restore workspace content from *ref* as a NEW commit.

        Unlike ``git reset --hard``, this preserves the rejected version
        in git history so it can be inspected or reused later.
        """
        self._git("checkout", ref, "--", ".")
        self._git("add", "-A")
        try:
            self._git("commit", "-m", f"rollback to {ref}")
        except RuntimeError:
            pass  # nothing changed

    def rollback_to_tag(self, tag: str) -> None:
        """Restore workspace to the state at *tag* (history preserved)."""
        self.rollback(tag)

    def get_diff(self, from_ref: str = "HEAD~1", to_ref: str = "HEAD") -> str:
        return self._git("diff", from_ref, to_ref)

    def get_diff_stat(self, from_ref: str = "HEAD~1", to_ref: str = "HEAD") -> str:
        return self._git("diff", "--stat", from_ref, to_ref)

    def get_log(self, n: int = 20) -> str:
        return self._git("log", "--oneline", f"-{n}")

    def list_tags(self) -> list[str]:
        output = self._git("tag", "-l", "evo-*", "--sort=-version:refname")
        return [t.strip() for t in output.splitlines() if t.strip()]

    def show_file_at(self, ref: str, filepath: str) -> str:
        return self._git("show", f"{ref}:{filepath}")

    def checkout_copy(self, ref: str, dest: Path) -> None:
        """Create a separate working copy of the workspace at *ref*.

        Uses ``git worktree`` so the copy shares the object store but has
        its own working tree.  Use :meth:`remove_copy` to clean up.
        """
        self._git("worktree", "add", "--detach", str(dest), ref)

    def remove_copy(self, dest: Path) -> None:
        """Remove a working copy created by :meth:`checkout_copy`."""
        self._git("worktree", "remove", str(dest), "--force")

    def _git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            cwd=str(self.root),
        )
        if result.returncode != 0 and "nothing to commit" not in result.stderr:
            stderr = result.stderr.strip()
            if stderr:
                raise RuntimeError(f"git {' '.join(args)}: {stderr}")
        return result.stdout.strip()
