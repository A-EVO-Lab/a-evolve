#!/usr/bin/env python3
"""Evolve an agent on WebArena Infinity using the A-EVOLVE propose+curator loop.

Uses the browser-use library with Bedrock models (ChatAWSBedrock) for agent
interaction, and WebArena Infinity's programmatic verifiers for evaluation.

Pipeline per batch:
  1. Parallel solve (browser-use Agent with ChatAWSBedrock on WebArena envs)
  2. Evaluate via programmatic verifiers (each task has a Python verify script)
  3. Analyze feedback + propose skills (via Bedrock Converse)
  4. Curator per topic (self-assigned domain skills)
  5. General curator (cross-task patterns)
  6. Reload workspace, next batch

Usage:
    python evo_harness/webarena_infinity.py \
        --webarena-dir /path/to/webarena-infinity \
        --web-app gmail --difficulty easy \
        --batch-size 5 --workers 2 \
        --output-dir outputs/webarena_evolve_v1

Prerequisites:
    pip install "browser-use[aws]"
    git clone https://github.com/web-arena-x/webarena-infinity.git
"""
from __future__ import annotations

import argparse
import atexit
import asyncio
import importlib.util
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["BYPASS_TOOL_CONSENT"] = "true"

logger = logging.getLogger(__name__)

_write_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Process cleanup — kill all child processes on exit/signal
# ---------------------------------------------------------------------------

_child_procs: list[subprocess.Popen] = []
_child_procs_lock = threading.Lock()
_cleanup_done = False


def _register_child(proc: subprocess.Popen):
    with _child_procs_lock:
        _child_procs.append(proc)


def _unregister_child(proc: subprocess.Popen):
    with _child_procs_lock:
        try:
            _child_procs.remove(proc)
        except ValueError:
            pass


def _kill_proc_tree(pid: int, sig: int = signal.SIGKILL):
    """Recursively kill all descendants of *pid*, then pid itself."""
    try:
        children = subprocess.run(
            ["pgrep", "-P", str(pid)],
            capture_output=True, text=True, timeout=5,
        )
        for child_pid in children.stdout.strip().split():
            if child_pid:
                _kill_proc_tree(int(child_pid), sig)
    except Exception:
        pass
    try:
        os.kill(pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _cleanup_all_children():
    """Kill all tracked child processes and their entire process trees."""
    global _cleanup_done
    if _cleanup_done:
        return
    _cleanup_done = True

    with _child_procs_lock:
        procs = list(_child_procs)
        _child_procs.clear()

    if not procs:
        return

    logger.info("Cleaning up %d tracked child processes...", len(procs))

    for proc in procs:
        try:
            if proc.poll() is None:
                proc.terminate()
        except Exception:
            pass

    time.sleep(2)

    for proc in procs:
        try:
            if proc.poll() is None:
                _kill_proc_tree(proc.pid)
        except Exception:
            pass

    # Belt-and-suspenders: kill direct children of the main process
    # (catches chromium processes spawned by playwright/browser-use)
    our_pid = os.getpid()
    try:
        result = subprocess.run(
            ["pgrep", "-P", str(our_pid)],
            capture_output=True, text=True, timeout=5,
        )
        for child_pid in result.stdout.strip().split():
            if child_pid:
                _kill_proc_tree(int(child_pid))
    except Exception:
        pass

    logger.info("Child process cleanup done.")


def _signal_handler(sig, frame):
    signame = "SIGINT" if sig == signal.SIGINT else "SIGTERM"
    logger.warning("Received %s — cleaning up child processes...", signame)
    _cleanup_all_children()
    sys.exit(1)


atexit.register(_cleanup_all_children)
signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)

# ---------------------------------------------------------------------------
# Seed skills
# ---------------------------------------------------------------------------

SEED_SKILLS_DIR = Path(__file__).resolve().parent.parent / "seed_skills" / "webarena_infinity"


def _init_seed_skills(workspace_dir: Path):
    """Copy seed skills from seed workspace into workspace/skills/seed/."""
    seed_skills_src = SEED_SKILLS_DIR
    seed_skills_dst = workspace_dir / "skills" / "seed"
    if seed_skills_dst.exists():
        return
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

# browser-use uses langchain model IDs (different from Bedrock Converse IDs)
# browser-use ChatAWSBedrock also needs inference profile IDs (us. prefix)
BROWSER_USE_MODEL_MAP = {
    "1": "us.anthropic.claude-opus-4-6-v1",
    "2": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "3": "us.anthropic.claude-opus-4-5-20251101-v1:0",
}

# ---------------------------------------------------------------------------
# Bedrock helpers (for curation LLM calls)
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
            config=BotoConfig(connect_timeout=60, read_timeout=600, retries={"max_attempts": 0}),
        )
        setattr(_thread_local, key, client)
    return getattr(_thread_local, key)


def _call_bedrock(client, model_id, system_prompt, user_message,
                  max_tokens=4096, temperature=0.0, total_timeout=900):
    """Simple Bedrock converse call. Returns (text, error)."""
    t0 = time.time()
    for attempt in range(5):
        if time.time() - t0 > total_timeout:
            return None, f"total timeout ({total_timeout}s) exceeded after {attempt} attempts"
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
# WebArena Infinity task loading
# ---------------------------------------------------------------------------

def load_webarena_tasks(
    webarena_dir: str,
    web_app: str | None = None,
    difficulty: str | None = None,
    task_suite: str = "real-tasks",
    limit: int | None = None,
) -> list[dict]:
    """Load tasks from WebArena Infinity apps/ directory.

    Structure: apps/<web_app>/<task_suite>.json
    Each task: {"id": "task_e1", "instruction": "...", "difficulty": "easy", "verify": "real-tasks/task_e1.py"}
    """
    wa_path = Path(webarena_dir)
    apps_base = wa_path / "apps"

    all_tasks = []

    # Find web app directories
    app_dirs = []
    if web_app:
        for app in web_app.split(","):
            app = app.strip()
            app_dir = apps_base / app
            if app_dir.is_dir():
                app_dirs.append((app, app_dir))
            else:
                # Prefix match
                for d in sorted(apps_base.iterdir()):
                    if d.is_dir() and d.name.startswith(app):
                        app_dirs.append((d.name, d))
    else:
        skip = {"__pycache__", "ablations", "app-description", "user-manuals"}
        # These apps are excluded from the official 1,260-task benchmark:
        # - figma-slides, figma-text-and-typography: not in the official 10 environments
        # - elation-patient-communication: "largely unusable" per the paper's manual examination
        excluded = {"figma-slides", "figma-text-and-typography", "elation-patient-communication"}
        for d in sorted(apps_base.iterdir()):
            if d.is_dir() and d.name not in skip and d.name not in excluded and not d.name.startswith("."):
                if (d / f"{task_suite}.json").exists():
                    app_dirs.append((d.name, d))

    for app_name, app_dir in app_dirs:
        tasks_file = app_dir / f"{task_suite}.json"
        if not tasks_file.exists():
            logger.warning("No %s.json in %s", task_suite, app_dir)
            continue
        try:
            raw_tasks = json.loads(tasks_file.read_text())
            if isinstance(raw_tasks, list):
                for t in raw_tasks:
                    t["web_app"] = app_name
                    t["_web_app_dir"] = str(app_dir)
                    all_tasks.append(t)
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Failed to load %s: %s", tasks_file, e)

    # Filter by difficulty
    if difficulty:
        difficulties = set(d.strip() for d in difficulty.split(","))
        all_tasks = [t for t in all_tasks if t.get("difficulty", "") in difficulties]

    # Normalize
    cleaned = []
    for t in all_tasks:
        if not t.get("instruction") and not t.get("intent"):
            continue
        task = {
            "id": t.get("id", t.get("task_id", f"task_{len(cleaned)}")),
            "instruction": t.get("instruction", t.get("intent", "")),
            "difficulty": t.get("difficulty", "unknown"),
            "verify": t.get("verify", ""),
            "web_app": t.get("web_app", "unknown"),
            "web_app_dir": t.get("_web_app_dir", ""),
            "metadata": {k: v for k, v in t.items()
                         if k not in ("id", "instruction", "intent", "difficulty",
                                      "verify", "web_app", "_web_app_dir")},
        }
        cleaned.append(task)

    if limit:
        cleaned = cleaned[:limit]

    logger.info("Loaded %d WebArena Infinity tasks from %s (apps: %s)",
                len(cleaned), webarena_dir,
                ", ".join(sorted(set(t["web_app"] for t in cleaned))))
    return cleaned


