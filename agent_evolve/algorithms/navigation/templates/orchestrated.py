"""Orchestrated (plan-driven) evolution template.

Two-agent collaboration:

  * **Planner** (one LLM call per cycle) — reads the batch, freely
    decides how to split the work, emits a JSON plan with one or more
    ``assignments``.  Each assignment nominates a target (``"main"`` or
    a branch name), a short ``focus`` label, and a free-form
    ``workload`` — the instructions the evolver will receive.

  * **Evolver** (one LLM call per assignment) — a single evolver agent
    invoked once per assignment the planner produced.  Receives the
    assignment's workload + focus as context, then runs the standard
    snapshot → prompt → LLM → diff → clear-drafts cycle in the sandbox.

Two planner prompts are shipped:

  * ``PLANNER_SYSTEM_PROMPT_NAV``  — used when navigation is enabled.
    The planner may nominate ``branch/<name>`` targets (creating or
    reusing branches) in addition to main.
  * ``PLANNER_SYSTEM_PROMPT_FLAT`` — used when navigation is disabled.
    The planner may only decompose the work into focused passes on
    ``main``; no branch vocabulary.

The shipped ``OrchestratedTemplate`` picks the prompt based on
``config.navigation_enabled``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...aevolve.prompts import build_evolution_prompt
from ....types import BranchInfo
from .base import EvolutionTemplate
from ._evolution_ops import (
    detect_mutations,
    disabled_layers,
    prepend_plan_context,
    snapshot_workspace,
)

if TYPE_CHECKING:
    from ....contract.workspace import AgentWorkspace
    from ....engine.versioning import VersionControl
    from ....types import StrategyTree
    from ..engine import NavigationEngine

logger = logging.getLogger(__name__)


# ── Template-owned prompts ───────────────────────────────────────────


PLANNER_SYSTEM_PROMPT_NAV = """\
You are the planner in a multi-agent evolution system.

The agent solves tasks that arrive in batches. Strategy-relevant properties of
tasks may shift over time — not topic labels, but the techniques required,
environment constraints, information availability, and decision structure.

The agent's workspace is version-controlled as a git tree:
- **main** branch: general-purpose strategy
- **branch/<name>**: specialized strategies for specific problem regimes

You have full freedom to decide how to break the evolution work down this
cycle. After analysing the batch you emit a plan — a list of **assignments**.
Each assignment is handed to the evolver, which executes it in a sandboxed
workspace.

## How to decide assignments

Look at the batch behaviour and the current tree. For each distinct gap or
opportunity you see, decide:

1. **Target** — where does this change belong?
   - ``"main"``: improvements that apply equally to all task types.
   - ``"branch/<descriptive-name>"``: when you observe a cluster of tasks
     that would benefit from a specialized approach different from main's
     general strategy (e.g., different search depth, reasoning style, or
     domain knowledge).  Pick an existing branch or propose a new one.

2. **Focus** — a short label (2-6 words) naming the concrete area of the
   workspace this assignment is about.  Examples:
     "search API skill", "verification tool registry",
     "uncertainty calibration prompt", "binary-analysis skills",
     "memory cleanup", "infra pipeline".

3. **Workload** — explicit instructions to the evolver.  Tell the evolver
   *what to change* and *why*, e.g.:
     "The search skill is returning stale results. Add a freshness check
     that re-queries if the top result's timestamp is older than 6 months.
     Update skills/search/SKILL.md and memory/search.jsonl with the new
     heuristic."

You may produce:
  - zero assignments (if the batch looks clean and nothing needs changing),
  - one assignment (e.g. just a focused main tweak),
  - several assignments on main (different focuses, same branch),
  - one-per-branch plus main,
  - or any mix you judge appropriate.

## Output

Produce a JSON plan only.  No surrounding prose.

{
  "summary": "One sentence describing what you concluded from the batch.",
  "assignments": [
    {
      "target": "main" | "branch/<name>",
      "focus":  "short label",
      "workload": "free-form instructions for the evolver (a paragraph)",
      "task_ids": ["ids of tasks that informed this assignment"]
    }
  ]
}

Rules:
- Use ``"main"`` verbatim for the main branch.
- Branch names MUST start with ``branch/`` and use lowercase hyphenated
  slugs (``branch/binary-analysis``).
