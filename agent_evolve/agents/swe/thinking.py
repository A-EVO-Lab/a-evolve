"""Sequential thinking tool -- from CodeDojo/swe-agent (MIT, ByteDance/Anthropic)."""

from __future__ import annotations

import json

from strands import tool

_thought_history: list[dict] = []
_branches: dict[str, list[dict]] = {}


def reset_thinking() -> None:
    """Reset thinking state between instances."""
    global _thought_history, _branches
    _thought_history = []
    _branches = {}


@tool
def sequentialthinking(
    thought: str,
    next_thought_needed: bool,
    thought_number: int,
    total_thoughts: int,
    is_revision: bool = False,
    revises_thought: int = 0,
    branch_from_thought: int = 0,
    branch_id: str = "",
    needs_more_thoughts: bool = False,
) -> str:
    """Break down complex problems through step-by-step reasoning.

    Use this to plan, analyze, revise, and verify before acting.
    Each thought builds on previous ones. You can revise, branch, or extend.

    Args:
        thought: Your current thinking step.
        next_thought_needed: True if more thinking is needed.
        thought_number: Current step number (starts at 1).
        total_thoughts: Estimated total steps (adjust as needed).
        is_revision: True if revising a previous thought.
        revises_thought: Which thought number is being revised.
        branch_from_thought: Branching point thought number.
        branch_id: Branch identifier.
        needs_more_thoughts: True if more steps needed beyond total.
    """
    entry = {
        "thought": thought,
        "thought_number": thought_number,
        "total_thoughts": total_thoughts,
        "next_thought_needed": next_thought_needed,
        "is_revision": is_revision,
        "revises_thought": revises_thought or None,
        "branch_from_thought": branch_from_thought or None,
        "branch_id": branch_id or None,
        "needs_more_thoughts": needs_more_thoughts,
    }

    if thought_number > total_thoughts:
        entry["total_thoughts"] = thought_number

    _thought_history.append(entry)

    if branch_from_thought and branch_id:
        _branches.setdefault(branch_id, []).append(entry)

    status = {
        "thought_number": entry["thought_number"],
        "total_thoughts": entry["total_thoughts"],
        "next_thought_needed": next_thought_needed,
        "branches": list(_branches.keys()),
        "history_length": len(_thought_history),
    }

    return f"Thinking step recorded.\n{json.dumps(status, indent=2)}"
