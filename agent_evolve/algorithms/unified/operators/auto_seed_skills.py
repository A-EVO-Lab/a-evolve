"""AutoSeedSkills — pattern-triggered skill injection.

Reference: ``agent_evolve/algorithms/adaptive_evolve/engine.py`` lines 417-462
(``_auto_seed_skills``). Independent reimplementation under ``unified/``.

Triggers (same thresholds as legacy):
- ``multi_requirement_miss`` ≥ 3 occurrences → seed ``multi-requirement-handler``
- ``wrong_entity_targeting`` ≥ 2 occurrences → seed ``entity-verification``
- Up to 2 weakest claim types with pass_rate < 0.5 → seed ``{type}-handler``
  (skill library is capped at ``state["max_skills"]`` entries, default 15).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..registry import register_operator
from ..types import MutationReport
from ._seed_skill_templates import (
    ENTITY_VERIFICATION_SKILL,
    MULTI_REQ_SKILL,
    build_claim_type_skill,
)

logger = logging.getLogger(__name__)


@register_operator("AutoSeedSkills")
class AutoSeedSkills:
    """Rule-triggered deterministic skill seeding.

    Consumes ``PatternDetector`` output (via EvidenceContext) and
    ``ClaimTypeAnalyzer`` output. Writes targeted SKILL.md files into
    ``workspace/skills/<name>/``.
    """

    WRITES: frozenset[str] = frozenset({"skills"})

    def apply(
        self,
        workspace: Any,
        context: Any,
        scope: dict[str, Any],
        state: dict[str, Any],
    ) -> MutationReport:
        if scope.get("skills") not in ("rw",):
            return MutationReport(operator_name="AutoSeedSkills", count=0)

        seeded = 0
        details: dict[str, Any] = {"seeded": []}
        max_skills = int(state.get("max_skills", 15))
        existing = {s.name for s in workspace.list_skills()}

        patterns = list(
            (context.entries.get("PatternDetector", {}) or {}).get("patterns", [])
        )
        pattern_by_name = {p["pattern_name"]: p for p in patterns}

        multi_req = pattern_by_name.get("multi_requirement_miss")
        if (
            multi_req
            and int(multi_req.get("count", 0)) >= 3
            and "multi-requirement-handler" not in existing
        ):
            _write_skill(workspace, "multi-requirement-handler", MULTI_REQ_SKILL)
            existing.add("multi-requirement-handler")
            seeded += 1
            details["seeded"].append("multi-requirement-handler")
            logger.info("Auto-seeded multi-requirement-handler skill")

        wrong_entity = pattern_by_name.get("wrong_entity_targeting")
        if (
            wrong_entity
            and int(wrong_entity.get("count", 0)) >= 2
            and "entity-verification" not in existing
        ):
            _write_skill(workspace, "entity-verification", ENTITY_VERIFICATION_SKILL)
            existing.add("entity-verification")
            seeded += 1
            details["seeded"].append("entity-verification")
            logger.info("Auto-seeded entity-verification skill")

        claim_types = context.entries.get("ClaimTypeAnalyzer", {}) or {}
        weakest = list(claim_types.get("weakest", []))
        by_type = claim_types.get("by_type", {})
        for ct, pass_rate in weakest[:2]:
            if pass_rate < 0.5 and len(existing) < max_skills - 1:
                skill_name = f"{ct}-handler"
                if skill_name in existing:
                    continue
                examples = (by_type.get(ct, {}) or {}).get("examples", []) or []
                body = build_claim_type_skill(ct, examples)
                _write_skill(workspace, skill_name, body)
                existing.add(skill_name)
                seeded += 1
                details["seeded"].append(skill_name)
                logger.info(
                    "Auto-seeded %s skill (pass rate: %.2f)", skill_name, pass_rate
                )

        # Publish the seeded skill names under the canonical
        # ``skills_added`` key so UnifiedEngine.step() can roll them up
        # into the summary alongside other operators.
        details["skills_added"] = list(details.get("seeded", []))
        return MutationReport(
            operator_name="AutoSeedSkills", count=seeded, details=details
        )


def _write_skill(workspace: Any, name: str, content: str) -> None:
    skill_dir = Path(workspace.root) / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content)
