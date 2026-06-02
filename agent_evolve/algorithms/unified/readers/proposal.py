"""ProposalReader — extract solver-attached skill proposals from trajectories.

Reference: agents/swe/agent.py:279-379 where the SWE solver attaches a
``_skill_proposal`` string to each Trajectory. The evolver curates these
into the skill library.

Output format is canonicalized (sorted by source_task_id, deterministic
``str`` shape) so downstream prompt building is byte-stable.
"""

from __future__ import annotations

from typing import Any

from ..registry import register_reader


@register_reader("ProposalReader")
class ProposalReader:
    """Output keys:

        "proposals": list of {
            "source_task_id": str,
            "raw": str,
            "action": str,
            "confidence": str,
            "target": str,
            "name": str,
            "description": str,
            "content": str,
        } sorted by source_task_id. Any fields absent in the raw proposal
        default to empty string.
        "n_proposals": int
    """

    def read(
        self,
        observations: list,
        workspace: Any,
        history: Any,
        config: Any,
        context: Any,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        proposals: list[dict[str, Any]] = []
        for obs in observations:
            raw = getattr(obs.trajectory, "_skill_proposal", "") or ""
            if not raw or "ACTION: NONE" in raw.upper():
                continue
            parsed = _parse_proposal(raw)
            parsed["source_task_id"] = getattr(obs.task, "id", "")
            parsed["raw"] = raw
            proposals.append(parsed)
        proposals.sort(key=lambda p: p["source_task_id"])
        return {"proposals": proposals, "n_proposals": len(proposals)}


def _parse_proposal(raw: str) -> dict[str, Any]:
    """Extract metadata + TYPE/NAME/DESCRIPTION/CONTENT from the raw proposal.

    Tolerates missing fields by defaulting to empty string. Does not
    attempt to validate the content; that is the curator's job.
    """
    action = ""
    confidence = ""
    target = ""
    analysis = ""
    itype = ""
    name = ""
    description = ""
    content_lines: list[str] = []
    in_content = False
    for line in raw.splitlines():
        upper = line.strip().upper()
        if upper.startswith("ACTION:"):
            action = line.split(":", 1)[1].strip()
        elif upper.startswith("CONFIDENCE:"):
            confidence = line.split(":", 1)[1].strip()
        elif upper.startswith("TARGET:"):
            target = line.split(":", 1)[1].strip()
        elif upper.startswith("ANALYSIS:"):
            analysis = line.split(":", 1)[1].strip()
        elif line.startswith("TYPE:"):
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
    content = "\n".join(content_lines).strip()
    return {
        "action": action.upper(),
        "confidence": confidence.upper(),
        "target": target,
        "analysis": analysis,
        "type": itype,
        "name": name,
        "description": description,
        "content": content,
    }
