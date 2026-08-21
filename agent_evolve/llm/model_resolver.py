"""Helpers for resolving configured LLM model strings."""

from __future__ import annotations

ATLAS_CLOUD_PREFIXES = ("atlascloud:", "atlascloud/")
ATLAS_CLOUD_DEFAULT_MODEL = "qwen/qwen3.5-flash"


def is_atlascloud_model(model: str) -> bool:
    """Return whether a model string requests Atlas Cloud explicitly."""
    return model.startswith(ATLAS_CLOUD_PREFIXES)


def strip_atlascloud_prefix(model: str) -> str:
    """Return the Atlas Cloud model id after removing the provider prefix."""
    for prefix in ATLAS_CLOUD_PREFIXES:
        if model.startswith(prefix):
            return model.removeprefix(prefix) or ATLAS_CLOUD_DEFAULT_MODEL
    return model
