#!/usr/bin/env python3
"""Evolve an agent on CL-bench using the standard A-EVOLVE loop.

Per-task cycle: solve → evaluate → observe → evolve → reload → next task.
Uses BaseAgent + AgentWorkspace + AEvolveEngine + Observer from the framework.

Skills accumulate in the workspace across tasks:
  workspace/skills/*/SKILL.md

Usage:
    conda run -n mem --no-capture-output python examples/evolve_cl_bench.py \\
        --max-samples 100 --max-evolve-turns 3 \\
        --output-dir outputs/cl_bench_evolve_v1
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["BYPASS_TOOL_CONSENT"] = "true"

from agent_evolve.benchmarks.cl_bench import (
    CLBenchBenchmark,
    _build_rubrics_text,
    _call_bedrock,
    _call_bedrock_converse,
    _convert_openai_messages_to_bedrock,
    _get_client,
    _init_worker,
    _parse_json_object,
    _truncate,
    MODEL_MAP,
)
from agent_evolve.config import EvolveConfig
from agent_evolve.contract.workspace import AgentWorkspace
from agent_evolve.engine.observer import Observer
from agent_evolve.protocol.base_agent import BaseAgent
from agent_evolve.types import Feedback, Observation, Task, Trajectory

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_SOLVER_MODEL = "1"    # Opus 4.6
DEFAULT_JUDGE_MODEL = "3"     # Opus 4.5
DEFAULT_EVOLVER_MODEL = "1"   # Opus 4.6

SEED_SYSTEM_PROMPT = ""

# ---------------------------------------------------------------------------
# Skill proposal prompt (solver reflects after failure)
# ---------------------------------------------------------------------------

PROPOSE_SKILL_PROMPT = """\
Your answer had issues. Here is the user's feedback:

{feedback}

{existing_skills_section}

Based on this feedback, write a SHORT, actionable tip for future questions about this same document.

ACTION: NEW / ENHANCE / NONE
TARGET: existing_skill_name (only for ENHANCE)
NAME: short-kebab-name (only for NEW)
DESCRIPTION: one sentence, under 100 chars
CONTENT:
## Key points
- (specific bullet points referencing exact terms/rules/numbers from the document)
## Gotchas
- (specific pitfalls to avoid, based on what the feedback flagged)

Rules:
- Bullet points, not paragraphs. CONTENT must be under 200 words.
- Reference exact terms, numbers, sections from the document — you just read it
- Focus ONLY on what the feedback flagged — don't write general advice
- Prefer ENHANCE over NEW if an existing skill is related
- If nothing useful, output ACTION: NONE"""

# ---------------------------------------------------------------------------
# Curator prompt (reviews proposals per context)
# ---------------------------------------------------------------------------

CURATOR_PROMPT = """\
You are a skill curator for a Q&A agent. You review skill proposals and decide \
which to keep in the skill library for a specific context document.

## Current Skill Library for this context ({n_skills}/{max_skills} slots used):
{existing_skills_list}

## Proposals from this batch:
{proposals_list}

For each proposal, output ONE of:

ACCEPT: <proposal_name>
(skill is added as-is)

MERGE: <proposal_name> INTO <existing_skill_name>
NEW_CONTENT:
(merged content combining both, under 500 words)

SKIP: <proposal_name>
REASON: <brief reason>

Decision criteria:
- HIGH confidence → lean ACCEPT
- LOW confidence → lean SKIP
- Overlaps existing → MERGE (preferred over ACCEPT)
- Budget full ({n_skills}/{max_skills}) → can only ENHANCE/MERGE existing, or SKIP
- Keep skills focused: one skill = one specific pattern/rule set
- Few broad skills better than many narrow ones

If no proposals, output: NO_PROPOSALS"""


# ---------------------------------------------------------------------------
# General curator prompt (cross-context failure pattern analysis)
# ---------------------------------------------------------------------------

GENERAL_CURATOR_PROMPT = """\
You are a meta-learning curator. You analyze failure patterns ACROSS contexts \
to distill general skills that help the agent on ANY task.

## Failed Task Analysis ({n_failed} failed tasks this batch):
{failed_summaries}

## Current General Skill Library ({n_general}/{max_general} slots used):
{general_skills_list}

## Your Job:
1. **Analyze failure patterns**: Look for REPEATED failure types across different contexts.
   - What types of issues appear across 3+ different contexts?
   - Are there systematic mistakes? (e.g., always missing multi-part questions, wrong tone, etc.)
   - Focus on the feedback analysis and solver proposals — they show what went wrong.