- Create a branch when you see a task cluster (2+ tasks) that needs a
  different strategy from the majority. Do NOT create a branch that would
  cover all tasks — that's just main.
- You may assign multiple focused workloads to the same target in one cycle.
- If no changes are warranted, return ``"assignments": []``.
- After emitting the JSON plan, do NOT make further tool calls.

Workspace access
----------------
You have ``workspace_bash`` to inspect trajectories and workspace state:
- ``/trajectories/batch_NNNN/index.txt`` — lightweight per-task summary
  (task_id, category, year, turns, elapsed, outcome). READ THIS FIRST.
- ``/solver_workspace/`` — current evolved workspace (prompts, skills, tools)
- ``/evolver_workspace/strategy_tree.md`` — branch descriptions + stats

NEVER read full trajectory_*.json files with cat — they are 500+ lines
and will crash your context window. If you need detail on a specific task,
use: head -50 /trajectories/batch_NNNN/trajectory_<id>.json

Start by reading index.txt to identify regime patterns (category clusters,
turn-count distributions, pass/fail by type).

Reading the batch
-----------------
Each task entry may include a ``status`` field:

- ``status: cut_off`` + ``cut_off_reason: batch_deadline`` — the task's
  worker was cancelled when the batch ran past its wall-clock deadline.
  ``turns`` reflects real progress made before cancellation (not zero).
- ``status: cut_off`` + ``cut_off_reason: per_task_timeout`` — the
  individual task exceeded the per-task wall-clock budget.
- No ``status`` field — task finished within its time budget
  (successful or not depending on benchmark logic).

Cut-off tasks do NOT mean the agent failed to start.  They mean the
agent was doing work but the wall-clock ran out.
"""


PLANNER_SYSTEM_PROMPT_FLAT = """\
You are the planner in a multi-agent evolution system.

The agent solves tasks that arrive in batches. Your job is to read the latest
batch and decompose the evolution work into one or more focused assignments.
There is no branching in this mode — every assignment targets the ``main``
workspace.

## How to decide assignments

For each distinct gap or opportunity the batch reveals, emit one assignment.

1. **Target** — always ``"main"``.

2. **Focus** — a short label (2-6 words) naming the concrete area of the
   workspace this assignment is about.  Examples:
     "search API skill", "verification tool registry",
     "uncertainty calibration prompt", "output parser",
     "memory cleanup", "infra pipeline".

3. **Workload** — explicit instructions to the evolver.  Tell the evolver
   *what to change* and *why*, not just a problem description.  Example:
     "The output parser fails on multi-line numeric answers. Extend
      skills/parse/SKILL.md with a regex that matches lines ending in a
      digit, and add a fallback to memory/parsing.jsonl."

You may produce:
  - zero assignments (if nothing needs changing),
  - one focused assignment,
  - several assignments covering different focuses on main.

Each assignment becomes one sandboxed LLM call, so split work only when
the focuses are genuinely independent — otherwise combine into one.

## Output

Produce a JSON plan only.  No surrounding prose.

{
  "summary": "One sentence describing what you concluded from the batch.",
  "assignments": [
    {
      "target": "main",
      "focus":  "short label",
      "workload": "free-form instructions for the evolver (a paragraph)",
      "task_ids": ["ids of tasks that informed this assignment"]
    }
  ]
}

Rules:
- ``target`` MUST be ``"main"`` — no branches in flat mode.
- If no changes are warranted, return ``"assignments": []``.
- Output ONLY the JSON plan.

Reading the batch
-----------------
Each task entry may include a ``status`` field:

- ``status: cut_off`` + ``cut_off_reason: batch_deadline`` — the task's
  worker was cancelled when the batch ran past its wall-clock deadline.
  ``turns`` reflects real progress made before cancellation (not zero).
- ``status: cut_off`` + ``cut_off_reason: per_task_timeout`` — the
  individual task exceeded the per-task wall-clock budget.
- No ``status`` field — task finished within its time budget.

