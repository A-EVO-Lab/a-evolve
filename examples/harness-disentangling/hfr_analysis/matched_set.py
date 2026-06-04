"""Identify the 288 matched (task × skill × evolver) cells on SkillBench.

A matched cell is defined as: a (task, skill, evolver) triple where the
same `skill` was loaded by both a strong solver (Opus 4.6 / Sonnet 4.6)
and a mid/weak solver (GPT-OSS-120B / Qwen3-235B / Qwen3-32B) under the
same evolver.

Output: list of dicts, each describing a matched cell pair (one strong
+ one mid/weak), keyed by (task, skill, evolver). The same triple may
appear multiple times if multiple strong solvers + multiple weak
solvers all loaded the same skill.
"""
from __future__ import annotations
import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, asdict


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _find_evolverbench_root(start: str) -> str:
    """Walk up from `start` until `_region_picker.py` (EvolverBench root marker) is found."""
    d = start
    for _ in range(6):
        if os.path.exists(os.path.join(d, "_region_picker.py")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    raise RuntimeError(
        f"Could not locate EvolverBench root from {start} (no _region_picker.py within 6 parent levels)"
    )


EVOLVERBENCH_ROOT = _find_evolverbench_root(SCRIPT_DIR)

STRONG = ["opus46", "sonnet46"]
MIDWEAK = ["gptoss120b", "qwen235b", "qwen32b"]
EVOLVERS = ["opus46", "sonnet46", "qwen235b"]


def parse_jsonl_line(line: str) -> dict | None:
    s = line.strip()
    s = re.sub(r"\bFalse\b", "false", s)
    s = re.sub(r"\bTrue\b", "true", s)
    s = re.sub(r"\bNone\b", "null", s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None


def sb_results_path(cell: str) -> str:
    return os.path.join(
        EVOLVERBENCH_ROOT, "results", "exp1_final_sb", cell, "results.jsonl"
    )


def load_sb_cell(cell: str) -> dict[str, dict]:
    """Return per-task info: passed (final), skills_loaded (union over cycles),
    score (max over cycles)."""
    path = sb_results_path(cell)
    if not os.path.exists(path):
        return {}
    per_task: dict[str, dict] = {}
    with open(path) as f:
        for line in f:
            r = parse_jsonl_line(line)
            if not r:
                continue
            tid = r.get("task_id")
            if not tid:
                continue
            entry = per_task.setdefault(
                tid,
                {
                    "task_id": tid,
                    "skills_loaded": set(),
                    "max_score": 0.0,
                    "final_passed": None,
                    "cycles_seen": 0,
                },
            )
            sl = r.get("skills_loaded") or []
            for s in sl:
                entry["skills_loaded"].add(s)
            sc = r.get("score")
            if isinstance(sc, (int, float)):
                entry["max_score"] = max(entry["max_score"], float(sc))
            if r.get("final") is True:
                entry["final_passed"] = bool(r.get("passed"))
            if r.get("cycle") is not None:
                entry["cycles_seen"] += 1
    # Cast set to sorted list for JSON
    for tid, info in per_task.items():
        info["skills_loaded"] = sorted(info["skills_loaded"])
    return per_task


@dataclass
class MatchedPair:
    task: str
    skill: str
    evolver: str
    strong_solver: str
    midweak_solver: str
    strong_passed: bool | None
    midweak_passed: bool | None
    strong_score: float
    midweak_score: float
    strong_cycles: int
    midweak_cycles: int


def build_matched_set() -> list[MatchedPair]:
    """Build the 288-ish matched-pair list."""
    # Load all relevant cells
    by_cell: dict[tuple[str, str], dict[str, dict]] = {}
    for solver in STRONG + MIDWEAK:
        for evol in EVOLVERS:
            cell = f"{solver}_x_{evol}_sb_s42"
            data = load_sb_cell(cell)
            if data:
                by_cell[(solver, evol)] = data

    pairs: list[MatchedPair] = []
    # For each evolver
    for evol in EVOLVERS:
        # For each task in the universe
        all_tasks: set[str] = set()
        for solver in STRONG + MIDWEAK:
            d = by_cell.get((solver, evol), {})
            all_tasks |= set(d.keys())
        for task in sorted(all_tasks):
            # Skill sets per solver tier
            strong_skill_map: dict[str, set[str]] = {}
            for s in STRONG:
                info = by_cell.get((s, evol), {}).get(task)
                if info:
                    strong_skill_map[s] = set(info["skills_loaded"])
            midweak_skill_map: dict[str, set[str]] = {}
            for s in MIDWEAK:
                info = by_cell.get((s, evol), {}).get(task)
                if info:
                    midweak_skill_map[s] = set(info["skills_loaded"])
            # Intersection per (strong_solver, midweak_solver) for each common skill
            for ss, ss_skills in strong_skill_map.items():
                for ms, ms_skills in midweak_skill_map.items():
                    common = ss_skills & ms_skills
                    for sk in common:
                        ss_info = by_cell[(ss, evol)][task]
                        ms_info = by_cell[(ms, evol)][task]
                        pairs.append(MatchedPair(
                            task=task,
                            skill=sk,
                            evolver=evol,
                            strong_solver=ss,
                            midweak_solver=ms,
                            strong_passed=ss_info["final_passed"],
                            midweak_passed=ms_info["final_passed"],
                            strong_score=ss_info["max_score"],
                            midweak_score=ms_info["max_score"],
                            strong_cycles=ss_info["cycles_seen"],
                            midweak_cycles=ms_info["cycles_seen"],
                        ))
    return pairs


def cell_id(solver: str, evolver: str, task: str, skill: str) -> str:
    return f"{solver}_x_{evolver}_{task}_skill_{skill}"


def unique_cells_from_pairs(pairs: list[MatchedPair]) -> list[dict]:
    """Flatten matched pairs into a unique list of cells to judge.

    Each unique (solver, evolver, task, skill) cell shows up once.
    """
    seen: set[tuple[str, str, str, str]] = set()
    cells: list[dict] = []
    for p in pairs:
        for solver, passed, score, cycles in [
            (p.strong_solver, p.strong_passed, p.strong_score, p.strong_cycles),
            (p.midweak_solver, p.midweak_passed, p.midweak_score, p.midweak_cycles),
        ]:
            key = (solver, p.evolver, p.task, p.skill)
            if key in seen:
                continue
            seen.add(key)
            cells.append({
                "cell_id": cell_id(solver, p.evolver, p.task, p.skill),
                "solver": solver,
                "evolver": p.evolver,
                "task": p.task,
                "skill": p.skill,
                "tier": "strong" if solver in STRONG else "midweak",
                "passed": passed,
                "score": score,
                "cycles": cycles,
            })
    return cells


if __name__ == "__main__":
    pairs = build_matched_set()
    cells = unique_cells_from_pairs(pairs)
    unique_skills = sorted({c["skill"] for c in cells})
    unique_tasks = sorted({c["task"] for c in cells})
    print(f"matched pairs: {len(pairs)}")
    print(f"unique cells: {len(cells)}")
    print(f"unique skills: {len(unique_skills)}")
    print(f"unique tasks: {len(unique_tasks)}")
    print(f"by tier: strong={sum(1 for c in cells if c['tier']=='strong')}, midweak={sum(1 for c in cells if c['tier']=='midweak')}")
    print(f"by solver: ")
    from collections import Counter
    print("  ", Counter(c["solver"] for c in cells))
    # Save
    out_dir = os.path.join(SCRIPT_DIR, "_artefacts")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "matched_pairs.json"), "w") as f:
        json.dump([asdict(p) for p in pairs], f, indent=2)
    with open(os.path.join(out_dir, "matched_cells.json"), "w") as f:
        json.dump(cells, f, indent=2)
    print(f"\nSaved to {out_dir}/")
