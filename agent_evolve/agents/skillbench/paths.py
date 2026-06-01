"""Path helpers scoped to SkillBench support."""

from __future__ import annotations

import os
from pathlib import Path

_SKILLBENCH_ROOT = Path(__file__).resolve().parent
_AGENT_EVOLVE_ROOT = _SKILLBENCH_ROOT.parents[2]
_SOURCE_ROOT = _AGENT_EVOLVE_ROOT.parent


def resolve_skillbench_relative_path(
    path_value: str | Path | None,
    *,
    repo_root: Path | None = None,
) -> Path | None:
    """Resolve a path relative to cwd first, then to a repo-like root."""
    if path_value is None:
        return None
    if isinstance(path_value, str) and not path_value.strip():
        return None

    raw = Path(path_value).expanduser()
    if raw.is_absolute():
        return raw.resolve()

    cwd_candidate = (Path.cwd() / raw).resolve()
    if cwd_candidate.exists():
        return cwd_candidate

    root = repo_root or _SOURCE_ROOT
    return (root / raw).resolve()


def resolve_skillbench_seed_workspaces_root() -> Path:
    """Locate bundled seed workspaces for SkillBench in source and wheel installs."""
    candidates = [
        _SOURCE_ROOT / "seed_workspaces",
        _AGENT_EVOLVE_ROOT / "seed_workspaces",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[-1]


def skillbench_default_cache_root(app_name: str = "agent-evolve") -> Path:
    """Return the user cache directory used by SkillBench bootstrap."""
    xdg_cache_home = os.environ.get("XDG_CACHE_HOME")
    if xdg_cache_home:
        return Path(xdg_cache_home).expanduser().resolve() / app_name
    return Path.home().expanduser().resolve() / ".cache" / app_name