# ---------------------------------------------------------------------------
# WebArena Infinity server management (uses evaluation/server.py)
# ---------------------------------------------------------------------------

_server_module_cache = None


def _get_server_module(webarena_dir: str):
    """Import start_server/stop_server/wait_for_server from evaluation/server.py (cached)."""
    global _server_module_cache
    if _server_module_cache is None:
        server_path = Path(webarena_dir) / "evaluation" / "server.py"
        if not server_path.exists():
            raise FileNotFoundError(f"Server module not found: {server_path}")
        spec = importlib.util.spec_from_file_location("wa_server", server_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _server_module_cache = mod
    return _server_module_cache


def reset_task_state(server_url: str):
    """Reset environment state via POST /api/reset (WebArena Infinity convention)."""
    import requests
    try:
        resp = requests.post(f"{server_url}/api/reset", timeout=30)
        resp.raise_for_status()
        time.sleep(0.5)
    except Exception as e:
        logger.debug("Reset failed for %s: %s", server_url, e)


# ---------------------------------------------------------------------------
# WebArena Infinity verifier
# ---------------------------------------------------------------------------

_VERIFY_TIMEOUT = 60  # seconds — per-verifier hard limit

def verify_task(task: dict, server_url: str) -> tuple[bool, str]:
    """Run the programmatic verifier for a task.

    Verifiers live at <web_app_dir>/<task["verify"]>.
    Returns (passed, detail_message).
    """
    if not task.get("verify"):
        return False, "No verifier specified for task"

    web_app_dir = task.get("web_app_dir", "")
    verifier_path = Path(web_app_dir) / task["verify"] if web_app_dir else None

    if not verifier_path or not verifier_path.exists():
        return False, f"Verifier not found: {task['verify']} in {web_app_dir}"

    try:
        import requests as _req
        old_get, old_post = _req.get, _req.post
        def _get_with_timeout(*a, **kw):
            kw.setdefault("timeout", 30)
            return old_get(*a, **kw)
        def _post_with_timeout(*a, **kw):
            kw.setdefault("timeout", 30)
            return old_post(*a, **kw)
        _req.get, _req.post = _get_with_timeout, _post_with_timeout

        try:
            spec = importlib.util.spec_from_file_location(
                f"verifier_{task['id']}", verifier_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            if hasattr(mod, "verify"):
                result = mod.verify(server_url)
                if isinstance(result, tuple) and len(result) == 2:
                    return bool(result[0]), str(result[1])
                elif isinstance(result, bool):
                    return result, "passed" if result else "failed"
                else:
                    return bool(result), str(result)
            else:
                return False, f"Verifier has no verify() function: {verifier_path}"
        finally:
            _req.get, _req.post = old_get, old_post

    except Exception as e:
        return False, f"Verifier error: {str(e)[:500]}"


# ---------------------------------------------------------------------------
# browser-use agent wrapper
# ---------------------------------------------------------------------------

def _create_llm(browser_use_model_id: str, region: str, *, use_boto3: bool = False):
    """Create a browser-use LLM backed by Bedrock.

    For Claude models, defaults to ChatAnthropicBedrock (Anthropic Messages API
    format via Bedrock auth) which matches the official webarena-infinity setup.
    Falls back to ChatAWSBedrock (boto3 Converse API) for non-Claude models or
    when use_boto3=True.
    """
    import boto3 as _boto3
    is_claude = "anthropic" in browser_use_model_id or "claude" in browser_use_model_id
    if is_claude and not use_boto3:
        from browser_use.llm.aws.chat_anthropic import ChatAnthropicBedrock
        return ChatAnthropicBedrock(
            model=browser_use_model_id,
            aws_region=region,
            session=_boto3.Session(region_name=region),
            max_tokens=8192,
        )
    from browser_use.llm import ChatAWSBedrock
    return ChatAWSBedrock(
        model=browser_use_model_id,
        aws_region=region,
        session=_boto3.Session(region_name=region),
    )


def _get_browser_use_agent_class(webarena_dir: str):
    """Import BrowserUseAgent from webarena-infinity/evaluation/agents.py (cached)."""
    cached = globals().get("_wa_BrowserUseAgent")
    if cached is None:
        agents_path = Path(webarena_dir) / "evaluation" / "agents.py"
        spec = importlib.util.spec_from_file_location("wa_agents", agents_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["wa_agents"] = mod  # @dataclass needs this to resolve cls.__module__
        spec.loader.exec_module(mod)
        cached = mod.BrowserUseAgent
        globals()["_wa_BrowserUseAgent"] = cached
    return cached


# ---------------------------------------------------------------------------
# Trajectory analysis helpers (adapted for browser-use)
# ---------------------------------------------------------------------------

def _extract_trajectory_signals(agent_result: dict) -> dict:
    """Extract structured behavioral signals from browser-use agent result."""
    actions = agent_result.get("actions", [])
    n_steps = agent_result.get("n_steps", len(actions))
    n_errors = 0
    n_clicks = 0
    n_fills = 0
    n_navigations = 0
    n_scrolls = 0
    error_messages = []

    for a in actions:
        a_lower = a.lower()
        if "error" in a_lower or "failed" in a_lower or "exception" in a_lower:
            n_errors += 1
            error_messages.append(a[:150])
        if "click" in a_lower:
            n_clicks += 1
        if "fill" in a_lower or "type" in a_lower or "input" in a_lower:
            n_fills += 1
        if "goto" in a_lower or "navigate" in a_lower or "go_to" in a_lower:
            n_navigations += 1
        if "scroll" in a_lower:
            n_scrolls += 1

    # Detect repeated actions
    action_counts: dict[str, int] = {}
    for a in actions:
        key = a[:100]
        action_counts[key] = action_counts.get(key, 0) + 1
    repeated = [a for a, cnt in action_counts.items() if cnt >= 3]

    return {
        "n_steps": n_steps,
        "n_errors": n_errors,
        "n_clicks": n_clicks,
        "n_fills": n_fills,
        "n_navigations": n_navigations,
        "n_scrolls": n_scrolls,
        "submitted": bool(agent_result.get("final_result")),
        "repeated_actions": repeated,
        "error_snippets": error_messages[:5],
        "elapsed": agent_result.get("elapsed", 0),
    }


def _compress_trajectory(agent_result: dict) -> str:
    """Compress a browser-use trajectory into a failure-focused summary."""
    actions = agent_result.get("actions", [])
    n_steps = len(actions)
    n_errors = sum(1 for a in actions if "error" in a.lower() or "failed" in a.lower())

    parts = [
        f"Steps: {n_steps}, Errors: {n_errors}, "
        f"Final: {bool(agent_result.get('final_result'))}"
    ]

    # First 3 actions
    for i, a in enumerate(actions[:3]):
        parts.append(f"[start] {a[:200]}")

    # Errors
    errors = [a for a in actions if "error" in a.lower() or "failed" in a.lower()]
    if errors:
        parts.append(f"\n--- Errors ({len(errors)}) ---")
        for e in errors[:5]:
            parts.append(f"  {e[:200]}")

    # Repeated actions
    action_counts: dict[str, int] = {}
    for a in actions:
        key = a[:100]
        action_counts[key] = action_counts.get(key, 0) + 1
    loops = {a: n for a, n in action_counts.items() if n >= 3}
    if loops:
        parts.append("\n--- Repeated actions ---")
        for a, n in loops.items():
            parts.append(f"  {a[:150]} (x{n})")

    # Last 3 actions
    if len(actions) > 3:
        parts.append("\n--- Final actions ---")
        for a in actions[-3:]:
            parts.append(f"  {a[:200]}")

    if agent_result.get("final_result"):
        parts.append(f"\n[result] {agent_result['final_result'][:300]}")

    if agent_result.get("error"):
        parts.append(f"\n[agent_error] {agent_result['error'][:300]}")

    return "\n".join(parts)


def _format_signals(signals: dict) -> str:
    """Format trajectory signals as a concise text block."""
    lines = [
        f"Steps: {signals['n_steps']}, Errors: {signals['n_errors']}, "
        f"Elapsed: {signals.get('elapsed', 0):.1f}s",
        f"Clicks: {signals['n_clicks']}, Fills: {signals['n_fills']}, "
        f"Navigations: {signals['n_navigations']}, Scrolls: {signals['n_scrolls']}",
        f"Submitted: {signals['submitted']}",
    ]
    if signals.get("repeated_actions"):
        lines.append(f"Repeated: {'; '.join(a[:60] for a in signals['repeated_actions'][:3])}")
    if signals.get("error_snippets"):
        lines.append(f"Errors: {'; '.join(s[:60] for s in signals['error_snippets'][:3])}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Analyze + Propose prompt
# ---------------------------------------------------------------------------

ANALYZE_AND_PROPOSE_PROMPT = """\
The evaluation result for this WebArena task:

{eval_result}

## Agent trajectory summary
{trajectory_summary}

## Trajectory signals
{trajectory_signals}

## Compressed trajectory
{compressed_trajectory}

{existing_skills_section}

## Step 1: Analyze the result
Consider the evaluation outcome, agent trajectory, and signals (errors, loops, timeouts).
For EACH distinct issue or failure reason, output:
ISSUE: <one-line summary of what went wrong or what was needed>
DETAIL: <specific actions, navigation patterns, or techniques that were missing>

## Step 2: Propose a skill
Based on your analysis, write a SHORT skill for future tasks of this type.

TOPIC: <broad topic — use the web app name (e.g. "gmail", "gitlab", "figma", "paypal") or a cross-app technique (e.g. "form-interaction", "search-and-filter", "data-extraction")>
ACTION: NEW / ENHANCE / NONE
TARGET: existing_skill_name (only for ENHANCE)
NAME: short-kebab-name (only for NEW)
DESCRIPTION: one sentence saying WHEN this skill applies — the agent sees ONLY this line to decide whether to read the skill. Be specific: "For Gmail tasks involving label management and email filtering" not "For Gmail tasks"
CONTENT:
## Key techniques
- (specific navigation patterns, element selectors, or workflows for this web app)
## Gotchas
- (specific pitfalls: dynamic elements, confirmation dialogs, timing issues)

FORBIDDEN — do NOT include any of the following (the agent already knows these):
- Basic browser navigation (how to click, type, scroll, go to URL)
- Generic web interaction advice ("wait for page to load", "check if element exists")
- How to use browser tools (click_element, type_text, go_to_url tool usage)
- Generic advice ("be careful", "verify the result", "read the page")
- Retry/timeout strategies
- Task-specific details (specific element IDs, one-time URLs, particular user data)
- Skills that only help for this exact task

REQUIRED — only include web-app-specific knowledge the agent does NOT already have:
- App-specific navigation flows (e.g., where settings are hidden, multi-step workflows)
- App-specific UI quirks (dynamic elements, confirmation dialogs, AJAX-loaded content)
- App-specific element patterns (how to identify the right button/link/form in this app)
- The skill MUST help on UNSEEN tasks in this web app, not just this one

Rules:
- Bullet points, not paragraphs. CONTENT must be under 200 words.
- Be SPECIFIC: include actual navigation flows, UI patterns, element types
- Focus on web-app-specific knowledge, NOT generic advice
- Prefer ENHANCE over NEW if an existing skill is related
- TOPIC must be BROAD: use web app name or cross-app technique. Do NOT create narrow topics
- If the task passed easily or nothing useful was learned, output ACTION: NONE
- The skill MUST be useful for UNSEEN tasks. If it only helps this exact scenario, output ACTION: NONE"""

# ---------------------------------------------------------------------------
# Curator prompt
# ---------------------------------------------------------------------------

CURATOR_PROMPT = """\
You are a skill curator for a web browsing agent solving WebArena tasks. \
You review skill proposals and decide which to keep for topic: {topic}.

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
- Overlaps existing -> MERGE (preferred)
- Budget full -> can only MERGE existing, or SKIP
- Check DESCRIPTION quality: it must clearly say WHEN the skill applies. The agent decides to read based on description alone. Vague descriptions like "for web tasks" → SKIP or rewrite in MERGE
- SKIP proposals containing FORBIDDEN content (basic browser navigation, generic web interaction advice, browser tool usage, retry/timeout strategies) — the agent already knows these
- Keep skills SHORT and SPECIFIC -- actual navigation patterns and techniques
- Few broad skills > many narrow ones
- SKIP proposals that are too task-specific (only help one exact scenario)
- Generalizability test: would this help on 3+ different unseen tasks? If not → SKIP

If no proposals: NO_PROPOSALS"""

# ---------------------------------------------------------------------------
# General curator prompt
# ---------------------------------------------------------------------------

GENERAL_CURATOR_PROMPT = """\
You are a meta-learning curator. You analyze failure patterns ACROSS WebArena tasks \
to distill general skills that help the agent on ANY web browsing task.

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
- (3-5 bullet points: specific browser actions/techniques)
(Under 200 words, bullet points only)

UPDATE_GENERAL: <existing-name>
NEW_CONTENT:
(updated content, under 200 words)

DELETE_GENERAL: <existing-name>
REASON: <why>

If no cross-task patterns: NO_PATTERNS

FORBIDDEN — do NOT include any of the following in skills (the agent already knows these):
- Basic browser navigation (how to click, type, scroll, go to URL)
- Generic web interaction advice ("wait for page to load", "check if element exists")
- How to use browser tools (click_element, type_text, go_to_url tool usage)
- Generic advice ("be careful", "verify the result", "read the page")
- Retry/timeout strategies

REQUIRED — only include web-app-specific knowledge the agent does NOT already have:
- App-specific navigation flows and multi-step workflows
- App-specific UI quirks (dynamic elements, AJAX, confirmation dialogs)
- Cross-app patterns for common web interaction categories (form filling, search, data extraction)

Rules:
- Max {max_general} general skills. Quality > quantity.
- Must appear in 2+ different tasks to be general.
- SPECIFIC and ACTIONABLE -- actual browser patterns, not advice.
- DELETE skills that contain FORBIDDEN content or are too generic.
- Prefer UPDATE over NEW."""

# ---------------------------------------------------------------------------
# Skill data structures
# ---------------------------------------------------------------------------

class SkillMeta:
    def __init__(self, name: str, description: str, path: str, body: str = ""):
        self.name = name
        self.description = description
        self.path = path
        self.body = body


def load_skills(workspace_dir: Path) -> list[SkillMeta]:
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


# ---------------------------------------------------------------------------
# Skill stats tracking and gating
# ---------------------------------------------------------------------------


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
        f"Output ONLY a JSON list of topic names, e.g. [\"gmail\", \"form-interaction\"]. "
        f"If none are relevant, output []."
    )
    client = _get_client(region)
    resp, err = _call_bedrock(client, model_id, "You select relevant skill topics.", prompt,
                              max_tokens=256, temperature=0.0)
    if err or not resp:
        return list(topics.keys())  # fallback: all topics
    try:
        start = resp.find("[")
        end = resp.rfind("]") + 1
        if start >= 0 and end > start:
            selected = json.loads(resp[start:end])
            return [t for t in selected if t in topics]
    except (json.JSONDecodeError, ValueError):
        pass
    return list(topics.keys())  # fallback: all topics


def build_skill_text(
    skills: list[SkillMeta],
    selected_topics: list[str] | None = None,
) -> tuple[str, list[str]]:
    """Build a text block with all skills to inject into the agent instruction.

    If selected_topics is provided, only inject topic skills from those topics.
    Returns (skill_text, list of injected skill names).
    """
    if not skills:
        return "", []

    parts = ["## Expert Skills\n",
             "Use the following domain knowledge to help complete the task:\n"]

    seed_skills = [s for s in skills if "seed/" in s.path]
    topic_skills = [s for s in skills if "topic/" in s.path]
    gen_skills = [s for s in skills if "general/" in s.path]

    if selected_topics is not None:
        topic_skills = [
            s for s in topic_skills
            if any(t in s.path for t in selected_topics)
        ]

    all_injected = seed_skills + topic_skills + gen_skills

    if seed_skills:
        parts.append("### Core Skills")
        for s in seed_skills:
            parts.append(f"\n**{s.name}**\n{s.body}" if s.body else f"\n**{s.name}**: {s.description}")

    if topic_skills:
        by_topic: dict[str, list[SkillMeta]] = defaultdict(list)
        for s in topic_skills:
            path_parts = Path(s.path).parts
            topic = path_parts[2] if len(path_parts) > 3 else path_parts[1] if len(path_parts) > 2 else "other"
            by_topic[topic].append(s)

        if by_topic:
            parts.append("\n### Domain Skills")
            for topic in sorted(by_topic):
                parts.append(f"\n**[{topic}]**")
                for s in by_topic[topic]:
                    parts.append(f"\n**{s.name}**\n{s.body}" if s.body else f"\n**{s.name}**: {s.description}")

    if gen_skills:
        parts.append("\n### General Strategies")
        for s in gen_skills:
            parts.append(f"\n**{s.name}**\n{s.body}" if s.body else f"\n**{s.name}**: {s.description}")

    return "\n".join(parts), [s.name for s in all_injected]


# ---------------------------------------------------------------------------
# Per-task pipeline
# ---------------------------------------------------------------------------

async def _solve_one_task(
    task: dict,
    agent,
    server_url: str,
    bedrock_model_id: str,
    region: str,
    skills: list[SkillMeta],
    workspace_dir: Path,
    log_dir: Path,
    feedback_level: str = "standard",
    do_propose: bool = True,
    evolve_all: bool = False,
    curator_model: str = "",
    selector_model: str = "",
) -> dict:
    """Full pipeline for one WebArena task: reset -> run -> verify -> analyze+propose.

    The agent is a persistent BrowserUseAgent (one per worker, reused across tasks).
    Follows the official run_eval_parallel.py flow: reset_state -> agent.run -> verify.
    """
    task_id = task["id"]
    app_prefix = re.sub(r"[^a-zA-Z0-9_-]", "_", task.get("web_app", "unknown"))
    task_name = f"{app_prefix}__{re.sub(r'[^a-zA-Z0-9_-]', '_', task_id)}"
    t0 = time.time()

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
        injected_skills = []

        # 1. Reset environment state (matches official run_task flow)
        task_log.info("Resetting state for %s (app=%s)", task_id, task["web_app"])
        reset_task_state(server_url)

        # 2. Build skill text and inject into instruction
        selected_topics = None
        if selector_model and skills:
            topic_skills = [s for s in skills if "topic/" in s.path]
            if topic_skills:
                by_topic: dict[str, list[SkillMeta]] = defaultdict(list)
                for s in topic_skills:
                    path_parts = Path(s.path).parts
                    topic = path_parts[2] if len(path_parts) > 3 else path_parts[1] if len(path_parts) > 2 else "other"
                    by_topic[topic].append(s)
                selected_topics = _select_relevant_topics(
                    task["instruction"], by_topic, region, selector_model,
                )
                task_log.info("Selected topics: %s (from %d available)", selected_topics, len(by_topic))
        skill_text, injected_skills = build_skill_text(skills, selected_topics=selected_topics)
        task_text = task["instruction"]
        if skill_text:
            task_text = f"{skill_text}\n\n---\n\n{task_text}"

        # 3. Run agent (reuses persistent browser session from worker)
        task_log.info("Running agent on task...")
        needs_restart = False
        agent_result = {
            "actions": [], "final_result": "", "n_steps": 0,
            "elapsed": 0, "error": None, "history_summary": "",
        }

        try:
            ar = await agent.run(
                task=task_text, server_url=server_url, task_dir=task_log_dir,
            )
            agent_result["elapsed"] = ar.elapsed
            agent_result["n_steps"] = ar.steps
            agent_result["final_result"] = ar.final_result or ""
            agent_result["actions"] = [f"step_{i}" for i in range(ar.steps)]

            summary_parts = [f"Steps: {ar.steps}, Elapsed: {ar.elapsed}s, Done: {ar.is_done}"]
            if ar.final_result:
                summary_parts.append(f"  Final: {ar.final_result[:200]}")
            if ar.errors:
                agent_result["errors"] = [str(e)[:200] for e in ar.errors[:5]]
                summary_parts.append(
                    f"  Errors: {'; '.join(str(e)[:100] for e in ar.errors[:3])}")
                # Check if browser is degraded (matches official pattern)
                err_text = " ".join(str(e) for e in ar.errors)
                if any(k in err_text for k in (
                    "INSUFFICIENT_RESOURCES", "Timeout", "CDP", "consecutive failures",
                )):
                    needs_restart = True
            agent_result["history_summary"] = "\n".join(summary_parts)
            task_log.info("Done: %d steps, %.1fs, done=%s", ar.steps, ar.elapsed, ar.is_done)

        except (asyncio.TimeoutError, asyncio.CancelledError) as e:
            agent_result["elapsed"] = time.time() - t0
            agent_result["error"] = type(e).__name__
            task_log.warning("Agent %s", type(e).__name__)
            needs_restart = True
        except (AssertionError, RuntimeError) as e:
            # browser-use EventBus assertion or stale browser session state
            agent_result["error"] = str(e)[:500]
            agent_result["elapsed"] = time.time() - t0
            task_log.error("Browser session error (will restart): %s", str(e)[:300])
            needs_restart = True
        except Exception as e:
            agent_result["error"] = str(e)[:500]
            agent_result["elapsed"] = time.time() - t0
            task_log.error("Agent error: %s", str(e)[:300])
            needs_restart = True

        solve_time = time.time() - t0

        # 4. Evaluate via programmatic verifier (in thread to avoid blocking event loop)
        task_log.info("Running verifier...")
        try:
            passed, verify_detail = await asyncio.wait_for(
                asyncio.to_thread(verify_task, task, server_url),
                timeout=_VERIFY_TIMEOUT,
            )
        except (asyncio.TimeoutError, asyncio.CancelledError):
            passed, verify_detail = False, f"Verifier timed out after {_VERIFY_TIMEOUT}s"
            task_log.warning("Verifier timed out for %s", task["id"])
        eval_output = f"Passed: {passed}\nDetail: {verify_detail}"

        task_log.info("RESULT: %s (%.0fs) — %s",
                      "PASS" if passed else "FAIL", time.time() - t0,
                      verify_detail[:200])

        # 5. Extract trajectory signals
        traj_signals = _extract_trajectory_signals(agent_result)
        compressed_traj = _compress_trajectory(agent_result)

        # 6. Analyze + Propose (for failures)
        feedback_analysis = None
        proposal = None

        if do_propose and (not passed or evolve_all):
            try:
                if feedback_level == "minimal":
                    eval_text = "FAILED"
                elif feedback_level == "standard":
                    eval_text = f"FAILED\nVerifier: {'passed' if passed else 'failed'}\nFailure reason: {_truncate(verify_detail, 300)}"
                else:  # full
                    eval_text = f"FAILED\n{eval_output}"

                # Existing skills list
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

                prompt_text = ANALYZE_AND_PROPOSE_PROMPT.format(
                    eval_result=eval_text,
                    trajectory_summary=agent_result.get("history_summary", "(no summary)"),
                    trajectory_signals=_format_signals(traj_signals),
                    compressed_trajectory=_truncate(compressed_traj, 1500),
                    existing_skills_section=skills_section,
                )

                # Use Bedrock Converse for analysis (not browser-use)
                client = _get_client(region)
                resp_text, err = await asyncio.to_thread(
                    _call_bedrock,
                    client, curator_model or bedrock_model_id,
                    "You analyze web agent trajectories and propose reusable skills.",
                    prompt_text,
                    1536, 0.3,
                )

                if resp_text:
                    action_idx = resp_text.upper().find("ACTION:")
                    if action_idx > 0:
                        feedback_analysis = resp_text[:action_idx].strip()
                    else:
                        feedback_analysis = resp_text.strip()
                    proposal = _parse_proposal(resp_text, task_name)

            except Exception as e:
                task_log.warning("Analyze+propose failed: %s", str(e)[:200])

        # Save artifacts
        (task_log_dir / "result.txt").write_text(
            f"task_id={task_id}\nweb_app={task['web_app']}\npassed={passed}\n"
            f"verify_detail={verify_detail}\n{eval_output}"
        )
        (task_log_dir / "agent_result.json").write_text(
            json.dumps({k: v for k, v in agent_result.items() if k != "history_json"},
                       indent=2, ensure_ascii=False, default=str)
        )

        return {
            "task_id": task_id,
            "task_name": task_name,
            "web_app": task["web_app"],
            "difficulty": task["difficulty"],
            "passed": passed,
            "verify_detail": verify_detail,
            "eval_output": _truncate(eval_output, 2000),
            "solve_time": solve_time,
            "total_time": time.time() - t0,
            "n_steps": agent_result.get("n_steps", 0),
            "feedback_analysis": feedback_analysis,
            "proposal": proposal,
            "trajectory_signals": traj_signals,
            "compressed_trajectory": compressed_traj,
            "agent_error": agent_result.get("error"),
            "needs_restart": needs_restart,
            "injected_skills": injected_skills,
        }

    except Exception as e:
        import traceback
        task_log.error("FATAL: %s\n%s", str(e)[:500], traceback.format_exc())
        logger.error("Task %s FATAL: %s\n%s", task_id, str(e)[:200], traceback.format_exc())
        return {
            "task_id": task_id,
            "task_name": task_name,
            "web_app": task.get("web_app", "unknown"),
            "difficulty": task.get("difficulty", "unknown"),
            "passed": False,
            "eval_output": f"ERROR: {str(e)[:500]}",
            "solve_time": 0,
            "total_time": time.time() - t0,
            "feedback_analysis": None,
            "proposal": None,
            "error": str(e)[:500],
            "needs_restart": True,
            "injected_skills": injected_skills,
        }


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
            f"  Source: {p['source_task']}\n"
            f"  Description: {p.get('description', '')[:150]}\n"
            f"  Content: {_truncate(p.get('content', ''), 300)}"
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
            parts_str = s.split(":", 1)[1].strip()
            if " INTO " in parts_str.upper():
                sp = parts_str.split(" INTO " if " INTO " in parts_str else " into ")
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
    failed_summaries: list[dict],
    workspace_dir: Path,
    region: str,
    model: str,
    max_general: int = 10,
    feedback_level: str = "standard",
) -> dict:
    if not failed_summaries:
        return {"added": 0, "updated": 0, "deleted": 0}

    summary_lines = []
    for s in failed_summaries[:30]:
        parts = [f"### {s['task_name']} ({s.get('web_app', '?')}) [{s.get('difficulty', '?')}]"]
        if s.get("trajectory_signals"):
            parts.append(f"Signals: {_format_signals(s['trajectory_signals'])}")
        if feedback_level != "minimal" and s.get("compressed_trajectory"):
            parts.append(f"Trajectory:\n{_truncate(s['compressed_trajectory'], 400 if feedback_level == 'standard' else 1000)}")
        if feedback_level != "minimal" and s.get("feedback_analysis"):
            parts.append(f"Analysis:\n{_truncate(s['feedback_analysis'], 400 if feedback_level == 'standard' else 1000)}")
        if s.get("proposal_summary"):
            parts.append(f"Proposal: {_truncate(s['proposal_summary'], 200)}")
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

    prompt = GENERAL_CURATOR_PROMPT.format(
        n_failed=len(failed_summaries),
        failed_summaries="\n\n".join(summary_lines),
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
    existing_names = {n for n, _, _ in existing}
    count = len(existing)
    stats = {"added": 0, "updated": 0, "deleted": 0}

    def _write_gen(name, desc, content):
        d = workspace_dir / "skills" / "general" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n\n{content}")

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
# Skill tree
# ---------------------------------------------------------------------------

def update_skill_tree(workspace_dir: Path):
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
# Batch evolve loop
# ---------------------------------------------------------------------------

async def evolve_batch(
    tasks: list[dict],
    workspace_dir: Path,
    browser_use_model_id: str,
    bedrock_model_id: str,
    curator_model: str,
    region: str,
    webarena_dir: str,
    batch_workers: int,
    max_skills_per_topic: int,
    max_general_skills: int,
    log_dir: Path,
    results_dir: Path,
    base_port: int = 8001,
    batch_label: str = "",
    no_evolve: bool = False,
    max_steps: int = 50,
    timeout_sec: int = 600,
    headless: bool = True,
    selector_model: str = "",
    feedback_level: str = "standard",
    evolve_all: bool = False,
) -> list[dict]:
    """Run one batch: parallel workers -> curate -> update skills.

    Follows the official WebArena Infinity worker pattern (run_eval_parallel.py):
    - Each worker gets its own server port and browser agent
    - One agent per worker, reused across tasks (keep_alive=True)
    - Queue-based task distribution with staggered worker startup
    - restart_session() on browser errors
    """

    skills = load_skills(workspace_dir)

    logger.info("[%s] Solving %d tasks with %d workers...", batch_label, len(tasks), batch_workers)

    # Group tasks by web_app (official runner handles one app at a time)
    by_app = defaultdict(list)
    for t in tasks:
        by_app[t["web_app"]].append(t)

    all_task_results = []
    port_offset = 0

    server_mod = _get_server_module(webarena_dir)
    BrowserUseAgent = _get_browser_use_agent_class(webarena_dir)

    for app_name, app_tasks in by_app.items():
        web_app_dir = app_tasks[0].get("web_app_dir", "")
        num_workers = min(batch_workers, len(app_tasks))

        task_queue: asyncio.Queue = asyncio.Queue()
        for t in app_tasks:
            await task_queue.put(t)

        worker_results: list[dict] = []
        results_lock = asyncio.Lock()

        # Timeout for setup/teardown/restart (not the task itself)
        SETUP_TIMEOUT = 60  # seconds
        TEARDOWN_TIMEOUT = 30
        # Per-task hard timeout: task timeout + extra for verify/propose
        TASK_HARD_TIMEOUT = timeout_sec + 120

        def _force_kill_browser(agent, tag: str):
            """Kill the Chrome process backing an agent's browser session.

            asyncio.wait_for cannot cancel Playwright/CDP blocking ops, so we
            kill the browser process directly to unblock the event loop.
            """
            try:
                session = getattr(agent, "_session", None)
                if session is None:
                    return
                browser = getattr(session, "browser", None) or getattr(session, "_browser", None)
                if browser is None:
                    return
                # Playwright Browser exposes .process for the underlying CDP process
                proc = getattr(browser, "process", None)
                if proc and proc.pid:
                    logger.warning("%s Force-killing browser PID %d", tag, proc.pid)
                    _kill_proc_tree(proc.pid)
                    return
                # Fallback: Playwright's own close (non-blocking attempt)
                import asyncio as _aio
                try:
                    _aio.get_event_loop().call_soon_threadsafe(
                        lambda: _aio.ensure_future(browser.close()))
                except Exception:
                    pass
            except Exception as e:
                logger.debug("%s _force_kill_browser error: %s", tag, e)

        async def _run_with_hard_timeout(coro, timeout_s: float, agent, tag: str):
            """Run *coro* with a hard timeout backed by a watchdog thread.

            If asyncio.wait_for doesn't return within *timeout_s*, a background
            thread kills the browser process to guarantee cancellation.
            """
            done_event = threading.Event()

            def _watchdog():
                if not done_event.wait(timeout=timeout_s + 10):
                    logger.error("%s Watchdog: killing browser after %ds", tag, timeout_s)
                    _force_kill_browser(agent, tag)

            wd = threading.Thread(target=_watchdog, daemon=True)
            wd.start()
            try:
                return await asyncio.wait_for(coro, timeout=timeout_s)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                _force_kill_browser(agent, tag)
                raise
            finally:
                done_event.set()

        async def _app_worker(worker_id: int, port: int):
            """One worker: own server + own agent, pulls tasks from queue."""
            server_proc = None
            tag = f"[W{worker_id}]"

            try:
                # Start dedicated server for this worker (matches official pattern)
                server_proc = server_mod.start_server(web_app_dir, port)
                if server_proc:
                    _register_child(server_proc)
                if not server_mod.wait_for_server(port):
                    logger.error("%s Server failed to start on port %d", tag, port)
                    if server_proc:
                        server_mod.stop_server(server_proc)
                        _unregister_child(server_proc)
                    return
                server_url = f"http://localhost:{port}"
                logger.info("%s Server ready on :%d for %s", tag, port, app_name)

                # Create ONE agent for this worker (reused across all tasks)
                llm = _create_llm(browser_use_model_id, region)
                agent = BrowserUseAgent(
                    llm,
                    max_steps=max_steps,
                    timeout=timeout_sec,
                    headless=headless,
                )

                try:
                    await asyncio.wait_for(agent.setup(server_url), timeout=SETUP_TIMEOUT)
                    logger.info("%s Agent ready", tag)
                except (Exception, asyncio.TimeoutError) as setup_err:
                    logger.error("%s Agent setup failed: %s", tag, setup_err)
                    return

                try:
                    while True:
                        try:
                            task = task_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break

                        task_id = task["id"]
                        logger.info("%s Starting task %s", tag, task_id)

                        try:
                            result = await _run_with_hard_timeout(
                                _solve_one_task(
                                    task=task,
                                    agent=agent,
                                    server_url=server_url,
                                    bedrock_model_id=bedrock_model_id,
                                    region=region,
                                    skills=skills,
                                    workspace_dir=workspace_dir,
                                    log_dir=log_dir,
                                    do_propose=not no_evolve,
                                    evolve_all=evolve_all,
                                    curator_model=curator_model,
                                    selector_model=selector_model,
                                    feedback_level=feedback_level,
                                ),
                                timeout_s=TASK_HARD_TIMEOUT,
                                agent=agent,
                                tag=tag,
                            )
                        except (asyncio.TimeoutError, asyncio.CancelledError):
                            logger.error("%s Task %s HARD TIMEOUT after %ds",
                                         tag, task_id, TASK_HARD_TIMEOUT)
                            _app_pfx = re.sub(r"[^a-zA-Z0-9_-]", "_", task.get("web_app", "unknown"))
                            result = {
                                "task_id": task_id,
                                "task_name": f"{_app_pfx}__{re.sub(r'[^a-zA-Z0-9_-]', '_', task_id)}",
                                "web_app": task.get("web_app", "unknown"),
                                "difficulty": task.get("difficulty", "unknown"),
                                "passed": False,
                                "eval_output": f"HARD TIMEOUT ({TASK_HARD_TIMEOUT}s)",
                                "solve_time": TASK_HARD_TIMEOUT,
                                "total_time": TASK_HARD_TIMEOUT,
                                "feedback_analysis": None,
                                "proposal": None,
                                "needs_restart": True,
                            }

                        status = "PASS" if result.get("passed") else "FAIL"
                        logger.info("%s Task %s: %s (%.0fs)",
                                    tag, task_id, status, result.get("total_time", 0))

                        async with results_lock:
                            worker_results.append(result)

                        # Restart browser if degraded (matches official pattern)
                        if result.get("needs_restart") and not task_queue.empty():
                            logger.info("%s Restarting browser session...", tag)
                            try:
                                await asyncio.wait_for(
                                    agent.restart_session(), timeout=SETUP_TIMEOUT)
                                logger.info("%s Browser restarted OK", tag)
                            except (Exception, asyncio.TimeoutError) as restart_err:
                                logger.error(
                                    "%s Browser restart failed: %s — force-killing", tag, restart_err)
                                _force_kill_browser(agent, tag)
                                try:
                                    agent._session = None
                                    await agent._start_session()
                                    page = await agent._session.get_current_page()
                                    await page.goto(server_url)
                                    await asyncio.sleep(2)
                                    logger.info("%s Fresh browser started OK", tag)
                                except Exception as fresh_err:
                                    logger.error("%s Cannot recover browser: %s", tag, fresh_err)
                                    break
                finally:
                    try:
                        await asyncio.wait_for(agent.teardown(), timeout=TEARDOWN_TIMEOUT)
                    except (Exception, asyncio.TimeoutError):
                        _force_kill_browser(agent, tag)

            finally:
                if server_proc:
                    server_mod.stop_server(server_proc)
                    _unregister_child(server_proc)

        # Staggered worker launch (matches official STAGGER_DELAY = 5)
        STAGGER_DELAY = 5

        async def _staggered_worker(i):
            if i > 0:
                await asyncio.sleep(i * STAGGER_DELAY)
            await _app_worker(i, base_port + port_offset + i)

        # Batch-level timeout: worst case = stagger + all tasks serial on one worker
        batch_timeout = (num_workers * STAGGER_DELAY) + (len(app_tasks) * TASK_HARD_TIMEOUT)
        try:
            await asyncio.wait_for(
                asyncio.gather(*[_staggered_worker(i) for i in range(num_workers)]),
                timeout=batch_timeout,
            )
        except (asyncio.TimeoutError, asyncio.CancelledError):
            logger.error("App %s: batch-level timeout after %ds, moving on", app_name, batch_timeout)
        all_task_results.extend(worker_results)
        port_offset += num_workers

    # Collect results
    results = all_task_results
    proposals = []
    failed_summaries = []

    for out in results:
        task_name = out.get("task_name", "")
        passed = out.get("passed", False)

        (results_dir / f"{task_name}.json").write_text(json.dumps(out, indent=2, default=str))

        if out.get("proposal"):
            proposals.append(out["proposal"])

        if not passed:
            proposal_summary = ""
            if out.get("proposal"):
                p = out["proposal"]
                proposal_summary = f"[{p.get('action', 'NEW')}] {p.get('name', '')}: {p.get('description', '')}"
            failed_summaries.append({
                "task_name": task_name,
                "web_app": out.get("web_app", "unknown"),
                "difficulty": out.get("difficulty", "unknown"),
                "feedback_analysis": out.get("feedback_analysis", ""),
                "proposal_summary": proposal_summary,
                "trajectory_signals": out.get("trajectory_signals"),
                "compressed_trajectory": out.get("compressed_trajectory", ""),
            })

    passed_count = sum(1 for r in results if r.get("passed"))
    logger.info("[%s] %d/%d passed, %d proposals", batch_label, passed_count, len(tasks), len(proposals))

    if no_evolve:
        return results

    # Curate per topic
    if proposals:
        topic_proposals = defaultdict(list)
        for p in proposals:
            topic_proposals[p.get("topic", "general")].append(p)

        total_stats = {"added": 0, "merged": 0, "skipped": 0}
        for topic, topic_props in topic_proposals.items():
            stats = await asyncio.to_thread(
                _curate_topic_proposals,
                topic, topic_props, workspace_dir, region, curator_model, max_skills_per_topic,
            )
            for k in total_stats:
                total_stats[k] += stats.get(k, 0)

        logger.info("[%s] Topic curation: +%d added, %d merged, %d skipped",
                     batch_label, total_stats["added"], total_stats["merged"], total_stats["skipped"])

    # General curator
    if max_general_skills > 0 and len(failed_summaries) >= 2:
        gen_stats = await asyncio.to_thread(
            _curate_general_skills,
            failed_summaries, workspace_dir, region, curator_model, max_general_skills,
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
    p = argparse.ArgumentParser(
        description="WebArena Infinity with A-EVOLVE propose+curator (browser-use + Bedrock)")
    p.add_argument("--webarena-dir", type=str, required=True,
                   help="Path to cloned webarena-infinity repo")
    p.add_argument("--web-app", type=str, default=None,
                   help="Comma-separated web app names (e.g. gmail,gitlab). Default: all")
    p.add_argument("--difficulty", type=str, default=None,
                   help="Comma-separated difficulty filters (easy,medium,hard)")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--shuffle", action="store_true")
    p.add_argument("--shuffle-seed", type=int, default=42)
    p.add_argument("--solver-model", type=str, default="2",
                   help="Model for browser-use solver (1=Opus, 2=Sonnet, 3=Opus4.5)")
    p.add_argument("--curator-model", type=str, default="2",
                   help="Model for curation (1=Opus, 2=Sonnet)")
    p.add_argument("--selector-model", type=str, default="2",
                   help="Model for topic skill selection (1=Opus, 2=Sonnet). Empty to disable.")
    p.add_argument("--region", type=str, default="us-west-2")
    p.add_argument("--batch-size", type=int, default=5)
    p.add_argument("--workers", type=int, default=2,
                   help="Parallel workers (each runs a browser instance)")
    p.add_argument("--max-steps", type=int, default=50,
                   help="Max browser-use agent steps per task")
    p.add_argument("--timeout", type=int, default=600,
                   help="Per-task timeout in seconds (default: 600)")
    p.add_argument("--max-skills-per-topic", type=int, default=5)
    p.add_argument("--max-general-skills", type=int, default=10)
    p.add_argument("--headless", action="store_true", default=True)
    p.add_argument("--no-headless", action="store_true",
                   help="Run browser with visible UI")
    p.add_argument("--no-evolve", action="store_true",
                   help="Baseline mode: skip propose + curation")
    p.add_argument("--evolve-all", action="store_true",
                   help="Propose for ALL tasks, not just failures")
    p.add_argument("--no-seed-skills", action="store_true",
                   help="Skip seed skills")
    p.add_argument("--base-port", type=int, default=8001,
                   help="Starting port for worker servers (each worker gets its own port)")
    p.add_argument("--feedback-level", type=str, default="standard",
                   choices=["minimal", "standard", "full"],
                   help="How much eval detail the evolver sees")
    p.add_argument("--output-dir", type=str, default="outputs/webarena_evolve")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for n in ("botocore", "urllib3", "httpcore", "httpx", "playwright", "browser_use"):
        logging.getLogger(n).setLevel(logging.WARNING)

    headless = args.headless and not args.no_headless
    feedback_level = args.feedback_level

    # Resolve models
    bedrock_model_id = MODEL_MAP.get(args.solver_model, args.solver_model)
    browser_use_model_id = BROWSER_USE_MODEL_MAP.get(args.solver_model, args.solver_model)
    curator_model_id = MODEL_MAP.get(args.curator_model, args.curator_model)
    selector_model_id = MODEL_MAP.get(args.selector_model, args.selector_model) if args.selector_model else ""

    # Load tasks
    all_tasks = load_webarena_tasks(
        webarena_dir=args.webarena_dir,
        web_app=args.web_app,
        difficulty=args.difficulty,
        limit=None,
    )

    if args.shuffle:
        import random
        random.seed(args.shuffle_seed)
        random.shuffle(all_tasks)
    if args.limit:
        all_tasks = all_tasks[:args.limit]

    if not all_tasks:
        print("No tasks found. Check --webarena-dir and --web-app.")
        return

    # Workers manage their own servers (one per worker, matching official pattern)
    base_port = args.base_port

    # Setup workspace
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir = output_dir / "workspace"

    if not workspace_dir.exists():
        workspace_dir.mkdir(parents=True, exist_ok=True)
        (workspace_dir / "skills").mkdir(exist_ok=True)
        (workspace_dir / "skills" / "topic").mkdir(exist_ok=True)
        (workspace_dir / "skills" / "general").mkdir(exist_ok=True)
        if not args.no_seed_skills:
            _init_seed_skills(workspace_dir)

    log_dir = output_dir / "logs"
    log_dir.mkdir(exist_ok=True)
    results_dir = output_dir / "results"
    results_dir.mkdir(exist_ok=True)

    # Stats by app
    by_app = defaultdict(int)
    by_diff = defaultdict(int)
    for t in all_tasks:
        by_app[t["web_app"]] += 1
        by_diff[t["difficulty"]] += 1

    logger.info(
        "Running %d tasks | solver=%s curator=%s | batch=%d workers=%d | "
        "max_steps=%d timeout=%ds headless=%s",
        len(all_tasks), browser_use_model_id, curator_model_id,
        args.batch_size, args.workers, args.max_steps, args.timeout, headless,
    )
    logger.info("Apps: %s", ", ".join(f"{k}({v})" for k, v in sorted(by_app.items())))
    logger.info("Difficulty: %s", ", ".join(f"{k}({v})" for k, v in sorted(by_diff.items())))

    # Batch loop — group by app so each batch only contains one app's tasks.
    # This avoids starting servers for multiple apps simultaneously and
    # matches the official runner which handles one app at a time.
    all_results = []
    t0 = time.time()

    tasks_by_app: dict[str, list[dict]] = defaultdict(list)
    for t in all_tasks:
        tasks_by_app[t["web_app"]].append(t)

    batches: list[tuple[str, list[dict]]] = []
    for app_name in sorted(tasks_by_app):
        app_tasks = tasks_by_app[app_name]
        for i in range(0, len(app_tasks), args.batch_size):
            batches.append((app_name, app_tasks[i:i + args.batch_size]))

    logger.info("Created %d batches across %d apps", len(batches), len(tasks_by_app))

    async def _run_all_batches():
        for bi, (app_name, batch) in enumerate(batches):
            logger.info("=== Batch %d/%d (%d tasks, app=%s) ===",
                        bi+1, len(batches), len(batch), app_name)
            batch_results = await evolve_batch(
                tasks=batch, workspace_dir=workspace_dir,
                browser_use_model_id=browser_use_model_id,
                bedrock_model_id=bedrock_model_id,
                curator_model=curator_model_id,
                region=args.region,
                webarena_dir=args.webarena_dir,
                batch_workers=args.workers,
                max_skills_per_topic=args.max_skills_per_topic,
                max_general_skills=args.max_general_skills,
                log_dir=log_dir, results_dir=results_dir,
                base_port=base_port,
                batch_label=f"B{bi+1}/{len(batches)}[{app_name}]",
                no_evolve=args.no_evolve,
                evolve_all=args.evolve_all,
                max_steps=args.max_steps,
                timeout_sec=args.timeout,
                headless=headless,
                selector_model=selector_model_id,
                feedback_level=feedback_level,
            )
            all_results.extend(batch_results)

            passed = sum(1 for r in all_results if r.get("passed"))
            total = len(all_results)
            logger.info("Cumulative: %d/%d (%.1f%%)", passed, total, 100 * passed / max(total, 1))

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_run_all_batches())
    finally:
        # Cancel any lingering tasks (browser-use PostHog, CDP, etc.)
        for task in asyncio.all_tasks(loop):
            task.cancel()
        # Give cancelled tasks a chance to finish (timeout 10s to avoid hanging)
        try:
            loop.run_until_complete(
                asyncio.wait_for(
                    asyncio.gather(*asyncio.all_tasks(loop), return_exceptions=True),
                    timeout=10,
                )
            )
        except (Exception, asyncio.TimeoutError):
            pass
        loop.close()

    # Final summary
    elapsed = time.time() - t0
    total_passed = sum(1 for r in all_results if r.get("passed"))
    total = len(all_results)

    by_app_results = defaultdict(lambda: {"p": 0, "t": 0})
    by_diff_results = defaultdict(lambda: {"p": 0, "t": 0})
    for r in all_results:
        app = r.get("web_app", "unknown")
        diff = r.get("difficulty", "unknown")
        by_app_results[app]["t"] += 1
        by_diff_results[diff]["t"] += 1
        if r.get("passed"):
            by_app_results[app]["p"] += 1
            by_diff_results[diff]["p"] += 1

    logger.info("=" * 70)
    logger.info("FINAL: %d/%d (%.1f%%) in %.0fs", total_passed, total,
                100 * total_passed / max(total, 1), elapsed)
    for diff in ["easy", "medium", "hard", "unknown"]:
        if diff in by_diff_results:
            d = by_diff_results[diff]
            logger.info("  %s: %d/%d (%.1f%%)", diff, d["p"], d["t"], 100*d["p"]/max(d["t"],1))
    for app, d in sorted(by_app_results.items()):
        logger.info("  %s: %d/%d (%.1f%%)", app, d["p"], d["t"], 100*d["p"]/max(d["t"],1))

    # Save summary
    summary = {
        "timestamp": datetime.now().isoformat(),
        "solver_model": browser_use_model_id,
        "curator_model": curator_model_id,
        "feedback_level": feedback_level,
        "total": total,
        "passed": total_passed,
        "rate": total_passed / max(total, 1),
        "elapsed": elapsed,
        "by_web_app": {k: dict(v) for k, v in by_app_results.items()},
        "by_difficulty": {k: dict(v) for k, v in by_diff_results.items()},
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    with open(output_dir / "all_results.jsonl", "w") as f:
        for r in all_results:
            f.write(json.dumps(r, default=str) + "\n")

    skills_out = output_dir / "final_skills"
    if (workspace_dir / "skills").exists():
        if skills_out.exists():
            shutil.rmtree(skills_out)
        shutil.copytree(workspace_dir / "skills", skills_out)


if __name__ == "__main__":
    main()
    _cleanup_all_children()
    # browser-use leaves non-daemon threads (PostHog, CDP, etc.) that prevent
    # clean exit after all work is done.  Force exit once results are saved.
    os._exit(0)
