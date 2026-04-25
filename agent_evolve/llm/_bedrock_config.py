"""Shared Bedrock botocore Config builder.

All Bedrock client constructors in this codebase (the canonical
`BedrockProvider`, the strands-based agents in `agents/swe/agent.py` and
`agents/mcp_mh/agent.py`, and the hand-rolled boto3 client in
`agents/terminal/react_solver.py`) read retry + timeout from the same
three environment variables so EvolverBench (and any caller) can tune
them from a wrapper script without editing Python source:

  BEDROCK_RETRY_MAX_ATTEMPTS   default 15
  BEDROCK_READ_TIMEOUT_SEC     default 600
  BEDROCK_CONNECT_TIMEOUT_SEC  default 30

The retry mode is always `adaptive` (boto3's recommended throttle-aware
mode). To disable retries entirely (e.g. when the caller layers its own
retry on top), pass `disable_retries=True` to `bedrock_boto_config()`.
"""
from __future__ import annotations

import os


_DEFAULT_RETRY_MAX_ATTEMPTS = 15
_DEFAULT_READ_TIMEOUT_SEC = 600
_DEFAULT_CONNECT_TIMEOUT_SEC = 30


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        v = int(raw)
        if v < 0:
            return default
        return v
    except ValueError:
        return default


def bedrock_boto_config(disable_retries: bool = False):
    """Build a botocore Config tuned for Bedrock sweeps.

    Reads BEDROCK_RETRY_MAX_ATTEMPTS / BEDROCK_READ_TIMEOUT_SEC /
    BEDROCK_CONNECT_TIMEOUT_SEC from the environment with sensible
    defaults (15 / 600 / 30). Always uses retry mode "adaptive".

    When `disable_retries=True`, sets `max_attempts=0` so the caller
    can layer its own retry policy on top (used by the TB react_solver
    which has hand-rolled retry already).
    """
    from botocore.config import Config as BotoConfig

    read_timeout = _env_int("BEDROCK_READ_TIMEOUT_SEC", _DEFAULT_READ_TIMEOUT_SEC)
    connect_timeout = _env_int("BEDROCK_CONNECT_TIMEOUT_SEC", _DEFAULT_CONNECT_TIMEOUT_SEC)
    if disable_retries:
        retries = {"max_attempts": 0}
    else:
        max_attempts = _env_int("BEDROCK_RETRY_MAX_ATTEMPTS", _DEFAULT_RETRY_MAX_ATTEMPTS)
        retries = {"max_attempts": max_attempts, "mode": "adaptive"}
    return BotoConfig(
        retries=retries,
        read_timeout=read_timeout,
        connect_timeout=connect_timeout,
    )


def bedrock_retry_max_attempts() -> int:
    """Same env semantics as bedrock_boto_config(); used by hand-rolled retry loops."""
    return _env_int("BEDROCK_RETRY_MAX_ATTEMPTS", _DEFAULT_RETRY_MAX_ATTEMPTS)


def bedrock_read_timeout_sec() -> int:
    return _env_int("BEDROCK_READ_TIMEOUT_SEC", _DEFAULT_READ_TIMEOUT_SEC)
