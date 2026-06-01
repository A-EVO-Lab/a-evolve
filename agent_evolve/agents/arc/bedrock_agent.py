"""
BedrockAgent: Tool-use agent powered by AWS Bedrock converse API.
Replaces the Symbolica agentica SDK's spawn() + agent.call() pattern.
"""

import json
import logging
import random
import time
from typing import Any, Callable

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError, ReadTimeoutError, ConnectTimeoutError

logger = logging.getLogger()

# Maximum converse turns per call() to prevent infinite loops
MAX_TURNS = 500
# Retry config for throttling / transient errors
MAX_RETRIES = 12
INITIAL_BACKOFF = 2.0
MAX_BACKOFF = 120.0

# Error codes that are retryable
RETRYABLE_CODES = frozenset({
    "ThrottlingException",
    "TooManyRequestsException",
    "ServiceUnavailableException",
    "InternalServerException",
    "ModelTimeoutException",
    "ModelNotReadyException",
    "ReadTimeoutError",
    "ConnectTimeoutError",
    "EndpointConnectionError",
})


class BedrockAgent:
    """
    An agent that uses AWS Bedrock converse API with tool use.

    Each agent has:
    - A system prompt (premise)
    - A set of tools (Bedrock toolSpec definitions + Python handler callables)
    - Persistent message history across call() invocations
    """

    def __init__(
        self,
        model_id: str,
        system_prompt: str,
        tools: list[dict[str, Any]],
        tool_handlers: dict[str, Callable[..., str]],
        agent_id: int = 0,
        max_tokens: int = 16384,
        region: str = "us-west-2",
        game_id: str = "",
        thinking_effort: str = "",  # "", "low", "medium", "high", "max"
    ) -> None:
        boto_config = BotoConfig(
            read_timeout=600,       # 10 min read timeout for long Opus responses
            connect_timeout=30,
            retries={"max_attempts": 0},  # We handle retries ourselves
        )
        self.client = boto3.client("bedrock-runtime", region_name=region, config=boto_config)
        self.model_id = model_id
        self.system = [{"text": system_prompt}, {"cachePoint": {"type": "default"}}]
        # Add cache point after tools so tool definitions are cached too
        self.tools = tools + [{"cachePoint": {"type": "default"}}] if tools else tools
        self.tool_handlers = dict(tool_handlers)  # name -> callable returning str
        self.messages: list[dict[str, Any]] = []
        self.agent_id = agent_id
        self.max_tokens = max_tokens
        self.game_id = game_id
        self.thinking_effort = thinking_effort
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cache_read_tokens = 0
        self.total_cache_write_tokens = 0
        self._consecutive_errors = 0

    @property
    def _log_prefix(self) -> str:
        prefix = f"Agent {self.agent_id}"
        if self.game_id:
            prefix = f"[{self.game_id}] {prefix}"
        return prefix

    def update_tool(self, name: str, handler: Callable[..., str]) -> None:
        """Replace a tool handler (e.g. fresh bounded_submit_action)."""
        self.tool_handlers[name] = handler

    def call(self, task: str) -> str:
        """
        Send a task message and run the converse loop until the model stops.

        The model may call tools (submit_action, spawn_agent, etc.) which are
        executed synchronously. Subagent tool calls may themselves run entire
        converse loops (nested agents).

        Returns the final text response from the model.
        """
        # Add user message
        self.messages.append({
            "role": "user",
            "content": [{"text": task}],
        })

        return self._run_converse_loop()

    def _run_converse_loop(self) -> str:
        """Inner converse loop handling tool use."""
        tool_config = {"tools": self.tools} if self.tools else None

        for turn in range(MAX_TURNS):
            response = self._converse_with_retry(tool_config)

            # Track token usage
            usage = response.get("usage", {})
            self.total_input_tokens += usage.get("inputTokens", 0)
            self.total_output_tokens += usage.get("outputTokens", 0)
            self.total_cache_read_tokens += usage.get("cacheReadInputTokens", 0)
            self.total_cache_write_tokens += usage.get("cacheWriteInputTokens", 0)

            # Get the assistant message
            assistant_msg = response["output"]["message"]
            self.messages.append(assistant_msg)

            stop_reason = response.get("stopReason", "end_turn")

            # Check for tool use
            tool_uses = [
                block for block in assistant_msg["content"]
                if "toolUse" in block
            ]

            if not tool_uses or stop_reason == "end_turn":
                # No tool calls or model decided to stop — extract final text
                text_parts = []
                for block in assistant_msg["content"]:
                    if "text" in block:
                        text_parts.append(block["text"])
                return "\n".join(text_parts) if text_parts else ""

            # Execute tool calls and build results
            tool_results = []
            for block in tool_uses:
                tu = block["toolUse"]
                tool_name = tu["name"]
                tool_input = tu["input"]
                tool_use_id = tu["toolUseId"]

                result_text = self._execute_tool(tool_name, tool_input)

                tool_results.append({
                    "toolResult": {
                        "toolUseId": tool_use_id,
                        "content": [{"text": result_text}],
                    }
                })

            # Send tool results back as a user message
            self.messages.append({
                "role": "user",
                "content": tool_results,
            })

        logger.warning(
            f"{self._log_prefix}: hit MAX_TURNS={MAX_TURNS}, forcing stop"
        )
        return "[Agent hit maximum turn limit]"

    def _execute_tool(self, name: str, input_data: dict[str, Any]) -> str:
        """Execute a tool handler and return the result as a string."""
        handler = self.tool_handlers.get(name)
        if handler is None:
            return f"ERROR: Unknown tool '{name}'"

        try:
            result = handler(**input_data)
            return str(result)
        except Exception as e:
            logger.error(f"{self._log_prefix}: tool '{name}' error: {e}")
            return f"ERROR: {type(e).__name__}: {e}"

    def _converse_with_retry(self, tool_config: dict | None) -> dict:
        """Call Bedrock converse with exponential backoff on throttling/transient errors."""
        params: dict[str, Any] = {
            "modelId": self.model_id,
            "messages": self.messages,
            "system": self.system,
            "inferenceConfig": {"maxTokens": self.max_tokens},
        }
        if tool_config:
            params["toolConfig"] = tool_config
        if self.thinking_effort:
            params["additionalModelRequestFields"] = {
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": self.thinking_effort},
            }

        for attempt in range(MAX_RETRIES):
            try:
                result = self.client.converse(**params)
                if self._consecutive_errors > 0:
                    logger.info(
                        f"{self._log_prefix}: recovered after {self._consecutive_errors} retries"
                    )
                self._consecutive_errors = 0
                return result
            except (ClientError, ReadTimeoutError, ConnectTimeoutError,
                    ConnectionError, OSError) as e:
                error_code = self._classify_error(e)
                if error_code in RETRYABLE_CODES:
                    self._consecutive_errors += 1
                    # Exponential backoff with full jitter
                    base_wait = min(INITIAL_BACKOFF * (2 ** attempt), MAX_BACKOFF)
                    wait = base_wait * (0.5 + random.random() * 0.5)
                    logger.warning(
                        f"{self._log_prefix}: {error_code} — "
                        f"retry {attempt + 1}/{MAX_RETRIES} in {wait:.1f}s "
                        f"(consecutive errors: {self._consecutive_errors})"
                    )
                    time.sleep(wait)
                else:
                    raise

        # Final attempt — let it raise if it fails
        logger.warning(
            f"{self._log_prefix}: final retry attempt ({MAX_RETRIES + 1})"
        )
        return self.client.converse(**params)

    @staticmethod
    def _classify_error(e: Exception) -> str:
        """Extract a classifiable error code from various exception types."""
        if isinstance(e, ReadTimeoutError):
            return "ReadTimeoutError"
        if isinstance(e, ConnectTimeoutError):
            return "ConnectTimeoutError"
        if isinstance(e, (ConnectionError, OSError)):
            return "EndpointConnectionError"
        if isinstance(e, ClientError):
            return e.response.get("Error", {}).get("Code", "UnknownError")
        return type(e).__name__

    @property
    def usage_summary(self) -> str:
        return (
            f"Agent {self.agent_id}: "
            f"{self.total_input_tokens:,} input + "
            f"{self.total_output_tokens:,} output = "
            f"{self.total_input_tokens + self.total_output_tokens:,} total tokens"
        )
