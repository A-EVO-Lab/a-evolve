"""Git-based version control for agent workspaces.

Ported from CodeDojo/swe-agent/swe_agent/evolve/state_repo.py and adapted
to work with the AgentWorkspace file system contract.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class VersionControl:
    """Manages git history on an agent workspace directory."""

    def __init__(self, workspace_root: str | Path):
        self.root = Path(workspace_root).resolve()

    def init(self) -> None:
        """Initialize a git repo in the workspace (idempotent).

        V2 forces the default branch to ``main`` so the navigation
        branch API (``create_branch``/``checkout_branch(\"main\")``) works
        on systems where git still defaults to ``master``.
        """
        if not (self.root / ".git").exists():
            logger.info("Initializing git repo at %s", self.root)
            self._git("init", "-b", "main")
            self._git("config", "user.email", "evolver@agent-evolve")
            self._git("config", "user.name", "Agent Evolve")

        # Only create the initial commit + evo-0 tag once. Repeated
        # calls to init() (from harness + navigation engine) must not
        # create extra empty commits or move the evo-0 tag.
        try:
            self._git("rev-parse", "evo-0")
            # Tag exists — repo already initialized, just stage any
            # new files without creating a new commit.
            self._git("add", "-A")
        except RuntimeError:
            # First init — create the initial commit and tag.
            self._git("add", "-A")
            try:
                self._git("commit", "--allow-empty", "-m", "Initial workspace state")
                self._git("tag", "evo-0")
                logger.info("Created initial commit with tag evo-0")
            except RuntimeError:
                pass

        # Ensure we're on 'main' (older git defaults to 'master' even when
        # ``-b main`` is unsupported — rename if so).
        try:
            current = self._git("rev-parse", "--abbrev-ref", "HEAD")
            if current == "master":
                self._git("branch", "-m", "master", "main")
        except RuntimeError:
            pass  # HEAD doesn't exist yet

    def commit(self, message: str, tag: str | None = None) -> bool:
        """Stage all changes and commit.  Returns True if a commit was created.

        Raises ``RuntimeError`` for real git failures (hook errors, lock
        files, config problems).  Returns False only for the benign
        "nothing to commit" case.
        """
        self._git("add", "-A")
        result = subprocess.run(
            ["git", "commit", "-m", message],
            capture_output=True, text=True, cwd=str(self.root),
        )
        if result.returncode == 0:
            logger.info("Committed: %s", message)
            committed = True
        elif "nothing to commit" in (result.stdout + result.stderr):
            logger.debug("Nothing to commit: %s", message)
            committed = False
        else:
            raise RuntimeError(
                f"git commit failed: {result.stderr.strip()}"
            )
        if tag:
            self._git("tag", "-f", tag)
            logger.debug("Tagged: %s", tag)
        return committed

    def rollback(self, ref: str = "HEAD~1") -> None:
        """Restore workspace content from *ref* as a NEW commit.

        Unlike ``git reset --hard``, this preserves the rejected version
        in git history so it can be inspected or reused later.

        Also removes files that were added after *ref* — ``git checkout
        ref -- .`` restores modified files but does not remove new files.
        """
        logger.info("Rolling back workspace to %s", ref)
        # Files at the target ref
        try:
            ref_files = set(
                self._git("ls-tree", "-r", "--name-only", ref).splitlines()
            )
        except RuntimeError:
            ref_files = set()
        # Files currently tracked
        current_files = set(
            self._git("ls-files").splitlines()
        )
        # Restore content from ref
        self._git("checkout", ref, "--", ".")
        # Remove files that didn't exist at ref
        for extra in sorted(current_files - ref_files):
            extra_path = self.root / extra
            if extra_path.exists():
                extra_path.unlink()
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

    def diff_from_head(self) -> str:
        """Return the diff of the most recent commit (HEAD~1..HEAD)."""
        try:
            return self._git("diff", "HEAD~1", "HEAD")
        except RuntimeError:
            return ""

    def diff_branch_from_main(self, branch: str) -> str:
        """Return the diff between main and *branch* tip."""
        try:
            return self._git("diff", "main", branch)
        except RuntimeError:
            return ""

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

    def checkout_branch_worktree(self, branch: str, dest: Path) -> None:
        """Create a working copy on *branch* (non-detached).

        Unlike :meth:`checkout_copy`, the worktree tracks the branch so
        commits advance its pointer.  Use :meth:`remove_copy` to clean up.
        """
        self._git("worktree", "add", str(dest), branch)

    def prune_worktrees(self) -> None:
        """Remove stale worktree metadata left by killed processes.

        Runs ``git worktree prune`` to clean up entries whose working
        directory no longer exists on disk, then forcibly removes any
        remaining worktrees whose paths match ``/tmp/mosaic-wt-*`` (the
        tempdir prefix used by the mosaic template).
        """
        import shutil
        self._git("worktree", "prune")
        try:
            output = self._git("worktree", "list", "--porcelain")
        except RuntimeError:
            return
        def _try_remove(wt_path_str: str) -> None:
            if "/tmp/mosaic-wt-" in wt_path_str and wt_path_str != str(self.root):
                try:
                    self._git("worktree", "remove", wt_path_str, "--force")
                    logger.info("Pruned stale worktree: %s", wt_path_str)
                except RuntimeError:
                    wt_path = Path(wt_path_str)
                    if wt_path.exists():
                        shutil.rmtree(wt_path, ignore_errors=True)
                    try:
                        self._git("worktree", "prune")
                    except RuntimeError:
                        pass

        current_wt = None
        for line in output.splitlines():
            if line.startswith("worktree "):
                if current_wt:
                    _try_remove(current_wt)
                current_wt = line[len("worktree "):]
            elif line == "" and current_wt:
                _try_remove(current_wt)
                current_wt = None
        if current_wt:
            _try_remove(current_wt)

    def release_branch_from_worktrees(self, branch: str) -> None:
        """Ensure *branch* is not locked by any worktree so it can be
        recreated or checked out.  Prunes stale worktree metadata first,
        then forcibly removes any worktree still tracking *branch*.
        """
        self.prune_worktrees()
        try:
            output = self._git("worktree", "list", "--porcelain")
        except RuntimeError:
            return
        current_wt = None
        current_branch = None
        def _flush():
            nonlocal current_wt, current_branch
            if current_branch == branch and current_wt and current_wt != str(self.root):
                try:
                    self._git("worktree", "remove", current_wt, "--force")
                except RuntimeError:
                    pass
                try:
                    self._git("worktree", "prune")
                except RuntimeError:
                    pass
            current_wt = None
            current_branch = None

        for line in output.splitlines():
            if line.startswith("worktree "):
                current_wt = line[len("worktree "):]
            elif line.startswith("branch refs/heads/"):
                current_branch = line[len("branch refs/heads/"):]
            elif line == "":
                _flush()
        # Flush the final record (porcelain output may lack trailing blank).
        _flush()

    # ── Branch operations (for --navigation strategy tree) ──────────

    @staticmethod
    def is_valid_ref_name(name: str) -> bool:
        """Return True if *name* is a legal git branch name.

        Delegates to ``git check-ref-format --branch`` which is the
        authoritative check (handles disallowed characters, ``..``,
        leading ``-``, leading/trailing dots, etc.).
        """
        if not name or not isinstance(name, str):
            return False
        # check-ref-format --branch enforces the "branch name"
        # restriction (e.g. no '@{', no '\\', …). It exits 0 for legal
        # names and non-zero otherwise.
        try:
            result = subprocess.run(
                ["git", "check-ref-format", "--branch", name],
                capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0

    def create_branch(self, name: str, from_ref: str = "HEAD") -> None:
        """Create a new branch from *from_ref* and switch to it.

        Raises ``ValueError`` for names that are not legal git refs and
        ``RuntimeError`` if the branch is somehow not present after
        ``git checkout -b`` (i.e. git silently didn't create it).
        """
        if not self.is_valid_ref_name(name):
            raise ValueError(f"Invalid git branch name: {name!r}")
        self._git("checkout", "-b", name, from_ref)
        if not self.branch_exists(name):
            raise RuntimeError(
                f"Branch {name!r} not present after create"
            )

    def checkout_branch(self, name: str) -> None:
        """Switch to an existing branch (or 'main')."""
        self._git("checkout", name)

    def merge_branch(self, source: str, target: str = "main") -> bool:
        """Merge *source* branch into *target*.

        Returns True if the merge produced a commit (or fast-forwarded),
        False if there was nothing to merge.  Raises ``RuntimeError`` on
        unresolvable conflicts (after aborting the merge).
        """
        current = self.get_current_branch()
        if current != target:
            self._git("checkout", target)
        pre_sha = self._git("rev-parse", "HEAD")
        try:
            self._git("merge", source, "-X", "theirs",
                       "-m", f"promote {source} into {target}")
            post_sha = self._git("rev-parse", "HEAD")
            return post_sha != pre_sha
        except RuntimeError as e:
            try:
                self._git("merge", "--abort")
            except RuntimeError:
                pass
            raise RuntimeError(f"Merge {source} into {target} failed: {e}") from e
        finally:
            if current != target:
                try:
                    self._git("checkout", current)
                except RuntimeError:
                    pass

    def rebase_branch(self, branch: str, onto: str = "main") -> bool:
        """Rebase *branch* onto *onto* so it inherits latest root changes.

        Returns True if rebase succeeded, False if aborted due to conflicts.
        """
        current = self.get_current_branch()
        self._git("checkout", branch)
        try:
            self._git("rebase", onto)
            return True
        except RuntimeError:
            self._git("rebase", "--abort")
            return False
        finally:
            self._git("checkout", current)

    def sync_paths_from(
        self, source: str, target: str, paths: list[str]
    ) -> bool:
        """Copy specific paths from *source* branch onto *target* branch.

        Used as fallback when rebase fails — brings main's tools/infra
        onto a branch without touching the branch's own prompts.
        Returns True if any files were synced.
        """
        current = self.get_current_branch()
        self._git("checkout", target)
        try:
            synced = False
            for path in paths:
                try:
                    self._git("checkout", source, "--", path)
                    synced = True
                except RuntimeError:
                    pass
            if synced:
                try:
                    self._git("commit", "-m",
                              f"sync {', '.join(paths)} from {source}")
                except RuntimeError:
                    pass
            return synced
        finally:
            self._git("checkout", current)

    def list_branches(self) -> list[str]:
        """Return all branch names except 'main' (or 'master')."""
        output = self._git("branch", "--format=%(refname:short)")
        return [b.strip() for b in output.splitlines()
                if b.strip() and b.strip() not in ("main", "master")]

    def delete_branch(self, name: str) -> None:
        """Delete a branch (must not be checked out)."""
        self._git("branch", "-D", name)

    def get_current_branch(self) -> str:
        """Return the name of the currently checked-out branch."""
        return self._git("rev-parse", "--abbrev-ref", "HEAD")

    def branch_exists(self, name: str) -> bool:
        """Check if a branch exists."""
        try:
            self._git("rev-parse", "--verify", name)
            return True
        except RuntimeError:
            return False

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
