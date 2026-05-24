"""ProposeCurateEngine -- unified evolution engine for propose+curate pipelines.

Shared pipeline:
  1. Extract proposals from observations (via feedback.raw["proposal"])
  2. Group proposals by topic/context
  3. Per-topic curation (LLM decides ACCEPT/MERGE/SKIP per group)
  4. General curation (cross-topic failure pattern analysis)
  5. Write skills to workspace

This engine is benchmark-agnostic. Prompts are passed in as parameters so
callers can use their exact domain-specific prompts (OSWorld GUI prompts,
CL-bench Q&A prompts, etc.) without modification.
"""

from __future__ import annotations

import logging
import re
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from ...config import EvolveConfig
from ...engine.base import EvolutionEngine
from ...types import Observation, StepResult

logger = logging.getLogger(__name__)


class ProposeCurateEngine(EvolutionEngine):
    """Propose+Curate evolution engine.

    Observations must carry proposals in their feedback.raw:
        obs.feedback.raw["proposal"] = {
            "topic": str,         # grouping key (e.g. "libreoffice-calc", context_id)
            "action": "NEW" | "ENHANCE",
            "target": str,        # existing skill name (for ENHANCE)
            "name": str,          # kebab-case skill name
            "description": str,   # one-line description
            "content": str,       # skill body (markdown)
            "source_task": str,   # task ID that generated this
        }

    Observations without proposals still contribute to general curation
    via their feedback detail.
    """

    def __init__(
        self,
        config: EvolveConfig,
        max_skills_per_topic: int = 5,
        max_general_skills: int = 10,
        skill_layout: str = "topic",
        curator_model: str | None = None,
        general_curator_model: str | None = None,
        evolve_passed: bool = False,
        topic_curator_prompt: str | None = None,
        general_curator_prompt: str | None = None,
        format_failed_summary: Callable[[dict], str] | None = None,
    ):
        """
        Args:
            config: Evolution config with model/region info.
            max_skills_per_topic: Maximum skills per topic group.
            max_general_skills: Maximum cross-topic general skills.
            skill_layout: "topic" for skills/topic/<topic>/<name>/SKILL.md,
                          "context" for skills/context/<ctx>/<name>/SKILL.md,
                          "flat" for skills/evolved/<name>/SKILL.md.
            curator_model: Model ID for per-topic curation.
            general_curator_model: Model ID for general curation.
            evolve_passed: Whether to curate proposals from passed tasks too.
            topic_curator_prompt: Prompt template for per-topic curation.
                Must have placeholders: {topic}, {n_skills}, {max_skills},
                {existing_skills_list}, {proposals_list}.
            general_curator_prompt: Prompt template for general curation.
                Must have placeholders: {n_failed}, {failed_summaries},
                {n_general}, {max_general}, {general_skills_list}.
            format_failed_summary: Optional function to format a failed summary
                dict into a string for the general curator prompt. If None, uses
                a default formatter.
        """
        self.config = config
        self.max_skills_per_topic = max_skills_per_topic
        self.max_general_skills = max_general_skills
        self.skill_layout = skill_layout
        self.curator_model = curator_model or config.evolver_model
        self.general_curator_model = general_curator_model or self.curator_model
        self.evolve_passed = evolve_passed
        self._cycle_count = 0
        self._region = config.extra.get("region", "us-west-2")

        # Use caller-provided prompts or fall back to minimal defaults
        from .prompts import DEFAULT_TOPIC_CURATOR_PROMPT, DEFAULT_GENERAL_CURATOR_PROMPT
        self._topic_curator_prompt = topic_curator_prompt or DEFAULT_TOPIC_CURATOR_PROMPT
        self._general_curator_prompt = general_curator_prompt or DEFAULT_GENERAL_CURATOR_PROMPT
        self._format_failed_summary = format_failed_summary or _default_format_failed_summary

    def step(
        self,
        workspace: Any,
        observations: list[Observation],
        history: Any,
        trial: Any,
    ) -> StepResult:
        self._cycle_count += 1
        workspace_dir = Path(workspace.root) if hasattr(workspace, "root") else Path(workspace)

        # 1. Extract proposals from observations
        proposals = self._extract_proposals(observations)
        failed_summaries = self._extract_failed_summaries(observations)

        if not proposals and not failed_summaries:
            return StepResult(
                mutated=False,
                summary=f"propose-curate cycle {self._cycle_count}: no proposals or failures",
            )

        # 2. Per-topic curation
        topic_stats = {"added": 0, "merged": 0, "skipped": 0}
        if proposals:
            topic_stats = self._curate_by_topic(proposals, workspace_dir)
            logger.info(
                "Cycle %d topic curation: +%d added, %d merged, %d skipped",
                self._cycle_count, topic_stats["added"], topic_stats["merged"],
                topic_stats["skipped"],
            )

        # 3. General curation (cross-topic patterns)
        general_stats = {"added": 0, "updated": 0, "deleted": 0}
        if self.max_general_skills > 0 and len(failed_summaries) >= 2:
            general_stats = self._curate_general(failed_summaries, workspace_dir)
            logger.info(
                "Cycle %d general curation: +%d added, %d updated, %d deleted",
                self._cycle_count, general_stats["added"], general_stats["updated"],
                general_stats["deleted"],
            )

        mutated = (topic_stats["added"] + topic_stats["merged"] +
                   general_stats["added"] + general_stats["updated"] +
                   general_stats["deleted"]) > 0

        return StepResult(
            mutated=mutated,
            summary=(
                f"propose-curate cycle {self._cycle_count}: "
                f"{len(proposals)} proposals, "
                f"topic(+{topic_stats['added']}/~{topic_stats['merged']}/-{topic_stats['skipped']}), "
                f"general(+{general_stats['added']}/~{general_stats['updated']}/-{general_stats['deleted']})"
            ),
            metadata={
                "cycle": self._cycle_count,
                "proposals": len(proposals),
                "topic_stats": topic_stats,
                "general_stats": general_stats,
            },
        )

    # ── Standalone convenience API (matches GuidedSynthesis pattern) ──

    def evolve(
        self,
        workspace: Any,
        observations: list[Observation],
        evo_number: int = 0,
    ) -> dict[str, Any]:
        """Run one evolution step without the full EvolutionLoop."""
        from ...engine.versioning import VersionControl

        workspace_root = Path(workspace.root) if hasattr(workspace, "root") else Path(workspace)
        vc = VersionControl(workspace_root)
        vc.init()

        vc.commit(
            message=f"pre-propose-curate-{evo_number}: snapshot",
            tag=f"pre-propose-curate-{evo_number}",
        )

        result = self.step(workspace, observations, history=None, trial=None)

        tag_msg = (
            f"propose-curate-{evo_number}: {result.summary}"
            if result.mutated
            else f"propose-curate-{evo_number}: no mutation"
        )
        vc.commit(message=tag_msg, tag=f"propose-curate-{evo_number}")
        return result.metadata

    # ── Proposal extraction ────────────────────────────────────

    def _extract_proposals(self, observations: list[Observation]) -> list[dict]:
        proposals = []
        for obs in observations:
            if not self.evolve_passed and obs.feedback.success:
                continue
            proposal = obs.feedback.raw.get("proposal")
            if proposal and isinstance(proposal, dict):
                if proposal.get("content") and proposal.get("action", "").upper() != "NONE":
                    proposals.append(proposal)
        return proposals

    def _extract_failed_summaries(self, observations: list[Observation]) -> list[dict]:
        summaries = []
        for obs in observations:
            if obs.feedback.success:
                continue
            summary = {
                "task_id": obs.task.id,
                "feedback_detail": obs.feedback.detail or "",
            }
            raw = obs.feedback.raw
            if raw.get("proposal"):
                p = raw["proposal"]
                summary["proposal_summary"] = (
                    f"[{p.get('action', 'NEW')}] {p.get('name', '')}: "
                    f"{p.get('description', '')}"
                )
            # Pass through any extra keys the caller puts in raw
            for key in ("domain", "category", "topic", "trajectory_signals",
                        "compressed_trajectory", "eval_metric", "failure_reason",
                        "bot_detection", "feedback_analysis", "task_name",
                        "context_id", "sub_category"):
                if key in raw:
                    summary[key] = raw[key]
            summaries.append(summary)
        return summaries

    # ── Per-topic curation ─────────────────────────────────────

    def _curate_by_topic(
        self, proposals: list[dict], workspace_dir: Path
    ) -> dict[str, int]:
        topic_groups: dict[str, list[dict]] = defaultdict(list)
        for p in proposals:
            topic_groups[p.get("topic", "general")].append(p)

        total_stats = {"added": 0, "merged": 0, "skipped": 0}
        for topic, topic_proposals in topic_groups.items():
            stats = self._curate_one_topic(topic, topic_proposals, workspace_dir)
            for k in total_stats:
                total_stats[k] += stats.get(k, 0)
        return total_stats

    def _curate_one_topic(
        self, topic: str, proposals: list[dict], workspace_dir: Path
    ) -> dict[str, int]:
        if not proposals:
            return {"added": 0, "merged": 0, "skipped": 0}

        # Resolve skill directory based on layout
        if self.skill_layout == "flat":
            topic_dir = workspace_dir / "skills" / "evolved"
        elif self.skill_layout == "context":
            topic_dir = workspace_dir / "skills" / "context" / topic
        else:
            topic_dir = workspace_dir / "skills" / "topic" / topic

        # Load existing skills for this topic
        existing = self._load_existing_skills(topic_dir)

        # Build prompt
        existing_list = (
            "\n".join(f"- **{n}**: {d}" for n, d in existing)
            if existing else "(empty)"
        )
        proposals_lines = []
        for p in proposals:
            proposals_lines.append(
                f"### [{p.get('action', 'NEW')}] {p.get('name', '?')}\n"
                f"  Source: {p.get('source_task', '?')}\n"
                f"  Description: {p.get('description', '')[:150]}\n"
                f"  Content: {_truncate(p.get('content', ''), 300)}"
            )

        prompt = self._topic_curator_prompt.format(
            topic=topic,
            n_skills=len(existing),
            max_skills=self.max_skills_per_topic,
            existing_skills_list=existing_list,
            proposals_list="\n\n".join(proposals_lines),
        )

        resp = self._call_llm(self.curator_model, prompt, "Review and decide.")
        if not resp:
            return {"added": 0, "merged": 0, "skipped": 0}

        return self._execute_topic_curation(
            resp, proposals, existing, topic_dir, topic
        )

    def _execute_topic_curation(
        self,
        text: str,
        proposals: list[dict],
        existing: list[tuple[str, str]],
        topic_dir: Path,
        topic: str,
    ) -> dict[str, int]:
        proposal_map = {p["name"]: p for p in proposals if p.get("name")}
        existing_names = {n for n, _ in existing}
        count = len(existing)
        stats = {"added": 0, "merged": 0, "skipped": 0}

        def _write(name: str, desc: str, content: str):
            d = topic_dir / name
            d.mkdir(parents=True, exist_ok=True)
            (d / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: {desc}\n---\n\n{content}"
            )

        def _fuzzy(raw: str, names: set[str]) -> str | None:
            clean = re.sub(r"[^a-z0-9-]", "-", raw.lower()).strip("-")
            if clean in names:
                return clean
            for n in names:
                if clean in n or n in clean:
                    return n
            return None

        for line in text.split("\n"):
            s = line.strip()
            u = s.upper()
            if u.startswith("ACCEPT:"):
                pn = _fuzzy(s.split(":", 1)[1].strip(), set(proposal_map.keys()))
                if pn and pn not in existing_names and count < self.max_skills_per_topic:
                    p = proposal_map[pn]
                    _write(pn, p.get("description", ""), p.get("content", ""))
                    existing_names.add(pn)
                    count += 1
                    stats["added"] += 1
                    logger.info("Topic %s ACCEPT: %s", topic, pn)
            elif u.startswith("MERGE:"):
                parts = s.split(":", 1)[1].strip()
                if " INTO " in parts.upper():
                    sp = parts.split(" INTO " if " INTO " in parts else " into ")
                    pn = _fuzzy(sp[0].strip(), set(proposal_map.keys()))
                    tn = _fuzzy(sp[1].strip() if len(sp) > 1 else "", existing_names)
                    if pn and tn:
                        merge_idx = text.find(s)
                        after = text[merge_idx + len(s):]
                        nc = ""
                        if "NEW_CONTENT:" in after:
                            nc = after.split("NEW_CONTENT:", 1)[1]
                            for m in ["ACCEPT:", "MERGE:", "SKIP:", "NO_PROPOSALS"]:
                                if m in nc:
                                    nc = nc[:nc.index(m)]
                            nc = nc.strip()
                        if nc:
                            old_desc = next((d for n, d in existing if n == tn), "")
                            _write(tn, old_desc or proposal_map.get(pn, {}).get("description", ""), nc)
                            stats["merged"] += 1
                            logger.info("Topic %s MERGE: %s into %s", topic, pn, tn)
            elif u.startswith("SKIP:"):
                stats["skipped"] += 1

        return stats

    # ── General curation ───────────────────────────────────────

    def _curate_general(
        self, failed_summaries: list[dict], workspace_dir: Path
    ) -> dict[str, int]:
        gen_dir = workspace_dir / "skills" / "general"
        existing = []
        if gen_dir.exists():
            for sf in sorted(gen_dir.rglob("SKILL.md")):
                content = sf.read_text()
                sn = sf.parent.name
                sd, body = "", content
                for sline in content.split("\n"):
                    if sline.strip().startswith("description:"):
                        sd = sline.split(":", 1)[1].strip()
                        break
                if content.startswith("---"):
                    end = content.find("---", 3)
                    if end != -1:
                        body = content[end + 3:].strip()
                existing.append((sn, sd, body[:300]))

        # Build summary text using the caller-provided formatter
        summary_lines = []
        for s in failed_summaries[:30]:
            summary_lines.append(self._format_failed_summary(s))

        gen_list = (
            "\n".join(f"- **{n}**: {d}" for n, d, _ in existing)
            if existing else "(empty)"
        )

        prompt = self._general_curator_prompt.format(
            n_failed=len(failed_summaries),
            failed_summaries="\n\n".join(summary_lines),
            n_general=len(existing),
            max_general=self.max_general_skills,
            general_skills_list=gen_list,
        )

        resp = self._call_llm(self.general_curator_model, prompt, "Analyze and decide.")
        if not resp:
            return {"added": 0, "updated": 0, "deleted": 0}

        return self._execute_general_curation(resp, workspace_dir, existing)

    def _execute_general_curation(
        self,
        text: str,
        workspace_dir: Path,
        existing: list[tuple[str, str, str]],
    ) -> dict[str, int]:
        existing_names = {n for n, _, _ in existing}
        count = len(existing)
        stats = {"added": 0, "updated": 0, "deleted": 0}

        def _write_gen(name: str, desc: str, content: str):
            d = workspace_dir / "skills" / "general" / name
            d.mkdir(parents=True, exist_ok=True)
            (d / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: {desc}\n---\n\n{content}"
            )

        lines = text.split("\n")
        i = 0
        while i < len(lines):
            s = lines[i].strip()
            u = s.upper()

            if u.startswith("NEW_GENERAL:"):
                name = re.sub(r"[^a-z0-9-]", "-", s.split(":", 1)[1].strip().lower()).strip("-")
                desc, content = "", ""
                i += 1
                while i < len(lines):
                    sl = lines[i].strip()
                    if sl.upper().startswith("DESCRIPTION:"):
                        desc = sl.split(":", 1)[1].strip()[:150]
                    elif sl.upper().startswith("CONTENT:"):
                        cl = []
                        i += 1
                        while i < len(lines):
                            su = lines[i].strip().upper()
                            if any(su.startswith(m) for m in [
                                "NEW_GENERAL:", "UPDATE_GENERAL:", "DELETE_GENERAL:", "NO_PATTERNS"
                            ]):
                                break
                            cl.append(lines[i])
                            i += 1
                        content = "\n".join(cl).strip()
                        break
                    i += 1
                if name and content and count < self.max_general_skills:
                    _write_gen(name, desc, content)
                    count += 1
                    stats["added"] += 1
                    logger.info("General NEW: %s", name)
                continue

            elif u.startswith("UPDATE_GENERAL:"):
                raw = s.split(":", 1)[1].strip()
                name = re.sub(r"[^a-z0-9-]", "-", raw.lower()).strip("-")
                matched = next(
                    (n for n in existing_names if name == n or name in n or n in name),
                    None,
                )
                content = ""
                i += 1
                while i < len(lines):
                    sl = lines[i].strip()
                    if sl.upper().startswith("NEW_CONTENT:") or sl.upper().startswith("CONTENT:"):
                        cl = []
                        i += 1
                        while i < len(lines):
                            su = lines[i].strip().upper()
                            if any(su.startswith(m) for m in [
                                "NEW_GENERAL:", "UPDATE_GENERAL:", "DELETE_GENERAL:", "NO_PATTERNS"
                            ]):
                                break
                            cl.append(lines[i])
                            i += 1
                        content = "\n".join(cl).strip()
                        break
                    i += 1
                if matched and content:
                    old_desc = next((d for n, d, _ in existing if n == matched), "")
                    _write_gen(matched, old_desc, content)
                    stats["updated"] += 1
                    logger.info("General UPDATE: %s", matched)
                continue

            elif u.startswith("DELETE_GENERAL:"):
                raw = s.split(":", 1)[1].strip()
                name = re.sub(r"[^a-z0-9-]", "-", raw.lower()).strip("-")
                matched = next(
                    (n for n in existing_names if name == n or name in n or n in name),
                    None,
                )
                if matched:
                    d = workspace_dir / "skills" / "general" / matched
                    if d.exists():
                        shutil.rmtree(d)
                        existing_names.discard(matched)
                        count -= 1
                        stats["deleted"] += 1
                        logger.info("General DELETE: %s", matched)

            i += 1
        return stats

    # ── Helpers ────────────────────────────────────────────────

    def _load_existing_skills(self, topic_dir: Path) -> list[tuple[str, str]]:
        existing = []
        if not topic_dir.exists():
            return existing
        for sf in sorted(topic_dir.rglob("SKILL.md")):
            content = sf.read_text()
            sn = sf.parent.name
            sd = ""
            for sline in content.split("\n"):
                if sline.strip().startswith("description:"):
                    sd = sline.split(":", 1)[1].strip()
                    break
            existing.append((sn, sd))
        return existing

    def _call_llm(self, model_id: str, system_prompt: str, user_message: str) -> str | None:
        """Call Bedrock LLM. Returns response text or None on failure."""
        import boto3
        from botocore.config import Config as BotoConfig
        import time

        client = boto3.client(
            "bedrock-runtime",
            region_name=self._region,
            config=BotoConfig(read_timeout=300, retries={"max_attempts": 0}),
        )

        for attempt in range(5):
            try:
                resp = client.converse(
                    modelId=model_id,
                    system=[{"text": system_prompt}],
                    messages=[{"role": "user", "content": [{"text": user_message}]}],
                    inferenceConfig={"maxTokens": 4096, "temperature": 0.0},
                )
                content = resp.get("output", {}).get("message", {}).get("content", [])
                text = "".join(b.get("text", "") for b in content)
                return text.strip() or None
            except Exception as e:
                err = str(e)
                base = 30 if "too many tokens" in err.lower() else (
                    4 if "throttl" in err.lower() else 2
                )
                delay = base * (2 ** attempt)
                if attempt < 4:
                    logger.warning("LLM call attempt %d failed: %s — retrying in %ds",
                                   attempt + 1, err[:120], delay)
                    time.sleep(delay)
                else:
                    logger.error("LLM call exhausted retries: %s", err[:200])
                    return None
        return None