Cut-off tasks do NOT mean the agent failed to start.  They mean the
agent was doing work but the wall-clock ran out.
"""


def build_planner_prompt(
    batch_results: list[dict],
    evolution_history: list[dict],
    branches: list[str],
    *,
    navigation_enabled: bool,
    trajectory_only: bool = False,
) -> str:
    """Build the planner's user-message prompt.

    The system prompt is selected by the caller based on
    ``navigation_enabled``; this function builds the batch + context
    section the planner reads to write the plan.
    """
    # The reveal gate has already been applied upstream (by the
    # NavigationEngine using the Observer): records whose labels are
    # still hidden no longer carry ``success``/``detail`` fields.  We
    # simply include those fields whenever they are present.
    # ``trajectory_only`` is kept for API compatibility but unused — the
    # observer owns the reveal decision now.
    del trajectory_only  # now observer-driven

    summaries = []
    for r in batch_results[:50]:  # cap to keep planner prompt under context limit
        entry: dict[str, Any] = {
            "task_id": r.get("instance_id", r.get("task_id", "")),
            "turns": r.get("turns", 0),
            "error": r.get("error", ""),
        }
        # Task preview so the planner sees what the task actually asks,
        # not just the ID. Fixes R2: prevents "FX prediction" hallucination.
        task_input = r.get("task_input") or r.get("input") or ""
        if isinstance(task_input, dict):
            task_input = task_input.get("input", "")
        if task_input:
            entry["task_preview"] = str(task_input)[:300]
        # Task metadata (category, domain, year) for regime identification
        for field in ("category", "domain", "year", "difficulty_level", "event"):
            val = r.get(field) or r.get("metadata", {}).get(field)
            if val:
                entry[field] = val
        # Liveness signal (not evaluation signal) for cancelled tasks.
        if r.get("status") == "cut_off":
            entry["status"] = "cut_off"
            if r.get("cut_off_reason"):
                entry["cut_off_reason"] = r["cut_off_reason"]
            if r.get("partial_elapsed") is not None:
                entry["partial_elapsed_s"] = round(float(r["partial_elapsed"]), 1)
        # Per-tool latency — lets the planner see which tools are the
        # actual bottleneck (e.g. slow search) instead of inferring it
        # from turn counts.
        timings = r.get("tool_timings") or []
        if timings:
            from agent_evolve.agents._partial_trajectory import (
                summarise_tool_latency,
            )
            s = summarise_tool_latency(timings)
            if s:
                entry["tool_latency"] = s
        # Include ground-truth fields only when they survived the
        # reveal gate (observer stripped them otherwise).
        if "success" in r:
            entry["success"] = r["success"]
            if "detail" in r:
                entry["detail"] = str(r["detail"])[:500]
        # Include solver behavioral fields (not labels — these are what
        # the solver chose, not whether it was correct).
        for field in ("decision", "confidence", "side", "gated"):
            if field in r:
                entry[field] = r[field]
        summaries.append(entry)

    if not batch_results:
        return "No task results to analyze."

    revealed = [r for r in batch_results if "success" in r]
    n_revealed = len(revealed)
    n_total = len(batch_results)
    n_pending = n_total - n_revealed
    if n_revealed == 0:
        header = (
            f"## Batch Results ({n_total} tasks; labels withheld — "
            f"infer from behaviour)"
        )
    else:
        n_success = sum(1 for r in revealed if r.get("success"))
        n_fail = n_revealed - n_success
        pending_part = f", {n_pending} pending reveal" if n_pending else ""
        header = (
            f"## Batch Results ({n_total} tasks: {n_success} passed, "
            f"{n_fail} failed{pending_part})"
        )

    tree_block = ""
    if navigation_enabled:
        tree_block = "\n## Current Branches\n" + (
            "\n".join(f"- {b}" for b in branches)
            if branches else "(no branches yet, only main)"
        )

    return f"""\
{header}

```json
{json.dumps(summaries, indent=2)}
```
{tree_block}

## Evolution History (last 5 cycles)
{json.dumps(evolution_history[-5:], indent=2) if evolution_history else '(first cycle)'}

## Instructions

