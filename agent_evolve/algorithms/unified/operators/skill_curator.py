"""SkillCurator — LLM curator for solver-proposed skills.

Reference: ``agent_evolve/algorithms/guided_synth/engine.py`` lines 319-431
(``_curate_proposals`` + ``_execute_curation``). Independent reimplementation
under ``unified/``.

Consumes ``ProposalReader`` output and calls an LLM curator which emits
decisions in a small DSL:

    ACCEPT: <name>
    REPLACE: <new> REPLACES <old>
    MERGE: <prop> INTO <existing>
    NEW_CONTENT: ...
    SKIP: <name>
"""

from __future__ import annotations

import logging
from typing import Any

from ..registry import register_operator
from ..types import MutationReport

logger = logging.getLogger(__name__)


GUIDED_SYNTHESIS_PROMPT = """\
You are a SKILL CURATOR for a SWE-bench solving agent. Solvers propose skills \
after completing tasks. Your job is to review these proposals and decide which \
to ACCEPT, REJECT, or MERGE into the skill library.

## Constraints

- Each skill has a NAME, DESCRIPTION (one sentence — the only thing the agent \
sees by default), and CONTENT (full procedure, loaded on demand via read_skill tool).
- Skills should be GENERALIZABLE — useful across many future tasks, not specific to one bug.
- You do NOT generate new skills yourself. You only curate what solvers propose.
- Keep the library lean — MERGE overlapping skills, REJECT task-specific ones.

## Decision criteria

Since we don't know if the solver's fix was correct, use the solver's \
CONFIDENCE as the primary signal:
- HIGH confidence proposals are likely based on genuine insight — lean towards ACCEPT
- LOW confidence proposals may be from confused solvers — lean towards SKIP

Solvers may propose ENHANCE (improve an existing skill) or NEW (add a new skill):
- ENHANCE proposals: if the enhancement adds value, ACCEPT it (replaces old version).
- NEW proposals: FIRST check if it overlaps with ANY existing skill. If so, MERGE it \
into the existing skill rather than adding a new one. The library should have FEW \
broad skills, not MANY narrow ones. Only ACCEPT a truly new skill if it covers a \
pattern no existing skill touches.

PREFER MERGE over ACCEPT. A library of 5-10 broad skills is better than 30 narrow ones.

## Output format

For each proposal, output ONE of:

ACCEPT: <proposal_name>
(the skill is added as-is to the library)

MERGE: <proposal_name> INTO <existing_skill_name>
NEW_CONTENT:
(merged skill content combining both insights, under 500 words)

SKIP: <proposal_name>
REASON: <brief reason — e.g. too task-specific, already covered>

Note: you cannot delete or replace skills. Skills only grow or get merged. \
Be generous — if a proposal has any generalizable value, ACCEPT it. \
Only SKIP if it's clearly redundant with an existing skill or too specific to one bug.

If there are no proposals, output: NO_PROPOSALS
"""


VERIFICATION_CURATOR_PROMPT = """\
You are a VERIFICATION SKILL CURATOR. Only accept skills about TESTING and VERIFYING fixes.

IMPORTANT: Output your decisions FIRST, one per line. No analysis needed.

Accept skills about: finding test files, writing repro scripts, before/after \
test comparison, edge case testing, detecting multi-file bugs.
Reject skills about: finding code, writing patches, debugging logic.

Prefer MERGE — a few broad verification skills beat many narrow ones.

Output format (decisions ONLY, no analysis):
ACCEPT: <name>
MERGE: <name> INTO <existing>
SKIP: <name>
"""


def _build_curation_context(workspace: Any, proposals: list[dict[str, Any]]) -> str:
    existing_skills = [s.name for s in workspace.list_skills()]
    parts = ["## Current Skill Library"]
    if existing_skills:
        parts.append(f"({len(existing_skills)} skills)")
        for name in existing_skills:
            content = workspace.read_skill(name) or ""
            desc = ""
            for line in content.split("\n"):
                if line.startswith("description:"):
                    desc = line.split(":", 1)[1].strip()
                    break
            parts.append(f"- **{name}**: {desc[:100]}")
    else:
        parts.append("(empty — 0/7 slots used)")
    if proposals:
        parts.append("")
        parts.append("## Solver Proposals")
        for p in proposals:
            action = p.get("action", "NEW")
            parts.append(
                f"\n### {'ENHANCE' if action == 'ENHANCE' else 'NEW'}: "
                f"{p.get('name', '')} (confidence: {p.get('confidence', '?')})"
            )
            parts.append(f"Source: {p.get('source_task_id', '?')}")
            if action == "ENHANCE":
                parts.append(f"Target skill: {p.get('target', '?')}")
                parts.append(
                    f"Solver analysis: {p.get('analysis', '')[:200]}"
                )
            parts.append(f"Description: {p.get('description', '')[:150]}")
            parts.append(f"Content preview: {p.get('content', '')[:200]}...")
    parts.append("")
    parts.append(
        "Review each proposal. For ENHANCE proposals, decide whether the "
        "enhancement improves the skill."
    )
    parts.append("Decide: ACCEPT, MERGE, or SKIP. You cannot delete skills.")
    return "\n".join(parts)


