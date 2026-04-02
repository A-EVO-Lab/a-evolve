#!/usr/bin/env python3
"""Grind SkillBench tasks: solve → evolve → solve SAME task → ... until pass or max_cycles.

For each task in each batch:
  - Solve the task
  - If PASS → move on, no evolution needed
  - If FAIL → evolve workspace based on full trajectory, then retry SAME task
  - Repeat until PASS or max_cycles exhausted

Evolved skills are injected into Docker containers so the agent can
discover them via list_skills / load_skill (same as curated skills).

Usage:
    # Quick test: 2 tasks, max 3 retries each
    python examples/skillbench/skillbench_evolve_in_situ_cycle.py \
      --batch-size 2 --max-cycles 3 --use-skills false --limit 2 -v

    # Full run
    python examples/skillbench/skillbench_evolve_in_situ_cycle.py \
      --batch-size 2 --max-cycles 5 --use-skills false
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agent_evolve.agents.skillbench import SkillBenchAgent
from agent_evolve.agents.skillbench.artifacts import export_skillbench_artifacts
from agent_evolve.agents.skillbench.paths import (
    resolve_skillbench_relative_path as resolve_runtime_path,
    resolve_skillbench_seed_workspaces_root,
)
from agent_evolve.agents.skillbench.repo import (
    SkillBenchSetupError,
    resolve_skillbench_paths,
)
from agent_evolve.agents.skillbench.dataset import load_all_tasks
from agent_evolve.benchmarks.skill_bench import SkillBenchBenchmark
from agent_evolve.config import EvolveConfig
from agent_evolve.algorithms.skillforge import AEvolveEngine
from agent_evolve.engine.observer import Observer
from agent_evolve.types import Feedback, Observation, Task, Trajectory

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_write_lock = threading.Lock()


# ── Helpers ─────────────────────────────────────────────────────────


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"Invalid boolean value: {value!r}")


def _parse_skill_select_limit(value: str) -> int:
    """Parse --skill-select-limit: '0' or 'all' = inject all, N>0 = top N."""
    v = value.strip().lower()
    if v in ("0", "all"):
        return 0
    try:
        n = int(v)
        return max(0, n)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Invalid skill-select-limit: {value!r}. Use 'all', '0', or a positive integer."
        )


def _resolve_path(path_value: str | None) -> Path | None:
    return resolve_runtime_path(path_value, repo_root=REPO_ROOT)


def _write_trace(workspace_root: Path, trace_name: str, content: str) -> Path:
    """Write a trace file to evolution/traces/ for debugging."""
    traces_dir = workspace_root / "evolution" / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)
    path = traces_dir / f"{trace_name}.md"
    path.write_text(content, encoding="utf-8")
    return path


def _trace_solver_result(
    workspace_root: Path,
    task_id: str,
    cycle: int,
    feedback: "Feedback",
    elapsed: float,
    evolver_feedback_detail: str,
) -> None:
    """Trace solver result: what happened when solving this task."""
    raw = feedback.raw or {}
    content = f"""# Solver Trace: {task_id} — Cycle {cycle}

## Result
- Passed: {feedback.success}
- Score: {feedback.score}
- Failure class: {raw.get('failure_class', 'unknown')}
- Category: {raw.get('category', 'unknown')}
- Duration: {elapsed:.0f}s

## Skills Loaded
{raw.get('skills_loaded', [])}

## Evolver Feedback
{evolver_feedback_detail}
"""
    _write_trace(workspace_root, f"task_{task_id}_cycle_{cycle}", content)


def _trace_evo_input(
    workspace_root: Path,
    evo_number: int,
    observation_logs: list,
    skill_names: list[str],
) -> None:
    """Trace evolver input: what the evolver will see."""
    obs_file = workspace_root / "evolution" / "current_observation.md"
    obs_content = obs_file.read_text(encoding="utf-8") if obs_file.exists() else "(not found)"

    # Summarize observations
    obs_summary = []
    for obs in observation_logs:
        obs_summary.append(
            f"- {obs.get('task_id', '?')}: success={obs.get('success')}, "
            f"score={obs.get('score', 0):.3f}, "
            f"feedback={obs.get('evolver_feedback_detail', obs.get('feedback_detail', ''))}"
        )

    # Skill list with sizes
    skills_dir = workspace_root / "skills"
    skill_info = []
    if skills_dir.is_dir():
        for d in sorted(skills_dir.iterdir()):
            sm = d / "SKILL.md"
            if sm.exists():
                lines = len(sm.read_text(encoding="utf-8", errors="replace").split("\n"))
                skill_info.append(f"- {d.name}: {lines} lines")

    content = f"""# Evolver Input Trace — Evolution #{evo_number}

## Observations Passed to Evolver
{chr(10).join(obs_summary) if obs_summary else '(none)'}

## Current Observation File
```
{obs_content}
```

## Current Skills ({len(skill_info)})
{chr(10).join(skill_info) if skill_info else '(none)'}

## Skill Names
{skill_names}
"""
    _write_trace(workspace_root, f"evo_{evo_number:03d}_input", content)


def _trace_evo_output(
    workspace_root: Path,
    evo_number: int,
    evolve_result: dict,
    n_distilled: int,
) -> None:
    """Trace evolver output: what changed after evolution."""
    import subprocess

    # Git diff
    try:
        diff = subprocess.run(
            ["git", "diff", "HEAD~1", "--stat"],
            cwd=str(workspace_root), capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except Exception:
        diff = "(git diff failed)"

    # Read new/modified skills
    skills_dir = workspace_root / "skills"
    skill_contents = []
    if skills_dir.is_dir():
        for d in sorted(skills_dir.iterdir()):
            sm = d / "SKILL.md"
            if sm.exists():
                text = sm.read_text(encoding="utf-8", errors="replace")
                skill_contents.append(f"### {d.name} ({len(text.split(chr(10)))} lines)\n```\n{text}\n```")

    content = f"""# Evolver Output Trace — Evolution #{evo_number}

## Evolve Result
- Skills before: {evolve_result.get('skills_before', '?')}
- Skills after: {evolve_result.get('skills_after', '?')}
- New skills: {evolve_result.get('new_skills', '?')}
- Distilled: {n_distilled}
- Usage: {evolve_result.get('usage', {})}

## Git Diff (stat)
```
{diff}
```

