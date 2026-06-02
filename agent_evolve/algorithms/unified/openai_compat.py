"""OpenAI-compatible provider used by the artifact-local unified engine."""

from __future__ import annotations

import json
import os
from typing import Any

from agent_evolve.llm.base import LLMMessage, LLMResponse


class OpenAICompatProvider:
    """Small OpenAI chat-completions wrapper with tool-loop support."""

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        try:
            import openai
        except ImportError as exc:
            raise ImportError("Install openai to use an OpenAI-compatible evolver") from exc

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
        **_: Any,
    ) -> LLMResponse:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        choice = response.choices[0]
        usage = getattr(response, "usage", None)
        return LLMResponse(
            content=choice.message.content or "",
            usage={
                "input_tokens": self._usage_value(usage, "prompt_tokens"),
                "output_tokens": self._usage_value(usage, "completion_tokens"),
            },
            raw=response,
        )

    def complete_with_tools(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        max_tokens: int = 4096,
        **_: Any,
    ) -> LLMResponse:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            max_tokens=max_tokens,
            tools=self._to_openai_tools(tools),
        )
        choice = response.choices[0]
        usage = getattr(response, "usage", None)
        return LLMResponse(
            content=choice.message.content or "",
            usage={
                "input_tokens": self._usage_value(usage, "prompt_tokens"),
                "output_tokens": self._usage_value(usage, "completion_tokens"),
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
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_message})

        openai_tools = self._to_openai_tools(tools)
        input_tokens = 0
        output_tokens = 0
        text_parts: list[str] = []
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
            input_tokens += self._usage_value(usage, "prompt_tokens")
            output_tokens += self._usage_value(usage, "completion_tokens")

            message = response.choices[0].message
            content = getattr(message, "content", None) or ""
            tool_calls = list(getattr(message, "tool_calls", None) or [])
            if content:
                text_parts.append(content)

            assistant_message: dict[str, Any] = {"role": "assistant", "content": content}
            if tool_calls:
                assistant_message["tool_calls"] = [
                    self._tool_call_to_dict(call) for call in tool_calls
                ]
            messages.append(assistant_message)

            if not tool_calls:
                break

            for tool_call in tool_calls:
                name = tool_call.function.name
                raw_args = tool_call.function.arguments or "{}"
                try:
                    parsed_args = json.loads(raw_args)
                    executor = tool_executor.get(name)
                    if executor is None:
                        result_text = f"ERROR: Unknown tool '{name}'"
                    elif isinstance(parsed_args, dict):
                        result_text = str(executor(**parsed_args))
                    else:
                        result_text = str(executor(parsed_args))
                except Exception as exc:  # noqa: BLE001
                    result_text = f"ERROR: {exc}"
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result_text,
                })

        return LLMResponse(
            content="\n".join(text_parts),
            usage={"input_tokens": input_tokens, "output_tokens": output_tokens},
            raw=last_response,
        )

    @staticmethod
    def _to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for tool in tools:
            if tool.get("type") == "function":
                result.append(tool)
                continue
            result.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {"type": "object"}),
                },
            })
        return result

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
