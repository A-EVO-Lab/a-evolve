"""Tests for Anthropic SDK signature compatibility."""

from types import SimpleNamespace

import pytest

from agent_evolve.llm.anthropic import AnthropicProvider
from agent_evolve.llm.base import LLMMessage


def _response(text: str = "FIXED") -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(text=text)],
        usage=SimpleNamespace(input_tokens=5, output_tokens=1),
    )


def _provider(messages: object) -> AnthropicProvider:
    provider = AnthropicProvider.__new__(AnthropicProvider)
    provider.model = "claude-test"
    provider.client = SimpleNamespace(messages=messages)
    return provider


class MessagesWithoutTemperature:
    def __init__(self) -> None:
        self.call: dict[str, object] | None = None

    def create(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        system: str,
    ) -> SimpleNamespace:
        self.call = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "system": system,
        }
        return _response()


class MessagesWithTemperature:
    def __init__(self) -> None:
        self.temperature: float | None = None

    def create(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> SimpleNamespace:
        self.temperature = temperature
        return _response()


class MessagesRaisingTypeError:
    def create(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> SimpleNamespace:
        raise TypeError("SDK request processing failed")


def test_complete_omits_temperature_when_sdk_does_not_accept_it() -> None:
    messages_api = MessagesWithoutTemperature()
    provider = _provider(messages_api)

    result = provider.complete(
        [LLMMessage("system", "Be concise"), LLMMessage("user", "Reply FIXED")],
        max_tokens=16,
        temperature=0.2,
    )

    assert messages_api.call == {
        "model": "claude-test",
        "messages": [{"role": "user", "content": "Reply FIXED"}],
        "max_tokens": 16,
        "system": "Be concise",
    }
    assert result.content == "FIXED"
    assert result.usage == {"input_tokens": 5, "output_tokens": 1}


def test_complete_preserves_temperature_when_sdk_accepts_it() -> None:
    messages_api = MessagesWithTemperature()
    provider = _provider(messages_api)

    provider.complete([LLMMessage("user", "Reply FIXED")], temperature=0.4)

    assert messages_api.temperature == 0.4


def test_complete_does_not_mask_type_error_from_sdk_call() -> None:
    provider = _provider(MessagesRaisingTypeError())

    with pytest.raises(TypeError, match="SDK request processing failed"):
        provider.complete([LLMMessage("user", "Reply FIXED")])