## Current Skills (first 500 chars each)
{chr(10).join(skill_contents) if skill_contents else '(none)'}
"""
    _write_trace(workspace_root, f"evo_{evo_number:03d}_output", content)


def _build_evolver_feedback_detail(
    task: Task,
    feedback: Feedback,
    feedback_level: str,
) -> str:
    return SkillBenchBenchmark.build_evolver_feedback(
        task,
        raw=feedback.raw,
        score=feedback.score,
        feedback_level=feedback_level,
    )


def _make_sanitized_observation(
    observation: Observation,
    feedback_level: str,
) -> Observation:
    evolver_feedback_detail = _build_evolver_feedback_detail(
        observation.task,
        observation.feedback,
        feedback_level,
    )
    return Observation(
        task=observation.task,
        trajectory=Trajectory(
            task_id=observation.trajectory.task_id,
            output=observation.trajectory.output,
            steps=[],
        ),
        feedback=Feedback(
            success=observation.feedback.success,
            score=observation.feedback.score,
            detail=evolver_feedback_detail,
            raw={},
        ),
    )


def _task_skill_dir(workspace_root: Path, task_id: str) -> Path:
    return workspace_root / "task_skills" / task_id


def _task_skill_path(workspace_root: Path, task_id: str) -> Path:
    return _task_skill_dir(workspace_root, task_id) / "SKILL.md"


def _should_pre_generate_task_skills(task_skill_mode: str) -> bool:
    return task_skill_mode == "pre_generate_and_retry"


def _should_retry_with_task_skills(task_skill_mode: str) -> bool:
    return task_skill_mode in {"retry_only", "pre_generate_and_retry"}


def _write_observation_for_evolver(
    workspace_root: Path,
    task: "Task",
    feedback: "Feedback",
    trajectory: "Trajectory",
    cycle: int,
    feedback_level: str = "tests",
) -> None:
    """Write a failure analysis to the workspace for the evolver to read.

    The observation file (``evolution/current_observation.md``) is the primary
    feedback channel — the evolver reads it via ``cat``.  The feedback level
    controls how much verifier information is included:

      none  — category + failure_class only (zero leakage)
      score — + reward score + aggregate pass/fail counts
      tests — + stripped test function names (SWE-bench equivalent)
      full  — + full verifier output with assertion values (v1 behavior)
    """
    evo_dir = workspace_root / "evolution"
    evo_dir.mkdir(parents=True, exist_ok=True)
    obs_path = evo_dir / "current_observation.md"

    raw = feedback.raw or {}
    failure_class = raw.get("failure_class", "unknown")
    category = raw.get("category", "unknown")
    difficulty = raw.get("difficulty", "unknown")
    skills_loaded = raw.get("skills_loaded", [])
    evolver_feedback_detail = _build_evolver_feedback_detail(
        task,
        feedback,
        feedback_level,
    )

    # ── Base content (all levels) ──
    parts = [
        f"# Current Failed Task — Cycle {cycle}\n",
        f"## Task: {task.id}",
        f"- Category: {category}",
        f"- Difficulty: {difficulty}",
        f"- Failure class: {failure_class}",
        f"\n## Task Description",
        task.input,
        f"\n## Skills Loaded During Solve",
        ", ".join(skills_loaded) if skills_loaded else "None loaded",
        f"\n## Evolver Feedback",
        evolver_feedback_detail,
    ]

    # ── Instructions (all levels) ──
    # Ablation study shows 15-line "key insight" skills are as effective as
    # 200-line tutorials. Focus the evolver on identifying the ONE critical
    # insight, not writing comprehensive guides.
    parts.append(f"\n## Instructions for Evolver")
    parts.append(
        f'Create or update a skill for the "{category}" category.\n'
        f"The skill should contain domain knowledge, working code, and "
        f"common pitfalls."
    )

    obs_path.write_text("\n".join(parts), encoding="utf-8")


SKILL_TEMPLATE = """\
---
name: {name}
description: {description}
category: {category}
version: {version}
---

## Overview
{overview}

## Workflow
{workflow}

## Key Rules
{rules}

## Verification
{verification}
"""

DISTILL_PROMPT = """\
You are a technical editor. Rewrite this skill into a concise, structured format.

CURRENT SKILL ({current_lines} lines — TOO LONG, max is 200 lines):
```
{content}
```

Rewrite into EXACTLY this structure (max 200 lines total):

---
name: {name}
description: <one line>
category: <category>
version: {version}
---

## Overview (2-3 sentences max)
When to use this skill and what it covers.

## Workflow (main section, include working code)
### Step 1: <action>
```python
# working code
```
### Step 2: <action>
...

## Key Rules (max 10 rules, one line each)
1. ALWAYS ...
2. NEVER ...

## Verification (max 5 checks)
- [ ] Check 1
- [ ] Check 2

IMPORTANT:
- Keep ONLY the most actionable content (working code, exact parameters)
- Remove ALL "RECURRING FAILURE", "Previous Failure", debug diary sections
- Remove duplicate/redundant advice
- MAX 200 lines total
"""

TASK_SKILL_PROMPT = """\
You are a technical analyst. Read this task description and create a focused \
skill document that will help an AI agent solve it.

Task: {task_id}
Category: {category}

Task Description:
{task_input}

Create a concise SKILL.md (max 100 lines) containing:
1. Overview: what this task requires (2-3 sentences)
2. Approach: step-by-step plan (5-8 steps)
3. Key technical details: packages needed, output format, file paths
4. Verification: how to check the solution is correct

Output ONLY the skill content (markdown with YAML frontmatter), nothing else.
"""


def _distill_bloated_skills(
    workspace_root: Path,
    model_id: str,
    region: str,
    *,
    max_lines: int = 200,
    max_bytes: int = 6000,
    log: logging.Logger | None = None,
) -> int:
    """Distill any skill exceeding size limits into the structured template.

    Returns the number of skills distilled.
    """
    from agent_evolve.llm.bedrock import BedrockProvider

    skills_dir = workspace_root / "skills"
    if not skills_dir.is_dir():
        return 0

    distilled = 0
    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue

        content = skill_md.read_text(encoding="utf-8", errors="replace")
        lines = content.split("\n")
        size = len(content.encode("utf-8"))

        if len(lines) <= max_lines and size <= max_bytes:
            continue

        # Extract current frontmatter
        name = skill_dir.name
        version = 1
        import re as _re
        ver_match = _re.search(r"version:\s*(\d+)", content)
        if ver_match:
            version = int(ver_match.group(1)) + 1

        if log:
            log.info(
                "  Distilling %s: %d lines / %d bytes → max %d lines",
                name, len(lines), size, max_lines,
            )

        provider = BedrockProvider(model_id=model_id, region=region)
        from agent_evolve.llm.base import LLMMessage
        messages = [
            LLMMessage(role="user", content=DISTILL_PROMPT.format(
                content=content,
                current_lines=len(lines),
                name=name,
                version=version,
            )),
        ]

        try:
            response = provider.complete(messages, max_tokens=4096, temperature=0.3)
            new_content = response.content or ""
            # Ensure it starts with frontmatter
            if not new_content.strip().startswith("---"):
                new_content = f"---\nname: {name}\nversion: {version}\n---\n\n{new_content}"
            # Enforce line cap (hard truncate if LLM ignored instruction)
            new_lines = new_content.split("\n")
            if len(new_lines) > max_lines + 20:
                new_content = "\n".join(new_lines[:max_lines])
            skill_md.write_text(new_content, encoding="utf-8")
            distilled += 1
            if log:
                log.info("  Distilled %s: %d → %d lines", name, len(lines), len(new_content.split("\n")))
        except Exception as e:
            if log:
                log.warning("  Distillation failed for %s: %s", name, e)

    return distilled


def _generate_task_skill(
    workspace_root: Path,
    task_id: str,
    task_input: str,
    category: str,
    model_id: str,
    region: str,
    log: logging.Logger | None = None,
) -> bool:
    """Generate a task-specific skill from task instruction (no test output).

    Writes to workspace/task_skills/<task_id>/SKILL.md.
    Returns True if skill was generated.
    """
    from agent_evolve.llm.bedrock import BedrockProvider
    from agent_evolve.llm.base import LLMMessage

    skill_path = _task_skill_path(workspace_root, task_id)
    if skill_path.exists():
        if log:
            log.info("  [cached] Task skill for %s already exists", task_id)
        return True

    task_skills_dir = skill_path.parent
    task_skills_dir.mkdir(parents=True, exist_ok=True)

    provider = BedrockProvider(model_id=model_id, region=region)
    messages = [
        LLMMessage(role="user", content=TASK_SKILL_PROMPT.format(
            task_id=task_id,
            category=category,
            task_input=task_input,
        )),
    ]

    try:
        response = provider.complete(messages, max_tokens=2048, temperature=0.3)
        content = response.content or ""
        if not content.strip():
            return False
        # Ensure frontmatter
        if not content.strip().startswith("---"):
            content = f"---\nname: {task_id}-guide\ndescription: Task-specific guidance for {task_id}\ncategory: {category}\n---\n\n{content}"
        skill_path.write_text(content, encoding="utf-8")
        if log:
            log.info("  Generated task skill for %s (%d lines)", task_id, len(content.split("\n")))
        return True
    except Exception as e:
        if log:
            log.warning("  Task skill generation failed for %s: %s", task_id, e)
        return False


def _cleanup_task_skills(workspace_root: Path) -> None:
    """Legacy helper retained for compatibility; task skills now persist across batches."""
    return None


EVOLVE_TASK_SKILL_PROMPT = """\
The agent failed to solve this task. Update the task-specific skill to help the retry.

