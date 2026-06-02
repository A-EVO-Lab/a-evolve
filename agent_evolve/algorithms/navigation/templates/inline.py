"""Inline evolution template -- single sandboxed LLM call with git access.

The evolver LLM is given full git access via its sandbox; it mutates the
workspace (and may create branches) in place. Post-hoc we detect the
mutation, commit main, then discover + register any new branches.

Template-specific prompt (``build_branching_section``) lives here so
that moving or replacing the template is a single-file operation.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .._evolver_prompts import build_evolution_prompt
from ....types import BranchInfo
from .base import EvolutionTemplate
from ._evolution_ops import (
    detect_mutations,
    disabled_layers,
    snapshot_workspace,
)

if TYPE_CHECKING:
    from ....contract.workspace import AgentWorkspace
    from ....engine.versioning import VersionControl
    from ....types import StrategyTree
    from ..engine import NavigationEngine

logger = logging.getLogger(__name__)


def build_branching_section(
    tree: Any,
    batch_results: list[dict],
) -> str:
    """Build the branching stanza appended to the inline evolution prompt.

    Describes existing branches + their stats and instructs the evolver
    to create/evolve branches directly via ``workspace_bash`` in the
    sandbox.  Used only in inline mode.
    """
    branch_lines = []
    if hasattr(tree, "branches") and tree.branches:
        for b in tree.branches:
            pct = (b.total_passed / b.total_tasks * 100) if b.total_tasks > 0 else 0
            branch_lines.append(
                f"- **{b.name}**: {b.description or 'no description'} "
                f"({b.total_passed}/{b.total_tasks} passed, {pct:.0f}%)"
            )
    else:
        branch_lines.append("(no branches yet — only main)")

    # Reveal-aware counts: a record with no "success" key came through
    # the reveal gate with its label withheld (trajectory_only on, or
    # temporal_reveal holding the label back until resolution).  Those
    # are *pending*, not *failed* — report them separately.
    n_tasks = len(batch_results)
    revealed = [r for r in batch_results if "success" in r]
    n_success = sum(1 for r in revealed if r.get("success"))
    n_fail = len(revealed) - n_success
    n_pending = n_tasks - len(revealed)
    if n_pending == n_tasks:
        batch_summary = f"{n_tasks} tasks this cycle (labels withheld — infer from behaviour)."
    elif n_pending:
        batch_summary = (
            f"{n_tasks} tasks this cycle ({n_success} passed, "
            f"{n_fail} failed, {n_pending} pending reveal)."
        )
    else:
        batch_summary = f"{n_tasks} tasks this cycle ({n_success} passed, {n_fail} failed)."

    return f"""

## Branch-Aware Evolution

This workspace is version-controlled as a git tree with a main branch
and optional specialized branches. You are currently evolving **main**.
You have full git access via workspace_bash.

### Current Strategy Tree

{chr(10).join(branch_lines)}

### Batch Summary

{batch_summary}

### Exploring Existing Branches

Before deciding whether to create new branches or update existing ones,
explore what each branch contains:

```bash
# See what's different on a branch vs main
git diff main..branch/<name> --stat
git show branch/<name>:prompts/system.md
git show branch/<name>:skills/       # list skills on the branch

# See recent branch evolution history
git log --oneline main..branch/<name>
```

This helps you decide whether an existing branch already handles a pattern
you've identified, or whether it needs updating.

### Branching Instructions

After making your workspace changes on main, analyze the batch results for
evidence of **distribution shift** — patterns where tasks require fundamentally
different techniques that would hurt other tasks if applied globally.

If you identify such patterns, create or update branches directly using git:

1. **Commit your main changes first:**
   ```bash
   git add -A && git commit -m "main: <summary of stationary improvements>"
   ```

2. **Create a new branch** or **update an existing one:**

   To create a new branch:
   ```bash
   git checkout -b branch/<descriptive-regime-name> main
   ```

   To update an existing branch:
   ```bash
   git checkout branch/<existing-branch-name>
   ```

   Use lowercase, hyphenated names describing the strategic shift
   (e.g., `branch/binary-analysis`, `branch/high-uncertainty`).

3. **Evolve the branch workspace** — edit prompts, skills, memory, tools
   to specialize for this regime. Changes here should be tailored — do NOT
   generalize across all task types.

4. **Create or update README.md** at the workspace root describing:
   - **Purpose:** what regime/property this branch handles
   - **Strategy:** the approach and key techniques
   - **Key artifacts:** which files were added or modified and why

   Example:
   ```markdown
   # Branch: branch/high-uncertainty

   ## Purpose
   Markets with price between 0.4-0.6 where evidence is weak.

   ## Strategy
   - Calibration heuristic for uncertain predictions
   - Hedging when evidence is contradictory

   ## Key Artifacts
   - skills/calibration_heuristic/SKILL.md
   - Modified prompts/system.md with uncertainty framing
   ```

5. **Commit the branch changes:**
   ```bash
   git add -A && git commit -m "branch/<name>: <summary of specialized changes>"
   ```

6. **Return to main** when done:
   ```bash
   git checkout main
   ```

Repeat steps 2-6 for each branch you want to create or update.

Guidelines:
- Only create branches for genuine non-stationary shifts, not one-off failures.
- Name branches after the strategic shift, not the topic.
- If all issues are stationary (domain-generalizable), just evolve main — no
  branches needed.