2. **Propose or update general skills**: Only for patterns that are NOT context-specific.
   - A general skill should help on tasks the agent hasn't seen before.
   - Do NOT create skills for context-specific knowledge (that's what context skills are for).
   - MERGE into existing general skills when the pattern overlaps.

Output your decisions:

For new skills:
NEW_GENERAL: <kebab-name>
DESCRIPTION: <one line, under 100 chars>
CONTENT:
## Pattern
- (what failure pattern this addresses, one line)
## Strategy
- (specific actionable bullet points, 3-5 max)
(Keep CONTENT under 200 words — bullet points only, no paragraphs)

For updating existing skills:
UPDATE_GENERAL: <existing-skill-name>
NEW_CONTENT:
(updated content, under 200 words, bullet points only)

For removing stale skills:
DELETE_GENERAL: <existing-skill-name>
REASON: <why>

If no general patterns found:
NO_PATTERNS

Rules:
- Maximum {max_general} general skills total. Quality over quantity.
- Each skill must address a pattern seen in 3+ different contexts.
- Be SPECIFIC and ACTIONABLE — not generic advice like "read carefully".
- Keep each skill SHORT: description < 100 chars, content < 200 words, bullet points only.
- Reference the actual failure patterns you observed.
- Prefer UPDATE over NEW if an existing skill is related."""


# ---------------------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------------------

def _retry(fn, *args, _max_retries: int = 5, _label: str = "", **kwargs):
    """Retry a function with exponential backoff (matches _call_bedrock pattern)."""
    for attempt in range(_max_retries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            err = str(e)
            base = 30 if "too many tokens" in err.lower() else (
                4 if "throttl" in err.lower() else 2
            )
            delay = base * (2 ** attempt)
            if attempt < _max_retries - 1:
                logger.warning(
                    "Retry %s attempt %d/%d: %s — waiting %ds",
                    _label, attempt + 1, _max_retries, err[:120], delay,
                )
                time.sleep(delay)
            else:
                logger.error("Retry %s exhausted after %d attempts: %s", _label, _max_retries, err[:200])
                raise


# ---------------------------------------------------------------------------
# Skill proposal + curation functions
# ---------------------------------------------------------------------------

def _propose_in_context(
    task: "Task",
    conv_state: dict,
    feedback_detail: str,
    existing_context_skills: list[tuple[str, str]],  # [(name, description), ...]
    region: str,
) -> dict | None:
    """Propose a skill in solver conversation context.

    Continues the solver's conversation (same context document + reasoning),
    asks the LLM to propose a skill based on the feedback.

    Returns proposal dict or None.
    """
    if not conv_state or not feedback_detail or "FAIL" not in feedback_detail:
        return None

    # Extract just the user feedback part
    feedback_text = feedback_detail
    if "User feedback:" in feedback_detail:
        feedback_text = feedback_detail.split("User feedback:", 1)[1].strip()
    if not feedback_text or len(feedback_text) < 20:
        return None

    client = conv_state["client"]
    model_id = conv_state["model_id"]
    system_prompts = conv_state["system_prompts"]
    messages = list(conv_state["messages"])  # copy to avoid mutation

    # Build existing skills section
    if existing_context_skills:
        skills_lines = "\n".join(
            f"- **{name}**: {desc}" for name, desc in existing_context_skills
        )
        existing_section = f"Current skills for this context:\n{skills_lines}"
    else:
        existing_section = "No existing skills for this context yet."

    prompt = PROPOSE_SKILL_PROMPT.format(
        feedback=feedback_text,
        existing_skills_section=existing_section,
    )
    messages.append({"role": "user", "content": [{"text": prompt}]})

    resp, err = _call_bedrock_converse(
        client, model_id, system_prompts, messages,
        max_tokens=1024, temperature=0.3,
    )
    if err or not resp:
        logger.debug("Propose failed for %s: %s", task.id[:8], err)
        return None

    return _parse_proposal(resp, task)


def _parse_proposal(resp: str, task: "Task") -> dict | None:
    """Parse a skill proposal response into a structured dict."""
    if "ACTION: NONE" in resp.upper():
        return None

    meta = task.metadata
    proposal = {
        "source_task": task.id,
        "context_id": meta.get("context_id", ""),
        "raw": resp,
        "confidence": "MEDIUM",
        "action": "NEW",
        "target": "",
        "name": "",
        "description": "",
        "content": "",
    }

    for line in resp.split("\n"):
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("CONFIDENCE:"):
            proposal["confidence"] = stripped.split(":", 1)[1].strip().upper()
        elif upper.startswith("ACTION:"):
            proposal["action"] = stripped.split(":", 1)[1].strip().upper()
        elif upper.startswith("TARGET:"):
            proposal["target"] = stripped.split(":", 1)[1].strip()
        elif upper.startswith("NAME:"):
            raw_name = stripped.split(":", 1)[1].strip()
            proposal["name"] = re.sub(r"[^a-z0-9-]", "-", raw_name.lower()).strip("-")
        elif upper.startswith("DESCRIPTION:"):
            proposal["description"] = stripped.split(":", 1)[1].strip()[:150]

    # Extract CONTENT block
    content_marker = "CONTENT:"
    idx = resp.upper().find(content_marker)
    if idx >= 0:
        proposal["content"] = resp[idx + len(content_marker):].strip()

    # For ENHANCE, name = target
    if proposal["action"] == "ENHANCE" and proposal["target"] and not proposal["name"]:
        proposal["name"] = proposal["target"]

    if not proposal["name"] and proposal["action"] != "NONE":
        proposal["name"] = f"skill-{task.id[:8]}"

    if not proposal["content"]:
        return None

    logger.debug(
        "Proposal from %s: %s %s (confidence=%s)",
        task.id[:8], proposal["action"], proposal["name"], proposal["confidence"],
    )
    return proposal


def _curate_context_proposals(
    context_id: str,
    proposals: list[dict],
    existing_skills: list[tuple[str, str, str]],  # [(name, description, path), ...]
    workspace_dir: Path,
    region: str,
    model: str = "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    max_skills: int = 5,
) -> dict:
    """Curator reviews proposals for one context. Returns {added, merged, skipped}."""
    _init_worker(region)

    if not proposals:
        return {"added": 0, "merged": 0, "skipped": 0}

    n_skills = len(existing_skills)

    # Build existing skills list
    if existing_skills:
        existing_list = "\n".join(
            f"- **{name}**: {desc}" for name, desc, _ in existing_skills
        )
    else:
        existing_list = "(empty)"

    # Build proposals list
    proposals_lines = []
    for p in proposals:
        action = p.get("action", "NEW")
        name = p.get("name", "unknown")
        conf = p.get("confidence", "MEDIUM")
        desc = p.get("description", "")[:150]
        content_preview = _truncate(p.get("content", ""), 300)
        target_line = f"\n  Target: {p['target']}" if p.get("target") else ""
        proposals_lines.append(
            f"### [{action}] {name} (confidence: {conf})\n"
            f"  Source task: {p['source_task'][:8]}{target_line}\n"
            f"  Description: {desc}\n"
            f"  Content: {content_preview}"
        )

    prompt = CURATOR_PROMPT.format(
        n_skills=n_skills,
        max_skills=max_skills,
        existing_skills_list=existing_list,
        proposals_list="\n\n".join(proposals_lines),
    )

    client = _get_client(region)
    resp, err = _call_bedrock(
        client, model, prompt, "Review the proposals above and make your decisions.",
        max_tokens=2048, temperature=0.0,
    )
    if err or not resp:
        logger.warning("Curator failed for context %s: %s", context_id[:8], err)
        return {"added": 0, "merged": 0, "skipped": 0}

    # Execute curation decisions
    return _execute_curation(resp, proposals, existing_skills, workspace_dir, context_id, max_skills)


def _execute_curation(
    decisions_text: str,
    proposals: list[dict],
    existing_skills: list[tuple[str, str, str]],
    workspace_dir: Path,
    context_id: str,
    max_skills: int = 5,
) -> dict:
    """Parse curator decisions and write skills to workspace."""
    proposal_map = {p["name"]: p for p in proposals}
    existing_names = {name for name, _, _ in existing_skills}
    current_count = len(existing_skills)
    stats = {"added": 0, "merged": 0, "skipped": 0}

    def _fuzzy_match_proposal(raw_name: str) -> str | None:
        clean = re.sub(r"[^a-z0-9-]", "-", raw_name.lower()).strip("-")
        if clean in proposal_map:
            return clean
        for pname in proposal_map:
            if clean in pname or pname in clean:
                return pname
        return None

    def _fuzzy_match_existing(raw_name: str) -> str | None:
        clean = re.sub(r"[^a-z0-9-]", "-", raw_name.lower()).strip("-")
        if clean in existing_names:
            return clean
        for ename in existing_names:
            if clean in ename or ename in clean:
                return ename
        return None

    def _write_skill(name: str, description: str, content: str):
        skill_dir = workspace_dir / "skills" / "context" / context_id / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_md = f"---\nname: {name}\ndescription: {description}\n---\n\n{content}"
        (skill_dir / "SKILL.md").write_text(skill_md)

    for line in decisions_text.split("\n"):
        stripped = line.strip()
        upper = stripped.upper()

        if upper.startswith("ACCEPT:"):
            raw_name = stripped.split(":", 1)[1].strip()
            pname = _fuzzy_match_proposal(raw_name)
            if pname and pname not in existing_names:
                if current_count < max_skills:
                    p = proposal_map[pname]
                    _write_skill(pname, p.get("description", ""), p.get("content", ""))
                    existing_names.add(pname)
                    current_count += 1
                    stats["added"] += 1
                    logger.info("Curator ACCEPT: %s for context %s", pname, context_id[:8])
                else:
                    logger.info("Curator ACCEPT %s skipped — budget full (%d/%d)", pname, current_count, max_skills)
                    stats["skipped"] += 1

        elif upper.startswith("MERGE:"):
            parts = stripped.split(":", 1)[1].strip()
            if " INTO " in parts.upper():
                raw_prop, raw_target = parts.upper().split(" INTO ", 1)
                # Use original case for matching
                parts_split = parts.split(" INTO " if " INTO " in parts else " into ")
                raw_prop_orig = parts_split[0].strip() if len(parts_split) > 1 else ""
                raw_target_orig = parts_split[1].strip() if len(parts_split) > 1 else ""

                pname = _fuzzy_match_proposal(raw_prop_orig)
                tname = _fuzzy_match_existing(raw_target_orig)

                if pname and tname:
                    # Look for NEW_CONTENT in subsequent text
                    merge_idx = decisions_text.find(stripped)
                    after = decisions_text[merge_idx + len(stripped):]
                    if "NEW_CONTENT:" in after:
                        new_content = after.split("NEW_CONTENT:", 1)[1]
                        # Cut at next decision marker
                        for marker in ["ACCEPT:", "MERGE:", "SKIP:", "NO_PROPOSALS"]:
                            if marker in new_content:
                                new_content = new_content[:new_content.index(marker)]
                        new_content = new_content.strip()
                        if new_content:
                            # Find existing description
                            old_desc = ""
                            for ename, edesc, _ in existing_skills:
                                if ename == tname:
                                    old_desc = edesc
                                    break
                            _write_skill(tname, old_desc or proposal_map.get(pname, {}).get("description", ""), new_content)
                            stats["merged"] += 1
                            logger.info("Curator MERGE: %s into %s for context %s", pname, tname, context_id[:8])
                    else:
                        # No NEW_CONTENT, use proposal content directly
                        p = proposal_map[pname]
                        old_desc = ""
                        for ename, edesc, _ in existing_skills:
                            if ename == tname:
                                old_desc = edesc
                                break
                        _write_skill(tname, old_desc or p.get("description", ""), p.get("content", ""))
                        stats["merged"] += 1

        elif upper.startswith("SKIP:"):
            stats["skipped"] += 1
            logger.debug("Curator SKIP: %s", stripped.split(":", 1)[1].strip()[:50])

    return stats


def _curate_general_skills(
    failed_summaries: list[dict],
    workspace_dir: Path,
    region: str,
    model: str = "us.anthropic.claude-opus-4-6-v1",
    max_general: int = 10,
) -> dict:
    """Analyze cross-context failure patterns and create/update general skills.

    failed_summaries: list of {task_id, context_id, category, feedback_analysis, proposal_summary}
    General curator sees feedback analysis + solver proposals, NOT original rubrics.
    Returns {added, updated, deleted}.
    """
    _init_worker(region)

    if not failed_summaries:
        return {"added": 0, "updated": 0, "deleted": 0}

    # Build failed task summary text from analysis + proposals
    summary_lines = []
    for s in failed_summaries[:30]:  # cap to avoid prompt overflow
        parts = [
            f"### Task {s['task_id'][:8]} [{s.get('category', '')}]",
            f"Context: {s.get('context_id', '')[:8]}",
        ]
        if s.get("feedback_detail"):
            parts.append(f"Feedback: {_truncate(s['feedback_detail'], 300)}")
        if s.get("proposal_summary"):
            parts.append(f"Solver proposal: {_truncate(s['proposal_summary'], 200)}")
        summary_lines.append("\n".join(parts) + "\n")

    # Build existing general skills list
    general_dir = workspace_dir / "skills" / "general"
    existing_general = []
    if general_dir.exists():
        for skill_file in sorted(general_dir.rglob("SKILL.md")):
            content = skill_file.read_text()
            s_name = skill_file.parent.name
            s_desc = ""
            for sline in content.split("\n"):
                if sline.strip().startswith("description:"):
                    s_desc = sline.split(":", 1)[1].strip()
                    break
            # Get body (strip frontmatter)
            body = content
            if content.startswith("---"):
                end = content.find("---", 3)
                if end != -1:
                    body = content[end + 3:].strip()
            existing_general.append((s_name, s_desc, body[:300]))

    if existing_general:
        general_list = "\n".join(
            f"- **{name}**: {desc}\n  Preview: {body[:150]}" for name, desc, body in existing_general
        )
    else:
        general_list = "(empty)"

    prompt = GENERAL_CURATOR_PROMPT.format(
        n_failed=len(failed_summaries),
        failed_summaries="\n\n".join(summary_lines),
        n_general=len(existing_general),
        max_general=max_general,
        general_skills_list=general_list,
    )

    client = _get_client(region)
    resp, err = _call_bedrock(
        client, model, prompt,
        "Analyze the failure patterns and make your decisions.",
        max_tokens=4096, temperature=0.0,
    )
    if err or not resp:
        logger.warning("General curator failed: %s", err)
        return {"added": 0, "updated": 0, "deleted": 0}

    # Parse and execute decisions
    return _execute_general_curation(resp, workspace_dir, existing_general, max_general)


def _execute_general_curation(
    decisions_text: str,
    workspace_dir: Path,
    existing_general: list[tuple[str, str, str]],
    max_general: int = 10,
) -> dict:
    """Parse general curator decisions and write/update/delete general skills."""
    existing_names = {name for name, _, _ in existing_general}
    current_count = len(existing_general)
    stats = {"added": 0, "updated": 0, "deleted": 0}

    def _write_general_skill(name: str, description: str, content: str):
        skill_dir = workspace_dir / "skills" / "general" / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_md = f"---\nname: {name}\ndescription: {description}\n---\n\n{content}"
        (skill_dir / "SKILL.md").write_text(skill_md)

    # Split into decision blocks
    lines = decisions_text.split("\n")
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        upper = stripped.upper()

        if upper.startswith("NEW_GENERAL:"):
            name = re.sub(r"[^a-z0-9-]", "-", stripped.split(":", 1)[1].strip().lower()).strip("-")
            desc = ""
            content = ""
            i += 1
            while i < len(lines):
                s = lines[i].strip()
                if s.upper().startswith("DESCRIPTION:"):
                    desc = s.split(":", 1)[1].strip()[:150]
                elif s.upper().startswith("CONTENT:"):
                    # Collect everything until next decision marker
                    content_lines = []
                    i += 1
                    while i < len(lines):
                        su = lines[i].strip().upper()
                        if any(su.startswith(m) for m in ["NEW_GENERAL:", "UPDATE_GENERAL:", "DELETE_GENERAL:", "NO_PATTERNS"]):
                            break
                        content_lines.append(lines[i])
                        i += 1
                    content = "\n".join(content_lines).strip()
                    break
                i += 1

            if name and content and current_count < max_general:
                _write_general_skill(name, desc, content)
                existing_names.add(name)
                current_count += 1
                stats["added"] += 1
                logger.info("General curator NEW: %s", name)
            elif current_count >= max_general:
                logger.info("General curator NEW %s skipped — budget full (%d/%d)", name, current_count, max_general)
            continue

        elif upper.startswith("UPDATE_GENERAL:"):
            raw_name = stripped.split(":", 1)[1].strip()
            name = re.sub(r"[^a-z0-9-]", "-", raw_name.lower()).strip("-")
            # Fuzzy match
            matched = None
            for ename in existing_names:
                if name == ename or name in ename or ename in name:
                    matched = ename
                    break
            content = ""
            i += 1
            while i < len(lines):
                s = lines[i].strip()
                if s.upper().startswith("NEW_CONTENT:") or s.upper().startswith("CONTENT:"):
                    content_lines = []
                    i += 1
                    while i < len(lines):
                        su = lines[i].strip().upper()
                        if any(su.startswith(m) for m in ["NEW_GENERAL:", "UPDATE_GENERAL:", "DELETE_GENERAL:", "NO_PATTERNS"]):
                            break
                        content_lines.append(lines[i])
                        i += 1
                    content = "\n".join(content_lines).strip()
                    break
                i += 1

            if matched and content:
                # Preserve existing description if not provided
                old_desc = ""
                for ename, edesc, _ in existing_general:
                    if ename == matched:
                        old_desc = edesc
                        break
                _write_general_skill(matched, old_desc, content)
                stats["updated"] += 1
                logger.info("General curator UPDATE: %s", matched)
            continue

        elif upper.startswith("DELETE_GENERAL:"):
            raw_name = stripped.split(":", 1)[1].strip()
            name = re.sub(r"[^a-z0-9-]", "-", raw_name.lower()).strip("-")
            matched = None
            for ename in existing_names:
                if name == ename or name in ename or ename in name:
                    matched = ename
                    break
            if matched:
                skill_dir = workspace_dir / "skills" / "general" / matched
                if skill_dir.exists():
                    shutil.rmtree(skill_dir)
                    existing_names.discard(matched)
                    current_count -= 1
                    stats["deleted"] += 1
                    logger.info("General curator DELETE: %s", matched)

        i += 1

    return stats


# ---------------------------------------------------------------------------
# CLBenchAgent — BaseAgent subclass for CL-bench
# ---------------------------------------------------------------------------

SKILL_SELECT_PROMPT = (
    "You are selecting which skills are relevant for a specific task.\n\n"
    "You will receive the task info and a skill tree. Each skill entry looks like:\n"
    '  - **skill-name** (`skills/general/skill-dir`): one-sentence description\n\n'
    "Be SELECTIVE — injecting irrelevant skills wastes context and can hurt performance.\n"
    "Only select skills whose description clearly relates to this task's domain or requirements.\n"
    "Select FEWER skills (0-5) rather than many. When in doubt, select NONE — no skills is better than bad skills.\n\n"
    "Output a JSON block with the skill PATHS (the part in backticks):\n"
    '```json\n{"selected": ["skills/general/preserve-exact-wording", "skills/general/format-compliance"], "reason": "brief explanation"}\n```\n\n'
    "Use the EXACT path from the backticks in the skill tree. Empty list [] is fine if none match."
)


class CLBenchAgent(BaseAgent):
    """Agent for CL-bench tasks.

    Supports hierarchical skill trees and LLM-based skill selection.
    """

    def __init__(
        self,
        workspace_dir: str | Path,
        bench: CLBenchBenchmark,
        selector_model: str = "us.anthropic.claude-opus-4-5-20251101-v1:0",
        no_general_skills: bool = False,
    ):
        self.bench = bench
        self.selector_model = selector_model
        self.no_general_skills = no_general_skills
        super().__init__(workspace_dir)

    def _select_skills(self, task: Task) -> list:
        """Use LLM + SKILL_TREE.md to select relevant skills by name."""
        if not self.skills:
            return []
        if len(self.skills) <= 2:
            return list(self.skills)

        # Build skill list from current self.skills (already filtered to general only by caller)
        skill_tree = "\n".join(
            f"- **{s.name}** (`{s.path}`): {s.description}" for s in self.skills
        )

        meta = task.metadata
        user_text = (
            f"Task category: {meta.get('context_category', '')} / {meta.get('sub_category', '')}\n"
            f"Task question: {meta.get('task_text', '')}\n"
            f"Context preview: {_truncate(meta.get('context', ''), 400)}\n\n"
            f"Skill Tree:\n{skill_tree}"
        )

        client = _get_client(self.bench.region)
        resp, err = _call_bedrock(
            client, self.selector_model, SKILL_SELECT_PROMPT, user_text,
            max_tokens=1024, temperature=0.0,
        )
        if err:
            logger.warning("Skill selection LLM failed: %s — returning empty", err)
            return []

        # Build name → skill lookup (multiple keys for fuzzy matching)
        def _normalize(s: str) -> str:
            """Normalize skill name for fuzzy matching: lowercase, hyphens/underscores → spaces."""
            return s.lower().replace("-", " ").replace("_", " ").strip()

        skill_lookup: dict[str, object] = {}
        for s in self.skills:
            skill_lookup[s.name] = s                          # exact name
            skill_lookup[_normalize(s.name)] = s              # normalized name
            skill_lookup[s.path] = s                          # full path: "skills/x/y"
            skill_lookup[s.path.removeprefix("skills/")] = s  # relative: "x/y"
            leaf = Path(s.path).name                          # leaf dir: "y"
            skill_lookup[leaf] = s
            skill_lookup[_normalize(leaf)] = s

        def _resolve(n: str):
            # Exact / normalized lookup
            hit = skill_lookup.get(n) or skill_lookup.get(_normalize(n)) or skill_lookup.get(f"skills/{n}")
            if hit:
                return hit
            # Substring fallback: if normalized query is contained in a skill name (or vice versa)
            nn = _normalize(n)
            for s in self.skills:
                sn = _normalize(s.name)
                if nn in sn or sn in nn:
                    return s
            return None

        parsed = _parse_json_object(resp)
        if parsed:
            # Accept various key names
            names = (
                parsed.get("selected")
                or parsed.get("skills")
                or parsed.get("skill_names")
                or parsed.get("names")
            )
            # Normalize to list: single string → [string], None → []
            if names is None:
                names = []
            elif isinstance(names, str):
                names = [names]
            elif not isinstance(names, list):
                logger.warning(
                    "Skill selection: 'selected' is unexpected type=%s, parsed=%s — returning empty",
                    type(names).__name__, _truncate(str(parsed), 300),
                )
                return []

            if not names:
                logger.debug("Task %s: LLM selected 0 skills — returning empty", task.id)
                return []

            selected = []
            for n in names:
                if not isinstance(n, str):
                    continue
                s = _resolve(n)
                if s and s not in selected:
                    selected.append(s)
            if selected:
                logger.debug("Task %s: selected %d skills: %s", task.id, len(selected), [s.name for s in selected])
                return selected
            # Names parsed but none matched existing skills
            logger.warning(
                "Skill selection: parsed names %s but none matched existing skills %s — returning empty",
                names, [s.name for s in self.skills],
            )
            return []

        # Fallback: try to extract skill names from raw text
        found = [s for s in self.skills if s.name in (resp or "")]
        if found:
            logger.debug("Task %s: extracted %d skills from raw text", task.id, len(found))
            return found

        logger.warning(
            "Skill selection parse failed: could not extract JSON or skill names (resp=%s) — returning empty",
            _truncate(resp, 300),
        )
        return []

    def solve(self, task: Task) -> Trajectory:
        """Solve a CL-bench task. Wrapper around solve_raw."""
        traj, _ = self.solve_raw(task)
        return traj

    def solve_raw(self, task: Task) -> tuple["Trajectory", dict]:
        """Solve and return (Trajectory, conversation_state).

        conversation_state contains system_prompts, messages (with assistant
        response appended), client, and model_id — enough to continue the
        conversation for in-context skill proposal.
        """
        client = _get_client(self.bench.region)
        meta = task.metadata
        task_id = task.id
        context_id = meta.get("context_id", "")

        system_prompt = self._build_system_prompt(task)

        raw_messages = self.bench._get_raw_messages(task_id)
        if raw_messages:
            system_prompts, bedrock_messages = _convert_openai_messages_to_bedrock(
                raw_messages,
                extra_system_text=system_prompt if system_prompt else None,
            )
        else:
            context = meta.get("context", "")
            task_text = meta.get("task_text", "")
            user_text = f"Context:\n{context}\n\nTask:\n{task_text}"
            system_prompts = [{"text": system_prompt}] if system_prompt else []
            bedrock_messages = [{"role": "user", "content": [{"text": user_text}]}]

        answer, err = _call_bedrock_converse(
            client, self.bench.model_id, system_prompts, bedrock_messages,
            max_tokens=self.bench.max_tokens, temperature=self.bench.temperature,
        )

        if err:
            logger.warning("Inference failed for %s: %s", task_id, err)
            traj = Trajectory(task_id=task_id, output=f"[ERROR] {err}")
            return traj, {}

        answer_text = (answer or "").strip()

        # Build conversation state: append assistant response to messages
        conv_messages = list(bedrock_messages)
        conv_messages.append({"role": "assistant", "content": [{"text": answer_text}]})

        state = {
            "system_prompts": system_prompts,
            "messages": conv_messages,
            "client": client,
            "model_id": self.bench.model_id,
        }
        return Trajectory(task_id=task_id, output=answer_text), state

    def _read_skill_body(self, skill) -> str:
        """Read SKILL.md body (strip frontmatter) for a SkillMeta."""
        skill_file = self.workspace.root / skill.path / "SKILL.md"
        if not skill_file.exists():
            return ""
        content = skill_file.read_text().strip()
        if content.startswith("---"):
            end = content.find("---", 3)
            if end != -1:
                return content[end + 3:].strip()
        return content

    def _build_system_prompt(self, task: Task) -> str:
        """Assemble system prompt with full skill content injected.

        Context-specific and general skills are injected with their full body.
        """
        parts = [self.system_prompt]
        context_id = task.metadata.get("context_id", "")

        ctx_skills = [s for s in self.skills if f"context/{context_id}" in s.path]
        gen_skills = [s for s in self.skills if "general/" in s.path]

        if ctx_skills:
            parts.append("\n\n## Lessons learned for this context")
            for skill in ctx_skills:
                body = self._read_skill_body(skill)
                parts.append(f"\n### {skill.name}\n{body}" if body else f"\n### {skill.name}\n{skill.description}")

        if gen_skills:
            parts.append("\n\n## General strategies")
            for skill in gen_skills:
                parts.append(f"\n- **{skill.name}**: {skill.description}")

        if ctx_skills or gen_skills:
            logger.debug(
                "Task %s [%s]: %d context + %d general skills injected",
                task.id, context_id[:8], len(ctx_skills), len(gen_skills),
            )

        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Workspace setup
# ---------------------------------------------------------------------------

def setup_workspace(workspace_dir: Path) -> None:
    """Initialize a minimal CL-bench workspace if it doesn't exist."""
    if workspace_dir.exists():
        return
    workspace_dir.mkdir(parents=True, exist_ok=True)

    (workspace_dir / "prompts").mkdir(parents=True, exist_ok=True)
    (workspace_dir / "prompts" / "system.md").write_text(SEED_SYSTEM_PROMPT)

    # Skills dir (starts empty — tips are learned from feedback)
    (workspace_dir / "skills").mkdir(parents=True, exist_ok=True)

    (workspace_dir / "memory").mkdir(parents=True, exist_ok=True)
    (workspace_dir / "evolution").mkdir(parents=True, exist_ok=True)
    logger.info("Created workspace at %s", workspace_dir)


# ---------------------------------------------------------------------------
# SKILL_TREE.md — skill index with descriptions and relationships
# ---------------------------------------------------------------------------

SKILL_TREE_PATH = "skills/SKILL_TREE.md"


def generate_skill_tree(workspace: AgentWorkspace) -> str:
    """Generate SKILL_TREE.md content from current skills as a hierarchical tree.

    Supports arbitrary nesting depth. Skills are organized by their directory
    structure under skills/, e.g.:
      skills/reasoning/legal-analysis/SKILL.md  → reasoning > legal-analysis
      skills/execution/simulation/SKILL.md      → execution > simulation
    """
    skills = workspace.list_skills()
    if not skills:
        return "# Skill Tree\n\nNo skills yet.\n"

    # Build a nested dict: {category: {subcategory: {... : [skills]}}}
    from collections import OrderedDict

    tree: dict = OrderedDict()
    for s in skills:
        # s.path is like "skills/reasoning/legal-analysis"
        parts = Path(s.path).parts  # ("skills", "reasoning", "legal-analysis")
        skill_parts = parts[1:]  # strip "skills/" prefix
        if len(skill_parts) <= 1:
            # Root-level skill (not nested under a category)
            tree.setdefault("_root_skills", []).append(s)
        else:
            # Navigate/create nested structure
            node = tree
            for part in skill_parts[:-1]:  # category levels
                node = node.setdefault(part, OrderedDict())
            node.setdefault("_skills", []).append(s)

    lines = ["# Skill Tree", ""]
    lines.append(f"Total skills: {len(skills)}")
    lines.append("")

    def _render_node(node: dict, depth: int = 0):
        """Recursively render tree nodes."""
        indent = "  " * depth
        # Render skills at this level
        for s in sorted(node.get("_skills", []), key=lambda x: x.name):
            lines.append(f"{indent}- **{s.name}** (`{s.path}`): {s.description}")
        # Render subcategories
        for key in sorted(k for k in node if k not in ("_skills", "_root_skills")):
            child = node[key]
            if isinstance(child, dict):
                # Count skills in this subtree
                count = _count_skills(child)
                lines.append(f"{indent}## {key}/ ({count} skills)" if depth == 0
                             else f"{indent}- **{key}/** ({count} skills)")
                _render_node(child, depth + 1)

    def _count_skills(node: dict) -> int:
        """Count total skills in a subtree."""
        count = len(node.get("_skills", []))
        for key in node:
            if key not in ("_skills", "_root_skills") and isinstance(node[key], dict):
                count += _count_skills(node[key])
        return count

    # Render root-level skills first
    root_skills = tree.get("_root_skills", [])
    if root_skills:
        for s in sorted(root_skills, key=lambda x: x.name):
            lines.append(f"- **{s.name}** (`{s.path}`): {s.description}")
        lines.append("")

    _render_node(tree)
    lines.append("")

    return "\n".join(lines)


def update_skill_tree(workspace: AgentWorkspace) -> None:
    """Regenerate and write SKILL_TREE.md."""
    content = generate_skill_tree(workspace)
    tree_path = workspace.root / SKILL_TREE_PATH
    tree_path.write_text(content)
    logger.debug("Updated SKILL_TREE.md: %d chars", len(content))


def read_skill_tree(workspace_dir: Path) -> str:
    """Read SKILL_TREE.md content, return empty string if not found."""
    tree_path = workspace_dir / SKILL_TREE_PATH
    if tree_path.exists():
        return tree_path.read_text()
    return ""


# ---------------------------------------------------------------------------
# Build feedback for evolver at different granularity levels
# ---------------------------------------------------------------------------

REPHRASE_FEEDBACK_PROMPT = """\
You are simulating a real user giving feedback on an AI assistant's response. \
The user asked a question and the AI's answer had some problems.

Given the list of issues below, write a natural user feedback message as if you \
are the user who is unsatisfied. You MUST address EVERY issue listed — do not \
skip or merge any. Be specific about what was wrong for each point.

Sound like a real person, not a rubric checklist. Do NOT mention "rubrics", \
"criteria", or "requirements". Use phrases like "you didn't mention...", \
"you got X wrong...", "I also needed...".

Output ONLY the feedback text, nothing else."""


def build_feedback_detail(
    task: Task,
    trajectory: Trajectory,
    feedback: Feedback,
    feedback_level: int = 2,
    region: str = "us-west-2",
) -> str:
    """Build feedback as natural user feedback via LLM rephrase.

    For passed tasks: just "PASS".
    For failed tasks: collect failed rubrics, LLM-rephrase into natural user feedback.
    """
    if feedback.success:
        return "Result: PASS"

    rubrics = task.metadata.get("rubrics", [])
    req_status = feedback.raw.get("requirement_status", [])

    # Collect failed rubric texts
    failed_rubrics = []
    for i, rubric in enumerate(rubrics):
        status = str(req_status[i]).strip().lower() if i < len(req_status) else "unknown"
        if status != "yes":
            text = rubric.get("rubric_criteria", "") if isinstance(rubric, dict) else str(rubric)
            failed_rubrics.append(text)

    if not failed_rubrics:
        return "Result: FAIL"

    # LLM rephrase into natural user feedback
    issues_text = "\n".join(f"- {r}" for r in failed_rubrics)
    task_question = task.metadata.get("task_text", "")[:300]
    user_msg = (
        f"Task the user asked:\n{task_question}\n\n"
        f"Issues with the AI's response:\n{issues_text}"
    )

    try:
        client = _get_client(region)
        rephrased, err = _call_bedrock(
            client, "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
            REPHRASE_FEEDBACK_PROMPT, user_msg,
            max_tokens=1024, temperature=0.3,
        )
        if not err and rephrased and rephrased.strip():
            return f"Result: FAIL\nUser feedback: {rephrased.strip()}"
    except Exception as e:
        logger.debug("Feedback rephrase failed: %s", e)

    # Fallback: simple summary without rubric format
    return f"Result: FAIL\nUser feedback: The response had issues — it didn't fully address what I asked."


# ---------------------------------------------------------------------------
# Solve/Judge helpers (with retry)
# ---------------------------------------------------------------------------

def _solve_one(agent: CLBenchAgent, bench: CLBenchBenchmark, task: Task, region: str) -> Trajectory:
    """Solve a single task with retry."""
    _init_worker(region)
    return _retry(agent.solve, task, _max_retries=5, _label=f"solve:{task.id}")


def _judge_one(bench: CLBenchBenchmark, task: Task, trajectory: Trajectory, region: str) -> Feedback:
    """Judge a single task with retry."""
    _init_worker(region)
    return _retry(bench.evaluate, task, trajectory, _max_retries=5, _label=f"judge:{task.id}")


def _process_one_task(
    agent: "CLBenchAgent",
    bench: CLBenchBenchmark,
    task: Task,
    region: str,
    feedback_level: int,
    workspace_dir: Path,
    do_propose: bool = True,
) -> dict:
    """Run the full pipeline for one task: solve → judge → rephrase → propose.

    Proposal happens IN the solver's conversation context (same as
    GuidedSynthesis) so the LLM sees the full context document and its
    own reasoning — no truncation.

    Returns a dict with trajectory, feedback, detail, proposal (or None).
    """
    _init_worker(region)

    # 1. Solve (returns conversation state for in-context proposal)
    conv_state = {}
    try:
        traj, conv_state = _retry(agent.solve_raw, task, _max_retries=5, _label=f"solve:{task.id}")
    except Exception as e:
        logger.error("Solve failed for %s: %s", task.id, e)
        traj = Trajectory(task_id=task.id, output=f"[ERROR] {e}")

    # 2. Judge
    try:
        fb = _retry(bench.evaluate, task, traj, _max_retries=5, _label=f"judge:{task.id}")
    except Exception as e:
        logger.error("Judge failed for %s: %s", task.id, e)
        fb = Feedback(success=False, score=0.0, detail=str(e), raw={})

    # 3. Rephrase feedback (only for failures)
    if fb.success:
        detail = "Result: PASS"
    else:
        try:
            detail = build_feedback_detail(task, traj, fb, feedback_level, region=region)
        except Exception as e:
            logger.debug("Feedback rephrase failed for %s: %s", task.id[:8], e)
            detail = "Result: FAIL"

    # 4. Propose skill IN CONTEXT (failures only)
    proposal = None
    if do_propose and not fb.success and conv_state and detail and "FAIL" in detail:
        try:
            ctx_id = task.metadata.get("context_id", "")
            ctx_skill_dir = workspace_dir / "skills" / "context" / ctx_id
            existing_ctx_skills = []
            if ctx_skill_dir.exists():
                for skill_file in sorted(ctx_skill_dir.rglob("SKILL.md")):
                    content = skill_file.read_text()
                    s_name = skill_file.parent.name
                    s_desc = ""
                    for sline in content.split("\n"):
                        if sline.strip().startswith("description:"):
                            s_desc = sline.split(":", 1)[1].strip()
                            break
                    existing_ctx_skills.append((s_name, s_desc))

            proposal = _propose_in_context(
                task=task,
                conv_state=conv_state,
                feedback_detail=detail,
                existing_context_skills=existing_ctx_skills,
                region=region,
            )
        except Exception as e:
            logger.warning("Propose failed for %s: %s", task.id[:8], e)

    return {
        "task": task,
        "trajectory": traj,
        "feedback": fb,
        "detail": detail,
        "proposal": proposal,
    }


COMPRESS_PROMPT_LEGACY = """\
You are compressing context for an evolution agent. The input contains task observations and a skill tree \
that together exceed the context budget. Compress them while preserving all actionable information.

Rules:
- For task observations: group by category, summarize common failure patterns, keep specific details \
  (rubric failures, error types) that the evolver needs. Drop redundant per-task details.
- For the skill tree: keep all skill names/paths/descriptions intact (the evolver needs them to decide \
  what to update). Only compress the body content of individual skills if needed.
- Preserve ALL skill paths exactly — the evolver writes to these paths.
- Output format: return a JSON object with two keys:
  {"compressed_logs": [...], "compressed_skill_tree": "..."}
  compressed_logs should be a list of observation dicts (same schema, just fewer/summarized).
  compressed_skill_tree should be the full SKILL_TREE.md content (possibly with shorter descriptions).
"""

def _compress_evolve_context(
    obs_logs: list[dict],
    skill_tree_content: str,
    region: str,
    compress_model: str = "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
) -> tuple[list[dict], str]:
    """Use LLM to compress observation logs and skill tree if they're too large.

    Returns (compressed_logs, compressed_skill_tree).
    """
    user_message = (
        f"## Task Observations ({len(obs_logs)} tasks)\n"
        f"```json\n{json.dumps(obs_logs, indent=1)}\n```\n\n"
        f"## Skill Tree\n```markdown\n{skill_tree_content}\n```\n\n"
        f"Target budget: compress to roughly half the current size while keeping all actionable info."
    )

    client = _get_client(region)
    resp, err = _call_bedrock(
        client, compress_model, COMPRESS_PROMPT, user_message,
        max_tokens=8192, temperature=0.0,
    )

    if err:
        logger.warning("Compression LLM failed: %s — falling back to truncation", err)
        return _truncate_fallback(obs_logs, skill_tree_content)

    parsed = _parse_json_object(resp)
    if not parsed:
        logger.warning("Compression returned invalid JSON — falling back to truncation")
        return _truncate_fallback(obs_logs, skill_tree_content)

    compressed_logs = parsed.get("compressed_logs", obs_logs)
    compressed_tree = parsed.get("compressed_skill_tree", skill_tree_content)

    logger.info(
        "Compressed evolve context: logs %d→%d items, skill_tree %d→%d chars",
        len(obs_logs), len(compressed_logs),
        len(skill_tree_content), len(compressed_tree),
    )
    return compressed_logs, compressed_tree


def _truncate_fallback(
    obs_logs: list[dict],
    skill_tree_content: str,
) -> tuple[list[dict], str]:
    """Simple truncation fallback if LLM compression fails."""
    # Keep failed tasks first, then passed, truncate each entry harder
    failed = [l for l in obs_logs if not l.get("success", False)]
    passed = [l for l in obs_logs if l.get("success", False)]
    keep = failed[-12:] + passed[-4:]
    for log in keep:
        for key in ("feedback_detail", "agent_output", "task_question"):
            if key in log and len(str(log[key])) > 150:
                log[key] = str(log[key])[:150] + "..."
    return keep, skill_tree_content[:20000]


# ---------------------------------------------------------------------------
# Parallel per-task evolve + merge
# ---------------------------------------------------------------------------

def _evolve_one_task(
    observation: Observation,
    workspace_dir: Path,
    config: EvolveConfig,
    feedback_level: int,
    evo_number: int,
    turn_history: str = "",
) -> dict:
    """Run evolve for a single task on a workspace copy. Returns new/modified skills."""
    task = observation.task
    task_id = task.id

    # Copy workspace to a temp dir
    tmp_dir = Path(tempfile.mkdtemp(prefix=f"evolve_{task_id[:8]}_"))
    try:
        shutil.copytree(workspace_dir, tmp_dir / "ws", dirs_exist_ok=True)
        tmp_ws = AgentWorkspace(tmp_dir / "ws")

        # Write this task's observation into the tmp workspace
        obs_dir = tmp_ws.root / "evolution" / "observations"
        obs_dir.mkdir(parents=True, exist_ok=True)
        obs_file = obs_dir / "current.jsonl"

        task_input = observation.task.input or ""
        task_marker = "\nTask:\n"
        marker_idx = task_input.find(task_marker)
        task_question = task_input[marker_idx + len(task_marker):][:500] if marker_idx >= 0 else task_input[:500]

        record = {
            "task_id": task_id,
            "category": task.metadata.get("context_category", ""),
            "sub_category": task.metadata.get("sub_category", ""),
            "task_question": task_question,
            "agent_output": observation.trajectory.output[:800],
            "success": observation.feedback.success,
            "score": observation.feedback.score,
            "feedback_detail": observation.feedback.detail,
        }
        obs_file.write_text(json.dumps(record, default=str) + "\n")

        # Run evolve
        evolver = AEvolveEngine(config)
        evolve_result = _retry(
            evolver.evolve,
            workspace=tmp_ws,
            observation_logs=[record],
            evo_number=evo_number,
            turn_history=turn_history,
            _max_retries=3,
            _label=f"evolve:{task_id[:8]}",
        )

        # Collect new/modified skills from tmp workspace
        new_skills = {}
        for skill_file in sorted((tmp_ws.root / "skills").rglob("SKILL.md")):
            skill_dir = skill_file.parent
            rel_path = str(skill_dir.relative_to(tmp_ws.root))
            content = skill_file.read_text()
            # Check if this skill is new or modified compared to original
            orig = workspace_dir / rel_path / "SKILL.md"
            if not orig.exists() or orig.read_text() != content:
                new_skills[rel_path] = content

        # Also check if system prompt was modified
        tmp_prompt = (tmp_ws.root / "prompts" / "system.md")
        orig_prompt = (workspace_dir / "prompts" / "system.md")
        prompt_changed = None
        if tmp_prompt.exists() and orig_prompt.exists():
            if tmp_prompt.read_text() != orig_prompt.read_text():
                prompt_changed = tmp_prompt.read_text()

        logger.info(
            "[evo %d] Evolve %s: %d new/modified skills, prompt_changed=%s",
            evo_number, task_id[:8], len(new_skills), prompt_changed is not None,
        )
        return {
            "task_id": task_id,
            "new_skills": new_skills,  # {rel_path: content}
            "prompt_changed": prompt_changed,
            "evolve_result": evolve_result,
        }
    except Exception as e:
        logger.error("Evolve failed for %s: %s", task_id[:8], e)
        return {"task_id": task_id, "new_skills": {}, "prompt_changed": None, "error": str(e)}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


MERGE_PROMPT = """\
You are a skill library curator. Your job is to maintain a high-quality skill tree.

The skill library lives under `skills/`. Each skill is a directory containing a SKILL.md file. You have full control over the tree structure — organize it however makes sense. You can use flat paths, nested categories, or deep hierarchies. You decide.

Example (just one possible structure — you choose your own):
```
skills/
├── legal-contract-analysis/SKILL.md       # flat
├── reasoning/                              # one level of grouping
│   └── scientific/SKILL.md
├── execution/simulation/                   # two levels
│   ├── state-tracking/SKILL.md
│   └── environment-exploration/SKILL.md
```

You will receive:
1. EXISTING SKILLS — the current skill tree (already in production)
2. CANDIDATE SKILLS — newly proposed skills from parallel evolve runs

Your job:
1. **Organize**: Structure the tree however you see fit. Group related skills, nest when it helps clarity, keep it flat when nesting adds no value. Reorganize existing structure if needed.
2. **Deduplicate**: If a candidate is semantically identical to an existing skill, SKIP it.
3. **Merge overlapping**: If a candidate partially overlaps with an existing skill, UPDATE the existing one. Keep each skill focused.
4. **Add new**: If a candidate covers a distinct failure pattern not yet covered, ADD it. New skills are welcome — the tree should grow as new patterns are discovered.
5. **Filter generic**: REJECT candidates that are vague meta-advice (e.g. "read carefully", "be thorough"). A good skill names a SPECIFIC pattern or technique.
6. **Prune**: If a skill has existed across multiple turns but its target category hasn't improved (check the Evolution History), consider deleting it.
7. **Split bloated skills**: If an existing skill tries to cover too many patterns (over ~1500 chars of content), split it into focused sub-skills.
8. **Prompt**: Only update the system prompt if there's a clear, specific improvement.

Output ONLY valid JSON:
{
  "skills_to_add": [
    {"path": "skills/.../skill-name", "content": "---\\nname: ...\\ndescription: ...\\n---\\n\\n# ...\\n\\n..."}
  ],
  "skills_to_update": [
    {"path": "skills/.../existing-skill", "content": "---\\nname: ...\\ndescription: ...\\n---\\n\\n# ...\\n\\n..."}
  ],
  "skills_to_delete": ["skills/.../skill-to-remove"],
  "prompt_update": null,
  "reasoning": "brief explanation of decisions"
}

Rules:
- path is relative, starting with "skills/" (e.g. "skills/poker-strategy", "skills/reasoning/legal/contracts")
- Each skill should have a clear, bounded scope — one specific pattern or technique
- Don't let skills become bloated catch-alls — a skill over 1500 chars is probably trying to do too much. Split it.
- A healthy tree has many focused leaves, not a few giant ones
- Balance: add new skills for new patterns, merge for overlapping patterns, prune for dead weight
"""


def _merge_evolve_results(
    results: list[dict],
    workspace_dir: Path,
    region: str = "us-west-2",
    merge_model: str = "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    turn_history: str = "",
) -> dict:
    """LLM-based merge of parallel evolve results.

    Uses an LLM to deduplicate, merge, filter, and prune skills.
    """
    # Collect all candidate skills and prompt changes
    all_new_skills: dict[str, list[tuple[str, str]]] = defaultdict(list)
    prompt_candidates: list[tuple[str, str]] = []

    for r in results:
        tid = r["task_id"]
        for path, content in r.get("new_skills", {}).items():
            all_new_skills[path].append((tid, content))
        if r.get("prompt_changed"):
            prompt_candidates.append((tid, r["prompt_changed"]))

    if not all_new_skills and not prompt_candidates:
        logger.info("Merge: nothing to merge")
        return {"merged_skills": 0, "deleted_skills": 0, "conflicts": 0}

    # Build existing skills summary (grouped by category)
    existing_lines = []
    skills_dir = workspace_dir / "skills"
    existing_skills = {}
    existing_by_category: dict[str, list[tuple[str, str]]] = defaultdict(list)
    if skills_dir.exists():
        for skill_file in sorted(skills_dir.rglob("SKILL.md")):
            rel = str(skill_file.parent.relative_to(workspace_dir))
            content = skill_file.read_text()
            existing_skills[rel] = content
            # Group by category
            parts = Path(rel).parts  # ("skills", "category", "name") or ("skills", "name")
            category = parts[1] if len(parts) > 2 else "(root)"
            existing_by_category[category].append((rel, content))

    for category in sorted(existing_by_category):
        if category != "(root)":
            existing_lines.append(f"## Category: {category}/")
        for rel, content in existing_by_category[category]:
            existing_lines.append(f"### `{rel}`\n```\n{content[:500]}\n```")

    # Build candidate skills summary
    candidate_lines = []
    for path, versions in all_new_skills.items():
        # If multiple versions, show all briefly
        if len(versions) == 1:
            tid, content = versions[0]
            candidate_lines.append(
                f"### `{path}` (from task {tid[:8]})\n```\n{content[:600]}\n```"
            )
        else:
            candidate_lines.append(f"### `{path}` ({len(versions)} versions)")
            for tid, content in versions:
                candidate_lines.append(f"**v:{tid[:8]}**\n```\n{content[:400]}\n```")

    # Build prompt change section
    prompt_section = "No prompt changes proposed."
    if prompt_candidates:
        current_prompt = (workspace_dir / "prompts" / "system.md").read_text()
        prompt_section = f"**Current prompt** ({len(current_prompt)} chars):\n```\n{current_prompt[:500]}\n```\n\n"
        for tid, p in prompt_candidates:
            prompt_section += f"**Proposed by {tid[:8]}** ({len(p)} chars):\n```\n{p[:500]}\n```\n\n"

    turn_history_section = ""
    if turn_history:
        turn_history_section = f"## Evolution History\n{turn_history}\n\n"

    user_message = (
        turn_history_section
        + f"## Existing Skills ({len(existing_skills)})\n\n"
        + ("\n\n".join(existing_lines) if existing_lines else "No existing skills.\n")
        + f"\n\n## Candidate Skills ({len(all_new_skills)} paths, "
        + f"{sum(len(v) for v in all_new_skills.values())} versions)\n\n"
        + "\n\n".join(candidate_lines)
        + f"\n\n## Prompt Changes\n{prompt_section}"
    )

    # Call LLM for merge decision
    client = _get_client(region)
    resp, err = _call_bedrock(
        client, merge_model, MERGE_PROMPT, user_message,
        max_tokens=8192, temperature=0.0,
    )

    if err:
        logger.error("LLM merge failed: %s — falling back to simple merge", err)
        return _simple_merge_fallback(all_new_skills, prompt_candidates, workspace_dir)

    parsed = _parse_json_object(resp)
    if not parsed:
        logger.error("LLM merge returned invalid JSON — falling back to simple merge")
        return _simple_merge_fallback(all_new_skills, prompt_candidates, workspace_dir)

    # Apply merge decisions
    added = 0
    updated = 0
    deleted = 0

    for skill in parsed.get("skills_to_add", []):
        path = skill.get("path", "")
        content = skill.get("content", "")
        if path and content:
            dest = workspace_dir / path / "SKILL.md"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)
            added += 1
            logger.info("Merge: added %s", path)

    for skill in parsed.get("skills_to_update", []):
        path = skill.get("path", "")
        content = skill.get("content", "")
        if path and content:
            dest = workspace_dir / path / "SKILL.md"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)
            updated += 1
            logger.info("Merge: updated %s", path)

    for path in parsed.get("skills_to_delete", []):
        skill_dir = workspace_dir / path
        if skill_dir.exists():
            shutil.rmtree(skill_dir)
            deleted += 1
            logger.info("Merge: deleted %s", path)

    if parsed.get("prompt_update"):
        (workspace_dir / "prompts" / "system.md").write_text(parsed["prompt_update"])
        logger.info("Merge: updated system prompt")

    reasoning = parsed.get("reasoning", "")
    logger.info(
        "LLM merge done: +%d added, ~%d updated, -%d deleted | %s",
        added, updated, deleted, reasoning[:200],
    )
    return {
        "merged_skills": added + updated,
        "deleted_skills": deleted,
        "conflicts": sum(1 for v in all_new_skills.values() if len(v) > 1),
        "reasoning": reasoning,
    }


