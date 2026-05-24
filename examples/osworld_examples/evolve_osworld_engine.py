#!/usr/bin/env python3
"""OSWorld evolution using the ProposeCurateEngine.

This is the engine-based version of evolve_osworld.py. The pipeline is:
  1. Parallel solve (ReAct agent on OSWorld VM)
  2. Parallel evaluate (env.evaluate() → 0.0 or 1.0)
  3. Analyze+propose skills (in solver conversation context)
  4. ProposeCurateEngine.step() — per-topic + general curation
  5. Reload workspace, next batch

Usage:
    python examples/osworld_examples/evolve_osworld_engine.py \
        --task-file evaluation_examples/test_all.json \
        --domain libreoffice_calc \
        --batch-size 5 --workers 2 \
        --output-dir outputs/osworld_evolve_engine_v1
"""
from __future__ import annotations

import argparse
import atexit
import json
import logging
import os
import re
import shutil
import signal
import sys
import threading
import time
import queue as queue_mod
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

OSWORLD_PATH = os.environ.get("OSWORLD_PATH")
if not OSWORLD_PATH:
    raise EnvironmentError("OSWORLD_PATH must be set to the OSWorld repo directory")
sys.path.insert(0, OSWORLD_PATH)
os.environ["BYPASS_TOOL_CONSENT"] = "true"

_RUN_ID = f"evolve-{os.getpid()}-{int(time.time())}"
os.environ["OSWORLD_RUN_ID"] = _RUN_ID

from agent_evolve.agents.osworld.react_solver import (
    react_solve, extract_conversation, SYSTEM_PROMPT as OSW_SYSTEM_PROMPT,
)
from agent_evolve.algorithms.propose_curate import ProposeCurateEngine
from agent_evolve.config import EvolveConfig
from agent_evolve.contract.workspace import AgentWorkspace
from agent_evolve.types import Feedback, Observation, Task, Trajectory

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# VM lifecycle management
# ---------------------------------------------------------------------------
_live_envs: list = []
_live_envs_lock = threading.Lock()
_cleanup_done = False


def _cleanup_all_envs():
    global _cleanup_done
    if _cleanup_done:
        return
    _cleanup_done = True
    with _live_envs_lock:
        envs = list(_live_envs)
        _live_envs.clear()
    if not envs:
        return
    logger.info("Cleanup: terminating %d OSWorld VM(s)...", len(envs))
    for env in envs:
        try:
            env.close()
        except Exception as e:
            logger.warning("Cleanup: env.close() failed: %s", e)
    logger.info("Cleanup: done.")


def _signal_handler(sig, frame):
    _cleanup_all_envs()
    sys.exit(1)


atexit.register(_cleanup_all_envs)
signal.signal(signal.SIGTERM, _signal_handler)
try:
    signal.signal(signal.SIGINT, _signal_handler)
except ValueError:
    pass

# ---------------------------------------------------------------------------
# Model map
# ---------------------------------------------------------------------------
MODEL_MAP = {
    "1": "us.anthropic.claude-opus-4-6-v1",
    "2": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "3": "us.anthropic.claude-opus-4-5-20251101-v1:0",
}

# ---------------------------------------------------------------------------
# Task loading
# ---------------------------------------------------------------------------


def load_osworld_tasks(task_file: str, domain: str = None) -> list[dict]:
    """Load OSWorld task configs from a JSON file."""
    task_file = Path(task_file)
    base_dir = task_file.parent

    with open(task_file) as f:
        meta = json.load(f)

    if not isinstance(meta, dict):
        return [t for t in meta if not domain or t.get("domain") == domain]

    tasks = []
    for dom, task_ids in meta.items():
        if domain and dom != domain:
            continue
        for tid in task_ids:
            config_path = base_dir / "examples" / dom / f"{tid}.json"
            if not config_path.exists():
                continue
            with open(config_path) as f:
                config = json.load(f)
            config.setdefault("id", tid)
            config.setdefault("domain", dom)
            tasks.append(config)
    return tasks


def _task_id(task_config: dict) -> str:
    return task_config.get("id", task_config.get("task_id", "unknown"))


def _task_domain(task_config: dict) -> str:
    return task_config.get("domain", task_config.get("_domain", "unknown"))


# ---------------------------------------------------------------------------
# Trajectory signal extraction (FULL version from original)
# ---------------------------------------------------------------------------