Task: {task_id} ({category})
Failure class: {failure_class}
Score: {score}
{feedback_section}

Current task skill:
```
{current_skill}
```

Rewrite the task skill to address the failure. Focus on:
1. What the agent likely did wrong (based on the failure class)
2. What specific approach should be taken instead
3. Any constraints or gotchas from the task description

{no_answers_instruction}

Output ONLY the updated SKILL.md content (with YAML frontmatter), max 100 lines.
"""


def _evolve_task_skill(
    workspace_root: Path,
    task: "Task",
    feedback: "Feedback",
    model_id: str,
    region: str,
    feedback_level: str = "tests",
    no_direct_answers: bool = True,
    log: logging.Logger | None = None,
) -> bool:
    """Evolve the task-specific skill based on failure feedback.

    Unlike general skill evolution (which creates category-level knowledge),
    this updates the per-task skill with specific guidance for retrying
    THIS task.
    """
    from agent_evolve.llm.bedrock import BedrockProvider
    from agent_evolve.llm.base import LLMMessage

    task_id = task.id
    category = task.metadata.get("category", "unknown")
    skill_path = _task_skill_path(workspace_root, task_id)
    task_skills_dir = skill_path.parent
    current_skill = ""
    if skill_path.exists():
        current_skill = skill_path.read_text(encoding="utf-8", errors="replace")

    raw = feedback.raw or {}
    failure_class = raw.get("failure_class", "unknown")
    score = feedback.score

    feedback_section = _build_evolver_feedback_detail(task, feedback, feedback_level)

    no_answers_instruction = ""
    if no_direct_answers:
        no_answers_instruction = (
            "IMPORTANT: Do NOT include specific numeric answers, expected values, "
            "or test assertions in the skill. The skill should teach the APPROACH, "
            "not encode the ANSWER."
        )

    provider = BedrockProvider(model_id=model_id, region=region)
    messages = [
        LLMMessage(role="user", content=EVOLVE_TASK_SKILL_PROMPT.format(
            task_id=task_id,
            category=category,
            failure_class=failure_class,
            score=score,
            feedback_section=feedback_section,
            current_skill=current_skill,
            no_answers_instruction=no_answers_instruction,
        )),
    ]

    try:
        response = provider.complete(messages, max_tokens=2048, temperature=0.3)
        content = response.content or ""
        if not content.strip():
            return False
        if not content.strip().startswith("---"):
            content = f"---\nname: {task_id}-guide\ncategory: {category}\n---\n\n{content}"
        task_skills_dir.mkdir(parents=True, exist_ok=True)
        skill_path.write_text(content, encoding="utf-8")
        if log:
            log.info("  Evolved task skill for %s (%d lines)", task_id, len(content.split("\n")))
        return True
    except Exception as e:
        if log:
            log.warning("  Task skill evolution failed for %s: %s", task_id, e)
        return False


SUCCESS_DISTILL_PROMPT = """\
A task just PASSED. Extract a concise, generalizable skill from this success.

Task: {task_id} (category: {category})
Cycles used: {cycles_used}

Task description (first 1500 chars):
{task_input}

Write a candidate general skill that captures what worked.
Format as a SKILL.md with YAML frontmatter including:
  name, description, category, scope (general/category_general),
  support_key (normalized key for dedup, e.g. "excel_formula_recalc")

Content sections (max 30 lines total):
  ## Overview (2 sentences)
  ## Key Parameters / API Patterns
  ## Pitfalls
  ## Verification

