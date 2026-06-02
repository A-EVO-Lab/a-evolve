"""TerminalSkillEvolve -- Terminal-Bench adaptive-skill style operator.

This atom keeps Terminal-Bench's tuned skill-evolution prompt semantics inside
the UnifiedEngine recipe model. It is intentionally self-contained: legacy
engine modules are treated as a specification, not imported.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..registry import register_operator
from ..types import MutationReport
from .llm_bash_evolve import (
    BASH_TOOL_SPEC,
    DEFAULT_EVOLVER_SYSTEM_PROMPT,
    _make_workspace_bash,
    _resolve_llm,
    _restore_tree,
    _snapshot_tree,
)

logger = logging.getLogger(__name__)


def _build_trajectory_only_heading() -> str:
    return """\
### Agent Behavior Analysis (this batch)

You can ONLY see the agent's actions. You do NOT have access to actual test results.

Each task includes:
- `signals`: automated behavioral metrics (turns, errors, timeouts, submission status, loops)
- `compressed_trajectory`: failure-focused summary (approach, errors, loops, final actions)
- `judge_verdict`: An LLM judge's assessment of whether the agent likely succeeded. Includes:
  - `score` (0-10): 0=complete failure, 5=partial, 10=likely solved
  - `category`: task type (build, debug, data-science, security, etc.)
  - `outcome`: what happened
  - `failure_reason`: specific thing that went wrong (if score < 7)

Use judge scores to prioritize your work:
- Score 0-3 (FAILED): Agent clearly failed. Analyze the failure_reason and trajectory to understand WHY.
- Score 4-6 (PARTIAL): Agent made progress but likely didn't finish. Look for what blocked it.
- Score 7-10 (LIKELY SOLVED): Agent probably succeeded. Skip these -- do not create skills from them.

Group failures by category. If multiple tasks in the same category failed for similar reasons, that's a pattern worth addressing with a category-specific skill."""


def _build_trajectory_only_instructions(
    current_skill_count: int,
    max_skills: int = 5,
    protect_skills: bool = False,
) -> str:
    skill_budget_note = ""
    if current_skill_count >= max_skills:
        skill_budget_note = f"""
**SKILL BUDGET REACHED ({current_skill_count}/{max_skills}).** You MUST NOT create new skills.
Instead: refine existing skills with new patterns from this batch's failures."""
    elif current_skill_count > 0:
        remaining = max_skills - current_skill_count
        skill_budget_note = f"""
**Skill budget: {current_skill_count}/{max_skills} used ({remaining} remaining).**"""

    protect_note = ""
    if protect_skills:
        protect_note = """
**EXISTING SKILLS ARE READ-ONLY.** You MUST NOT modify or delete any existing skill files. \
You may ONLY create NEW skills. Existing skills have been validated and optimized -- do not touch them."""

    modify_or_create = """3. **For each pattern with 2+ failed tasks**, either:
   - **Refine an existing skill** if it covers that category but missed the specific failure.
   - **Create a new skill** if no existing skill covers that failure category."""
    if protect_skills:
        modify_or_create = """3. **For each pattern with 2+ failed tasks**, create a NEW skill targeting that failure category. \
Do NOT modify existing skills."""

    return f"""\
**You may ONLY modify skills.** Do NOT modify prompts/system.md, memory, or tools.
{protect_note}
Skills are loaded on demand by the agent via `read_skill(name)`. The agent sees skill \
names and descriptions in its system prompt, and decides which to read. Good skills are \
ones the agent will actually choose to read and benefit from.

**Analysis steps:**
1. **Sort tasks by judge score.** List each task with its score, category, and failure_reason.
2. **Identify failure patterns.** Group failed tasks (score < 7) by category and failure reason.
{modify_or_create}
4. **If failures are diverse** (all different categories/reasons), focus on the lowest-scoring tasks.
5. **Skip tasks with score >= 7** -- the agent likely solved them without help.
{skill_budget_note}

**Skill quality checklist:**
- `name` in YAML frontmatter must be short, descriptive kebab-case (the agent matches by name)
- `description` must clearly say WHEN this skill applies (the agent decides to read based on this)
- Body must contain domain-specific knowledge the agent couldn't infer on its own
- Max 2000 chars per skill -- concise and actionable
- Include verification steps (how to confirm the task is solved)

**FORBIDDEN -- do NOT write any of the following (the agent already knows these):**
- Timeout handling, package installation tips, session persistence warnings
- Generic debugging advice, command chaining tips
- Any advice about HOW to use bash/python tools

**REQUIRED -- only write domain knowledge the agent does NOT already have:**
- Specific libraries/tools/commands needed for a task category
- Verification steps that prove a task category is solved
- Common domain-specific pitfalls and how to avoid them

Use the workspace_bash tool to read/write files. Verify with `git diff`."""