def _extract_trajectory_signals(conversation: list[dict]) -> dict:
    """Extract structured behavioral signals from a GUI conversation trajectory."""
    n_turns = 0
    n_actions = 0
    n_clicks = 0
    n_keystrokes = 0
    n_scrolls = 0
    n_errors = 0
    n_timeouts = 0
    actions_run: list[str] = []
    submitted = False
    submit_value = ""
    error_messages: list[str] = []

    for entry in conversation:
        role = entry.get("role", "")
        parts = entry.get("parts", [])

        if role == "assistant":
            n_turns += 1
            for part in parts:
                if part.get("type") == "tool_use":
                    fn = part.get("name", "")
                    inp = part.get("input", {})

                    if fn == "computer":
                        n_actions += 1
                        action_type = inp.get("action", "")
                        coord = inp.get("coordinate")
                        text_val = inp.get("text", "")
                        desc = str(action_type)
                        if coord:
                            desc += f"({coord[0]},{coord[1]})"
                        if text_val and len(str(text_val)) <= 50:
                            desc += f" '{text_val}'"
                        actions_run.append(desc[:120])
                        if action_type in ("left_click", "right_click", "double_click",
                                           "middle_click", "triple_click", "left_press"):
                            n_clicks += 1
                        if action_type in ("type", "key", "hold_key"):
                            n_keystrokes += 1
                        if action_type == "scroll":
                            n_scrolls += 1

                    elif fn in ("submit", "task_submit"):
                        submitted = True
                        submit_value = inp.get("answer", "")

        elif role == "user":
            for part in parts:
                if part.get("type") == "tool_result":
                    content = part.get("text", "")
                    if "Error:" in content or "ERROR:" in content:
                        n_errors += 1
                        error_messages.append(content[:150])
                    if "timed out" in content.lower() or "timeout" in content.lower():
                        n_timeouts += 1

    action_counts: dict[str, int] = {}
    for a in actions_run:
        action_counts[a] = action_counts.get(a, 0) + 1
    repeated_actions = [a for a, cnt in action_counts.items() if cnt >= 3]

    return {
        "n_turns": n_turns,
        "n_actions": n_actions,
        "n_clicks": n_clicks,
        "n_keystrokes": n_keystrokes,
        "n_scrolls": n_scrolls,
        "n_errors": n_errors,
        "n_timeouts": n_timeouts,
        "submitted": submitted,
        "submit_value": submit_value,
        "repeated_actions": repeated_actions,
        "error_snippets": error_messages[:5],
    }


def _compress_trajectory(conversation: list[dict]) -> str:
    """Compress a GUI trajectory into a failure-focused summary."""
    events: list[dict] = []
    prev_code = ""

    for entry in conversation:
        role = entry.get("role", "")
        parts = entry.get("parts", [])

        if role == "assistant":
            for part in parts:
                if part.get("type") == "tool_use":
                    fn = part.get("name", "")
                    inp = part.get("input", {})
                    answer = inp.get("answer", "")

                    if fn in ("submit", "task_submit"):
                        events.append({"type": "submit", "value": answer})
                    elif fn == "computer":
                        action_type = inp.get("action", "")
                        coord = inp.get("coordinate")
                        text_val = inp.get("text", "")
                        desc = str(action_type)
                        if coord:
                            desc += f"({coord[0]},{coord[1]})"
                        if text_val:
                            desc += f" '{str(text_val)[:100]}'"
                        prev_code = desc[:250] if desc else ""
                        if prev_code:
                            events.append({"type": "action", "code": prev_code})

        elif role == "user":
            for part in parts:
                if part.get("type") == "tool_result":
                    content = part.get("text", "").strip()
                    is_error = (
                        "Error:" in content
                        or "ERROR:" in content
                        or "Traceback" in content[:200]
                        or "TIMEOUT" in content.upper()[:50]
                        or "timed out" in content.lower()[:80]
                    )
                    if is_error:
                        events.append({
                            "type": "error",
                            "code": prev_code,
                            "output": content[:300],
                        })

    parts: list[str] = []
    n_actions = sum(1 for e in events if e["type"] == "action")
    n_errors = sum(1 for e in events if e["type"] == "error")
    submitted = any(e["type"] == "submit" for e in events)

    parts.append(f"Actions: {n_actions}, Errors: {n_errors}, Submitted: {submitted}")

    actions_seen = 0
    for e in events:
        if e["type"] == "action":
            actions_seen += 1
            if actions_seen <= 3:
                parts.append(f"[start] computer({e['code'][:150]})")

    if n_errors > 0:
        parts.append(f"\n--- Errors ({n_errors}) ---")
        for e in events:
            if e["type"] == "error":
                parts.append(f"  code: {e.get('code', '?')[:150]}")
                parts.append(f"  err: {e['output'][:200]}")

    action_list = [e["code"] for e in events if e["type"] == "action"]
    action_counts: dict[str, int] = {}
    for a in action_list:
        action_counts[a] = action_counts.get(a, 0) + 1
    loops = {a: n for a, n in action_counts.items() if n >= 3}
    if loops:
        parts.append(f"\n--- Repeated actions ---")
        for a, n in loops.items():
            parts.append(f"  {a[:120]} (x{n})")

    last_actions = [e for e in events if e["type"] == "action"][-3:]
    if last_actions:
        parts.append(f"\n--- Final actions ---")
        for e in last_actions:
            parts.append(f"  computer({e['code'][:150]})")

    if submitted:
        submit_events = [e for e in events if e["type"] == "submit"]
        if submit_events:
            parts.append(f"\n[submitted] {submit_events[-1].get('value', '')}")

    return "\n".join(parts)


