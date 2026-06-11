"""Prompt templates for A-Evolve."""

from __future__ import annotations

import json
from typing import Any

from ...contract.workspace import AgentWorkspace

DEFAULT_EVOLVER_SYSTEM_PROMPT = """\
You are a meta-learning agent that improves another agent by modifying its workspace files.

The workspace follows a standard directory structure:
- prompts/system.md  -- the agent's system prompt
- skills/*/SKILL.md  -- reusable skill definitions
- skills/_drafts/    -- draft skills from the solver
- memory/*.jsonl     -- episodic and semantic memory
- tools/             -- helper scripts the agent can invoke via bash
- infra/             -- infrastructure pipelines (run by framework, has network)

Each cycle you will receive:
- A trajectory memory index over recent task attempts (task_id, batch,
  cycle_age, turns, task_input_preview, trajectory_file, patch_file)
- A permissions section listing which layers you CAN and CANNOT modify
- Instructions tailored to the enabled layers

Trajectory memory:
- Recent per-task trajectories and patches are mounted read-only under
  /trajectories/batch_NNNN/. You can inspect them with workspace_bash:
      cat /trajectories/batch_0042/trajectory_<task_id>.json | jq .
      jq '[.[] | select(.role=="tool_use") | .content]' /trajectories/...
      grep -l "error" /trajectories/batch_0042/patch_*.diff
- The index gives you turn counts and a short task_input_preview so you
  can decide which trajectories are worth opening. Prefer reading a few
  representative trajectories over scanning everything.
- You will NOT see pass/fail, score, or judge feedback for any task.
  Infer what's working and what isn't from agent behaviour alone:
  repeated tool failures, reasoning that doesn't converge, missed
  affordances, verbose but fruitless loops.

Follow the permissions and instructions in each cycle message exactly.
Changes to disabled layers will be reverted automatically.

Guidelines:
- Quality over quantity. Only create artifacts that genuinely help future tasks.
- Skills use SKILL.md format with YAML frontmatter (name, description).
- Keep memory concise and actionable.
- When modifying files, use precise edits.
- Use the provided bash tool to read/write files in the workspace.
- Verify your changes with `git diff` before finishing.

Tool creation:
- Tools are helper scripts (Python or bash) the solver can run via its bash tool.
- To create a tool: write the script to tools/<name>.py, then register it in
  tools/registry.yaml under the "tools" key with name, description, and usage fields.
- Example registry.yaml entry:
    tools:
      - name: find_test_files
        description: Find test files related to a given source file
        usage: python tools/find_test_files.py <source_file>
- Only create tools for patterns you see repeated across multiple tasks.
"""


