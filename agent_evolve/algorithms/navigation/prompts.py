"""Prompt templates shared by every evolution template.

Only **routing-time** prompts live here — the F_Navigate system prompt
and the helper that renders each branch's workspace content for the
routing LLM.  These are consumed by ``NavigationEngine.navigate`` and
are used regardless of which evolution template is active.

Template-specific prompts (batch analysis, inline branching guidance)
live alongside the template file that uses them:

  - ``templates/orchestrated.py`` owns ``ANALYZE_PLAN_SYSTEM_PROMPT`` +
    ``build_analyze_plan_prompt``.
  - ``templates/inline.py`` owns ``build_branching_section``.
"""

from __future__ import annotations

from typing import Any


NAVIGATE_SYSTEM_PROMPT = """\
You are a fast task router. For each task you must pick exactly one branch
from the list provided.

Decision procedure:
1. Read each branch's README, focusing on its "When to route here" section.
2. Match the task description against each branch's positive signals
   (categories, keywords, metadata) and negative signals (NOT applicable).
3. If exactly one specialized branch matches strongly → route there with
   high confidence (>= 0.7).
4. If multiple branches partially match or none match clearly → route to
   "main" with confidence reflecting your uncertainty.
5. Branches without a clear "When to route here" section should be treated
   as low-confidence candidates — prefer main unless the regime is obvious.

Output JSON only:
{"branch": "main", "confidence": 0.8, "reason": "brief reason"}
"""


def build_navigate_prompt(
    task_description: str,
    branches: list[dict[str, Any]],
) -> str:
    """Build a compact prompt for F_Navigate using branch READMEs."""
    branch_sections = []
    for b in branches:
        lines = [f"### {b['name']}"]
        desc = b.get('description', '')
        if desc:
            lines.append(desc)
        readme = b.get("readme", "")
        if readme:
            lines.append(readme[:1000])
        branch_sections.append("\n".join(lines))

    return f"""\
## Task

{task_description}

## Branches

{chr(10).join(branch_sections)}

---
Route to best branch. JSON: {{"branch": "<name>", "confidence": <0-1>, "reason": "<brief>"}}
"""
