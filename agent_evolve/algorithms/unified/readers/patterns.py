"""PatternDetector — identify named failure patterns in the batch.

Reference: ``agent_evolve/algorithms/adaptive_evolve/analyzer.py`` lines 396-481
(``FailurePatternDetector``). Independent reimplementation under ``unified/``.

Produces a deterministically-sorted list of named patterns that downstream
operators like ``AutoSeedSkills`` use as triggers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ..registry import register_reader


@dataclass
class _Pattern:
    pattern_name: str
    count: int
    task_ids: list[str]
    description: str
    suggested_fix: str
    examples: list[dict[str, Any]] = field(default_factory=list)


@register_reader("PatternDetector")
class PatternDetector:
    """Output keys:

        "patterns": list of dicts with keys {"pattern_name", "count",
                     "task_ids", "description", "suggested_fix", "examples"},
                     sorted by pattern_name.
        "names": sorted list[str] of detected pattern names (convenience)
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
        obs_records: list[dict[str, Any]] = []
        for obs in observations:
            obs_records.append(
                {
                    "task_id": getattr(obs.task, "id", ""),
                    "task_input": getattr(obs.task, "input", "") or "",
                    "score": round(float(getattr(obs.feedback, "score", 1.0)), 4),
                    "success": bool(getattr(obs.feedback, "success", False)),
                    "output": getattr(obs.trajectory, "output", "") or "",
                    "steps": list(getattr(obs.trajectory, "steps", []) or []),
                }
            )

        patterns: list[_Pattern] = []

        # Pattern 1: multi-requirement misses (score ~0.5 and task has "and"/"also").
        multi_req: list[str] = []
        for o in obs_records:
            if 0.45 <= o["score"] <= 0.55 and (
                " and " in o["task_input"] or " also " in o["task_input"]
            ):
                multi_req.append(o["task_id"])
        if len(multi_req) >= 3:
            patterns.append(
                _Pattern(
                    pattern_name="multi_requirement_miss",
                    count=len(multi_req),
                    task_ids=sorted(multi_req)[:5],
                    description="Agent fulfills some requirements but misses others (score ~0.5)",
                    suggested_fix="Add structured requirement extraction protocol",
                )
            )

        # Pattern 2: complete misses with non-empty output.
        complete_miss: list[str] = []
        for o in obs_records:
            if o["score"] == 0.0 and len(o["output"]) > 100:
                complete_miss.append(o["task_id"])
        if len(complete_miss) >= 2:
            patterns.append(
                _Pattern(
                    pattern_name="wrong_entity_targeting",
                    count=len(complete_miss),
                    task_ids=sorted(complete_miss)[:5],
                    description="Agent produces output but scores 0.0 (wrong entity likely)",
                    suggested_fix="Add early entity verification checkpoint",
                )
            )

        # Pattern 3: near misses around 0.67-0.73.
        near_miss: list[str] = []
        for o in obs_records:
            if 0.65 <= o["score"] <= 0.75:
                near_miss.append(o["task_id"])
        if len(near_miss) >= 3:
            patterns.append(
                _Pattern(
                    pattern_name="near_miss",
                    count=len(near_miss),
                    task_ids=sorted(near_miss)[:5],
                    description="Agent gets most claims right but misses one detail",
                    suggested_fix="Strengthen final verification: check EVERY requirement",
                )
            )

        # Pattern 4: code-execution under-utilization on long/failing trajectories.
        code_needed: list[str] = []
        for o in obs_records:
            total_calls = 0
            code_calls = 0
            for step in o["steps"]:
                tcs = step.get("tool_calls", []) if isinstance(step, dict) else []
                total_calls += len(tcs)
                for tc in tcs:
                    if "execute_code" in str(tc.get("tool", "")):
                        code_calls += 1
            if total_calls >= 15 and code_calls == 0 and not o["success"]:
                code_needed.append(o["task_id"])
        if len(code_needed) >= 2:
            patterns.append(
                _Pattern(
                    pattern_name="missed_code_opportunity",
                    count=len(code_needed),
                    task_ids=sorted(code_needed)[:5],
                    description="Tasks with 15+ tool calls but no code execution (search/iteration likely)",
                    suggested_fix="Lower code execution threshold: use for search tasks at 10+ calls",
                )
            )

        patterns.sort(key=lambda p: p.pattern_name)
        return {
            "patterns": [asdict(p) for p in patterns],
            "names": [p.pattern_name for p in patterns],
        }
