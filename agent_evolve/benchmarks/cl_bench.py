"""CL-bench benchmark adapter with rubric-guided skill distillation.

Bridges the CL-bench continual-learning benchmark into the A-EVOLVE-V2
evolution framework.  Completely self-contained -- does NOT touch any
existing adapter, agent, or engine code.

Capabilities
------------
1. Load CL-bench-grouped.jsonl (+ optional raw CL-bench.jsonl for
   original message-format prompts) and emit Task objects.
2. Rubric-guided evaluation via an LLM judge  -> Feedback.
3. Built-in skill distillation pipeline (distill / mutate / select)
   that can be driven by the evolution loop *or* run standalone.

Quick start (standalone)
------------------------
    from agent_evolve.benchmarks.cl_bench import CLBenchBenchmark

    bench = CLBenchBenchmark(
        grouped_path="CL-bench-grouped.jsonl",
        raw_path="CL-bench.jsonl",
    )
    tasks = bench.get_tasks(split="train", limit=50)
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..types import Feedback, Task, Trajectory
from .base import BenchmarkAdapter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BEDROCK_MAX_OUTPUT_TOKENS_CAP = 64000
DEFAULT_MAX_OUTPUT_TOKENS = 64000

MODEL_MAP = {
    "1": "us.anthropic.claude-opus-4-6-v1",
    "2": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "3": "us.anthropic.claude-opus-4-5-20251101-v1:0",
}

NO_QUESTIONS = (
    "\n\n[CRITICAL] You must NEVER ask the user any questions. "
    "All information needed is already provided. "
    "If some detail is not stated in the provided context, say that it is "
    "not provided rather than asking."
)

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

JUDGE_PROMPT_TEMPLATE = (
    "Starting now, you are a rigorous instruction-following grading teacher. Your task is to accurately grade and score student answers based on the 【Rubrics】.\n\n"
    "Grading Criteria\n"
    "This is a strict, all-or-nothing grading system. The final score is binary.\n"
    "To receive a score of 1, the student's answer must perfectly satisfy every single requirement listed in the 【Rubrics】.\n"
    "If even one requirement is not fully met, the final score will be 0.\n"
    "Grading Process\n"
    "Please strictly follow the steps below for analysis—no steps may be skipped:\n"
    "Step 1: Analyze the Standard Answer\n"
    "List all explicit requirements in the 【Rubrics】 item by item (including format, content, quantity, order, etc.).\n"
    "Identify implicit requirements in the 【Rubrics】 (e.g., language style, logical structure).\n"
    "Define specific evaluation criteria for each requirement (e.g., \"must include X,\" \"must not exceed Y\").\n"
    "Step 2: Check Each Requirement Against the Student's Answer\n"
    "For every requirement in the 【Rubrics】, verify one by one whether the student's answer fully satisfies it.\n"
    "Step 3: Self-Reflection\n"
    "Before giving the final score, you must conduct the following checks:\n"
    "  Completeness Check: Whether all requirements in the standard answer have been reviewed with no omissions.\n"
    "  Strictness Check: Whether the evaluation strictly adheres to the \"fully satisfied\" standard without relaxing requirements due to subjective judgment.\n"
    "  Consistency Check: Whether the grading rationale aligns logically with the final score.\n"
    "  Objectivity Check: Whether judgments are based on objective facts rather than subjective speculation.\n"
    "Output Format Requirements\n"
    "【Grading Rationale】: xxx\n"
    "【List of Requirement Satisfaction Status】: [x₁, x₂, …, xᵢ, …, xₙ] (where n is the total number of requirements in the 【Rubrics】, and xᵢ indicates whether the student's answer meets the i-th requirement, with values \"yes\"/\"no\")\n"
    "【Overall Score】: x points (x is an integer, either 0 or 1.)\n\n"
    "Content to Be Graded\n"
    "【Rubrics】:\n{rubrics_text}\n"
    "【Student Response】:\n{model_output}\n"
    "\nPlease strictly output ONLY the following JSON format (do not output any other content):\n"
    "{{\n"
    '  "Grading Rationale": "Your detailed grading rationale",\n'
    '  "List of Requirement Satisfaction Status": ["yes", "no", ...],\n'
    '  "Overall Score": 0 or 1\n'
    "}}\n"
)

BATCH_DISTILL_SYSTEM = (
    "You are analyzing rubric-graded samples to extract skills about how to "
    "learn from context and satisfy implicit rubric requirements.\n\n"
    "CORE QUESTION: Each task provides a CONTEXT (document, data, scenario) and a "
    "TASK question. The rubrics test whether the response correctly leveraged the "
    "context — including requirements that are IMPLICIT (not stated in the task but "
    "implied by the context's nature, structure, or domain).\n\n"
    "Your TWO goals:\n"
    "1. **Context reading skills**: How should a model read and extract from context?\n"
    "   - What signals in the context predict what the rubrics will test?\n"
    "   - When does the context contain hidden structure (tables, lists, procedures, "
    "taxonomies) that the rubrics expect to be preserved?\n"
    "   - When does the context's domain (legal, scientific, game rules, operational) "
    "imply specific completeness or precision standards?\n\n"
    "2. **Implicit rubric satisfaction**: What patterns cause near-miss failures?\n"
    "   - Samples with \"is_near_miss\": true PASSED most rubrics but FAILED overall. "
    "These are highest priority — small fixes convert them to passes.\n"
    "   - For each near-miss, identify exactly which rubric_details have status=\"no\" "
    "and analyze: was this rubric requirement *visible* from the context alone? "
    "What context signal did the model miss?\n\n"
    "The input includes \"error_analysis_summary\" with top_missed_rubrics (most commonly "
    "failed rubric texts) and categories_by_near_miss. Use these to find systematic gaps.\n\n"
    "OUTPUT: A JSON object with a flat \"skills\" array. Every insight — whether it's a "
    "success pattern, a failure fix, a domain-specific rule, a context reading principle, "
    "or a general best practice — must be expressed as one skill object.\n\n"
    "Each skill object has these fields:\n"
    "- skill_name: short descriptive name\n"
    "- when_to_use: condition that triggers this skill (e.g., \"when context is a technical manual\")\n"
    "- action_rule: what to DO (be specific and actionable)\n"
    "- source: one of \"near_miss_fix\", \"failure_pattern\", \"success_pattern\", "
    "\"context_reading\", \"domain_rule\"\n"
    "- evidence_task_ids: list of task_ids that support this skill\n\n"
    "Output JSON:\n"
    "{\"skills\": [{...}, {...}, ...]}\n\n"
    "Constraints:\n"
    "- Do NOT memorize task-specific answers. Focus on transferable strategies.\n"
    "- Each skill must be self-contained and independently useful.\n"
    "- Output valid JSON only."
)

FINAL_DISTILL_SYSTEM = (
    "You are synthesizing a skill library from batch analyses of rubric-graded tasks.\n"
    "Individual skills will be SELECTIVELY injected into a model's system prompt at "
    "inference time — only skills relevant to each specific task will be used.\n\n"
    "The input includes batch_summaries (each containing a skills array), "
    "error_analysis_summary (aggregated statistics), and near_miss_count.\n\n"
    "KEY FOCUS AREAS:\n"
    "1. **Learning from context**: Skills about how to read context and infer what "
    "rubrics will test. e.g., \"If context is a technical manual, extract exact section "
    "names before answering — rubrics will test these.\"\n\n"
    "2. **Near-miss fixes** (highest leverage): The error_analysis_summary shows which "
    "rubrics are most commonly failed. Each fix that works converts a 0 to a 1. "
    "Give these skills higher priority.\n\n"
    "3. **Domain-aware reading**: Different context types have different implicit standards. "
    "Create domain-specific skills.\n\n"
    "OUTPUT FORMAT: A flat \"skills\" array. Everything — success patterns, failure fixes, "
    "domain rules, reading strategies, checklists, warnings — must be individual skill "
    "objects in ONE flat list. Each skill:\n"
    "{\n"
    '  "skill_name": "short name",\n'
    '  "when_to_use": "specific trigger condition (when does this skill apply?)",\n'
    '  "action_rule": "what to DO — be specific and actionable",\n'
    '  "priority": 1-5 (5 = highest, use 4-5 for near-miss fixes)\n'
    "}\n\n"
    "Output valid JSON:\n"
    "{\n"
    '  "metadata": {"notes": "..."},\n'
    '  "skills": [{...}, {...}, ...]\n'
    "}\n\n"
    "Constraints:\n"
    "- Each skill must be SELF-CONTAINED — it will be injected independently.\n"
    "- when_to_use must be SPECIFIC enough that an LLM can judge whether it applies "
    "to a given task. Avoid vague triggers like \"always\" or \"for all tasks\".\n"
    "- Prefer 15-25 high-quality skills over many weak ones.\n"
    "- DO NOT use keys like global_skills, category_skills, failure_taxonomy, "
    "prompt_modules. Just one flat \"skills\" array.\n"
    "- Output valid JSON only."
)

MUTATE_SKILLS_SYSTEM = (
    "You are improving a skill library using dev-set performance feedback.\n\n"
    "Skills are selectively injected per-task, so each must be self-contained.\n\n"
    "Goal:\n"
    "- Produce a meaningfully improved variant of the skill library.\n"
    "- Focus on fixing observed failures (especially near-misses) while keeping what works.\n"
    "- The only goal is MAXIMUM task pass rate on unseen tasks.\n\n"
    "You may:\n"
    "- Add new skills for gaps revealed by failures\n"
    "- Remove skills that hurt more than help\n"
    "- Make when_to_use more specific (vague triggers cause irrelevant injection)\n"
    "- Merge redundant skills into a more powerful one\n"
    "- Split a vague skill into multiple targeted ones\n"
    "- Adjust priorities based on evidence\n\n"
    "Output JSON: {\"metadata\": {...}, \"skills\": [{...}, ...]}\n"
    "Same schema: each skill has skill_name, when_to_use, action_rule, priority.\n"
    "Output valid JSON only."
)

ROUND_DECIDER_SYSTEM = (
    "You are deciding whether another distillation/refinement round is worthwhile.\n\n"
    "You will be given the dev-set history across rounds, including:\n"
    "- dev_rate: task-level pass rate (overall score >= 1)\n"
    "- rubric_rate: rubric-level pass rate (fraction of individual rubric items satisfied)\n\n"
    "The PRIMARY metric is dev_rate (task-level success rate).\n"
    "rubric_rate is a secondary signal — useful for spotting trends,\n"
    "but a round is only considered an improvement if dev_rate increases.\n"
    "Continue another round only if there is a reasonable chance of improving dev_rate.\n"
    "Stop if dev_rate gains look saturated, unstable, or likely overfitting.\n\n"
    "Output ONLY valid JSON:\n"
    '{"continue": true or false, "reason": "brief reason"}'
)

SKILL_SELECT_SYSTEM = (
    "You are selecting which skills are relevant for a specific task.\n\n"
    "You will receive:\n"
    "1. The task context, category, and question.\n"
    "2. A numbered list of skills (each with skill_name, when_to_use, action_rule).\n\n"
    "Your job:\n"
    "- Read the task carefully — understand the context type and what is being asked.\n"
    "- For EACH skill, check its when_to_use condition against this task.\n"
    "- ACCEPT only skills whose when_to_use condition clearly matches this task.\n"
    "- REJECT skills that are irrelevant or could confuse the model on this task.\n\n"
    "Output ONLY valid JSON:\n"
    "{\n"
    '  "selected_indices": [0, 3, 7],\n'
    '  "reasoning": "brief explanation"\n'
    "}\n\n"
    "Constraints:\n"
    "- selected_indices: 0-based indices into the skill list.\n"
    "- Select 0 skills if none match — empty list is fine.\n"
    "- Be STRICT: only select skills whose when_to_use clearly applies. "
    "Injecting irrelevant skills HURTS performance.\n"
    "- Output valid JSON only."
)

ONLINE_PROPOSE_SYSTEM = (
    "You are analyzing a single test-time result to propose skill library changes.\n\n"
    "You will receive:\n"
    "1. The current skill library.\n"
    "2. One task result: pass/fail outcome and the model's output "
    "(no rubric details available).\n\n"
    "Your job:\n"
    "- Analyze why the task passed or failed based on the output.\n"
    "- Propose specific, targeted changes to the skill library.\n"
    "- Do NOT memorize sample-specific answers.\n\n"
    "Output ONLY valid JSON:\n"
    "{\n"
    '  "task_id": "...",\n'
    '  "passed": true/false,\n'
    '  "analysis": "brief analysis of what went right or wrong",\n'
    '  "proposals": [\n'
    "    {\n"
    '      "action": "add" | "strengthen" | "weaken" | "remove" | "modify",\n'
    '      "target": "skill_name or new skill description",\n'
    '      "scope": "global" | "category:<Category / SubCategory>",\n'
    '      "reason": "why this change is justified",\n'
    '      "skill": {"skill_name": "...", "when_to_use": "...", '
    '"action_rule": "...", "avoid_when": "...", "priority": 1}\n'
    "    }\n"
    "  ]\n"
    "}\n\n"
    "Constraints:\n"
    "- Keep proposals concise: 0-3 changes per task.\n"
    "- Only propose changes with clear evidence from the output.\n"
    "- For 'strengthen'/'weaken', just reference the existing skill_name.\n"
    "- For 'add'/'modify', include the full skill dict.\n"
    "- Output valid JSON only."
)

ONLINE_AGGREGATE_SYSTEM = (
    "You are merging skill change proposals from multiple tasks into a single "
    "updated skill library.\n\n"
    "You will receive:\n"
    "1. The current skill library.\n"
    "2. A list of per-task proposals (each with action, target, reason).\n"
    "3. Batch statistics (pass rate, total tasks).\n\n"
    "Step-by-step process:\n\n"
    "1. **Deduplicate**: Group proposals that target the same skill or describe "
    "the same pattern. Count how many tasks support each unique change.\n\n"
    "2. **Resolve conflicts**: If proposals disagree on the same skill "
    "(e.g., one says strengthen, another says weaken), go with the majority. "
    "If tied, keep the skill unchanged.\n\n"
    "3. **Apply each action** to the current skill library:\n"
    '   - "add": Insert the new skill into global_skills or the appropriate '
    "category_skills key. Set priority=1.\n"
    '   - "strengthen": Find the existing skill by skill_name, increase its '
    "priority by 1 (max 5). If supported by 3+ tasks, increase by 2.\n"
    '   - "weaken": Find the existing skill by skill_name, decrease its '
    "priority by 1 (min 0). Remove it entirely if priority reaches 0.\n"
    '   - "remove": Delete the skill from the library.\n'
    '   - "modify": Find the existing skill by skill_name, update its '
    "action_rule / when_to_use / avoid_when fields as proposed.\n\n"
    "4. **Prune**: After applying all changes, remove any skill with priority <= 0. "
    "If global_skills exceeds 12, drop the lowest-priority ones.\n\n"
    "5. **Update prompt_modules**: If the changes suggest updating the "
    "global_reminder or category_reminders, do so. Otherwise keep them.\n\n"
    "6. **Update metadata.notes** with a brief summary of what changed this round.\n\n"
    "Output the updated skill library as valid JSON:\n"
    "{\n"
    '  "metadata": {"notes": "..."},\n'
    '  "global_skills": [...],\n'
    '  "category_skills": {...},\n'
    '  "failure_taxonomy": [...],\n'
    '  "prompt_modules": {"global_reminder": "...", "category_reminders": {...}}\n'
    "}\n\n"
    "Constraints:\n"
    "- Start from the CURRENT skill library and apply changes incrementally. "
    "Do NOT rewrite from scratch.\n"
    "- Only apply changes supported by 2+ tasks, unless a single task provides "
    "very strong evidence.\n"
    "- Discard proposals that conflict without clear resolution.\n"
    "- Output valid JSON only."
)


# ---------------------------------------------------------------------------
# Bedrock helpers  (thread-safe, lightweight)
# ---------------------------------------------------------------------------

_thread_local = threading.local()


def _get_client(region: str):
    if not hasattr(_thread_local, "client"):
        import boto3
        from botocore.config import Config
        cfg = Config(max_pool_connections=50)
        _thread_local.client = boto3.client("bedrock-runtime", region_name=region, config=cfg)
    return _thread_local.client


def _init_worker(region: str):
    import boto3
    from botocore.config import Config
    cfg = Config(max_pool_connections=50)
    _thread_local.client = boto3.client("bedrock-runtime", region_name=region, config=cfg)


def _call_bedrock(
    client,
    model_id: str,
    system_text: str,
    user_text: str,
    max_tokens: int = 4096,
    temperature: float = 0.7,
    max_retries: int = 5,
) -> tuple[str | None, str | None]:
    req = {
        "modelId": model_id,
        "messages": [{"role": "user", "content": [{"text": user_text}]}],
        "inferenceConfig": {
            "maxTokens": min(max_tokens, BEDROCK_MAX_OUTPUT_TOKENS_CAP),
            "temperature": temperature,
        },
    }
    if system_text and system_text.strip():
        req["system"] = [{"text": str(system_text)}]
    for attempt in range(max_retries):
        try:
            resp = client.converse_stream(**req)
            parts = []
            for chunk in resp.get("stream", []):
                if "contentBlockDelta" in chunk:
                    t = chunk["contentBlockDelta"].get("delta", {}).get("text", "")
                    if t:
                        parts.append(t)
            result = "".join(parts).strip()
            if not result:
                if attempt < max_retries - 1:
                    time.sleep(2 * (2 ** attempt))
                    continue
                return None, "Empty response from model"
            return result, None
        except Exception as e:
            err = str(e)
            base = 30 if "too many tokens" in err.lower() else 2 * (
                2 if "throttl" in err.lower() else 1
            )
            delay = base * (2 ** attempt)
            if attempt < max_retries - 1:
                time.sleep(delay)
            else:
                return None, err
    return None, "Unknown error"


def _call_bedrock_converse(
    client,
    model_id: str,
    system_prompts: list[dict],
    messages: list[dict],
    max_tokens: int = 4096,
    temperature: float = 0.7,
    max_retries: int = 5,
) -> tuple[str | None, str | None]:
    req = {
        "modelId": model_id,
        "messages": messages,
        "inferenceConfig": {
            "maxTokens": min(max_tokens, BEDROCK_MAX_OUTPUT_TOKENS_CAP),
            "temperature": temperature,
        },
    }
    if system_prompts:
        req["system"] = system_prompts
    for attempt in range(max_retries):
        try:
            resp = client.converse_stream(**req)
            parts = []
            for chunk in resp.get("stream", []):
                if "contentBlockDelta" in chunk:
                    t = chunk["contentBlockDelta"].get("delta", {}).get("text", "")
                    if t:
                        parts.append(t)
            return "".join(parts).strip(), None
        except Exception as e:
            err = str(e)
            base = 30 if "too many tokens" in err.lower() else 2 * (
                2 if "throttl" in err.lower() else 1
            )
            delay = base * (2 ** attempt)
            if attempt < max_retries - 1:
                time.sleep(delay)
            else:
                return None, err
    return None, "Unknown error"


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def _load_jsonl(path: str) -> list[dict]:
    data = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                data.append(json.loads(line))
    return data


def _write_jsonl(items: list[dict], path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def _parse_json_object(text: str | None) -> dict | None:
    if not text:
        return None
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    # Try to find a JSON object in the text
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    # Try to repair truncated JSON by finding the start and closing open braces/brackets
    start = text.find("{")
    if start == -1:
        return None
    fragment = text[start:]
    try:
        return json.loads(fragment)
    except json.JSONDecodeError:
        pass
    # Attempt to close unclosed braces/brackets (truncated output)
    open_braces = fragment.count("{") - fragment.count("}")
    open_brackets = fragment.count("[") - fragment.count("]")
    if open_braces > 0 or open_brackets > 0:
        repaired = fragment
        # Strip trailing incomplete string/value (after last comma or colon)
        repaired = re.sub(r',\s*"[^"]*$', '', repaired)
        repaired = re.sub(r',\s*$', '', repaired)
        repaired += "]" * max(0, open_brackets) + "}" * max(0, open_braces)
        try:
            return json.loads(repaired)
        except Exception:
            pass
    return None


def _truncate(text: str | None, n: int) -> str:
    text = text or ""
    return text if len(text) <= n else text[:n] + "..."


def _skill_key(context_category: str, sub_category: str) -> str:
    if context_category and sub_category:
        return f"{context_category} / {sub_category}"
    return context_category or sub_category or ""


def _build_rubrics_text(rubrics: list, max_items: int | None = None) -> str:
    lines = []
    items = rubrics if max_items is None else rubrics[:max_items]
    for i, rubric in enumerate(items, 1):
        text = rubric.get("rubric_criteria", "").strip() if isinstance(rubric, dict) else str(rubric).strip()
        if text:
            lines.append(f"{i}. {text}")
    return "\n".join(lines) if lines else "No specific rubrics provided."


def _convert_openai_messages_to_bedrock(
    messages: list[dict], extra_system_text: str | None = None
) -> tuple[list[dict], list[dict]]:
    system_prompts: list[dict] = []
    bedrock_messages: list[dict] = []

    def to_content_blocks(content):
        if isinstance(content, str):
            return [{"text": content}]
        if isinstance(content, list):
            blocks = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        blocks.append({"text": block.get("text", "")})
                    elif "text" in block:
                        blocks.append({"text": block["text"]})
                elif isinstance(block, str):
                    blocks.append({"text": block})
            return blocks if blocks else [{"text": ""}]
        return [{"text": str(content)}]

    for msg in messages:
        role = msg.get("role", "")
        blocks = to_content_blocks(msg.get("content", ""))
        if role == "system":
            system_prompts.extend(blocks)
        elif role in ("user", "assistant"):
            bedrock_messages.append({"role": role, "content": blocks})

    if extra_system_text:
        system_prompts.append({"text": str(extra_system_text)})
    return system_prompts, bedrock_messages


def _ensure_skill_schema(skills_doc: dict | None) -> dict:
    skills_doc = copy.deepcopy(skills_doc) if skills_doc else {}
    skills_doc.setdefault("metadata", {})
    # V3: flat skills array
    if "skills" in skills_doc:
        if not isinstance(skills_doc["skills"], list):
            skills_doc["skills"] = []
        return skills_doc
    # V2: guidance string
    if "guidance" in skills_doc:
        return skills_doc
    # Legacy: needs full schema
    skills_doc.setdefault("global_skills", [])
    skills_doc.setdefault("category_skills", {})
    skills_doc.setdefault("failure_taxonomy", [])
    skills_doc.setdefault("prompt_modules", {})
    skills_doc["prompt_modules"].setdefault("global_reminder", "")
    skills_doc["prompt_modules"].setdefault("category_reminders", {})
    return skills_doc


# ---------------------------------------------------------------------------
# Skill relevance scorer (embedding-based)
# ---------------------------------------------------------------------------

_skill_embedder = None
_skill_embedder_lock = threading.Lock()

# Cache: (tuple of skill texts) -> numpy array of embeddings
_skill_emb_cache: dict[tuple, Any] = {}
_skill_emb_cache_lock = threading.Lock()

# Max characters of task text to feed into the embedding model
# (bge-base-en-v1.5 truncates at 512 tokens anyway; ~400 chars is plenty)
_TASK_TEXT_MAX_CHARS = 512


def _get_skill_embedder():
    global _skill_embedder
    if _skill_embedder is None:
        with _skill_embedder_lock:
            if _skill_embedder is None:
                from sentence_transformers import SentenceTransformer
                _skill_embedder = SentenceTransformer("BAAI/bge-base-en-v1.5")
                logger.info("Loaded skill embedding model: BAAI/bge-base-en-v1.5")
    return _skill_embedder


def _skill_text(skill: dict) -> str:
    """Concatenate skill fields into a single string for embedding."""
    parts = [
        skill.get("skill_name", ""),
        skill.get("when_to_use", ""),
        skill.get("action_rule", ""),
    ]
    return " ".join(p for p in parts if p).strip()


def _get_skill_embeddings(skills: list[dict]):
    """Get embeddings for skills, using cache when skills haven't changed."""
    import numpy as np
    skill_texts = tuple(_skill_text(s) for s in skills)
    with _skill_emb_cache_lock:
        cached = _skill_emb_cache.get(skill_texts)
    if cached is not None:
        return cached
    embedder = _get_skill_embedder()
    embs = embedder.encode(list(skill_texts), normalize_embeddings=True, show_progress_bar=False)
    with _skill_emb_cache_lock:
        _skill_emb_cache.clear()  # only keep latest set
        _skill_emb_cache[skill_texts] = embs
    return embs