def _format_signals(signals: dict) -> str:
    """Format trajectory signals as a concise text block."""
    lines = [
        f"Turns: {signals['n_turns']}, Actions: {signals['n_actions']}, "
        f"Clicks: {signals['n_clicks']}, Keystrokes: {signals['n_keystrokes']}, "
        f"Scrolls: {signals['n_scrolls']}",
        f"Errors: {signals['n_errors']}, Timeouts: {signals['n_timeouts']}",
        f"Submitted: {signals['submitted']}",
    ]
    if signals.get("repeated_actions"):
        lines.append(f"Repeated actions: {'; '.join(s[:80] for s in signals['repeated_actions'][:3])}")
    if signals.get("error_snippets"):
        lines.append(f"Error samples: {'; '.join(s[:60] for s in signals['error_snippets'][:3])}")
    if signals.get("bot_detection"):
        lines.append(f"Bot detection: {signals['bot_detection']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Bot detection helpers
# ---------------------------------------------------------------------------

_BOT_PATTERNS = [
    (r"captcha|i['’]m not a robot|recaptcha|hcaptcha", "captcha"),
    (r"cloudflare.*verify|verify you are human|challenge.*cloudflare", "cloudflare_challenge"),
    (r"access denied|403 forbidden|403 error|HTTP 403", "access_denied_403"),
    (r"unusual traffic|automated queries|bot.*detect|rate.?limit", "rate_limit_or_bot"),
    (r"please verify|security check|human verification", "verification_challenge"),
]


def _detect_bot_detection(conversation: list[dict], compressed_traj: str) -> str | None:
    """Scan trajectory for bot detection / access denial patterns."""
    texts: list[str] = []
    for entry in conversation:
        for part in entry.get("parts", []):
            if part.get("type") == "text":
                texts.append(part.get("text", "")[:2000])
            elif part.get("type") == "tool_result":
                texts.append(part.get("text", "")[:2000])
    texts.append(compressed_traj)

    combined = " ".join(texts).lower()
    for pattern, label in _BOT_PATTERNS:
        if re.search(pattern, combined, re.IGNORECASE):
            return label
    return None


# ---------------------------------------------------------------------------
# Build rich evaluation text for propose prompt
# ---------------------------------------------------------------------------


def _build_eval_text(score: float, eval_detail: dict, bot_detection: str | None) -> str:
    """Build a detailed evaluation description for the propose prompt."""
    parts = [f"FAILED (score={score:.1f})"]

    metric_func = eval_detail.get("metric_func", "")
    if metric_func:
        parts.append(f"Evaluation metric: {metric_func}")

    failure_reason = eval_detail.get("failure_reason", "")
    if failure_reason and failure_reason != "none":
        parts.append(f"Failure reason: {failure_reason}")

    details = eval_detail.get("details", [])
    if details:
        parts.append("Per-metric breakdown:")
        for d in details[:5]:
            m_name = d.get("metric", "?")
            m_score = d.get("score", 0.0)
            result_repr = d.get("result_state", "")
            line = f"  - {m_name}: score={m_score:.1f}"
            if result_repr and result_repr not in ("None", "''", '""'):
                result_repr = result_repr[:300]
                line += f", agent_result={result_repr}"
            fr = d.get("failure_reason", "")
            if fr:
                line += f" ({fr})"
            parts.append(line)

    if not details:
        rs = eval_detail.get("result_state", "")
        if rs and rs not in ("None", "''", '""'):
            parts.append(f"Agent's result state: {rs[:400]}")

    if bot_detection:
        parts.append(f"\nNOTE: Bot detection/access denial was detected in the trajectory "
                     f"(type: {bot_detection}). This failure may be caused by anti-bot "
                     f"measures rather than agent error. Focus proposals on workarounds "
                     f"(alternative sites, cached pages, different approaches) rather than "
                     f"GUI technique improvements.")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Build propose messages with last N screenshots kept
# ---------------------------------------------------------------------------


def _build_propose_messages(
    messages: list[dict],
    keep_last_n_images: int = 3,
) -> list[dict]:
    """Copy conversation messages for propose, keeping last N screenshots."""
    total_images = 0
    for msg in messages:
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            bt = b.get("type", "")
            if bt == "image":
                total_images += 1
            elif bt == "tool_result" and isinstance(b.get("content"), list):
                for c in b["content"]:
                    if isinstance(c, dict) and c.get("type") == "image":
                        total_images += 1

    keep_from = max(0, total_images - keep_last_n_images)

    img_idx = 0
    propose_messages = []
    for msg in messages:
        content = msg.get("content", [])
        if isinstance(content, str):
            propose_messages.append(msg)
            continue
        if not isinstance(content, list):
            continue
        new_blocks = []
        for b in content:
            if not isinstance(b, dict):
                continue
            bt = b.get("type", "")
            if bt == "thinking":
                continue
            if bt == "image":
                if img_idx >= keep_from:
                    new_blocks.append(b)
                img_idx += 1
                continue
            if bt == "tool_result" and isinstance(b.get("content"), list):
                b = dict(b)
                new_content = []
                for c in b["content"]:
                    if isinstance(c, dict) and c.get("type") == "image":
                        if img_idx >= keep_from:
                            new_content.append(c)
                        img_idx += 1
                    else:
                        new_content.append(c)
                b["content"] = new_content
            new_blocks.append(b)
        if new_blocks:
            propose_messages.append({"role": msg["role"], "content": new_blocks})

    return propose_messages


def _truncate(s: str, n: int = 300) -> str:
    return s[:n] + "..." if len(s) > n else s


# ---------------------------------------------------------------------------
# Propose system prompt (IDENTICAL to original)
# ---------------------------------------------------------------------------

PROPOSE_SYSTEM_PROMPT = """\
You are a skill extraction agent for an OSWorld GUI desktop agent. \
You analyze task attempts and distill reusable skills.

A good skill is:
- **Specific**: actual menu paths, keyboard shortcuts, a11y tree element names, CLI commands
- **Structured**: bullet points under "Key techniques" and "Gotchas", under 200 words
- **Actionable**: another agent can read it and immediately apply the technique
- **Transferable**: useful beyond this single task — focus on the application/domain pattern

A bad skill is:
- Generic advice ("be careful", "verify output", "check the screenshot")
- Task-specific (only applies to this exact task, not future similar ones)
- Redundant with an existing skill in the library

If the failure was caused by bot detection, CAPTCHA, or access denial, \
focus on workaround strategies (alternative sites, cached pages, different search engines) \
rather than GUI techniques. If nothing useful was learned, output ACTION: NONE."""

# ---------------------------------------------------------------------------
# Analyze + Propose prompt (IDENTICAL to original)
# ---------------------------------------------------------------------------

ANALYZE_AND_PROPOSE_PROMPT = """\
The evaluation result for this task:

{eval_result}

## Trajectory signals
{trajectory_signals}

## Compressed trajectory
{compressed_trajectory}

{existing_skills_section}

## Step 1: Analyze the result
Consider the evaluation score, trajectory signals (errors, loops, timeouts), and compressed trajectory.
For EACH distinct issue or failure reason, output:
ISSUE: <one-line summary of what went wrong or what was needed>
DETAIL: <specific GUI actions, UI elements, or techniques that were missing>

## Step 2: Propose a skill
Based on your analysis, write a SHORT skill for future tasks of this type.

TOPIC: <broad application-level topic, e.g. "libreoffice-calc", "chrome", "gimp", "vlc", "vscode", "thunderbird", "os-desktop">
ACTION: NEW / ENHANCE / NONE
TARGET: existing_skill_name (only for ENHANCE)
NAME: short-kebab-name (only for NEW)
DESCRIPTION: one sentence saying WHEN this skill applies — the agent sees ONLY this line to decide whether to read the skill. Be specific: "For LibreOffice Calc tasks involving formula editing and cell formatting" not "For spreadsheet tasks"
CONTENT:
## Key techniques
- (specific GUI actions, keyboard shortcuts, menu paths, or a11y tree patterns)
## Gotchas
- (specific pitfalls based on what went wrong)

FORBIDDEN — do NOT include any of the following (the agent already knows these):
- Basic mouse/keyboard operations (how to click, type, press keys)
- How to take or interpret screenshots
- How to use the computer_use tool (coordinate clicking, typing, scrolling)
- Generic GUI advice ("look at the screen", "wait for the window to load", "check the result")
- Retry/timeout strategies

REQUIRED — only include application-specific knowledge the agent does NOT already have:
- Application-specific menu paths, keyboard shortcuts, and dialog sequences
- Application-specific a11y tree element names and patterns
- Non-obvious workflows (e.g., enabling a hidden feature, multi-step dialog navigation)

Rules:
- Bullet points, not paragraphs. CONTENT must be under 200 words.
- Be SPECIFIC: include actual menu paths, keyboard shortcuts, a11y tree element names
- Focus on application-specific knowledge, NOT generic advice (no "look carefully", "check errors")
- Prefer ENHANCE over NEW if an existing skill is related
- TOPIC should be BROAD (application-level), not task-specific. Use "libreoffice-calc" not "libreoffice-calc-vlookup"
- TOPIC should match an existing topic if applicable; create a new one only if no existing topic fits
- If the task passed easily or nothing useful was learned, output ACTION: NONE"""

ANALYZE_AND_PROPOSE_PASS_PROMPT = """\
The evaluation result for this task:

PASSED (score={score:.1f})

## Trajectory signals
{trajectory_signals}

## Compressed trajectory
{compressed_trajectory}

{existing_skills_section}

## Step 1: Analyze what worked
Review the trajectory and identify techniques that were effective — especially \
non-obvious ones (workarounds, specific menu paths, key combos, a11y patterns).

## Step 2: Propose a skill (if warranted)
If the approach contained reusable, non-obvious techniques worth preserving:

TOPIC: <broad application-level topic, e.g. "libreoffice-calc", "chrome", "gimp", "vlc", "vscode", "thunderbird", "os-desktop">
ACTION: NEW / ENHANCE / NONE
TARGET: existing_skill_name (only for ENHANCE)
NAME: short-kebab-name (only for NEW)
DESCRIPTION: one sentence saying WHEN this skill applies — the agent sees ONLY this line to decide whether to read the skill. Be specific: "For GIMP tasks involving layer manipulation and export settings" not "For image editing tasks"
CONTENT:
## Key techniques
- (specific steps that worked)
## Gotchas
- (obstacles encountered and how they were overcome)

FORBIDDEN — do NOT include any of the following (the agent already knows these):
- Basic mouse/keyboard operations (how to click, type, press keys)
- How to take or interpret screenshots
- How to use the computer_use tool (coordinate clicking, typing, scrolling)
- Generic GUI advice ("look at the screen", "wait for the window to load", "check the result")
- Retry/timeout strategies

REQUIRED — only include application-specific knowledge the agent does NOT already have:
- Application-specific menu paths, keyboard shortcuts, and dialog sequences
- Application-specific a11y tree element names and patterns
- Non-obvious workflows discovered during this task

Rules:
- Only propose if the technique is non-trivial and transferable
- Output ACTION: NONE if the task was straightforward or already covered by existing skills
- Prefer ENHANCE over NEW if an existing skill is related
- TOPIC should be BROAD (application-level), not task-specific. Use "libreoffice-calc" not "libreoffice-calc-vlookup"
- CONTENT under 200 words, bullet points only"""

# ---------------------------------------------------------------------------
# Curator prompts (IDENTICAL to original evolve_osworld.py)
# ---------------------------------------------------------------------------

CURATOR_PROMPT = """\
You are a skill curator for a GUI task-solving agent on OSWorld (Ubuntu desktop). \
You review skill proposals and decide which to keep in the skill library for topic: {topic}.

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
- MERGE is always preferred over ACCEPT — combine related techniques into fewer, broader skills
- Overlaps existing -> MERGE (append new techniques to existing skill)
- Multiple narrow proposals on the same app -> MERGE into one broad skill
- Budget full -> can only MERGE existing, or SKIP
- Check DESCRIPTION quality: it must clearly say WHEN the skill applies. The agent decides to read based on description alone. Vague descriptions like "for desktop tasks" → SKIP or rewrite in MERGE
- SKIP proposals containing FORBIDDEN content (basic mouse/keyboard ops, screenshot usage, computer_use tool usage, generic GUI advice, retry strategies) — the agent already knows these
- Keep skills SHORT and SPECIFIC -- actual menu paths, shortcuts, and GUI techniques, not advice
- Few broad skills > many narrow ones — one skill covering multiple techniques is better than many single-technique skills

If no proposals: NO_PROPOSALS"""

GENERAL_CURATOR_PROMPT = """\
You are a meta-learning curator. You analyze failure patterns ACROSS tasks \
to distill general skills that help the agent on ANY OSWorld desktop task.

## Failed Task Analysis ({n_failed} tasks):
{failed_summaries}

## Current General Skills ({n_general}/{max_general} slots):
{general_skills_list}

For REPEATED patterns across 2+ different tasks, output:

NEW_GENERAL: <kebab-name>
DESCRIPTION: <one line saying WHEN this skill applies — the agent sees ONLY this line to decide whether to read the skill>
CONTENT:
## Pattern
- (one line: what failure type)
## Strategy
- (3-5 bullet points: specific GUI techniques/shortcuts)
(Under 200 words, bullet points only)

UPDATE_GENERAL: <existing-name>
NEW_CONTENT:
(updated content, under 200 words)

DELETE_GENERAL: <existing-name>
REASON: <why>

If no cross-task patterns: NO_PATTERNS

FORBIDDEN — do NOT include any of the following in skills (the agent already knows these):
- Basic mouse/keyboard operations (how to click, type, press keys)
- How to take or interpret screenshots
- How to use the computer_use tool (coordinate clicking, typing, scrolling)
- Generic GUI advice ("look at the screen", "wait for the window to load", "check the result")
- Retry/timeout strategies

REQUIRED — only include application-specific knowledge the agent does NOT already have:
- Application-specific menu paths, keyboard shortcuts, and dialog sequences
- Application-specific a11y tree patterns
- Cross-application GUI interaction patterns (e.g., drag-and-drop conventions, file dialogs)

Rules:
- Max {max_general} general skills. Quality > quantity.
- Must appear in 2+ different tasks to be general.
- SPECIFIC and ACTIONABLE -- actual GUI actions, menu paths, shortcuts, not advice.
- DELETE skills that contain FORBIDDEN content or are too generic.
- Prefer UPDATE over NEW."""


# ---------------------------------------------------------------------------
# Format failed summary for general curator (matches original logic)
# ---------------------------------------------------------------------------


def _format_failed_summary_standard(s: dict) -> str:
    """Format failed summary for general curator — standard feedback level."""
    parts = [f"### {s.get('task_name', s.get('task_id', '?'))} ({s.get('domain', '')})"]
    eval_info = []
    if s.get("eval_metric"):
        eval_info.append(f"metric={s['eval_metric']}")
    if s.get("failure_reason"):
        eval_info.append(f"reason={s['failure_reason']}")
    if s.get("bot_detection"):
        eval_info.append(f"bot_detection={s['bot_detection']}")
    if eval_info:
        parts.append(f"Eval: {', '.join(eval_info)}")
    if s.get("trajectory_signals"):
        parts.append(f"Signals: {_format_signals(s['trajectory_signals'])}")
    if s.get("compressed_trajectory"):
        parts.append(f"Trajectory:\n{_truncate(s['compressed_trajectory'], 400)}")
    if s.get("feedback_analysis"):
        parts.append(f"Analysis:\n{_truncate(s['feedback_analysis'], 400)}")
    if s.get("proposal_summary"):
        parts.append(f"Proposal: {_truncate(s['proposal_summary'], 200)}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Skill loading & system prompt building
# ---------------------------------------------------------------------------


def load_skills(workspace_dir: Path) -> list[dict]:
    skills = []
    skills_dir = workspace_dir / "skills"
    if not skills_dir.exists():
        return skills
    for sf in sorted(skills_dir.rglob("SKILL.md")):
        content = sf.read_text().strip()
        name = sf.parent.name
        desc, body = "", content
        for line in content.split("\n"):
            if line.strip().startswith("description:"):
                desc = line.split(":", 1)[1].strip()
                break
        if content.startswith("---"):
            end = content.find("---", 3)
            if end != -1:
                body = content[end + 3:].strip()
        rel_path = str(sf.parent.relative_to(workspace_dir))
        skills.append({"name": name, "description": desc, "body": body, "path": rel_path})
    return skills


def build_system_prompt(skills: list[dict]) -> str:
    parts = [OSW_SYSTEM_PROMPT]
    if skills:
        parts.append("\n\n## Skills")
        for s in skills:
            parts.append(f"\n### {s['name']}\n{s['body']}" if s['body'] else f"\n### {s['name']}\n{s['description']}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Parse proposal from LLM response (IDENTICAL to original)
# ---------------------------------------------------------------------------


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
# Per-task solve+evaluate+propose (FULL version)
# ---------------------------------------------------------------------------


def solve_one_task(
    task_config: dict,
    env,
    model_id: str,
    region: str,
    max_tokens: int,
    system_prompt: str,
    workspace_dir: Path,
    log_dir: Path,
    max_steps: int = 30,
    evolve_all: bool = False,
    curator_model: str = "",
    feedback_level: str = "standard",
) -> dict:
    """Full pipeline for one task: solve → evaluate → analyze+propose."""
    task_name = _task_id(task_config)
    domain = _task_domain(task_config)
    task_instruction = task_config.get("instruction", task_config.get("task", ""))
    t0 = time.time()

    task_log_dir = log_dir / task_name
    task_log_dir.mkdir(parents=True, exist_ok=True)

    try:
        # 1. Reset VM
        for attempt in range(3):
            try:
                env.reset(task_config=task_config)
                break
            except Exception:
                if attempt < 2:
                    time.sleep(10)
                else:
                    raise RuntimeError(f"Setup failed for {task_name}")
        time.sleep(60)
        obs = env._get_obs()

        # 2. Solve
        react_result = react_solve(
            task_prompt=task_instruction,
            env=env,
            model_id=model_id,
            region=region,
            max_tokens=max_tokens,
            timeout_sec=task_config.get("agent_timeout_sec", 900),
            max_turns=max_steps,
            system_prompt=system_prompt,
            initial_obs=obs,
        )
        conversation = extract_conversation(react_result.messages)
        solve_time = time.time() - t0

        # 3. Evaluate
        time.sleep(20)
        eval_detail = {}
        try:
            eval_detail = env.evaluate_detailed()
            score = float(eval_detail.get("score", 0.0))
        except Exception as e:
            score = 0.0
            eval_detail = {"score": 0.0, "metric_func": "unknown",
                           "result_state": f"eval_exception: {str(e)[:200]}",
                           "failure_reason": "eval_exception", "details": []}

        passed = score >= 1.0

        # 4. Extract trajectory signals
        traj_signals = _extract_trajectory_signals(conversation)
        compressed_traj = _compress_trajectory(conversation)

        # 5. Detect bot detection
        bot_detection = _detect_bot_detection(conversation, compressed_traj)
        if bot_detection:
            traj_signals["bot_detection"] = bot_detection

        # 6. Analyze + Propose
        feedback_analysis = None
        proposal = None

        should_propose = (react_result.messages and (not passed or evolve_all))

        if should_propose:
            try:
                existing_all_skills = []
                skills_dir = workspace_dir / "skills"
                if skills_dir.exists():
                    for sf in sorted(skills_dir.rglob("SKILL.md")):
                        sn = sf.parent.name
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
                        score=score,
                        trajectory_signals=_format_signals(traj_signals),
                        compressed_trajectory=_truncate(compressed_traj, 1500),
                        existing_skills_section=skills_section,
                    )
                else:
                    if feedback_level == "minimal":
                        eval_text = f"FAILED (score={score:.1f})"
                    elif feedback_level == "standard":
                        eval_text = _build_eval_text(score, eval_detail, bot_detection)
                    else:  # full
                        eval_text = _build_eval_text(score, eval_detail, bot_detection)
                        if eval_detail.get("details"):
                            for d in eval_detail["details"]:
                                rs = d.get("result_state", "")
                                if rs:
                                    eval_text += f"\nFull result_state ({d.get('metric','?')}): {rs[:1000]}"
                        elif eval_detail.get("result_state"):
                            eval_text += f"\nFull result_state: {str(eval_detail['result_state'])[:1000]}"
                    prompt_text = ANALYZE_AND_PROPOSE_PROMPT.format(
                        eval_result=eval_text,
                        trajectory_signals=_format_signals(traj_signals),
                        compressed_trajectory=_truncate(compressed_traj, 1500),
                        existing_skills_section=skills_section,
                    )

                from anthropic import AnthropicBedrock

                propose_messages = _build_propose_messages(
                    react_result.messages, keep_last_n_images=3,
                )
                propose_messages.append({
                    "role": "user",
                    "content": [{"type": "text", "text": prompt_text}],
                })

                propose_client = AnthropicBedrock(
                    aws_access_key=os.getenv("AWS_ACCESS_KEY_ID"),
                    aws_secret_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
                    aws_region=region,
                )
                resp_text = ""
                try:
                    propose_resp = propose_client.messages.create(
                        model=curator_model,
                        max_tokens=1536,
                        messages=propose_messages,
                        system=PROPOSE_SYSTEM_PROMPT,
                        temperature=0.3,
                    )
                    for block in propose_resp.content:
                        if getattr(block, "type", None) == "text":
                            resp_text += block.text
                except Exception as e:
                    logger.warning("Propose call failed for %s: %s", task_name, str(e)[:200])

                if resp_text:
                    action_idx = resp_text.upper().find("ACTION:")
                    if action_idx > 0:
                        feedback_analysis = resp_text[:action_idx].strip()
                    else:
                        feedback_analysis = resp_text.strip()
                    proposal = _parse_proposal(resp_text, task_name)

            except Exception as e:
                logger.warning("Analyze+propose failed for %s: %s", task_name, str(e)[:200])

        # Save artifacts
        (task_log_dir / "conversation.json").write_text(
            json.dumps(conversation, indent=2, ensure_ascii=False, default=str)
        )

        return {
            "task_name": task_name,
            "domain": domain,
            "passed": passed,
            "score": score,
            "eval_detail": {
                "metric_func": eval_detail.get("metric_func", ""),
                "failure_reason": eval_detail.get("failure_reason", ""),
                "result_state": str(eval_detail.get("result_state", ""))[:500],
                "bot_detection": bot_detection,
            },
            "solve_time": solve_time,
            "total_time": time.time() - t0,
            "feedback_analysis": feedback_analysis,
            "proposal": proposal,
            "trajectory_signals": traj_signals,
            "compressed_trajectory": compressed_traj,
        }

    except Exception as e:
        return {
            "task_name": task_name,
            "domain": domain,
            "passed": False,
            "score": 0.0,
            "error": str(e)[:500],
            "proposal": None,
            "feedback_analysis": None,
            "trajectory_signals": None,
            "compressed_trajectory": None,
        }


# ---------------------------------------------------------------------------
# Main batch loop with ProposeCurateEngine
# ---------------------------------------------------------------------------


def main():
    p = argparse.ArgumentParser(description="OSWorld + ProposeCurateEngine")
    p.add_argument("--task-file", type=str, required=True)
    p.add_argument("--provider", type=str, default="aws", choices=["aws", "vmware", "docker"])
    p.add_argument("--domain", type=str, default=None)
    p.add_argument("--tasks", type=str, default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--shuffle", action="store_true")
    p.add_argument("--solver-model", type=str, default="1")
    p.add_argument("--curator-model", type=str, default="2")
    p.add_argument("--region", type=str, default="us-west-2")
    p.add_argument("--max-tokens", type=int, default=16384)
    p.add_argument("--batch-size", type=int, default=5)
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--max-steps", type=int, default=30)
    p.add_argument("--max-skills-per-topic", type=int, default=5)
    p.add_argument("--max-general-skills", type=int, default=10)
    p.add_argument("--no-evolve", action="store_true")
    p.add_argument("--evolve-all", action="store_true")
    p.add_argument("--feedback-level", type=str, default="standard",
                   choices=["minimal", "standard", "full"])
    p.add_argument("--output-dir", type=str, default="outputs/osworld_evolve_engine")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for n in ("botocore", "urllib3", "httpcore", "httpx"):
        logging.getLogger(n).setLevel(logging.WARNING)

    model_id = MODEL_MAP.get(args.solver_model, args.solver_model)
    curator_model_id = MODEL_MAP.get(args.curator_model, args.curator_model)

    # Load tasks
    all_tasks = load_osworld_tasks(args.task_file, domain=args.domain)
    if args.tasks:
        ids = set(n.strip() for n in args.tasks.split(","))
        all_tasks = [t for t in all_tasks if _task_id(t) in ids]
    if args.shuffle:
        import random
        random.seed(42)
        random.shuffle(all_tasks)
    if args.limit:
        all_tasks = all_tasks[:args.limit]

    if not all_tasks:
        print("No tasks to run.")
        return

    # Setup workspace + engine
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir = output_dir / "workspace"
    log_dir = output_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    if not workspace_dir.exists():
        workspace_dir.mkdir(parents=True, exist_ok=True)
        (workspace_dir / "skills" / "topic").mkdir(parents=True, exist_ok=True)
        (workspace_dir / "skills" / "general").mkdir(parents=True, exist_ok=True)
        seed_src = Path(__file__).resolve().parent.parent.parent / "seed_workspaces" / "osworld" / "skills"
        if seed_src.exists():
            seed_dst = workspace_dir / "skills" / "seed"
            shutil.copytree(seed_src, seed_dst)

    config = EvolveConfig(
        evolver_model=curator_model_id,
        extra={"region": args.region},
    )
    engine = ProposeCurateEngine(
        config=config,
        max_skills_per_topic=args.max_skills_per_topic,
        max_general_skills=args.max_general_skills,
        skill_layout="topic",
        curator_model=curator_model_id,
        evolve_passed=args.evolve_all,
        topic_curator_prompt=CURATOR_PROMPT,
        general_curator_prompt=GENERAL_CURATOR_PROMPT,
        format_failed_summary=_format_failed_summary_standard,
    )
    workspace = AgentWorkspace(workspace_dir)

    # Batch loop
    all_results = []
    batches = [all_tasks[i:i + args.batch_size] for i in range(0, len(all_tasks), args.batch_size)]

    logger.info("Running %d tasks in %d batches | solver=%s curator=%s",
                len(all_tasks), len(batches), model_id, curator_model_id)

    for bi, batch in enumerate(batches):
        logger.info("=== Batch %d/%d (%d tasks) ===", bi + 1, len(batches), len(batch))

        skills = load_skills(workspace_dir)
        system_prompt = build_system_prompt(skills)

        # Parallel solve
        task_queue: queue_mod.Queue = queue_mod.Queue()
        for t in batch:
            task_queue.put(t)

        task_outputs: dict[str, dict] = {}
        outputs_lock = threading.Lock()

        def _worker(worker_idx: int):
            from desktop_env.desktop_env import DesktopEnv
            env = None
            try:
                env_kwargs = dict(
                    provider_name=args.provider,
                    region=args.region,
                    os_type="Ubuntu",
                    action_space="claude_computer_use",
                    screen_size=(1920, 1080),
                    require_a11y_tree=False,
                )
                if args.provider == "aws":
                    from desktop_env.providers.aws.manager import IMAGE_ID_MAP
                    ami = IMAGE_ID_MAP[args.region].get((1920, 1080))
                    env_kwargs["snapshot_name"] = ami
                env = DesktopEnv(**env_kwargs)
                with _live_envs_lock:
                    _live_envs.append(env)

                while True:
                    try:
                        t = task_queue.get_nowait()
                    except queue_mod.Empty:
                        break
                    tid = _task_id(t)
                    try:
                        out = solve_one_task(
                            task_config=t, env=env,
                            model_id=model_id, region=args.region,
                            max_tokens=args.max_tokens,
                            system_prompt=system_prompt,
                            workspace_dir=workspace_dir,
                            log_dir=log_dir,
                            max_steps=args.max_steps,
                            evolve_all=args.evolve_all,
                            curator_model=curator_model_id,
                            feedback_level=args.feedback_level,
                        )
                    except Exception as e:
                        logger.error("[worker-%d] Task %s failed: %s", worker_idx, tid, e)
                        out = {
                            "task_name": tid, "domain": "unknown",
                            "passed": False, "score": 0.0, "error": str(e),
                            "proposal": None, "feedback_analysis": None,
                        }
                    with outputs_lock:
                        task_outputs[tid] = out
            finally:
                if env:
                    try:
                        env.close()
                    except Exception:
                        pass
                    with _live_envs_lock:
                        try:
                            _live_envs.remove(env)
                        except ValueError:
                            pass

        threads = []
        for i in range(min(args.workers, len(batch))):
            t = threading.Thread(target=_worker, args=(i,))
            t.start()
            threads.append(t)
        for t in threads:
            t.join()

        # Build observations for engine
        observations = []
        for tc in batch:
            tid = _task_id(tc)
            out = task_outputs.get(tid, {})
            passed = out.get("passed", False)
            score = out.get("score", 0.0)

            raw = {
                "domain": out.get("domain", "unknown"),
                "task_name": tid,
            }
            if out.get("proposal"):
                raw["proposal"] = out["proposal"]
            if out.get("trajectory_signals"):
                raw["trajectory_signals"] = out["trajectory_signals"]
            if out.get("compressed_trajectory"):
                raw["compressed_trajectory"] = out["compressed_trajectory"]
            if out.get("feedback_analysis"):
                raw["feedback_analysis"] = out["feedback_analysis"]
            ed = out.get("eval_detail", {})
            if ed.get("metric_func"):
                raw["eval_metric"] = ed["metric_func"]
            if ed.get("failure_reason"):
                raw["failure_reason"] = ed["failure_reason"]
            if ed.get("bot_detection"):
                raw["bot_detection"] = ed["bot_detection"]

            task_obj = Task(id=tid, input=tc.get("instruction", ""), metadata={"domain": out.get("domain")})
            traj = Trajectory(task_id=tid, output="")
            fb = Feedback(success=passed, score=score, detail=out.get("feedback_analysis", ""), raw=raw)
            observations.append(Observation(task=task_obj, trajectory=traj, feedback=fb))

            all_results.append(out)

        # Run engine
        if not args.no_evolve and observations:
            result = engine.step(workspace, observations, history=None, trial=None)
            logger.info("Engine: %s", result.summary)

        passed_so_far = sum(1 for r in all_results if r.get("passed"))
        logger.info("Cumulative: %d/%d (%.1f%%)", passed_so_far, len(all_results),
                    100 * passed_so_far / max(len(all_results), 1))

    # Final summary
    total_passed = sum(1 for r in all_results if r.get("passed"))
    total = len(all_results)
    logger.info("=" * 60)
    logger.info("FINAL: %d/%d (%.1f%%)", total_passed, total, 100 * total_passed / max(total, 1))

    summary = {
        "timestamp": datetime.now().isoformat(),
        "solver_model": model_id,
        "curator_model": curator_model_id,
        "total": total,
        "passed": total_passed,
        "rate": total_passed / max(total, 1),
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    with open(output_dir / "all_results.jsonl", "w") as f:
        for r in all_results:
            f.write(json.dumps(r, default=str) + "\n")

    _cleanup_all_envs()


if __name__ == "__main__":
    main()