def _merge_terminal_evidence(context: Any) -> list[dict[str, Any]]:
    terminal = (context.entries.get("TerminalTrajectoryReader", {}) or {}).get(
        "per_task", []
    )
    judge = (context.entries.get("LLMJudgeReader", {}) or {}).get("per_task", [])
    judge_by_task = {
        str(v.get("task_id", "")): v
        for v in judge
        if isinstance(v, dict) and int(v.get("score", -1) or -1) >= 0
    }

    summaries: list[dict[str, Any]] = []
    for row in terminal:
        if not isinstance(row, dict):
            continue
        entry = {
            "task_id": row.get("task_id", ""),
            "signals": row.get("signals", {}),
            "compressed_trajectory": row.get("compressed_trajectory", ""),
        }
        verdict = judge_by_task.get(str(row.get("task_id", "")))
        if verdict:
            entry["judge_verdict"] = {
                "score": verdict.get("score", -1),
                "category": verdict.get("category", "unknown"),
                "outcome": verdict.get("outcome", ""),
                "failure_reason": verdict.get("failure_reason", ""),
            }
        summaries.append(entry)
    return summaries


def _draft_section(workspace: Any) -> str:
    drafts = list(workspace.list_drafts())
    if not drafts:
        return "No draft skills this batch."
    parts = []
    for draft in drafts:
        name = draft.get("name", "")
        content = draft.get("content", "")[:1000]
        parts.append(f"#### Draft: {name}\n```markdown\n{content}\n```")
    return "\n\n".join(parts)


def _build_prompt(
    workspace: Any,
    context: Any,
    cycle_num: int,
    state: dict[str, Any],
) -> str:
    summaries = _merge_terminal_evidence(context)
    skills = workspace.list_skills()
    skill_names = [s.name for s in skills]
    max_skills = int(state.get("max_skills", 5) or 5)
    protect_skills = bool(state.get("protect_skills", False))

    permission_lines: list[str] = []
    if bool(state.get("evolve_prompts", False)):
        permission_lines.append("- You CAN modify prompts/system.md")
    if bool(state.get("evolve_skills", True)):
        if protect_skills:
            permission_lines.append(
                "- You CAN create NEW skills in skills/ but MUST NOT modify or delete existing skills"
            )
        else:
            permission_lines.append("- You CAN create/modify/delete skills in skills/")
    if bool(state.get("evolve_memory", False)):
        permission_lines.append("- You CAN add/prune entries in memory/*.jsonl")
    if bool(state.get("evolve_tools", False)):
        permission_lines.append("- You CAN create/modify tools in tools/")

    return f"""\
## Evolution Cycle #{cycle_num}

### Permissions
{chr(10).join(permission_lines)}

{_build_trajectory_only_heading()}
```json
{json.dumps(summaries, indent=2)}
```

### Draft Skills
{_draft_section(workspace)}

### Current Skills ({len(skill_names)})
{chr(10).join(f'- {name}' for name in skill_names) if skill_names else 'No skills yet.'}

### Instructions
{_build_trajectory_only_instructions(len(skill_names), max_skills=max_skills, protect_skills=protect_skills)}

When done, summarize what you changed and why.
"""


def _restore_scope(
    workspace: Any,
    scope: dict[str, Any],
    snapshots: dict[str, dict[str, bytes] | None],
) -> list[str]:
    restored: list[str] = []
    root = Path(workspace.root)
    for artifact in ("prompts", "memory", "tools"):
        path = root / artifact
        if scope.get(artifact) not in ("rw", "append"):
            current = _snapshot_tree(path)
            if current != snapshots.get(artifact):
                _restore_tree(path, snapshots.get(artifact))
                restored.append(artifact)

    if scope.get("skills") != "rw":
        skills_path = root / "skills"
        current = _snapshot_tree(skills_path)
        if current != snapshots.get("skills"):
            _restore_tree(skills_path, snapshots.get("skills"))
            restored.append("skills")
        return restored

    return restored


