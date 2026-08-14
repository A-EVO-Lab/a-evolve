"""Prompt templates for A-Evolve."""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from ...contract.workspace import AgentWorkspace

DEFAULT_EVOLVER_SYSTEM_PROMPT = """\
You are a meta-learning agent. You analyze task feedback and distill actionable skills.

The workspace structure:
- prompts/system.md       -- the agent's system prompt
- skills/general/*/SKILL.md          -- general skills (apply across contexts)
- skills/context/<context_id>/*/SKILL.md  -- context-specific skills (auto-injected for that context)

Each skill is a SKILL.md file with YAML frontmatter (`name`, `description`) and an optional structured body.

## Two types of skills

### Context-specific skills (`skills/context/<context_id>/`)
Mini reference cards for a specific document/context. **The FULL content (description + body) is injected** when the solver sees the same context again. So put all critical details in the body — they WILL be seen by the solver.
- **Ground in the context**: reference specific content FROM the context document — exact terms, section names, data points, persona definitions, structural patterns
- **Combine context + feedback**: the feedback tells you what was missed, the context tells you WHERE to find it. Encode both.
- **Structure the body** with clear sections (Key points, Gotchas, Format requirements, etc.)
- **Be thorough**: every detail from the feedback that could help on the next task for this context should be in the skill

### General skills (`skills/general/`)
Cross-cutting patterns that generalize across ALL contexts. **Full content (description + body) is injected** when selected by the LLM selector.
- **Only write what the model doesn't already know** — skip obvious advice ("read carefully", "be thorough", "follow instructions")
- **Only create when** you see a REPEATED failure pattern across DIFFERENT contexts
- **No context-specific references** — must apply universally

## Skill format
```
---
name: <kebab-case-name>
description: <one-line summary of the skill>
---
## Key points
- <point 1>
- <point 2>

## Gotchas
- <common mistake from feedback>
```
Use structured sections (##, bullet lists) to organize. Keep it compact — no filler text, just actionable points.

## Your job each cycle
1. **Read feedback carefully** — the feedback is the PRIMARY source of learning. It tells you exactly what went wrong and what the rubric expected. Extract concrete lessons from it.
2. For each failed task: identify what the feedback says was missing/wrong, then encode that as a skill
   - Same context_id → context-specific skill (include the exact detail the feedback pointed out)
   - Pattern seen across multiple contexts → general skill
3. For passed tasks: note what worked, reinforce in existing skills if relevant
4. Write skills as SKILL.md files. Update SKILL_TREE.md after changes.

## Rules
- **Feedback-driven**: every skill must trace back to specific feedback. Don't invent skills from guessing — learn from what the feedback actually says.
- Context skills: encode the EXACT requirements the feedback revealed (terms, formats, values, persona details)
- General skills: only create when the SAME type of feedback failure appears across DIFFERENT contexts
- STRONGLY prefer UPDATING an existing skill over creating a new one
- Keep skills concise but informative — no fluff, just the actionable lessons from feedback
- Do NOT waste turns reading files. Go straight to writing.
"""


def _extract_behavioral_signals(log: dict[str, Any]) -> dict[str, Any]:
    """Extract behavioral signals from trajectory data."""
    steps = log.get("steps", [])
    if not steps:
        steps = log.get("trajectory", {}).get("steps", []) if isinstance(log.get("trajectory"), dict) else []

    tool_steps = [s for s in steps if isinstance(s, dict) and "tool" in s]
    n_steps = len(steps)
    n_tool_calls = len(tool_steps)

    # Detect errors
    error_count = 0
    for s in tool_steps:
        output = str(s.get("output", ""))
        if any(k in output.lower() for k in ("error", "exception", "traceback", "failed")):
            error_count += 1

    # Detect loops (same action repeated 3+ times consecutively)
    has_loops = False
    if tool_steps:
        actions = [s.get("action", s.get("tool", "")) for s in tool_steps]
        for i in range(len(actions) - 2):
            if actions[i] == actions[i + 1] == actions[i + 2]:
                has_loops = True
                break

    # Extract files from diff output
    agent_output = log.get("agent_output", "")
    files_edited = []
    for m in re.finditer(r"^(?:\+\+\+)\s+[ab]/(.+)$", agent_output, re.MULTILINE):
        if m.group(1) != "/dev/null":
            files_edited.append(m.group(1))

    signals: dict[str, Any] = {"steps": n_steps}
    if n_tool_calls:
        signals["tool_calls"] = n_tool_calls
    if files_edited:
        signals["files_edited"] = files_edited[:5]
    if error_count:
        signals["errors"] = error_count
    if has_loops:
        signals["has_loops"] = True

    return signals


def _build_aggregate_stats(
    failed_logs: list[dict[str, Any]],
    passed_logs: list[dict[str, Any]],
) -> str:
    """Build aggregate stats section for minimal feedback level."""
    cat_counts: Counter[str] = Counter()
    for log in failed_logs:
        cat = log.get("category", "") or log.get("context_id", "unknown")
        cat_counts[cat] += 1

    lines = ["#### Failure Distribution"]
    if cat_counts:
        for cat, cnt in cat_counts.most_common(10):
            lines.append(f"- {cat}: {cnt} failed")
    else:
        lines.append("- (no category data)")

    return "\n".join(lines)


