#!/usr/bin/env python3
"""Evolve an agent on Terminal-Bench 2.0 using the A-EVOLVE propose+curator loop.

Pipeline per batch:
  1. Parallel solve (ReAct agent with bash/python/submit tools in Docker)
  2. Parallel evaluate (test.sh inside container)
  3. Analyze feedback + propose skills (in solver conversation context)
  4. Curator per topic (self-assigned domain skills)
  5. General curator (cross-task patterns)
  6. Reload workspace, next batch

Usage:
    python evo_harness/terminal_bench.py \
        --limit 50 --batch-size 10 --workers 4 \
        --output-dir outputs/terminal_evolve_v1
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["BYPASS_TOOL_CONSENT"] = "true"

from agent_evolve.agents.terminal.dataset import load_all_tasks, TB2Task
from agent_evolve.agents.terminal.docker_env import TB2Container, pull_image
from agent_evolve.agents.terminal.react_solver import (
    react_solve, extract_conversation, TOOL_SPECS, SYSTEM_PROMPT as TB2_SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)

_write_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Seed skills
# ---------------------------------------------------------------------------

SEED_SKILLS_DIR = Path(__file__).resolve().parent.parent / "seed_skills" / "terminal"


def _init_seed_skills(workspace_dir: Path):
    """Copy seed skills into workspace/skills/seed/."""
    seed_skills_src = SEED_SKILLS_DIR
    seed_skills_dst = workspace_dir / "skills" / "seed"
    if seed_skills_dst.exists():
        return  # already initialized
    if not seed_skills_src.exists():
        logger.warning("Seed skills not found at %s", seed_skills_src)
        return
    shutil.copytree(seed_skills_src, seed_skills_dst)
    logger.info("Copied %d seed skills from %s",
                len(list(seed_skills_dst.rglob("SKILL.md"))), seed_skills_src)


# ---------------------------------------------------------------------------
# Model map
# ---------------------------------------------------------------------------

MODEL_MAP = {
    "1": "us.anthropic.claude-opus-4-6-v1",
    "2": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "3": "us.anthropic.claude-opus-4-5-20251101-v1:0",
}

# ---------------------------------------------------------------------------
# Bedrock helpers (reuse from cl_bench)
# ---------------------------------------------------------------------------

_thread_local = threading.local()


def _get_client(region: str = "us-west-2"):
    """Get or create a thread-local Bedrock runtime client."""
    key = f"bedrock_{region}"
    if not hasattr(_thread_local, key):
        import boto3
        from botocore.config import Config as BotoConfig
        client = boto3.client(
            "bedrock-runtime",
            region_name=region,
            config=BotoConfig(read_timeout=300, retries={"max_attempts": 0}),
        )
        setattr(_thread_local, key, client)
    return getattr(_thread_local, key)



def _call_bedrock(client, model_id, system_prompt, user_message,
                  max_tokens=4096, temperature=0.0):
    """Simple Bedrock converse call. Returns (text, error)."""
    for attempt in range(5):
        try:
            inference_config = {"maxTokens": max_tokens}
            if "opus-4-7" not in model_id:
                inference_config["temperature"] = temperature
            resp = client.converse(
                modelId=model_id,
                system=[{"text": system_prompt}],
                messages=[{"role": "user", "content": [{"text": user_message}]}],
                inferenceConfig=inference_config,
            )
            content = resp.get("output", {}).get("message", {}).get("content", [])
            text = "".join(b.get("text", "") for b in content)
            return text.strip(), None
        except Exception as e:
            err = str(e)
            base = 30 if "too many tokens" in err.lower() else (
                4 if "throttl" in err.lower() else 2
            )
            delay = base * (2 ** attempt)
            if attempt < 4:
                time.sleep(delay)
            else:
                return None, err
    return None, "exhausted retries"


def _truncate(s: str, n: int = 300) -> str:
    return s[:n] + "..." if len(s) > n else s


# ---------------------------------------------------------------------------
# Trajectory analysis helpers (ported from a-evolve)
# ---------------------------------------------------------------------------

def _extract_trajectory_signals(conversation: list[dict]) -> dict:
    """Extract structured behavioral signals from a conversation trajectory."""
    n_turns = 0
    n_tool_calls = 0
    n_errors = 0
    n_timeouts = 0
    tools_used: dict[str, int] = {}
    commands_run: list[str] = []
    submitted = False
    submit_value = ""
    error_messages: list[str] = []

    for msg in conversation:
        role = msg.get("role", "")
        if role == "assistant":
            n_turns += 1
            for tc in msg.get("tool_calls", []):
                n_tool_calls += 1
                fn = tc.get("function", "")
                tools_used[fn] = tools_used.get(fn, 0) + 1
                args = tc.get("arguments", {})
                cmd = args.get("cmd", "") or args.get("command", "") or args.get("code", "")
                if cmd:
                    commands_run.append(cmd[:80])
                if fn in ("submit", "task_submit"):
                    submitted = True
                    submit_value = args.get("answer", "")
        elif role == "tool":
            content = msg.get("content") or ""
            if "ERROR:" in content or "error:" in content.lower()[:50]:
                n_errors += 1
                error_messages.append(content[:100])
            if "timed out" in content.lower() or "timeout" in content.lower():
                n_timeouts += 1

    # Detect repeated commands (same command run 3+ times)
    cmd_counts: dict[str, int] = {}
    for c in commands_run:
        cmd_counts[c] = cmd_counts.get(c, 0) + 1
    repeated_commands = [c for c, cnt in cmd_counts.items() if cnt >= 3]

    return {
        "n_turns": n_turns,
        "n_tool_calls": n_tool_calls,
        "n_errors": n_errors,
        "n_timeouts": n_timeouts,
        "tools_used": tools_used,
        "submitted": submitted,
        "submit_value": submit_value,
        "repeated_commands": repeated_commands,
        "error_snippets": error_messages[:5],
    }


def _compress_trajectory(conversation: list[dict]) -> str:
    """Compress a trajectory into a failure-focused summary."""
    events: list[dict] = []
    prev_cmd = ""

    for msg in conversation:
        role = msg.get("role", "")
        if role == "assistant":
            for tc in msg.get("tool_calls", []):
                fn = tc.get("function", "")
                args = tc.get("arguments", {})
                cmd = args.get("cmd", "") or args.get("command", "") or args.get("code", "")
                answer = args.get("answer", "")
                if fn in ("submit", "task_submit"):
                    events.append({"type": "submit", "value": answer})
                elif cmd:
                    prev_cmd = cmd[:200]
                    events.append({"type": "cmd", "fn": fn, "cmd": prev_cmd})
        elif role == "tool":
            content = (msg.get("content") or "").strip()
            is_error = (
                "ERROR:" in content
                or "error:" in content[:80].lower()
                or "Traceback" in content[:200]
                or "TIMEOUT" in content.upper()[:50]
                or "timed out" in content.lower()[:80]
                or "No such file" in content[:100]
                or "command not found" in content[:100]
            )
            if is_error:
                events.append({
                    "type": "error",
                    "cmd": prev_cmd,
                    "output": content[:300],
                })

    # Build compressed summary
    parts: list[str] = []
    n_cmds = sum(1 for e in events if e["type"] == "cmd")
    n_errors = sum(1 for e in events if e["type"] == "error")
    submitted = any(e["type"] == "submit" for e in events)

    parts.append(f"Commands: {n_cmds}, Errors: {n_errors}, Submitted: {submitted}")

    # First 3 commands (approach)
    cmds_seen = 0
    for e in events:
        if e["type"] == "cmd":
            cmds_seen += 1
            if cmds_seen <= 3:
                parts.append(f"[start] {e['fn']}({e['cmd']})")

    # All errors with context
    if n_errors > 0:
        parts.append(f"\n--- Errors ({n_errors}) ---")
        for e in events:
            if e["type"] == "error":
                parts.append(f"  cmd: {e.get('cmd', '?')}")
                parts.append(f"  err: {e['output'][:200]}")

    # Detect loops
    cmd_list = [e["cmd"] for e in events if e["type"] == "cmd"]
    cmd_counts: dict[str, int] = {}
    for c in cmd_list:
        cmd_counts[c] = cmd_counts.get(c, 0) + 1
    loops = {c: n for c, n in cmd_counts.items() if n >= 3}
    if loops:
        parts.append("\n--- Repeated commands ---")
        for c, n in loops.items():
            parts.append(f"  {c} (x{n})")

    # Last 3 commands
    last_cmds = [e for e in events if e["type"] == "cmd"][-3:]
    if last_cmds:
        parts.append("\n--- Final commands ---")
        for e in last_cmds:
            parts.append(f"  {e['fn']}({e['cmd']})")

    if submitted:
        submit_events = [e for e in events if e["type"] == "submit"]
        if submit_events:
            parts.append(f"\n[submitted] {submit_events[-1].get('value', '')}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# LLM Judge (from a-evolve adaptive_skill)
# ---------------------------------------------------------------------------

JUDGE_SYSTEM_PROMPT = """\
You are evaluating whether an AI agent successfully completed a command-line task.
You can ONLY see the agent's actions (commands run and their outputs). You do NOT have access to the actual test results.
Based on the trajectory, estimate whether the task was completed successfully."""

JUDGE_USER_TEMPLATE = """\
Task: {task_id}

Agent trajectory:
{trajectory}

Based on this trajectory, evaluate the agent's performance:
1. Score (0-10): 0=complete failure, 5=partial progress, 10=likely fully solved
2. Category: What type of task is this? (build, debug, data-science, security, scientific, system-admin, software-engineering, etc.)
3. Outcome: One sentence describing what happened.
4. Failure reason: If score < 7, what specific thing went wrong? Be concrete.