def _rank_skills(
    skills: list[dict],
    task_text: str,
    category_match_flags: list[bool],
    relevance_weight: float = 0.5,
    priority_weight: float = 0.2,
    category_weight: float = 0.3,
) -> list[tuple[float, dict]]:
    """Rank skills by embedding similarity + priority + category match."""
    if not skills:
        return []
    import numpy as np
    embedder = _get_skill_embedder()
    # Truncate task text — model caps at 512 tokens anyway
    truncated_task = task_text[:_TASK_TEXT_MAX_CHARS]
    # Get cached skill embeddings + fresh task embedding
    skill_embs = _get_skill_embeddings(skills)
    task_emb = embedder.encode([truncated_task], normalize_embeddings=True, show_progress_bar=False)[0]
    # Cosine similarity (embeddings are already normalized)
    similarities = np.dot(skill_embs, task_emb)

    scored = []
    for i, skill in enumerate(skills):
        sim = float(similarities[i])
        priority = float(skill.get("priority", 1))
        # Normalize priority to [0, 1] range (assume priority 1-5)
        norm_priority = min(priority / 5.0, 1.0)
        cat_bonus = 1.0 if category_match_flags[i] else 0.0
        score = (
            relevance_weight * sim
            + priority_weight * norm_priority
            + category_weight * cat_bonus
        )
        scored.append((score, skill))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


