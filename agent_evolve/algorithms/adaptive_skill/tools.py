"""Bash tool spec and LLM provider factory for A-Evolve."""

from __future__ import annotations

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


def create_default_llm(config: EvolveConfig) -> LLMProvider:
    """Create the default LLM provider based on the evolver_model config string.

    When ``config.evolver_base_url`` is set all calls are routed to that local
    OpenAI-compatible endpoint regardless of the model name, so you can use any
    locally-served model (Ollama, LM Studio, vLLM, LocalAI, …).
    """
    model = config.evolver_model

    # Local OpenAI-compatible endpoint takes priority over cloud providers.
    if config.evolver_base_url:
        from ...llm.openai import OpenAIProvider

        return OpenAIProvider(
            model=model,
            base_url=config.evolver_base_url,
            api_key=config.evolver_api_key,
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