Respond in JSON format:
{{"score": N, "category": "...", "outcome": "...", "failure_reason": "..."}}"""


def _judge_one_task(
    task_name: str,
    compressed_trajectory: str,
    region: str,
    model_id: str,
) -> dict:
    """Use an LLM to score a trajectory as a proxy for success/failure."""
    prompt = JUDGE_USER_TEMPLATE.format(
        task_id=task_name, trajectory=compressed_trajectory,
    )
    client = _get_client(region)
    resp, err = _call_bedrock(
        client, model_id, JUDGE_SYSTEM_PROMPT, prompt,
        max_tokens=300, temperature=0.0,
    )
    if err or not resp:
        return {"score": -1, "category": "unknown", "outcome": "judge unavailable", "failure_reason": ""}
    try:
        text = resp.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {"score": -1, "category": "unknown", "outcome": f"parse error: {resp[:100]}", "failure_reason": ""}




def _format_signals(signals: dict) -> str:
    """Format trajectory signals as a concise text block."""
    lines = [
        f"Turns: {signals['n_turns']}, Tool calls: {signals['n_tool_calls']}, "
        f"Errors: {signals['n_errors']}, Timeouts: {signals['n_timeouts']}",
        f"Tools: {', '.join(f'{k}({v})' for k, v in signals['tools_used'].items())}",
        f"Submitted: {signals['submitted']}",
    ]
    if signals.get("repeated_commands"):
        lines.append(f"Repeated commands: {'; '.join(signals['repeated_commands'][:3])}")
    if signals.get("error_snippets"):
        lines.append(f"Error samples: {'; '.join(s[:60] for s in signals['error_snippets'][:3])}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Analyze + Propose prompt (merged, in solver conversation context)
# ---------------------------------------------------------------------------

ANALYZE_AND_PROPOSE_FAIL_PROMPT = """\
The evaluation result for this task:

{eval_result}

## Judge Assessment
{judge_verdict}

## Trajectory signals
{trajectory_signals}

## Compressed trajectory
{compressed_trajectory}

{existing_skills_section}

## Step 1: Analyze the result
Consider the judge assessment, test output, trajectory signals (errors, loops, timeouts), and compressed trajectory.
For EACH distinct issue or failure reason, output:
ISSUE: <one-line summary of what went wrong or what was needed>
DETAIL: <specific commands, files, or techniques that were missing>

## Step 2: Propose a GENERALIZABLE skill
Extract a skill that helps on a BROAD CLASS of similar tasks, not just this one task.

TOPIC: <kebab-case BROAD domain>
ACTION: NEW / ENHANCE / NONE
TARGET: existing_skill_name (only for ENHANCE)
NAME: short-kebab-name describing the TECHNIQUE CLASS, not a specific tool or task
DESCRIPTION: one sentence saying WHEN this skill applies — the agent sees ONLY this line to decide whether to read the skill. Must be broad enough to cover multiple tasks but specific enough to be useful.
CONTENT:
## When to use
- (what class of tasks this applies to)
## Key techniques
- (specific commands, tools, or approaches that work across tasks in this domain)
## Common pitfalls
- (failure modes that recur in this domain, with solutions)

Rules:
- Bullet points, not paragraphs. CONTENT must be under 200 words.
- Be SPECIFIC: include actual commands, flags, patterns — but generalize them (use placeholders like <model>, <arch>, <input_file>)
- The skill must be useful for UNSEEN future tasks, not just a replay of this task
- Do NOT include task-specific details (specific filenames, URLs, dataset names, model names)
- CONTENT should cover the TECHNIQUE CLASS, not one instance
- Prefer ENHANCE over NEW if an existing skill covers the same domain
- TOPIC should match an existing topic if applicable; create a new one only if no existing topic fits
- If the failure was purely task-specific with no generalizable lesson, output ACTION: NONE"""


ANALYZE_AND_PROPOSE_PASS_PROMPT = """\
You PASSED this task. Now extract GENERALIZABLE experience that helps on a BROAD CLASS of similar tasks.

## Trajectory signals
{trajectory_signals}

## Compressed trajectory
{compressed_trajectory}

{existing_skills_section}

## Step 1: Identify transferable patterns
Review your trajectory and extract what FUTURE tasks in the same DOMAIN can reuse.
Focus on:
- **Non-obvious commands or flags** that apply broadly (not just to this task)
- **Detours and corrections**: if you tried something that failed before finding the right approach, that detour IS the lesson for the whole domain
- **Domain-specific patterns**: tool quirks, config conventions, common gotchas
- **Efficient pipelines**: command chains or workflows reusable across tasks

For each pattern, output:
PATTERN: <one-line reusable insight — must apply beyond this specific task>
COMMANDS: <the actual command(s) or technique>

Skip anything trivial (cd, ls, cat) or that any engineer would know.
Skip anything specific to this one task (specific filenames, URLs, dataset names).

## Step 2: Propose a GENERALIZABLE skill (only if you found non-trivial patterns)
Distill the patterns into a concise, reusable skill about the TECHNIQUE, not the task.

TOPIC: <kebab-case BROAD domain>
ACTION: NEW / ENHANCE / NONE
TARGET: existing_skill_name (only for ENHANCE)
NAME: short-kebab-name describing the TECHNIQUE CLASS, not a specific tool or task
DESCRIPTION: one sentence saying WHEN this skill applies — must be broad enough to cover multiple tasks but specific enough to be useful.
CONTENT:
## When to use
- (what class of tasks this applies to)
## Key techniques
- (transferable command patterns, pipelines, or approaches)
## Detours to avoid
- (approaches that looked right but failed, and why — generalizable lessons)

Rules:
- Bullet points only. CONTENT must be under 150 words.
- ONLY include what is NOT obvious — no generic advice.
- Do NOT include task-specific details (specific filenames, URLs, dataset names, model names)
- CONTENT should cover the TECHNIQUE CLASS, not one instance (use placeholders like <model>, <arch>, <format>)
- The skill must be useful for UNSEEN future tasks, not a replay of this task
- If your trajectory was straightforward with no non-trivial insights, output ACTION: NONE.
- Prefer ENHANCE over NEW if an existing skill covers the same domain.
- TOPIC should match an existing topic if applicable."""

# ---------------------------------------------------------------------------
# Curator prompt (reviews proposals per topic)
# ---------------------------------------------------------------------------

CURATOR_PROMPT = """\
You are a skill curator for a terminal task-solving agent. You review skill proposals \
and decide which to keep in the skill library for topic: {topic}.

Your goal: build a library of BROADLY REUSABLE skills, not task-specific walkthroughs.

## Current Skill Library ({n_skills}/{max_skills} slots used):
{existing_skills_list}

## Proposals from this batch:
{proposals_list}

For each proposal, output ONE of:

ACCEPT: <proposal_name>

MERGE: <proposal_name> INTO <existing_skill_name>
NEW_CONTENT:
(merged content, under 200 words, bullet points only)

SKIP: <proposal_name>
REASON: <brief reason>

Rules:
- SKIP proposals that are purely task-specific walkthroughs with no reusable insight
- Overlaps existing → MERGE (preferred). When merging, keep specific commands and techniques
- Budget full → can only MERGE existing, or SKIP
- Keep skills SHORT and SPECIFIC — actual commands, flags, and techniques
- Prefer MERGE over SKIP when possible

If no proposals: NO_PROPOSALS"""

# ---------------------------------------------------------------------------
# General curator prompt
# ---------------------------------------------------------------------------

GENERAL_CURATOR_PROMPT = """\
You are a meta-learning curator. You analyze patterns ACROSS tasks \
to distill general TECHNIQUES that help the agent on ANY terminal task.

Your goal: extract broadly applicable problem-solving strategies and tool-usage patterns, \
NOT summaries of individual task solutions.

## {analysis_heading} ({n_tasks} tasks):
{task_summaries}

## Current General Skills ({n_general}/{max_general} slots):
{general_skills_list}

For REPEATED patterns across 2+ different tasks, output:

NEW_GENERAL: <kebab-name describing the TECHNIQUE, e.g. "systematic-binary-analysis" not "solve-reverse-engineering">
DESCRIPTION: <one line saying WHEN this skill applies — the agent sees ONLY this line to decide whether to read the skill. Be specific: "For tasks requiring systematic analysis of compiled binaries" not "For security tasks">
CONTENT:
## When to use
- (what class of tasks or situations this applies to)
## Strategy
- (3-5 bullet points: specific commands, flags, pipelines — reusable across tasks)
## Common mistakes
- (1-2 pitfalls observed across tasks, with fixes)
(Under 200 words, bullet points only)