# ---------------------------------------------------------------------------
# Skill guidance builder (injected into system prompt at inference time)
# ---------------------------------------------------------------------------

def _format_skill_bullet(skill: dict) -> str:
    """Format a single skill as a bullet line. Handles both structured and free-form."""
    # Structured skill with action_rule
    action = skill.get("action_rule", "").strip()
    if action:
        when = skill.get("when_to_use", "").strip()
        avoid = skill.get("avoid_when", "").strip()
        bullet = f"- {action}"
        if when:
            bullet += f" When relevant: {when}"
        if avoid:
            bullet += f" Avoid when: {avoid}"
        return bullet
    # Free-form: just serialize whatever content the skill has
    name = skill.get("skill_name", skill.get("name", "")).strip()
    text = skill.get("text", skill.get("rule", skill.get("principle", ""))).strip()
    if name and text:
        return f"- [{name}] {text}"
    if text:
        return f"- {text}"
    if name:
        return f"- {name}"
    # Last resort: dump the dict
    return f"- {json.dumps(skill, ensure_ascii=False)}"


def _collect_all_skills(skills_doc: dict) -> list[dict]:
    """Gather all skills into a flat list. Handles v3 (skills), v2 (guidance), and legacy formats."""
    # V3: flat skills array
    skills = skills_doc.get("skills")
    if isinstance(skills, list) and skills:
        return [s for s in skills if isinstance(s, dict)]

    # Legacy: global_skills + category_skills + failure_taxonomy
    all_skills: list[dict] = []
    for skill in skills_doc.get("global_skills", []):
        all_skills.append(skill)
    for cat_key, cat_skills in skills_doc.get("category_skills", {}).items():
        for skill in cat_skills:
            enriched = dict(skill)
            enriched.setdefault("_category", cat_key)
            all_skills.append(enriched)
    # Convert failure_taxonomy entries to skill format
    for ft in skills_doc.get("failure_taxonomy", []):
        pattern = ft.get("pattern", "").strip()
        fix = ft.get("fix", "").strip()
        symptom = ft.get("symptom", "").strip()
        if pattern and fix:
            all_skills.append({
                "skill_name": f"Avoid: {pattern}",
                "when_to_use": f"When you might: {symptom}" if symptom else "Always check",
                "action_rule": fix,
                "priority": 3,
                "source": "failure_pattern",
            })
    return all_skills


