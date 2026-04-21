"""SanityCheck — deterministic post-mutation workspace cleanup.

Reference: ``agent_evolve/algorithms/adaptive_evolve/engine.py`` lines
615-717 (``_workspace_sanity_check``). Independent reimplementation under
``unified/``.

Cleanup steps:
1. Truncate prompt over ``state["prompt_max_chars"]`` preserving seed content.
2. Remove empty skills (body < 20 chars).
3. Deduplicate skills by Jaccard word overlap (>0.6).
4. Strip overfitting batch/cycle-specific lines from the prompt.
5. Enforce a ``state["max_skills"]`` cap.
6. Restore the seed identity paragraph if removed.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ..registry import register_operator
from ..types import MutationReport

logger = logging.getLogger(__name__)


_OVERFITTING_PATTERNS = (
    r"^.*B\d+:.*$",
    r"^.*batch \d+.*$",
    r"^.*\d+/\d+ at.*$",
    r"^.*\d+ claims? lost.*$",
)


@register_operator("SanityCheck")
class SanityCheck:
    """Deterministic post-mutation cleanup. No LLM calls."""

    WRITES: frozenset[str] = frozenset({"prompts", "skills"})

    def apply(
        self,
        workspace: Any,
        context: Any,
        scope: dict[str, Any],
        state: dict[str, Any],
    ) -> MutationReport:
        fixes: list[str] = []
        prompt_max_chars = int(state.get("prompt_max_chars", 4000))
        max_skills = int(state.get("max_skills", 15))
        seed_prompt = state.get("seed_prompt")
        if seed_prompt is None:
            seed_prompt = workspace.read_prompt()
            state["seed_prompt"] = seed_prompt

        prompts_mode = scope.get("prompts", "ro")
        skills_mode = scope.get("skills", "ro")

        # 1. Truncate bloated prompt.
        if prompts_mode == "rw":
            prompt = workspace.read_prompt()
            if len(prompt) > prompt_max_chars:
                truncated = _truncate_prompt(prompt, seed_prompt, prompt_max_chars)
                workspace.write_prompt(truncated)
                fixes.append(
                    f"Truncated prompt: {len(prompt)} -> {len(truncated)} chars"
                )

        # 2. Remove empty skills.
        if skills_mode == "rw":
            for skill in workspace.list_skills():
                content = workspace.read_skill(skill.name)
                body = _strip_frontmatter(content)
                if len(body.strip()) < 20:
                    workspace.delete_skill(skill.name)
                    fixes.append(f"Removed empty skill: {skill.name}")

        # 3. Deduplicate skills by Jaccard overlap.
        if skills_mode == "rw":
            skills = workspace.list_skills()
            if len(skills) >= 2:
                words_by_name: dict[str, set[str]] = {}
                size_by_name: dict[str, int] = {}
                for s in skills:
                    content = workspace.read_skill(s.name)
                    body = _strip_frontmatter(content).lower()
                    words_by_name[s.name] = set(re.findall(r"\b[a-z]{3,}\b", body))
                    size_by_name[s.name] = len(body)
                names = list(words_by_name)
                removed: set[str] = set()
                for i in range(len(names)):
                    if names[i] in removed:
                        continue
                    for j in range(i + 1, len(names)):
                        if names[j] in removed:
                            continue
                        w1 = words_by_name[names[i]]
                        w2 = words_by_name[names[j]]
                        if not w1 or not w2:
                            continue
                        jaccard = len(w1 & w2) / len(w1 | w2)
                        if jaccard > 0.6:
                            victim = (
                                names[i]
                                if size_by_name[names[i]] < size_by_name[names[j]]
                                else names[j]
                            )
                            workspace.delete_skill(victim)
                            removed.add(victim)
                            fixes.append(
                                f"Removed duplicate skill: {victim} "
                                f"(jaccard={jaccard:.2f})"
                            )
                            break

        # 4. Strip overfitting lines from prompt.
        if prompts_mode == "rw":
            prompt = workspace.read_prompt()
            lines = prompt.splitlines()
            cleaned = []
            stripped_count = 0
            for line in lines:
                if any(
                    re.search(p, line, re.IGNORECASE) for p in _OVERFITTING_PATTERNS
                ):
                    stripped_count += 1
                else:
                    cleaned.append(line)
            if stripped_count > 0:
                workspace.write_prompt("\n".join(cleaned))
                fixes.append(
                    f"Stripped {stripped_count} overfitting line(s) from prompt"
                )

        # 5. Enforce skill count cap.
        if skills_mode == "rw":
            skills = workspace.list_skills()
            if len(skills) > max_skills:
                excess = skills[:-max_skills]
                for s in excess:
                    workspace.delete_skill(s.name)
                    fixes.append(f"Removed excess skill: {s.name}")

        # 6. Restore seed identity paragraph if removed.
        if prompts_mode == "rw" and seed_prompt:
            seed_identity = seed_prompt.split("\n\n")[0].strip()
            current = workspace.read_prompt()
            if seed_identity and seed_identity not in current:
                workspace.write_prompt(seed_identity + "\n\n" + current)
                fixes.append("Restored seed identity paragraph")

        if fixes:
            logger.info("SanityCheck fixes: %s", fixes)

        return MutationReport(
            operator_name="SanityCheck",
            count=len(fixes),
            details={"fixes": fixes},
        )


def _strip_frontmatter(content: str) -> str:
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            return parts[2]
    return content


def _truncate_prompt(prompt: str, seed: str, limit: int) -> str:
    if len(prompt) <= limit:
        return prompt
    if seed and len(seed) <= limit:
        sections = re.split(r"(?=^## )", prompt, flags=re.MULTILINE)
        seed_sections = re.split(r"(?=^## )", seed, flags=re.MULTILINE)
        seed_headers = {
            s.split("\n")[0].strip() for s in seed_sections if s.strip()
        }
        result = seed.rstrip()
        remaining = limit - len(result)
        for section in sections:
            header = section.split("\n")[0].strip()
            if header in seed_headers or not header:
                continue
            if len(section) + 2 <= remaining:
                result += "\n\n" + section.rstrip()
                remaining -= len(section) + 2
        return result.rstrip() + "\n"
    sections = re.split(r"(?=^## )", prompt, flags=re.MULTILINE)
    parts = [sections[0]] if sections else []
    remaining = limit - len(parts[0]) if parts else limit
    for section in sections[1:]:
        if len(section) <= remaining:
            parts.append(section)
            remaining -= len(section)
    return "".join(parts).rstrip() + "\n"
