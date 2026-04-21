"""FixHallucinations — write tool-name correction skill + prune memory.

Reference: ``agent_evolve/algorithms/adaptive_evolve/engine.py`` lines
401-415 (``_apply_auto_corrections``) plus its nested
``self._prune_memory(workspace)`` call at line 414. The legacy method is a
compound: it both writes the ``tool-name-corrections`` skill (via
``McpAutoCorrector.apply``) and prunes the memory dir to a fixed cap, so
this operator reproduces **both** behaviours to match the legacy step() path
byte-for-byte.

DEC-1/DEC-2: the reimplementation lives entirely under ``unified/`` and does
not import from any legacy engine module.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..registry import register_operator
from ..types import MutationReport

logger = logging.getLogger(__name__)


@register_operator("FixHallucinations")
class FixHallucinations:
    """Operator that fuses hallucination-correction + memory pruning.

    Internal state (AC-6 plan name: ``state["_accumulated_state"]``,
    a dict that wraps the per-atom carry-over):

        ``state["_accumulated_state"]["name_corrections"]`` — dict[str, str]
        of wrong→correct tool names accumulated across cycles. Seeded
        from ``context["PatternDetector"]`` or hallucination hints
        carried in observations; callers may also push to this dict
        directly from their own diagnostics. Mirrors legacy
        ``AdaptiveEvolveEngine._accumulated_state["name_corrections"]``
        at ``adaptive_evolve/engine.py:204``.

        ``state["_accumulated_state"]["existing_param_keys"]`` — set of
        tool names already recorded with param-error memory so
        duplicates are skipped across cycles.

        ``state["memory_cap"]`` — int (default 15); legacy default.
    """

    WRITES: frozenset[str] = frozenset({"skills", "memory"})

    def apply(
        self,
        workspace: Any,
        context: Any,
        scope: dict[str, Any],
        state: dict[str, Any],
    ) -> MutationReport:
        fixes = 0
        details: dict[str, Any] = {}

        accumulated: dict[str, Any] = state.setdefault("_accumulated_state", {})
        name_corrections: dict[str, str] = accumulated.setdefault(
            "name_corrections", {}
        )
        memory_cap: int = int(state.get("memory_cap", 15))
        existing_param_keys: set[str] = accumulated.setdefault(
            "existing_param_keys", set()
        )

        # Pull hallucination map out of the PatternDetector / observation info if present.
        patterns_out = context.entries.get("PatternDetector", {}) if hasattr(context, "entries") else {}
        hallucinations = patterns_out.get("hallucination_map", {}) or {}
        for k, v in hallucinations.items():
            name_corrections[str(k)] = str(v)

        if name_corrections and _scope_allows(scope, "skills"):
            fixes += _write_name_correction_skill(workspace, name_corrections)
            details["name_corrections_written"] = len(name_corrections)

        param_errors = patterns_out.get("param_errors", []) or []
        if param_errors and _scope_allows(scope, "memory"):
            new_entries = _write_param_memory(workspace, param_errors, existing_param_keys)
            fixes += new_entries
            details["param_memory_added"] = new_entries

        # Memory pruning — matches legacy _prune_memory nested behaviour.
        if _scope_allows(scope, "memory"):
            pruned = _prune_memory(workspace, memory_cap)
            if pruned:
                details["memory_entries_pruned"] = pruned
                fixes += 1  # legacy returns min(pruned, 1)

        return MutationReport(
            operator_name="FixHallucinations",
            count=fixes,
            details=details,
        )


def _scope_allows(scope: dict[str, Any], artifact: str) -> bool:
    mode = scope.get(artifact)
    return mode in ("rw", "append")


def _write_name_correction_skill(workspace: Any, corrections: dict[str, str]) -> int:
    if not corrections:
        return 0
    lines = [
        "---",
        "name: tool-name-corrections",
        "description: Maps commonly hallucinated tool names to correct names",
        "---",
        "",
        "# Tool Name Corrections",
        "",
        "Use EXACT tool names. Common mistakes and their corrections:",
        "",
        "| Wrong Name | Correct Name |",
        "|------------|-------------|",
    ]
    for wrong, correct in sorted(corrections.items()):
        lines.append(f"| `{wrong}` | `{correct}` |")
    lines.extend(
        [
            "",
            "Always verify tool names against the available tool list before calling.",
        ]
    )
    content = "\n".join(lines) + "\n"
    skill_path = Path(workspace.root) / "skills" / "tool-name-corrections" / "SKILL.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text(content)
    logger.info("Wrote tool-name-corrections skill (%d mappings)", len(corrections))
    return 1


def _write_param_memory(
    workspace: Any,
    param_errors: list[dict[str, Any]],
    existing_keys: set[str],
) -> int:
    memory_dir = Path(workspace.root) / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    memory_file = memory_dir / "tool_param_errors.jsonl"

    # Hydrate existing_keys from disk on first call.
    if memory_file.exists() and not existing_keys:
        for line in memory_file.read_text().splitlines():
            if line.strip():
                try:
                    entry = json.loads(line)
                    existing_keys.add(entry.get("tool", ""))
                except json.JSONDecodeError:
                    pass

    new_entries = 0
    with open(memory_file, "a") as f:
        for pe in param_errors:
            tool = pe.get("tool", "")
            if tool and tool not in existing_keys:
                entry = {
                    "tool": tool,
                    "error": pe.get("error", "")[:300],
                    "type": "param_error",
                }
                f.write(json.dumps(entry) + "\n")
                existing_keys.add(tool)
                new_entries += 1
    if new_entries:
        logger.info("Added %d param error entries to memory", new_entries)
    return new_entries


def _prune_memory(workspace: Any, cap: int) -> int:
    memory_dir = Path(workspace.root) / "memory"
    if not memory_dir.exists():
        return 0
    pruned = 0
    for mem_file in memory_dir.glob("*.jsonl"):
        lines = [l for l in mem_file.read_text().splitlines() if l.strip()]
        if len(lines) > cap:
            kept = lines[-cap:]
            mem_file.write_text("\n".join(kept) + "\n")
            pruned += len(lines) - cap
    if pruned:
        logger.info("Pruned %d memory entries (cap=%d)", pruned, cap)
    return pruned
