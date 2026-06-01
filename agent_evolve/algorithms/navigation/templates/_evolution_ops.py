"""Shared evolution primitives for the inline and orchestrated templates.

These are the workspace-snapshot, mutation-detection, layer-protection and
plan-context helpers both templates need. They are direct ports of the thin
adapter logic that previously lived in the generic ``activity`` runtime
(``activity/nodes/workspace.py`` and ``activity/nodes/prompt.py``); the
templates now call them as plain Python instead of routing a typed dataflow
graph through ``ActivityRuntime``.

Behavior is identical to the former actions — same md5 hashing, same compared
fields, same plan-context shapes — so the evolution paths are unchanged.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ....contract.workspace import AgentWorkspace


def _hash_dir(d) -> str:
    """Hash filenames + contents of a directory for change detection."""
    h = hashlib.md5()
    if d.exists():
        for f in sorted(d.iterdir()):
            if f.is_file():
                h.update(f.name.encode())
                try:
                    h.update(f.read_bytes())
                except Exception:
                    pass
    return h.hexdigest()


def snapshot_workspace(ws: AgentWorkspace) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Capture pre-mutation workspace state + current drafts.

    Returns ``(snapshot, drafts)`` where ``snapshot`` is an opaque dict
    consumed by :func:`detect_mutations`.
    """
    skills = {s.name for s in ws.list_skills()}
    prompt = ws.read_prompt()
    memory = ws.read_all_memories(limit=9999)
    tools = ws.read_tool_registry()
    snapshot = {
        "skills": skills,
        "prompt": prompt,
        "memory_len": len(memory),
        "tools": tools,
        "tools_hash": _hash_dir(ws.root / "tools"),
        "infra_hash": _hash_dir(ws.root / "infra"),
    }
    return snapshot, ws.list_drafts()


def detect_mutations(ws: AgentWorkspace, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Compare current workspace state against a snapshot.

    Emits a report whose ``mutated`` flag is True if any enumerated layer
    changed (prompt / skills / memory / tools / infra).
    """
    skills_after = {s.name for s in ws.list_skills()}
    prompt_changed = ws.read_prompt() != snapshot["prompt"]
    memory_changed = len(ws.read_all_memories(limit=9999)) != snapshot["memory_len"]
    skills_changed = skills_after != snapshot["skills"]
    tools_changed = (
        ws.read_tool_registry() != snapshot["tools"]
        or _hash_dir(ws.root / "tools") != snapshot.get("tools_hash", "")
    )
    infra_changed = _hash_dir(ws.root / "infra") != snapshot.get("infra_hash", "")

    changed = []
    if prompt_changed:
        changed.append("prompt")
    if skills_changed:
        changed.append("skills")
    if memory_changed:
        changed.append("memory")
    if tools_changed:
        changed.append("tools")
    if infra_changed:
        changed.append("infra")

    return {
        "mutated": bool(changed),
        "summary": ", ".join(changed) if changed else "no mutation",
        "changed_layers": changed,
        "new_skills": sorted(skills_after - snapshot["skills"]),
    }


def disabled_layers(cfg) -> list[str]:
    """Layers whose ``cfg.evolve_*`` flag is off (passed to ``ws.protect``)."""
    return [
        name
        for name, on in [
            ("prompts", cfg.evolve_prompts),
            ("skills", cfg.evolve_skills),
            ("memory", cfg.evolve_memory),
            ("tools", cfg.evolve_tools),
            ("infra", cfg.evolve_infra),
        ]
        if not on
    ]


def prepend_plan_context(prompt: str, plan: dict | None, target: str | None) -> str:
    """Prepend a plan-context block to the evolution prompt.

    Accepts the new single-assignment shape
    (``{"summary", "assignment": {"focus", "workload"}}``) and the legacy
    ``{"main_evolution", "branches"}`` shape. Returns the prompt unchanged
    when neither a plan nor a target is supplied (passthrough).
    """
    target = target or ""
    if not plan and not target:
        return prompt
    plan = plan or {}
    target = target or "main"

    lines = ["## Evolution Plan Context\n"]
    if plan.get("summary"):
        lines.append(f"**Plan summary:** {plan['summary']}\n")

    # New shape — a single assignment dict on the plan.
    assignment = plan.get("assignment")
    if isinstance(assignment, dict):
        lines.append(
            f"**Your target:** `{target}`"
            + ("  (main branch)" if target == "main" else "  (specialized branch)")
        )
        focus = assignment.get("focus", "")
        if focus:
            lines.append(f"**Focus:** {focus}")
        workload = assignment.get("workload", "")
        if workload:
            lines.append(f"**Workload:**\n{workload}")
        return "\n".join(lines) + "\n\n" + prompt

    # Legacy shape — main_evolution / branches.
    if target == "main":
        main_evo = plan.get("main_evolution", {})
        lines.append("**Your role:** Evolve the main (general-purpose) branch.")
        lines.append(f"**Focus:** {main_evo.get('description', '')}")
        for insight in main_evo.get("insights", []):
            lines.append(f"- {insight}")
    else:
        for bp in plan.get("branches", []):
            if bp.get("name") == target:
                lines.append(f"**Your role:** Evolve the specialized branch `{target}`.")
                lines.append(f"**Branch purpose:** {bp.get('description', '')}")
                lines.append(f"**Guidance:** {bp.get('evolution_guidance', '')}")
                break
    return "\n".join(lines) + "\n\n" + prompt