UPDATE_GENERAL: <existing-name>
NEW_CONTENT:
(updated content, under 200 words — generalize, don't append task-specific details)

DELETE_GENERAL: <existing-name>
REASON: <why — e.g. too task-specific, subsumed by another skill, contains forbidden content>

If no cross-task patterns: NO_PATTERNS

Rules:
- Max {max_general} general skills. Quality > quantity.
- Must appear in 2+ different tasks to be general.
- SPECIFIC and ACTIONABLE — actual commands and techniques, not generic advice
- Prefer UPDATE over NEW.
- DELETE skills that are too narrow or no longer useful."""

# ---------------------------------------------------------------------------
# Agent Evolver (bash-tool based, aggregates batch info like old a-evolve)
# ---------------------------------------------------------------------------

EVOLVER_BASH_TOOL = {
    "toolSpec": {
        "name": "bash",
        "description": "Run a bash command in the workspace directory. Use this to read, create, and update skill files.",
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The bash command to execute in the workspace directory",
                    }
                },
                "required": ["command"],
            }
        },
    }
}

AGENT_EVOLVER_SYSTEM = """\
You are a skill evolution agent for a terminal task-solving AI. \
You analyze batch results and evolve the skill library by writing files directly.

## Workspace Layout
```
skills/
  seed/            # Read-only seed skills — do NOT modify
  topic/<topic>/<skill-name>/SKILL.md
  general/<skill-name>/SKILL.md
  SKILL_TREE.md    # Auto-generated — do NOT edit
```

## Skill File Format
```
---
name: <kebab-case-name>
description: <one sentence, under 100 chars>
---

## Key techniques
- (specific commands, flags, patterns)
## Gotchas
- (pitfalls to avoid, with specifics)
```

## Your Job
1. Read the batch results (task outcomes, judge verdicts, compressed trajectories)
2. Identify patterns: what knowledge would have prevented failures? What techniques from successes transfer?
3. Use the bash tool to read existing skills, then create new or update existing skill files
4. Prefer updating/merging into existing skills over creating new ones
5. After all changes, summarize what you did

## Constraints
- Max {max_topic_skills} skills per topic directory, max {max_general_skills} general skills
- Skills must be SPECIFIC: actual commands, flags, file paths, tool quirks
- NO generic advice ("read carefully", "be thorough", "check errors")
- Bullet points only, under 200 words per skill body
- Do NOT touch skills/seed/ — those are read-only
"""

AGENT_CURATOR_SYSTEM = """\
You are a skill curation agent for a terminal task-solving AI. \
You merge and refine skill proposals from solvers into a flat skill library.

## Workspace Layout
```
skills/
  seed/            # Read-only seed skills — do NOT modify
  evolved/<skill-name>/SKILL.md   # YOUR output — flat, no sub-directories
  SKILL_TREE.md    # Auto-generated — do NOT edit
```

## Skill File Format
```
---
name: <kebab-case-name>
description: <one sentence, under 100 chars>
---

## Key techniques
- (specific commands, flags, patterns)
## Gotchas
- (pitfalls to avoid, with specifics)
```

## Your Job
You receive proposals from solvers who just completed tasks. Each proposal contains \
domain knowledge extracted in-context. Your job is to MERGE these into a compact, \
high-quality skill library under skills/evolved/.

1. Read existing skills with bash to see what's already there
2. MERGE similar proposals into BROAD skills:
   - Multiple task-specific proposals (e.g., "gpt2-c-inference", "fasttext-training", \
"bert-embedding") should become ONE broad skill (e.g., "ml-model-inference")
   - PRESERVE specific commands, flags, and gotchas from every proposal — don't lose details
   - A single skill can cover multiple related techniques (e.g., "build-and-compile" \
covers C builds, Rust builds, cross-compilation, gcov instrumentation)
3. UPDATE existing skills by appending new techniques rather than creating new ones
4. Write skills using bash:
   ```
   mkdir -p skills/evolved/<name>
   cat > skills/evolved/<name>/SKILL.md << 'SKILL'
   ...
   SKILL
   ```
5. Summarize all changes at the end

## Constraints
- HARD LIMIT: {max_skills} total skills under skills/evolved/ — count before writing
- If at limit, MERGE into existing skills instead of creating new ones
- Skills must be SPECIFIC: actual commands, flags, file paths, tool quirks
- NO generic advice ("read carefully", "be thorough", "check errors")
- Bullet points only, under 300 words per skill body
- Prefer FEWER broader skills over MANY narrow ones
- Do NOT touch skills/seed/ — those are read-only
- Do NOT create topic/ or general/ subdirectories — everything goes in skills/evolved/
"""

# ---------------------------------------------------------------------------
# Skill data structures
# ---------------------------------------------------------------------------

class SkillMeta:
    """Lightweight skill metadata."""
    def __init__(self, name: str, description: str, path: str, body: str = ""):
        self.name = name
        self.description = description
        self.path = path
        self.body = body


def load_skills(workspace_dir: Path) -> list[SkillMeta]:
    """Load all skills from workspace."""
    skills = []
    skills_dir = workspace_dir / "skills"
    if not skills_dir.exists():
        return skills
    for skill_file in sorted(skills_dir.rglob("SKILL.md")):
        if "disabled" in skill_file.parts:
            continue
        content = skill_file.read_text().strip()
        name = skill_file.parent.name
        desc = ""
        for line in content.split("\n"):
            if line.strip().startswith("description:"):
                desc = line.split(":", 1)[1].strip()
                break
        body = content
        if content.startswith("---"):
            end = content.find("---", 3)
            if end != -1:
                body = content[end + 3:].strip()
        rel_path = str(skill_file.parent.relative_to(workspace_dir))
        skills.append(SkillMeta(name, desc, rel_path, body))
    return skills


def _select_relevant_topics(
    task_prompt: str,
    topics: dict[str, list[SkillMeta]],
    region: str,
    model_id: str,
) -> list[str]:
    """Quick LLM call to select relevant topics for a task."""
    topic_list = "\n".join(
        f"- {topic}: {', '.join(s.name + ' — ' + s.description for s in skills)}"
        for topic, skills in sorted(topics.items())
    )
    prompt = (
        f"Task:\n{task_prompt}\n\n"
        f"Available skill topics:\n{topic_list}\n\n"
        f"Which topics are relevant to this task? "
        f"You are NOT required to select any topic — only select topics that are genuinely relevant. "
        f"Output ONLY a JSON list of topic names, e.g. [\"network-debugging\", \"git-ops\"]. "
        f"If none are relevant, output []."
    )
    client = _get_client(region)
    resp, err = _call_bedrock(client, model_id, "You select relevant skill topics.", prompt,
                              max_tokens=256, temperature=0.0)
    if err or not resp:
        return list(topics.keys())  # fallback: all topics
    # Parse JSON list
    try:
        # Extract JSON array from response
        start = resp.find("[")
        end = resp.rfind("]") + 1
        if start >= 0 and end > start:
            selected = json.loads(resp[start:end])
            return [t for t in selected if t in topics]
    except (json.JSONDecodeError, ValueError):
        pass
    return list(topics.keys())  # fallback: all topics


def build_system_prompt(
    skills: list[SkillMeta],
    lazy_load: bool = False,
    selected_topics: list[str] | None = None,
) -> tuple[str, list[str]]:
    """Build system prompt with skills. Returns (prompt_text, injected_skill_names).

    lazy_load=False (default): full body injected into prompt.
    lazy_load=True: only name+description, solver uses read_skill() tool.
    selected_topics: if provided, only inject topic skills from these topics (full injection only).
    """
    parts = [TB2_SYSTEM_PROMPT]

    seed_skills = [s for s in skills if "seed/" in s.path]
    topic_skills = [s for s in skills if "topic/" in s.path]
    gen_skills = [s for s in skills if "general/" in s.path]
    evolved_skills = [s for s in skills if "evolved/" in s.path]

    all_skills = seed_skills + topic_skills + gen_skills + evolved_skills
    if not all_skills:
        return "\n".join(parts), []

    if lazy_load:
        parts.append("\n\n## Available Skills\n")
        parts.append(
            "You have specialized skills available. "
            "Call `read_skill(name)` to load the full content "
            "before tackling a relevant challenge.\n"
        )
        for s in all_skills:
            parts.append(f"- **{s.name}**: {s.description}")
        parts.append(
            "\nAfter you think you have completed the task, "
            "read the self-verification skill to verify your solution."
        )
    else:
        if seed_skills:
            parts.append("\n\n## Core skills")
            for s in seed_skills:
                parts.append(f"\n### {s.name}\n{s.body}" if s.body else f"\n### {s.name}\n{s.description}")

        if topic_skills:
            by_topic: dict[str, list[SkillMeta]] = defaultdict(list)
            for s in topic_skills:
                path_parts = Path(s.path).parts
                topic = path_parts[2] if len(path_parts) > 3 else path_parts[1] if len(path_parts) > 2 else "other"
                by_topic[topic].append(s)

            if selected_topics is not None:
                by_topic = {t: ss for t, ss in by_topic.items() if t in selected_topics}

            if by_topic:
                parts.append("\n\n## Domain skills")
                for topic in sorted(by_topic):
                    parts.append(f"\n**[{topic}]**")
                    for s in by_topic[topic]:
                        parts.append(f"\n### {s.name}\n{s.body}" if s.body else f"\n### {s.name}\n{s.description}")

        if gen_skills:
            parts.append("\n\n## General strategies")
            for s in gen_skills:
                parts.append(f"\n### {s.name}\n{s.body}" if s.body else f"\n### {s.name}\n{s.description}")

        if evolved_skills:
            parts.append("\n\n## Evolved skills")
            for s in evolved_skills:
                parts.append(f"\n### {s.name}\n{s.body}" if s.body else f"\n### {s.name}\n{s.description}")

        parts.append(
            "\n\nAfter you think you have completed the task, "
            "read the self-verification skill above to verify your solution."
        )

    n_topic = len([s for s in topic_skills if selected_topics is None or
                   any(t in s.path for t in (selected_topics or []))])
    mode = "lazy" if lazy_load else "injected"
    logger.debug("%d seed + %d topic + %d general + %d evolved skills (%s)",
                 len(seed_skills), n_topic, len(gen_skills), len(evolved_skills), mode)

    return "\n".join(parts), [s.name for s in all_skills]


# ---------------------------------------------------------------------------
# Per-task pipeline
# ---------------------------------------------------------------------------

def _solve_one_task(
    task: TB2Task,
    model_id: str,
    region: str,
    max_tokens: int,
    system_prompt: str,
    skills: list[SkillMeta],
    workspace_dir: Path,
    log_dir: Path,
    do_propose: bool = True,
    lazy_load: bool = False,
    selector_model: str = "",
    curator_model: str = "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    evolve_all: bool = False,
    feedback_level: str = "standard",
) -> dict:
    """Full pipeline for one task: solve → evaluate → analyze+propose.

    Returns dict with task info, result, and optional proposal.
    """
    task_name = task.name
    t0 = time.time()
    container = None

    # Setup per-task logger
    task_log_dir = log_dir / task_name
    task_log_dir.mkdir(parents=True, exist_ok=True)
    task_log = logging.getLogger(f"task.{task_name}")
    task_log.setLevel(logging.DEBUG)
    task_log.propagate = False
    task_log.handlers.clear()
    fh = logging.FileHandler(task_log_dir / "solve.log", mode="w")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s", datefmt="%H:%M:%S"))
    task_log.addHandler(fh)

    try:
        # 1. Pull image + start container
        if not pull_image(task.docker_image):
            raise RuntimeError(f"Failed to pull image {task.docker_image}")
        container = TB2Container(task.docker_image)
        container.start()
        task_log.info("Container %s started for %s", container.container_name, task_name)

        # 2. Select relevant skills
        task_system_prompt = system_prompt
        skills_for_solver = None

        if lazy_load and skills:
            # Lazy load: all skills available via read_skill tool
            skills_for_solver = {s.name: s.body or s.description for s in skills}
        elif not lazy_load and skills and selector_model:
            # Full injection: select relevant topics first
            topic_skills = [s for s in skills if "topic/" in s.path]
            if topic_skills:
                by_topic: dict[str, list[SkillMeta]] = defaultdict(list)
                for s in topic_skills:
                    path_parts = Path(s.path).parts
                    topic = path_parts[2] if len(path_parts) > 3 else path_parts[1] if len(path_parts) > 2 else "other"
                    by_topic[topic].append(s)
                selected = _select_relevant_topics(
                    task.prompt, by_topic, region, selector_model,
                )
                task_system_prompt, _ = build_system_prompt(skills, selected_topics=selected)
                task_log.info("Selected topics: %s (from %d available)", selected, len(by_topic))

        # 3. Solve via ReAct
        timeout_sec = task.metadata.get("agent_timeout_sec", 900)
        react_result = react_solve(
            task_prompt=f"{task.prompt}\n",
            container_name=container.container_name,
            model_id=model_id,
            region=region,
            max_tokens=max_tokens,
            timeout_sec=timeout_sec,
            log=task_log,
            system_prompt=task_system_prompt,
            skills=skills_for_solver,
        )
        conversation = extract_conversation(react_result.messages)
        usage = {
            "input_tokens": react_result.total_input_tokens,
            "output_tokens": react_result.total_output_tokens,
        }
        solve_time = time.time() - t0

        # 4. Evaluate (run test.sh)
        passed = False
        eval_output = ""
        if task.test_sh_path and os.path.exists(task.test_sh_path):
            if not container._running:
                container.start()
            container.exec("mkdir -p /tests /logs/verifier")
            if task.files:
                for cpath, lpath in task.files.items():
                    if os.path.exists(lpath):
                        try:
                            container.copy_to(lpath, cpath)
                        except Exception as e:
                            task_log.warning("Copy failed %s: %s", lpath, e)
            else:
                container.copy_to(task.test_sh_path, "/tests/test.sh")
                if task.test_py_path and os.path.exists(task.test_py_path):
                    container.copy_to(task.test_py_path, "/tests/test_outputs.py")

            verifier_timeout = task.metadata.get("verifier_timeout_sec", 900)
            passed, eval_output = container.run_tests_with_retry(
                task.test_sh_path, timeout=verifier_timeout, max_retries=3
            )

        container.stop()
        container = None

        task_log.info("RESULT: %s (%.0fs)", "PASS" if passed else "FAIL", time.time() - t0)

        # 5. Extract trajectory signals
        traj_signals = _extract_trajectory_signals(conversation)
        compressed_traj = _compress_trajectory(conversation)

        # 6. LLM Judge (trajectory-based assessment)
        judge_verdict = None
        if compressed_traj:
            try:
                judge_verdict = _judge_one_task(
                    task_name, compressed_traj, region, curator_model,
                )
                task_log.info("Judge: score=%s, category=%s, outcome=%s",
                              judge_verdict.get("score", -1),
                              judge_verdict.get("category", "?"),
                              _truncate(judge_verdict.get("outcome", ""), 80))
            except Exception as e:
                task_log.warning("Judge failed: %s", str(e)[:100])

        # 7. Analyze + Propose (for failures, in conversation context)
        feedback_analysis = None
        proposal = None

        if do_propose and (not passed or evolve_all) and react_result.messages:
            try:
                # Build eval result text
                status_label = "PASSED" if passed else "FAILED"
                if feedback_level == "minimal":
                    eval_text = status_label
                elif feedback_level == "standard":
                    eval_text = f"{status_label}\nTest output:\n{_truncate(eval_output, 500)}"
                else:  # full
                    eval_text = f"{status_label}\nTest output:\n{_truncate(eval_output, 2000)}"

                # Existing skills (all topics + general)
                existing_all_skills = []
                skills_dir = workspace_dir / "skills"
                if skills_dir.exists():
                    for sf in sorted(skills_dir.rglob("SKILL.md")):
                        sn = sf.parent.name
                        # Extract topic from path
                        rel = sf.parent.relative_to(skills_dir)
                        topic_tag = rel.parts[1] if len(rel.parts) > 2 else rel.parts[0]
                        sd = ""
                        for sline in sf.read_text().split("\n"):
                            if sline.strip().startswith("description:"):
                                sd = sline.split(":", 1)[1].strip()
                                break
                        existing_all_skills.append((sn, topic_tag, sd))

                if existing_all_skills:
                    skills_section = "Current skills:\n" + "\n".join(
                        f"- **{n}** [{t}]: {d}" for n, t, d in existing_all_skills
                    )
                else:
                    skills_section = "No existing skills yet."

                if passed:
                    prompt_text = ANALYZE_AND_PROPOSE_PASS_PROMPT.format(
                        trajectory_signals=_format_signals(traj_signals),
                        compressed_trajectory=_truncate(compressed_traj, 1500),
                        existing_skills_section=skills_section,
                    )
                else:
                    judge_text = "No judge assessment available."
                    if judge_verdict and judge_verdict.get("score", -1) >= 0:
                        judge_text = (
                            f"Score: {judge_verdict['score']}/10\n"
                            f"Category: {judge_verdict.get('category', 'unknown')}\n"
                            f"Outcome: {judge_verdict.get('outcome', '')}\n"
                            f"Failure reason: {judge_verdict.get('failure_reason', '')}"
                        )
                    prompt_text = ANALYZE_AND_PROPOSE_FAIL_PROMPT.format(
                        eval_result=eval_text,
                        judge_verdict=judge_text,
                        trajectory_signals=_format_signals(traj_signals),
                        compressed_trajectory=_truncate(compressed_traj, 1500),
                        existing_skills_section=skills_section,
                    )

                # Continue in solver conversation context
                messages = list(react_result.messages)
                messages.append({"role": "user", "content": [{"text": prompt_text}]})

                import boto3
                from botocore.config import Config as BotoConfig
                client = boto3.client(
                    "bedrock-runtime", region_name=region,
                    config=BotoConfig(read_timeout=300, retries={"max_attempts": 0}),
                )
                # Must include toolConfig because messages contain toolUse/toolResult blocks
                _inf_cfg = {"maxTokens": 1536}
                if "opus-4-7" not in model_id:
                    _inf_cfg["temperature"] = 0.3
                resp = client.converse(
                    modelId=model_id,
                    messages=messages,
                    system=[{"text": task_system_prompt}],
                    toolConfig={"tools": TOOL_SPECS},
                    inferenceConfig=_inf_cfg,
                )
                resp_text = ""
                for b in resp.get("output", {}).get("message", {}).get("content", []):
                    resp_text += b.get("text", "")

                if resp_text:
                    # Extract analysis (before ACTION:)
                    action_idx = resp_text.upper().find("ACTION:")
                    if action_idx > 0:
                        feedback_analysis = resp_text[:action_idx].strip()
                    else:
                        feedback_analysis = resp_text.strip()

                    # Parse proposal
                    proposal = _parse_proposal(resp_text, task_name)

            except Exception as e:
                task_log.warning("Analyze+propose failed: %s", str(e)[:200])

        # Save artifacts
        (task_log_dir / "result.txt").write_text(f"passed={passed}\n{eval_output}")
        (task_log_dir / "conversation.json").write_text(
            json.dumps(conversation, indent=2, ensure_ascii=False, default=str)
        )

        return {
            "task_name": task_name,
            "difficulty": task.metadata.get("difficulty", "unknown"),
            "passed": passed,
            "eval_output": _truncate(eval_output, 2000),
            "usage": usage,
            "solve_time": solve_time,
            "total_time": time.time() - t0,
            "feedback_analysis": feedback_analysis,
            "proposal": proposal,
            "trajectory_signals": traj_signals,
            "compressed_trajectory": compressed_traj,
            "judge_verdict": judge_verdict,
            "metadata": task.metadata,
        }

    except Exception as e:
        task_log.error("FATAL: %s", str(e)[:500])
        return {
            "task_name": task_name,
            "difficulty": task.metadata.get("difficulty", "unknown"),
            "passed": False,
            "eval_output": f"ERROR: {str(e)[:500]}",
            "usage": {},
            "solve_time": 0,
            "total_time": time.time() - t0,
            "feedback_analysis": None,
            "proposal": None,
            "metadata": task.metadata,
            "error": str(e)[:500],
        }
    finally:
        if container is not None:
            try:
                container.stop()
            except Exception:
                pass


def _parse_proposal(resp: str, task_name: str) -> dict | None:
    """Parse skill proposal from LLM response."""
    if "ACTION: NONE" in resp.upper():
        return None

    proposal = {
        "source_task": task_name,
        "topic": "general",
        "raw": resp,
        "action": "NEW",
        "target": "",
        "name": "",
        "description": "",
        "content": "",
    }

    for line in resp.split("\n"):
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("TOPIC:"):
            raw_topic = stripped.split(":", 1)[1].strip()
            proposal["topic"] = re.sub(r"[^a-z0-9-]", "-", raw_topic.lower()).strip("-") or "general"
        elif upper.startswith("ACTION:"):
            proposal["action"] = stripped.split(":", 1)[1].strip().upper()
        elif upper.startswith("TARGET:"):
            proposal["target"] = stripped.split(":", 1)[1].strip()
        elif upper.startswith("NAME:"):
            raw = stripped.split(":", 1)[1].strip()
            proposal["name"] = re.sub(r"[^a-z0-9-]", "-", raw.lower()).strip("-")
        elif upper.startswith("DESCRIPTION:"):
            proposal["description"] = stripped.split(":", 1)[1].strip()[:150]

    idx = resp.upper().find("CONTENT:")
    if idx >= 0:
        raw_content = resp[idx + len("CONTENT:"):].strip()
        # Strip trailing metadata lines that may leak after content
        _META_TAGS = ("TOPIC:", "ACTION:", "TARGET:", "NAME:", "DESCRIPTION:")
        lines = raw_content.split("\n")
        while lines:
            last = lines[-1].strip().upper()
            if any(last.startswith(tag) for tag in _META_TAGS):
                lines.pop()
            else:
                break
        proposal["content"] = "\n".join(lines).strip()

    if proposal["action"] == "ENHANCE" and proposal["target"] and not proposal["name"]:
        proposal["name"] = proposal["target"]
    if not proposal["name"] and proposal["action"] != "NONE":
        proposal["name"] = f"skill-{task_name[:20]}"
    if not proposal["content"]:
        return None

    return proposal


# ---------------------------------------------------------------------------
# Curation functions
# ---------------------------------------------------------------------------

def _curate_topic_proposals(
    topic: str,
    proposals: list[dict],
    workspace_dir: Path,
    region: str,
    model: str,
    max_skills: int = 5,
) -> dict:
    """Curator reviews proposals for one topic."""
    if not proposals:
        return {"added": 0, "merged": 0, "skipped": 0}

    topic_dir = workspace_dir / "skills" / "topic" / topic
    existing = []
    if topic_dir.exists():
        for sf in sorted(topic_dir.rglob("SKILL.md")):
            content = sf.read_text()
            sn = sf.parent.name
            sd = ""
            for sline in content.split("\n"):
                if sline.strip().startswith("description:"):
                    sd = sline.split(":", 1)[1].strip()
                    break
            existing.append((sn, sd))

    existing_list = "\n".join(f"- **{n}**: {d}" for n, d in existing) if existing else "(empty)"
    proposals_lines = []
    for p in proposals:
        proposals_lines.append(
            f"### [{p.get('action', 'NEW')}] {p.get('name', '?')}\n"
            f"- Source task: {p['source_task']}\n"
            f"- Description: {p.get('description', '')[:150]}\n"
            f"- Content:\n{_truncate(p.get('content', ''), 300)}"
        )

    prompt = CURATOR_PROMPT.format(
        topic=topic,
        n_skills=len(existing),
        max_skills=max_skills,
        existing_skills_list=existing_list,
        proposals_list="\n\n".join(proposals_lines),
    )

    client = _get_client(region)
    resp, err = _call_bedrock(client, model, prompt, "Review and decide.", max_tokens=2048, temperature=0.0)
    if err or not resp:
        logger.warning("Curator failed for topic %s: %s", topic, err)
        return {"added": 0, "merged": 0, "skipped": 0}

    return _execute_topic_curation(resp, proposals, existing, workspace_dir, topic, max_skills)


def _execute_topic_curation(
    text: str, proposals: list[dict], existing: list[tuple[str, str]],
    workspace_dir: Path, topic: str, max_skills: int,
) -> dict:
    """Parse curator decisions, write skills."""
    proposal_map = {p["name"]: p for p in proposals}
    existing_names = {n for n, _ in existing}
    count = len(existing)
    stats = {"added": 0, "merged": 0, "skipped": 0}

    def _write(name, desc, content):
        d = workspace_dir / "skills" / "topic" / topic / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n\n{content}")

    def _fuzzy(raw, names):
        clean = re.sub(r"[^a-z0-9-]", "-", raw.lower()).strip("-")
        if clean in names:
            return clean
        for n in names:
            if clean in n or n in clean:
                return n
        return None

    for line in text.split("\n"):
        s = line.strip()
        u = s.upper()
        if u.startswith("ACCEPT:"):
            pn = _fuzzy(s.split(":", 1)[1].strip(), set(proposal_map.keys()))
            if pn and pn not in existing_names and count < max_skills:
                p = proposal_map[pn]
                _write(pn, p.get("description", ""), p.get("content", ""))
                existing_names.add(pn)
                count += 1
                stats["added"] += 1
        elif u.startswith("MERGE:"):
            parts = s.split(":", 1)[1].strip()
            if " INTO " in parts.upper():
                sp = parts.split(" INTO " if " INTO " in parts else " into ")
                pn = _fuzzy(sp[0].strip(), set(proposal_map.keys()))
                tn = _fuzzy(sp[1].strip() if len(sp) > 1 else "", existing_names)
                if pn and tn:
                    merge_idx = text.find(s)
                    after = text[merge_idx + len(s):]
                    nc = ""
                    if "NEW_CONTENT:" in after:
                        nc = after.split("NEW_CONTENT:", 1)[1]
                        for m in ["ACCEPT:", "MERGE:", "SKIP:", "NO_PROPOSALS"]:
                            if m in nc:
                                nc = nc[:nc.index(m)]
                        nc = nc.strip()
                    if nc:
                        old_desc = next((d for n, d in existing if n == tn), "")
                        _write(tn, old_desc or proposal_map.get(pn, {}).get("description", ""), nc)
                        stats["merged"] += 1
        elif u.startswith("SKIP:"):
            stats["skipped"] += 1

    return stats


def _curate_general_skills(
    task_summaries: list[dict],
    workspace_dir: Path,
    region: str,
    model: str,
    max_general: int = 10,
    evolve_all: bool = False,
    feedback_level: str = "standard",
) -> dict:
    """General curator: cross-task patterns."""
    if not task_summaries:
        return {"added": 0, "updated": 0, "deleted": 0}

    summary_lines = []
    for s in task_summaries[:30]:
        status = "PASS" if s.get("passed") else "FAIL"
        parts = [f"### {s['task_name']} [{status}] (difficulty: {s.get('difficulty', '?')})"]
        jv = s.get("judge_verdict")
        if jv and jv.get("score", -1) >= 0:
            parts.append(f"- Judge: score={jv['score']}/10, category={jv.get('category','?')}, "
                         f"failure={jv.get('failure_reason','')[:100]}")
        if s.get("trajectory_signals"):
            parts.append(f"- Signals: {_format_signals(s['trajectory_signals'])}")
        if feedback_level == "minimal":
            pass  # only task_name, status, difficulty, trajectory_signals
        elif feedback_level == "standard":
            if s.get("compressed_trajectory"):
                parts.append(f"- Trajectory: {_truncate(s['compressed_trajectory'], 400)}")
            if s.get("feedback_analysis"):
                parts.append(f"- Analysis: {_truncate(s['feedback_analysis'], 400)}")
        else:  # full
            if s.get("compressed_trajectory"):
                parts.append(f"- Trajectory: {_truncate(s['compressed_trajectory'], 1000)}")
            if s.get("feedback_analysis"):
                parts.append(f"- Analysis: {_truncate(s['feedback_analysis'], 1000)}")
        if s.get("proposal_summary"):
            parts.append(f"- Proposal: {_truncate(s['proposal_summary'], 200)}")
        summary_lines.append("\n".join(parts))

    gen_dir = workspace_dir / "skills" / "general"
    existing = []
    if gen_dir.exists():
        for sf in sorted(gen_dir.rglob("SKILL.md")):
            content = sf.read_text()
            sn = sf.parent.name
            sd = ""
            for sline in content.split("\n"):
                if sline.strip().startswith("description:"):
                    sd = sline.split(":", 1)[1].strip()
                    break
            body = content
            if content.startswith("---"):
                end = content.find("---", 3)
                if end != -1:
                    body = content[end + 3:].strip()
            existing.append((sn, sd, body[:300]))

    gen_list = "\n".join(
        f"- **{n}**: {d}" for n, d, _ in existing
    ) if existing else "(empty)"

    analysis_heading = (
        "All Task Analysis (passed + failed)"
        if evolve_all else "Failed Task Analysis"
    )
    prompt = GENERAL_CURATOR_PROMPT.format(
        analysis_heading=analysis_heading,
        n_tasks=len(task_summaries),
        task_summaries="\n\n".join(summary_lines),
        n_general=len(existing),
        max_general=max_general,
        general_skills_list=gen_list,
    )

    client = _get_client(region)
    resp, err = _call_bedrock(client, model, prompt, "Analyze and decide.", max_tokens=4096, temperature=0.0)
    if err or not resp:
        return {"added": 0, "updated": 0, "deleted": 0}

    return _execute_general_curation(resp, workspace_dir, existing, max_general)


def _execute_general_curation(text, workspace_dir, existing, max_general):
    """Parse general curator decisions."""
    existing_names = {n for n, _, _ in existing}
    count = len(existing)
    stats = {"added": 0, "updated": 0, "deleted": 0}

    def _clean_content(raw: str) -> str:
        """Strip trailing meta commentary that isn't skill content."""
        lines = raw.split("\n")
        while lines:
            last = lines[-1].strip()
            if not last:
                lines.pop()
                continue
            # Drop lines that are clearly curator meta commentary, not skill content
            lower = last.lower()
            if (lower.startswith("no other") or lower.startswith("no new")
                    or lower.startswith("no additional") or lower.startswith("no further")
                    or lower.startswith("the ") and "skill" in lower and ("valid" in lower or "covered" in lower or "needed" in lower)
                    or lower.startswith("no deletion") or lower.startswith("no changes")):
                lines.pop()
            else:
                break
        return "\n".join(lines).strip()

    def _write_gen(name, desc, content):
        d = workspace_dir / "skills" / "general" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n\n{_clean_content(content)}")

    lines = text.split("\n")
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        u = s.upper()

        if u.startswith("NEW_GENERAL:"):
            name = re.sub(r"[^a-z0-9-]", "-", s.split(":", 1)[1].strip().lower()).strip("-")
            desc, content = "", ""
            i += 1
            while i < len(lines):
                sl = lines[i].strip()
                if sl.upper().startswith("DESCRIPTION:"):
                    desc = sl.split(":", 1)[1].strip()[:150]
                elif sl.upper().startswith("CONTENT:"):
                    cl = []
                    i += 1
                    while i < len(lines):
                        su = lines[i].strip().upper()
                        if any(su.startswith(m) for m in ["NEW_GENERAL:", "UPDATE_GENERAL:", "DELETE_GENERAL:", "NO_PATTERNS"]):
                            break
                        cl.append(lines[i])
                        i += 1
                    content = "\n".join(cl).strip()
                    break
                i += 1
            if name and content and count < max_general:
                _write_gen(name, desc, content)
                count += 1
                stats["added"] += 1
            continue

        elif u.startswith("UPDATE_GENERAL:"):
            raw = s.split(":", 1)[1].strip()
            name = re.sub(r"[^a-z0-9-]", "-", raw.lower()).strip("-")
            matched = next((n for n in existing_names if name == n or name in n or n in name), None)
            content = ""
            i += 1
            while i < len(lines):
                sl = lines[i].strip()
                if sl.upper().startswith("NEW_CONTENT:") or sl.upper().startswith("CONTENT:"):
                    cl = []
                    i += 1
                    while i < len(lines):
                        su = lines[i].strip().upper()
                        if any(su.startswith(m) for m in ["NEW_GENERAL:", "UPDATE_GENERAL:", "DELETE_GENERAL:", "NO_PATTERNS"]):
                            break
                        cl.append(lines[i])
                        i += 1
                    content = "\n".join(cl).strip()
                    break
                i += 1
            if matched and content:
                old_desc = next((d for n, d, _ in existing if n == matched), "")
                _write_gen(matched, old_desc, content)
                stats["updated"] += 1
            continue

        elif u.startswith("DELETE_GENERAL:"):
            raw = s.split(":", 1)[1].strip()
            name = re.sub(r"[^a-z0-9-]", "-", raw.lower()).strip("-")
            matched = next((n for n in existing_names if name == n or name in n or n in name), None)
            if matched:
                d = workspace_dir / "skills" / "general" / matched
                if d.exists():
                    shutil.rmtree(d)
                    existing_names.discard(matched)
                    count -= 1
                    stats["deleted"] += 1

        i += 1
    return stats


# ---------------------------------------------------------------------------
# Agent evolver functions
# ---------------------------------------------------------------------------

def _workspace_bash(command: str, workspace_dir: Path, timeout: int = 30) -> str:
    """Execute a bash command scoped to the workspace directory."""
    try:
        result = subprocess.run(
            ["bash", "-c", command],
            cwd=str(workspace_dir),
            capture_output=True, text=True, timeout=timeout,
        )
        output = result.stdout
        if result.stderr:
            output += f"\nSTDERR: {result.stderr}"
        if result.returncode != 0:
            output += f"\n(exit code: {result.returncode})"
        return output[:4000] or "(no output)"
    except subprocess.TimeoutExpired:
        return f"TIMEOUT after {timeout}s"
    except Exception as e:
        return f"ERROR: {str(e)[:200]}"


def _build_evolver_user_prompt(
    batch_results: list[dict],
    workspace_dir: Path,
    evolve_all: bool = False,
    feedback_level: str = "standard",
) -> str:
    """Build user prompt for the agent evolver with batch summaries."""
    passed = sum(1 for r in batch_results if r.get("passed"))
    failed = len(batch_results) - passed

    parts = [f"## Batch Results ({len(batch_results)} tasks, {passed} passed, {failed} failed)\n"]

    # Task summaries
    parts.append("### Task Summaries\n")
    for r in batch_results:
        status = "PASS" if r.get("passed") else "FAIL"
        # For passed tasks without evolve_all, just show one-liner
        if r.get("passed") and not evolve_all:
            parts.append(f"**{r['task_name']}** ({r.get('difficulty','?')}) [{status}]\n")
            continue

        parts.append(f"**{r['task_name']}** ({r.get('difficulty','?')}) [{status}]")

        if feedback_level == "minimal":
            # Only task name, status, difficulty, trajectory signals
            if r.get("trajectory_signals"):
                parts.append(f"Signals: {_format_signals(r['trajectory_signals'])}")
        elif feedback_level == "standard":
            jv = r.get("judge_verdict")
            if jv and jv.get("score", -1) >= 0:
                parts.append(
                    f"Judge: score={jv['score']}/10, category={jv.get('category','?')}, "
                    f"outcome={jv.get('outcome','')[:100]}, "
                    f"failure={jv.get('failure_reason','')[:150]}"
                )
            if r.get("trajectory_signals"):
                parts.append(f"Signals: {_format_signals(r['trajectory_signals'])}")
            if r.get("compressed_trajectory"):
                parts.append(f"Trajectory:\n{_truncate(r['compressed_trajectory'], 800)}")
        else:  # full
            jv = r.get("judge_verdict")
            if jv and jv.get("score", -1) >= 0:
                parts.append(
                    f"Judge: score={jv['score']}/10, category={jv.get('category','?')}, "
                    f"outcome={jv.get('outcome','')[:100]}, "
                    f"failure={jv.get('failure_reason','')[:150]}"
                )
            if r.get("trajectory_signals"):
                parts.append(f"Signals: {_format_signals(r['trajectory_signals'])}")
            if r.get("compressed_trajectory"):
                parts.append(f"Trajectory:\n{_truncate(r['compressed_trajectory'], 2000)}")

        parts.append("")

    # Current skill library
    parts.append("\n### Current Skill Library")
    parts.append("Use `bash` to read full contents. Summary:")
    skills_dir = workspace_dir / "skills"
    if skills_dir.exists():
        for category in ["seed", "topic", "general"]:
            cat_dir = skills_dir / category
            if not cat_dir.exists():
                continue
            skill_files = sorted(cat_dir.rglob("SKILL.md"))
            if skill_files:
                parts.append(f"\n**{category}/**")
                for sf in skill_files:
                    desc = ""
                    for line in sf.read_text().split("\n"):
                        if line.strip().startswith("description:"):
                            desc = line.split(":", 1)[1].strip()
                            break
                    rel = sf.relative_to(skills_dir)
                    parts.append(f"  - `{rel.parent}`: {desc}")
    else:
        parts.append("(no skills yet)")

    # Instructions
    parts.append("\n### Instructions")
    parts.append(
        "Analyze the batch results above. Use the bash tool to:\n"
        "1. `cat` any existing skills you want to review or update\n"
        "2. Create new skills: `mkdir -p skills/topic/<topic>/<name> && "
        "cat > skills/topic/<topic>/<name>/SKILL.md << 'SKILL' ...`\n"
        "3. Update existing skills by overwriting the SKILL.md file\n"
        "4. Focus on FAILED tasks: what knowledge would prevent these failures?\n"
    )
    if evolve_all:
        parts.append(
            "5. Also extract non-obvious techniques from PASSED tasks.\n"
        )
    parts.append(
        "After making changes, list a summary of what you created/updated."
    )

    return "\n".join(parts)


def _run_agent_loop(
    system_prompt: str,
    user_prompt: str,
    workspace_dir: Path,
    region: str,
    model_id: str,
    max_rounds: int = 15,
    label: str = "agent",
) -> dict:
    """Shared agentic loop: LLM + bash tool, multi-turn conversation."""
    messages = [{"role": "user", "content": [{"text": user_prompt}]}]

    client = _get_client(region)
    total_input = 0
    total_output = 0
    tool_calls = 0
    final_text = ""
    round_i = 0

    for round_i in range(max_rounds):
        try:
            _evolver_cfg = {"maxTokens": 4096}
            if "opus-4-7" not in model_id:
                _evolver_cfg["temperature"] = 0.3
            resp = client.converse(
                modelId=model_id,
                system=[{"text": system_prompt}],
                messages=messages,
                toolConfig={"tools": [EVOLVER_BASH_TOOL]},
                inferenceConfig=_evolver_cfg,
            )
        except Exception as e:
            err_str = str(e)
            logger.warning("%s converse failed (round %d): %s", label, round_i, err_str[:200])
            if "throttl" in err_str.lower():
                time.sleep(5 * (round_i + 1))
                continue
            break

        usage = resp.get("usage", {})
        total_input += usage.get("inputTokens", 0)
        total_output += usage.get("outputTokens", 0)

        assistant_content = resp.get("output", {}).get("message", {}).get("content", [])
        stop_reason = resp.get("stopReason", "")

        messages.append({"role": "assistant", "content": assistant_content})

        text_parts = []
        tool_uses = []
        for block in assistant_content:
            if "text" in block:
                text_parts.append(block["text"])
            elif "toolUse" in block:
                tool_uses.append(block["toolUse"])
        if text_parts:
            final_text = "\n".join(text_parts)

        if not tool_uses or stop_reason == "end_turn":
            break

        tool_results = []
        for tu in tool_uses:
            tool_calls += 1
            cmd = tu.get("input", {}).get("command", "")
            logger.debug("%s bash [%d]: %s", label, tool_calls, cmd[:120])
            output = _workspace_bash(cmd, workspace_dir)
            tool_results.append({
                "toolResult": {
                    "toolUseId": tu["toolUseId"],
                    "content": [{"text": output}],
                }
            })
        messages.append({"role": "user", "content": tool_results})

    logger.info("%s done: %d rounds, %d bash calls, %d/%d tokens",
                label, round_i + 1, tool_calls, total_input, total_output)
    return {
        "text": final_text,
        "tool_calls": tool_calls,
        "rounds": round_i + 1,
        "input_tokens": total_input,
        "output_tokens": total_output,
    }


def _run_agent_evolver(
    batch_results: list[dict],
    workspace_dir: Path,
    region: str,
    model_id: str,
    max_topic_skills: int = 5,
    max_general_skills: int = 10,
    evolve_all: bool = False,
    max_rounds: int = 15,
    feedback_level: str = "standard",
) -> dict:
    """Run the agent evolver: LLM with bash tool iteratively writes skills."""
    system = AGENT_EVOLVER_SYSTEM.format(
        max_topic_skills=max_topic_skills,
        max_general_skills=max_general_skills,
    )
    user = _build_evolver_user_prompt(batch_results, workspace_dir, evolve_all, feedback_level=feedback_level)
    return _run_agent_loop(system, user, workspace_dir, region, model_id,
                           max_rounds=max_rounds, label="evolver")


# ---------------------------------------------------------------------------
# Agent curator (hybrid: per-task proposals + agent curation)
# ---------------------------------------------------------------------------

def _build_curator_agent_prompt(
    proposals: list[dict],
    batch_results: list[dict],
    workspace_dir: Path,
    evolve_all: bool = False,
) -> str:
    """Build user prompt for the agent curator with all proposals."""
    passed = sum(1 for r in batch_results if r.get("passed"))
    failed = len(batch_results) - passed

    parts = [f"## Skill Proposals ({len(proposals)} proposals from "
             f"{len(batch_results)} tasks, {passed} passed, {failed} failed)\n"]

    # Build task status lookup for enriching proposals
    task_status = {}
    for r in batch_results:
        jv = r.get("judge_verdict") or {}
        task_status[r.get("task_name", "")] = {
            "passed": r.get("passed", False),
            "judge_score": jv.get("score", -1),
            "judge_category": jv.get("category", "?"),
        }

    # Proposals — primary input, ordered: failed tasks first
    failed_proposals = [p for p in proposals
                        if not task_status.get(p["source_task"], {}).get("passed", False)]
    passed_proposals = [p for p in proposals
                        if task_status.get(p["source_task"], {}).get("passed", False)]
    ordered_proposals = failed_proposals + passed_proposals

    for i, p in enumerate(ordered_proposals, 1):
        src = p["source_task"]
        ts = task_status.get(src, {})
        status = "PASS" if ts.get("passed") else "FAIL"
        score = ts.get("judge_score", -1)
        score_str = f", judge={score}/10" if score >= 0 else ""

        parts.append(
            f"### Proposal {i}: {p.get('name', '?')}\n"
            f"Source: {src} [{status}{score_str}]\n"
            f"Solver topic: {p.get('topic', '?')}  (for reference only — reclassify as needed)\n"
            f"Action: {p.get('action', 'NEW')}\n"
            f"Description: {p.get('description', '')}\n"
            f"Content:\n{p.get('content', '(empty)')}\n"
        )

    # Batch overview
    parts.append("\n## Batch Overview")
    parts.append(f"{len(batch_results)} tasks: {passed} passed, {failed} failed")
    no_proposal_tasks = [
        r["task_name"] for r in batch_results
        if not any(p["source_task"] == r["task_name"] for p in proposals)
    ]
    if no_proposal_tasks:
        parts.append(f"Tasks without proposals (ACTION: NONE): {', '.join(no_proposal_tasks[:20])}")

    # Current skill library
    parts.append("\n## Current Skill Library")
    parts.append("Use `bash` to read full contents before deciding to merge.")
    skills_dir = workspace_dir / "skills"
    n_evolved = 0
    if skills_dir.exists():
        for category in ["seed", "evolved"]:
            cat_dir = skills_dir / category
            if not cat_dir.exists():
                continue
            skill_files = sorted(cat_dir.rglob("SKILL.md"))
            if skill_files:
                parts.append(f"\n**{category}/** ({len(skill_files)} skills)")
                for sf in skill_files:
                    desc = ""
                    for line in sf.read_text().split("\n"):
                        if line.strip().startswith("description:"):
                            desc = line.split(":", 1)[1].strip()
                            break
                    parts.append(f"  - `{sf.parent.name}`: {desc}")
                if category == "evolved":
                    n_evolved = len(skill_files)
    else:
        parts.append("(no skills yet)")

    # Instructions
    parts.append("\n## Instructions")
    parts.append(f"Evolved skills: {n_evolved} (hard limit in system prompt)")
    parts.append(
        "1. Use bash to read existing evolved skills you want to review or merge into\n"
        "2. MERGE related proposals into broad skills — each skill should cover a DOMAIN, "
        "not a single task\n"
        "3. If an existing evolved skill covers a related domain, APPEND new techniques "
        "to it rather than creating a new skill\n"
        "4. Write skills:\n"
        "   ```\n"
        "   mkdir -p skills/evolved/<name>\n"
        "   cat > skills/evolved/<name>/SKILL.md << 'SKILL'\n"
        "   ---\n"
        "   name: <name>\n"
        "   description: <one line>\n"
        "   ---\n"
        "   <content>\n"
        "   SKILL\n"
        "   ```\n"
        "5. Summarize all changes at the end"
    )

    return "\n".join(parts)


def _run_agent_curator(
    proposals: list[dict],
    batch_results: list[dict],
    workspace_dir: Path,
    region: str,
    model_id: str,
    max_skills: int = 10,
    evolve_all: bool = False,
    max_rounds: int = 10,
) -> dict:
    """Run the agent curator: merge proposals + write skills via bash tool."""
    system = AGENT_CURATOR_SYSTEM.format(max_skills=max_skills)
    user = _build_curator_agent_prompt(proposals, batch_results, workspace_dir, evolve_all)
    return _run_agent_loop(system, user, workspace_dir, region, model_id,
                           max_rounds=max_rounds, label="curator")


# ---------------------------------------------------------------------------
# Skill tree
# ---------------------------------------------------------------------------

def update_skill_tree(workspace_dir: Path):
    """Regenerate SKILL_TREE.md."""
    skills = load_skills(workspace_dir)
    if not skills:
        content = "# Skill Tree\n\nNo skills yet.\n"
    else:
        lines = ["# Skill Tree", "", f"Total: {len(skills)}", ""]
        by_prefix = defaultdict(list)
        for s in skills:
            parts = Path(s.path).parts
            prefix = parts[1] if len(parts) > 2 else "root"
            by_prefix[prefix].append(s)
        for prefix in sorted(by_prefix):
            lines.append(f"## {prefix}/")
            for s in sorted(by_prefix[prefix], key=lambda x: x.name):
                lines.append(f"- **{s.name}**: {s.description}")
            lines.append("")
        content = "\n".join(lines)
    (workspace_dir / "skills" / "SKILL_TREE.md").write_text(content)


# ---------------------------------------------------------------------------
# Skill stats tracking and gating
# ---------------------------------------------------------------------------

def load_skill_stats(workspace_dir: Path) -> dict:
    stats_file = workspace_dir / "skills" / "STATS.json"
    if stats_file.exists():
        return json.loads(stats_file.read_text())
    return {}


def save_skill_stats(workspace_dir: Path, stats: dict):
    stats_file = workspace_dir / "skills" / "STATS.json"
    stats_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = stats_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(stats, indent=2))
    tmp.rename(stats_file)