@register_operator("TerminalSkillEvolve")
class TerminalSkillEvolve:
    """Single Terminal-Bench skill evolution pass."""

    WRITES: frozenset[str] = frozenset({"skills"})
    DEFAULT_MODEL = "us.anthropic.claude-opus-4-6-v1"
    DEFAULT_REGION = "us-west-2"
    DEFAULT_MAX_TOKENS = 16384

    def apply(
        self,
        workspace: Any,
        context: Any,
        scope: dict[str, Any],
        state: dict[str, Any],
    ) -> MutationReport:
        cycle_num = int(state.get("cycle_num", 0)) + 1
        state["cycle_num"] = cycle_num

        root = Path(workspace.root)
        skills_before = {s.name for s in workspace.list_skills()}
        snapshots = {
            "prompts": _snapshot_tree(root / "prompts"),
            "skills": _snapshot_tree(root / "skills"),
            "memory": _snapshot_tree(root / "memory"),
            "tools": _snapshot_tree(root / "tools"),
        }

        prompt = _build_prompt(workspace, context, cycle_num, state)
        max_tokens = int(state.get("max_tokens", self.DEFAULT_MAX_TOKENS))
        bash_fn = _make_workspace_bash(workspace.root)

        provider = state.get("llm_provider")
        mock = state.get("mock")
        try:
            if provider is not None:
                response_content = self._call_provider(provider, prompt, bash_fn, max_tokens)
            elif callable(mock):
                response_content = str(mock(prompt) or "")
            else:
                model = state.get("model_id", self.DEFAULT_MODEL)
                region = state.get("region", self.DEFAULT_REGION)
                provider, _kind = _resolve_llm(model, region)
                response_content = self._call_provider(provider, prompt, bash_fn, max_tokens)
        except Exception as exc:  # noqa: BLE001
            logger.error("TerminalSkillEvolve: LLM call failed: %s", exc)
            return MutationReport(
                operator_name="TerminalSkillEvolve",
                count=0,
                details={"error": str(exc)[:200]},
            )

        restored = _restore_scope(
            workspace,
            scope,
            snapshots,
        )
        try:
            workspace.clear_drafts()
        except Exception:
            pass

        skills_after = {s.name for s in workspace.list_skills()}
        added = sorted(skills_after - skills_before)
        removed = sorted(skills_before - skills_after)
        max_skills = int(state.get("max_skills", 0) or 0)
        over_budget = max_skills > 0 and len(skills_after) > max_skills

        return MutationReport(
            operator_name="TerminalSkillEvolve",
            count=len(added) + len(removed),
            details={
                "cycle": cycle_num,
                "skills_added": added,
                "skills_removed": removed,
                "scope_restored": restored,
                "response_len": len(response_content or ""),
                "soft_max_skills": max_skills,
                "skills_after": len(skills_after),
                "over_budget": over_budget,
            },
        )

    @staticmethod
    def _call_provider(
        provider: Any,
        prompt: str,
        bash_fn: Any,
        max_tokens: int,
    ) -> str:
        try:
            from agent_evolve.llm.bedrock import BedrockProvider

            if isinstance(provider, BedrockProvider):
                response = provider.converse_loop(
                    system_prompt=DEFAULT_EVOLVER_SYSTEM_PROMPT,
                    user_message=prompt,
                    tools=[BASH_TOOL_SPEC],
                    tool_executor={"workspace_bash": bash_fn},
                    max_tokens=max_tokens,
                )
                return response.content
        except ImportError:
            pass

        from agent_evolve.llm.base import LLMMessage

        response = provider.complete(
            [
                LLMMessage(role="system", content=DEFAULT_EVOLVER_SYSTEM_PROMPT),
                LLMMessage(role="user", content=prompt),
            ],
            max_tokens=max_tokens,
        )
        return response.content
