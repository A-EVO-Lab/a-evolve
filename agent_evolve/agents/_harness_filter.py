"""Worker-side per-task harness filtering for retrieval adaptation (M1/M2).

The solve loop builds ONE system prompt per group (all tasks share it), but
retrieval operators select a different skill/tool subset PER TASK. Since the
worker process only receives ``args_dict`` (not the live agent/workspace),
``build_prompts`` additionally emits the raw harness *pieces*; this module
rebuilds a filtered system prompt + ``tool_files`` from those pieces for one
task.

Only invoked when ``args_dict["task_filters"][task_id]`` is present — i.e.
M1/M2 only. M0/M4 never carry pieces or filters, so this module is never
called on the default path.
"""

from __future__ import annotations

from typing import Any


def compose_harness_prompt(
    base_prompt: str,
    skills: list[dict],   # [{"name","description","body"}]
    tools: list[dict],    # [{"name","description"}]
    memories: list[str],
) -> str:
    """Assemble a system prompt from harness pieces.

    Mirrors the section format the agents' ``_build_system_prompt`` uses
    (## Available Skills / ## Evolved Tools / ## Relevant Memories) so a
    filtered prompt reads the same way to the solver.
    """
    parts = [base_prompt]
    if skills:
        parts.append("\n\n## Available Skills\n")
        parts.append("You have specialized skills. Review them when facing relevant challenges.\n")
        for s in skills:
            parts.append(f"- **{s['name']}**: {s.get('description','')}")
            body = s.get("body", "")
            if body:
                parts.append(f"\n{body}\n")
    if tools:
        parts.append("\n\n## Evolved Tools\n")
        parts.append("Analysis tools available in the sandbox. Run them via the bash tool.\n")
        for t in tools:
            parts.append(f"- **{t['name']}**: {t.get('description','')}")
            parts.append(f"  Usage: `python3 /tools/{t['name']}.py`")
    if memories:
        parts.append("\n\n## Relevant Memories\n")
        for m in memories:
            parts.append(f"- {m}")
    return "\n".join(parts)


def apply_task_filter(
    system_prompt: str,
    tool_files: dict[str, str],
    task_filter: dict[str, list[str]],
    harness_pieces: dict[str, Any] | None,
) -> tuple[str, dict[str, str]]:
    """Return (filtered_system_prompt, filtered_tool_files) for one task.

    ``task_filter`` = {"skills": [...names...], "tools": [...names...],
    "memory": [...ids...]}. ``harness_pieces`` (emitted by build_prompts)
    carries {base_prompt, skills, tools, memories} — the full raw material;
    we keep only the named items. If pieces are missing we degrade safely
    to filtering only ``tool_files`` and leaving the prompt as-is.
    """
    keep_skills = set(task_filter.get("skills") or [])
    keep_tools = set(task_filter.get("tools") or [])
    keep_mem = set(task_filter.get("memory") or [])

    # Always filter tool_files by selected tool names (drops unused sources
    # from the sandbox even if the prompt rebuild is skipped).
    filtered_tool_files = {
        fname: src for fname, src in (tool_files or {}).items()
        if fname[:-3] in keep_tools  # "espn_scores.py" -> "espn_scores"
    }

    if not harness_pieces:
        return system_prompt, filtered_tool_files

    sel_skills = [s for s in harness_pieces.get("skills", []) if s["name"] in keep_skills]
    sel_tools = [t for t in harness_pieces.get("tools", []) if t["name"] in keep_tools]
    all_mem = harness_pieces.get("memories", [])  # [(id, text)]
    sel_mem = [text for (mid, text) in all_mem if mid in keep_mem]

    new_prompt = compose_harness_prompt(
        harness_pieces.get("base_prompt", ""), sel_skills, sel_tools, sel_mem,
    )
    return new_prompt, filtered_tool_files


def build_harness_pieces(agent) -> dict[str, Any]:
    """Collect raw harness pieces from an agent for worker-side filtering.

    Called by build_prompts ONLY when piece-emission is enabled (M1/M2).
    Reads skill bodies, tool descriptions, and memory texts so the worker
    can reassemble any per-task subset.
    """
    base_prompt = ""
    try:
        base_prompt = agent.workspace.read_prompt() or ""
    except Exception:
        pass

    skills = []
    for s in getattr(agent, "skills", []) or []:
        body = ""
        try:
            content = agent.get_skill_content(s.name) if hasattr(agent, "get_skill_content") \
                else agent.workspace.read_skill(s.name)
            if content:
                body = content.split("---", 2)[-1].strip() if "---" in content else content
        except Exception:
            pass
        skills.append({"name": s.name, "description": getattr(s, "description", ""), "body": body})

    tools = [
        {"name": t.get("name", ""), "description": t.get("description", "")}
        for t in (getattr(agent, "tool_registry", []) or [])
        if t.get("name")
    ]

    memories = []
    for i, m in enumerate(getattr(agent, "memories", []) or []):
        text = " ".join(
            str(m.get(k, "")) for k in ("content", "insight", "text", "approach")
            if m.get(k)
        ).strip()
        if text:
            memories.append((f"memory:{i}", text))

    return {"base_prompt": base_prompt, "skills": skills, "tools": tools, "memories": memories}
