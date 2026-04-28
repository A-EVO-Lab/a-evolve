"""GuidedSynthesisEngine — memory-first evolution with LLM-guided intervention synthesis.

Replaces the 5-phase SWE-Evolve pipeline with a simpler 2-phase loop:
  1. Write minimal episodic memory from the failed attempt
  2. Call evolver LLM to generate one targeted intervention (skill or prompt fragment)

The evolver prompt encodes learnings from E02 experiments about what kinds of
interventions actually help: methodology skills and concise behavioral nudges.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ...config import EvolveConfig
from ...engine.base import EvolutionEngine
from ...llm.base import LLMProvider
from ...types import Observation, StepResult

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


class GuidedSynthesisEngine(EvolutionEngine):
    """Memory-first evolution with LLM-guided synthesis."""

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

    def __init__(
        self,
        config: EvolveConfig,
        llm: LLMProvider | None = None,
        write_memory: bool = True,
        verification_focus: bool = False,
    ) -> None:
        self.config = config
        self._llm = llm
        self._cycle_count = 0
        self._write_memory = write_memory
        self._verification_focus = verification_focus
        self._cumulative_pass = 0
        self._cumulative_fail = 0

    @property
    def llm(self) -> LLMProvider:
        if self._llm is None:
            from ..adaptive_skill.tools import create_default_llm
            self._llm = create_default_llm(self.config)
        return self._llm

    # ── EvolutionEngine interface ────────────────────────────

    MAX_SKILLS = 999  # no hard cap — evolver curates quality

    def step(
        self,
        workspace: Any,
        observations: list[Observation],
        history: Any,
        trial: Any,
    ) -> StepResult:

        self._cycle_count += 1
        logger.info("=== Guided Synthesis step %d ===", self._cycle_count)

        # Phase 1: Write minimal memory (optional — useful for retry, not for sequential)
        if self._write_memory:
            logger.info("Phase 1: Writing minimal memory from %d observation(s)", len(observations))
            for obs in observations:
                self._write_minimal_memory(workspace, obs)
        else:
            logger.info("Phase 1: Skipping memory (write_memory=False)")

        # Phase 2: Curate solver-proposed skills (NEW or ENHANCE)
        proposals = []
        for obs in observations:
            proposal_raw = getattr(obs.trajectory, "_skill_proposal", "")
            if not proposal_raw or "ACTION: NONE" in proposal_raw.upper():
                continue
            # Extract confidence and action type
            confidence = "MEDIUM"
            action = "NEW"
            target = ""
            analysis = ""
            for line in proposal_raw.split("\n"):
                stripped = line.strip().upper()
                if stripped.startswith("CONFIDENCE:"):
                    confidence = line.split(":", 1)[1].strip().upper()
                elif stripped.startswith("ACTION:"):
                    action = line.split(":", 1)[1].strip().upper()
                elif stripped.startswith("TARGET:"):
                    target = line.split(":", 1)[1].strip()
                elif stripped.startswith("ANALYSIS:"):
                    analysis = line.split(":", 1)[1].strip()
            parsed = self._parse_intervention(proposal_raw)
            if parsed:
                parsed["confidence"] = confidence
                parsed["action"] = action  # NEW or ENHANCE
                parsed["target"] = target  # existing skill name (for ENHANCE)
                parsed["analysis"] = analysis
                parsed["source_task"] = obs.task.id
                proposals.append(parsed)

        applied_names = []
        if proposals:
            n_new = sum(1 for p in proposals if p["action"] == "NEW")
            n_enhance = sum(1 for p in proposals if p["action"] == "ENHANCE")
            logger.info("Phase 2: Curating %d proposal(s) (%d new, %d enhance)",
                        len(proposals), n_new, n_enhance)
            context = self._build_curation_context(workspace, proposals)
            decisions = self._curate_proposals(context)
            applied_names = self._execute_curation(workspace, proposals, decisions)
        else:
            logger.info("Phase 2: No proposals to curate")

        return StepResult(
            mutated=len(applied_names) > 0,
            summary=(
                f"guided-synth cycle {self._cycle_count}: "
                f"curated {len(proposals)} proposals, applied {len(applied_names)}: {applied_names}"
            ),
            metadata={
                "cycle": self._cycle_count,
                "proposals": len(proposals),
                "applied": applied_names,
            },
        )

    # ── Standalone convenience API ───────────────────────────

    def evolve(
        self,
        workspace: Any,
        observation_logs: list[Observation],
        evo_number: int = 0,
    ) -> dict[str, Any]:
        from ...engine.history import EvolutionHistory
        from ...engine.observer import Observer
        from ...engine.versioning import VersionControl

        logger.info(
            "evolve() called: evo_number=%d, observations=%d, workspace=%s",
            evo_number, len(observation_logs), workspace.root,
        )

        vc = VersionControl(workspace.root)
        vc.init()

        evolution_dir = workspace.root / "evolution"
        evolution_dir.mkdir(parents=True, exist_ok=True)
        observer = Observer(evolution_dir)
        history = EvolutionHistory(observer, vc)

        vc.commit(
            message=f"pre-guided-synth-{evo_number}: snapshot before evolution",
            tag=f"pre-guided-synth-{evo_number}",
        )

        result = self.step(workspace, observation_logs, history, trial=None)

        tag_msg = (
            f"guided-synth-{evo_number}: {result.summary}"
            if result.mutated
            else f"guided-synth-{evo_number}: no mutation"
        )
        vc.commit(message=tag_msg, tag=f"guided-synth-{evo_number}")
        logger.info("evolve() complete: %s", tag_msg)

        return result.metadata

    # ── Phase 1: Minimal memory ──────────────────────────────

    def _write_minimal_memory(
        self,
        workspace: Any,
        obs: Observation,
    ) -> None:
        task_id = obs.task.id
        agent_output = obs.trajectory.output or ""
        score = obs.feedback.score if obs.feedback else 0.0

        # Extract files from diff
        files_in_patch: list[str] = []
        if agent_output.strip():
            for m in re.finditer(
                r"^(?:\+\+\+)\s+[ab]/(.+)$", agent_output, re.MULTILINE
            ):
                if m.group(1) != "/dev/null":
                    files_in_patch.append(m.group(1))

        summary = (
            f"Cycle {self._cycle_count}: "
            f"Edited {len(files_in_patch)} file(s): {', '.join(files_in_patch[:5])}. "
            f"Score: {score}."
        )

        memory_entry = {
            "task_id": task_id,
            "cycle": self._cycle_count,
            "score": score,
            "files_edited": files_in_patch,
            "approach_summary": summary,
        }

        workspace.add_memory(memory_entry, category="episodic")
        logger.info(
            "Wrote memory for task=%s cycle=%d score=%.1f files=%s",
            task_id, self._cycle_count, score, files_in_patch[:3],
        )

    # ── Phase 2: Skill curation ─────────────────────────────

    def _build_curation_context(
        self,
        workspace: Any,
        proposals: list[dict],
    ) -> str:
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
                parts.append(f"\n### {'ENHANCE' if action == 'ENHANCE' else 'NEW'}: {p['name']} (confidence: {p.get('confidence', '?')})")
                parts.append(f"Source: {p.get('source_task', '?')}")
                if action == "ENHANCE":
                    parts.append(f"Target skill: {p.get('target', '?')}")
                    parts.append(f"Solver analysis: {p.get('analysis', '')[:200]}")
                parts.append(f"Description: {p.get('description', '')[:150]}")
                parts.append(f"Content preview: {p['content'][:200]}...")

        parts.append("")
        parts.append("Review each proposal. For ENHANCE proposals, decide whether the enhancement improves the skill.")
        parts.append("Decide: ACCEPT, MERGE, or SKIP. You cannot delete skills.")
        return "\n".join(parts)

    def _curate_proposals(self, context: str) -> str:
        from ...llm.base import LLMMessage
        prompt = self.VERIFICATION_CURATOR_PROMPT if self._verification_focus else GUIDED_SYNTHESIS_PROMPT
        messages = [
            LLMMessage(role="system", content=prompt),
            LLMMessage(role="user", content=context),
        ]
        try:
            response = self.llm.complete(messages, max_tokens=2048)
            raw = response.content.strip()
            logger.info("Curator raw response:\n%s", raw[:500])
            return raw
        except Exception as e:
            logger.error("Curation LLM call failed: %s", e)
            return ""

    def _execute_curation(
        self,
        workspace: Any,
        proposals: list[dict],
        decisions_raw: str,
    ) -> list[str]:
        """Parse curation decisions and apply to workspace."""
        applied = []
        proposal_map = {p["name"]: p for p in proposals}
        existing_skills = {s.name for s in workspace.list_skills()}

        def _fuzzy_match_proposal(name: str) -> str | None:
            """Match curator's name to actual proposal name (curator may truncate)."""
            if name in proposal_map:
                return name
            # Try prefix match
            for pname in proposal_map:
                if pname.startswith(name) or name.startswith(pname):
                    return pname
            return None

        def _fuzzy_match_skill(name: str) -> str | None:
            """Match curator's name to actual skill name."""
            if name in existing_skills:
                return name
            for sname in existing_skills:
                if sname.startswith(name) or name.startswith(sname):
                    return sname
            return None

        for line in decisions_raw.split("\n"):
            line = line.strip().strip("*").strip()  # strip markdown bold **

            if line.startswith("ACCEPT:"):
                raw_name = line.split(":", 1)[1].strip()
                name = _fuzzy_match_proposal(raw_name)
                if name and name not in existing_skills:
                    if len(existing_skills) < self.MAX_SKILLS:
                        p = proposal_map[name]
                        desc = p.get("description", p["content"][:100])[:150]  # cap description length
                        workspace.write_skill(name, f"---\nname: {name}\ndescription: {desc}\n---\n\n{p['content']}")
                        existing_skills.add(name)
                        applied.append(f"accept:{name}")
                        logger.info("Curated ACCEPT: %s", name)

            elif line.startswith("REPLACE:"):
                # REPLACE: new_name REPLACES old_name
                parts = line.split(":", 1)[1].strip()
                if " REPLACES " in parts:
                    raw_new, raw_old = parts.split(" REPLACES ", 1)
                    new_name = _fuzzy_match_proposal(raw_new.strip())
                    old_name = _fuzzy_match_skill(raw_old.strip())
                    if new_name and old_name:
                        workspace.delete_skill(old_name)
                        existing_skills.discard(old_name)
                        p = proposal_map[new_name]
                        desc = p.get("description", p["content"][:100])[:150]  # cap description length
                        workspace.write_skill(new_name, f"---\nname: {new_name}\ndescription: {desc}\n---\n\n{p['content']}")
                        existing_skills.add(new_name)
                        applied.append(f"replace:{old_name}->{new_name}")
                        logger.info("Curated REPLACE: %s -> %s", old_name, new_name)

            elif line.startswith("MERGE:"):
                # MERGE: proposal_name INTO existing_name
                parts = line.split(":", 1)[1].strip()
                if " INTO " in parts:
                    raw_prop, raw_target = parts.split(" INTO ", 1)
                    prop_name = _fuzzy_match_proposal(raw_prop.strip())
                    target_name = _fuzzy_match_skill(raw_target.strip())
                    if prop_name and target_name:
                        # Look for NEW_CONTENT in subsequent lines
                        idx = decisions_raw.find(line)
                        after = decisions_raw[idx + len(line):]
                        if "NEW_CONTENT:" in after:
                            new_content = after.split("NEW_CONTENT:", 1)[1]
                            end_markers = ["ACCEPT:", "MERGE:", "SKIP:", "NO_PROPOSALS"]
                            for marker in end_markers:
                                if marker in new_content:
                                    new_content = new_content[:new_content.index(marker)]
                            new_content = new_content.strip()
                            if new_content:
                                old_skill = workspace.read_skill(target_name) or ""
                                old_desc = ""
                                for sl in old_skill.split("\n"):
                                    if sl.startswith("description:"):
                                        old_desc = sl.split(":", 1)[1].strip()
                                        break
                                workspace.write_skill(target_name,
                                    f"---\nname: {target_name}\ndescription: {old_desc}\n---\n\n{new_content}")
                                applied.append(f"merge:{prop_name}->{target_name}")
                                logger.info("Curated MERGE: %s into %s", prop_name, target_name)

            elif line.startswith("SKIP:"):
                name = line.split(":", 1)[1].strip()
                logger.info("Curated SKIP: %s", name)

        return applied

    # ── Legacy: failure context (still used for non-curation mode) ──

    def _build_failure_context(
        self,
        workspace: Any,
        observations: list[Observation],
    ) -> str:
        # Gather current workspace state
        existing_skills = [s.name for s in workspace.list_skills()]
        existing_fragments = list(workspace.list_fragments())

        # Build observation summary
        obs_lines = []
        n_success = 0
        n_fail = 0
        n_unknown = 0
        fail_files: list[str] = []
        for obs in observations:
            task_id = obs.task.id
            score = obs.feedback.score if obs.feedback else 0.0
            patch = obs.trajectory.output or ""
            files = []
            for m in re.finditer(r"^(?:\+\+\+)\s+[ab]/(.+)$", patch, re.MULTILINE):
                if m.group(1) != "/dev/null":
                    files.append(m.group(1))
            n_steps = len([s for s in obs.trajectory.steps if isinstance(s, dict) and "tool" in s])
            traj_summary = self._summarize_trajectory(obs.trajectory.steps)
            reflection = getattr(obs.trajectory, "_reflection", "") or ""

            # Build diagnosis: prefer reflection (semantic), fall back to heuristic summary
            diagnosis = reflection[:300] if reflection else traj_summary

            # Check if feedback is masked (none mode: score=0, detail="", success=False)
            is_masked = (score == 0.0 and not obs.feedback.detail and not obs.feedback.success)
            if is_masked:
                n_unknown += 1
                obs_lines.append(f"- {task_id}: {n_steps} steps, edited {', '.join(files[:3])}. {diagnosis}")
            elif score > 0:
                n_success += 1
                obs_lines.append(f"- PASS {task_id}: {n_steps} steps, edited {', '.join(files[:3])}")
            else:
                n_fail += 1
                obs_lines.append(f"- FAIL {task_id}: {n_steps} steps, edited {', '.join(files[:3])}. {diagnosis}")
                fail_files.extend(files)

        # Track cumulative stats
        self._cumulative_pass += n_success
        self._cumulative_fail += n_fail
        total_pass = self._cumulative_pass
        total_fail = self._cumulative_fail

        # Compute repo breakdown
        from collections import Counter
        fail_repos = Counter()
        for obs in observations:
            score = obs.feedback.score if obs.feedback else 0.0
            if score == 0:
                repo = obs.task.id.split("__")[0] if "__" in obs.task.id else "unknown"
                fail_repos[repo] += 1

        parts = [
            "## Evolution Context\n",
            f"Evolution generation: {self._cycle_count}",
        ]
        if n_unknown > 0:
            parts.append(f"Overall: {total_pass + total_fail + n_unknown} tasks attempted (results not available)")
            parts.append("")
            parts.append(f"### Latest Batch ({len(observations)} tasks, results unknown)")
        else:
            parts.append(f"Overall record: {total_pass} passed, {total_fail} failed out of {total_pass + total_fail} tasks")
            parts.append("")
            parts.append(f"### Latest Batch ({len(observations)} tasks: {n_success} passed, {n_fail} failed)")
            if fail_repos:
                parts.append(f"Failed repos: {dict(fail_repos)}")
        parts.extend(obs_lines)
        parts.append("")

        parts.append("### Current Workspace Interventions")
        if existing_skills:
            parts.append(f"Skills already present: {', '.join(existing_skills)}")
        else:
            parts.append("Skills: none")
        if existing_fragments:
            frag_names = [f.removesuffix(".md") for f in existing_fragments]
            parts.append(f"Prompt fragments already present: {', '.join(frag_names)}")
        else:
            parts.append("Prompt fragments: none")

        parts.append("")
        if not existing_skills and not existing_fragments:
            parts.append(
                "This is the FIRST intervention. Generate a SKILL with a step-by-step methodology "
                "for systematically exploring an unfamiliar codebase and fixing a bug. "
                "Include concrete commands (grep, find, pytest) the agent should run."
            )
        else:
            parts.append(
                "Analyze the failure patterns above. Generate ONE new intervention that is "
                "DIFFERENT from the ones already present. Target the most common failure mode. "
                "If a skill already exists, generate a short FRAGMENT (1-2 sentences) with a "
                "complementary behavioral nudge."
            )

        return "\n".join(parts)

    def _synthesize_intervention(
        self,
        context: str,
    ) -> dict[str, str] | None:
        from ...llm.base import LLMMessage

        messages = [
            LLMMessage(role="system", content=GUIDED_SYNTHESIS_PROMPT),
            LLMMessage(role="user", content=context),
        ]

        try:
            response = self.llm.complete(messages, max_tokens=2048)
            content = response.content.strip()
        except Exception as e:
            logger.error("Synthesis LLM call failed: %s", e)
            return None

        return self._parse_intervention(content)

    def _parse_intervention(self, raw: str) -> dict[str, str] | None:
        """Parse the LLM response into {type, name, description, content}."""
        lines = raw.strip().split("\n")

        itype = None
        name = None
        description = ""
        content_lines: list[str] = []
        in_content = False

        for line in lines:
            if line.startswith("TYPE:"):
                itype = line.split(":", 1)[1].strip().lower()
            elif line.startswith("NAME:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("DESCRIPTION:"):
                description = line.split(":", 1)[1].strip()
            elif line.startswith("CONTENT:"):
                in_content = True
                rest = line.split(":", 1)[1].strip()
                if rest:
                    content_lines.append(rest)
            elif in_content:
                content_lines.append(line)

        if not itype or not name or not content_lines:
            logger.warning(
                "Failed to parse intervention from LLM response (type=%s, name=%s, content_lines=%d)",
                itype, name, len(content_lines),
            )
            logger.debug("Raw response:\n%s", raw[:500])
            return None

        if itype not in ("skill", "fragment"):
            logger.warning("Unknown intervention type: %s", itype)
            return None

        # Clean up name
        name = re.sub(r"[^a-z0-9_]", "_", name.lower()).strip("_")

        content = "\n".join(content_lines).strip()

        # Strip markdown code fences if wrapped
        if content.startswith("```"):
            fence_lines = content.split("\n")
            if fence_lines[-1].strip() == "```":
                content = "\n".join(fence_lines[1:-1]).strip()

        logger.info("Parsed intervention: type=%s name=%s desc=%s content_len=%d",
                    itype, name, description[:60], len(content))
        return {"type": itype, "name": name, "description": description, "content": content}

    # ── Trajectory summarization ─────────────────────────────

    @staticmethod
    def _summarize_trajectory(steps: list[dict]) -> str:
        """Summarize a trajectory into a compact behavioral diagnosis (~1-2 sentences).

        Extracts action distribution, unique files read/edited, and behavioral patterns
        from the tool-call trace. No LLM call — pure heuristic.
        """
        from collections import Counter

        if not steps:
            return "No trajectory data."

        tool_steps = [s for s in steps if isinstance(s, dict) and "tool" in s]
        if not tool_steps:
            return "No tool calls."

        # Count actions
        actions = Counter(s.get("action", "unknown") for s in tool_steps)

        # Extract unique files read and edited
        files_read = set()
        files_edited = set()
        for s in tool_steps:
            action = s.get("action", "")
            f = s.get("file", "")
            if not f or f == "-20":  # junk from find output
                continue
            if action in ("read_file", "search", "navigate"):
                files_read.add(f)
            elif action in ("edit_file", "write_file"):
                files_edited.add(f)

        # Count test runs
        test_runs = sum(1 for s in tool_steps if "pytest" in str(s.get("input_summary", "")).lower())

        # Detect patterns
        patterns = []
        n = len(tool_steps)

        if n > 80:
            patterns.append(f"very long exploration ({n} calls)")
        elif n > 50:
            patterns.append(f"long exploration ({n} calls)")

        if len(files_read) > 10:
            patterns.append(f"read {len(files_read)} unique files")
        elif len(files_read) == 0:
            patterns.append("read no files before editing")

        if test_runs == 0:
            patterns.append("never ran tests")
        elif test_runs > 10:
            patterns.append(f"ran tests {test_runs} times")

        # Check if agent edited early vs late
        first_edit_idx = next(
            (i for i, s in enumerate(tool_steps) if s.get("action") in ("edit_file", "write_file")),
            n,
        )
        if first_edit_idx < 3 and n > 10:
            patterns.append("edited very early (step {})".format(first_edit_idx))
        elif first_edit_idx > n * 0.7:
            patterns.append("edited very late (step {}/{})".format(first_edit_idx, n))

        if not patterns:
            patterns.append(f"{n} calls")

        return "; ".join(patterns)

    # ── Apply intervention ───────────────────────────────────

    def _apply_intervention(
        self,
        workspace: Any,
        intervention: dict[str, str],
    ) -> bool:
        itype = intervention["type"]
        name = intervention["name"]
        content = intervention["content"]

        if itype == "skill":
            # Check if already exists
            existing = {s.name for s in workspace.list_skills()}
            if name in existing:
                logger.info("Skill %s already exists, skipping", name)
                return False
            desc = intervention.get("description", "")[:150]
            if not desc:
                # Fallback: extract first meaningful line
                for line in content.split("\n"):
                    line = line.strip()
                    if line and not line.startswith("#"):
                        desc = line[:150]
                        break
            if not desc:
                desc = content[:150].replace("\n", " ").strip()
            skill_md = f"---\nname: {name}\ndescription: {desc}\n---\n\n{content}"
            workspace.write_skill(name, skill_md)
            logger.info("Applied skill: %s (%d chars)", name, len(content))
            return True

        elif itype == "fragment":
            existing = set(workspace.list_fragments())
            if f"{name}.md" in existing:
                logger.info("Fragment %s already exists, skipping", name)
                return False
            workspace.write_fragment(f"{name}.md", content)
            # Also inject into system prompt
            current_prompt = workspace.read_prompt()
            marker = f"<!-- evolve:{name} -->"
            if marker not in current_prompt:
                injection = f"\n\n{marker}\n## {name.replace('_', ' ').title()}\n{content}\n"
                workspace.write_prompt(current_prompt + injection)
            logger.info("Applied fragment: %s (%d chars)", name, len(content))
            return True

        logger.warning("Unknown intervention type: %s", itype)
        return False

    # ── Pruning ──────────────────────────────────────────────

    PRUNE_PROMPT = """\
You are reviewing a list of interventions (skills and prompt fragments) \
for a SWE-bench solving agent. Identify which ones are REDUNDANT — they \
teach the same methodology or give the same advice as another.

For each redundant item, output a line: REMOVE: <name>
Keep the BEST version of each unique idea. Keep items that cover \
genuinely different strategies. Output NOTHING if all items are unique.
"""

    def _prune_similar(
        self,
        workspace: Any,
        skill_names: list[str],
        fragment_names: list[str],
    ) -> list[str]:
        """Use LLM to identify and remove redundant interventions."""
        from ...llm.base import LLMMessage

        # Build inventory
        items = []
        for name in skill_names:
            content = workspace.read_skill(name) or ""
            # Truncate skill content to first 200 chars for comparison
            body = content.split("---", 2)[-1].strip()[:200] if "---" in content else content[:200]
            items.append(f"SKILL: {name}\n  {body}")
        for fname in fragment_names:
            content = workspace.read_fragment(fname) or ""
            items.append(f"FRAGMENT: {fname.removesuffix('.md')}\n  {content[:200]}")

        if len(items) < 3:
            return []

        user_msg = "## Current interventions:\n\n" + "\n\n".join(items)

        try:
            messages = [
                LLMMessage(role="system", content=self.PRUNE_PROMPT),
                LLMMessage(role="user", content=user_msg),
            ]
            response = self.llm.complete(messages, max_tokens=512)
            raw = response.content.strip()
        except Exception as e:
            logger.error("Prune LLM call failed: %s", e)
            return []

        # Parse REMOVE: lines
        pruned = []
        for line in raw.split("\n"):
            line = line.strip()
            if line.startswith("REMOVE:"):
                name = line.split(":", 1)[1].strip()
                pruned.append(name)

        # Execute removal
        removed = []
        for name in pruned:
            if name in skill_names:
                workspace.delete_skill(name)
                removed.append(f"skill:{name}")
                logger.info("Pruned skill: %s", name)
            elif f"{name}.md" in fragment_names or name in [f.removesuffix(".md") for f in fragment_names]:
                frag_file = f"{name}.md"
                frag_path = workspace.prompts_dir / "fragments" / frag_file
                if frag_path.exists():
                    frag_path.unlink()
                # Also remove from system prompt
                current_prompt = workspace.read_prompt()
                marker = f"<!-- evolve:{name} -->"
                if marker in current_prompt:
                    # Remove the injected section
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

        return removed
