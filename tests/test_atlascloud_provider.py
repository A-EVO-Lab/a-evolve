"""Tests for Atlas Cloud provider resolution."""

from __future__ import annotations

import sys
import types

from agent_evolve.algorithms.adaptive_skill.tools import create_default_llm as create_skill_llm
from agent_evolve.config import EvolveConfig
from agent_evolve.llm.model_resolver import is_atlascloud_model, strip_atlascloud_prefix


class FakeOpenAI:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=lambda **_: None)
        )


def test_atlascloud_model_prefix_helpers():
    assert is_atlascloud_model("atlascloud:qwen/qwen3.5-flash")
    assert is_atlascloud_model("atlascloud/deepseek-ai/deepseek-v4-pro")
    assert not is_atlascloud_model("gpt-4o")
    assert strip_atlascloud_prefix("atlascloud:qwen/qwen3.5-flash") == "qwen/qwen3.5-flash"
    assert (
        strip_atlascloud_prefix("atlascloud/deepseek-ai/deepseek-v4-pro")
        == "deepseek-ai/deepseek-v4-pro"
    )


def test_atlascloud_provider_uses_openai_compatible_endpoint(monkeypatch):
    fake_module = types.SimpleNamespace(OpenAI=FakeOpenAI)
    monkeypatch.setitem(sys.modules, "openai", fake_module)

    from agent_evolve.llm.atlascloud import AtlasCloudProvider

    provider = AtlasCloudProvider(model="qwen/qwen3.5-flash", api_key="ac-test")

    assert provider.model == "qwen/qwen3.5-flash"
    assert provider.client.kwargs == {
        "api_key": "ac-test",
        "base_url": "https://api.atlascloud.ai/v1",
    }


def test_default_llm_factory_resolves_atlascloud(monkeypatch):
    fake_module = types.SimpleNamespace(OpenAI=FakeOpenAI)
    monkeypatch.setitem(sys.modules, "openai", fake_module)
    monkeypatch.setenv("ATLASCLOUD_API_KEY", "ac-env")

    provider = create_skill_llm(
        EvolveConfig(evolver_model="atlascloud:deepseek-ai/deepseek-v4-pro")
    )

    assert provider.model == "deepseek-ai/deepseek-v4-pro"
    assert provider.client.kwargs["api_key"] == "ac-env"
    assert provider.client.kwargs["base_url"] == "https://api.atlascloud.ai/v1"
