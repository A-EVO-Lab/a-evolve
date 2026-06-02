"""NavigationEngine -- git branching + task routing + pluggable evolution.

Navigation = branching.  Git branches isolate strategies; the navigator
routes each task to the best-equipped branch.  Evolution execution is
delegated to a pluggable ``EvolutionTemplate``.

Usage:
    NavigationEngine(config)                              # inline (default)
    NavigationEngine(config, mode="orchestrated")         # plan-driven
    NavigationEngine(config, template=MyCustomTemplate)   # custom

Inherits from AEvolveEngine:
  - evolve()    -- standard workspace mutation (standalone mode)
  - _run_llm()  -- sandboxed LLM execution
  - step()      -- EvolutionEngine interface
  - config, llm -- shared state
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...contract.workspace import AgentWorkspace
from ...engine.versioning import VersionControl
from ...types import StrategyTree
from ._evolver_engine import AEvolveEngine
from .prompts import NAVIGATE_SYSTEM_PROMPT, build_navigate_prompt

if TYPE_CHECKING:
    from .templates.base import EvolutionTemplate

logger = logging.getLogger(__name__)


class NavigationEngine(AEvolveEngine):
    """Git branching + task routing + pluggable evolution template.

    Navigation core (always available):
      - navigate()                -- F_Navigate: route task to branch (LLM)
      - _read_branch_leaves()    -- read workspace content from git branches
      - _resolve_branch_name()   -- normalize LLM branch name output
      - _format_tree()           -- ASCII visualization of strategy tree

    Evolution modes:
      - mode="inline"        (default) — evolver decides branches in one
                                          sandboxed call (InlineTemplate)
      - mode="orchestrated"             — analyst → main → branches
                                          (OrchestratedTemplate)
      - template=<instance>             — any EvolutionTemplate subclass
    """

    def __init__(
        self,
        config: Any,
        llm: Any | None = None,
        mode: str = "inline",
        template: EvolutionTemplate | None = None,
    ):
        super().__init__(config, llm)
        self.mode = mode
        # Optional Observer — when set, the engine applies the reveal gate
        # (trajectory_only + temporal_reveal) to batch_results before
        # handing them to any template.  Set by the harness after
        # construction via ``engine.observer = observer``.
        self.observer: Any | None = None
        if template is not None:
            self._template = template
        elif mode == "orchestrated":
            from .templates.orchestrated import OrchestratedTemplate
            self._template = OrchestratedTemplate(self)
        elif mode == "inline":
            from .templates.inline import InlineTemplate
            self._template = InlineTemplate(self)
        elif mode == "structured_navigation":
            from .templates.structured_navigation import StructuredNavigationTemplate
            self._template = StructuredNavigationTemplate(self)
        else:
            raise ValueError(
                f"Unknown evolution mode {mode!r} "
                f"(expected 'inline', 'orchestrated', or "
                f"'structured_navigation', or pass template=)"
            )

    # ── Visualization ───────────────────────────────────────────

    @staticmethod
    def _format_tree(tree: StrategyTree, vc: VersionControl, evo_number: int) -> str:
        """Render the strategy tree as ASCII art for logging."""
        try:
            tags = vc.list_tags()
            n_evo = len([t for t in tags if t.startswith("evo-")])
        except Exception:
            n_evo = evo_number + 1

        n_show = min(n_evo, 10)
        if n_show == 0:
            main_dots = "●"
        else:
            main_dots = "──".join(["○"] * n_show) + "──●"
        if n_evo > 10:
            main_dots = "○──…" + main_dots

        lines = [
            "",
            f"  main:  {main_dots}  (evo-{evo_number})",
        ]

        if not tree.branches:
            lines.append("         (no branches)")
        else:
            fork_col = len("  main:  ") + n_show * 3
            for b in tree.branches:
                try:
                    br_log = vc._git("log", "--oneline", f"main..{b.name}")
                    br_n = len(br_log.strip().splitlines()) if br_log.strip() else 0
                except Exception:
                    br_n = 1
                br_n = max(br_n, 1)
                br_dots = "──".join(["○"] * min(br_n, 6))

                stats = ""
                if b.total_tasks > 0:
                    pct = b.total_passed / b.total_tasks * 100
                    stats = f"  ({b.total_passed}/{b.total_tasks}, {pct:.0f}%)"

                pad = " " * fork_col
                lines.append(f"{pad}╲")
                lines.append(f"  {b.name}:  {br_dots}{stats}")

        lines.append("")
        return "\n".join(lines)

    # ── F_Navigate ──────────────────────────────────────────────

    def navigate(
        self,
        task_description: str,
        tree: StrategyTree,
        workspace_root: Path | None = None,
    ) -> str:
        """F_Navigate: examine git tree leaves and pick the best branch.

        Reads actual workspace content (system prompt, skills, tools) from
        each branch so the LLM can make an informed routing decision.
        Returns branch name (e.g., "main" or "branch/algebraic-reasoning").

        The returned name is verified to exist in git (not just in the
        in-memory tree), so callers can checkout it without a fallback
        race. Routing falls back to "main" when the navigator's confidence
        is below ``config.branch_confidence_threshold``.
        """
        vc = VersionControl(workspace_root) if workspace_root else None

        if not tree.branches:
            return "main"

        # Only offer the LLM branches that actually exist in git and
        # haven't failed checkout too many times.
        viable = self._viable_branches(tree, vc)
        if not viable:
            return "main"

        # Confidence threshold from config (default 0.7).
        threshold = float(getattr(self.config, "branch_confidence_threshold", 0.7))
        if threshold < 0:
            # Negative means "never branch" — always route to main.
            return "main"

        branch_summaries = self._read_branch_leaves(tree, workspace_root, viable)
        prompt = build_navigate_prompt(task_description, branch_summaries)
        try:
            from ...llm.bedrock import BedrockProvider
            if isinstance(self.llm, BedrockProvider):
                response = self.llm.converse_loop(
                    system_prompt=NAVIGATE_SYSTEM_PROMPT,
                    user_message=prompt,
                    tools=[],
                    tool_executor={},
                    max_tokens=256,
                    temperature=0.0,
                )
                result = json.loads(response.content)
                raw = result.get("branch", "main")
                conf = float(result.get("confidence", 0.0))
                resolved = self._resolve_branch_name(raw, tree, vc=vc)
                # If router returns a non-main branch with confidence below
                # threshold, fall back to main. main is always allowed
                # regardless of confidence.
                if resolved != "main" and conf < threshold:
                    logger.info(
                        "Routing %s → main (confidence %.2f < %.2f)",
                        raw, conf, threshold,
                    )
                    return "main"
                return resolved
        except Exception:
            pass
        return "main"

    @staticmethod
    def _viable_branches(
        tree: StrategyTree, vc: VersionControl | None
    ) -> list[str]:
        """Return branch names safe to route to this cycle.

        Filters out (a) branches with too many recent checkout failures
        and (b) branches present in-memory but missing from git.
        """
        viable: list[str] = []
        for b in tree.branches:
            if getattr(b, "failed_checkouts", 0) > 2:
                continue
            if vc is not None and not vc.branch_exists(b.name):
                continue
            viable.append(b.name)
        return viable

    @staticmethod
    def _resolve_branch_name(
        raw: str,
        tree: StrategyTree,
        vc: VersionControl | None = None,
    ) -> str:
        """Normalize LLM-returned branch name to match actual git branch.

        The navigator LLM may return bare names like "turn-budget-convergence"
        instead of the full git name "branch/turn-budget-convergence".
        Match against known branch names in the strategy tree, then
        verify that the resolved name actually exists in git before
        returning it (if ``vc`` is provided).
        """
        if raw == "main":
            return "main"
        known = {b.name for b in tree.branches}

        candidate: str | None = None
        if raw in known:
            candidate = raw
        elif f"branch/{raw}" in known:
            candidate = f"branch/{raw}"
        elif raw.startswith("branch/"):
            stripped = raw[len("branch/"):]
            for name in known:
                if name.endswith(stripped):
                    candidate = name
                    break
        if candidate is None:
            for name in known:
                if name.endswith(f"/{raw}") or name == raw:
                    candidate = name
                    break

        if candidate is None:
            logger.warning(
                "Navigator returned unknown branch %r, falling back to main", raw
            )
            return "main"

        if vc is not None and not vc.branch_exists(candidate):
            logger.warning(
                "Branch %r exists in strategy tree but not in git — "
                "falling back to main", candidate,
            )
            return "main"

        return candidate

    @staticmethod
    def _sanitize_branch_name(raw: str) -> str | None:
        """Coerce an LLM-proposed branch name into a legal git ref.

        The LLM in ``_analyze_and_plan`` tends to emit bare, task-shaped
        names (e.g. ``"Multi-Outcome Markets"``) that fail git's
        ref-format checks. This helper lowercases, replaces whitespace
        with dashes, strips illegal characters, collapses consecutive
        dots/dashes, and enforces a canonical ``branch/<slug>`` prefix.

        Returns None if the sanitised name is empty or degenerate.
        """
        import re

        if not raw or not isinstance(raw, str):
            return None
        s = raw.strip().lower()
        if s == "main":
            return "main"
        if s.startswith("branch/"):
            s = s[len("branch/"):]
        s = re.sub(r"\s+", "-", s)
        s = re.sub(r"[^a-z0-9._/\-]", "", s)
        s = re.sub(r"\.{2,}", ".", s)
        s = re.sub(r"-{2,}", "-", s)
        s = s.strip("-./")
        if not s or s.startswith(".") or s.endswith("."):
            return None
        return f"branch/{s}"

    def _read_branch_leaves(
        self,
        tree: StrategyTree,
        workspace_root: Path | None,
        viable: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Read workspace content from each branch (git tree leaves).

        For each branch (including main), reads the system prompt, skill
        names, and tool registry so the navigator can compare strategies.

        If ``viable`` is provided, only those branch names (plus ``main``)
        are presented to the caller — this lets the navigator ignore
        branches that are missing from git or have failed checkouts.
        """
        if viable is not None:
            allowed = set(viable) | {"main"}
            tree_branches = [b for b in tree.branches if b.name in allowed and b.name != "main"]
        else:
            tree_branches = [b for b in tree.branches if b.name != "main"]
        branch_names = ["main"] + [b.name for b in tree_branches]
        descriptions = {"main": "general-purpose root strategy"}
        for b in tree.branches:
            descriptions[b.name] = b.description

        if not workspace_root:
            return [
                {"name": n, "description": descriptions.get(n, "")}
                for n in branch_names
            ]

        vc = VersionControl(workspace_root)
        summaries = []
        for name in branch_names:
            summary: dict[str, Any] = {
                "name": name,
                "description": descriptions.get(name, ""),
            }
            try:
                summary["readme"] = vc.show_file_at(name, "README.md")
            except Exception:
                summary["readme"] = ""
            summaries.append(summary)
        return summaries

    # ── Evolution with Navigation ──────────────────────────────

    def evolve_with_navigation(
        self,
        solver_workspace: AgentWorkspace,
        batch_results: list[dict[str, Any]],
        tree: StrategyTree,
        evo_number: int = 0,
        routing_log_path: Path | None = None,
    ) -> dict[str, Any]:
        """Evolve the workspace with navigation-aware branching.

        Delegates to the active evolution template (inline or orchestrated).
        """
        vc = VersionControl(solver_workspace.root)
        vc.init()

        # Pre-evolution snapshot
        vc.commit(
            message=f"pre-nav-evo-{evo_number}: snapshot before navigation evolution",
            tag=f"pre-nav-evo-{evo_number}",
        )

        # Apply the reveal gate once, centrally, before any template sees
        # the batch.  Templates then treat records with missing
        # success/score as "pending" (not "failed") — see
        # build_branching_section / build_planner_prompt.
        gated_batch = (
            self.observer.filter_batch_for_evolver(batch_results)
            if self.observer is not None else batch_results
        )

        result = self._template.execute(
            vc, solver_workspace, gated_batch,
            tree, evo_number, routing_log_path,
        )

        tree_viz = self._format_tree(tree, vc, evo_number)
        logger.info("Strategy tree after cycle %d:\n%s", evo_number, tree_viz)

        return result
