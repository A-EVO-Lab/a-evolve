"""LLMJudgeReader — assign proxy scores to trajectories via an LLM judge.

Reference: ``agent_evolve/algorithms/adaptive_skill/prompts.py`` lines 247-298
(``judge_trajectories``). Independent reimplementation under ``unified/`` —
no import from the legacy adaptive_skill module.

The judge is best-effort. When the Bedrock provider is unavailable in a
given environment (e.g., unit tests without AWS creds), the reader returns
empty verdicts rather than failing the cycle; callers (the controller, the
operators) treat missing verdicts as "no proxy signal".
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..registry import register_reader
from .trajectory import _compress_one as _compress_trajectory

logger = logging.getLogger(__name__)


JUDGE_SYSTEM_PROMPT = (
    "You are evaluating whether an AI agent successfully completed a "
    "command-line task.\nYou can ONLY see the agent's actions (commands "
    "run and their outputs). You do NOT have access to the actual test "
    "results.\nBased on the trajectory, estimate whether the task was "
    "completed successfully."
)

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


def _fallback(task_id: str, reason: str) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "score": -1,
        "category": "unknown",
        "outcome": f"judge unavailable: {reason}",
        "failure_reason": "",
    }


@register_reader("LLMJudgeReader")
class LLMJudgeReader:
    """Output keys:

        "per_task": list of {"task_id", "score", "category", "outcome",
                             "failure_reason"}, sorted by task_id.
                    ``score=-1`` means the judge was not available.

    Uses a Bedrock LLM when available. The model id is read from
    ``state["model_id"]`` (set by the controller), falling back to a
    project-wide default.
    """

    DEFAULT_MODEL = "us.anthropic.claude-opus-4-6-v1"
    DEFAULT_REGION = "us-west-2"

    def read(
        self,
        observations: list,
        workspace: Any,
        history: Any,
        config: Any,
        context: Any,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        model_id = state.get("model_id", self.DEFAULT_MODEL)
        region = state.get("region", self.DEFAULT_REGION)

        try:
            from agent_evolve.llm.bedrock import BedrockProvider
            from agent_evolve.llm.base import LLMMessage
        except ImportError as e:
            logger.warning("BedrockProvider not available, skipping judge: %s", e)
            return {
                "per_task": [
                    _fallback(getattr(o.task, "id", ""), "BedrockProvider not importable")
                    for o in observations
                ]
            }

        llm = BedrockProvider(model_id=model_id, region=region)
        verdicts: list[dict[str, Any]] = []
        for obs in observations:
            task_id = getattr(obs.task, "id", "")
            conv = list(getattr(obs.trajectory, "conversation", []) or [])
            compressed = _compress_trajectory(conv)
            prompt = JUDGE_USER_TEMPLATE.format(
                task_id=task_id, trajectory=compressed
            )
            try:
                response = llm.complete(
                    messages=[
                        LLMMessage(role="system", content=JUDGE_SYSTEM_PROMPT),
                        LLMMessage(role="user", content=prompt),
                    ],
                    max_tokens=300,
                    temperature=0.0,
                )
                text = response.content.strip()
                if "```" in text:
                    text = text.split("```")[1]
                    if text.startswith("json"):
                        text = text[4:]
                    text = text.strip()
                parsed = json.loads(text)
                verdicts.append(
                    {
                        "task_id": task_id,
                        "score": int(parsed.get("score", -1)),
                        "category": str(parsed.get("category", "unknown")),
                        "outcome": str(parsed.get("outcome", "")),
                        "failure_reason": str(parsed.get("failure_reason", "")),
                    }
                )
            except Exception as e:  # noqa: BLE001 — judge is best-effort
                logger.warning("Judge failed for %s: %s", task_id, str(e)[:120])
                verdicts.append(_fallback(task_id, f"{type(e).__name__}"))

        verdicts.sort(key=lambda v: v["task_id"])
        return {"per_task": verdicts}