- Always verify branch operations succeeded: `git branch --list`
- Always return to main before finishing.
"""


class InlineTemplate(EvolutionTemplate):
    """Inline evolution: single LLM call with git access."""

    def __init__(self, engine: NavigationEngine):
        self.engine = engine

    @property
    def name(self) -> str:
        return "inline"

    def execute(
        self,
        vc: VersionControl,
        solver_workspace: AgentWorkspace,
        batch_results: list[dict[str, Any]],
        tree: StrategyTree,
        evo_number: int,
        routing_log_path: Path | None,
    ) -> dict[str, Any]:
        ws = solver_workspace
        cfg = self.engine.config

        # 1. Snapshot branches before the LLM runs.
        list_before = sorted(vc.list_branches())

        # 2. Ensure HEAD on main (strict — raises on failure).
        self._ensure_main(vc, safe=False)

        # 3. Snapshot workspace state (for diffing) + capture drafts.
        snapshot, drafts = snapshot_workspace(ws)

        # 4. Build the evolution prompt, 5. append the branching stanza.
        prompt = build_evolution_prompt(
            ws, batch_results, drafts, evo_number,
            evolve_prompts=cfg.evolve_prompts,
            evolve_skills=cfg.evolve_skills,
            evolve_memory=cfg.evolve_memory,
            evolve_tools=cfg.evolve_tools,
            evolve_infra=cfg.evolve_infra,
            include_patches=cfg.evolver_include_patches,
            trajectory_only=cfg.trajectory_only,
        )
        prompt += build_branching_section(tree, batch_results)

        # 6. Run the sandboxed LLM (protecting layers whose evolution is off).
        with ws.protect(disabled_layers(cfg)):
            llm_response = self.engine._run_llm(prompt, ws.root) or {}

        # 7. Clear drafts (before diff — inline-specific ordering).
        ws.clear_drafts()

        # 8. Safe-ensure main (the evolver may have left us on a branch).
        self._ensure_main(vc, safe=True)

        # 9. Detect mutations.
        report = detect_mutations(ws, snapshot)
        main_mutated = bool(report.get("mutated", False))

        # 10. Commit main (always, even if no-op — matches original).
        vc.commit(
            message=f"evo-{evo_number}-main: inline evolution",
            tag=f"evo-{evo_number}-main",
        )

        # 11-12. Discover branches the evolver created + register them.
        new_branches = sorted(set(vc.list_branches()) - set(list_before))
        self._register_branches(vc, tree, new_branches, evo_number)

        # 13. Tag branch heads that advanced this cycle.
        self._tag_branch_heads(vc, evo_number)

        mutated = main_mutated or bool(new_branches)
        trajectory = [{
            "step": "evolve_inline",
            "mutated": main_mutated,
            "new_branches": sorted(new_branches),
            "total_branches": sorted(vc.list_branches()),
            "conversation": llm_response.get("conversation", []),
        }]

        if mutated:
            logger.info(
                "Inline evolution: main_mutated=%s, new_branches=%s",
                main_mutated, sorted(new_branches),
            )

        return {
            "evo_number": evo_number,
            "mutated": mutated,
            "plan": {},
            "branches": tree.branch_names(),
            "trajectory": trajectory,
        }

    # ── git helpers (ported from activity/nodes/git.py) ──────────

    @staticmethod
    def _ensure_main(vc: VersionControl, *, safe: bool) -> None:
        """Ensure HEAD is on main. When ``safe``, swallow errors + retry."""
        try:
            if vc.get_current_branch() != "main":
                if safe:
                    logger.warning("Evolver left workspace off main, returning")
                vc.checkout_branch("main")
        except Exception:
            if not safe:
                raise
            try:
                vc.checkout_branch("main")
            except Exception:
                pass

    @staticmethod
    def _register_branches(
        vc: VersionControl,
        tree: StrategyTree,
        new_branches: list[str],
        evo_number: int,
    ) -> None:
        """Register newly-discovered branches into the StrategyTree.

        Prefer the README's first non-heading line as the description;
        fall back to the branch's first commit message.
        """
        known = {b.name for b in tree.branches}
        for branch_name in new_branches:
            if branch_name in known:
                continue
            description = ""
            try:
                readme = vc.show_file_at(branch_name, "README.md")
                for line in readme.splitlines():
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#"):
                        description = stripped[:200]
                        break
            except Exception:
                pass
            if not description:
                try:
                    description = vc._git(
                        "log", "--format=%s", f"main..{branch_name}", "-1"
                    ).strip()
                except Exception:
                    pass
            tree.branches.append(BranchInfo(
                name=branch_name,
                created_at_cycle=evo_number,
                description=description,
            ))
            logger.info("Discovered new branch: %s", branch_name)

    @staticmethod
    def _tag_branch_heads(vc: VersionControl, evo_number: int) -> None:
        """Tag every branch head that advanced since ``pre-nav-evo-{n}``."""
        for branch_name in vc.list_branches():
            tag_suffix = branch_name.replace("/", "-")
            try:
                diff = vc._git(
                    "log", "--oneline",
                    f"pre-nav-evo-{evo_number}..{branch_name}", "-1",
                )
                if diff.strip():
                    vc._git("tag", "-f",
                            f"evo-{evo_number}-{tag_suffix}", branch_name)
            except Exception:
                pass
