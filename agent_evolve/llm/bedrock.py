"""AWS Bedrock LLM provider using the Converse API."""

from __future__ import annotations

import logging
import time
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
            from botocore.config import Config
        except ImportError:
            raise ImportError("pip install boto3  (or: pip install agent-evolve[bedrock])")

        self.model_id = model_id
        self.region = region
        self.client = boto3.client(
            "bedrock-runtime",
            region_name=region,
            config=Config(
                read_timeout=600,   # 10 min for long stream responses
                retries={"max_attempts": 3, "mode": "adaptive"},
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

            # Call converse_stream and consume the stream, with retry for
            # both API errors and stream read errors (e.g. ReadTimeout).
            output_content: list[dict] = []
            stop_reason = "end_turn"

            for _attempt in range(5):
                # Rebuild params in case messages were compressed
                params = {
                    "modelId": self.model_id,
                    "messages": converse_messages,
                    "inferenceConfig": {"maxTokens": max_tokens},
                }
                if system_blocks:
                    params["system"] = system_blocks
                if tool_config:
                    params["toolConfig"] = tool_config
                try:
                    stream_resp = self.client.converse_stream(**params)

                    # Consume stream
                    output_content = []
                    current_text = ""
                    current_tool_use: dict | None = None
                    current_tool_input_parts: list[str] = []
                    stop_reason = "end_turn"

                    for event in stream_resp.get("stream", []):
                        if "metadata" in event:
                            u = event["metadata"].get("usage", {})
                            total_input_tokens += u.get("inputTokens", 0)
                            total_output_tokens += u.get("outputTokens", 0)
                        elif "contentBlockStart" in event:
                            start = event["contentBlockStart"].get("start", {})
                            if "toolUse" in start:
                                if current_text:
                                    output_content.append({"text": current_text})
                                    current_text = ""
                                current_tool_use = {
                                    "toolUseId": start["toolUse"]["toolUseId"],
                                    "name": start["toolUse"]["name"],
                                }
                                current_tool_input_parts = []
                        elif "contentBlockDelta" in event:
                            delta = event["contentBlockDelta"].get("delta", {})
                            if "text" in delta:
                                current_text += delta["text"]
                            elif "toolUse" in delta:
                                current_tool_input_parts.append(delta["toolUse"].get("input", ""))
                        elif "contentBlockStop" in event:
                            if current_tool_use is not None:
                                import json as _json
                                raw_input = "".join(current_tool_input_parts)
                                try:
                                    parsed_input = _json.loads(raw_input) if raw_input else {}
                                except _json.JSONDecodeError:
                                    parsed_input = {"raw": raw_input}
                                current_tool_use["input"] = parsed_input
                                output_content.append({"toolUse": current_tool_use})
                                current_tool_use = None
                                current_tool_input_parts = []
                            elif current_text:
                                output_content.append({"text": current_text})
                                current_text = ""
                        elif "messageStop" in event:
                            stop_reason = event["messageStop"].get("stopReason", "end_turn")

                    # Flush remaining text
                    if current_text:
                        output_content.append({"text": current_text})
                        current_text = ""
                    break  # success

                except Exception as e:
                    err = str(e)
                    err_lower = err.lower()
                    # Prompt too long → compress and retry
                    if "prompt is too long" in err_lower or (
                        "validationexception" in err_lower and "too long" in err_lower
                    ):
                        compressed = self._compress_messages(converse_messages)
                        if compressed:
                            logger.warning(
                                "converse_loop: prompt too long, compressed %d→%d messages",
                                len(converse_messages), len(compressed),
                            )
                            converse_messages = compressed
                            continue
                        else:
                            logger.error("converse_loop: prompt too long, cannot compress further: %s", err)
                            raise
                    # Non-retryable validation errors
                    if "validationexception" in err_lower and "throttl" not in err_lower:
                        logger.error("converse_loop ValidationException (not retrying): %s", err)
                        raise
                    # Retryable: throttle, timeout, transient errors
                    base = 30 if "too many token" in err_lower else (
                        4 if ("throttl" in err_lower or "timed out" in err_lower) else 2
                    )
                    delay = base * (2 ** _attempt)
                    if _attempt < 4:
                        logger.warning(
                            "converse_loop retry %d/5: %s — waiting %ds",
                            _attempt + 1, err[:200], delay,
                        )
                        time.sleep(delay)
                    else:
                        raise

            # Guard against empty content (would cause ValidationException on next turn)
            if not output_content:
                logger.warning("converse_loop turn %d: empty output_content from stream", turn)
                output_content = [{"text": "(empty response)"}]

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

                    # Cap tool result size to prevent conversation from exceeding context window
                    result_str = str(result_text)
                    MAX_TOOL_RESULT_CHARS = 30000  # ~7.5K tokens
                    if len(result_str) > MAX_TOOL_RESULT_CHARS:
                        result_str = (
                            result_str[:MAX_TOOL_RESULT_CHARS]
                            + f"\n\n... [output truncated: {len(result_text)} chars total, "
                            f"showing first {MAX_TOOL_RESULT_CHARS}]"
                        )
                    tool_results.append({
                        "toolResult": {
                            "toolUseId": tool_use_id,
                            "content": [{"text": result_str}],
                        }
                    })

            if stop_reason == "tool_use" and tool_results:
                converse_messages.append({"role": "user", "content": tool_results})
                continue

            # Model finished (end_turn or max_tokens)
            break

        actual_turns = turn + 1
        logger.info(
            "converse_loop finished: %d turns, %d input tokens, %d output tokens",
            actual_turns, total_input_tokens, total_output_tokens,
        )
        return LLMResponse(
            content="\n".join(accumulated_text),
            usage={
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "turns": actual_turns,
            },
            raw={},
        )

    # ── Internal helpers ─────────────────────────────────────────────

    @staticmethod
    def _compress_messages(messages: list[dict]) -> list[dict] | None:
        """Compress conversation history to fit within token limits.

        Strategy: keep the first user message (the original prompt) and
        the most recent turns. For older turns, truncate tool results
        (which are typically the largest content — e.g. cat output).
        """
        if len(messages) <= 3:
            # Can't compress further: user + assistant + tool_result
            return None

        MAX_TOOL_RESULT_CHARS = 500
        MAX_TEXT_CHARS = 1000
        compressed = []

        # Always keep first message (original user prompt) intact
        compressed.append(messages[0])

        # For middle messages (tool use turns), aggressively truncate
        # Keep last 4 messages intact (most recent context)
        middle = messages[1:-4] if len(messages) > 5 else []
        tail = messages[-4:] if len(messages) > 5 else messages[1:]

        for msg in middle:
            new_msg = {"role": msg["role"], "content": []}
            for block in msg.get("content", []):
                if "toolResult" in block:
                    tr = block["toolResult"]
                    truncated_content = []
                    for c in tr.get("content", []):
                        if "text" in c:
                            text = c["text"]
                            if len(text) > MAX_TOOL_RESULT_CHARS:
                                text = text[:MAX_TOOL_RESULT_CHARS] + "\n... [truncated]"
                            truncated_content.append({"text": text})
                        else:
                            truncated_content.append(c)
                    new_msg["content"].append({
                        "toolResult": {
                            "toolUseId": tr["toolUseId"],
                            "content": truncated_content,
                        }
                    })
                elif "text" in block:
                    text = block["text"]
                    if len(text) > MAX_TEXT_CHARS:
                        text = text[:MAX_TEXT_CHARS] + "\n... [truncated]"
                    new_msg["content"].append({"text": text})
                else:
                    # toolUse blocks — keep as-is (small)
                    new_msg["content"].append(block)
            compressed.append(new_msg)

        # Keep recent messages intact
        compressed.extend(tail)

        logger.info(
            "Compressed conversation: %d messages, middle %d turns truncated",
            len(compressed), len(middle),
        )
        return compressed

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