Do NOT include task-specific details or answer values.
Focus on TRANSFERABLE patterns that help similar tasks.
"""


def _distill_success_to_draft(
    workspace_root: Path,
    task_id: str,
    task_input: str,
    category: str,
    cycles_used: int,
    model_id: str,
    region: str,
    log: logging.Logger | None = None,
) -> dict | None:
    """Distill a successful solve into a draft candidate skill.

    Writes to skills/_drafts/<support_key>.md. Returns metadata dict or None.
    """
    from agent_evolve.llm.bedrock import BedrockProvider
    from agent_evolve.llm.base import LLMMessage

    provider = BedrockProvider(model_id=model_id, region=region)
    messages = [
        LLMMessage(role="user", content=SUCCESS_DISTILL_PROMPT.format(
            task_id=task_id,
            category=category,
            cycles_used=cycles_used,
            task_input=task_input,
        )),
    ]

    try:
        response = provider.complete(messages, max_tokens=1024, temperature=0.3)
        content = response.content or ""
        if not content.strip():
            return None

        # Extract support_key from frontmatter
        import re
        key_match = re.search(r"support_key:\s*(.+)", content)
        support_key = key_match.group(1).strip().strip('"\'') if key_match else task_id
        support_key = re.sub(r"[^a-z0-9_-]", "-", support_key.lower()).strip("-")
        if not support_key:
            support_key = task_id

        # Write to _drafts/
        drafts_dir = workspace_root / "skills" / "_drafts"
        drafts_dir.mkdir(parents=True, exist_ok=True)
        draft_path = drafts_dir / f"{support_key}.md"

        # Track supporting tasks in a metadata file
        meta_path = drafts_dir / f"{support_key}.meta.json"
        meta = {"support_key": support_key, "supporting_tasks": [], "category": category}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
            except Exception:
                pass
        if task_id not in meta.get("supporting_tasks", []):
            meta.setdefault("supporting_tasks", []).append(task_id)

        draft_path.write_text(content, encoding="utf-8")
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

        if log:
            n_support = len(meta["supporting_tasks"])
            log.info("  Draft skill '%s' from %s (support: %d tasks)",
                     support_key, task_id, n_support)

        return meta

    except Exception as e:
        if log:
            log.warning("  Success distillation failed for %s: %s", task_id, e)
        return None


def _promote_drafts(
    workspace_root: Path,
    threshold: int = 2,
    log: logging.Logger | None = None,
) -> int:
    """Promote draft skills that have enough task support to the main skill library.

    Returns number of skills promoted.
    """
    drafts_dir = workspace_root / "skills" / "_drafts"
    if not drafts_dir.is_dir():
        return 0

    promoted = 0
    for meta_path in sorted(drafts_dir.glob("*.meta.json")):
        try:
            meta = json.loads(meta_path.read_text())
        except Exception:
            continue

        support_key = meta.get("support_key", "")
        supporting_tasks = meta.get("supporting_tasks", [])
        category = meta.get("category", "")

        if len(supporting_tasks) < threshold:
            continue

        draft_path = drafts_dir / f"{support_key}.md"
        if not draft_path.exists():
            continue

        # Promote: move from _drafts/ to skills/<support_key>/
        skill_dir = workspace_root / "skills" / support_key
        if skill_dir.exists():
            continue  # Already promoted or name conflict

        skill_dir.mkdir(parents=True, exist_ok=True)
        content = draft_path.read_text(encoding="utf-8")
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

        if log:
            log.info("  PROMOTED draft '%s' → skills/%s (supported by %d tasks: %s)",
                     support_key, support_key, len(supporting_tasks),
                     ", ".join(supporting_tasks[:5]))

        promoted += 1

    return promoted


def _write_result(path: Path, result: dict) -> None:
    with _write_lock:
        with open(path, "a") as f:
            f.write(json.dumps(result, default=str) + "\n")


def _load_completed(path: Path) -> set[str]:
    done: set[str] = set()
    if not path.exists():
        return done
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            tid = d.get("task_id")
            if tid and d.get("final"):
                done.add(tid)
        except json.JSONDecodeError:
            pass
    return done


def _compute_metrics(path: Path) -> dict:
    results = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            if r.get("final"):
                results.append(r)
        except json.JSONDecodeError:
            pass

    if not results:
        return {"total": 0, "passed": 0, "pass_ratio": 0.0}

    total = len(results)
    passed = sum(1 for r in results if r.get("passed"))

    # How many needed grinding (>1 cycle)
    grind_needed = sum(1 for r in results if r.get("cycles_used", 1) > 1)
    grind_resolved = sum(1 for r in results if r.get("cycles_used", 1) > 1 and r.get("passed"))

    by_diff: dict[str, list] = {}
    for r in results:
        d = r.get("difficulty", "unknown")
        by_diff.setdefault(d, []).append(r)

    by_cat: dict[str, list] = {}
    for r in results:
        c = r.get("category", "unknown")
        by_cat.setdefault(c, []).append(r)

    def _ratio(items: list) -> dict:
        t = len(items)
        p = sum(1 for r in items if r.get("passed"))
        return {"total": t, "passed": p, "pass_ratio": round(p / t, 4) if t else 0.0}

    return {
        "total": total,
        "passed": passed,
        "pass_ratio": round(passed / total, 4) if total else 0.0,
        "grind_needed": grind_needed,
        "grind_resolved": grind_resolved,
        "by_difficulty": {d: _ratio(items) for d, items in sorted(by_diff.items())},
        "by_category": {c: _ratio(items) for c, items in sorted(by_cat.items())},
    }


# ── Main ────────────────────────────────────────────────────────────


def main() -> int:
    p = argparse.ArgumentParser(
        description="Grind SkillBench tasks: solve → evolve → retry until pass or max_cycles"
    )

    # Grind settings
    p.add_argument("--max-cycles", type=int, default=3,
                   help="Max solve→evolve cycles per task (default 3)")
    p.add_argument("--batch-size", type=int, default=2,
                   help="Tasks per batch (keep small to avoid prompt overflow, default 2)")
    p.add_argument("--max-workers", type=int, default=1,
                   help="Parallel workers for solving (1=serial, >1=parallel solve, serial evolve)")

    # Tasks
    p.add_argument("--use-skills", type=_parse_bool, default=False)
    p.add_argument("--tasks-dir-with-skills", type=str, default=None)
    p.add_argument("--tasks-dir-without-skills", type=str, default=None)
    p.add_argument("--split-seed", type=int, default=42)
    p.add_argument("--category", type=str, default=None)
    p.add_argument("--difficulty", type=str, default=None)
    p.add_argument("--limit", type=int, default=None,
                   help="Max total tasks (for quick testing)")

    # Agent / execution
    p.add_argument("--mode", default="native", choices=["native", "harbor"])
    p.add_argument("--native-profile", default="terminus2",
                   choices=["strands", "terminus2", "terminus2_legacy"])
    p.add_argument("--score-mode", default="dual",
                   choices=["reward", "binary", "dual"])
    p.add_argument("--model-id", type=str,
                   default="us.anthropic.claude-opus-4-5-20251101-v1:0")
    p.add_argument("--region", type=str, default="us-west-2")
    p.add_argument("--max-tokens", type=int, default=16384)
    p.add_argument("--retry-max", type=int, default=6)
    p.add_argument("--retry-min-wait-sec", type=float, default=1.0)
    p.add_argument("--retry-max-wait-sec", type=float, default=120.0)

    # Evolver
    p.add_argument("--evolver-model-id", type=str, default=None)
    p.add_argument(
        "--task-skill-mode",
        type=str,
        default="pre_generate_and_retry",
        choices=["off", "retry_only", "pre_generate_and_retry"],
        help="How task-specific skills are used during grinding.",
    )

    # Workspace
    p.add_argument(
        "--seed-workspace",
        type=str,
        default=str(resolve_skillbench_seed_workspaces_root() / "skillbench"),
        help="Seed workspace directory (default: bundled skillbench workspace)",
    )
    p.add_argument("--work-dir", type=str, default=None,
                   help="Workspace dir (default: <run-dir>/workspace)")

    # Output
    p.add_argument("--output", type=str, default=None)
    p.add_argument("--run-dir", type=str, default=None,
                   help="Run directory (default: logs/grind_run_<timestamp>)")

    # Harbor
    p.add_argument("--harbor-repo", type=str, default=None)
    p.add_argument("--harbor-agent-import-path", type=str,
                   default="libs.terminus_agent.agents.terminus_2."
                           "harbor_terminus_2_skills:HarborTerminus2WithSkills")
    p.add_argument("--harbor-model-name", type=str, default=None)
    p.add_argument("--harbor-jobs-dir", type=str,
                   default="/tmp/aevolve-skillbench-harbor-jobs")
    p.add_argument("--harbor-timeout-sec", type=int, default=1800)
    p.add_argument("--harbor-uv-cmd", type=str, default="uv run harbor run")

    # Feedback level (follows SWE-bench pattern)
    p.add_argument("--feedback-level", type=str, default="tests",
                   choices=["none", "score", "tests", "masked", "full"],
                   help="How much verifier feedback the evolver sees: "
                        "none=category+failure_class only, "
                        "score=+reward+counts, "
                        "tests=+test names (params stripped, default), "
                        "masked=+verifier output with assertion values masked, "
                        "full=+assertion values+verifier output")
    p.add_argument("--no-direct-answers", type=_parse_bool, default=True,
                   help="Prevent skills from encoding specific answer values (default: true)")

    # Skill injection control
    p.add_argument("--skill-select-limit", type=str, default="0",
                   help="Max workspace skills to inject per task. "
                        "0 or 'all' = inject all (default). "
                        "N>0 = keyword-match top N relevant skills.")

    # Evolution scope control
    p.add_argument("--evolve-skills", type=_parse_bool, default=True,
                   help="Allow evolver to create/modify skills (default: true)")
    p.add_argument("--evolve-memory", type=_parse_bool, default=False,
                   help="Allow evolver to modify memory files (default: false)")
    p.add_argument("--evolve-prompts", type=_parse_bool, default=False,
                   help="Allow evolver to modify system prompt (default: false)")
    p.add_argument("--evolve-tools", type=_parse_bool, default=False,
                   help="Allow evolver to create tools (default: false)")
    p.add_argument("--distill", type=_parse_bool, default=False,
                   help="Post-evolution distillation of bloated skills (default: false)")

    # Success distillation mode
    p.add_argument("--success-mode", type=str, default="off",
                   choices=["off", "draft_only", "gated_promotion"],
                   help="What to do when a task passes: "
                        "off=nothing (current), "
                        "draft_only=distill candidate skill to _drafts/ (not injected), "
                        "gated_promotion=draft + promote to skills/ when 2+ tasks support it")
    p.add_argument("--promotion-threshold", type=int, default=2,
                   help="Min tasks supporting a draft before promotion (default: 2)")

    p.add_argument("--metrics-only", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    # ── Logging ───────────────────────────────────────────────────
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    for n in ("botocore", "urllib3", "httpcore", "httpx",
              "strands.models", "strands.tools", "strands.telemetry"):
        logging.getLogger(n).setLevel(logging.WARNING)
    log = logging.getLogger("skillbench_grind")

    run_dir = Path(args.run_dir) if args.run_dir else Path(
        f"logs/grind_run_{time.strftime('%Y%m%d_%H%M%S')}_pid{os.getpid()}"
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("SKILLBENCH_RUN_ID", run_dir.name)

    output_path = Path(args.output) if args.output else (run_dir / "results.jsonl")
    work_dir = _resolve_path(args.work_dir) if args.work_dir else (run_dir / "workspace")
    artifacts_dir = run_dir / "outputs"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    # ── Metrics-only mode ─────────────────────────────────────────
    if args.metrics_only:
        if not output_path.exists():
            print(f"No results file: {output_path}")
            return 1
        metrics = _compute_metrics(output_path)
        print(json.dumps(metrics, indent=2))
        return 0

    try:
        resolved_skillbench = resolve_skillbench_paths(
            tasks_with_skills_dir=args.tasks_dir_with_skills,
            tasks_without_skills_dir=args.tasks_dir_without_skills,
            harbor_repo=args.harbor_repo,
        )
    except SkillBenchSetupError as exc:
        print(str(exc))
        return 1

    tasks_dir = resolved_skillbench.selected_tasks_dir(use_skills=args.use_skills)
    seed_dir = _resolve_path(args.seed_workspace)
    if seed_dir is None or not seed_dir.exists():
        print(f"Seed workspace not found: {seed_dir}")
        return 1

    # ── Load tasks ────────────────────────────────────────────────
    all_sb_tasks = load_all_tasks(str(tasks_dir))
    if args.category:
        all_sb_tasks = [t for t in all_sb_tasks if t.metadata.get("category") == args.category]
    if args.difficulty:
        all_sb_tasks = [t for t in all_sb_tasks if t.metadata.get("difficulty") == args.difficulty]
    # random.Random(args.split_seed).shuffle(all_sb_tasks)
    if args.limit:
        all_sb_tasks = all_sb_tasks[:args.limit]

    total_tasks = len(all_sb_tasks)
    batches = [all_sb_tasks[i:i + args.batch_size]
               for i in range(0, total_tasks, args.batch_size)]

    log.info("=" * 70)
    log.info("SkillBench Grind (cycle per task)")
    log.info("  Total tasks:    %d (%d batches of %d)", total_tasks, len(batches), args.batch_size)
    log.info("  Max cycles:     %d per task", args.max_cycles)
    log.info("  Use skills:     %s", args.use_skills)
    log.info("  Feedback level: %s", args.feedback_level)
    log.info("  Task skills:    %s", args.task_skill_mode)
    _ssl = _parse_skill_select_limit(args.skill_select_limit)
    log.info("  Skill select:   %s", "all" if _ssl == 0 else f"top {_ssl}")
    log.info("  Model:          %s", args.model_id)
    log.info("  Evolver model:  %s", args.evolver_model_id or args.model_id)
    log.info("  Run dir:        %s", run_dir)
    log.info("  SkillBench src: %s", resolved_skillbench.source)
    log.info("  SkillBench repo:%s", resolved_skillbench.repo_dir)
    log.info("  SkillBench ref: %s", resolved_skillbench.repo_ref)
    if args.task_skill_mode == "pre_generate_and_retry":
        log.info(
            "  Note: cycle-1 passes may include pre-generated task-specific skills, "
            "so they are not a pure general-skill transfer signal."
        )
    log.info("=" * 70)

    # ── Prepare workspace ─────────────────────────────────────────
    if not work_dir.exists() and seed_dir.exists():
        work_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(seed_dir, work_dir)
        log.info("Copied seed workspace %s -> %s", seed_dir, work_dir)

    # ── Create benchmark + agent + evolver ────────────────────────
    harbor_repo = resolved_skillbench.harbor_repo
    harbor_jobs_dir = _resolve_path(args.harbor_jobs_dir)
    effective_harbor_model = args.harbor_model_name or args.model_id

    bm = SkillBenchBenchmark(
        tasks_with_skills_dir=str(resolved_skillbench.tasks_with_skills_dir),
        tasks_without_skills_dir=str(resolved_skillbench.tasks_without_skills_dir),
        use_skills=args.use_skills,
        split_seed=args.split_seed,
        execution_mode=args.mode,
        shuffle=False,
        harbor_repo=str(harbor_repo),
        harbor_agent_import_path=args.harbor_agent_import_path,
        harbor_model_name=effective_harbor_model,
        harbor_jobs_dir=str(harbor_jobs_dir) if harbor_jobs_dir else None,
        harbor_timeout_sec=args.harbor_timeout_sec,
        harbor_uv_cmd=args.harbor_uv_cmd,
        native_profile=args.native_profile,
        score_mode=args.score_mode,
        retry_max=args.retry_max,
        retry_min_wait_sec=args.retry_min_wait_sec,
        retry_max_wait_sec=args.retry_max_wait_sec,
    )

    agent = SkillBenchAgent(
        workspace_dir=work_dir,
        model_id=args.model_id,
        region=args.region,
        max_tokens=args.max_tokens,
        tasks_dir=str(tasks_dir),
        execution_mode=args.mode,
        harbor_repo=str(harbor_repo),
        harbor_agent_import_path=args.harbor_agent_import_path,
        harbor_model_name=effective_harbor_model,
        harbor_jobs_dir=str(harbor_jobs_dir) if harbor_jobs_dir else None,
        harbor_timeout_sec=args.harbor_timeout_sec,
        harbor_uv_cmd=args.harbor_uv_cmd,
        native_profile=args.native_profile,
        score_mode=args.score_mode,
        retry_max=args.retry_max,
        retry_min_wait_sec=args.retry_min_wait_sec,
        retry_max_wait_sec=args.retry_max_wait_sec,
        write_episodic_memory=args.evolve_memory,
        skill_select_limit=_parse_skill_select_limit(args.skill_select_limit),
    )
    runtime_config = bm.get_agent_runtime_config()
    runtime_config["harbor_model_name"] = effective_harbor_model
    runtime_config["native_profile"] = args.native_profile
    runtime_config["score_mode"] = args.score_mode
    runtime_config["retry_max"] = args.retry_max
    runtime_config["retry_min_wait_sec"] = args.retry_min_wait_sec
    runtime_config["retry_max_wait_sec"] = args.retry_max_wait_sec
    runtime_config["task_skills_enabled"] = args.task_skill_mode != "off"
    agent.configure_from_benchmark(runtime_config)
    agent.write_episodic_memory = args.evolve_memory  # Only write memories if memory evolution enabled
    agent.task_skills_enabled = args.task_skill_mode != "off"

    evolution_dir = work_dir / "evolution"
    evolution_dir.mkdir(parents=True, exist_ok=True)
    observer = Observer(evolution_dir)

    config = EvolveConfig(
        evolver_model=args.evolver_model_id or args.model_id,
        extra={"region": args.region},
        evolve_skills=args.evolve_skills,
        evolve_memory=args.evolve_memory,
        evolve_prompts=args.evolve_prompts,
        evolve_tools=args.evolve_tools,
    )
    evolver = AEvolveEngine(config)

    log.info("  Evolve scope: skills=%s memory=%s prompts=%s tools=%s",
             args.evolve_skills, args.evolve_memory, args.evolve_prompts, args.evolve_tools)

    completed = _load_completed(output_path)
    if completed:
        log.info("Resuming: %d tasks already completed", len(completed))

    # ── Helper: convert SBTask → Task ─────────────────────────────
    def _to_task(sb_task) -> Task:
        return Task(
            id=sb_task.name,
            input=sb_task.prompt,
            metadata={
                "task_name": sb_task.name,
                "task_dir": sb_task.metadata.get("task_dir", ""),
                "dockerfile_dir": sb_task.dockerfile_dir,
                "test_sh_path": sb_task.test_sh_path,
                "test_py_path": sb_task.test_py_path,
                "category": sb_task.metadata.get("category", "unknown"),
                "difficulty": sb_task.metadata.get("difficulty", "unknown"),
                "agent_timeout_sec": sb_task.metadata.get("agent_timeout_sec", 900),
                "verifier_timeout_sec": sb_task.metadata.get("verifier_timeout_sec", 900),
                "build_timeout_sec": sb_task.metadata.get("build_timeout_sec", 600),
                "cpus": sb_task.metadata.get("cpus", 1),
                "memory": sb_task.metadata.get("memory", "4g"),
                "backend": "native",
                "comparison_key": sb_task.name,
            },
        )

    # ── Helper: solve one task (one cycle), thread-safe ─────────
    def _solve_one(sb_task, task: Task, cycle: int, batch_num: int) -> dict:
        """Solve one task for one cycle. Returns result dict. Thread-safe."""
        t0 = time.time()
        try:
            trajectory = agent.solve(task)
            feedback = bm.evaluate(task, trajectory)
            elapsed = time.time() - t0
            artifact_paths = export_skillbench_artifacts(
                artifacts_dir=artifacts_dir,
                task_id=task.id,
                mode=args.mode,
                native_profile=args.native_profile,
                model_id=args.model_id,
                region=args.region,
                max_tokens=args.max_tokens,
                use_skills=args.use_skills,
                split_seed=args.split_seed,
                trajectory=trajectory,
                feedback=feedback,
                elapsed=elapsed,
                run_id=os.environ.get("SKILLBENCH_RUN_ID"),
                cycle=cycle,
            )
            log.info("  Saved cycle artifacts: %s", artifact_paths.conversation_path.name)
            if artifact_paths.official_like_trial_dir is not None:
                log.info(
                    "  Saved official-like trajectory artifacts under %s",
                    artifact_paths.official_like_trial_dir,
                )
            return {
                "sb_task": sb_task, "task": task, "trajectory": trajectory,
                "feedback": feedback, "cycle": cycle, "batch_num": batch_num,
                "elapsed": elapsed, "error": None,
            }
        except Exception as e:
            elapsed = time.time() - t0
            return {
                "sb_task": sb_task, "task": task, "trajectory": None,
                "feedback": None, "cycle": cycle, "batch_num": batch_num,
                "elapsed": elapsed, "error": str(e),
            }

    max_workers = max(1, args.max_workers)

    # ══════════════════════════════════════════════════════════════
    # MAIN LOOP: process batches, grind each task
    # Strategy: solve in PARALLEL, evolve in SERIAL
    # ══════════════════════════════════════════════════════════════

    run_start = time.time()
    stats = {"done": 0, "pass": 0, "fail": 0, "err": 0, "grind_resolved": 0}
    evo_counter = 0

    for batch_idx, batch in enumerate(batches):
        batch_num = batch_idx + 1
        n_skills = len(agent.workspace.list_skills())
        n_memories = len(agent.workspace.read_all_memories(limit=9999))

        log.info("")
        log.info("=" * 70)
        log.info("  BATCH %d/%d — %d tasks (workers=%d)",
                 batch_num, len(batches), len(batch), max_workers)
        log.info("  Skills: %d | Memories: %d", n_skills, n_memories)
        log.info("=" * 70)

        # Filter already-completed tasks
        pending = []
        for sb_task in batch:
            if sb_task.name in completed:
                log.info("  [skip] %s (already completed)", sb_task.name)
                continue
            pending.append(sb_task)

        if not pending:
            continue

        # Pre-generate task-specific skills for all pending tasks when enabled.
        if _should_pre_generate_task_skills(args.task_skill_mode):
            for sb_task in pending:
                category = sb_task.metadata.get("category", "unknown")
                _generate_task_skill(
                    work_dir, sb_task.name, sb_task.prompt, category,
                    args.model_id, args.region, log=log,
                )
            agent.reload_from_fs()

        # Track per-task state across cycles
        task_state: dict[str, dict] = {}
        for sb_task in pending:
            task_state[sb_task.name] = {
                "sb_task": sb_task,
                "task": _to_task(sb_task),
                "resolved": False,
                "cycles_used": 0,
            }

        # ── Grind cycles: solve parallel → evolve serial → retry ──
        for cycle in range(1, args.max_cycles + 1):
            # Collect tasks that still need solving this cycle
            to_solve = [
                ts for ts in task_state.values()
                if not ts["resolved"] and ts["cycles_used"] < cycle
            ]
            if not to_solve:
                break

            # ── PARALLEL SOLVE ──
            if max_workers > 1 and len(to_solve) > 1:
                log.info("  [cycle %d] Solving %d tasks in parallel (workers=%d)",
                         cycle, len(to_solve), min(max_workers, len(to_solve)))
                futures = {}
                with ThreadPoolExecutor(max_workers=min(max_workers, len(to_solve))) as ex:
                    for ts in to_solve:
                        fut = ex.submit(
                            _solve_one, ts["sb_task"], ts["task"], cycle, batch_num,
                        )
                        futures[fut] = ts
                results = []
                for fut in as_completed(futures):
                    results.append(fut.result())
            else:
                # Serial solve (max_workers=1 or single task)
                results = []
                for ts in to_solve:
                    r = _solve_one(ts["sb_task"], ts["task"], cycle, batch_num)
                    results.append(r)

            # ── Process results + collect failures ──
            cycle_observations: list[Observation] = []
            for r in results:
                task_id = r["task"].id
                ts = task_state[task_id]
                ts["cycles_used"] = cycle

                if r["error"]:
                    log.error("  [%d/%d] %s cycle %d: ERROR %s (%.0fs)",
                              stats["done"] + 1, total_tasks, task_id,
                              cycle, r["error"], r["elapsed"])
                    _write_result(output_path, {
                        "task_id": task_id, "passed": False, "score": 0.0,
                        "failure_class": "error", "cycle": cycle,
                        "batch": batch_num,
                        "category": r["sb_task"].metadata.get("category", "unknown"),
                        "difficulty": r["sb_task"].metadata.get("difficulty", "unknown"),
                        "duration_sec": round(r["elapsed"], 1),
                        "detail": f"ERROR: {r['error']}", "final": False,
                    })
                    ts["resolved"] = False  # Mark as done (error = give up)
                    ts["cycles_used"] = args.max_cycles  # Don't retry
                    continue

                feedback = r["feedback"]
                trajectory = r["trajectory"]
                task = r["task"]

                # ── GAP#1: score + failure_class + test counts in per-cycle line ──
                _fc = feedback.raw.get("failure_class", "unknown")
                _rw = feedback.raw.get("reward_float", 0.0)
                _test_info = SkillBenchBenchmark._extract_test_results(
                    str(feedback.raw.get("verifier_tail", ""))
                )
                _np, _nf = _test_info["n_passed"], _test_info["n_failed"]
                _test_str = f" ({_np}/{_np + _nf} tests)" if (_np + _nf) > 0 else ""
                # ── GAP#5: score delta between cycles ──
                _prev_score = ts.get("best_score", 0.0)
                _delta_str = ""
                if cycle > 1 and not feedback.success:
                    delta = _rw - _prev_score
                    _delta_str = f" delta={delta:+.3f}"
                ts["best_score"] = max(_prev_score, _rw)
                # ── GAP#3: skills actually used by solver ──
                _skills_loaded = []
                if trajectory.steps:
                    _skills_loaded = trajectory.steps[-1].get("skills_loaded", [])
                _skills_str = ""
                if _skills_loaded:
                    _skills_str = f" skills_used={_skills_loaded}"

                log.info(
                    "  [%d/%d] %s cycle %d/%d: %s score=%.3f%s %s (%.0fs)%s%s",
                    stats["done"] + 1, total_tasks, task_id,
                    cycle, args.max_cycles,
                    "PASS" if feedback.success else "FAIL",
                    _rw, _test_str, _fc,
                    r["elapsed"], _delta_str, _skills_str,
                )
                # ── GAP#4: accumulate token usage ──
                _usage = {}
                if trajectory.steps:
                    _usage = trajectory.steps[-1].get("usage", {})
                _solver_in = int(_usage.get("input_tokens", 0))
                _solver_out = int(_usage.get("output_tokens", 0))
                stats.setdefault("solver_input_tokens", 0)
                stats.setdefault("solver_output_tokens", 0)
                stats["solver_input_tokens"] += _solver_in
                stats["solver_output_tokens"] += _solver_out

                _write_result(output_path, {
                    "task_id": task_id,
                    "passed": feedback.success,
                    "score": feedback.score,
                    "reward_float": _rw,
                    "failure_class": _fc,
                    "tests_passed": _np,
                    "tests_failed": _nf,
                    "skills_loaded": _skills_loaded,
                    "cycle": cycle, "batch": batch_num,
                    "category": r["sb_task"].metadata.get("category", "unknown"),
                    "difficulty": r["sb_task"].metadata.get("difficulty", "unknown"),
                    "duration_sec": round(r["elapsed"], 1),
                    "detail": feedback.detail,
                    "final": False,
                })

                # Trace solver result
                evolver_feedback_detail = _build_evolver_feedback_detail(
                    task,
                    feedback,
                    args.feedback_level,
                )
                _trace_solver_result(
                    work_dir,
                    task_id,
                    cycle,
                    feedback,
                    r["elapsed"],
                    evolver_feedback_detail,
                )

                if feedback.success:
                    ts["resolved"] = True
                elif cycle < args.max_cycles:
                    cycle_observations.append(
                        Observation(task=task, trajectory=trajectory, feedback=feedback)
                    )

            # ── SERIAL EVOLVE (on all failures from this cycle) ──
            if cycle_observations and cycle < args.max_cycles:
                agent.export_to_fs()
                observer.collect([
                    _make_sanitized_observation(obs, args.feedback_level)
                    for obs in cycle_observations
                ])

                # Write observation for the LAST failure (evolver reads one file)
                last_obs = cycle_observations[-1]
                _write_observation_for_evolver(
                    work_dir, last_obs.task, last_obs.feedback,
                    last_obs.trajectory, cycle,
                    feedback_level=args.feedback_level,
                )

                evo_counter += 1
                log.info("  --- Evolution cycle %d (%d failures) ---",
                         evo_counter, len(cycle_observations))

                evo_logs = []
                for obs in cycle_observations:
                    entry: dict[str, Any] = {
                        "task_id": obs.task.id,
                        "task_input": obs.task.input,
                        "agent_output": obs.trajectory.output,
                        "steps": obs.trajectory.steps,
                    }
                    # Gate success/score by feedback_level to prevent leakage
                    if args.feedback_level in ("score", "tests", "masked", "full"):
                        entry["score"] = obs.feedback.score
                    if args.feedback_level in ("tests", "masked", "full"):
                        entry["success"] = obs.feedback.success
                    entry["evolver_feedback_detail"] = _build_evolver_feedback_detail(
                        obs.task,
                        obs.feedback,
                        args.feedback_level,
                    )
                    evo_logs.append(entry)

                # Trace evolver input
                skill_names_before = [
                    s.name for s in agent.workspace.list_skills()
                ]
                _trace_evo_input(work_dir, evo_counter, evo_logs, skill_names_before)

                evo_t0 = time.time()
                try:
                    evolve_result = evolver.evolve(
                        workspace=agent.workspace,
                        observation_logs=evo_logs,
                        evo_number=evo_counter,
                    )
                    n_distilled = 0
                    if args.distill:
                        n_distilled = _distill_bloated_skills(
                            work_dir, args.model_id, args.region,
                            max_lines=200, max_bytes=6000, log=log,
                        )

                    # Trace evolver output
                    _trace_evo_output(work_dir, evo_counter, evolve_result, n_distilled)

                    agent.reload_from_fs()
                    _evo_elapsed = time.time() - evo_t0
                    _evo_usage = evolve_result.get("usage", {})
                    _evo_added = evolve_result.get("skills_added", [])
                    _evo_removed = evolve_result.get("skills_removed", [])
                    # ── GAP#4: accumulate evolver token usage ──
                    stats.setdefault("evolver_input_tokens", 0)
                    stats.setdefault("evolver_output_tokens", 0)
                    stats["evolver_input_tokens"] += int(_evo_usage.get("input_tokens", 0))
                    stats["evolver_output_tokens"] += int(_evo_usage.get("output_tokens", 0))

                    _evo_detail = ""
                    if _evo_added:
                        _evo_detail += f" +{_evo_added}"
                    if _evo_removed:
                        _evo_detail += f" -{_evo_removed}"
                    log.info(
                        "  Evolution done in %.0fs — %d new skills (skills: %d -> %d), %d distilled%s",
                        _evo_elapsed,
                        evolve_result.get("new_skills", 0),
                        evolve_result.get("skills_before", 0),
                        evolve_result.get("skills_after", 0),
                        n_distilled,
                        _evo_detail,
                    )

                    # Evolve task-specific skills for each failed task.
                    if _should_retry_with_task_skills(args.task_skill_mode):
                        for obs in cycle_observations:
                            _evolve_task_skill(
                                work_dir,
                                obs.task,
                                obs.feedback,
                                args.model_id,
                                args.region,
                                feedback_level=args.feedback_level,
                                no_direct_answers=args.no_direct_answers,
                                log=log,
                            )
                        agent.reload_from_fs()

                except Exception as e:
                    log.error("  Evolution failed: %s", e)
                    agent.reload_from_fs()

        # ── Record final results + success distillation ──
        for ts in task_state.values():
            task_id = ts["task"].id
            resolved = ts["resolved"]
            cycles_used = ts["cycles_used"]

            stats["done"] += 1
            if resolved:
                stats["pass"] += 1
                if cycles_used > 1:
                    stats["grind_resolved"] += 1
            else:
                stats["fail"] += 1

            _write_result(output_path, {
                "task_id": task_id,
                "passed": resolved,
                "cycles_used": cycles_used,
                "batch": batch_num,
                "category": ts["sb_task"].metadata.get("category", "unknown"),
                "difficulty": ts["sb_task"].metadata.get("difficulty", "unknown"),
                "final": True,
            })
            completed.add(task_id)

            # ── Success distillation (draft_only or gated_promotion) ──
            # Skip for multi-cycle passes: the evolver already created a
            # workspace skill from the failure, so success distillation
            # would produce a weaker duplicate.
            if resolved and args.success_mode != "off" and cycles_used == 1:
                category = ts["sb_task"].metadata.get("category", "unknown")
                _distill_success_to_draft(
                    work_dir, task_id, ts["sb_task"].prompt, category,
                    cycles_used, args.model_id, args.region, log=log,
                )

            n = stats["done"]
            log.info(
                "  => %s %s (cycles=%d) | pass=%d fail=%d | rate=%.0f%%",
                task_id, "RESOLVED" if resolved else "FAILED", cycles_used,
                stats["pass"], stats["fail"],
                100 * stats["pass"] / n if n else 0,
            )

        # ── Gated promotion: promote drafts with enough support ──
        if args.success_mode == "gated_promotion":
            n_promoted = _promote_drafts(
                work_dir, threshold=args.promotion_threshold, log=log,
            )
            if n_promoted > 0:
                agent.reload_from_fs()

    # ══════════════════════════════════════════════════════════════
    # SUMMARY
    # ══════════════════════════════════════════════════════════════

    total_elapsed = time.time() - run_start
    metrics = _compute_metrics(output_path)
    metrics["workspace"] = {
        "skills_final": len(agent.workspace.list_skills()),
        "memories_final": len(agent.workspace.read_all_memories(limit=9999)),
        "work_dir": str(work_dir),
    }
    metrics["config"] = {
        "max_cycles": args.max_cycles,
        "batch_size": args.batch_size,
        "use_skills": args.use_skills,
        "model_id": args.model_id,
        "evolver_model_id": args.evolver_model_id or args.model_id,
        "native_profile": args.native_profile,
        "split_seed": args.split_seed,
        "feedback_level": args.feedback_level,
        "task_skill_mode": args.task_skill_mode,
        "total_sec": round(total_elapsed, 1),
    }
    metrics_path = output_path.with_suffix(".metrics.json")
    metrics_path.write_text(json.dumps(metrics, indent=2))

    # ── GAP#4: token usage summary ──
    _s_in = stats.get("solver_input_tokens", 0)
    _s_out = stats.get("solver_output_tokens", 0)
    _e_in = stats.get("evolver_input_tokens", 0)
    _e_out = stats.get("evolver_output_tokens", 0)
    _total_tokens = _s_in + _s_out + _e_in + _e_out

    log.info("")
    log.info("=" * 70)
    log.info("DONE in %.0fs", total_elapsed)
    log.info("  Overall:       %d/%d passed (%.1f%%)",
             metrics["passed"], metrics["total"], 100 * metrics["pass_ratio"])
    log.info("  Grind needed:  %d tasks needed retries", metrics.get("grind_needed", 0))
    log.info("  Grind resolved:%d tasks resolved via evolution", metrics.get("grind_resolved", 0))
    if _total_tokens > 0:
        log.info("  Tokens:        solver=%s (in=%s out=%s) evolver=%s (in=%s out=%s) total=%s",
                 f"{(_s_in + _s_out):,}", f"{_s_in:,}", f"{_s_out:,}",
                 f"{(_e_in + _e_out):,}", f"{_e_in:,}", f"{_e_out:,}",
                 f"{_total_tokens:,}")
    log.info("  Results:  %s", output_path)
    log.info("  Metrics:  %s", metrics_path)
    log.info("=" * 70)

    print(json.dumps(metrics, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
