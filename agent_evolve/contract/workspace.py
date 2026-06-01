"""AgentWorkspace -- typed read/write access to an agent workspace directory."""

from __future__ import annotations

import json
import logging
import re
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import yaml

from ..types import SkillMeta

_log = logging.getLogger(__name__)


def _parse_jsonl(path: Path) -> list[dict[str, Any]]:
    """Parse a JSONL file, skipping corrupt lines with a warning."""
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    with open(path) as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                _log.warning("Skipping corrupt line %d in %s: %s",
                             lineno, path, line.rstrip()[:120])
    return entries


class AgentWorkspace:
    """Provides typed read/write access to an agent workspace following the FS contract.

    This is the primary interface used by both BaseAgent (to load state) and
    the Evolver (to mutate state).
    """

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.prompts_dir = self.root / "prompts"
        self.skills_dir = self.root / "skills"
        self.drafts_dir = self.skills_dir / "_drafts"
        self.tools_dir = self.root / "tools"
        self.memory_dir = self.root / "memory"
        self.infra_dir = self.root / "infra"  # V2: framework-run pipelines with network access
        self.evolution_dir = self.root / "evolution"

    # ── Prompts ──────────────────────────────────────────────────────

    def read_prompt(self) -> str:
        path = self.prompts_dir / "system.md"
        return path.read_text() if path.exists() else ""

    def write_prompt(self, content: str) -> None:
        self.prompts_dir.mkdir(parents=True, exist_ok=True)
        (self.prompts_dir / "system.md").write_text(content)

    def read_fragment(self, name: str) -> str:
        path = self.prompts_dir / "fragments" / name
        return path.read_text() if path.exists() else ""

    def write_fragment(self, name: str, content: str) -> None:
        frag_dir = self.prompts_dir / "fragments"
        frag_dir.mkdir(parents=True, exist_ok=True)
        (frag_dir / name).write_text(content)

    def list_fragments(self) -> list[str]:
        frag_dir = self.prompts_dir / "fragments"
        if not frag_dir.exists():
            return []
        return sorted(f.name for f in frag_dir.iterdir() if f.is_file())

    # ── Skills ───────────────────────────────────────────────────────

    def list_skills(self) -> list[SkillMeta]:
        if not self.skills_dir.exists():
            return []
        skills = []
        for d in sorted(self.skills_dir.iterdir()):
            if not d.is_dir() or d.name.startswith("_"):
                continue
            skill_file = d / "SKILL.md"
            if skill_file.exists():
                meta = _parse_skill_frontmatter(skill_file)
                meta.path = str(d.relative_to(self.root))
                skills.append(meta)
        return skills

    def read_skill(self, name: str) -> str:
        path = self.skills_dir / name / "SKILL.md"
        return path.read_text() if path.exists() else ""

    def write_skill(self, name: str, content: str) -> None:
        skill_dir = self.skills_dir / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(content)

    def delete_skill(self, name: str) -> None:
        skill_dir = self.skills_dir / name
        if skill_dir.exists():
            shutil.rmtree(skill_dir)

    # ── Drafts ───────────────────────────────────────────────────────

    def list_drafts(self) -> list[dict[str, str]]:
        if not self.drafts_dir.exists():
            return []
        return [
            {"name": f.stem, "content": f.read_text()}
            for f in sorted(self.drafts_dir.glob("*.md"))
        ]

    def write_draft(self, name: str, content: str) -> None:
        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        (self.drafts_dir / f"{name}.md").write_text(content)

    def clear_drafts(self) -> None:
        if self.drafts_dir.exists():
            for f in self.drafts_dir.glob("*.md"):
                f.unlink()

    # ── Tools ────────────────────────────────────────────────────────

    def read_tool_registry(self) -> list[dict[str, Any]]:
        path = self.tools_dir / "registry.yaml"
        if not path.exists():
            return []
        with open(path) as f:
            data = yaml.safe_load(f)
        return data.get("tools", []) if isinstance(data, dict) else []

    def write_tool_registry(self, tools: list[dict[str, Any]]) -> None:
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        with open(self.tools_dir / "registry.yaml", "w") as f:
            yaml.dump({"tools": tools}, f, default_flow_style=False)

    def read_tool(self, name: str) -> str:
        path = self.tools_dir / f"{name}.py"
        return path.read_text() if path.exists() else ""

    def write_tool(self, name: str, content: str) -> None:
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        (self.tools_dir / f"{name}.py").write_text(content)

    # ── Memory ───────────────────────────────────────────────────────

    def add_memory(self, entry: dict[str, Any], category: str = "episodic") -> None:
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        path = self.memory_dir / f"{category}.jsonl"
        with open(path, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def read_memories(self, category: str = "episodic", limit: int = 100) -> list[dict[str, Any]]:
        return _parse_jsonl(self.memory_dir / f"{category}.jsonl")[-limit:]

    def read_all_memories(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.memory_dir.exists():
            return []
        all_memories: list[dict[str, Any]] = []
        for jsonl in sorted(self.memory_dir.glob("*.jsonl")):
            for entry in _parse_jsonl(jsonl):
                entry.setdefault("_category", jsonl.stem)
                all_memories.append(entry)
        return all_memories[-limit:]

    # ── Harness (optional scaffolding code, mutated by MetaHarness) ──

    def read_harness(self) -> str | None:
        """Read harness.py from the workspace root, or None if absent."""
        path = self.root / "harness.py"
        return path.read_text() if path.exists() else None

    def write_harness(self, content: str) -> None:
        """Write harness.py to the workspace root."""
        (self.root / "harness.py").write_text(content)

    # ── Evolution metadata (read-only for agents, managed by engine) ─

    def read_evolution_history(self) -> list[dict[str, Any]]:
        return _parse_jsonl(self.evolution_dir / "history.jsonl")

    # ── Layer protection ────────────────────────────────────────
    # V2: enforce gating by snapshotting layer dirs before handing the
    # workspace to the evolver; restore them on exit to discard any
    # changes made to disabled layers.

    @contextmanager
    def protect(self, layers: list[str]):
        """Snapshot layer dirs on enter; restore on exit, discarding changes."""
        dir_map = {
            "prompts": self.prompts_dir,
            "skills": self.skills_dir,
            "memory": self.memory_dir,
            "tools": self.tools_dir,
            "infra": self.infra_dir,
        }
        backups: dict[str, tuple[Path, Path | None]] = {}
        for layer in layers:
            src = dir_map[layer]
            if src.exists():
                bak = src.with_suffix(".protect_bak")
                if bak.exists():
                    shutil.rmtree(bak)
                shutil.copytree(src, bak)
                backups[layer] = (src, bak)
            else:
                backups[layer] = (src, None)
        try:
            yield
        finally:
            for src, bak in backups.values():
                if src.exists():
                    shutil.rmtree(src)
                if bak is not None:
                    bak.rename(src)

    def read_evolution_metrics(self) -> dict[str, Any]:
        path = self.evolution_dir / "metrics.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text())


# ── Helpers ──────────────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


def _parse_skill_frontmatter(path: Path) -> SkillMeta:
    """Extract name + description from SKILL.md YAML frontmatter."""
    text = path.read_text()
    m = _FRONTMATTER_RE.match(text)
    if m:
        try:
            meta = yaml.safe_load(m.group(1)) or {}
            return SkillMeta(
                name=meta.get("name", path.parent.name),
                description=meta.get("description", ""),
                path="",
            )
        except yaml.YAMLError:
            pass
    return SkillMeta(name=path.parent.name, description="", path="")
