"""Evolution configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class EvolveConfig:
    """Configuration for an evolution run.

    The ``extra`` dict supports the following MCP-related keys:

    - ``mcp_env_file`` (str): Path to a ``.env`` file containing API keys.
      Falls back to the ``MCP_ENV_FILE`` environment variable. Default: ``".env"``.
    - ``mcp_aws_secret_name`` (str): AWS Secrets Manager secret name for API keys.
    - ``mcp_aws_region`` (str): AWS region for Secrets Manager lookups.
    - ``mcp_server_key_map`` (str): Path to a custom YAML server-to-key mapping file.
    """

    batch_size: int = 10
    max_cycles: int = 20
    holdout_ratio: float = 0.2
    parallel_workers: int = 1
    parallel_backend: str = "thread"

    # Gating: which layers the evolver is allowed to mutate
    evolve_prompts: bool = True
    evolve_skills: bool = True
    evolve_memory: bool = True
    evolve_tools: bool = False

    # When True, the evolver only sees agent trajectories (tool calls and
    # outputs) — no pass/fail, score, or test output.  This forces the
    # meta-learner to infer improvement opportunities from behavior alone.
    trajectory_only: bool = False

    # Evolver LLM
    evolver_model: str = "us.anthropic.claude-opus-4-6-v1"
    evolver_max_tokens: int = 16384

    # Convergence
    egl_threshold: float = 0.05
    egl_window: int = 3

    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: str | Path) -> EvolveConfig:
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        known = {k: v for k, v in raw.items() if k in known_fields}
        extra = {k: v for k, v in raw.items() if k not in known_fields}
        return cls(**known, extra=extra)