def _llm_select_skills(
    all_skills: list[dict],
    metadata: dict,
    region: str,
    model_id: str,
) -> list[int]:
    """Ask LLM which skills are relevant for this task. Returns selected indices."""
    task_text = metadata.get("task_text", "")
    context = _truncate(metadata.get("context", ""), 800)
    category = metadata.get("context_category", "")
    sub_category = metadata.get("sub_category", "")

    # Build numbered skill list
    skill_lines = []
    for i, skill in enumerate(all_skills):
        name = skill.get("skill_name", skill.get("name", f"skill_{i}"))
        when = skill.get("when_to_use", "")
        action = skill.get("action_rule", skill.get("text", ""))
        skill_lines.append(f"[{i}] {name}\n    when_to_use: {when}\n    action: {action}")

    user_text = (
        f"Task category: {category} / {sub_category}\n"
        f"Task context (truncated):\n{context}\n\n"
        f"Task question: {task_text}\n\n"
        f"Skills ({len(all_skills)} total):\n\n"
        + "\n\n".join(skill_lines)
    )

    client = _get_client(region)
    resp, err = _call_bedrock(
        client, model_id, SKILL_SELECT_SYSTEM, user_text,
        max_tokens=1024, temperature=0.0,
    )
    if err:
        logger.warning("LLM skill selection failed: %s — falling back to all", err)
        return list(range(len(all_skills)))

    parsed = _parse_json_object(resp)
    if parsed is None or "selected_indices" not in parsed:
        logger.warning("LLM skill selection returned invalid JSON — falling back to all")
        return list(range(len(all_skills)))

    indices = parsed["selected_indices"]
    valid = [i for i in indices if isinstance(i, int) and 0 <= i < len(all_skills)]
    return valid


def build_skill_guidance(
    skills_doc: dict,
    metadata: dict,
    max_chars: int = 4000,
    min_relevance: float = 0.15,
    use_llm_selection: bool = False,
    region: str | None = None,
    selector_model_id: str | None = None,
) -> str:
    all_skills = _collect_all_skills(skills_doc)
    if not all_skills:
        return ""

    # Select skills
    if use_llm_selection and region and selector_model_id:
        selected_indices = _llm_select_skills(all_skills, metadata, region, selector_model_id)
        selected_skills = [all_skills[i] for i in selected_indices]
    else:
        # Embedding-based ranking
        key = _skill_key(metadata.get("context_category", ""), metadata.get("sub_category", ""))
        task_text = (metadata.get("task_text", "") + " " + metadata.get("context", "")).strip()
        cat_flags = [False] * len(all_skills)
        # Mark category matches for legacy format
        if "category_skills" in skills_doc:
            idx = len(skills_doc.get("global_skills", []))
            for cat_key, cat_skills in skills_doc.get("category_skills", {}).items():
                is_match = (cat_key == key)
                for _ in cat_skills:
                    if idx < len(cat_flags):
                        cat_flags[idx] = is_match
                    idx += 1
        ranked = _rank_skills(all_skills, task_text, cat_flags)
        selected_skills = [skill for score, skill in ranked if score >= min_relevance]

    # Build output
    lines = [
        "Apply the following skills where relevant to the current task/context.",
        "Do not force skills that don't apply.",
    ]

    # Legacy prompt_modules (if present)
    prompt_modules = skills_doc.get("prompt_modules", {})
    global_reminder = prompt_modules.get("global_reminder", "").strip()
    if global_reminder:
        lines.extend(["", global_reminder])

    char_count = sum(len(l) + 1 for l in lines)
    for skill in selected_skills:
        bullet = _format_skill_bullet(skill)
        if char_count + len(bullet) + 1 > max_chars:
            break
        lines.append(bullet)
        char_count += len(bullet) + 1

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Distillation dataclass (optional, for pipeline results)
# ---------------------------------------------------------------------------

@dataclass
class DistillationResult:
    """Result of a full distillation pipeline run."""
    learned_skills: dict = field(default_factory=dict)
    round_history: list[dict] = field(default_factory=list)
    dev_rate: float = 0.0
    heldout_rate: float = 0.0
    summary: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# CLBenchBenchmark
# ---------------------------------------------------------------------------