def _build_task_summary(log: dict[str, Any], feedback_level: str) -> dict[str, Any]:
    """Build per-task summary dict with feedback_level controlling detail."""
    entry: dict[str, Any] = {
        "task_id": log.get("task_id", ""),
        "context_id": log.get("context_id", ""),
        "success": log.get("success", False),
        "score": log.get("score", 0.0),
    }
    if log.get("category"):
        entry["category"] = log["category"]
    if log.get("sub_category"):
        entry["sub_category"] = log["sub_category"]

    if feedback_level == "minimal":
        entry.update(_extract_behavioral_signals(log))
        return entry

    # standard and full: include feedback + task question + agent output
    fb_limit = 300 if feedback_level == "standard" else 2000
    entry["feedback"] = log.get("feedback_detail", "")[:fb_limit]

    task_input = log.get("task_input", "")
    q_limit = 200 if feedback_level == "standard" else 500
    if task_input:
        task_marker = "\nTask:\n"
        idx = task_input.find(task_marker)
        if idx >= 0:
            entry["task_question"] = task_input[idx + len(task_marker):][:q_limit]
        else:
            entry["task_question"] = task_input[:q_limit]

    agent_output = log.get("agent_output", "")
    o_limit = 200 if feedback_level == "standard" else 1000
    if agent_output:
        entry["agent_output_preview"] = agent_output[:o_limit]

    if feedback_level == "full":
        entry.update(_extract_behavioral_signals(log))
        raw = log.get("feedback", {})
        if isinstance(raw, dict) and raw.get("raw"):
            raw_data = raw["raw"]
            entry["raw_feedback"] = json.dumps(raw_data, default=str)[:1000] if not isinstance(raw_data, str) else raw_data[:1000]

    return entry


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
    feedback_level: str = "standard",
    turn_history: str = "",
) -> str:
    """Build the user-message prompt for one evolution cycle.

    feedback_level controls how much evaluation detail the evolver sees:
      "minimal"  — pass/fail + behavioral signals (no test output)
      "standard" — failure reasons, which tests failed (truncated)
      "full"     — complete feedback, raw evaluation data
    """
    failed_logs = [l for l in logs if not l.get("success", False)]
    passed_logs = [l for l in logs if l.get("success", False)]

    recent_failed = failed_logs[-16:]
    recent_passed = passed_logs[-16:]

    failed_summaries = [_build_task_summary(l, feedback_level) for l in recent_failed]
    passed_summaries = [_build_task_summary(l, feedback_level) for l in recent_passed]

    passed_count = len(passed_logs)
    total_count = len(logs)

    skills = workspace.list_skills()
    skill_names = [s.name for s in skills]

    skill_tree_path = workspace.root / "skills" / "SKILL_TREE.md"
    skill_tree_content = skill_tree_path.read_text() if skill_tree_path.exists() else "No SKILL_TREE.md yet."

    current_prompt = workspace.read_prompt()
    prompt_section = f"```\n{current_prompt[:3000]}\n```" if current_prompt else "No system prompt yet."

    draft_section = "No draft skills this batch."
    if drafts:
        parts = []
        for d in drafts:
            parts.append(f"#### Draft: {d['name']}\n```markdown\n{d['content'][:1000]}\n```")
        draft_section = "\n\n".join(parts)

    permission_lines = []
    if evolve_prompts:
        permission_lines.append("- You CAN modify prompts/system.md")
    if evolve_skills:
        permission_lines.append("- You CAN create/modify/delete skills in skills/")
    if evolve_memory:
        permission_lines.append("- You CAN add/prune entries in memory/*.jsonl")
    if evolve_tools:
        permission_lines.append("- You CAN create/modify tools in tools/")

    turn_history_section = ""
    if turn_history:
        turn_history_section = f"""### Evolution History
{turn_history}

"""

    aggregate_section = ""
    if feedback_level == "minimal":
        aggregate_section = "\n" + _build_aggregate_stats(failed_logs, passed_logs) + "\n"

    return f"""\
## Evolution Cycle #{evo_number}

{turn_history_section}### Permissions
{chr(10).join(permission_lines)}

### Task Results
{passed_count}/{total_count} tasks passed.
{aggregate_section}
#### Failed Tasks ({len(failed_summaries)} shown):
```json
{json.dumps(failed_summaries, indent=2)}
```

#### Passed Tasks ({len(passed_summaries)} shown):
```json
{json.dumps(passed_summaries, indent=2)}
```

### Draft Skills
{draft_section}

### Current System Prompt
{prompt_section}

### SKILL_TREE.md (full content)
```markdown
{skill_tree_content}
```

### Instructions
1. Analyze BOTH passed and failed tasks — find COMMON failure/success patterns
2. Distill patterns into SHORT skills (description < 150 chars, body < 300 chars)
3. UPDATE existing skills when possible. Only create new ones for genuinely distinct patterns.
4. After changes, update `skills/SKILL_TREE.md`
5. Consolidate redundant skills
6. Go straight to writing — don't waste turns reading

CRITICAL:
- Skills must be SHORT and SPECIFIC — not generic advice
- Do NOT create a skill for every failed task. Look for SHARED patterns.
- Keep total skill count manageable — quality over quantity.
When done, briefly summarize what you changed.
"""
