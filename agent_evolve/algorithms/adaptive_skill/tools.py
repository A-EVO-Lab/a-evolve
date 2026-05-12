"""Bash tool spec and LLM provider factory for A-Evolve."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ...config import EvolveConfig
from ...llm.base import LLMProvider

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


def make_workspace_bash(workspace_root: str | Path):
    """Create a bash callable scoped to the workspace directory."""

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
        except Exception as e:
            return f"ERROR: {e}"

    return bash


def _is_openai_compatible_model(model: str) -> bool:
    return (
        model.startswith("openai:")
        or model.startswith("/")
        or model.startswith("file:")
    )


def _openai_base_url(config: EvolveConfig) -> str | None:
    return (
        config.extra.get("openai_base_url")
        or config.extra.get("base_url")
        or os.environ.get("EVOLVER_OPENAI_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
    )


def create_default_llm(config: EvolveConfig) -> LLMProvider:
    """Create the default LLM provider based on the evolver_model config string."""
    model = config.evolver_model

    if _is_openai_compatible_model(model):
        from ...llm.openai import OpenAIProvider

        base_url = _openai_base_url(config)
        if (model.startswith("/") or model.startswith("file:")) and not base_url:
            raise ValueError(
                "Local/path evolver models require EVOLVER_OPENAI_BASE_URL "
                "or OPENAI_BASE_URL pointing at an OpenAI-compatible server."
            )
        return OpenAIProvider(
            model=model.removeprefix("openai:").removeprefix("file:"),
            base_url=base_url,
        )

    if "." in model and ("anthropic" in model or "amazon" in model or "meta" in model):
        from ...llm.bedrock import BedrockProvider

        region = config.extra.get("region", "us-west-2")
        return BedrockProvider(model_id=model, region=region)

    if model.startswith("claude"):
        from ...llm.anthropic import AnthropicProvider

        return AnthropicProvider(model=model)

    if model.startswith(("gpt-", "o1", "o3")):
        from ...llm.openai import OpenAIProvider

        return OpenAIProvider(model=model)

    from ...llm.bedrock import BedrockProvider

    return BedrockProvider(model_id=model)