def _default_format_failed_summary(s: dict) -> str:
    """Default formatter for failed task summaries in the general curator prompt."""
    parts = [f"### {s.get('task_name', s.get('task_id', '?'))}"]
    if s.get("domain") or s.get("category"):
        parts.append(f"Domain: {s.get('domain', s.get('category', ''))}")
    if s.get("eval_metric"):
        parts.append(f"Eval metric: {s['eval_metric']}")
    if s.get("failure_reason"):
        parts.append(f"Failure reason: {s['failure_reason']}")
    if s.get("bot_detection"):
        parts.append(f"Bot detection: {s['bot_detection']}")
    if s.get("trajectory_signals"):
        sig = s["trajectory_signals"]
        if isinstance(sig, dict):
            parts.append(
                f"Signals: turns={sig.get('n_turns', '?')}, "
                f"actions={sig.get('n_actions', '?')}, "
                f"errors={sig.get('n_errors', '?')}"
            )
    if s.get("compressed_trajectory"):
        parts.append(f"Trajectory:\n{_truncate(s['compressed_trajectory'], 400)}")
    if s.get("feedback_analysis"):
        parts.append(f"Analysis:\n{_truncate(s['feedback_analysis'], 400)}")
    if s.get("proposal_summary"):
        parts.append(f"Proposal: {_truncate(s['proposal_summary'], 200)}")
    return "\n".join(parts)


def _truncate(s: str, n: int = 300) -> str:
    return s[:n] + "..." if len(s) > n else s
