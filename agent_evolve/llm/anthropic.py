"""Anthropic Claude LLM provider."""

from __future__ import annotations

from typing import Any

from .base import LLMMessage, LLMProvider, LLMResponse


class AnthropicProvider(LLMProvider):
    """LLM provider using the Anthropic API (Claude models)."""

    def __init__(self, model: str = "claude-sonnet-4-6", api_key: str | None = None):
        try:
            import anthropic
        except ImportError:
            raise ImportError("pip install anthropic  (or: pip install agent-evolve[anthropic])")

        self.model = model
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    def complete(
        self,
        messages: list[LLMMessage],
        max_tokens: int = 4096,
        temperature: float = 0.0,
        **kwargs,
    ) -> LLMResponse:
        system = None
        api_messages = []
        for m in messages:
            if m.role == "system":
                system = m.content
            else:
                api_messages.append({"role": m.role, "content": m.content})

        params: dict[str, Any] = {
            "model": self.model,
            "messages": api_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            params["system"] = system

        response = self.client.messages.create(**params)
        return LLMResponse(
            content=response.content[0].text if response.content else "",
            usage={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
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
        system = None
        api_messages = []
        for m in messages:
            if m.role == "system":
                system = m.content
            else:
                api_messages.append({"role": m.role, "content": m.content})

        params: dict[str, Any] = {
            "model": self.model,
            "messages": api_messages,
            "max_tokens": max_tokens,
            "tools": tools,
        }
        if system:
            params["system"] = system

        response = self.client.messages.create(**params)
        text_parts = [b.text for b in response.content if hasattr(b, "text")]
        return LLMResponse(
            content="\n".join(text_parts),
            usage={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
            raw=response,
        )