def update_skill_stats(stats: dict, injected_skills: list[str], passed: bool) -> dict:
    for name in injected_skills:
        if name not in stats:
            stats[name] = {"inject_count": 0, "pass_count": 0, "fail_count": 0}
        stats[name]["inject_count"] += 1
        if passed:
            stats[name]["pass_count"] += 1
        else:
            stats[name]["fail_count"] += 1
    return stats




# ---------------------------------------------------------------------------
# Batch evolve loop
# ---------------------------------------------------------------------------

def evolve_batch(
    tasks: list[TB2Task],
    workspace_dir: Path,
    model_id: str,
    curator_model: str,
    region: str,
    max_tokens: int,
    batch_workers: int,
    max_skills_per_topic: int,
    max_general_skills: int,
    log_dir: Path,
    results_dir: Path,
    batch_label: str = "",
    no_evolve: bool = False,
    lazy_load: bool = False,
    selector_model: str = "",
    evolve_all: bool = False,
    agent_evolve: bool = False,
    agent_curate: bool = False,
    feedback_level: str = "standard",
    cumulative_task_rate: float = 0.0,
) -> list[dict]:
    """Run one batch: parallel solve → curate → update skills."""

    # Load current skills (always load — seed skills should be available even in no_evolve)
    skills = load_skills(workspace_dir)

    # Base system prompt (used as-is for lazy_load, or as fallback for full injection)
    system_prompt, injected_skill_names = build_system_prompt(skills, lazy_load=lazy_load)

    # Parallel solve
    logger.info("[%s] Solving %d tasks with %d workers, %d skills injected...",
                batch_label, len(tasks), batch_workers, len(injected_skill_names))
    task_outputs = {}
    with ThreadPoolExecutor(max_workers=batch_workers) as pool:
        futures = {}
        for t in tasks:
            fut = pool.submit(
                _solve_one_task,
                task=t, model_id=model_id, region=region,
                max_tokens=max_tokens, system_prompt=system_prompt,
                skills=skills, workspace_dir=workspace_dir,
                log_dir=log_dir, do_propose=not no_evolve and not agent_evolve,
                lazy_load=lazy_load,
                selector_model=selector_model,
                curator_model=curator_model,
                evolve_all=evolve_all,
                feedback_level=feedback_level,
            )
            futures[fut] = t
        for fut in as_completed(futures):
            t = futures[fut]
            try:
                task_outputs[t.name] = fut.result()
            except Exception as e:
                logger.error("Task %s failed: %s", t.name, e)
                task_outputs[t.name] = {
                    "task_name": t.name,
                    "passed": False, "error": str(e), "proposal": None,
                    "feedback_analysis": None, "difficulty": t.metadata.get("difficulty", "unknown"),
                }

    # Collect results
    results = []
    proposals = []
    task_summaries = []

    for t in tasks:
        out = task_outputs.get(t.name, {})
        passed = out.get("passed", False)
        results.append(out)

        # Save per-task result
        (results_dir / f"{t.name}.json").write_text(json.dumps(out, indent=2, default=str))

        if out.get("proposal"):
            proposals.append(out["proposal"])

        # Build summary: all tasks when evolve_all, only failed otherwise
        if not passed or evolve_all:
            proposal_summary = ""
            if out.get("proposal"):
                p = out["proposal"]
                proposal_summary = f"[{p.get('action', 'NEW')}] {p.get('name', '')}: {p.get('description', '')}"
            task_summaries.append({
                "task_name": t.name,
                "passed": passed,
                "difficulty": out.get("difficulty", "unknown"),
                "feedback_analysis": out.get("feedback_analysis", ""),
                "proposal_summary": proposal_summary,
                "trajectory_signals": out.get("trajectory_signals"),
                "compressed_trajectory": out.get("compressed_trajectory", ""),
                "judge_verdict": out.get("judge_verdict"),
            })

    passed_count = sum(1 for r in results if r.get("passed"))
    logger.info("[%s] %d/%d passed, %d proposals", batch_label, passed_count, len(tasks), len(proposals))

    # Track skill stats (no pruning)
    if not no_evolve and injected_skill_names:
        skill_stats = load_skill_stats(workspace_dir)
        for r in results:
            update_skill_stats(skill_stats, injected_skill_names, r.get("passed", False))
        save_skill_stats(workspace_dir, skill_stats)

    if no_evolve:
        return results

    if agent_evolve:
        # Agent evolver: single LLM with bash tool writes skills directly
        evolver_result = _run_agent_evolver(
            batch_results=results,
            workspace_dir=workspace_dir,
            region=region,
            model_id=curator_model,
            max_topic_skills=max_skills_per_topic,
            max_general_skills=max_general_skills,
            evolve_all=evolve_all,
            feedback_level=feedback_level,
        )
        logger.info("[%s] Agent evolver: %d bash calls in %d rounds",
                     batch_label,
                     evolver_result.get("tool_calls", 0),
                     evolver_result.get("rounds", 0))
        update_skill_tree(workspace_dir)
        new_skills = load_skills(workspace_dir)
        logger.info("[%s] Skills: %d total", batch_label, len(new_skills))
        return results

    if agent_curate:
        # Agent curator: per-task proposals + single agent merges & writes via bash
        if proposals:
            curator_result = _run_agent_curator(
                proposals=proposals,
                batch_results=results,
                workspace_dir=workspace_dir,
                region=region,
                model_id=curator_model,
                max_skills=max_skills_per_topic,
                evolve_all=evolve_all,
            )
            logger.info("[%s] Agent curator: %d bash calls in %d rounds, %d proposals",
                         batch_label,
                         curator_result.get("tool_calls", 0),
                         curator_result.get("rounds", 0),
                         len(proposals))
        else:
            logger.info("[%s] No proposals to curate", batch_label)
        update_skill_tree(workspace_dir)
        new_skills = load_skills(workspace_dir)
        logger.info("[%s] Skills: %d total", batch_label, len(new_skills))
        return results

    # Curate per topic (self-assigned by proposer)
    if proposals:
        topic_proposals = defaultdict(list)
        for p in proposals:
            topic_proposals[p.get("topic", "general")].append(p)

        total_stats = {"added": 0, "merged": 0, "skipped": 0}
        for topic, topic_props in topic_proposals.items():
            stats = _curate_topic_proposals(
                topic, topic_props, workspace_dir, region, curator_model, max_skills_per_topic,
            )
            for k in total_stats:
                total_stats[k] += stats.get(k, 0)

        logger.info("[%s] Topic curation: +%d added, %d merged, %d skipped",
                     batch_label, total_stats["added"], total_stats["merged"], total_stats["skipped"])

    # General curator
    if max_general_skills > 0 and len(task_summaries) >= 2:
        gen_stats = _curate_general_skills(
            task_summaries, workspace_dir, region, curator_model, max_general_skills,
            evolve_all=evolve_all,
            feedback_level=feedback_level,
        )
        logger.info("[%s] General curator: +%d added, %d updated, %d deleted",
                     batch_label, gen_stats["added"], gen_stats["updated"], gen_stats["deleted"])

    # Update skill tree
    update_skill_tree(workspace_dir)
    new_skills = load_skills(workspace_dir)
    logger.info("[%s] Skills: %d total", batch_label, len(new_skills))

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description="Terminal-Bench 2.0 with A-EVOLVE propose+curator")
    p.add_argument("--challenges-dir", type=str, default=None)
    p.add_argument("--tasks", type=str, default=None, help="Comma-separated task names")
    p.add_argument("--exclude", type=str, default=None, help="Comma-separated exclusions")
    p.add_argument("--category", type=str, default=None)
    p.add_argument("--difficulty", type=str, default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--shuffle", action="store_true")
    p.add_argument("--shuffle-seed", type=int, default=42,
                   help="Random seed for shuffle (default: 42)")
    p.add_argument("--solver-model", type=str, default="1")
    p.add_argument("--curator-model", type=str, default="2")
    p.add_argument("--region", type=str, default="us-west-2")
    p.add_argument("--max-tokens", type=int, default=16384)
    p.add_argument("--batch-size", type=int, default=10)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--max-skills-per-topic", type=int, default=5)
    p.add_argument("--max-general-skills", type=int, default=10)
    p.add_argument("--no-evolve", action="store_true",
                   help="Baseline mode: skip propose + curation, just solve + evaluate")
    p.add_argument("--evolve-all", action="store_true",
                   help="Propose and curate for ALL tasks (passed+failed), not just failed")
    p.add_argument("--lazy-load", action="store_true",
                   help="Lazy-load skills: name+desc in prompt, full body via read_skill tool")
    p.add_argument("--selector-model", type=str, default="2",
                   help="Model for topic selection (default: 2=Sonnet). Set empty to disable.")
    p.add_argument("--agent-evolve", action="store_true",
                   help="Use agent evolver with bash tool (aggregates batch info, writes skills directly)")
    p.add_argument("--agent-curate", action="store_true",
                   help="Hybrid: per-task propose + agent curator with bash tool (domain-level organization)")
    p.add_argument("--use-seed-skills", action="store_true",
                   help="Opt in to the bundled seed skills (default: disabled)")
    p.add_argument("--seed-workspace", type=str, default=None,
                   help="Seed workspace to copy (default: empty)")
    p.add_argument("--feedback-level", type=str, default="standard",
                   choices=["minimal", "standard", "full"],
                   help="How much eval detail the evolver sees")
    p.add_argument("--output-dir", type=str, default="outputs/terminal_evolve")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    if args.agent_evolve and args.agent_curate:
        p.error("--agent-evolve and --agent-curate are mutually exclusive")

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for n in ("botocore", "urllib3", "httpcore", "httpx"):
        logging.getLogger(n).setLevel(logging.WARNING)

    # Resolve models
    model_id = MODEL_MAP.get(args.solver_model, args.solver_model)
    curator_model_id = MODEL_MAP.get(args.curator_model, args.curator_model)
    selector_model_id = MODEL_MAP.get(args.selector_model, args.selector_model) if args.selector_model else ""

    # Load tasks
    all_tasks = load_all_tasks(args.challenges_dir)
    logger.info("Loaded %d tasks", len(all_tasks))

    if args.tasks:
        names = set(n.strip() for n in args.tasks.split(","))
        all_tasks = [t for t in all_tasks if t.name in names]
    if args.exclude:
        excl = set(n.strip() for n in args.exclude.split(","))
        all_tasks = [t for t in all_tasks if t.name not in excl]
    if args.category:
        all_tasks = [t for t in all_tasks if t.metadata.get("category") == args.category]
    if args.difficulty:
        all_tasks = [t for t in all_tasks if t.metadata.get("difficulty") == args.difficulty]
    if args.shuffle:
        import random
        random.seed(args.shuffle_seed)
        random.shuffle(all_tasks)
    if args.limit:
        all_tasks = all_tasks[:args.limit]

    if not all_tasks:
        print("No tasks to run.")
        return

    # Setup workspace
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir = output_dir / "workspace"

    if not workspace_dir.exists():
        workspace_dir.mkdir(parents=True, exist_ok=True)
        if args.seed_workspace and Path(args.seed_workspace).exists():
            shutil.copytree(args.seed_workspace, workspace_dir, dirs_exist_ok=True)
            logger.info("Copied seed workspace from %s", args.seed_workspace)
        else:
            # Minimal workspace
            (workspace_dir / "skills").mkdir(exist_ok=True)
            (workspace_dir / "skills" / "topic").mkdir(exist_ok=True)
            (workspace_dir / "skills" / "general").mkdir(exist_ok=True)
        if args.use_seed_skills:
            _init_seed_skills(workspace_dir)

    log_dir = output_dir / "logs"
    log_dir.mkdir(exist_ok=True)
    results_dir = output_dir / "results"
    results_dir.mkdir(exist_ok=True)

    logger.info(
        "Running %d tasks | solver=%s curator=%s | batch=%d workers=%d | "
        "max_topic_skills=%d max_general=%d",
        len(all_tasks), model_id, curator_model_id, args.batch_size, args.workers,
        args.max_skills_per_topic, args.max_general_skills,
    )

    # Batch loop
    all_results = []
    t0 = time.time()
    batches = [all_tasks[i:i+args.batch_size] for i in range(0, len(all_tasks), args.batch_size)]

    for bi, batch in enumerate(batches):
        logger.info("=== Batch %d/%d (%d tasks) ===", bi+1, len(batches), len(batch))
        cumulative_rate = sum(1 for r in all_results if r.get("passed")) / max(len(all_results), 1)
        batch_results = evolve_batch(
            tasks=batch, workspace_dir=workspace_dir,
            model_id=model_id, curator_model=curator_model_id,
            region=args.region, max_tokens=args.max_tokens,
            batch_workers=args.workers,
            max_skills_per_topic=args.max_skills_per_topic,
            max_general_skills=args.max_general_skills,
            log_dir=log_dir, results_dir=results_dir,
            batch_label=f"B{bi+1}/{len(batches)}",
            no_evolve=args.no_evolve,
            lazy_load=args.lazy_load,
            selector_model=selector_model_id,
            evolve_all=args.evolve_all,
            agent_evolve=args.agent_evolve,
            agent_curate=args.agent_curate,
            feedback_level=args.feedback_level,
            cumulative_task_rate=cumulative_rate,
        )
        all_results.extend(batch_results)

        # Clean up any zombie containers from this batch
        try:
            out = subprocess.run(
                ["docker", "ps", "-a", "--filter", "status=created",
                 "--filter", "status=exited", "--format", "{{.Names}}"],
                capture_output=True, text=True, timeout=30,
            )
            for name in out.stdout.strip().split("\n"):
                name = name.strip()
                if name and name.startswith("tb2-"):
                    subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=10)
            logger.debug("Post-batch container cleanup done.")
        except Exception as e:
            logger.warning("Post-batch container cleanup failed: %s", e)

        passed = sum(1 for r in all_results if r.get("passed"))
        total = len(all_results)
        logger.info("Cumulative: %d/%d (%.1f%%)", passed, total, 100 * passed / max(total, 1))

    # Final summary
    elapsed = time.time() - t0
    total_passed = sum(1 for r in all_results if r.get("passed"))
    total = len(all_results)

    by_cat = defaultdict(lambda: {"p": 0, "t": 0})
    by_diff = defaultdict(lambda: {"p": 0, "t": 0})
    for r in all_results:
        cat = r.get("metadata", {}).get("category", "unknown")
        diff = r.get("difficulty", "unknown")
        by_cat[cat]["t"] += 1
        by_diff[diff]["t"] += 1
        if r.get("passed"):
            by_cat[cat]["p"] += 1
            by_diff[diff]["p"] += 1

    logger.info("=" * 70)
    logger.info("FINAL: %d/%d (%.1f%%) in %.0fs", total_passed, total,
                100 * total_passed / max(total, 1), elapsed)
    for diff in ["easy", "medium", "hard"]:
        if diff in by_diff:
            d = by_diff[diff]
            logger.info("  %s: %d/%d (%.1f%%)", diff, d["p"], d["t"], 100*d["p"]/max(d["t"],1))
    for cat, d in sorted(by_cat.items()):
        logger.info("  %s: %d/%d (%.1f%%)", cat, d["p"], d["t"], 100*d["p"]/max(d["t"],1))

    # Save summary
    summary = {
        "timestamp": datetime.now().isoformat(),
        "solver_model": model_id,
        "curator_model": curator_model_id,
        "total": total,
        "passed": total_passed,
        "rate": total_passed / max(total, 1),
        "elapsed": elapsed,
        "by_category": {k: dict(v) for k, v in by_cat.items()},
        "by_difficulty": {k: dict(v) for k, v in by_diff.items()},
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    # Save all results
    with open(output_dir / "all_results.jsonl", "w") as f:
        for r in all_results:
            f.write(json.dumps(r, default=str) + "\n")

    # Copy final skills
    skills_out = output_dir / "final_skills"
    if (workspace_dir / "skills").exists():
        if skills_out.exists():
            shutil.rmtree(skills_out)
        shutil.copytree(workspace_dir / "skills", skills_out)


if __name__ == "__main__":
    main()
