"""MCP-Atlas benchmark adapter.

Loads tasks from the HuggingFace dataset ``ScaleAI/MCP-Atlas`` and evaluates
agent trajectories by comparing output against expected results.  Follows the
same split / cache pattern as :class:`SweVerifiedBenchmark`.

Now supports both LiteLLM (official method) and AWS Bedrock for evaluation.
LiteLLM enables using Gemini 2.5 Pro (official default) or any other model.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from ...agents.mcp.key_registry import KeyRegistry
from ...agents.mcp.task_filter import filter_tasks_by_keys
from ...types import Feedback, Task, Trajectory
from ..base import BenchmarkAdapter

logger = logging.getLogger(__name__)


class McpAtlasBenchmark(BenchmarkAdapter):
    """MCP-Atlas benchmark adapter.

    Loads tasks from HuggingFace ``ScaleAI/MCP-Atlas`` (or any compatible
    dataset) and evaluates trajectories via whitespace-normalised string
    comparison against expected output.
    """

    @property
    def feedback_capability(self):
        from ...algorithms.unified.types import FeedbackCapability
        return FeedbackCapability(
            has_pass_fail=True,
            has_per_claim=True,
            judge_available=True,
        )

    def __init__(
        self,
        dataset_name: str = "ScaleAI/MCP-Atlas",
        shuffle: bool = True,
        holdout_ratio: float = 0.2,
        eval_model_id: str = "gemini/gemini-2.5-pro",  # Official default
        eval_region: str = "us-west-2",
        use_litellm: bool = True,  # Use LiteLLM by default (official method)
        concurrency: int = 5,  # Max concurrent evaluations (official default)
    ):
        """Initialize MCP-Atlas benchmark adapter.

        Args:
            dataset_name: HuggingFace dataset name
            shuffle: Whether to shuffle the dataset
            holdout_ratio: Ratio of data to hold out for validation
            eval_model_id: Model ID for evaluation judge. For LiteLLM, use format like
                "gemini/gemini-2.5-pro" (official), "claude-3-5-sonnet-20241022", "gpt-4o".
                For Bedrock, use ARN like "us.anthropic.claude-sonnet-4-20250514-v1:0".
            eval_region: AWS region (only for Bedrock)
            use_litellm: If True, use LiteLLM (official method, supports any provider).
                If False, fall back to AWS Bedrock. Env var EVAL_USE_LITELLM can override.
            concurrency: Max concurrent evaluation requests (official default: 5)
        """
        self.dataset_name = dataset_name
        self.shuffle = shuffle
        self.holdout_ratio = holdout_ratio
        self.eval_model_id = eval_model_id
        self.eval_region = eval_region
        self._cache: dict[str, list[dict]] = {}
        self._split_done = False

        # Check environment variable override
        env_use_litellm = os.getenv("EVAL_USE_LITELLM", "").lower()
        if env_use_litellm in ("true", "1", "yes"):
            use_litellm = True
        elif env_use_litellm in ("false", "0", "no"):
            use_litellm = False

        self.use_litellm = use_litellm
        self.concurrency = concurrency
        self._bedrock_client = None

        if self.use_litellm:
            logger.info(f"Using LiteLLM with model: {self.eval_model_id} (official method)")
        else:
            logger.info(f"Using AWS Bedrock with model: {self.eval_model_id}")

    # ── Public API ───────────────────────────────────────────────────

    def get_tasks(self, split: str = "train", limit: int = 10, key_registry: KeyRegistry | None = None) -> list[Task]:
        """Return up to *limit* Task objects from the requested split."""
        rows = self._load_split(split)
        tasks: list[Task] = []
        for row in rows[:limit]:
            # MCP-Atlas HF dataset uses uppercase column names:
            #   TASK, ENABLED_TOOLS, PROMPT, GTFA_CLAIMS, TRAJECTORY
            task_id = row.get("TASK") or row.get("task_id", "")
            prompt = row.get("PROMPT") or row.get("prompt", row.get("task_description", ""))
            enabled_tools = row.get("ENABLED_TOOLS", "[]")
            if isinstance(enabled_tools, str):
                import json as _json
                enabled_tools = _json.loads(enabled_tools)
            gtfa_claims = row.get("GTFA_CLAIMS", "")

            # Derive unique MCP server names from tool prefixes
            # Tool names follow the pattern: servername_toolname
            server_names = sorted({t.rsplit("_", 1)[0] for t in enabled_tools if "_" in t})

            tasks.append(Task(
                id=task_id,
                input=prompt,
                metadata={
                    "task_id": task_id,
                    "enabled_tools": enabled_tools,
                    "mcp_server_names": server_names,
                    "mcp_server_config": {},  # populated at runtime by agent
                    "expected_output": gtfa_claims,
                    "difficulty": row.get("difficulty", ""),
                    "category": row.get("category", ""),
                },
            ))
        if key_registry is not None:
            tasks, _filtered_out = filter_tasks_by_keys(tasks, key_registry)
        return tasks

    def evaluate(self, task: Task, trajectory: Trajectory) -> Feedback:
        """Evaluate trajectory using LLM-as-judge coverage scoring.

        Uses the same prompt and scoring logic as the official MCP-Atlas
        evaluation (mcp_evals_scores.py). Each ground-truth claim is
        evaluated individually against the agent's output.

        Scoring: fulfilled=1.0, partially_fulfilled=0.5, not_fulfilled=0.0
        Final score = average across all claims.

        This method runs async evaluation in sync context for compatibility.
        """
        # Run async evaluation in sync context
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        return loop.run_until_complete(self.evaluate_async(task, trajectory))

    async def evaluate_async(self, task: Task, trajectory: Trajectory) -> Feedback:
        """Async version of evaluate() with concurrent claim evaluation.

        This matches the official MCP-Atlas evaluation method which evaluates
        claims concurrently for better performance.
        """
        # Check for missing_key failure in trajectory steps
        for step in trajectory.steps:
            if step.get("failure_reason") == "missing_key":
                return Feedback(
                    success=False,
                    score=0.0,
                    detail=f"Task {task.id}: missing API key",
                    raw={"task_id": task.id, "reason": "missing_key"},
                )

        actual = str(trajectory.output or "").strip()

        if not actual:
            return Feedback(
                success=False,
                score=0.0,
                detail=f"Empty output for task {task.id}",
                raw={"task_id": task.id, "reason": "empty_output"},
            )

        claims = self._extract_claims(task.metadata.get("expected_output", ""))
        if not claims:
            return Feedback(
                success=False,
                score=0.0,
                detail=f"No claims to evaluate for task {task.id}",
                raw={"task_id": task.id, "reason": "no_claims"},
            )

        # Evaluate all claims concurrently (official method)
        # Create a fresh semaphore per call — a shared instance breaks when
        # evaluate() is called from different threads (each with its own
        # event loop), because asyncio.Semaphore is bound to the loop that
        # was running when it was created.
        semaphore = asyncio.Semaphore(self.concurrency)

        async def eval_with_semaphore(claim: str) -> dict:
            async with semaphore:
                return await self._judge_single_claim_async(claim, actual)

        tasks = [eval_with_semaphore(claim) for claim in claims]
        results = await asyncio.gather(*tasks)

        # Aggregate results
        coverage_map = {"fulfilled": 1.0, "partially_fulfilled": 0.5, "not_fulfilled": 0.0}
        per_claim = []
        total_score = 0.0

        for result in results:
            outcome = result.get("coverage_outcome", "not_fulfilled")
            score = coverage_map.get(outcome, 0.0)
            total_score += score
            per_claim.append({
                "claim": result.get("claim_text", ""),
                "outcome": outcome,
                "score": score,
                "justification": result.get("justification", ""),
            })

        coverage_score = round(total_score / len(claims), 3)
        fulfilled = sum(1 for c in per_claim if c["score"] >= 1.0)
        partial = sum(1 for c in per_claim if c["score"] == 0.5)

        return Feedback(
            success=coverage_score >= 0.75,
            score=coverage_score,
            detail=(
                f"Task {task.id}: coverage={coverage_score:.3f} "
                f"({fulfilled} fulfilled, {partial} partial, "
                f"{len(claims) - fulfilled - partial} not fulfilled "
                f"out of {len(claims)} claims)"
            ),
            raw={
                "task_id": task.id,
                "coverage_score": coverage_score,
                "per_claim": per_claim,
                "total_claims": len(claims),
                "fulfilled": fulfilled,
                "partial": partial,
            },
        )

    # ── LLM Judge helpers ───────────────────────────────────────────

    async def _judge_single_claim_async(self, claim: str, response: str) -> dict:
        """Async wrapper for claim evaluation. Routes to LiteLLM or Bedrock."""
        if self.use_litellm:
            return await self._judge_single_claim_litellm(claim, response)
        else:
            # Bedrock is synchronous, run in executor
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None, self._judge_single_claim_bedrock, claim, response
            )

    async def _judge_single_claim_litellm(self, claim: str, response: str) -> dict:
        """Evaluate one claim using LiteLLM (official method).

        Supports any model via LiteLLM's unified API:
        - Gemini: "gemini/gemini-2.5-pro" (official default)
        - OpenAI: "gpt-4o", "gpt-5.1"
        - Claude: "claude-3-5-sonnet-20241022"
        - And 100+ other models
        """
        import json as _json
        import re as _re

        try:
            import litellm
        except ImportError:
            logger.error(
                "LiteLLM not installed. Run: pip install litellm\n"
                "Or set use_litellm=False to use Bedrock"
            )
            return self._fallback_result(claim, "LiteLLM not installed")

        prompt = self._get_evaluation_prompt(claim, response)

        # Get API key from environment
        api_key = os.getenv("EVAL_LLM_API_KEY") or os.getenv("LLM_API_KEY")
        if not api_key:
            logger.warning(
                "No API key found. Set EVAL_LLM_API_KEY or LLM_API_KEY env var.\n"
                "Falling back to 'not_fulfilled' for claim evaluation."
            )
            return self._fallback_result(claim, "No API key")

        # Configure LiteLLM
        litellm.set_verbose = False

        max_retries = 6
        for attempt in range(max_retries):
            try:
                result = await litellm.acompletion(
                    model=self.eval_model_id,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                    temperature=0.0,
                    api_key=api_key,
                    api_base=os.getenv("EVAL_LLM_BASE_URL") or None,
                )

                content = result.choices[0].message.content
                parsed = _json.loads(content)
                return parsed

            except Exception as e:
                error_msg = str(e).lower()
                is_retryable = any(
                    x in error_msg for x in [
                        "rate limit", "quota", "timeout", "503", "502",
                        "throttl", "too many requests", "unavailable"
                    ]
                )

                if is_retryable and attempt < max_retries - 1:
                    wait_time = min(2 ** attempt, 30)
                    logger.info(
                        "LiteLLM API error (attempt %d/%d), retrying in %ds: %s",
                        attempt + 1, max_retries, wait_time, str(e)[:100]
                    )
                    await asyncio.sleep(wait_time)
                elif attempt == max_retries - 1:
                    logger.warning(
                        "LiteLLM evaluation failed after %d attempts for claim '%s': %s",
                        max_retries, claim[:60], e
                    )
                    return self._fallback_result(claim, f"Failed after {max_retries} retries")
                else:
                    raise

        return self._fallback_result(claim, "Max retries exceeded")

    def _get_bedrock_client(self):
        """Lazy-init a boto3 Bedrock Runtime client."""
        if self._bedrock_client is None:
            import boto3
            self._bedrock_client = boto3.client(
                "bedrock-runtime", region_name=self.eval_region
            )
        return self._bedrock_client

    def _get_evaluation_prompt(self, claim: str, response: str) -> str:
        """Get the evaluation prompt (same for LiteLLM and Bedrock)."""
        return (
            "You are evaluating how well a model's response addresses a "
            "specific expert-defined claim.\n"
            "SCORING CRITERIA:\n"
            "- fulfilled: Claim is completely and accurately addressed. "
            "The response covers all key details.\n"
            "- partially_fulfilled: Claim is partially addressed. "
            "The response covers some but not all key details.\n"
            "- not_fulfilled: Claim is not addressed. "
            "The response does not include any key details.\n"
            "NUMERICAL COMPARISON GUIDELINES:\n"
            "- For numerical values, use reasonable approximation thresholds:\n"
            "  * Exact match NOT required for decimals\n"
            "  * Values within 5% of the claimed number are considered matching\n"
            "  * For percentages, ±1 percentage points is acceptable\n"
            "  * Round to appropriate significant figures based on context\n"
            "- Consider the precision appropriate to the domain:\n"
            "  * Scientific measurements may need higher precision\n"
            "  * General statistics/estimates can have looser matching\n"
            "  * Financial figures should match to reasonable business precision "
            "(e.g., millions/billions don't need exact cents)\n"
            '- If a number is expressed differently but mathematically equivalent '
            '(e.g., "0.5" vs "50%" vs "half"), consider it a match\n'
            "CLAIM TO EVALUATE:\n"
            f"{claim}\n"
            "MODEL RESPONSE TO ANALYZE:\n"
            f"{response}\n"
            "INSTRUCTIONS:\n"
            "1. Determine if the core requirement of the claim is met in the response\n"
            "2. Check if all key components from the claim appear substantively "
            "in the response\n"
            "   - For numerical values, apply the flexible matching guidelines above\n"
            "   - Focus on whether the same magnitude and meaning are conveyed\n"
            "3. Assign the appropriate coverage_outcome\n"
            "4. Provide specific justification referencing what was/wasn't covered\n"
            "   - When numbers differ slightly, note if they're within acceptable range\n"
            "5. Provide a confidence level (0.0-1.0) for your assessment\n"
            "Be rigorous but fair in your assessment. Focus on whether the response "
            "conveys the same information as the claim, not on exact numerical "
            "precision unless precision is critical to the claim's meaning.\n\n"
            "Respond with ONLY a JSON object with these fields:\n"
            '{"claim_text": "...", "coverage_outcome": "fulfilled|partially_fulfilled|not_fulfilled", '
            '"justification": "...", "confidence_level": 0.0-1.0}'
        )

    def _fallback_result(self, claim: str, reason: str) -> dict:
        """Fallback result when evaluation fails."""
        return {
            "claim_text": claim,
            "coverage_outcome": "not_fulfilled",
            "justification": f"Evaluation failed: {reason}",
            "confidence_level": 0.1,
        }

    @staticmethod
    def _extract_claims(claim_blob) -> list[str]:
        """Extract individual claims from GTFA_CLAIMS.

        Mirrors ``extract_claims()`` from the official MCP-Atlas eval.
        """
        import json as _json
        import ast as _ast
        import re as _re

        if claim_blob is None:
            return []

        if isinstance(claim_blob, list):
            out = []
            for item in claim_blob:
                if isinstance(item, dict) and "claim" in item:
                    t = McpAtlasBenchmark._clean_claim(str(item["claim"]))
                else:
                    t = McpAtlasBenchmark._clean_claim(str(item))
                if t and len(t) > 3:
                    out.append(t)
            return out

        if not isinstance(claim_blob, str):
            claim_blob = str(claim_blob)
        claim_blob = claim_blob.strip()
        if not claim_blob:
            return []

        # Try JSON / Python literal parse
        if claim_blob.startswith("[") and claim_blob.endswith("]"):
            for parser in (_json.loads, _ast.literal_eval):
                try:
                    parsed = parser(claim_blob)
                    if isinstance(parsed, list):
                        out = []
                        for item in parsed:
                            if isinstance(item, dict) and "claim" in item:
                                t = McpAtlasBenchmark._clean_claim(str(item["claim"]))
                            else:
                                t = McpAtlasBenchmark._clean_claim(str(item))
                            if t and len(t) > 3:
                                out.append(t)
                        return out
                except (ValueError, SyntaxError):
                    continue

        # Numbered list
        numbered = _re.compile(r"(?:^|\n)(\d+)\.\s+")
        if numbered.search(claim_blob):
            parts = numbered.split(claim_blob)
            out = []
            i = 1
            while i + 1 < len(parts):
                t = parts[i + 1].strip().rstrip("\n").strip()
                if t and len(t) > 3:
                    out.append(t)
                i += 2
            if out:
                return out

        # Bullet / separator fallback
        for sep in ["\n•", "\n-", "\n*", ";", "||"]:
            if sep in claim_blob:
                out = [
                    McpAtlasBenchmark._clean_claim(p)
                    for p in claim_blob.split(sep)
                ]
                out = [c for c in out if c and len(c) > 3]
                if out:
                    return out

        # Newline fallback
        return [
            c for c in
            (McpAtlasBenchmark._clean_claim(l) for l in claim_blob.split("\n"))
            if c and len(c) > 3
        ]

    @staticmethod
    def _clean_claim(text: str) -> str:
        """Clean a single claim string (mirrors ``clean_claim_text``)."""
        import re as _re
        text = text.strip()
        text = _re.sub(r"^[-*•·◦‣⁃]\s*", "", text)
        text = _re.sub(r"^\d+[.)]\s*", "", text)
        text = text.replace("\u201c", '"').replace("\u201d", '"')
        text = text.replace("\u2018", "'").replace("\u2019", "'")
        text = text.replace("\u2013", "-").replace("\u2014", "-")
        text = text.replace("\u2026", "...")
        text = _re.sub(r'[.\s]*["\']+$', "", text)
        text = _re.sub(r'["\']+\.*$', "", text)
        return text.strip()

    def _judge_single_claim_bedrock(self, claim: str, response: str) -> dict:
        """Evaluate one claim against the response via Bedrock (legacy method).

        Uses the exact same prompt as the official MCP-Atlas evaluator.
        """
        import json as _json
        import re as _re

        prompt = self._get_evaluation_prompt(claim, response)

        try:
            from botocore.exceptions import ClientError

            client = self._get_bedrock_client()
            max_retries = 6
            for attempt in range(max_retries):
                try:
                    resp = client.converse(
                        modelId=self.eval_model_id,
                        messages=[{"role": "user", "content": [{"text": prompt}]}],
                        inferenceConfig={"maxTokens": 1024, "temperature": 0.0},
                    )
                    break
                except ClientError as e:
                    error_code = e.response["Error"]["Code"]
                    if error_code in ("ThrottlingException", "TooManyRequestsException",
                                      "ServiceUnavailableException", "ModelTimeoutException"):
                        if attempt == max_retries - 1:
                            raise
                        wait = min(2 ** attempt * 5, 120)
                        logger.info("Bedrock %s on claim judge (attempt %d/%d), waiting %ds ...",
                                    error_code, attempt + 1, max_retries, wait)
                        time.sleep(wait)
                    else:
                        raise

            text = resp["output"]["message"]["content"][0]["text"]

            # Robust JSON extraction — handle markdown fences, preamble text, etc.
            text = text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            # Try direct parse first
            try:
                return _json.loads(text)
            except _json.JSONDecodeError:
                pass
            # Fallback: find first { ... } block
            match = _re.search(r"\{[^{}]*\}", text, _re.DOTALL)
            if match:
                try:
                    return _json.loads(match.group())
                except _json.JSONDecodeError:
                    pass
            # Last resort: regex extract the outcome
            outcome_match = _re.search(
                r'"coverage_outcome"\s*:\s*"(fulfilled|partially_fulfilled|not_fulfilled)"', text
            )
            outcome = outcome_match.group(1) if outcome_match else "not_fulfilled"
            logger.warning("Fell back to regex JSON extraction for claim '%s'", claim[:60])
            return {
                "claim_text": claim,
                "coverage_outcome": outcome,
                "justification": "Parsed via regex fallback",
                "confidence_level": 0.5,
            }
        except Exception as e:
            logger.warning("Bedrock judge failed for claim '%s': %s", claim[:80], e)
            return self._fallback_result(claim, f"Bedrock error: {e}")

    # ── Internals ────────────────────────────────────────────────────

    def _load_split(self, split: str) -> list[dict]:
        """Load and cache a dataset split.

        MCP-Atlas only has a ``test`` split on HuggingFace, so we load it
        once and partition into train/holdout ourselves.
        """
        if not self._split_done:
            self._do_split()

        if split in self._cache:
            return self._cache[split]

        # Fallback: anything unknown maps to train
        return self._cache.get("train", [])

    def _do_split(self) -> None:
        """Load the single HF split and partition into train + holdout."""
        from datasets import load_dataset
        import random

        # Try "test" first; fall back to "train" (HF dataset may only have one split)
        try:
            ds = load_dataset(self.dataset_name, split="test")
        except ValueError:
            ds = load_dataset(self.dataset_name, split="train")
        rows = [dict(row) for row in ds]

        if self.shuffle:
            random.shuffle(rows)

        n_holdout = max(1, int(len(rows) * self.holdout_ratio))
        self._cache["holdout"] = rows[:n_holdout]
        self._cache["train"] = rows[n_holdout:]
        self._cache["test"] = rows  # full set

        self._split_done = True
        logger.info(
            "Loaded %d tasks from %s (train=%d, holdout=%d)",
            len(rows),
            self.dataset_name,
            len(self._cache["train"]),
            len(self._cache["holdout"]),
        )
