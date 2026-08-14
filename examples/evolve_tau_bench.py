#!/usr/bin/env python3
"""Evolve an agent on Tau-bench (airline/retail) using A-EVOLVE propose+curator.

Tau-bench is a tool-calling benchmark where an LLM agent handles customer
service requests (airline reservations / retail orders) against a simulated
database, with an LLM-simulated user.  Evaluation compares the agent's final
database state against ground truth.

Pipeline per batch:
  1. Parallel solve (tau-bench ToolCallingAgent via litellm + Bedrock)
  2. Built-in reward (database state hash + output string matching)
  3. Analyze feedback + propose skills (via Bedrock Converse)
  4. Curator per topic (self-assigned domain skills)
  5. General curator (cross-task patterns)
  6. Reload workspace, next batch

Usage:
    python examples/evolve_tau_bench.py \
        --env retail --task-split test \
        --solver-model 2 --user-model 2 \
        --batch-size 10 --workers 5 \
        --output-dir outputs/tau_bench_retail_evolve

Prerequisites:
    pip install -e /path/to/tau-bench
    AWS credentials configured (Bedrock access)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
import threading
import time
import traceback
import random
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("AWS_REGION_NAME", "us-west-2")

import litellm
litellm.drop_params = True

# Opus 4.7 does not support temperature — patch ToolCallingAgent to skip it
_original_tau_completion = litellm.completion

def _patched_completion(*args, **kwargs):
    model = kwargs.get("model", args[0] if args else "")
    if "opus-4-7" in str(model):
        kwargs.pop("temperature", None)
    return _original_tau_completion(*args, **kwargs)

litellm.completion = _patched_completion

logger = logging.getLogger(__name__)

_write_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Model map
# ---------------------------------------------------------------------------

MODEL_MAP = {
    "1": "us.anthropic.claude-opus-4-6-v1",
    "2": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "3": "us.anthropic.claude-opus-4-5-20251101-v1:0",
}

# ---------------------------------------------------------------------------
# Bedrock helpers (for propose/curate LLM calls via boto3)
# ---------------------------------------------------------------------------

_thread_local = threading.local()


def _get_client(region: str = "us-west-2"):
    key = f"bedrock_{region}"
    c = getattr(_thread_local, key, None)
    if c is None:
        import boto3
        from botocore.config import Config as BotoConfig
        c = boto3.client(
            "bedrock-runtime",
            region_name=region,
            config=BotoConfig(read_timeout=300, retries={"max_attempts": 0}),
        )
        setattr(_thread_local, key, c)
    return c


def _call_bedrock(client, model_id, system_prompt, user_message,
                  max_tokens=4096, temperature=0.0):
    for attempt in range(5):
        try:
            inference_config = {"maxTokens": max_tokens}
            if "opus-4-7" not in model_id:
                inference_config["temperature"] = temperature
            body = {
                "messages": [{"role": "user", "content": [{"text": user_message}]}],
                "system": [{"text": system_prompt}],
                "inferenceConfig": inference_config,
            }
            resp = client.converse(modelId=model_id, **body)
            text = ""
            for block in resp.get("output", {}).get("message", {}).get("content", []):
                if "text" in block:
                    text += block["text"]
            return text, None
        except Exception as e:
            err_str = str(e)
            if "ThrottlingException" in err_str or "Too many requests" in err_str:
                wait = 4 * (2 ** attempt)
            elif "too many tokens" in err_str.lower() or "token" in err_str.lower():
                wait = 30
            else:
                wait = 2 * (2 ** attempt)
            if attempt < 4:
                time.sleep(wait)
            else:
                return "", err_str


def _truncate(s: str, n: int = 300) -> str:
    return s[:n] + "..." if len(s) > n else s


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
    for sf in sorted(skills_dir.rglob("SKILL.md")):
        if "disabled" in sf.parts:
            continue
        content = sf.read_text()
        name = sf.parent.name
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
        rel_path = str(sf.parent.relative_to(skills_dir))
        skills.append(SkillMeta(name=name, description=desc, path=rel_path, body=body))
    return skills


# ---------------------------------------------------------------------------
# Skill stats tracking and gating
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Skill injection into tau-bench wiki (system prompt)
# ---------------------------------------------------------------------------

def _inject_skills_into_wiki(
    wiki: str,
    skills: list[SkillMeta],
    selected_topics: list[str] | None = None,
) -> tuple[str, list[str]]:
    if not skills:
        return wiki, []

    topic_skills = []
    general_skills = []
    for s in skills:
        parts = Path(s.path).parts
        prefix = parts[0] if parts else "general"
        if prefix == "general":
            general_skills.append(s)
        elif prefix == "topic":
            if selected_topics is None:
                topic_skills.append(s)
            else:
                topic_name = parts[1] if len(parts) > 1 else ""
                if topic_name in selected_topics:
                    topic_skills.append(s)
        elif prefix == "seed":
            topic_skills.append(s)

    if not topic_skills and not general_skills:
        return wiki, []

    sections = ["\n\n## Expert Tips from Previous Tasks"]
    if topic_skills:
        sections.append("### Domain Skills")
        for s in topic_skills:
            sections.append(f"**{s.name}**\n{s.body}")
    if general_skills:
        sections.append("### General Strategies")
        for s in general_skills:
            sections.append(f"**{s.name}**\n{s.body}")

    return wiki + "\n".join(sections), [s.name for s in topic_skills + general_skills]


# ---------------------------------------------------------------------------
# Trajectory signals and compression
# ---------------------------------------------------------------------------

def _extract_trajectory_signals(messages: list[dict]) -> dict:
    signals = {
        "n_steps": 0,
        "n_errors": 0,
        "n_user_turns": 0,
        "tools_called": [],
        "tool_call_counts": defaultdict(int),
        "transferred_to_human": False,
        "error_messages": [],
    }
    for m in messages:
        if m.get("role") == "assistant":
            if m.get("tool_calls"):
                signals["n_steps"] += 1
                tc = m["tool_calls"][0]
                name = tc["function"]["name"]
                signals["tools_called"].append(name)
                signals["tool_call_counts"][name] += 1
                if name == "transfer_to_human_agents":
                    signals["transferred_to_human"] = True
            elif m.get("content"):
                signals["n_steps"] += 1
        elif m.get("role") == "tool":
            content = m.get("content", "")
            if content.startswith("Error:"):
                signals["n_errors"] += 1
                signals["error_messages"].append(content[:200])
        elif m.get("role") == "user":
            signals["n_user_turns"] += 1

    signals["tool_call_counts"] = dict(signals["tool_call_counts"])
    return signals


def _format_signals(signals: dict) -> str:
    parts = [
        f"steps={signals['n_steps']}",
        f"errors={signals['n_errors']}",
        f"user_turns={signals['n_user_turns']}",
        f"transferred={'yes' if signals['transferred_to_human'] else 'no'}",
    ]
    if signals.get("tool_call_counts"):
        tool_str = ", ".join(f"{k}:{v}" for k, v in signals["tool_call_counts"].items())
        parts.append(f"tools=[{tool_str}]")
    return " | ".join(parts)


def _compress_trajectory(messages: list[dict], max_chars: int = 3000) -> str:
    steps = []
    step_num = 0
    for i, m in enumerate(messages):
        if m.get("role") == "system":
            continue
        if m.get("role") == "user":
            text = _truncate(m.get("content", ""), 200)
            steps.append(f"[User] {text}")
        elif m.get("role") == "assistant":
            if m.get("tool_calls"):
                tc = m["tool_calls"][0]
                fn = tc["function"]["name"]
                args_str = tc["function"].get("arguments", "{}")
                if isinstance(args_str, str):
                    args_str = _truncate(args_str, 150)
                else:
                    args_str = _truncate(json.dumps(args_str), 150)
                step_num += 1
                steps.append(f"[Step {step_num}] {fn}({args_str})")
            elif m.get("content"):
                step_num += 1
                steps.append(f"[Step {step_num}] respond: {_truncate(m['content'], 200)}")
        elif m.get("role") == "tool":
            content = m.get("content", "")
            steps.append(f"  -> {_truncate(content, 200)}")

    full = "\n".join(steps)
    if len(full) <= max_chars:
        return full

    # Keep first 3 steps + errors + last 3 steps
    head = steps[:8]
    tail = steps[-6:]
    errors = [s for s in steps[8:-6] if "Error:" in s]
    middle = errors[:5] if errors else ["  ... (truncated) ..."]
    return _truncate("\n".join(head + middle + tail), max_chars)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

ANALYZE_AND_PROPOSE_PROMPT = """\
The evaluation result for this {env_name} customer service task:

