"""Run HFR pipeline on Haiku 4.5 SkillBench cells.

Haiku was excluded from the original matched-pair run (matched_set.py only
covers STRONG = {opus46, sonnet46} and MIDWEAK = {gptoss120b, qwen235b,
qwen32b}). This script builds a Haiku-only cell list, reuses the cached
rubrics where possible, extracts the missing ones, then runs stage 2
(Sonnet judging) + stage 4 (mechanical proxies), and prints Haiku's
required_adherence so it can drop into Table~\\ref{tab:agent_sfr}.
"""
from __future__ import annotations
import json
import os
import statistics
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from matched_set import load_sb_cell, cell_id, EVOLVERS  # noqa: E402
from bedrock_client import SonnetJudge  # noqa: E402
from pipeline import (  # noqa: E402
    stage1_extract_rubrics,
    stage2_judge_cells,
    stage4_mechanical,
    ARTEFACTS,
)
from aggregate import compute_sfr  # noqa: E402


HAIKU = "haiku45"


def build_haiku_cells() -> list[dict]:
    """One cell per (haiku45, evolver, task, skill) where the skill loaded."""
    cells: list[dict] = []
    for evol in EVOLVERS:
        cell_name = f"{HAIKU}_x_{evol}_sb_s42"
        per_task = load_sb_cell(cell_name)
        for tid, info in per_task.items():
            for skill in info["skills_loaded"]:
                cells.append({
                    "cell_id": cell_id(HAIKU, evol, tid, skill),
                    "solver": HAIKU,
                    "evolver": evol,
                    "task": tid,
                    "skill": skill,
                    "tier": "other",  # Haiku sits outside the strong/midweak schema
                    "passed": info.get("final_passed"),
                    "score": info.get("max_score"),
                    "cycles": info.get("cycles_seen", 0),
                })
    return cells


def main():
    cells = build_haiku_cells()
    print(f"Built {len(cells)} Haiku cells across {len(EVOLVERS)} evolvers", flush=True)

    judge = SonnetJudge()
    print(f"Judge: model={judge.model_id}, region={judge.region}", flush=True)

    t0 = time.time()
    print("\n[stage 1] rubric extraction (reuses cached rubrics)", flush=True)
    rubrics = stage1_extract_rubrics(cells, judge, max_workers=8)
    print(f"  rubrics for {len(rubrics)} (skill, evolver) combos available", flush=True)

    print("\n[stage 2] per-cell judging (Sonnet 4.6 verdicts)", flush=True)
    judgments = stage2_judge_cells(cells, rubrics, judge, max_workers=8)
    print(f"  {len(judgments)} judgments written", flush=True)

    print("\n[stage 4] mechanical proxies", flush=True)
    mech = stage4_mechanical(cells, rubrics)
    print(f"  {len(mech)} mechanical-proxy files written", flush=True)

    # Aggregate Haiku-only required_adherence
    print("\n[aggregate] Haiku required_adherence + observed_sfr", flush=True)
    req_vals, obs_vals = [], []
    n_cells_with_req = 0
    for j in judgments.values():
        sfr = compute_sfr(j)
        if sfr["required_adherence"] is not None:
            req_vals.append(sfr["required_adherence"])
            n_cells_with_req += 1
        if sfr["observed_sfr"] is not None:
            obs_vals.append(sfr["observed_sfr"])

    if req_vals:
        mean_req = statistics.mean(req_vals)
        mean_obs = statistics.mean(obs_vals) if obs_vals else float("nan")
        print(f"\nHaiku 4.5 HFR (Required-Adherence): {mean_req:.4f}  (n={n_cells_with_req}/{len(judgments)})")
        print(f"Haiku 4.5 Observed-SFR:             {mean_obs:.4f}  (n={len(obs_vals)})")
    else:
        print("\nHaiku 4.5 HFR: NO DATA (no judgments returned)")

    elapsed = time.time() - t0
    print(f"\nElapsed: {elapsed:.1f}s")

    # Per-evolver breakdown
    print("\nPer-evolver breakdown:")
    by_evol = {}
    for j in judgments.values():
        meta = j.get("_meta", {})
        evol = meta.get("evolver", "?")
        sfr = compute_sfr(j)
        if sfr["required_adherence"] is not None:
            by_evol.setdefault(evol, []).append(sfr["required_adherence"])
    for evol, vals in sorted(by_evol.items()):
        print(f"  haiku45 × {evol:>10}: mean={statistics.mean(vals):.4f}  n={len(vals)}")


if __name__ == "__main__":
    main()
