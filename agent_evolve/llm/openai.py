"""OpenAI GPT LLM provider."""

from __future__ import annotations

from typing import Any

from .base import LLMMessage, LLMProvider, LLMResponse


class OpenAIProvider(LLMProvider):
    """LLM provider using the OpenAI API (GPT / o-series models)."""

    def __init__(self, model: str = "gpt-4o", api_key: str | None = None):
        try:
            import openai
        except ImportError:
            raise ImportError("pip install openai  (or: pip install agent-evolve[openai])")

        self.model = model
        self.client = openai.OpenAI(api_key=api_key) if api_key else openai.OpenAI()

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
            tools=tools,
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
