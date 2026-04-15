"""Workspace ↔ GEPA candidate serialization.

Converts A-Evolve workspace layers (system prompt, fragments, skills, memory)
to GEPA's dict[str, str] candidate format and back.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...config import EvolveConfig
    from ...contract.workspace import AgentWorkspace

_FRAGMENT_DELIM = "=== FRAGMENT: {} ==="
_SKILL_DELIM = "=== SKILL: {} ==="
_FRAGMENT_RE = re.compile(r"^=== FRAGMENT: (.+?) ===$", re.MULTILINE)
_SKILL_RE = re.compile(r"^=== SKILL: (.+?) ===$", re.MULTILINE)


def build_candidate(workspace: AgentWorkspace, config: EvolveConfig) -> dict[str, str]:
    """Read workspace layers into a GEPA candidate dict."""
    candidate: dict[str, str] = {}
    if config.evolve_prompts:
        candidate["system_prompt"] = workspace.read_prompt()
        candidate["prompt_fragments"] = serialize_fragments(workspace)
    if config.evolve_skills:
        candidate["skills"] = serialize_skills(workspace)
    if config.evolve_memory:
        candidate["memory"] = serialize_memory(workspace)
    return candidate


def serialize_fragments(workspace: AgentWorkspace) -> str:
    parts: list[str] = []
    for name in workspace.list_fragments():
        content = workspace.read_fragment(name)
        parts.append(_FRAGMENT_DELIM.format(name))
        parts.append(content)
    return "\n".join(parts)


def serialize_skills(workspace: AgentWorkspace) -> str:
    parts: list[str] = []
    for skill_meta in workspace.list_skills():
        content = workspace.read_skill(skill_meta.name)
        parts.append(_SKILL_DELIM.format(skill_meta.name))
        parts.append(content)
    return "\n".join(parts)


def serialize_memory(workspace: AgentWorkspace) -> str:
    all_memories = workspace.read_all_memories(limit=10000)
    lines: list[str] = []
    for entry in all_memories:
        lines.append(json.dumps(entry, default=str))
    return "\n".join(lines)


def parse_fragments(blob: str) -> list[tuple[str, str]]:
    return _parse_delimited(blob, _FRAGMENT_RE)


def parse_skills(blob: str) -> list[tuple[str, str]]:
    return _parse_delimited(blob, _SKILL_RE)


def _parse_delimited(blob: str, pattern: re.Pattern) -> list[tuple[str, str]]:
    matches = list(pattern.finditer(blob))
    if not matches:
        return []
    result: list[tuple[str, str]] = []
    for i, match in enumerate(matches):
        name = match.group(1)
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(blob)
        content = blob[start:end].strip()
        result.append((name, content))
    return result


def restore_candidate(
    workspace: AgentWorkspace, candidate: dict[str, str], config: EvolveConfig
) -> None:
    if "system_prompt" in candidate:
        workspace.write_prompt(candidate["system_prompt"])
    if "prompt_fragments" in candidate:
        for name in workspace.list_fragments():
            (workspace.prompts_dir / "fragments" / name).unlink()
        for name, content in parse_fragments(candidate["prompt_fragments"]):
            workspace.write_fragment(name, content)
    if "skills" in candidate:
        for skill in workspace.list_skills():
            workspace.delete_skill(skill.name)
        for name, content in parse_skills(candidate["skills"]):
            workspace.write_skill(name, content)
    if "memory" in candidate:
        restore_memory(workspace, candidate["memory"])


def restore_memory(workspace: AgentWorkspace, memory_blob: str) -> None:
    if workspace.memory_dir.exists():
        for f in workspace.memory_dir.glob("*.jsonl"):
            f.unlink()
    for line in memory_blob.splitlines():
        line = line.strip()
        if not line:
            continue
        entry = json.loads(line)
        category = entry.pop("_category", "episodic")
        workspace.add_memory(entry, category=category)
