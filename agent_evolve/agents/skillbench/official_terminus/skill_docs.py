"""Container-adapted vendored skill loader based on official Terminus implementation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..docker_env import SkillBenchContainer

DEFAULT_SKILL_DIRS = [
    Path("/root/.terminus/skills"),
    Path("/root/.claude/skills"),
    Path("/root/.codex/skills"),
    Path("/root/.opencode/skill"),
    Path("/root/.agents/skills"),
    Path("/root/.goose/skills"),
    Path("/root/.factory/skills"),
    Path("/root/.github/skills"),
]


@dataclass(frozen=True)
class SkillMetadata:
    name: str
    description: str
    frontmatter: str
    location: str


class SkillDocLoader:
    """Synchronous container adapter mirroring official SkillDocLoader semantics."""

    def __init__(
        self,
        container: SkillBenchContainer,
        max_total_chars: int = 16000,
        max_skill_chars: int = 4000,
    ) -> None:
        self._container = container
        self._max_total_chars = max_total_chars
        self._max_skill_chars = max_skill_chars
        self._last_metadata: list[SkillMetadata] = []

    def build_index(self, roots: Iterable[Path]) -> str:
        metadata = self._collect_metadata(roots)
        self._last_metadata = metadata
        if not metadata:
            return "No skills available."

        lines: list[str] = ["Available skills:"]
        remaining = self._max_total_chars
        for entry in metadata:
            line = f"- {entry.name}: {entry.description or 'No description provided.'}"
            if remaining <= 0:
                lines.append("(Additional skills omitted for length.)")
                break
            if len(line) > remaining:
                line = line[:remaining] + " (Truncated)"
            lines.append(line)
            remaining -= len(line)

        return "\n".join(lines).strip()

    def load_skill(self, name: str, roots: Iterable[Path]) -> str | None:
        skill_dir = self._find_skill_dir(name, roots)
        if skill_dir is None:
            return None

        skill_md = skill_dir / "SKILL.md"
        content = self._read_file(skill_md)
        if content is None:
            return None
        return content

    def get_metadata(self) -> list[SkillMetadata]:
        return list(self._last_metadata)

    def load_references(self, name: str, roots: Iterable[Path]) -> list[tuple[str, str]]:
        skill_dir = self._find_skill_dir(name, roots)
        if skill_dir is None:
            return []
        return self._read_references(skill_dir / "references")

    def _collect_metadata(self, roots: Iterable[Path]) -> list[SkillMetadata]:
        seen = set()
        metadata: list[SkillMetadata] = []

        for root in roots:
            root_contents = self._list_dir(root)
            if root_contents is None:
                continue

            for skill_name in sorted(root_contents):
                if skill_name in seen:
                    continue

                skill_dir = root / skill_name
                skill_md = skill_dir / "SKILL.md"
                text = self._read_file(skill_md)

                if text is None:
                    continue

                frontmatter = self._parse_frontmatter(text)
                description = frontmatter.get("description", "").strip()
                frontmatter_block = self._extract_frontmatter_block(text)
                metadata.append(
                    SkillMetadata(
                        name=skill_name,
                        description=description,
                        frontmatter=frontmatter_block,
                        location=str(skill_dir),
                    )
                )
                seen.add(skill_name)
        return metadata

    def _find_skill_dir(self, name: str, roots: Iterable[Path]) -> Path | None:
        for root in roots:
            candidate = root / name
            skill_md = candidate / "SKILL.md"
            if self._file_exists(skill_md):
                return candidate
        return None

    def _file_exists(self, path: Path) -> bool:
        _, _, rc = self._container.exec_command(f"test -f {str(path)!r}")
        return rc == 0

    def _list_dir(self, path: Path) -> list[str] | None:
        stdout, _, rc = self._container.exec_command(f"ls -1 {str(path)!r}")
        if rc != 0:
            return None
        stdout = self._sanitize_output(stdout or "")
        if not stdout:
            return []
        return [line.strip() for line in stdout.splitlines() if line.strip()]

    def _read_file(self, path: Path) -> str | None:
        try:
            text = self._container.read_file(str(path))
        except FileNotFoundError:
            return None

        text = self._sanitize_output(text or "")
        if not text:
            return None

        if len(text) > self._max_skill_chars:
            return text[: self._max_skill_chars] + "\n(Truncated)"
        return text

    def _read_references(self, ref_dir: Path) -> list[tuple[str, str]]:
        files = self._list_dir(ref_dir)
        if files is None:
            return []

        refs: list[tuple[str, str]] = []
        for fname in sorted(files):
            if not fname.endswith(".md"):
                continue
            content = self._read_file(ref_dir / fname)
            if content:
                refs.append((fname, content))
        return refs

    @staticmethod
    def _sanitize_output(text: str) -> str:
        if not text:
            return ""
        lines = []
        for line in text.splitlines():
            if SkillDocLoader._is_shell_warning(line):
                continue
            lines.append(line)
        return "\n".join(lines).strip()

    @staticmethod
    def _is_shell_warning(line: str) -> bool:
        stripped = line.strip()
        return (
            stripped.startswith("bash:")
            or stripped.startswith("sh:")
            or stripped.startswith("cannot set terminal process group")
            or stripped.startswith("no job control in this shell")
        )

    def _parse_frontmatter(self, text: str) -> dict[str, str]:
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            return {}
        if lines[0].strip() != "---":
            return {}
        end_index = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end_index = i
                break
        if end_index is None:
            return {}
        metadata: dict[str, str] = {}
        for line in lines[1:end_index]:
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key in {"name", "description"}:
                metadata[key] = value
        return metadata

    def _extract_frontmatter_block(self, text: str) -> str:
        lines = text.splitlines()
        if not lines or lines[0].strip() != "---":
            return ""
        end_index = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end_index = i
                break
        if end_index is None:
            return ""
        return "\n".join(lines[: end_index + 1]).strip()
