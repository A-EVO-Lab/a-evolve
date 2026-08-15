"""Minimal CL-Bench loading and rubric-evaluation adapter for EVO-HARNESS."""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import threading
import time

from ..types import Feedback, Task, Trajectory
from .base import BenchmarkAdapter

logger = logging.getLogger(__name__)

BEDROCK_MAX_OUTPUT_TOKENS_CAP = 64000
DEFAULT_MAX_OUTPUT_TOKENS = 64000

MODEL_MAP = {
    "1": "us.anthropic.claude-opus-4-6-v1",
    "2": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "3": "us.anthropic.claude-opus-4-5-20251101-v1:0",
}
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
    inference_config = {"maxTokens": min(max_tokens, BEDROCK_MAX_OUTPUT_TOKENS_CAP)}
    if "opus-4-7" not in model_id:
        inference_config["temperature"] = temperature
    req = {
        "modelId": model_id,
        "messages": [{"role": "user", "content": [{"text": user_text}]}],
        "inferenceConfig": inference_config,
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
    inference_config = {"maxTokens": min(max_tokens, BEDROCK_MAX_OUTPUT_TOKENS_CAP)}
    if "opus-4-7" not in model_id:
        inference_config["temperature"] = temperature
    req = {
        "modelId": model_id,
        "messages": messages,
        "inferenceConfig": inference_config,
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


class CLBenchBenchmark(BenchmarkAdapter):
    """Load CL-Bench tasks and evaluate responses with its rubric judge.

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
