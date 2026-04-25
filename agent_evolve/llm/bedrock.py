"""AWS Bedrock LLM provider using the Converse API."""

from __future__ import annotations

import logging
from typing import Any

from .base import LLMMessage, LLMProvider, LLMResponse

logger = logging.getLogger(__name__)


class BedrockProvider(LLMProvider):
    """LLM provider using AWS Bedrock Converse API.

    Mirrors the model setup used in CodeDojo/swe-agent (strands BedrockModel)
    but implemented directly with boto3 for framework independence.
    """

    def __init__(
        self,
        model_id: str = "us.anthropic.claude-sonnet-4-20250514-v1:0",
        region: str = "us-west-2",
    ):
        try:
            import boto3
        except ImportError:
            raise ImportError("pip install boto3  (or: pip install agent-evolve[bedrock])")

        from ._bedrock_config import bedrock_boto_config

        self.model_id = model_id
        self.region = region
        # retry / timeouts come from BEDROCK_RETRY_MAX_ATTEMPTS /
        # BEDROCK_READ_TIMEOUT_SEC / BEDROCK_CONNECT_TIMEOUT_SEC env vars
        # (defaults 15 / 600 / 30, mode=adaptive). Surfaced as visible env
        # var knobs in every evolve / baseline wrapper script.
        self.client = boto3.client(
            "bedrock-runtime",
            region_name=region,
            config=bedrock_boto_config(),
        )

    def complete(
        self,
        messages: list[LLMMessage],
        max_tokens: int = 4096,
        temperature: float = 0.0,
        **kwargs,
    ) -> LLMResponse:
        system_blocks, converse_messages = self._split_messages(messages)

        params: dict[str, Any] = {
            "modelId": self.model_id,
            "messages": converse_messages,
            "inferenceConfig": {
                "maxTokens": max_tokens,
                "temperature": temperature,
            },
        }
        if system_blocks:
            params["system"] = system_blocks

        response = self.client.converse(**params)
        return self._parse_response(response)

    def complete_with_tools(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        max_tokens: int = 4096,
        **kwargs,
    ) -> LLMResponse:
        system_blocks, converse_messages = self._split_messages(messages)

        tool_config = {"tools": self._to_bedrock_tools(tools)}

        params: dict[str, Any] = {
            "modelId": self.model_id,
            "messages": converse_messages,
            "inferenceConfig": {"maxTokens": max_tokens},
            "toolConfig": tool_config,
        }
        if system_blocks:
            params["system"] = system_blocks

        response = self.client.converse(**params)
        return self._parse_response(response)

    def converse_loop(
        self,
        system_prompt: str,
        user_message: str,
        tools: list[dict[str, Any]],
        tool_executor: dict[str, Any],
        max_tokens: int = 16384,
        max_turns: int = 50,
    ) -> LLMResponse:
        """Run a multi-turn conversation with tool use until the model stops.

        This mirrors the agentic loop pattern used by strands-agents.

        Args:
            system_prompt: System prompt text.
            user_message: Initial user message.
            tools: Tool definitions in Bedrock format.
            tool_executor: Dict mapping tool names to callable functions.
            max_tokens: Max tokens per turn.
            max_turns: Safety limit on conversation turns.

        Returns:
            Final LLMResponse with the accumulated text output.
        """
        system_blocks = [{"text": system_prompt}] if system_prompt else []
        tool_config = {"tools": self._to_bedrock_tools(tools)} if tools else None

        converse_messages = [{"role": "user", "content": [{"text": user_message}]}]

        total_input_tokens = 0
        total_output_tokens = 0
        accumulated_text: list[str] = []

        for turn in range(max_turns):
            params: dict[str, Any] = {
                "modelId": self.model_id,
                "messages": converse_messages,
                "inferenceConfig": {"maxTokens": max_tokens},
            }
            if system_blocks:
                params["system"] = system_blocks
            if tool_config:
                params["toolConfig"] = tool_config

            response = self.client.converse(**params)

            usage = response.get("usage", {})
            total_input_tokens += usage.get("inputTokens", 0)
            total_output_tokens += usage.get("outputTokens", 0)

            output_content = response.get("output", {}).get("message", {}).get("content", [])
            stop_reason = response.get("stopReason", "end_turn")

            # Add assistant message
            converse_messages.append({"role": "assistant", "content": output_content})

            # Collect text blocks and handle tool use
            tool_results = []
            for block in output_content:
                if "text" in block:
                    accumulated_text.append(block["text"])
                elif "toolUse" in block:
                    tool_use = block["toolUse"]
                    tool_name = tool_use["name"]
                    tool_input = tool_use.get("input", {})
                    tool_use_id = tool_use["toolUseId"]

                    executor = tool_executor.get(tool_name)
                    if executor:
                        try:
                            result_text = executor(**tool_input) if isinstance(tool_input, dict) else executor(tool_input)
                        except Exception as e:
                            result_text = f"ERROR: {e}"
                    else:
                        result_text = f"ERROR: Unknown tool '{tool_name}'"

                    tool_results.append({
                        "toolResult": {
                            "toolUseId": tool_use_id,
                            "content": [{"text": str(result_text)}],
                        }
                    })

            if stop_reason == "tool_use" and tool_results:
                converse_messages.append({"role": "user", "content": tool_results})
                continue

            # Model finished (end_turn or max_tokens)
            break

        return LLMResponse(
            content="\n".join(accumulated_text),
            usage={
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
            },
            raw=response,
        )

    # ── Internal helpers ─────────────────────────────────────────────

    @staticmethod
    def _split_messages(
        messages: list[LLMMessage],
    ) -> tuple[list[dict], list[dict]]:
        """Split messages into Bedrock system blocks and converse messages."""
        system_blocks: list[dict] = []
        converse_messages: list[dict] = []
        for m in messages:
            if m.role == "system":
                system_blocks.append({"text": m.content})
            else:
                converse_messages.append({
                    "role": m.role,
                    "content": [{"text": m.content}],
                })
        return system_blocks, converse_messages

    @staticmethod
    def _to_bedrock_tools(tools: list[dict[str, Any]]) -> list[dict]:
        """Convert tool definitions to Bedrock toolSpec format.

        Accepts either:
          - Already-formatted Bedrock tools (with 'toolSpec' key)
          - Simplified format: {name, description, input_schema}
        """
        bedrock_tools = []
        for t in tools:
            if "toolSpec" in t:
                bedrock_tools.append(t)
            else:
                bedrock_tools.append({
                    "toolSpec": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "inputSchema": {
                            "json": t.get("input_schema", t.get("parameters", {}))
                        },
                    }
                })
        return bedrock_tools

    @staticmethod
    def _parse_response(response: dict) -> LLMResponse:
        """Parse a Bedrock Converse API response into LLMResponse."""
        output = response.get("output", {})
        message = output.get("message", {})
        content_blocks = message.get("content", [])

        text_parts = [b["text"] for b in content_blocks if "text" in b]
        usage = response.get("usage", {})

        return LLMResponse(
            content="\n".join(text_parts),
            usage={
                "input_tokens": usage.get("inputTokens", 0),
                "output_tokens": usage.get("outputTokens", 0),
            },
            raw=response,
        )
