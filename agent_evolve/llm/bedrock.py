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
        model_id: str | None = None,
        region: str | None = None,
    ):
        import os

        if model_id is None:
            model_id = os.environ.get("SOLVER_MODEL", "<solver-model-id>")
        if region is None:
            region = os.environ.get("AWS_REGION", "us-west-2")
        try:
            import boto3
            import os
            from botocore.config import Config as BotocoreConfig
        except ImportError:
            raise ImportError("pip install boto3  (or: pip install agent-evolve[bedrock])")

        # Clean AWS environment to force IAM instance role usage
        for key in ['AWS_PROFILE', 'AWS_SHARED_CREDENTIALS_FILE', 'AWS_CONFIG_FILE']:
            if key in os.environ:
                del os.environ[key]

        # Clear boto3 session cache to ensure fresh credentials
        if hasattr(boto3, 'DEFAULT_SESSION') and boto3.DEFAULT_SESSION:
            boto3.DEFAULT_SESSION = None

        self.model_id = model_id
        self.region = region
        self.client = boto3.client(
            "bedrock-runtime",
            region_name=region,
            config=BotocoreConfig(
                read_timeout=600,
                retries={"max_attempts": 8, "mode": "adaptive"},
            ),
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
        temperature: float = 0.0,
        **kwargs,
    ) -> LLMResponse:
        system_blocks, converse_messages = self._split_messages(messages)

        tool_config = {"tools": self._to_bedrock_tools(tools)}

        params: dict[str, Any] = {
            "modelId": self.model_id,
            "messages": converse_messages,
            "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature},
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
        max_turns: int | None = None,
        temperature: float = 0.0,
        verbose: bool = False,
    ) -> LLMResponse:
        """Run a multi-turn conversation with tool use until the model stops.

        This mirrors the agentic loop pattern used by strands-agents.

        Args:
            system_prompt: System prompt text.
            user_message: Initial user message.
            tools: Tool definitions in Bedrock format.
            tool_executor: Dict mapping tool names to callable functions.
            max_tokens: Max tokens per turn.
            max_turns: Optional safety cap on conversation turns.  ``None``
                (default) means "loop until the model emits a non-``tool_use``
                stop reason" — appropriate for evolution-time calls where
                we want the LLM to complete its full workplan rather than
                be truncated.  Pass an int to bound for debugging or cost
                control.

        Returns:
            Final LLMResponse with the accumulated text output.
        """
        system_blocks = [{"text": system_prompt}] if system_prompt else []
        tool_config = {"tools": self._to_bedrock_tools(tools)} if tools else None

        converse_messages = [{"role": "user", "content": [{"text": user_message}]}]

        total_input_tokens = 0
        total_output_tokens = 0
        accumulated_text: list[str] = []

        turn = 0
        while max_turns is None or turn < max_turns:
            params: dict[str, Any] = {
                "modelId": self.model_id,
                "messages": converse_messages,
                "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature},
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
                    if verbose:
                        print(f"\n{'─'*70}")
                        print(f"🤖 EVOLVER [turn {turn+1}]:")
                        print(block["text"])
                elif "toolUse" in block:
                    tool_use = block["toolUse"]
                    tool_name = tool_use["name"]
                    tool_input = tool_use.get("input", {})
                    tool_use_id = tool_use["toolUseId"]

                    if verbose:
                        print(f"\n{'─'*70}")
                        print(f"🔧 TOOL CALL: {tool_name}")
                        for k, v in (tool_input if isinstance(tool_input, dict) else {"input": tool_input}).items():
                            print(f"   {k}: {v}")

                    executor = tool_executor.get(tool_name)
                    if executor:
                        try:
                            result_text = executor(**tool_input) if isinstance(tool_input, dict) else executor(tool_input)
                        except Exception as e:
                            result_text = f"ERROR: {e}"
                    else:
                        result_text = f"ERROR: Unknown tool '{tool_name}'"

                    if verbose:
                        print(f"📋 RESULT:")
                        print(str(result_text))

                    tool_results.append({
                        "toolResult": {
                            "toolUseId": tool_use_id,
                            "content": [{"text": str(result_text)}],
                        }
                    })

            if stop_reason == "tool_use" and tool_results:
                converse_messages.append({"role": "user", "content": tool_results})
                turn += 1
                continue

            # Model finished (end_turn or max_tokens)
            break

        return LLMResponse(
            content="\n".join(accumulated_text),
            usage={
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
            },
            raw={"last_response": response, "conversation": converse_messages},
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