def _execute_decisions(
    workspace: Any,
    proposals: list[dict[str, Any]],
    decisions_raw: str,
    max_skills: int,
) -> list[str]:
    applied: list[str] = []
    proposal_map = {p.get("name", ""): p for p in proposals if p.get("name")}
    existing_skills = {s.name for s in workspace.list_skills()}

    def _fuzzy_match_proposal(name: str) -> str | None:
        if name in proposal_map:
            return name
        for pname in proposal_map:
            if pname.startswith(name) or name.startswith(pname):
                return pname
        return None

    def _fuzzy_match_skill(name: str) -> str | None:
        if name in existing_skills:
            return name
        for sname in existing_skills:
            if sname.startswith(name) or name.startswith(sname):
                return sname
        return None

    for line in decisions_raw.split("\n"):
        line = line.strip().strip("*").strip()

        if line.startswith("ACCEPT:"):
            raw_name = line.split(":", 1)[1].strip()
            name = _fuzzy_match_proposal(raw_name)
            if name and name not in existing_skills and len(existing_skills) < max_skills:
                p = proposal_map[name]
                desc = (p.get("description", "") or p.get("content", "")[:100])[:150]
                workspace.write_skill(
                    name, f"---\nname: {name}\ndescription: {desc}\n---\n\n{p.get('content', '')}"
                )
                existing_skills.add(name)
                applied.append(f"accept:{name}")
                logger.info("Curated ACCEPT: %s", name)
            continue

        if line.startswith("REPLACE:"):
            parts = line.split(":", 1)[1].strip()
            if " REPLACES " in parts:
                raw_new, raw_old = parts.split(" REPLACES ", 1)
                new_name = _fuzzy_match_proposal(raw_new.strip())
                old_name = _fuzzy_match_skill(raw_old.strip())
                if new_name and old_name:
                    workspace.delete_skill(old_name)
                    existing_skills.discard(old_name)
                    p = proposal_map[new_name]
                    desc = (p.get("description", "") or p.get("content", "")[:100])[:150]
                    workspace.write_skill(
                        new_name,
                        f"---\nname: {new_name}\ndescription: {desc}\n---\n\n{p.get('content', '')}",
                    )
                    existing_skills.add(new_name)
                    applied.append(f"replace:{old_name}->{new_name}")
                    logger.info("Curated REPLACE: %s -> %s", old_name, new_name)
            continue

        if line.startswith("MERGE:"):
            parts = line.split(":", 1)[1].strip()
            if " INTO " in parts:
                raw_prop, raw_target = parts.split(" INTO ", 1)
                prop_name = _fuzzy_match_proposal(raw_prop.strip())
                target_name = _fuzzy_match_skill(raw_target.strip())
                if prop_name and target_name:
                    idx = decisions_raw.find(line)
                    after = decisions_raw[idx + len(line):]
                    if "NEW_CONTENT:" in after:
                        new_content = after.split("NEW_CONTENT:", 1)[1]
                        for marker in ("ACCEPT:", "MERGE:", "SKIP:", "NO_PROPOSALS"):
                            if marker in new_content:
                                new_content = new_content[: new_content.index(marker)]
                        new_content = new_content.strip()
                        if new_content:
                            old_skill = workspace.read_skill(target_name) or ""
                            old_desc = ""
                            for sl in old_skill.split("\n"):
                                if sl.startswith("description:"):
                                    old_desc = sl.split(":", 1)[1].strip()
                                    break
                            workspace.write_skill(
                                target_name,
                                f"---\nname: {target_name}\ndescription: {old_desc}\n---\n\n{new_content}",
                            )
                            applied.append(f"merge:{prop_name}->{target_name}")
                            logger.info(
                                "Curated MERGE: %s into %s", prop_name, target_name
                            )
            continue

        if line.startswith("SKIP:"):
            name = line.split(":", 1)[1].strip()
            logger.info("Curated SKIP: %s", name)
            continue

    return applied


