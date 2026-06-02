"""PruneSkills — LLM-assisted redundancy removal across skills + fragments.

Reference: ``agent_evolve/algorithms/guided_synth/engine.py`` lines 743-821
(``_prune_similar``). Independent reimplementation under ``unified/``.

The operator only runs when the skill/fragment inventory has ≥ 3 entries,
matching legacy behaviour.
"""

from __future__ import annotations

import logging
from typing import Any

from ..registry import register_operator
from ..types import MutationReport

logger = logging.getLogger(__name__)


PRUNE_PROMPT = """\
You are reviewing a list of interventions (skills and prompt fragments) \
for a SWE-bench solving agent. Identify which ones are REDUNDANT — they \
teach the same methodology or give the same advice as another.

For each redundant item, output a line: REMOVE: <name>
Keep the BEST version of each unique idea. Keep items that cover \
genuinely different strategies. Output NOTHING if all items are unique.
"""


@register_operator("PruneSkills")
class PruneSkills:
    """Delete redundant skills / fragments as judged by an LLM.

    State keys:
        ``state["mock_pruner"]`` — test hook: callable(prompt) -> raw decision.
        ``state["model_id"]`` / ``state["region"]`` — optional LLM overrides.
    """

    WRITES: frozenset[str] = frozenset({"skills", "prompts"})

    DEFAULT_MODEL = "us.anthropic.claude-opus-4-6-v1"
    DEFAULT_REGION = "us-west-2"

    def apply(
        self,
        workspace: Any,
        context: Any,
        scope: dict[str, Any],
        state: dict[str, Any],
    ) -> MutationReport:
        skills_mode = scope.get("skills", "ro")
        prompts_mode = scope.get("prompts", "ro")
        if skills_mode != "rw" and prompts_mode != "rw":
            return MutationReport(operator_name="PruneSkills", count=0)

        skill_names = [s.name for s in workspace.list_skills()]
        fragment_names = list(workspace.list_fragments())

        items: list[str] = []
        for name in skill_names:
            content = workspace.read_skill(name) or ""
            body = (
                content.split("---", 2)[-1].strip()[:200]
                if "---" in content
                else content[:200]
            )
            items.append(f"SKILL: {name}\n  {body}")
        for fname in fragment_names:
            content = workspace.read_fragment(fname) or ""
            items.append(f"FRAGMENT: {fname.removesuffix('.md')}\n  {content[:200]}")

        if len(items) < 3:
            return MutationReport(operator_name="PruneSkills", count=0)

        user_msg = "## Current interventions:\n\n" + "\n\n".join(items)

        mock = state.get("mock_pruner")
        if callable(mock):
            raw = str(mock(user_msg))
        else:
            try:
                from agent_evolve.llm.bedrock import BedrockProvider
                from agent_evolve.llm.base import LLMMessage
            except ImportError as e:
                logger.warning("PruneSkills: LLM provider unavailable (%s)", e)
                return MutationReport(
                    operator_name="PruneSkills",
                    count=0,
                    details={"error": f"provider unavailable: {e}"},
                )
            llm = BedrockProvider(
                model_id=state.get("model_id", self.DEFAULT_MODEL),
                region=state.get("region", self.DEFAULT_REGION),
            )
            try:
                response = llm.complete(
                    [
                        LLMMessage(role="system", content=PRUNE_PROMPT),
                        LLMMessage(role="user", content=user_msg),
                    ],
                    max_tokens=512,
                )
                raw = response.content.strip()
            except Exception as exc:  # noqa: BLE001
                logger.error("PruneSkills: LLM call failed: %s", exc)
                return MutationReport(
                    operator_name="PruneSkills",
                    count=0,
                    details={"error": str(exc)[:200]},
                )

        to_remove: list[str] = []
        for line in raw.split("\n"):
            line = line.strip()
            if line.startswith("REMOVE:"):
                to_remove.append(line.split(":", 1)[1].strip())

        removed: list[str] = []
        for name in to_remove:
            if name in skill_names and skills_mode == "rw":
                workspace.delete_skill(name)
                removed.append(f"skill:{name}")
                logger.info("Pruned skill: %s", name)
                continue
            fragment_basenames = [f.removesuffix(".md") for f in fragment_names]
            if (name in fragment_basenames or f"{name}.md" in fragment_names) and prompts_mode == "rw":
                frag_path = workspace.prompts_dir / "fragments" / f"{name}.md"
                if frag_path.exists():
                    frag_path.unlink()
                current_prompt = workspace.read_prompt()
                marker = f"<!-- evolve:{name} -->"
                if marker in current_prompt:
                    lines = current_prompt.split("\n")
                    new_lines = []
                    skip = False
                    for ln in lines:
                        if marker in ln:
                            skip = True
                            continue
                        if skip and ln.startswith("<!-- evolve:"):
                            skip = False
                        if skip and ln.startswith("## ") and "evolve" not in ln.lower():
                            skip = False
                        if not skip:
                            new_lines.append(ln)
                    workspace.write_prompt("\n".join(new_lines))
                removed.append(f"fragment:{name}")
                logger.info("Pruned fragment: %s", name)

        return MutationReport(
            operator_name="PruneSkills",
            count=len(removed),
            details={"removed": removed},
        )