Analyze the batch. Decide how to split the evolution work into
assignments. Emit the JSON plan.
"""


# Backward-compatible aliases.  Older code imported
# ANALYZE_PLAN_SYSTEM_PROMPT / build_analyze_plan_prompt; keep them
# pointing at the navigation-enabled variants so existing callers still
# function.
ANALYZE_PLAN_SYSTEM_PROMPT = PLANNER_SYSTEM_PROMPT_NAV


def build_analyze_plan_prompt(
    batch_results: list[dict],
    evolution_history: list[dict],
    branches: list[str],
    *,
    trajectory_only: bool = False,
) -> str:
    """Backward-compat wrapper — equivalent to the nav-enabled prompt."""
    return build_planner_prompt(
        batch_results, evolution_history, branches,
        navigation_enabled=True,
        trajectory_only=trajectory_only,
    )


# ── Template ─────────────────────────────────────────────────────────


class OrchestratedTemplate(EvolutionTemplate):
    """Plan-driven multi-agent evolution.

    Cycle structure:
      1. Planner emits a list of assignments.
      2. For each assignment: checkout target → run branch_cycle
         Activity with the assignment's workload as plan context →
         commit ``evo-{N}-{target-slug}``.
      3. Return to main.
    """

    def __init__(self, engine: NavigationEngine):
        self.engine = engine

    @property
    def name(self) -> str:
        return "orchestrated"

    def execute(
        self,
        vc: VersionControl,
        solver_workspace: AgentWorkspace,
        batch_results: list[dict[str, Any]],
        tree: StrategyTree,
        evo_number: int,
        routing_log_path: Path | None,
    ) -> dict[str, Any]:
        trajectory: list[dict[str, Any]] = []
        mutated = False
        nav_enabled = bool(getattr(self.engine.config, "navigation_enabled", False))

        # ── Step 1: Planner produces assignments ────────────────
        routing_history = _load_routing_history(routing_log_path)
        plan = self._run_planner(
            batch_results, routing_history, tree.branch_names(),
            navigation_enabled=nav_enabled,
            workspace_root=solver_workspace.root,
        )
        assignments = list(plan.get("assignments", []) or [])
        trajectory.append({
            "step": "plan",
            "plan_summary": plan.get("summary", ""),
            "n_assignments": len(assignments),
            "targets": [a.get("target", "main") for a in assignments],
        })
        logger.info(
            "Planner emitted %d assignment(s): %s",
            len(assignments), plan.get("summary", ""),
        )

        # ── Step 2: Realise any branch targets the plan nominated ───
        if nav_enabled:
            branch_assignments = [
                a for a in assignments
                if a.get("target", "main") != "main"
            ]
            if branch_assignments:
                self._realise_assignment_branches(
                    vc, tree, branch_assignments, evo_number,
                )

        # ── Step 3: One evolver call per assignment ─────────────
        # Run main assignments first, so when we rebase branches
        # (below) they rebase onto an up-to-date main.
        ordered = sorted(
            assignments,
            key=lambda a: 0 if a.get("target", "main") == "main" else 1,
        )

        main_mutated_this_cycle = False
        for i, assignment in enumerate(ordered):
            target = assignment.get("target", "main") or "main"

            # Skip any branch-targeted assignments in flat mode.
            if not nav_enabled and target != "main":
                logger.warning(
                    "Dropping branch assignment %r in flat mode: %s",
                    target, assignment.get("focus", ""),
                )
                continue

            # If this assignment targets a branch and main was just
            # mutated, rebase the branch before we work on it.
            if target != "main" and main_mutated_this_cycle:
                try:
                    rebased = vc.rebase_branch(target, "main")
                    if not rebased:
                        logger.info("Rebase %s failed, syncing tools/infra from main", target)
                        vc.sync_paths_from("main", target, [
                            "tools/", "infra/", "tools/registry.yaml",
                        ])
                except Exception as e:
                    logger.warning("Rebase %s failed: %s", target, e)

            # Checkout target.
            try:
                vc.checkout_branch(target)
            except Exception as e:
                logger.warning("Checkout %s failed: %s", target, e)
                continue

            # The planner's assignment (focus + workload) already tells the
            # evolver what to do — no need for full trajectory index.
            # Pass minimal batch (just task_ids for reference) to keep the
            # evolver's context under the 1M token limit.
            task_ids = set(assignment.get("task_ids", []) or [])
            if task_ids:
                filtered = [
                    {"instance_id": r.get("instance_id", r.get("task_id", "")),
                     "turns": r.get("turns", 0)}
                    for r in batch_results
                    if r.get("instance_id") in task_ids
                ][:10]
            else:
                filtered = []

            br_result = self._execute_plan_step(
                solver_workspace, filtered, evo_number,
                assignment=assignment,
                target=target,
                tag_suffix=target.replace("/", "-"),
            )
            trajectory.append({
                "step": "branch_evolution" if target != "main" else "deepen_main",
                "target": target,
                "focus": assignment.get("focus", ""),
                "mutated": br_result.get("mutated", False),
                "summary": br_result.get("summary", ""),
                "conversation": br_result.get("conversation", []),
            })
            if br_result.get("mutated"):
                mutated = True
                if target == "main":
                    main_mutated_this_cycle = True

        # Return to main.
        try:
            vc.checkout_branch("main")
        except Exception:
            pass

        return {
            "evo_number": evo_number,
            "mutated": mutated,
            "plan": plan,
            "branches": tree.branch_names(),
            "trajectory": trajectory,
        }

    # ── Internals ───────────────────────────────────────────────

    def _run_planner(
        self,
        batch_results: list[dict],
        evolution_history: list[dict],
        branch_names: list[str],
        *,
        navigation_enabled: bool,
        workspace_root: Any = None,
    ) -> dict[str, Any]:
        """Run the planner LLM with bash access to read trajectories."""
        cfg = self.engine.config
        system_prompt = (
            PLANNER_SYSTEM_PROMPT_NAV if navigation_enabled
            else PLANNER_SYSTEM_PROMPT_FLAT
        )
        prompt = build_planner_prompt(
            batch_results, evolution_history, branch_names,
            navigation_enabled=navigation_enabled,
            trajectory_only=cfg.trajectory_only,
        )

        # Use _run_llm for bash access (planner can read trajectories)
        if workspace_root is not None:
            try:
                from ._evolution_workspace import get_evolver_workspace_path
                evo_ws = get_evolver_workspace_path(workspace_root)
                # Use compact prompt for bash mode — planner discovers details
                # via bash rather than receiving everything in the initial message.
                # This prevents context overflow from large task previews.
                compact_prompt = build_planner_prompt(
                    batch_results[:20], evolution_history, branch_names,
                    navigation_enabled=navigation_enabled,
                    trajectory_only=cfg.trajectory_only,
                )
                result = self.engine._run_llm(
                    compact_prompt, workspace_root,
                    system_prompt=system_prompt,
                    evolver_workspace=evo_ws if evo_ws.exists() else None,
                )
                content = result.get("content", "")
                plan = _parse_plan(content, navigation_enabled)
                plan["_trajectory"] = {
                    "prompt": compact_prompt,
                    "response": content,
                }
                return plan
            except Exception as e:
                logger.warning("Planner with bash failed: %s", e)

        # Fallback: no-tool single call with index embedded
        # Append batch index so planner sees regime info without bash
        index_lines = []
        for r in batch_results:
            cat = r.get("category", "")
            yr = r.get("year", "")
            turns = r.get("turns", 0)
            tid = r.get("instance_id", r.get("task_id", ""))
            outcome = ("PASS" if r.get("success") else "FAIL") if "success" in r else "pending"
            if cat or yr:
                index_lines.append(f"{tid} | {cat} | {yr} | {turns}t | {outcome}")
        if index_lines:
            prompt += "\n\n## Batch Index (task | category | year | turns | outcome)\n```\n"
            prompt += "\n".join(index_lines) + "\n```\n"

        llm = getattr(self.engine, "llm", None)
        if llm is None:
            return _default_plan(batch_results, navigation_enabled)
        try:
            from ....llm.bedrock import BedrockProvider

            if not isinstance(llm, BedrockProvider):
                return _default_plan(batch_results, navigation_enabled)

            response = llm.converse_loop(
                system_prompt=system_prompt,
                user_message=prompt,
                tools=[],
                tool_executor={},
                max_tokens=4096,
                temperature=0.0,
            )
            plan = _parse_plan(response.content, navigation_enabled)
            plan["_trajectory"] = {
                "prompt": prompt,
                "response": response.content,
            }
            return plan
        except Exception as e:
            logger.warning("Planner LLM failed: %s", e)
            return _default_plan(batch_results, navigation_enabled)

    def _execute_plan_step(
        self,
        workspace: AgentWorkspace,
        observation_logs: list[dict[str, Any]],
        evo_number: int,
        *,
        assignment: dict[str, Any],
        target: str,
        tag_suffix: str,
    ) -> dict[str, Any]:
        """Run the branch_cycle Activity + commit with the canonical tag."""
        from ....engine.versioning import VersionControl

        vc = VersionControl(workspace.root)

        # Repackage the assignment as a "plan" for prepend_plan_context.
        # We use the assignment's own vocabulary (focus + workload) — the
        # plan-context prompt adapts to both the old and new shapes.
        plan_for_ctx = {
            "summary": assignment.get("focus", ""),
            "assignment": assignment,
        }

        cfg = self.engine.config
        ws = workspace

        # Per-branch evolver cycle: snapshot → prompt → plan-context →
        # call_llm → diff (before clear) → clear_drafts.
        snapshot, drafts = snapshot_workspace(ws)
        prompt = build_evolution_prompt(
            ws, observation_logs, drafts, evo_number,
            evolve_prompts=cfg.evolve_prompts,
            evolve_skills=cfg.evolve_skills,
            evolve_memory=cfg.evolve_memory,
            evolve_tools=cfg.evolve_tools,
            evolve_infra=cfg.evolve_infra,
            include_patches=cfg.evolver_include_patches,
            trajectory_only=cfg.trajectory_only,
        )
        prompt = prepend_plan_context(prompt, plan_for_ctx, target)

        with ws.protect(disabled_layers(cfg)):
            response = self.engine._run_llm(prompt, ws.root) or {}

        report = detect_mutations(ws, snapshot)
        ws.clear_drafts()

        mutated = bool(report.get("mutated", False))
        summary = report.get("summary", "no mutation")

        if target == "main":
            tag = f"evo-{evo_number}-main"
            msg = f"evo-{evo_number}-main: orchestrated evolution"
        else:
            tag = f"evo-{evo_number}-{tag_suffix}"
            msg = f"{tag}: {summary}"

        vc.commit(message=msg, tag=tag)

        return {
            "mutated": mutated,
            "summary": summary,
            "conversation": response.get("conversation", []),
        }

    def _realise_branches(
        self,
        vc: VersionControl,
        tree: StrategyTree,
        recs: list[dict[str, Any]],
        evo_number: int,
    ) -> list[dict[str, Any]]:
        """Sanitise + create a list of branch recommendations.

        Historical helper — the test suite uses this shape
        (``{"name": ...}``).  The orchestrated template's hot path uses
        ``_realise_assignment_branches`` below, which operates on the
        new assignment schema directly.
        """
        from ..engine import NavigationEngine

        seen: set[str] = set()
        realised: list[dict[str, Any]] = []
        current = vc.get_current_branch()
        for rec in recs:
            if not isinstance(rec, dict):
                continue
            raw_name = rec.get("name", "")
            clean = NavigationEngine._sanitize_branch_name(raw_name)
            if not clean or clean == "main" or clean in seen:
                if not clean or clean == "main":
                    logger.warning(
                        "Skipping branch with unusable name: %r", raw_name,
                    )
                continue
            seen.add(clean)
            rec = dict(rec)
            rec["name"] = clean

            if vc.branch_exists(clean):
                realised.append(rec)
                continue

            try:
                vc.create_branch(clean, "main")
                logger.info(
                    "Created branch: %s (%s)",
                    clean, rec.get("description", ""),
                )
            except (ValueError, RuntimeError) as e:
                logger.warning("Failed to create branch %r: %s", clean, e)
                continue

            if not any(b.name == clean for b in tree.branches):
                tree.branches.append(BranchInfo(
                    name=clean,
                    created_at_cycle=evo_number,
                    description=rec.get("description", ""),
                ))
            realised.append(rec)

        try:
            vc.checkout_branch(current)
        except Exception:
            try:
                vc.checkout_branch("main")
            except Exception:
                pass
        return realised

    def _realise_assignment_branches(
        self,
        vc: VersionControl,
        tree: StrategyTree,
        assignments: list[dict[str, Any]],
        evo_number: int,
    ) -> None:
        """Sanitise + create any branches the planner's assignments target.

        Mutates ``assignment["target"]`` in place to the sanitised name
        and appends new ``BranchInfo`` to ``tree.branches``.
        """
        from ..engine import NavigationEngine

        current = vc.get_current_branch()
        for rec in assignments:
            raw = rec.get("target", "")
            if raw == "main":
                continue
            clean = NavigationEngine._sanitize_branch_name(raw)
            if not clean or clean == "main":
                logger.warning("Dropping unusable branch target %r", raw)
                rec["target"] = "main"  # fall back so the assignment still runs
                continue
            rec["target"] = clean
            if vc.branch_exists(clean):
                continue
            try:
                vc.create_branch(clean, "main")
                logger.info(
                    "Created branch: %s (focus: %s)",
                    clean, rec.get("focus", ""),
                )
            except (ValueError, RuntimeError) as e:
                logger.warning("Failed to create branch %r: %s", clean, e)
                rec["target"] = "main"
                continue
            if not any(b.name == clean for b in tree.branches):
                tree.branches.append(BranchInfo(
                    name=clean,
                    created_at_cycle=evo_number,
                    description=rec.get("focus", ""),
                ))
        try:
            vc.checkout_branch(current)
        except Exception:
            try:
                vc.checkout_branch("main")
            except Exception:
                pass


# ── Helpers ──────────────────────────────────────────────────────────


def _load_routing_history(path: Path | None) -> list[dict[str, Any]]:
    if not path or not path.exists():
        return []
    history: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        if line.strip():
            try:
                history.append(json.loads(line))
            except Exception:
                pass
    return history


def _default_plan(
    batch_results: list[dict],
    navigation_enabled: bool,
) -> dict[str, Any]:
    """Fallback when the planner LLM is unavailable.

    Emits one main assignment covering every task.  Shape matches the
    new assignment schema so downstream code doesn't need to special-case
    the fallback path.
    """
    all_ids = [r.get("instance_id", "") for r in batch_results]
    return {
        "summary": "default plan (LLM unavailable)",
        "assignments": [
            {
                "target": "main",
                "focus": "generic evolution",
                "workload": "Evolve main workspace with all batch tasks.",
                "task_ids": all_ids,
            }
        ],
    }


def _parse_plan(text: str, navigation_enabled: bool) -> dict[str, Any]:
    """Extract + sanitise the JSON plan from the planner's response."""
    import re

    from ..engine import NavigationEngine

    for m in re.finditer(r"\{.*\}", text, re.DOTALL):
        try:
            plan = json.loads(m.group(0))
        except (json.JSONDecodeError, ValueError):
            continue
        if "assignments" not in plan and "main_evolution" not in plan:
            continue
        # Support both the new schema and the legacy one.
        if "assignments" not in plan:
            plan = _legacy_to_assignments(plan)
        plan.setdefault("summary", "")
        clean: list[dict[str, Any]] = []
        for a in plan.get("assignments", []) or []:
            if not isinstance(a, dict):
                continue
            target = a.get("target", "main") or "main"
            if target != "main" and not navigation_enabled:
                logger.warning(
                    "Planner proposed branch target %r in flat mode — "
                    "rewriting to main", target,
                )
                target = "main"
            if target != "main":
                sanitized = NavigationEngine._sanitize_branch_name(target)
                if not sanitized or sanitized == "main":
                    logger.warning("Dropping unusable branch target %r", target)
                    continue
                target = sanitized
            a["target"] = target
            clean.append(a)
        plan["assignments"] = clean
        return plan
    return {"summary": "", "assignments": []}


def _legacy_to_assignments(plan: dict[str, Any]) -> dict[str, Any]:
    """Translate the old {main_evolution, branches} schema to assignments.

    Kept so historical logs / hand-written test plans continue to work.
    """
    assignments: list[dict[str, Any]] = []
    main_evo = plan.get("main_evolution") or {}
    if main_evo.get("description"):
        assignments.append({
            "target": "main",
            "focus": "main evolution",
            "workload": main_evo.get("description", ""),
            "task_ids": main_evo.get("task_ids", []),
        })
    for bp in plan.get("branches", []) or []:
        if not isinstance(bp, dict):
            continue
        assignments.append({
            "target": bp.get("name", ""),
            "focus": bp.get("description", ""),
            "workload": bp.get("evolution_guidance", bp.get("description", "")),
            "task_ids": bp.get("task_ids", []),
        })
    return {
        "summary": plan.get("summary", ""),
        "assignments": assignments,
    }