def build_evolution_prompt(
    workspace: AgentWorkspace,
    logs: list[dict[str, Any]],
    drafts: list[dict[str, str]],
    evo_number: int,
    *,
    evolve_prompts: bool = True,
    evolve_skills: bool = True,
    evolve_memory: bool = True,
    evolve_tools: bool = False,
    evolve_infra: bool = True,
    include_patches: bool = False,
    trajectory_only: bool = False,
) -> str:
    """Build the user-message prompt for one evolution cycle.

    Produces a trajectory index: task_id, batch, cycle_age, turns, a
    short task-input preview, and pointer paths the evolver can open via
    workspace_bash against the /trajectories mount.

    Ground-truth labels (success/score) are included transparently when
    the upstream gate left them in the logs — either via
    ``observer.get_recent_logs`` (which overlays revealed_supplement) or
    via ``filter_batch_for_evolver`` (which strips unrevealed tasks).
    The prompt builder itself is a passthrough: it includes whatever
    fields survived the upstream reveal decision.

    ``include_patches`` is a no-op. ``trajectory_only`` is honored as a
    fallback: when True, labels are stripped from logs that the upstream
    caller did not pre-filter (e.g. the standard EvolutionLoop path).
    """
    del include_patches
    # When trajectory_only is set and upstream didn't pre-filter,
    # strip labels here as a safety net.
    if trajectory_only:
        logs = [
            {k: v for k, v in log.items()
             if k not in ("success", "score", "feedback", "feedback_detail")}
            for log in logs
        ]

    # Order logs newest-to-oldest by batch id so the evolver can reason
    # about temporal ordering without being handed raw timestamps.
    def _batch_id(log: dict[str, Any]) -> int:
        raw = log.get("batch") or log.get("batch_num") or log.get("evo_cycle") or 0
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0

    sorted_logs = sorted(logs, key=_batch_id, reverse=True)
    latest_batch = _batch_id(sorted_logs[0]) if sorted_logs else 0

    summaries = []
    for log in sorted_logs:
        tid = log.get("task_id", log.get("instance_id", ""))
        tid_safe = str(tid).replace("/", "_")
        batch_id = _batch_id(log)
        conversation = log.get("conversation") or []
        task_input = log.get("task_input") or log.get("task", {}).get("input") or ""
        entry = {
            "task_id": tid,
            "batch": batch_id,
            "cycle_age": max(latest_batch - batch_id, 0),
            "turns": len(conversation),
            "task_input_preview": str(task_input)[:200],
            "trajectory_file": f"/trajectories/batch_{batch_id:04d}/trajectory_{tid_safe}.json",
            "patch_file": f"/trajectories/batch_{batch_id:04d}/patch_{tid_safe}.diff",
        }
        # Surface liveness signal (not evaluation signal) when a task
        # was cut off by a timeout / batch-deadline.  These come from
        # the solver's partial snapshot via the harness enrichment
        # (solve_all_with_evolution.py::_enrich_from_partial).
        steps = log.get("steps") or []
        step0 = steps[0] if isinstance(steps, list) and steps else {}
        if not isinstance(step0, dict):
            step0 = {}
        # Raw batch_results (navigation path) carry liveness signals at
        # top level; observer JSONL nests them in steps[0].  Merge so
        # downstream reads are shape-agnostic.
        for k in ("status", "cut_off_reason", "partial_elapsed", "tool_timings"):
            if k not in step0 and k in log:
                step0[k] = log[k]
        status = step0.get("status")
        if status == "cut_off":
            entry["status"] = "cut_off"
            cut_off_reason = step0.get("cut_off_reason")
            if cut_off_reason:
                entry["cut_off_reason"] = cut_off_reason
            partial_elapsed = step0.get("partial_elapsed")
            if partial_elapsed is not None:
                entry["partial_elapsed_s"] = round(float(partial_elapsed), 1)
        # Per-tool latency summary — lets the evolver see which tools
        # are the actual time bottleneck (e.g. a slow web_search) rather
        # than inferring "too many turns" from trajectory length alone.
        timings = step0.get("tool_timings")
        if timings:
                from agent_evolve.agents._partial_trajectory import (
                    summarise_tool_latency,
                )
                summary_line = summarise_tool_latency(timings)
                if summary_line:
                    entry["tool_latency"] = summary_line
        # Pass through revealed ground-truth labels.  The upstream gate
        # (observer.get_recent_logs or filter_batch_for_evolver) already
        # stripped these from tasks that haven't resolved yet — we just
        # include whatever survived.
        if "success" in log:
            entry["success"] = log["success"]
        if "score" in log:
            entry["score"] = log["score"]
        summaries.append(entry)

    skills = workspace.list_skills()
    skill_names = [s.name for s in skills]

    tool_registry = workspace.read_tool_registry()
    tool_names = [t.get("name", "") for t in tool_registry]

    draft_section = "No draft skills this batch."
    if drafts:
        parts = []
        for d in drafts:
            parts.append(f"#### Draft: {d['name']}\n```markdown\n{d['content']}\n```")
        draft_section = "\n\n".join(parts)

    permission_lines = []
    if evolve_prompts:
        permission_lines.append("- You CAN modify prompts/system.md")
    else:
        permission_lines.append("- You CANNOT modify prompts/ (changes will be reverted)")
    if evolve_skills:
        permission_lines.append("- You CAN create/modify/delete skills in skills/")
    else:
        permission_lines.append("- You CANNOT modify skills/ (changes will be reverted)")
    if evolve_memory:
        permission_lines.append("- You CAN add/prune entries in memory/*.jsonl")
    else:
        permission_lines.append("- You CANNOT modify memory/ (changes will be reverted)")
    if evolve_tools:
        permission_lines.append("- You CAN create/modify tools in tools/")
    else:
        permission_lines.append("- You CANNOT modify tools/ (changes will be reverted)")
    if evolve_infra:
        permission_lines.append("- You CAN create/modify files in infra/")
    else:
        permission_lines.append("- You CANNOT modify infra/ (changes will be reverted)")

    # Only include sections for enabled layers
    sections = []
    if logs:
        n_labeled = sum(1 for s in summaries if "success" in s)
        if n_labeled:
            label_note = (
                f"Ground-truth labels (success/score) are shown for "
                f"{n_labeled}/{len(summaries)} tasks whose outcome is "
                f"known. Unlabeled tasks are still pending — infer from "
                f"behaviour."
            )
        else:
            label_note = (
                "No ground-truth labels available — infer what worked "
                "from the agent's tool calls and outputs."
            )
        memory_note = (
            "### Trajectory Memory Index\n"
            "Recent task attempts, newest-to-oldest by `batch`. "
            "`trajectory_file` and `patch_file` are absolute paths in "
            "your sandbox — open them via `workspace_bash` with `cat`, "
            "`jq`, or `grep` to inspect full tool-call traces. "
            f"{label_note}\n"
            f"```json\n{json.dumps(summaries, indent=2)}\n```"
        )
        sections.append(memory_note)
    if evolve_skills:
        sections.append(f"### Draft Skills\n{draft_section}")
        sections.append(f"### Current Skills\n{chr(10).join(f'- {s}' for s in skill_names) if skill_names else 'No skills yet.'}")
    if evolve_tools:
        sections.append(f"### Current Tools\n{chr(10).join(f'- {t}' for t in tool_names) if tool_names else 'No tools yet.'}")

    # Instructions matching enabled layers
    instructions = ["1. Review the task summaries -- identify patterns, common failures, recurring themes"]
    step = 2
    if evolve_skills:
        instructions.append(f"{step}. Review draft skills -- decide: refine into a real skill, merge with existing, or discard")
        step += 1
        instructions.append(f"{step}. Review current skills -- any need updating based on new evidence?")
        step += 1
    if evolve_memory:
        instructions.append(f"{step}. Review memory -- prune redundant entries, add high-level insights")
        step += 1
    if evolve_prompts:
        instructions.append(f"{step}. Improve the system prompt if needed")
        step += 1
    if evolve_tools:
        instructions.append(f"{step}. Create/update tools for recurring patterns (write script + update tools/registry.yaml)")
        step += 1
    if evolve_infra:
        instructions.append(f"{step}. Create/update infrastructure pipelines in infra/ if failures are infrastructure-level")
        step += 1
    instructions.append(f"{step}. Use the workspace_bash tool to read/write files in the workspace")
    step += 1
    instructions.append(f"{step}. Verify your changes with `git diff` before finishing")

    return f"""\
## Evolution Cycle #{evo_number}

### Permissions
{chr(10).join(permission_lines)}

{chr(10).join(sections)}

### Instructions
{chr(10).join(instructions)}

When done, summarize what you changed and why.
"""


# ── Navigation prompts ──────────────────────────────────────────────────
# The canonical home is ``agent_evolve.algorithms.navigation.prompts``.
# Lazy re-export (via ``__getattr__``) keeps legacy imports working:
#
#     from agent_evolve.algorithms.aevolve.prompts import NAVIGATE_SYSTEM_PROMPT
#
# without creating a circular dependency between ``aevolve.engine`` and
# ``navigation.engine``.


def __getattr__(name: str):
    if name in (
        "ANALYZE_PLAN_SYSTEM_PROMPT",
        "NAVIGATE_SYSTEM_PROMPT",
        "build_analyze_plan_prompt",
        "build_navigate_prompt",
    ):
        from . import prompts as _nav_prompts  # local import breaks cycle
        return getattr(_nav_prompts, name)
    raise AttributeError(name)