def _simple_merge_fallback(
    all_new_skills: dict,
    prompt_candidates: list,
    workspace_dir: Path,
) -> dict:
    """Simple fallback merge when LLM merge fails."""
    merged = 0
    for path, versions in all_new_skills.items():
        best_tid, best_content = max(versions, key=lambda x: len(x[1]))
        dest = workspace_dir / path / "SKILL.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(best_content)
        merged += 1
    if prompt_candidates:
        best_tid, best_prompt = max(prompt_candidates, key=lambda x: len(x[1]))
        (workspace_dir / "prompts" / "system.md").write_text(best_prompt)
    return {"merged_skills": merged, "deleted_skills": 0, "conflicts": 0}


# ---------------------------------------------------------------------------
# Batch evolve loop
# ---------------------------------------------------------------------------

def evolve_batch(
    agent: CLBenchAgent,
    bench: CLBenchBenchmark,
    tasks: list[Task],
    config: EvolveConfig,
    observer: Observer,
    max_evolve_turns: int = 3,
    feedback_level: int = 2,
    batch_workers: int = 4,
    batch_label: str = "",
    results_dir: Path | None = None,
    no_retest: bool = False,
) -> list[dict]:
    """Run batch solve → judge → unified evolve loop.

    If no_retest is True, each batch only does:
      solve → judge → evolve → next batch
    Skills carry forward but failed tasks are NOT re-tested.

    Otherwise (default), within each turn:
      1. Parallel solve all remaining tasks
      2. Parallel judge all of them
      3. Unified evolve on ALL results (passed + failed) on the main workspace
      4. Update SKILL_TREE.md
      5. Repeat with remaining failures
    """
    region = bench.region
    workspace_dir = agent.workspace.root

    # Track per-task best results
    task_results: dict[str, dict] = {}
    for t in tasks:
        task_results[t.id] = {
            "task_id": t.id,
            "category": t.metadata.get("context_category", ""),
            "sub_category": t.metadata.get("sub_category", ""),
            "passed": False,
            "best_rubric_passed": -1,
            "rubric_total": len(t.metadata.get("rubrics", [])),
            "turns_used": 0,
        }

    remaining_tasks = list(tasks)
    turn_summaries: list[str] = []  # accumulate per-turn summaries for evolution context

    effective_turns = 1 if no_retest else max_evolve_turns + 1

    for turn in range(effective_turns):
        if not remaining_tasks:
            break

        # -- Reload agent (picks up latest skills + SKILL_TREE.md) --
        agent.reload_from_fs()

        curator_model = config.extra.get("curator_model", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
        max_skills_per_context = config.extra.get("max_skills_per_context", 5)
        # Always propose — skills carry forward to next batch even in no_retest mode
        do_propose = True

        # -- Parallel per-task pipeline: solve → judge → rephrase → propose --
        logger.info(
            "[%s turn %d] Processing %d tasks (solve→judge→rephrase→propose)...",
            batch_label, turn, len(remaining_tasks),
        )
        task_outputs: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=batch_workers, initializer=_init_worker, initargs=(region,)) as pool:
            futures = {
                pool.submit(
                    _process_one_task,
                    agent=agent, bench=bench, task=t, region=region,
                    feedback_level=feedback_level, workspace_dir=workspace_dir,
                    do_propose=do_propose,
                ): t
                for t in remaining_tasks
            }
            for fut in as_completed(futures):
                t = futures[fut]
                try:
                    task_outputs[t.id] = fut.result()
                except Exception as e:
                    logger.error("Task pipeline failed for %s: %s", t.id, e)
                    task_outputs[t.id] = {
                        "task": t,
                        "trajectory": Trajectory(task_id=t.id, output=f"[ERROR] {e}"),
                        "feedback": Feedback(success=False, score=0.0, detail=str(e), raw={}),
                        "detail": "Result: FAIL",
                        "proposal": None,
                    }

        # -- Process results --
        failed_tasks = []
        all_observations = []
        proposals: list[dict] = []
        failed_summaries_for_general: list[dict] = []  # for general curator

        for t in remaining_tasks:
            out = task_outputs[t.id]
            fb = out["feedback"]
            traj = out["trajectory"]
            detail = out["detail"]

            req_status = fb.raw.get("requirement_status", [])
            rubric_passed = sum(1 for s in req_status if str(s).strip().lower() == "yes")
            rubric_total = task_results[t.id]["rubric_total"]

            logger.info(
                "[%s turn %d] %s %s — rubrics %d/%d",
                batch_label, turn, t.id, "PASS" if fb.success else "FAIL",
                rubric_passed, rubric_total,
            )

            if rubric_passed > task_results[t.id]["best_rubric_passed"]:
                task_results[t.id]["best_rubric_passed"] = rubric_passed
            task_results[t.id]["turns_used"] = turn + 1

            if results_dir:
                task_log = results_dir / t.id
                task_log.mkdir(parents=True, exist_ok=True)
                (task_log / f"turn_{turn}.json").write_text(json.dumps({
                    "turn": turn,
                    "passed": fb.success,
                    "rubric_passed": rubric_passed,
                    "rubric_total": rubric_total,
                    "output_preview": _truncate(traj.output, 500),
                }, ensure_ascii=False, indent=2))

            if fb.success:
                task_results[t.id]["passed"] = True
            else:
                failed_tasks.append(t)
                # Collect feedback analysis + proposal for general curator
                # NO raw rubrics — general curator sees analysis + proposals only
                proposal_summary = ""
                if out.get("proposal"):
                    p = out["proposal"]
                    proposal_summary = f"[{p.get('action', 'NEW')}] {p.get('name', '')}: {p.get('description', '')}"
                failed_summaries_for_general.append({
                    "task_id": t.id,
                    "context_id": t.metadata.get("context_id", ""),
                    "category": t.metadata.get("context_category", ""),
                    "sub_category": t.metadata.get("sub_category", ""),
                    "feedback_detail": detail,
                    "proposal_summary": proposal_summary,
                })

            leveled_fb = Feedback(success=fb.success, score=fb.score, detail=detail, raw=fb.raw)
            all_observations.append(Observation(task=t, trajectory=traj, feedback=leveled_fb))

            if out["proposal"]:
                proposals.append(out["proposal"])

        passed_this_turn = len(remaining_tasks) - len(failed_tasks)
        logger.info(
            "[%s turn %d] Batch result: %d/%d passed, %d failed, %d proposals",
            batch_label, turn, passed_this_turn, len(remaining_tasks),
            len(failed_tasks), len(proposals),
        )

        # Build per-category breakdown for this turn
        cat_stats: dict[str, dict] = defaultdict(lambda: {"passed": 0, "total": 0})
        for t in (remaining_tasks if turn == 0 else tasks):  # first turn: current batch; later: all
            cat = t.metadata.get("context_category", "unknown")
            tid = t.id
            if tid in task_results:
                cat_stats[cat]["total"] += 1
                if task_results[tid]["passed"]:
                    cat_stats[cat]["passed"] += 1

        total_passed = sum(1 for r in task_results.values() if r["passed"])
        cat_line = ", ".join(f"{c}: {s['passed']}/{s['total']}" for c, s in sorted(cat_stats.items()))
        skills_count = len(agent.workspace.list_skills()) if hasattr(agent, 'workspace') else 0
        turn_summaries.append(
            f"Turn {turn}: {total_passed}/{len(task_results)} passed overall "
            f"({passed_this_turn} new this turn) | {skills_count} skills | by category: {cat_line}"
        )

        remaining_tasks = failed_tasks

        # -- GuidedSynthesis-style curate: group proposals → curator per context --
        # Always curate — skills carry forward to next batch even in no_retest mode
        if proposals:
            agent.export_to_fs()
            observer.collect(all_observations)

            logger.info(
                "[%s turn %d] Curating %d proposals across contexts...",
                batch_label, turn, len(proposals),
            )

            # Group proposals by context_id
            ctx_proposals: dict[str, list[dict]] = defaultdict(list)
            for p in proposals:
                ctx_proposals[p.get("context_id", "")].append(p)

            # Curate per context (parallel across contexts)
            total_stats = {"added": 0, "merged": 0, "skipped": 0}
            with ThreadPoolExecutor(max_workers=batch_workers, initializer=_init_worker, initargs=(region,)) as pool:
                curate_futures = {}
                for ctx_id, ctx_props in ctx_proposals.items():
                    # Get existing skills for this context
                    ctx_skill_dir = workspace_dir / "skills" / "context" / ctx_id
                    existing = []
                    if ctx_skill_dir.exists():
                        for skill_file in sorted(ctx_skill_dir.rglob("SKILL.md")):
                            content = skill_file.read_text()
                            s_name = skill_file.parent.name
                            s_desc = ""
                            for sline in content.split("\n"):
                                if sline.strip().startswith("description:"):
                                    s_desc = sline.split(":", 1)[1].strip()
                                    break
                            s_path = str(skill_file.parent.relative_to(workspace_dir))
                            existing.append((s_name, s_desc, s_path))

                    fut = pool.submit(
                        _curate_context_proposals,
                        context_id=ctx_id,
                        proposals=ctx_props,
                        existing_skills=existing,
                        workspace_dir=workspace_dir,
                        region=region,
                        model=curator_model,
                        max_skills=max_skills_per_context,
                    )
                    curate_futures[fut] = ctx_id

                for fut in as_completed(curate_futures):
                    try:
                        stats = fut.result()
                        for k in total_stats:
                            total_stats[k] += stats.get(k, 0)
                    except Exception as e:
                        logger.warning("Curation failed for context %s: %s", curate_futures[fut][:8], e)

            logger.info(
                "[%s turn %d] Context curation: +%d added, %d merged, %d skipped",
                batch_label, turn, total_stats["added"], total_stats["merged"],
                total_stats["skipped"],
            )

        # -- General curator: cross-context failure pattern analysis --
        max_general_skills = config.extra.get("max_general_skills", 10)
        if max_general_skills > 0 and failed_summaries_for_general and len(failed_summaries_for_general) >= 3:
            logger.info(
                "[%s turn %d] General curator analyzing %d failed tasks for cross-context patterns...",
                batch_label, turn, len(failed_summaries_for_general),
            )
            try:
                general_stats = _curate_general_skills(
                    failed_summaries=failed_summaries_for_general,
                    workspace_dir=workspace_dir,
                    region=region,
                    model=curator_model,
                    max_general=max_general_skills,
                )
                logger.info(
                    "[%s turn %d] General curator: +%d added, %d updated, %d deleted",
                    batch_label, turn,
                    general_stats["added"], general_stats["updated"], general_stats["deleted"],
                )
            except Exception as e:
                logger.warning("General curator failed: %s", e)

        # -- Update SKILL_TREE.md --
        agent.reload_from_fs()
        update_skill_tree(agent.workspace)

        skills_after = len(agent.workspace.list_skills())
        logger.info(
            "[%s turn %d] Evolve done | %d skills now",
            batch_label, turn, skills_after,
        )

        if not remaining_tasks or turn >= max_evolve_turns:
            break

    return list(task_results.values())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description="CL-bench per-task evolve loop (A-EVOLVE)")
    p.add_argument("--grouped-path", type=str, default="/fsx/tianxin/CL-bench/CL-bench-grouped.jsonl")
    p.add_argument("--raw-path", type=str, default="/fsx/tianxin/CL-bench/CL-bench.jsonl")
    p.add_argument("--max-samples", type=int, default=100)
    p.add_argument("--solver-model", type=str, default=DEFAULT_SOLVER_MODEL)
    p.add_argument("--judge-model", type=str, default=DEFAULT_JUDGE_MODEL)
    p.add_argument("--evolver-model", type=str, default=DEFAULT_EVOLVER_MODEL)
    p.add_argument("--curator-model", type=str, default="2",
                    help="Model for skill curation (default: Sonnet 4.5)")
    p.add_argument("--max-skills-per-context", type=int, default=5,
                    help="Max skills per context (default: 5)")
    p.add_argument("--max-general-skills", type=int, default=10,
                    help="Max general skills across all contexts (default: 10)")
    p.add_argument("--selector-model", type=str, default="2",
                    help="Model for skill selection (default: Sonnet 4.5, use MODEL_MAP keys or full ID)")
    p.add_argument("--region", type=str, default="us-west-2")
    p.add_argument("--max-evolve-turns", type=int, default=3,
                    help="Max evolve iterations per task (0 = single-shot baseline)")
    p.add_argument("--no-retest", action="store_true",
                    help="Don't re-solve tasks after evolve; just learn and move to next batch")
    p.add_argument("--no-general-skills", action="store_true",
                    help="Disable general skills: only use context-specific skills (no selector needed)")
    p.add_argument("--feedback-level", type=int, default=2, choices=[1, 2, 3],
                    help="Feedback granularity: 1=binary, 2=rubric count (default), 3=full detail")
    p.add_argument("--batch-size", type=int, default=10,
                    help="Number of tasks per batch (parallel solve+judge, then unified evolve)")
    p.add_argument("--batch-workers", type=int, default=4,
                    help="Max parallel workers within a batch for solve/judge")
    p.add_argument("--workspace-dir", type=str, default=None)
    p.add_argument("--output-dir", type=str, default="outputs/cl_bench_evolve")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    # -- Logging --
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for n in ("botocore", "urllib3", "httpcore", "httpx"):
        logging.getLogger(n).setLevel(logging.WARNING)

    os.makedirs(args.output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # -- Workspace --
    workspace_dir = Path(args.workspace_dir) if args.workspace_dir else Path(args.output_dir) / "workspace"
    setup_workspace(workspace_dir)

    # -- Benchmark --
    bench = CLBenchBenchmark(
        grouped_path=args.grouped_path,
        raw_path=args.raw_path,
        k_dev_contexts=0,
        max_samples=args.max_samples,
        model_id=args.solver_model,
        judge_model_id=args.judge_model,
        region=args.region,
    )
    bench._ensure_loaded()
    all_tasks = bench.get_tasks(split="test", limit=999999)

    # -- Build per-context task pools for random sampling --
    import random
    from collections import defaultdict
    random.seed(42)
    ctx_pools = defaultdict(list)
    for t in all_tasks:
        ctx_pools[t.metadata.get("context_id", "")].append(t)
    # Shuffle tasks within each context
    for cid in ctx_pools:
        random.shuffle(ctx_pools[cid])
    total_tasks = sum(len(v) for v in ctx_pools.values())
    logger.info("Built task pools: %d tasks across %d contexts",
                total_tasks, len(ctx_pools))

    # -- Agent --
    selector_model_id = MODEL_MAP.get(args.selector_model, args.selector_model)
    evolver_model_id = MODEL_MAP.get(args.evolver_model, args.evolver_model)
    agent = CLBenchAgent(
        workspace_dir=workspace_dir,
        bench=bench,
        selector_model=selector_model_id,
        no_general_skills=args.no_general_skills,
    )

    # -- Observer --
    evolution_dir = workspace_dir / "evolution"
    evolution_dir.mkdir(parents=True, exist_ok=True)
    observer = Observer(evolution_dir)

    # -- Evolve config (proposal + curator models) --
    curator_model_id = MODEL_MAP.get(args.curator_model, args.curator_model)
    evolver_extra = {
        "region": args.region,
        "curator_model": curator_model_id,
        "max_skills_per_context": args.max_skills_per_context,
        "max_general_skills": args.max_general_skills,
    }
    config = EvolveConfig(
        evolver_model=evolver_model_id,
        evolver_max_tokens=16384,
        evolve_prompts=True,
        evolve_skills=True,
        evolve_memory=True,
        extra=evolver_extra,
    )

    # -- Generate initial SKILL_TREE.md --
    update_skill_tree(agent.workspace)

    logger.info(
        "Loaded %d tasks | solver=%s judge=%s curator=%s | "
        "max_evolve_turns=%d | feedback_level=%d | batch_size=%d "
        "batch_workers=%d | max_ctx_skills=%d max_general=%d",
        total_tasks, bench.model_id, bench.judge_model_id,
        curator_model_id, args.max_evolve_turns, args.feedback_level,
        args.batch_size, args.batch_workers, args.max_skills_per_context,
        args.max_general_skills,
    )

    # -- Run batch evolve loop with random context/task sampling --
    results_dir = Path(args.output_dir) / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    all_results = []

    t0 = time.time()
    batch_size = args.batch_size
    num_batches = (total_tasks + batch_size - 1) // batch_size
    batch_idx = 0

    while any(ctx_pools.values()):
        # Pick batch_size contexts randomly (no repeat within batch), one task each
        available_cids = [cid for cid, pool in ctx_pools.items() if pool]
        chosen_cids = random.sample(available_cids, min(batch_size, len(available_cids)))
        batch_tasks = [ctx_pools[cid].pop() for cid in chosen_cids]
        # Remove exhausted contexts
        ctx_pools = {k: v for k, v in ctx_pools.items() if v}

        logger.info(
            "=== Batch %d/%d (%d tasks from %d contexts, %d remaining) ===",
            batch_idx + 1, num_batches, len(batch_tasks), len(chosen_cids),
            sum(len(v) for v in ctx_pools.values()),
        )

        batch_results = evolve_batch(
            agent, bench, batch_tasks, config, observer,
            max_evolve_turns=args.max_evolve_turns,
            feedback_level=args.feedback_level,
            batch_workers=args.batch_workers,
            results_dir=results_dir,
            batch_label=f"B{batch_idx+1}/{num_batches}",
            no_retest=args.no_retest,
        )
        all_results.extend(batch_results)

        passed_so_far = sum(1 for r in all_results if r["passed"])
        logger.info(
            "Batch %d done: cumulative %d/%d (%.1f%%)",
            batch_idx + 1, passed_so_far, len(all_results),
            100 * passed_so_far / max(len(all_results), 1),
        )
        batch_idx += 1

    elapsed = time.time() - t0

    # -- Aggregate stats --
    total_passed = sum(1 for r in all_results if r["passed"])
    total_rubric_pass = sum(r["best_rubric_passed"] for r in all_results)
    total_rubric_total = sum(r["rubric_total"] for r in all_results)

    cat_stats = defaultdict(lambda: {"passed": 0, "total": 0, "rp": 0, "rt": 0})
    for r in all_results:
        cat = r.get("category", "unknown")
        cat_stats[cat]["passed"] += int(r["passed"])
        cat_stats[cat]["total"] += 1
        cat_stats[cat]["rp"] += r["best_rubric_passed"]
        cat_stats[cat]["rt"] += r["rubric_total"]

    logger.info("=" * 70)
    logger.info(
        "FINAL: %d/%d passed (%.1f%%) | rubrics %d/%d (%.1f%%) | %.0fs",
        total_passed, len(all_results),
        100 * total_passed / max(len(all_results), 1),
        total_rubric_pass, total_rubric_total,
        100 * total_rubric_pass / max(total_rubric_total, 1),
        elapsed,
    )
    for cat, s in sorted(cat_stats.items()):
        logger.info(
            "  %s: %d/%d (%.1f%%) | rubrics %d/%d (%.1f%%)",
            cat, s["passed"], s["total"],
            100 * s["passed"] / max(s["total"], 1),
            s["rp"], s["rt"], 100 * s["rp"] / max(s["rt"], 1),
        )

    # -- Save --
    skills_out = Path(args.output_dir) / "skills"
    if (workspace_dir / "skills").exists():
        if skills_out.exists():
            shutil.rmtree(skills_out)
        shutil.copytree(workspace_dir / "skills", skills_out)

    summary = {
        "timestamp": timestamp,
        "solver_model": bench.model_id,
        "judge_model": bench.judge_model_id,
        "evolver_model": evolver_model_id,
        "selector_model": selector_model_id,
        "max_samples": args.max_samples,
        "max_evolve_turns": args.max_evolve_turns,
        "feedback_level": args.feedback_level,
        "batch_size": args.batch_size,
        "batch_workers": args.batch_workers,
        "unified_evolve": True,
        "no_general_skills": args.no_general_skills,
        "total_tasks": len(all_results),
        "passed": total_passed,
        "rate": total_passed / max(len(all_results), 1),
        "rubric_pass": total_rubric_pass,
        "rubric_total": total_rubric_total,
        "rubric_rate": total_rubric_pass / max(total_rubric_total, 1),
        "elapsed_sec": elapsed,
        "per_category": {
            cat: {
                "passed": s["passed"], "total": s["total"],
                "rate": s["passed"] / max(s["total"], 1),
                "rubric_pass": s["rp"], "rubric_total": s["rt"],
                "rubric_rate": s["rp"] / max(s["rt"], 1),
            }
            for cat, s in sorted(cat_stats.items())
        },
    }
    (Path(args.output_dir) / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False)
    )

    with open(Path(args.output_dir) / "all_results.jsonl", "w") as f:
        for r in all_results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    logger.info("Done! Output: %s", args.output_dir)


if __name__ == "__main__":
    main()
