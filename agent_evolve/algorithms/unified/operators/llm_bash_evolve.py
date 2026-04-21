"""LLMBashEvolve — single LLM call with bash access that mutates the workspace.

Reference: ``agent_evolve/algorithms/adaptive_skill/engine.py`` lines 153-185
(``_run_llm``) and ``agent_evolve/algorithms/adaptive_skill/tools.py``
(``BASH_TOOL_SPEC`` / ``make_workspace_bash`` / ``create_default_llm``).
Independent reimplementation under ``unified/`` with identical bash spec and
behaviour. Prompt input is built from the EvidenceContext using canonical
JSON serialization (``sort_keys=True``, fixed float format) per AC-8.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from ..registry import register_operator
from ..types import MutationReport

logger = logging.getLogger(__name__)


BASH_TOOL_SPEC = {
    "name": "workspace_bash",
    "description": (
        "Execute a bash command in the agent workspace directory. "
        "Use this to read/write skills, prompts, memory files, and inspect git history."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The bash command to execute in the workspace directory.",
            },
        },
        "required": ["command"],
    },
}


DEFAULT_EVOLVER_SYSTEM_PROMPT = """\
You are a meta-learning agent that improves another agent by modifying its workspace files.

The workspace follows a standard directory structure:
- prompts/system.md  -- the agent's system prompt
- skills/*/SKILL.md  -- reusable skill definitions
- skills/_drafts/    -- draft skills from the solver
- memory/*.jsonl     -- episodic and semantic memory
- tools/             -- tool implementations

Your job each cycle:
1. Analyze task observation logs -- identify patterns, common failures, recurring themes
2. Review draft skills -- refine into real skills, merge with existing, or discard
3. Improve the system prompt if needed
4. Update memory with high-level insights, prune redundant entries
5. Use the provided bash tool to read/write files in the workspace
6. Verify your changes with `git diff` before finishing

Guidelines:
- Quality over quantity. Only create skills that genuinely help future tasks.
- Skills use SKILL.md format with YAML frontmatter (name, description).
- Keep memory concise and actionable.
- When modifying files, use precise edits.
"""


def _make_workspace_bash(workspace_root: str | Path):
    def bash(command: str) -> str:
        try:
            result = subprocess.run(
                ["bash", "-c", command],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(workspace_root),
            )
            output = (result.stdout + result.stderr).strip()
            return output if output else "(no output)"
        except subprocess.TimeoutExpired:
            return "ERROR: Command timed out."
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    return bash


def _resolve_llm(model: str, region: str):
    if "." in model and ("anthropic" in model or "amazon" in model or "meta" in model):
        from ...llm.bedrock import BedrockProvider

        return BedrockProvider(model_id=model, region=region), "bedrock"
    if model.startswith("claude"):
        from ...llm.anthropic import AnthropicProvider

        return AnthropicProvider(model=model), "anthropic"
    if model.startswith(("gpt-", "o1", "o3")):
        from ...llm.openai import OpenAIProvider

        return OpenAIProvider(model=model), "openai"
    from ...llm.bedrock import BedrockProvider

    return BedrockProvider(model_id=model), "bedrock"


def _canonical_json(obj: Any) -> str:
    """Canonical JSON: sorted keys, ensure_ascii off, stable float formatting."""

    def _default(o: Any) -> Any:
        if hasattr(o, "__dict__"):
            return o.__dict__
        return str(o)

    return json.dumps(obj, sort_keys=True, ensure_ascii=False, default=_default)


def _build_user_prompt(evidence: dict[str, Any], cycle_num: int) -> str:
    """Canonicalize the EvidenceContext so mocked-LLM diffs are byte-stable."""
    payload = {"cycle": cycle_num, "evidence": {k: evidence[k] for k in sorted(evidence)}}
    return (
        f"## Evolution Cycle #{cycle_num}\n\n"
        "### Evidence Context (canonicalized JSON)\n```json\n"
        + _canonical_json(payload)
        + "\n```\n\n### Instructions\n"
        "1. Review the evidence above — identify patterns, common failures, recurring themes.\n"
        "2. Use the workspace_bash tool to read/write files in the workspace.\n"
        "3. Prefer small, targeted skill additions; avoid rewriting large prompt sections.\n"
        "4. Verify your changes with `git diff` before finishing.\n"
    )


@register_operator("LLMBashEvolve")
class LLMBashEvolve:
    """Single LLM+bash pass that mutates the workspace.

    State keys:
        ``state["cycle_num"]`` — monotonically incremented across cycles.
        ``state["model_id"]`` / ``state["region"]`` — optional overrides.
        ``state["max_tokens"]`` — optional override.
        ``state["mock"]`` — for tests: a callable taking the prompt and
            returning a string, bypassing the real LLM.
    """

    WRITES: frozenset[str] = frozenset({"prompts", "skills", "memory", "tools"})

    DEFAULT_MODEL = "us.anthropic.claude-opus-4-6-v1"
    DEFAULT_REGION = "us-west-2"
    DEFAULT_MAX_TOKENS = 16384

    def apply(
        self,
        workspace: Any,
        context: Any,
        scope: dict[str, Any],
        state: dict[str, Any],
    ) -> MutationReport:
        cycle_num = int(state.get("cycle_num", 0)) + 1
        state["cycle_num"] = cycle_num
        skills_before = {s.name for s in workspace.list_skills()}

        evidence = dict(getattr(context, "entries", {}))
        user_prompt = _build_user_prompt(evidence, cycle_num)

        mock = state.get("mock")
        if callable(mock):
            response_content = mock(user_prompt)
        else:
            model = state.get("model_id", self.DEFAULT_MODEL)
            region = state.get("region", self.DEFAULT_REGION)
            max_tokens = int(state.get("max_tokens", self.DEFAULT_MAX_TOKENS))
            try:
                llm, kind = _resolve_llm(model, region)
            except ImportError as e:
                logger.warning("LLMBashEvolve: provider unavailable (%s)", e)
                return MutationReport(
                    operator_name="LLMBashEvolve",
                    count=0,
                    details={"error": f"provider unavailable: {e}"},
                )
            bash_fn = _make_workspace_bash(workspace.root)
            try:
                from ...llm.bedrock import BedrockProvider

                if kind == "bedrock" and isinstance(llm, BedrockProvider):
                    response = llm.converse_loop(
                        system_prompt=DEFAULT_EVOLVER_SYSTEM_PROMPT,
                        user_message=user_prompt,
                        tools=[BASH_TOOL_SPEC],
                        tool_executor={"workspace_bash": lambda command: bash_fn(command)},
                        max_tokens=max_tokens,
                    )
                    response_content = response.content
                else:
                    from ...llm.base import LLMMessage

                    response = llm.complete(
                        [
                            LLMMessage(role="system", content=DEFAULT_EVOLVER_SYSTEM_PROMPT),
                            LLMMessage(role="user", content=user_prompt),
                        ],
                        max_tokens=max_tokens,
                    )
                    response_content = response.content
            except Exception as exc:  # noqa: BLE001
                logger.error("LLMBashEvolve: LLM call failed: %s", exc)
                return MutationReport(
                    operator_name="LLMBashEvolve",
                    count=0,
                    details={"error": str(exc)[:200]},
                )

        skills_after = {s.name for s in workspace.list_skills()}
        added = sorted(skills_after - skills_before)
        removed = sorted(skills_before - skills_after)
        try:
            workspace.clear_drafts()
        except Exception:
            pass

        return MutationReport(
            operator_name="LLMBashEvolve",
            count=len(added) + len(removed),
            details={
                "cycle": cycle_num,
                "skills_added": added,
                "skills_removed": removed,
                "response_len": len(response_content or ""),
            },
        )