{eval_result}

## Task user scenario (first message)
{task_instruction}

## Trajectory signals
{trajectory_signals}

## Compressed trajectory
{compressed_trajectory}

{existing_skills_section}

## Step 1: Analyze the result
Consider the evaluation outcome, agent trajectory, and tool usage patterns.
For EACH distinct issue, output:
ISSUE: <what went wrong — policy violation, wrong tool, missing verification, etc.>
DETAIL: <specific tool calls, arguments, or user interactions that were wrong>

## Step 2: Propose a skill
Based on your analysis, write a SHORT skill for future tasks of this type.

TOPIC: <broad topic — e.g. "order-cancellation", "flight-booking", "payment-handling", \
"return-policy", "reservation-modification", "user-verification", "transfer-to-human">
ACTION: NEW / ENHANCE / NONE
TARGET: existing_skill_name (only for ENHANCE)
NAME: short-kebab-name (only for NEW)
DESCRIPTION: one sentence saying WHEN this skill applies
CONTENT:
## Key rules
- (specific policy rules that were violated or misapplied)
## Tool usage patterns
- (correct tool sequences, required verification steps, argument formats)
## Gotchas
- (edge cases, common misinterpretations of the policy)

FORBIDDEN — do NOT include:
- The full wiki/policy content (the agent already has it as system prompt)
- Generic advice ("verify the result", "check the status", "be careful")
- Obvious policy restatements that are clear from the wiki
- Task-specific details (customer names, order IDs, specific products, flight numbers)
- Skills that only apply to this one narrow scenario