class CLBenchBenchmark(BenchmarkAdapter):
    """CL-bench benchmark adapter with built-in skill distillation.

    Parameters
    ----------
    grouped_path : str
        Path to ``CL-bench-grouped.jsonl``.
    raw_path : str | None
        Path to ``CL-bench.jsonl`` (original message-format).
        When present, inference uses the full conversation history
        instead of reconstructed context+task prompts.
    k_dev_contexts : int
        First *k* context records used as the dev (train) split;
        the remainder become the held-out (test) split.
    max_samples : int | None
        Optional cap on total context records before splitting.
    model_id : str
        Bedrock model id for inference (or key in MODEL_MAP).
    judge_model_id : str
        Bedrock model id for rubric judging.
    region : str
        AWS region for Bedrock.
    max_tokens : int
        Max output tokens for inference calls.
    temperature : float
        Sampling temperature for inference.
    workers_infer : int
        Parallelism for inference.
    workers_eval : int
        Parallelism for evaluation / judging.
    """

    def __init__(
        self,
        grouped_path: str = "CL-bench-grouped.jsonl",
        raw_path: str | None = "CL-bench.jsonl",
        k_dev_contexts: int = 100,
        max_samples: int | None = None,
        model_id: str = "1",
        judge_model_id: str = "3",
        region: str | None = None,
        max_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        temperature: float = 0.7,
        workers_infer: int = 12,
        workers_eval: int = 16,
    ):
        self.grouped_path = grouped_path
        self.raw_path = raw_path
        self.k_dev_contexts = k_dev_contexts
        self.max_samples = max_samples
        self.model_id = MODEL_MAP.get(model_id, model_id)
        self.judge_model_id = MODEL_MAP.get(judge_model_id, judge_model_id)
        self.region = region or os.environ.get("BEDROCK_REGION", "us-west-2")
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.workers_infer = workers_infer
        self.workers_eval = workers_eval
        self.use_llm_skill_selection = False
        self.selector_model_id = self.model_id

        # Lazy-loaded caches
        self._grouped: list[dict] | None = None
        self._raw_message_lookup: dict[str, list] | None = None
        self._dev: list[dict] | None = None
        self._test: list[dict] | None = None

    # ── BenchmarkAdapter interface ────────────────────────────────────

    def get_tasks(self, split: str = "train", limit: int = 10) -> list[Task]:
        """Return CL-bench tasks.

        split="train"  -> dev (first k contexts)
        split="test" / "holdout" -> held-out (remaining contexts)
        """
        self._ensure_loaded()
        grouped = self._dev if split == "train" else self._test
        flat = self._flatten_grouped(grouped)
        tasks = []
        for rec, task_idx, task_obj in flat[:limit]:
            task_id = task_obj.get("task_id", f"{rec.get('context_id', '')}_{task_idx}")
            tasks.append(Task(
                id=task_id,
                input=self._build_task_input(rec, task_obj),
                metadata={
                    "context_id": rec.get("context_id"),
                    "task_id": task_id,
                    "task_idx": task_idx,
                    "context_category": rec.get("context_category", ""),
                    "sub_category": rec.get("sub_category", ""),
                    "context": rec.get("context", ""),
                    "task_text": task_obj.get("task", ""),
                    "system_prompt": rec.get("system_prompt", ""),
                    "rubrics": task_obj.get("rubrics", []),
                },
            ))
        return tasks

    def evaluate(self, task: Task, trajectory: Trajectory) -> Feedback:
        """Rubric-guided LLM judge evaluation."""
        rubrics = task.metadata.get("rubrics", [])
        if not rubrics:
            return Feedback(
                success=False,
                score=0.0,
                detail="No rubrics available for this task.",
                raw={"task_id": task.id},
            )

        client = _get_client(self.region)
        rubrics_text = _build_rubrics_text(rubrics)
        prompt = JUDGE_PROMPT_TEMPLATE.format(
            rubrics_text=rubrics_text,
            model_output=trajectory.output,
        )
        resp, err = _call_bedrock(
            client, self.judge_model_id, "", prompt,
            max_tokens=2048, temperature=0.7,
        )
        if err:
            return Feedback(
                success=False,
                score=0.0,
                detail=f"Judge API failed: {err}",
                raw={"task_id": task.id, "error": err},
            )

        parsed = _parse_json_object(resp)
        if parsed is None:
            return Feedback(
                success=False,
                score=0.0,
                detail=f"Judge returned unparseable output: {_truncate(resp, 500)}",
                raw={"task_id": task.id, "raw_judge": resp},
            )

        score = float(parsed.get("Overall Score", 0))
        rationale = parsed.get("Grading Rationale", "")
        req_status = parsed.get("List of Requirement Satisfaction Status", [])
        return Feedback(
            success=score >= 1.0,
            score=score,
            detail=f"Task {task.id}: {'PASS' if score >= 1.0 else 'FAIL'}\n{rationale}",
            raw={
                "task_id": task.id,
                "grading_rationale": rationale,
                "requirement_status": req_status,
                "score": score,
            },
        )

    # ── Inference (solve a task via Bedrock) ──────────────────────────

    def infer_one(
        self,
        task: Task,
        learned_skills: dict | None = None,
    ) -> Trajectory:
        """Run inference on a single CL-bench task, returning a Trajectory.

        This can be used as a lightweight "agent solve" when you don't need
        a full BaseAgent subclass.
        """
        client = _get_client(self.region)
        meta = task.metadata
        task_id = task.id
        context = meta.get("context", "")
        task_text = meta.get("task_text", "")
        system_prompt = meta.get("system_prompt", "")

        skill_block = ""
        if learned_skills:
            skill_block = build_skill_guidance(
                learned_skills, meta,
                use_llm_selection=self.use_llm_skill_selection,
                region=self.region,
                selector_model_id=self.selector_model_id,
            )

        # Try raw messages first (preserves original conversation history)
        raw_messages = self._get_raw_messages(task_id)
        if raw_messages:
            system_prompts, bedrock_messages = _convert_openai_messages_to_bedrock(
                raw_messages, extra_system_text=skill_block or None,
            )
            answer, err = _call_bedrock_converse(
                client, self.model_id, system_prompts, bedrock_messages,
                max_tokens=self.max_tokens, temperature=self.temperature,
            )
        else:
            user_text = f"Context:\n{context}\n\nTask:\n{task_text}"
            final_system = system_prompt
            if skill_block:
                final_system = (final_system + "\n\n" + skill_block).strip()
            answer, err = _call_bedrock(
                client, self.model_id, final_system, user_text,
                max_tokens=self.max_tokens, temperature=self.temperature,
            )

        if err:
            logger.warning("Inference failed for %s: %s", task_id, err)
            return Trajectory(task_id=task_id, output=f"[ERROR] {err}")

        return Trajectory(task_id=task_id, output=(answer or "").strip())

    # ── Batch inference + evaluation helpers ──────────────────────────

    def batch_infer(
        self,
        tasks: list[Task],
        learned_skills: dict | None = None,
    ) -> list[Trajectory]:
        """Parallel inference over a list of tasks."""
        def worker(task):
            # ensure each thread has its own client
            _init_worker(self.region)
            return self.infer_one(task, learned_skills)

        return self._run_parallel(
            tasks, worker, self.workers_infer, "CL-Bench-Infer",
        )

    def batch_evaluate(
        self,
        tasks: list[Task],
        trajectories: list[Trajectory],
    ) -> list[Feedback]:
        """Parallel rubric-judging over task/trajectory pairs."""
        pairs = list(zip(tasks, trajectories))

        def worker(pair):
            _init_worker(self.region)
            return self.evaluate(pair[0], pair[1])

        return self._run_parallel(
            pairs, worker, self.workers_eval, "CL-Bench-Judge",
        )

    # ── Skill distillation pipeline ───────────────────────────────────

    def distill_skills(
        self,
        graded_items: list[dict],
        batch_size: int = 20,
        max_output_chars: int = 1200,
        max_rubrics: int = 8,
    ) -> dict:
        """Distill transferable skills from graded dev samples.

        Parameters
        ----------
        graded_items : list[dict]
            Each item must have at minimum: task_id, rubrics, model_output,
            score, grading_rationale, requirement_status, and metadata with
            context_category / sub_category.
        """
        client = _get_client(self.region)
        normalized = [
            self._normalize_for_distill(x, max_output_chars, max_rubrics)
            for x in graded_items if x.get("rubrics")
        ]
        sampled = self._dedupe_distill_samples(normalized)
        if not sampled:
            raise RuntimeError("No graded samples available for distillation")

        # Prioritize near-miss samples: put them first so they appear in
        # early batches and get the most attention from the distiller.
        near_miss = [s for s in sampled if s.get("is_near_miss")]
        other_failed = [s for s in sampled if not s.get("is_near_miss") and s.get("score", 0) < 1]
        passed = [s for s in sampled if s.get("score", 0) >= 1]
        sampled = near_miss + other_failed + passed

        # Build error analysis summary across all samples
        error_analysis = self._build_error_analysis(sampled)
        logger.info(
            "Distill input: %d samples (%d near-miss, %d other-failed, %d passed). "
            "Top missed rubric patterns: %s",
            len(sampled), len(near_miss), len(other_failed), len(passed),
            [p["pattern"][:60] for p in error_analysis.get("top_missed_rubrics", [])[:3]],
        )

        # Batch distillation
        batch_outputs = []
        for start in range(0, len(sampled), batch_size):
            batch = sampled[start:start + batch_size]
            user_text = json.dumps({
                "samples": batch,
                "error_analysis_summary": error_analysis,
            }, ensure_ascii=False, indent=2)
            parsed = None
            # Retry up to 3 times; on failure, halve the batch to reduce output size
            for retry in range(3):
                cur_batch = batch if retry == 0 else batch[:max(1, len(batch) // 2)]
                cur_text = user_text if retry == 0 else json.dumps(
                    {"samples": cur_batch}, ensure_ascii=False, indent=2)
                resp, err = _call_bedrock(
                    client, self.model_id, BATCH_DISTILL_SYSTEM, cur_text,
                    max_tokens=16384, temperature=0.7,
                )
                if err:
                    logger.warning(
                        "Batch distillation attempt %d failed (batch %d-%d): %s",
                        retry + 1, start, start + len(cur_batch), err,
                    )
                    continue
                parsed = _parse_json_object(resp)
                if parsed is not None:
                    break
                logger.warning(
                    "Batch distillation attempt %d returned invalid JSON "
                    "(batch %d-%d, resp length=%d). Last 300 chars: %s",
                    retry + 1, start, start + len(cur_batch),
                    len(resp or ""), (resp or "")[-300:],
                )
            if parsed is None:
                logger.warning(
                    "Skipping batch %d-%d after 3 failed attempts",
                    start, start + len(batch),
                )
                continue
            batch_outputs.append(parsed)

        if not batch_outputs:
            raise RuntimeError("All batch distillation batches failed to produce valid JSON")

        # Final synthesis
        final_payload = {
            "sample_count": len(sampled),
            "near_miss_count": len(near_miss),
            "error_analysis_summary": error_analysis,
            "batch_summaries": batch_outputs,
        }
        learned = None
        for retry in range(3):
            resp, err = _call_bedrock(
                client, self.model_id, FINAL_DISTILL_SYSTEM,
                json.dumps(final_payload, ensure_ascii=False, indent=2),
                max_tokens=16384, temperature=0.7,
            )
            if err:
                logger.warning("Final distillation attempt %d failed: %s", retry + 1, err)
                continue
            learned = _parse_json_object(resp)
            if learned is not None:
                break
            logger.warning(
                "Final distillation attempt %d returned invalid JSON (resp length=%d). "
                "Last 300 chars: %s",
                retry + 1, len(resp or ""), (resp or "")[-300:],
            )
        if learned is None:
            raise RuntimeError("Final distillation returned invalid JSON after 3 attempts")

        learned.setdefault("metadata", {})
        learned["metadata"].update({
            "distilled_from_samples": len(sampled),
            "distilled_from_unique_contexts": len(
                {s.get("context_id") for s in sampled if s.get("context_id")}
            ),
            "batch_size": batch_size,
        })
        learned["sampled_task_ids"] = [s["task_id"] for s in sampled]
        return learned

    def mutate_skills(
        self,
        base_skills: dict,
        round_history: list[dict],
        candidate_count: int = 3,
        seed: int = 0,
    ) -> list[dict]:
        """Generate skill variants via LLM-driven mutation.

        Returns a list of candidate dicts:
        ``[{"candidate_id": ..., "skills": ..., "source": ...}, ...]``
        """
        client = _get_client(self.region)
        base_skills = _ensure_skill_schema(base_skills)
        candidates = [{"candidate_id": "base", "skills": base_skills, "source": "distilled_base"}]

        for idx in range(max(0, candidate_count - 1)):
            payload = {
                "seed": seed + idx,
                "base_skills": base_skills,
                "round_history": round_history,
                "instruction": "Create one conservative but meaningfully different variant.",
            }
            resp, err = _call_bedrock(
                client, self.model_id, MUTATE_SKILLS_SYSTEM,
                json.dumps(payload, ensure_ascii=False, indent=2),
                max_tokens=8192, temperature=0.7,
            )
            if err:
                logger.warning("Skill mutation %d failed: %s", idx + 1, err)
                continue
            parsed = _parse_json_object(resp)
            if parsed is None:
                logger.warning("Skill mutation %d returned invalid JSON", idx + 1)
                continue
            candidates.append({
                "candidate_id": f"mutant_{idx + 1}",
                "skills": _ensure_skill_schema(parsed),
                "source": "mutated",
            })
        return candidates

    def should_continue(
        self,
        round_history: list[dict],
        learned_skills: dict,
        round_idx: int,
        max_rounds: int,
    ) -> tuple[bool, str]:
        """Ask the LLM whether another distillation round is worthwhile."""
        if round_idx >= max_rounds:
            return False, "Reached max rounds"
        client = _get_client(self.region)
        payload = {
            "current_round": round_idx,
            "max_rounds": max_rounds,
            "round_history": round_history,
            "learned_skills_summary": self._summarize_skills(learned_skills),
        }
        resp, err = _call_bedrock(
            client, self.model_id, ROUND_DECIDER_SYSTEM,
            json.dumps(payload, ensure_ascii=False, indent=2),
            max_tokens=512, temperature=0.7,
        )
        if err:
            return False, f"Round decider error: {err}"
        parsed = _parse_json_object(resp)
        if not parsed or "continue" not in parsed:
            return False, "Round decider returned invalid output"
        return bool(parsed.get("continue")), str(parsed.get("reason", "")).strip()

    def online_evolve_skills(
        self,
        current_skills: dict | None,
        new_graded: list[dict],
        max_output_chars: int = 1200,
    ) -> dict:
        """Incrementally update skills using test-time feedback.

        New design: each task independently proposes skill changes in
        parallel, then all proposals are aggregated into a single update.

        Only pass/fail + model output are available (no rubric details).
        """
        current_skills = _ensure_skill_schema(current_skills)

        # Build per-task items (only pass/fail signal)
        task_items = []
        for x in new_graded:
            score = x.get("score", 0)
            task_items.append({
                "task_id": x.get("task_id"),
                "context_category": (x.get("metadata") or {}).get("context_category", ""),
                "sub_category": (x.get("metadata") or {}).get("sub_category", ""),
                "passed": score >= 1,
                "model_output": _truncate(x.get("model_output", ""), max_output_chars),
            })
        if not task_items:
            return current_skills

        # Phase 1: Parallel per-task proposals
        skills_json = json.dumps(current_skills, ensure_ascii=False, indent=2)

        def propose_one(item):
            _init_worker(self.region)
            client = _get_client(self.region)
            payload = json.dumps({
                "current_skills": current_skills,
                "task_result": item,
            }, ensure_ascii=False, indent=2)
            resp, err = _call_bedrock(
                client, self.model_id, ONLINE_PROPOSE_SYSTEM, payload,
                max_tokens=4096, temperature=0.7,
            )
            if err:
                logger.warning("Proposal failed for %s: %s", item.get("task_id"), err)
                return None
            parsed = _parse_json_object(resp)
            if parsed is None:
                logger.warning(
                    "Proposal returned invalid JSON for %s. Raw (last 300): %s",
                    item.get("task_id"), (resp or "")[-300:]
                )
                return None
            return parsed

        proposals = self._run_parallel(
            task_items, propose_one, self.workers_eval, "Online-Propose",
        )
        valid_proposals = [p for p in proposals if p and p.get("proposals")]
        logger.info(
            "Online proposals: %d/%d tasks produced valid proposals",
            len(valid_proposals), len(task_items),
        )

        if not valid_proposals:
            logger.warning("No valid proposals — keeping current skills")
            return current_skills

        # Phase 2: Aggregate all proposals into a single skill update
        n_passed = sum(1 for x in task_items if x["passed"])
        agg_payload = json.dumps({
            "current_skills": current_skills,
            "proposals": valid_proposals,
            "stats": {
                "total_tasks": len(task_items),
                "passed": n_passed,
                "failed": len(task_items) - n_passed,
                "pass_rate": n_passed / len(task_items) if task_items else 0.0,
                "proposals_received": len(valid_proposals),
            },
        }, ensure_ascii=False, indent=2)

        client = _get_client(self.region)
        resp, err = _call_bedrock(
            client, self.model_id, ONLINE_AGGREGATE_SYSTEM, agg_payload,
            max_tokens=16384, temperature=0.7,
        )
        if err:
            logger.warning("Online aggregation failed: %s — keeping current skills", err)
            return current_skills

        parsed = _parse_json_object(resp)
        if parsed is None:
            logger.warning(
                "Online aggregation returned invalid JSON — keeping current skills. "
                "Raw response (last 500 chars): %s", (resp or "")[-500:]
            )
            return current_skills

        return _ensure_skill_schema(parsed)

    def run_full_pipeline(
        self,
        output_dir: str = "outputs/cl_bench_pipeline",
        max_rounds: int = 2,
        distill_batch_size: int = 20,
        candidate_count: int = 3,
        seed: int = 0,
        enable_offline_distill: bool = True,
        enable_online_evolution: bool = False,
        online_batch_size: int = 20,
        use_llm_skill_selection: bool = False,
    ) -> DistillationResult:
        """Run the complete pipeline end-to-end.

        Two independently controllable modules:

        1. **Offline distillation** (``enable_offline_distill``):
           Distill transferable skills from labeled dev data through
           multiple rounds of distill -> mutate -> select.  The best
           skill library is then applied to the held-out set.

        2. **Online evolution** (``enable_online_evolution``):
           During held-out evaluation, process tasks in batches.
           After each batch the LLM judge scores the responses,
           and the feedback is used to incrementally refine the
           skill library before the next batch.

        Both modules can be enabled simultaneously (offline seeds the
        initial skills, online refines them at test time) or independently.

        Returns a ``DistillationResult`` with the final learned skills,
        round history, and dev / held-out accuracy.
        """
        os.makedirs(output_dir, exist_ok=True)
        self._ensure_loaded()
        self.use_llm_skill_selection = use_llm_skill_selection

        dev_tasks = self.get_tasks(split="train", limit=999999)
        test_tasks = self.get_tasks(split="test", limit=999999)
        logger.info(
            "Pipeline: %d dev tasks, %d held-out tasks  "
            "[offline_distill=%s, online_evolution=%s, llm_skill_select=%s]",
            len(dev_tasks), len(test_tasks),
            enable_offline_distill, enable_online_evolution, use_llm_skill_selection,
        )

        # -- Round 0: baseline on dev --
        dev_trajs = self.batch_infer(dev_tasks, learned_skills=None)
        dev_fb = self.batch_evaluate(dev_tasks, dev_trajs)
        dev_graded = self._feedback_to_graded(dev_tasks, dev_trajs, dev_fb)
        self._save_graded(dev_graded, os.path.join(output_dir, "dev_round0_baseline_graded.jsonl"))

        dev_score = sum(1 for fb in dev_fb if fb.success)
        round_history: list[dict] = [{
            "round": 0,
            "type": "baseline",
            "dev_score_1": dev_score,
            "dev_total": len(dev_fb),
            "dev_rate": dev_score / len(dev_fb) if dev_fb else 0,
        }]

        learned_skills = None
        current_dev_graded = dev_graded

        # ==================================================================
        # Module 1: Offline distillation (from labeled dev data)
        # ==================================================================
        if enable_offline_distill:
            for round_idx in range(1, max_rounds + 1):
                logger.info("Starting offline distillation round %d", round_idx)

                distilled = self.distill_skills(current_dev_graded, batch_size=distill_batch_size)
                distilled["metadata"]["round"] = round_idx
                self._save_json(distilled, os.path.join(output_dir, f"learned_skills_round{round_idx}_base.json"))

                candidates = self.mutate_skills(
                    distilled, round_history,
                    candidate_count=candidate_count,
                    seed=seed + round_idx * 1000,
                )

                best_score_rate = -1.0
                best_skills = None
                best_graded = None
                candidate_summaries = []

                for cand in candidates:
                    cand_id = cand["candidate_id"]
                    skills_doc = cand["skills"]
                    self._save_json(
                        skills_doc,
                        os.path.join(output_dir, f"learned_skills_round{round_idx}_{cand_id}.json"),
                    )

                    cand_trajs = self.batch_infer(dev_tasks, learned_skills=skills_doc)
                    cand_fb = self.batch_evaluate(dev_tasks, cand_trajs)
                    cand_graded = self._feedback_to_graded(dev_tasks, cand_trajs, cand_fb)
                    self._save_graded(
                        cand_graded,
                        os.path.join(output_dir, f"dev_round{round_idx}_{cand_id}_graded.jsonl"),
                    )

                    cand_score = sum(1 for fb in cand_fb if fb.success)
                    cand_rate = cand_score / len(cand_fb) if cand_fb else 0.0
                    candidate_summaries.append({
                        "candidate_id": cand_id,
                        "source": cand["source"],
                        "score_rate": cand_rate,
                        "score_1": cand_score,
                        "total": len(cand_fb),
                    })

                    if cand_rate > best_score_rate:
                        best_score_rate = cand_rate
                        best_skills = skills_doc
                        best_graded = cand_graded

                learned_skills = best_skills
                current_dev_graded = best_graded
                round_history.append({
                    "round": round_idx,
                    "type": "offline_distill",
                    "dev_score_1": int(best_score_rate * len(dev_fb)),
                    "dev_total": len(dev_fb),
                    "dev_rate": best_score_rate,
                    "candidate_results": candidate_summaries,
                })

                go_next, reason = self.should_continue(
                    round_history, learned_skills, round_idx, max_rounds,
                )
                round_history[-1]["continue_decision"] = go_next
                round_history[-1]["continue_reason"] = reason
                if not go_next:
                    logger.info("Stopping after round %d: %s", round_idx, reason)
                    break

            if learned_skills is None:
                learned_skills = self.distill_skills(current_dev_graded, batch_size=distill_batch_size)

        final_skills = _ensure_skill_schema(learned_skills) if learned_skills else _ensure_skill_schema(None)
        self._save_json(final_skills, os.path.join(output_dir, "learned_skills_pre_online.json"))

        # ==================================================================
        # Module 2: Online evolution (test-time feedback-driven refinement)
        # ==================================================================
        if enable_online_evolution and test_tasks:
            logger.info(
                "Starting online evolution on %d held-out tasks (batch_size=%d)",
                len(test_tasks), online_batch_size,
            )
            all_heldout_trajs: list[Trajectory] = []
            all_heldout_fb: list[Feedback] = []
            online_skills = copy.deepcopy(final_skills)
            online_history: list[dict] = []
            accumulated_graded: list[dict] = []

            for batch_start in range(0, len(test_tasks), online_batch_size):
                batch_tasks = test_tasks[batch_start:batch_start + online_batch_size]
                batch_idx = batch_start // online_batch_size
                logger.info(
                    "Online batch %d: tasks %d-%d (skills: %d global, %d categories)",
                    batch_idx, batch_start, batch_start + len(batch_tasks),
                    len(online_skills.get("global_skills", [])),
                    len(online_skills.get("category_skills", {})),
                )

                # Infer with current skills
                batch_trajs = self.batch_infer(batch_tasks, learned_skills=online_skills)
                batch_fb = self.batch_evaluate(batch_tasks, batch_trajs)
                all_heldout_trajs.extend(batch_trajs)
                all_heldout_fb.extend(batch_fb)

                # Collect graded items from this batch
                batch_graded = self._feedback_to_graded(batch_tasks, batch_trajs, batch_fb)
                accumulated_graded.extend(batch_graded)
                self._save_graded(
                    batch_graded,
                    os.path.join(output_dir, f"online_batch{batch_idx}_graded.jsonl"),
                )

                batch_score = sum(1 for fb in batch_fb if fb.success)
                batch_rate = batch_score / len(batch_fb) if batch_fb else 0.0
                online_history.append({
                    "batch": batch_idx,
                    "tasks_start": batch_start,
                    "tasks_end": batch_start + len(batch_tasks),
                    "score_1": batch_score,
                    "total": len(batch_fb),
                    "rate": batch_rate,
                })
                logger.info(
                    "Online batch %d: %.4f (%d/%d)",
                    batch_idx, batch_rate, batch_score, len(batch_fb),
                )

                # Evolve skills using feedback from this batch
                online_skills = self.online_evolve_skills(
                    online_skills, batch_graded,
                )
                self._save_json(
                    online_skills,
                    os.path.join(output_dir, f"online_skills_after_batch{batch_idx}.json"),
                )

            final_skills = online_skills
            heldout_trajs = all_heldout_trajs
            heldout_fb = all_heldout_fb
            heldout_graded = self._feedback_to_graded(test_tasks, heldout_trajs, heldout_fb)

            round_history.append({
                "type": "online_evolution",
                "online_batches": len(online_history),
                "online_batch_size": online_batch_size,
                "online_history": online_history,
            })
        else:
            # -- Standard held-out evaluation (no online evolution) --
            heldout_trajs = self.batch_infer(test_tasks, learned_skills=final_skills)
            heldout_fb = self.batch_evaluate(test_tasks, heldout_trajs)
            heldout_graded = self._feedback_to_graded(test_tasks, heldout_trajs, heldout_fb)

        self._save_graded(heldout_graded, os.path.join(output_dir, "heldout_graded.jsonl"))
        self._save_json(final_skills, os.path.join(output_dir, "learned_skills.json"))

        heldout_score = sum(1 for fb in heldout_fb if fb.success)
        final_dev_score = sum(1 for g in current_dev_graded if g.get("score", 0) >= 1)

        summary = {
            "grouped_path": self.grouped_path,
            "k_dev_contexts": self.k_dev_contexts,
            "dev_tasks": len(dev_tasks),
            "heldout_tasks": len(test_tasks),
            "enable_offline_distill": enable_offline_distill,
            "enable_online_evolution": enable_online_evolution,
            "rounds_run": max(0, len([r for r in round_history if r.get("type") == "offline_distill"])),
            "round_history": round_history,
            "final_dev_rate": final_dev_score / len(dev_tasks) if dev_tasks else 0,
            "heldout_rate": heldout_score / len(test_tasks) if test_tasks else 0,
        }
        self._save_json(summary, os.path.join(output_dir, "summary.json"))
        logger.info(
            "Pipeline done. Dev rate: %.4f  Held-out rate: %.4f",
            summary["final_dev_rate"], summary["heldout_rate"],
        )

        return DistillationResult(
            learned_skills=final_skills,
            round_history=round_history,
            dev_rate=summary["final_dev_rate"],
            heldout_rate=summary["heldout_rate"],
            summary=summary,
        )

    # ── Conversion: Observations -> graded items for distillation ─────

    @staticmethod
    def observations_to_graded(observations: list) -> list[dict]:
        """Convert A-EVOLVE-V2 Observation objects to graded item dicts
        suitable for ``distill_skills``.

        This bridges the evolution loop output into the distillation
        pipeline input.
        """
        from ..types import Observation
        graded = []
        for obs in observations:
            if not isinstance(obs, Observation):
                continue
            task = obs.task
            traj = obs.trajectory
            fb = obs.feedback
            graded.append({
                "task_id": task.id,
                "context_id": task.metadata.get("context_id"),
                "model_output": traj.output,
                "score": fb.score,
                "grading_rationale": fb.detail,
                "requirement_status": fb.raw.get("requirement_status", []),
                "rubrics": task.metadata.get("rubrics", []),
                "metadata": {
                    "task_id": task.id,
                    "context_id": task.metadata.get("context_id"),
                    "context_category": task.metadata.get("context_category", ""),
                    "sub_category": task.metadata.get("sub_category", ""),
                },
            })
        return graded

    # ── Internal helpers ──────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        if self._grouped is not None:
            return
        self._grouped = _load_jsonl(self.grouped_path)

        # Build raw message lookup
        self._raw_message_lookup = {}
        if self.raw_path and os.path.exists(self.raw_path):
            for item in _load_jsonl(self.raw_path):
                tid = (item.get("metadata") or {}).get("task_id") or item.get("task_id")
                msgs = item.get("messages", [])
                if tid and msgs:
                    self._raw_message_lookup[tid] = msgs
            logger.info(
                "Loaded raw message lookup for %d task_ids",
                len(self._raw_message_lookup),
            )

        # Split dev / test by context index
        data = self._grouped
        if self.max_samples is not None:
            data = data[:max(0, self.max_samples)]
        self._dev = [copy.deepcopy(r) for r in data[:self.k_dev_contexts]]
        self._test = [copy.deepcopy(r) for r in data[self.k_dev_contexts:]]
        logger.info(
            "Split: %d dev contexts, %d held-out contexts",
            len(self._dev), len(self._test),
        )

    def _get_raw_messages(self, task_id: str) -> list[dict] | None:
        self._ensure_loaded()
        if self._raw_message_lookup:
            return self._raw_message_lookup.get(task_id)
        return None

    @staticmethod
    def _flatten_grouped(data: list[dict]) -> list[tuple[dict, int, dict]]:
        flat = []
        for rec in data:
            for idx, task_obj in enumerate(rec.get("tasks", [])):
                flat.append((rec, idx, task_obj))
        return flat

    @staticmethod
    def _build_task_input(rec: dict, task_obj: dict) -> str:
        context = rec.get("context", "")
        task = task_obj.get("task", "")
        return f"Context:\n{context}\n\nTask:\n{task}"

    def _run_parallel(self, items, worker_fn, workers, desc):
        from tqdm import tqdm
        results = []
        if workers <= 1:
            _init_worker(self.region)
            for item in tqdm(items, desc=desc, ncols=80):
                results.append(worker_fn(item))
            return results
        with ThreadPoolExecutor(
            max_workers=workers,
            initializer=lambda: _init_worker(self.region),
        ) as ex:
            futures = {ex.submit(worker_fn, item): i for i, item in enumerate(items)}
            result_map = {}
            with tqdm(total=len(items), desc=desc, ncols=80) as pbar:
                for future in as_completed(futures):
                    result_map[futures[future]] = future.result()
                    pbar.update(1)
            # Preserve original order
            for i in range(len(items)):
                results.append(result_map[i])
        return results

    @staticmethod
    def _normalize_for_distill(item: dict, max_output_chars: int, max_rubrics: int) -> dict:
        status = item.get("requirement_status", [])
        if not isinstance(status, list):
            status = []

        # Compute rubric-level pass/fail breakdown
        rubric_passed = 0
        rubric_failed = 0
        rubric_total = 0
        failed_rubric_indices: list[int] = []
        for i, s in enumerate(status[:max_rubrics]):
            s_lower = str(s).strip().lower()
            if s_lower == "n/a":
                continue
            rubric_total += 1
            if s_lower == "yes":
                rubric_passed += 1
            else:
                rubric_failed += 1
                failed_rubric_indices.append(i)

        # Tag near-miss: failed overall but missed only 1-2 rubrics
        score = item.get("score", 0)
        is_near_miss = (
            score < 1
            and rubric_total > 0
            and rubric_failed <= 2
        )

        # Build per-rubric detail: pair each rubric text with its status
        rubrics_raw = item.get("rubrics", [])[:max_rubrics]
        rubric_details = []
        for i, rubric in enumerate(rubrics_raw):
            text = rubric.get("rubric_criteria", "").strip() if isinstance(rubric, dict) else str(rubric).strip()
            s_val = str(status[i]).strip().lower() if i < len(status) else "unknown"
            rubric_details.append({"index": i, "rubric": text, "status": s_val})

        return {
            "task_id": (
                item.get("task_id")
                or (item.get("metadata") or {}).get("task_id")
                or item.get("idx")
            ),
            "context_id": (
                item.get("context_id")
                or (item.get("metadata") or {}).get("context_id")
            ),
            "context_category": (item.get("metadata") or {}).get("context_category", ""),
            "sub_category": (item.get("metadata") or {}).get("sub_category", ""),
            "score": score,
            "is_near_miss": is_near_miss,
            "rubric_breakdown": {
                "passed": rubric_passed,
                "failed": rubric_failed,
                "total": rubric_total,
                "failed_indices": failed_rubric_indices,
            },
            "rubric_details": rubric_details,
            "task_text": "",
            "rubrics": _build_rubrics_text(item.get("rubrics", []), max_items=max_rubrics),
            "model_output": _truncate(item.get("model_output", ""), max_output_chars),
            "grading_rationale": _truncate(item.get("grading_rationale", ""), 1500),
            "requirement_status": status[:max_rubrics],
        }

    @staticmethod
    def _build_error_analysis(samples: list[dict]) -> dict:
        """Aggregate rubric-level error patterns across all samples."""
        from collections import Counter
        failed_rubric_texts: list[str] = []
        category_fail_counts: Counter = Counter()
        category_near_miss_counts: Counter = Counter()
        total_failed = 0
        total_near_miss = 0
        total_passed = 0

        for s in samples:
            score = s.get("score", 0)
            cat = s.get("context_category", "") or ""
            sub = s.get("sub_category", "") or ""
            cat_key = f"{cat} / {sub}" if cat and sub else cat or sub or "unknown"

            if score >= 1:
                total_passed += 1
            else:
                total_failed += 1
                if s.get("is_near_miss"):
                    total_near_miss += 1
                    category_near_miss_counts[cat_key] += 1
                category_fail_counts[cat_key] += 1

                # Collect failed rubric texts
                for detail in s.get("rubric_details", []):
                    if detail.get("status") == "no" and detail.get("rubric"):
                        failed_rubric_texts.append(detail["rubric"])

        # Find most commonly failed rubric patterns (by exact text)
        rubric_counter = Counter(failed_rubric_texts)
        top_missed = [
            {"pattern": text, "count": count}
            for text, count in rubric_counter.most_common(15)
        ]

        return {
            "total_samples": len(samples),
            "total_passed": total_passed,
            "total_failed": total_failed,
            "total_near_miss": total_near_miss,
            "top_missed_rubrics": top_missed,
            "categories_by_failure": category_fail_counts.most_common(10),
            "categories_by_near_miss": category_near_miss_counts.most_common(10),
        }

    @staticmethod
    def _dedupe_distill_samples(samples: list[dict]) -> list[dict]:
        by_task: dict[str, dict] = {}
        for s in samples:
            tid = s.get("task_id")
            if tid:
                by_task[tid] = s
        return [by_task[k] for k in sorted(by_task)]

    @staticmethod
    def _summarize_skills(skills_doc: dict, max_skills: int = 10, max_global: int = 6, max_category: int = 2) -> dict:
        if not skills_doc:
            return {}
        # V3: flat skills array
        skills = skills_doc.get("skills")
        if isinstance(skills, list) and skills:
            return {
                "skills_count": len(skills),
                "skills_preview": [
                    {
                        "skill_name": s.get("skill_name", ""),
                        "when_to_use": s.get("when_to_use", ""),
                        "priority": s.get("priority", ""),
                    }
                    for s in skills[:max_skills]
                ],
            }
        # V2: guidance string
        guidance = skills_doc.get("guidance")
        if guidance is not None:
            text = _guidance_to_text(guidance)
            return {"guidance_preview": _truncate(text, 2000)}
        # Legacy format
        out: dict[str, Any] = {"global_skills": [], "category_skills": {}}
        for skill in skills_doc.get("global_skills", [])[:max_global]:
            out["global_skills"].append({
                "skill_name": skill.get("skill_name", ""),
                "when_to_use": skill.get("when_to_use", ""),
                "action_rule": skill.get("action_rule", ""),
            })
        for key, skills_list in list(skills_doc.get("category_skills", {}).items())[:8]:
            out["category_skills"][key] = [
                {
                    "skill_name": s.get("skill_name", ""),
                    "when_to_use": s.get("when_to_use", ""),
                    "action_rule": s.get("action_rule", ""),
                }
                for s in skills_list[:max_category]
            ]
        return out

    @staticmethod
    def _feedback_to_graded(
        tasks: list[Task],
        trajectories: list[Trajectory],
        feedbacks: list[Feedback],
    ) -> list[dict]:
        graded = []
        for task, traj, fb in zip(tasks, trajectories, feedbacks):
            graded.append({
                "task_id": task.id,
                "context_id": task.metadata.get("context_id"),
                "model_output": traj.output,
                "score": fb.score,
                "grading_rationale": fb.detail,
                "requirement_status": fb.raw.get("requirement_status", []),
                "rubrics": task.metadata.get("rubrics", []),
                "metadata": {
                    "task_id": task.id,
                    "context_id": task.metadata.get("context_id"),
                    "context_category": task.metadata.get("context_category", ""),
                    "sub_category": task.metadata.get("sub_category", ""),
                },
            })
        return graded

    @staticmethod
    def _save_graded(items: list[dict], path: str) -> None:
        _write_jsonl(items, path)

    @staticmethod
    def _save_json(obj: dict, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
