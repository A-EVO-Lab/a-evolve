"""DraftReader — list solver-proposed skill drafts in the workspace.

Drafts are SKILL.md candidates that the agent wrote during solve(); the
evolver either promotes them to real skills or discards them.

Reference: contract/workspace.py ``AgentWorkspace.list_drafts()``.
"""

from __future__ import annotations

from typing import Any

from ..registry import register_reader


@register_reader("DraftReader")
class DraftReader:
    """Output keys:

        "drafts": list of {"name": str, "content": str} sorted by name
        "n_drafts": int
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
        drafts = list(workspace.list_drafts())
        drafts.sort(key=lambda d: d.get("name", ""))
        return {"drafts": drafts, "n_drafts": len(drafts)}