REQUIRED — only include:
- Subtle policy interpretations the agent got wrong
- Multi-step workflows that need specific ordering
- Edge cases where the policy is ambiguous
- Verification steps the agent skipped
- The skill MUST help on UNSEEN tasks, not just this one

Rules:
- Bullet points, not paragraphs. CONTENT must be under 200 words.
- Be SPECIFIC: reference exact tool names, argument patterns, policy sections
- Prefer ENHANCE over NEW if an existing skill is related
- TOPIC must be BROAD. Do NOT create narrow task-specific topics.
- If the task passed easily or nothing useful was learned, output ACTION: NONE
- The skill MUST be useful for UNSEEN tasks. If it only helps this exact scenario, output ACTION: NONE"""

CURATOR_PROMPT = """\
You are a skill curator for a tool-calling customer service agent solving \
{env_name} tasks. You review skill proposals for topic: {topic}.

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
- SKIP proposals that just restate obvious policy rules from the wiki
- Keep skills SHORT and SPECIFIC — actual tool usage patterns and edge cases
- Few broad skills > many narrow ones
- SKIP proposals that are too task-specific (only help one exact scenario)
- Generalizability test: would this help on 3+ different unseen tasks? If not → SKIP

If no proposals: NO_PROPOSALS"""

GENERAL_CURATOR_PROMPT = """\
You are a meta-learning curator. You analyze failure patterns ACROSS {env_name} \
customer service tasks to distill general skills.

## Failed Task Analysis ({n_failed} tasks):
{failed_summaries}

## Current General Skills ({n_general}/{max_general} slots):
{general_skills_list}

For REPEATED patterns across 2+ different tasks, output:

NEW_GENERAL: <kebab-name>
DESCRIPTION: <one line>
CONTENT:
## Pattern
- (what failure type)
## Strategy
- (3-5 bullet points: tool usage patterns, policy interpretation, verification steps)
(Under 200 words)

UPDATE_GENERAL: <existing-name>
NEW_CONTENT:
(updated content, under 200 words)

DELETE_GENERAL: <existing-name>
REASON: <why>

If no cross-task patterns: NO_PATTERNS

