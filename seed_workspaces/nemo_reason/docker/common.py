"""Shared utilities for train.py and inference.py."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import yaml

_BOXED_RE = re.compile(r"\\boxed\s*\{([^{}]*)\}")


def load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def resolve_uri(uri: str) -> str:
    """`hf://...` → HF repo id; `local:///path` → filesystem path; passthrough otherwise."""
    for prefix in ("hf://", "local://"):
        if uri.startswith(prefix):
            return uri[len(prefix):]
    return uri


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def extract_boxed(text: str) -> str:
    """Return content of the last ``\\boxed{...}`` group, or empty string."""
    if not text:
        return ""
    matches = _BOXED_RE.findall(text)
    return matches[-1].strip() if matches else ""


def env_path(var: str, default: str) -> Path:
    """Read an env var as a Path, falling back to *default*."""
    return Path(os.environ.get(var, default))
