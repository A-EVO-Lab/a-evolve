"""Atlas Cloud OpenAI-compatible LLM provider."""

from __future__ import annotations

import os

from .openai import OpenAIProvider

ATLAS_CLOUD_API_BASE = "https://api.atlascloud.ai/v1"
ATLAS_CLOUD_DEFAULT_MODEL = "qwen/qwen3.5-flash"


class AtlasCloudProvider(OpenAIProvider):
    """LLM provider using Atlas Cloud's OpenAI-compatible chat API."""

    def __init__(
        self,
        model: str = ATLAS_CLOUD_DEFAULT_MODEL,
        api_key: str | None = None,
        base_url: str | None = None,
    ):
        resolved_api_key = (
            api_key
            or os.environ.get("ATLASCLOUD_API_KEY")
            or os.environ.get("ATLAS_CLOUD_API_KEY")
        )
        resolved_base_url = (
            base_url
            or os.environ.get("ATLASCLOUD_API_BASE")
            or os.environ.get("ATLAS_CLOUD_API_BASE")
            or ATLAS_CLOUD_API_BASE
        )
        super().__init__(
            model=model,
            api_key=resolved_api_key,
            base_url=resolved_base_url,
        )