Rules:
- Max {max_general} general skills. Quality > quantity.
- Must appear in 2+ different tasks to be general.
- SPECIFIC and ACTIONABLE — actual tool patterns, not advice.
- Prefer UPDATE over NEW."""

# ---------------------------------------------------------------------------
# Skill selection
# ---------------------------------------------------------------------------

def _select_relevant_topics(
    task_description: str,
    topics: dict[str, list[SkillMeta]],
    region: str,
    model_id: str,
) -> list[str]:
    if not topics:
        return []

    topic_list = []
    for tname, tskills in sorted(topics.items()):
        descs = ", ".join(s.description[:80] for s in tskills[:3])
        topic_list.append(f"- {tname}: {descs}")

    prompt = (
        "Given the following customer service task, which skill topics are relevant?\n\n"
        f"Task: {task_description[:500]}\n\n"
        f"Available topics:\n" + "\n".join(topic_list) + "\n\n"
        "Output a JSON list of relevant topic names. You are NOT required to select any.\n"
        "Output: "
    )
    client = _get_client(region)
    resp, err = _call_bedrock(client, model_id, "You select relevant skill topics.", prompt,
                              max_tokens=256, temperature=0.0)
    if err or not resp:
        return list(topics.keys())
    try:
        match = re.search(r"\[.*?\]", resp, re.DOTALL)
        if match:
            selected = json.loads(match.group())
            return [t for t in selected if t in topics]
    except (json.JSONDecodeError, TypeError):
        pass
    return list(topics.keys())


# ---------------------------------------------------------------------------
# Proposal parsing
# ---------------------------------------------------------------------------

def _parse_proposal(resp: str, task_name: str) -> dict | None:
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
# Curation functions
# ---------------------------------------------------------------------------

def _curate_topic_proposals(
    topic: str,
    proposals: list[dict],
    workspace_dir: Path,
    region: str,
    model: str,
    max_skills: int = 5,
    env_name: str = "retail",
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
        env_name=env_name,
        topic=topic,
        n_skills=len(existing),
        max_skills=max_skills,
        existing_skills_list=existing_list,
        proposals_list="\n\n".join(proposals_lines),
    )

    client = _get_client(region)
    resp, err = _call_bedrock(client, model, prompt, "Review and decide.", max_tokens=2048)
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
                        for marker in ["ACCEPT:", "MERGE:", "SKIP:", "NO_PROPOSALS"]:
                            if marker in nc:
                                nc = nc[:nc.index(marker)]
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
    env_name: str = "retail",
) -> dict:
    if not failed_summaries:
        return {"added": 0, "updated": 0, "deleted": 0}

    summary_lines = []
    for s in failed_summaries[:30]:
        parts = [f"### Task {s['task_name']} ({s.get('env_name', '?')})"]
        if s.get("trajectory_signals"):
            parts.append(f"Signals: {_format_signals(s['trajectory_signals'])}")
        if feedback_level != "minimal" and s.get("compressed_trajectory"):
            limit = 400 if feedback_level == "standard" else 1000
            parts.append(f"Trajectory:\n{_truncate(s['compressed_trajectory'], limit)}")
        if feedback_level != "minimal" and s.get("feedback_analysis"):
            limit = 400 if feedback_level == "standard" else 1000
            parts.append(f"Analysis:\n{_truncate(s['feedback_analysis'], limit)}")
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

    gen_list = "\n".join(f"- **{n}**: {d}" for n, d, _ in existing) if existing else "(empty)"

    prompt = GENERAL_CURATOR_PROMPT.format(
        env_name=env_name,
        n_failed=len(failed_summaries),
        failed_summaries="\n\n".join(summary_lines),
        n_general=len(existing),
        max_general=max_general,
        general_skills_list=gen_list,
    )

    client = _get_client(region)
    resp, err = _call_bedrock(client, model, prompt, "Analyze and decide.", max_tokens=4096)
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
                        if any(su.startswith(m) for m in
                               ["NEW_GENERAL:", "UPDATE_GENERAL:", "DELETE_GENERAL:", "NO_PATTERNS"]):
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
                        if any(su.startswith(m) for m in
                               ["NEW_GENERAL:", "UPDATE_GENERAL:", "DELETE_GENERAL:", "NO_PATTERNS"]):
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


def update_skill_tree(workspace_dir: Path):
    skills = load_skills(workspace_dir)
    if not skills:
        content = "# Skill Tree\n\nNo skills yet.\n"
    else:
        lines = ["# Skill Tree", "", f"Total: {len(skills)}", ""]
        by_prefix = defaultdict(list)
        for s in skills:
            parts = Path(s.path).parts
            prefix = parts[0] if len(parts) > 1 else "root"
            by_prefix[prefix].append(s)
        for prefix in sorted(by_prefix):
            lines.append(f"## {prefix}/")
            for s in sorted(by_prefix[prefix], key=lambda x: x.name):
                lines.append(f"- **{s.name}**: {s.description}")
            lines.append("")
        content = "\n".join(lines)
    (workspace_dir / "skills" / "SKILL_TREE.md").write_text(content)


# ---------------------------------------------------------------------------
# Per-task solve pipeline
# ---------------------------------------------------------------------------

def _build_feedback(result_info: dict, reward: float,
                    messages: list[dict], feedback_level: str) -> str:
    passed = reward >= (1.0 - 1e-6)
    if passed:
        return "PASSED"

    if feedback_level == "minimal":
        return "FAILED"

    parts = ["FAILED"]

    ri = result_info.get("reward_info")
    if ri:
        ri_info = ri.get("info", {})
        if "r_actions" in ri_info:
            parts.append(f"Database state correct: {ri_info['r_actions']}")
        if "r_outputs" in ri_info:
            parts.append(f"Outputs correct: {ri_info['r_outputs']}")
            outputs = ri_info.get("outputs", {})
            if outputs:
                for out_str, found in outputs.items():
                    if not found:
                        parts.append(f"  Missing output: \"{out_str}\"")

    if feedback_level == "full":
        traj = _compress_trajectory(messages, max_chars=3000)
        parts.append(f"\nFull trajectory:\n{traj}")
    elif feedback_level == "standard":
        traj = _compress_trajectory(messages, max_chars=1000)
        parts.append(f"\nTrajectory summary:\n{traj}")

    return "\n".join(parts)


def _solve_one_task(
    task_index: int,
    env_name: str,
    solver_model: str,
    solver_provider: str,
    user_model: str,
    user_provider: str,
    bedrock_model_id: str,
    region: str,
    skills: list[SkillMeta],
    workspace_dir: Path,
    log_dir: Path,
    feedback_level: str = "standard",
    do_propose: bool = True,
    evolve_all: bool = False,
    selector_model: str = "",
    task_split: str = "test",
    temperature: float = 0.0,
) -> dict:
    from tau_bench.envs import get_env
    from tau_bench.agents.tool_calling_agent import ToolCallingAgent

    task_name = f"{env_name}_task_{task_index}"
    t0 = time.time()

    injected_skills = []

    result_out = {
        "task_index": task_index,
        "task_name": task_name,
        "env_name": env_name,
        "passed": False,
        "reward": 0.0,
        "solve_time": 0.0,
        "total_cost": 0.0,
        "n_steps": 0,
        "trajectory_signals": {},
        "compressed_trajectory": "",
        "feedback_analysis": None,
        "proposal": None,
        "agent_error": None,
        "injected_skills": [],
    }

    try:
        env = get_env(
            env_name,
            user_strategy="llm",
            user_model=user_model,
            user_provider=user_provider,
            task_split=task_split,
            task_index=task_index,
        )

        # Select topics and inject skills
        selected_topics = None
        if skills and selector_model:
            topic_groups = defaultdict(list)
            for s in skills:
                parts = Path(s.path).parts
                if parts[0] == "topic" and len(parts) > 1:
                    topic_groups[parts[1]].append(s)
            if topic_groups:
                task_desc = env.task.instruction[:300]
                selected_topics = _select_relevant_topics(
                    task_desc, topic_groups, region, selector_model)

        augmented_wiki, injected_skills = _inject_skills_into_wiki(env.wiki, skills, selected_topics)
        result_out["injected_skills"] = injected_skills

        agent_kwargs = dict(
            tools_info=env.tools_info,
            wiki=augmented_wiki,
            model=solver_model,
            provider=solver_provider,
        )
        if "opus-4-7" not in solver_model:
            agent_kwargs["temperature"] = temperature
        agent = ToolCallingAgent(**agent_kwargs)

        solve_result = agent.solve(env=env, task_index=task_index, max_num_steps=30)

        result_out["reward"] = solve_result.reward
        result_out["passed"] = solve_result.reward >= (1.0 - 1e-6)
        result_out["total_cost"] = solve_result.total_cost or 0.0
        result_out["solve_time"] = time.time() - t0

        signals = _extract_trajectory_signals(solve_result.messages)
        result_out["trajectory_signals"] = signals
        result_out["n_steps"] = signals["n_steps"]
        result_out["compressed_trajectory"] = _compress_trajectory(
            solve_result.messages, max_chars=2000)

        # Build feedback and propose
        if do_propose and (not result_out["passed"] or evolve_all):
            eval_text = _build_feedback(
                solve_result.info, solve_result.reward,
                solve_result.messages, feedback_level)

            existing_skills_section = ""
            skills_dir = workspace_dir / "skills"
            if skills_dir.exists():
                all_skills = []
                for sf in sorted(skills_dir.rglob("SKILL.md")):
                    content = sf.read_text()
                    sn = sf.parent.name
                    sd = ""
                    for sline in content.split("\n"):
                        if sline.strip().startswith("description:"):
                            sd = sline.split(":", 1)[1].strip()
                            break
                    all_skills.append(f"- {sn}: {sd}")
                if all_skills:
                    existing_skills_section = (
                        f"## Existing skills ({len(all_skills)})\n" +
                        "\n".join(all_skills[:30])
                    )

            prompt_text = ANALYZE_AND_PROPOSE_PROMPT.format(
                env_name=env_name,
                eval_result=eval_text,
                task_instruction=_truncate(env.task.instruction, 500),
                trajectory_signals=_format_signals(signals),
                compressed_trajectory=_truncate(
                    result_out["compressed_trajectory"], 2000),
                existing_skills_section=existing_skills_section,
            )

            client = _get_client(region)
            resp, err = _call_bedrock(
                client, bedrock_model_id,
                "You analyze tool-calling agent failures and propose reusable skills.",
                prompt_text, max_tokens=2048)

            if resp and not err:
                result_out["feedback_analysis"] = resp
                proposal = _parse_proposal(resp, task_name)
                if proposal:
                    result_out["proposal"] = proposal

    except Exception as e:
        result_out["agent_error"] = str(e)
        result_out["solve_time"] = time.time() - t0
        logger.error("[%s] Error: %s", task_name, traceback.format_exc())

    # Save per-task log
    task_log = log_dir / task_name
    task_log.mkdir(parents=True, exist_ok=True)
    with open(task_log / "result.json", "w") as f:
        json.dump(result_out, f, indent=2, default=str)

    status = "PASS" if result_out["passed"] else "FAIL"
    logger.info("[%s] %s (reward=%.1f, steps=%d, cost=$%.3f, time=%.0fs)",
                task_name, status, result_out["reward"],
                result_out["n_steps"], result_out["total_cost"],
                result_out["solve_time"])

    return result_out


# ---------------------------------------------------------------------------
# Batch evolve loop
# ---------------------------------------------------------------------------

def evolve_batch(
    task_indices: list[int],
    env_name: str,
    workspace_dir: Path,
    solver_model: str,
    solver_provider: str,
    user_model: str,
    user_provider: str,
    bedrock_model_id: str,
    curator_model: str,
    region: str,
    batch_workers: int,
    max_skills_per_topic: int,
    max_general_skills: int,
    log_dir: Path,
    results_dir: Path,
    batch_label: str = "",
    no_evolve: bool = False,
    evolve_all: bool = False,
    selector_model: str = "",
    feedback_level: str = "standard",
    task_split: str = "test",
    temperature: float = 0.0,
) -> list[dict]:

    skills = load_skills(workspace_dir)
    logger.info("[%s] Solving %d tasks with %d workers (%d skills loaded)...",
                batch_label, len(task_indices), batch_workers, len(skills))

    results = []
    with ThreadPoolExecutor(max_workers=batch_workers) as executor:
        futures = {}
        for idx in task_indices:
            f = executor.submit(
                _solve_one_task,
                task_index=idx,
                env_name=env_name,
                solver_model=solver_model,
                solver_provider=solver_provider,
                user_model=user_model,
                user_provider=user_provider,
                bedrock_model_id=bedrock_model_id,
                region=region,
                skills=skills,
                workspace_dir=workspace_dir,
                log_dir=log_dir,
                feedback_level=feedback_level,
                do_propose=not no_evolve,
                evolve_all=evolve_all,
                selector_model=selector_model,
                task_split=task_split,
                temperature=temperature,
            )
            futures[f] = idx

        for f in as_completed(futures):
            try:
                results.append(f.result())
            except Exception as e:
                idx = futures[f]
                logger.error("[%s] Task %d failed: %s", batch_label, idx, e)
                results.append({
                    "task_index": idx,
                    "task_name": f"{env_name}_task_{idx}",
                    "env_name": env_name,
                    "passed": False,
                    "reward": 0.0,
                    "agent_error": str(e),
                })

    passed_count = sum(1 for r in results if r.get("passed"))
    total = len(results)
    logger.info("[%s] Batch done: %d/%d passed (%.1f%%)",
                batch_label, passed_count, total, 100.0 * passed_count / max(total, 1))

    # Save batch results
    obs_dir = workspace_dir / "evolution" / "observations"
    obs_dir.mkdir(parents=True, exist_ok=True)
    with _write_lock:
        with open(obs_dir / f"{batch_label}.jsonl", "w") as f:
            for r in results:
                f.write(json.dumps(r, default=str) + "\n")

    if no_evolve:
        return results

    # Gather proposals and failed summaries
    proposals = [r["proposal"] for r in results if r.get("proposal")]
    failed_summaries = []
    for r in results:
        if not r.get("passed"):
            failed_summaries.append({
                "task_name": r["task_name"],
                "env_name": r.get("env_name", env_name),
                "trajectory_signals": r.get("trajectory_signals", {}),
                "compressed_trajectory": r.get("compressed_trajectory", ""),
                "feedback_analysis": r.get("feedback_analysis", ""),
                "proposal_summary": (r["proposal"]["name"] + ": " + r["proposal"]["description"])
                    if r.get("proposal") else "",
            })

    # Topic curation
    if proposals:
        by_topic = defaultdict(list)
        for p in proposals:
            by_topic[p["topic"]].append(p)

        logger.info("[%s] Curating %d proposals across %d topics...",
                    batch_label, len(proposals), len(by_topic))

        for topic, tproposals in by_topic.items():
            stats = _curate_topic_proposals(
                topic, tproposals, workspace_dir, region,
                curator_model, max_skills_per_topic, env_name=env_name)
            logger.info("[%s]   topic=%s: +%d merged=%d skipped=%d",
                        batch_label, topic, stats["added"], stats["merged"], stats["skipped"])

    # General curation
    if len(failed_summaries) >= 2:
        gen_stats = _curate_general_skills(
            failed_summaries, workspace_dir, region,
            curator_model, max_general_skills, feedback_level, env_name=env_name)
        logger.info("[%s] General curation: +%d updated=%d deleted=%d",
                    batch_label, gen_stats["added"], gen_stats["updated"], gen_stats["deleted"])

    update_skill_tree(workspace_dir)
    new_skills = load_skills(workspace_dir)
    logger.info("[%s] Skills: %d total", batch_label, len(new_skills))

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(
        description="Tau-bench with A-EVOLVE propose+curator evolution loop")
    p.add_argument("--env", type=str, default="retail",
                   choices=["retail", "airline", "both"],
                   help="Tau-bench environment (default: retail). 'both' runs airline then retail with shared skills.")
    p.add_argument("--task-split", type=str, default="test",
                   choices=["test", "train", "dev"],
                   help="Task split (default: test)")
    p.add_argument("--solver-model", type=str, default="2",
                   help="1=Opus4.6, 2=Sonnet4.5, 3=Opus4.5")
    p.add_argument("--user-model", type=str, default="2",
                   help="Model for user simulator (default: same as solver)")
    p.add_argument("--curator-model", type=str, default="2")
    p.add_argument("--selector-model", type=str, default="2")
    p.add_argument("--region", type=str, default="us-west-2")
    p.add_argument("--batch-size", type=int, default=10)
    p.add_argument("--workers", type=int, default=5)
    p.add_argument("--max-skills-per-topic", type=int, default=5)
    p.add_argument("--max-general-skills", type=int, default=10)
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--feedback-level", type=str, default="standard",
                   choices=["minimal", "standard", "full"])
    p.add_argument("--limit", type=int, default=None,
                   help="Max number of tasks to run")
    p.add_argument("--shuffle", action="store_true")
    p.add_argument("--shuffle-seed", type=int, default=42)
    p.add_argument("--no-evolve", action="store_true",
                   help="Baseline mode: solve only, no skill evolution")
    p.add_argument("--evolve-all", action="store_true",
                   help="Propose for ALL tasks, not just failures")
    p.add_argument("--output-dir", type=str,
                   default="outputs/tau_bench_evolve")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )

    # Resolve model IDs
    solver_litellm = MODEL_MAP.get(args.solver_model, args.solver_model)
    user_litellm = MODEL_MAP.get(args.user_model, args.user_model)
    bedrock_model = MODEL_MAP.get(args.solver_model, args.solver_model)
    curator_model = MODEL_MAP.get(args.curator_model, args.curator_model)
    selector_model = MODEL_MAP.get(args.selector_model, args.selector_model)

    envs_to_run = ["airline", "retail"] if args.env == "both" else [args.env]

    logger.info("Environment: %s (%s split)", args.env, args.task_split)
    logger.info("Solver: %s, User sim: %s, Curator: %s",
                solver_litellm, user_litellm, curator_model)
    logger.info("Feedback level: %s, Evolve: %s",
                args.feedback_level, "OFF" if args.no_evolve else "ON")

    # Setup workspace (shared across envs)
    output_dir = Path(args.output_dir)
    workspace_dir = output_dir / "workspace"
    log_dir = output_dir / "logs"
    results_dir = output_dir / "results"
    for d in [workspace_dir / "skills" / "topic",
              workspace_dir / "skills" / "general",
              workspace_dir / "evolution" / "observations",
              log_dir, results_dir]:
        d.mkdir(parents=True, exist_ok=True)

    all_results = []
    t_start = time.time()
    global_batch_idx = 0

    for env_name in envs_to_run:
        from tau_bench.envs import get_env
        temp_env = get_env(
            env_name,
            user_strategy="llm",
            user_model=user_litellm,
            user_provider="bedrock",
            task_split=args.task_split,
        )
        n_tasks = len(temp_env.tasks)
        task_indices = list(range(n_tasks))

        if args.shuffle:
            random.seed(args.shuffle_seed)
            random.shuffle(task_indices)
        if args.limit:
            task_indices = task_indices[:args.limit]

        logger.info("[%s] Tasks: %d total, %d selected", env_name, n_tasks, len(task_indices))

        for batch_offset in range(0, len(task_indices), args.batch_size):
            batch = task_indices[batch_offset:batch_offset + args.batch_size]
            batch_label = f"{env_name}_batch_{global_batch_idx:04d}"
            global_batch_idx += 1

            results = evolve_batch(
                task_indices=batch,
                env_name=env_name,
                workspace_dir=workspace_dir,
                solver_model=solver_litellm,
                solver_provider="bedrock",
                user_model=user_litellm,
                user_provider="bedrock",
                bedrock_model_id=bedrock_model,
                curator_model=curator_model,
                region=args.region,
                batch_workers=args.workers,
                max_skills_per_topic=args.max_skills_per_topic,
                max_general_skills=args.max_general_skills,
                log_dir=log_dir,
                results_dir=results_dir,
                batch_label=batch_label,
                no_evolve=args.no_evolve,
                evolve_all=args.evolve_all,
                selector_model=selector_model if not args.no_evolve else "",
                feedback_level=args.feedback_level,
                task_split=args.task_split,
                temperature=args.temperature,
            )
            all_results.extend(results)

            passed = sum(1 for r in all_results if r.get("passed"))
            total = len(all_results)
            logger.info("Progress: batch %d, %d/%d passed (%.1f%%)",
                        global_batch_idx, passed, total,
                        100.0 * passed / max(total, 1))

    elapsed = time.time() - t_start
    passed = sum(1 for r in all_results if r.get("passed"))
    total = len(all_results)

    logger.info("=" * 60)
    logger.info("FINAL: %d/%d passed (%.1f%%) in %.0fs",
                passed, total, 100.0 * passed / max(total, 1), elapsed)

    # Per-env breakdown
    for env_name in envs_to_run:
        env_results = [r for r in all_results if r.get("env_name") == env_name]
        env_passed = sum(1 for r in env_results if r.get("passed"))
        if env_results:
            logger.info("  %s: %d/%d (%.1f%%)", env_name, env_passed,
                        len(env_results), 100.0 * env_passed / len(env_results))

    # Save all_results.jsonl
    with open(output_dir / "all_results.jsonl", "w") as f:
        for r in all_results:
            f.write(json.dumps(r, default=str) + "\n")

    # Save summary.json
    summary = {
        "timestamp": datetime.now().isoformat(),
        "env": args.env,
        "envs_run": envs_to_run,
        "task_split": args.task_split,
        "solver_model": solver_litellm,
        "user_model": user_litellm,
        "curator_model": curator_model,
        "feedback_level": args.feedback_level,
        "total": total,
        "passed": passed,
        "rate": passed / max(total, 1),
        "elapsed": elapsed,
    }
    for env_name in envs_to_run:
        env_results = [r for r in all_results if r.get("env_name") == env_name]
        env_passed = sum(1 for r in env_results if r.get("passed"))
        summary[f"{env_name}_total"] = len(env_results)
        summary[f"{env_name}_passed"] = env_passed
        summary[f"{env_name}_rate"] = env_passed / max(len(env_results), 1)
    with open(output_dir / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    # Copy final skills
    final_skills_dir = output_dir / "final_skills"
    if (workspace_dir / "skills").exists():
        if final_skills_dir.exists():
            shutil.rmtree(final_skills_dir)
        shutil.copytree(workspace_dir / "skills", final_skills_dir)

    logger.info("Results saved to %s", output_dir)


if __name__ == "__main__":
    main()