@register_operator("SkillCurator")
class SkillCurator:
    """Curator operator.

    State keys:
        ``state["max_skills"]`` — hard cap on the curated library (default 999).
        ``state["verification_focus"]`` — use the verification-only curator prompt.
        ``state["mock_curator"]`` — test hook: callable(prompt) -> raw_decisions.
        ``state["model_id"]`` / ``state["region"]`` — optional LLM overrides.
    """

    WRITES: frozenset[str] = frozenset({"skills"})

    DEFAULT_MODEL = "us.anthropic.claude-opus-4-6-v1"
    DEFAULT_REGION = "us-west-2"

    def apply(
        self,
        workspace: Any,
        context: Any,
        scope: dict[str, Any],
        state: dict[str, Any],
    ) -> MutationReport:
        if scope.get("skills") not in ("rw",):
            return MutationReport(operator_name="SkillCurator", count=0)

        proposals = list(
            (context.entries.get("ProposalReader", {}) or {}).get("proposals", [])
        )
        if not proposals:
            return MutationReport(
                operator_name="SkillCurator", count=0, details={"proposals": 0}
            )

        max_skills = int(state.get("max_skills", 999))
        verification_focus = bool(state.get("verification_focus", False))
        ctx_text = _build_curation_context(workspace, proposals)

        mock = state.get("mock_curator")
        provider = state.get("llm_provider")
        if callable(mock):
            raw = str(mock(ctx_text))
        elif provider is not None:
            # Provider-based path — accepts any object implementing the
            # ``complete(messages, max_tokens)`` protocol, including the
            # fake Bedrock providers used in differential parity tests.
            try:
                from agent_evolve.llm.base import LLMMessage
            except ImportError as e:
                logger.warning("SkillCurator: LLMMessage unavailable (%s)", e)
                return MutationReport(
                    operator_name="SkillCurator",
                    count=0,
                    details={"error": f"provider unavailable: {e}"},
                )
            prompt = (
                VERIFICATION_CURATOR_PROMPT if verification_focus else GUIDED_SYNTHESIS_PROMPT
            )
            try:
                response = provider.complete(
                    [
                        LLMMessage(role="system", content=prompt),
                        LLMMessage(role="user", content=ctx_text),
                    ],
                    max_tokens=2048,
                )
                raw = response.content.strip()
            except Exception as exc:  # noqa: BLE001
                logger.error("SkillCurator: provider call failed: %s", exc)
                return MutationReport(
                    operator_name="SkillCurator",
                    count=0,
                    details={"error": str(exc)[:200]},
                )
        else:
            try:
                from agent_evolve.llm.bedrock import BedrockProvider
                from agent_evolve.llm.base import LLMMessage
            except ImportError as e:
                logger.warning("SkillCurator: LLM provider unavailable (%s)", e)
                return MutationReport(
                    operator_name="SkillCurator",
                    count=0,
                    details={"error": f"provider unavailable: {e}"},
                )
            llm = BedrockProvider(
                model_id=state.get("model_id", self.DEFAULT_MODEL),
                region=state.get("region", self.DEFAULT_REGION),
            )
            prompt = (
                VERIFICATION_CURATOR_PROMPT if verification_focus else GUIDED_SYNTHESIS_PROMPT
            )
            try:
                response = llm.complete(
                    [
                        LLMMessage(role="system", content=prompt),
                        LLMMessage(role="user", content=ctx_text),
                    ],
                    max_tokens=2048,
                )
                raw = response.content.strip()
            except Exception as exc:  # noqa: BLE001
                logger.error("SkillCurator: LLM call failed: %s", exc)
                return MutationReport(
                    operator_name="SkillCurator",
                    count=0,
                    details={"error": str(exc)[:200]},
                )

        applied = _execute_decisions(workspace, proposals, raw, max_skills)
        # Split the "accept:<name>", "replace:<old>->-<new>", "merge:<src>-><dst>"
        # strings into normalized skills_added / skills_removed lists so
        # UnifiedEngine.step() and the differential-parity tests can
        # aggregate them uniformly with other operators.
        skills_added: list[str] = []
        skills_removed: list[str] = []
        for entry in applied:
            if entry.startswith("accept:"):
                skills_added.append(entry.split(":", 1)[1])
            elif entry.startswith("replace:"):
                rhs = entry.split(":", 1)[1]
                if "->" in rhs:
                    old, new = rhs.split("->", 1)
                    skills_removed.append(old)
                    skills_added.append(new)
            elif entry.startswith("merge:"):
                rhs = entry.split(":", 1)[1]
                if "->" in rhs:
                    _src, dst = rhs.split("->", 1)
                    # Merged content replaces the destination body; surface as
                    # an "added" event for summary parity with legacy "applied".
                    skills_added.append(dst)
        return MutationReport(
            operator_name="SkillCurator",
            count=len(applied),
            details={
                "proposals": len(proposals),
                "applied": applied,
                "skills_added": skills_added,
                "skills_removed": skills_removed,
            },
        )
