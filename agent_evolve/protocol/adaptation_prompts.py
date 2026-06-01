"""Solve-time SELECTION prompts for the M2 agentic-filter adaptation operator.

IMPORTANT: these are *solve-time* prompts used to pick a relevant subset of
harness items per task. They are NOT evolution-stage prompts (analyst /
researcher / builder / verifier) and are kept deliberately separate from the
evolver/navigator prompts under algorithms/navigation/.

No ground-truth / outcome information is ever placed in these prompts — the
selector conditions on the task description and item metadata only (upholds
the adaptation no-feedback invariant).
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .catalog import CatalogItem

SELECTION_SYSTEM_PROMPT = (
    "You are a harness selector. Given a task and a list of candidate "
    "harness items (skills, tools, memory notes) retrieved for it, choose "
    "the MINIMAL subset that is actually relevant to solving this specific "
    "task. Prefer precision: exclude items that do not clearly help. "
    "Return ONLY a JSON array of the chosen item ids, e.g. "
    '["tool:espn_scores", "skill:sports_prediction"]. No prose.'
)


def build_selection_prompt(task_text: str, candidates: list["CatalogItem"]) -> str:
    """User message: the task + the candidate items (id, kind, name, snippet)."""
    lines = ["## Task", task_text.strip(), "", "## Candidate harness items"]
    for c in candidates:
        snippet = c.text[:240].replace("\n", " ")
        lines.append(f"- id={c.id} | kind={c.kind} | name={c.name} | {snippet}")
    lines.append("")
    lines.append("Return a JSON array of the ids to load for THIS task "
                 "(minimal relevant subset).")
    return "\n".join(lines)


def parse_selection(content: str | None, valid_ids: set[str]) -> list[str]:
    """Parse the model's JSON array of chosen ids; keep only valid ones.

    Tolerant: finds the first JSON array in the text. Returns [] if none
    parse, which the caller treats as "fall back to top-k".
    """
    if not content:
        return []
    m = re.search(r"\[.*?\]", content, re.DOTALL)
    if not m:
        return []
    try:
        ids = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(ids, list):
        return []
    out, seen = [], set()
    for x in ids:
        s = str(x).strip()
        if s in valid_ids and s not in seen:
            out.append(s)
            seen.add(s)
    return out
