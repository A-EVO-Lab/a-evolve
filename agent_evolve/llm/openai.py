"""OpenAI / OpenAI-compatible LLM provider."""

from __future__ import annotations

import json
import os
from typing import Any

from .base import LLMMessage, LLMProvider, LLMResponse


class OpenAIProvider(LLMProvider):
    """LLM provider using OpenAI Chat Completions or compatible servers."""

    def __init__(
        self,
        model: str = "gpt-4o",
        api_key: str | None = None,
        base_url: str | None = None,
    ):
        try:
            import openai
        except ImportError:
            raise ImportError("pip install openai  (or: pip install agent-evolve[openai])")

        self.model = model
        resolved_base_url = (
            base_url
            or os.environ.get("EVOLVER_OPENAI_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
        )
        resolved_api_key = (
            api_key
            or os.environ.get("EVOLVER_OPENAI_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )
        kwargs: dict[str, Any] = {}
        if resolved_base_url:
            kwargs["base_url"] = resolved_base_url
            kwargs["api_key"] = resolved_api_key or "EMPTY"
        elif resolved_api_key:
            kwargs["api_key"] = resolved_api_key
        self.client = openai.OpenAI(**kwargs)

    def complete(
        self,
        messages: list[LLMMessage],
        max_tokens: int = 4096,
        temperature: float = 0.0,
        **kwargs,
    ) -> LLMResponse:
        api_messages = [{"role": m.role, "content": m.content} for m in messages]

        response = self.client.chat.completions.create(
            model=self.model,
            messages=api_messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        choice = response.choices[0]
        usage = response.usage
        return LLMResponse(
            content=choice.message.content or "",
            usage={
                "input_tokens": usage.prompt_tokens if usage else 0,
                "output_tokens": usage.completion_tokens if usage else 0,
            },
            raw=response,
        )

    def complete_with_tools(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        max_tokens: int = 4096,
        **kwargs,
    ) -> LLMResponse:
        api_messages = [{"role": m.role, "content": m.content} for m in messages]

        response = self.client.chat.completions.create(
            model=self.model,
            messages=api_messages,
            max_tokens=max_tokens,
            tools=self._to_openai_tools(tools),
        )
        choice = response.choices[0]
        usage = response.usage
        return LLMResponse(
            content=choice.message.content or "",
            usage={
                "input_tokens": usage.prompt_tokens if usage else 0,
                "output_tokens": usage.completion_tokens if usage else 0,
            },
            raw=response,
        )

    def converse_loop(
        self,
        system_prompt: str,
        user_message: str,
        tools: list[dict[str, Any]],
        tool_executor: dict[str, Any],
        max_tokens: int = 16384,
        max_turns: int = 50,
    ) -> LLMResponse:
        """Run a tool-use loop against an OpenAI-compatible chat endpoint."""
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_message})

        openai_tools = self._to_openai_tools(tools)
        total_input_tokens = 0
        total_output_tokens = 0
        accumulated_text: list[str] = []
        last_response: Any = None

        for _ in range(max_turns):
            params: dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "max_tokens": max_tokens,
            }
            if openai_tools:
                params["tools"] = openai_tools
                params["tool_choice"] = "auto"
            response = self.client.chat.completions.create(**params)
            last_response = response
            usage = getattr(response, "usage", None)
            total_input_tokens += self._usage_value(usage, "prompt_tokens")
            total_output_tokens += self._usage_value(usage, "completion_tokens")

            choice = response.choices[0]
            message = choice.message
            content = getattr(message, "content", None) or ""
            tool_calls = list(getattr(message, "tool_calls", None) or [])
            if content:
                accumulated_text.append(content)

            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": content,
            }
            if tool_calls:
                assistant_message["tool_calls"] = [
                    self._tool_call_to_dict(tool_call) for tool_call in tool_calls
                ]
            messages.append(assistant_message)

            if not tool_calls:
                break

            for tool_call in tool_calls:
                fn = tool_call.function
                name = fn.name
                raw_args = fn.arguments or "{}"
                try:
                    parsed_args = json.loads(raw_args)
                except json.JSONDecodeError as exc:
                    result_text = f"ERROR: malformed tool arguments for {name}: {exc}"
                else:
                    executor = tool_executor.get(name)
                    if executor is None:
                        result_text = f"ERROR: Unknown tool '{name}'"
                    else:
                        try:
                            if isinstance(parsed_args, dict):
                                result = executor(**parsed_args)
                            else:
                                result = executor(parsed_args)
                            result_text = str(result)
                        except Exception as exc:  # noqa: BLE001
                            result_text = f"ERROR: {exc}"
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result_text,
                })

        return LLMResponse(
            content="\n".join(accumulated_text),
            usage={
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
            },
            raw=last_response,
        )

    @staticmethod
    def _to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        openai_tools: list[dict[str, Any]] = []
        for tool in tools:
            if tool.get("type") == "function":
                openai_tools.append(tool)
                continue
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {"type": "object"}),
                },
            })
        return openai_tools

    @staticmethod
    def _tool_call_to_dict(tool_call: Any) -> dict[str, Any]:
        return {
            "id": tool_call.id,
            "type": "function",
            "function": {
                "name": tool_call.function.name,
                "arguments": tool_call.function.arguments or "{}",
            },
        }

    @staticmethod
    def _usage_value(usage: Any, key: str) -> int:
        if usage is None:
            return 0
        if isinstance(usage, dict):
            return int(usage.get(key, 0) or 0)
        return int(getattr(usage, key, 0) or 0)
