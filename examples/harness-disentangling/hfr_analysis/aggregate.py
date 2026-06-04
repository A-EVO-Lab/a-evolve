"""Aggregate SFR pipeline outputs into report-ready tables + statistics."""
from __future__ import annotations
import csv
import json
import math
import os
import random
import statistics
from collections import Counter, defaultdict
from typing import Any

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ART = os.path.join(SCRIPT_DIR, "_artefacts")
JUDGMENTS_DIR = os.path.join(ART, "judgments")
MECHANICAL_DIR = os.path.join(ART, "mechanical")


def load_all_judgments() -> list[dict]:
    out = []
    if not os.path.exists(JUDGMENTS_DIR):
        return out
    for fname in sorted(os.listdir(JUDGMENTS_DIR)):
        if not fname.endswith(".json"):
            continue
        with open(os.path.join(JUDGMENTS_DIR, fname)) as f:
            out.append(json.load(f))
    return out


def load_all_mechanical() -> list[dict]:
    out = []
    if not os.path.exists(MECHANICAL_DIR):
        return out
    for fname in sorted(os.listdir(MECHANICAL_DIR)):
        if not fname.endswith(".json"):
            continue
        with open(os.path.join(MECHANICAL_DIR, fname)) as f:
            out.append(json.load(f))
    return out


def compute_sfr(judgment: dict) -> dict[str, float | int]:
    """Compute Observed-SFR and Required-Adherence from a judgment."""
    verdicts = judgment.get("verdicts", [])
    counts = Counter(v.get("verdict") for v in verdicts)
    F = counts.get("FOLLOWED", 0)
    VC = counts.get("VIOLATED_COMMISSION", 0)
    VO = counts.get("VIOLATED_OMISSION", 0)
    RBU = counts.get("REQUIRED_BUT_UNOBSERVED", 0)
    NA = counts.get("NOT_APPLICABLE", 0)
    IE = counts.get("INSUFFICIENT_EVIDENCE", 0)

    obs_denom = F + VC + VO
    obs_sfr = F / obs_denom if obs_denom > 0 else None
    req_denom = F + VC + VO + RBU
    req_adh = F / req_denom if req_denom > 0 else None

    return {
        "n_FOLLOWED": F,
        "n_VIOLATED_COMMISSION": VC,
        "n_VIOLATED_OMISSION": VO,
        "n_REQUIRED_BUT_UNOBSERVED": RBU,
        "n_NOT_APPLICABLE": NA,
        "n_INSUFFICIENT_EVIDENCE": IE,
        "observed_sfr": obs_sfr,
        "required_adherence": req_adh,
        "n_verdicts": len(verdicts),
    }


def build_cell_table() -> list[dict]:
    """Combined per-cell row with judgment + mechanical proxies + SFR."""
    judg_by_id = {j["_meta"]["cell_id"]: j for j in load_all_judgments() if "_meta" in j}
    mech_by_id = {m["_meta"]["cell_id"]: m for m in load_all_mechanical() if "_meta" in m}

    rows = []
    for cid, j in judg_by_id.items():
        meta = j["_meta"]
        sfr = compute_sfr(j)
        mech = mech_by_id.get(cid, {})
        phase = j.get("phase_adherence", {})
        row = {
            "cell_id": cid,
            "solver": meta["solver"],
            "evolver": meta["evolver"],
            "task": meta["task"],
            "skill": meta["skill"],
            "tier": meta["tier"],
            "passed": meta.get("passed"),
            "score": meta.get("score"),
            "trajectory_turns": meta.get("trajectory_turns"),
            **sfr,
            # phase adherence
            "phase_skill_loaded": phase.get("skill_loaded"),
            "phase_first_action": phase.get("first_action"),
            "phase_midpoint": phase.get("midpoint"),
            "phase_pre_final": phase.get("pre_final"),
            "phase_final_validation": phase.get("final_validation"),
            # mechanical
            "trajectory_total_tokens": mech.get("trajectory_total_tokens"),
            "tool_filename_overlap_rate": mech.get("tool_filename_overlap_rate"),
            "retrieval_to_use_gap_tokens": mech.get("retrieval_to_use_gap_tokens"),
            "retrieval_to_use_gap_turns": mech.get("retrieval_to_use_gap_turns"),
            "ordered_milestone_completion": mech.get("ordered_milestone_completion"),
            "forbidden_identifier_mention": mech.get("forbidden_identifier_mention", mech.get("forbidden_violations")),
            "early_termination_with_required_unfinished": mech.get("early_termination_with_required_unfinished"),
            "answer_before_validation": mech.get("answer_before_validation"),
            "mechanical_violation_count": mech.get("mechanical_violation_count"),
            "mechanical_violation_flag": mech.get("mechanical_violation_flag"),
        }
        rows.append(row)
    return rows


def write_csv(rows: list[dict], path: str):
    if not rows:
        return
    keys = list(rows[0].keys())
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def summarise_by(rows: list[dict], group_keys: list[str]) -> list[dict]:
    """Group rows by given keys; compute means/medians of numeric fields."""
    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        key = tuple(r.get(k) for k in group_keys)
        grouped[key].append(r)

    out = []
    for key, items in grouped.items():
        rec = dict(zip(group_keys, key))
        rec["n_cells"] = len(items)
        for fld in [
            "observed_sfr", "required_adherence",
            "tool_filename_overlap_rate",
            "retrieval_to_use_gap_tokens", "retrieval_to_use_gap_turns",
            "ordered_milestone_completion", "mechanical_violation_count",
            "trajectory_total_tokens", "trajectory_turns",
            "phase_skill_loaded", "phase_first_action", "phase_midpoint",
            "phase_pre_final", "phase_final_validation",
        ]:
            vals = [r[fld] for r in items if r.get(fld) is not None]
            if vals:
                rec[f"mean_{fld}"] = round(statistics.mean(vals), 4)
                if len(vals) > 1:
                    rec[f"std_{fld}"] = round(statistics.stdev(vals), 4)
        # Pass rate
        passes = [r.get("passed") for r in items if r.get("passed") is not None]
        if passes:
            rec["pass_rate"] = round(sum(1 for p in passes if p) / len(passes), 4)
        out.append(rec)
    return out


def paired_delta(rows: list[dict]) -> dict:
    """Compute paired delta: per (task, skill, evolver), mean strong-tier SFR
    minus mean midweak-tier SFR. Bootstrap 10k for 95% CI.

    Per Codex r3 fix #1: only include triples that have BOTH at least one
    strong-tier cell AND at least one midweak-tier cell. Triples with only
    one tier (which can creep in if rows is not pre-filtered to the matched
    set) are excluded from the delta computation."""
    # Group by triple
    by_triple: dict[tuple, dict[str, list[dict]]] = defaultdict(lambda: {"strong": [], "midweak": []})
    for r in rows:
        key = (r["task"], r["skill"], r["evolver"])
        if r["tier"] in ("strong", "midweak"):
            by_triple[key][r["tier"]].append(r)
    # Audit: count triples by membership pattern
    n_strong_only = sum(1 for t in by_triple.values() if t["strong"] and not t["midweak"])
    n_midweak_only = sum(1 for t in by_triple.values() if t["midweak"] and not t["strong"])
    n_both = sum(1 for t in by_triple.values() if t["strong"] and t["midweak"])
    print(f"[paired_delta] triples: both_tiers={n_both} strong_only={n_strong_only} midweak_only={n_midweak_only} (only 'both' contribute to delta)")

    deltas_required = []
    deltas_observed = []
    for key, tiers in by_triple.items():
        if not tiers["strong"] or not tiers["midweak"]:
            continue
        s_req = [c["required_adherence"] for c in tiers["strong"] if c["required_adherence"] is not None]
        m_req = [c["required_adherence"] for c in tiers["midweak"] if c["required_adherence"] is not None]
        if s_req and m_req:
            deltas_required.append(statistics.mean(s_req) - statistics.mean(m_req))
        s_obs = [c["observed_sfr"] for c in tiers["strong"] if c["observed_sfr"] is not None]
        m_obs = [c["observed_sfr"] for c in tiers["midweak"] if c["observed_sfr"] is not None]
        if s_obs and m_obs:
            deltas_observed.append(statistics.mean(s_obs) - statistics.mean(m_obs))

    def _bootstrap(deltas: list[float], n_iter: int = 10000) -> tuple[float, float, float]:
        if not deltas:
            return (float("nan"), float("nan"), float("nan"))
        rng = random.Random(42)
        means = []
        n = len(deltas)
        for _ in range(n_iter):
            sample = [deltas[rng.randint(0, n - 1)] for _ in range(n)]
            means.append(statistics.mean(sample))
        means.sort()
        lo = means[int(n_iter * 0.025)]
        hi = means[int(n_iter * 0.975)]
        return statistics.mean(deltas), lo, hi

    req_mean, req_lo, req_hi = _bootstrap(deltas_required)
    obs_mean, obs_lo, obs_hi = _bootstrap(deltas_observed)

    return {
        "n_triples_required": len(deltas_required),
        "n_triples_observed": len(deltas_observed),
        "required_adherence_paired_delta_mean": round(req_mean, 4),
        "required_adherence_paired_delta_95ci": [round(req_lo, 4), round(req_hi, 4)],
        "observed_sfr_paired_delta_mean": round(obs_mean, 4),
        "observed_sfr_paired_delta_95ci": [round(obs_lo, 4), round(obs_hi, 4)],
    }


def correlation(rows: list[dict], x_key: str, y_key: str) -> float | None:
    pairs = [(r[x_key], r[y_key]) for r in rows
             if r.get(x_key) is not None and r.get(y_key) is not None
             and not (isinstance(r[x_key], float) and math.isnan(r[x_key]))
             and not (isinstance(r[y_key], float) and math.isnan(r[y_key]))]
    if len(pairs) < 3:
        return None
    xs, ys = zip(*pairs)
    # Spearman rank correlation
    def _rank(vals):
        sorted_v = sorted(enumerate(vals), key=lambda x: x[1])
        ranks = [0.0] * len(vals)
        for new_rank, (orig_idx, _) in enumerate(sorted_v):
            ranks[orig_idx] = new_rank
        return ranks
    rx = _rank(xs)
    ry = _rank(ys)
    n = len(rx)
    mean_x = sum(rx) / n
    mean_y = sum(ry) / n
    num = sum((rx[i] - mean_x) * (ry[i] - mean_y) for i in range(n))
    den_x = math.sqrt(sum((rx[i] - mean_x) ** 2 for i in range(n)))
    den_y = math.sqrt(sum((ry[i] - mean_y) ** 2 for i in range(n)))
    if den_x == 0 or den_y == 0:
        return None
    return num / (den_x * den_y)


def write_report(rows: list[dict], out_path: str):
    """Compose the report.md with all tables + stats."""
    by_solver = summarise_by(rows, ["solver"])
    by_tier_pass = summarise_by(rows, ["tier", "passed"])
    by_tier = summarise_by(rows, ["tier"])

    delta = paired_delta(rows)

    # Correlations: required_adherence vs each mechanical proxy
    correls = {
        "required_adherence_vs_tool_filename_overlap": correlation(rows, "required_adherence", "tool_filename_overlap_rate"),
        "required_adherence_vs_ordered_milestone": correlation(rows, "required_adherence", "ordered_milestone_completion"),
        "required_adherence_vs_retrieval_gap": correlation(rows, "required_adherence", "retrieval_to_use_gap_tokens"),
        "required_adherence_vs_mechanical_violations": correlation(rows, "required_adherence", "mechanical_violation_count"),
        "observed_sfr_vs_required_adherence": correlation(rows, "observed_sfr", "required_adherence"),
    }

    n_total = len(rows)
    n_pass = sum(1 for r in rows if r.get("passed"))

    lines = []
    lines.append("# SFR Pipeline Report")
    lines.append("")
    lines.append(f"Generated from {n_total} matched SkillBench cells (Sonnet 4.6 judge, Bedrock).")
    lines.append("")
    lines.append("## Primary result: paired Required-Adherence delta (strong − midweak)")
    lines.append("")
    lines.append(f"- n_triples (with both tiers, required-adherence defined): {delta['n_triples_required']}")
    lines.append(f"- mean delta (Required-Adherence): **{delta['required_adherence_paired_delta_mean']:+.4f}**")
    lines.append(f"- 95% CI (bootstrap, 10k): [{delta['required_adherence_paired_delta_95ci'][0]:+.4f}, {delta['required_adherence_paired_delta_95ci'][1]:+.4f}]")
    lines.append("")
    lines.append("## Sensitivity: paired Observed-SFR delta (strong − midweak)")
    lines.append("")
    lines.append(f"- n_triples: {delta['n_triples_observed']}")
    lines.append(f"- mean delta: **{delta['observed_sfr_paired_delta_mean']:+.4f}**")
    lines.append(f"- 95% CI: [{delta['observed_sfr_paired_delta_95ci'][0]:+.4f}, {delta['observed_sfr_paired_delta_95ci'][1]:+.4f}]")
    lines.append("")
    lines.append("## Per-solver summary (means)")
    lines.append("")
    lines.append("| Solver | n_cells | pass_rate | Required-Adherence | Observed-SFR | overlap | milestone | retrieval_gap_tok | mech_viol | traj_tokens |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for rec in sorted(by_solver, key=lambda x: x.get("solver", "")):
        lines.append(
            f"| {rec.get('solver')} | {rec['n_cells']} | {rec.get('pass_rate','?')} | "
            f"{rec.get('mean_required_adherence','?')} | {rec.get('mean_observed_sfr','?')} | "
            f"{rec.get('mean_tool_filename_overlap_rate','?')} | {rec.get('mean_ordered_milestone_completion','?')} | "
            f"{rec.get('mean_retrieval_to_use_gap_tokens','?')} | {rec.get('mean_mechanical_violation_count','?')} | "
            f"{rec.get('mean_trajectory_total_tokens','?')} |"
        )
    lines.append("")
    lines.append("## Per-tier × outcome (key 2×2)")
    lines.append("")
    lines.append("| Tier | Passed | n_cells | Required-Adherence | Observed-SFR | traj_tokens | mech_viol |")
    lines.append("|---|---|---:|---:|---:|---:|---:|")
    for rec in sorted(by_tier_pass, key=lambda x: (x.get("tier",""), str(x.get("passed","")))):
        lines.append(
            f"| {rec.get('tier')} | {rec.get('passed')} | {rec['n_cells']} | "
            f"{rec.get('mean_required_adherence','?')} | {rec.get('mean_observed_sfr','?')} | "
            f"{rec.get('mean_trajectory_total_tokens','?')} | {rec.get('mean_mechanical_violation_count','?')} |"
        )
    lines.append("")
    lines.append("## Phase-adherence drift (Stage 6, exploratory)")
    lines.append("")
    lines.append("| Tier | n | skill_loaded | first_action | midpoint | pre_final | final_validation |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for rec in sorted(by_tier, key=lambda x: x.get("tier","")):
        lines.append(
            f"| {rec.get('tier')} | {rec['n_cells']} | "
            f"{rec.get('mean_phase_skill_loaded','?')} | {rec.get('mean_phase_first_action','?')} | "
            f"{rec.get('mean_phase_midpoint','?')} | {rec.get('mean_phase_pre_final','?')} | "
            f"{rec.get('mean_phase_final_validation','?')} |"
        )
    lines.append("")
    lines.append("## Spearman ρ correlations (reliability cross-validation)")
    lines.append("")
    for k, v in correls.items():
        v_str = f"{v:+.4f}" if v is not None else "n/a"
        lines.append(f"- {k}: {v_str}")
    lines.append("")
    lines.append("## Denominator audit (per tier counts)")
    lines.append("")
    lines.append("| Tier | n_cells | FOLLOWED | V_COMMISSION | V_OMISSION | REQUIRED_BUT_UNOBSERVED | NOT_APPLICABLE | INSUFFICIENT_EVIDENCE |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for tier in ["strong", "midweak"]:
        tier_rows = [r for r in rows if r["tier"] == tier]
        if not tier_rows:
            continue
        n = len(tier_rows)
        lines.append(
            f"| {tier} | {n} | "
            f"{sum(r['n_FOLLOWED'] for r in tier_rows)} | "
            f"{sum(r['n_VIOLATED_COMMISSION'] for r in tier_rows)} | "
            f"{sum(r['n_VIOLATED_OMISSION'] for r in tier_rows)} | "
            f"{sum(r['n_REQUIRED_BUT_UNOBSERVED'] for r in tier_rows)} | "
            f"{sum(r['n_NOT_APPLICABLE'] for r in tier_rows)} | "
            f"{sum(r['n_INSUFFICIENT_EVIDENCE'] for r in tier_rows)} |"
        )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("Raw per-cell data in `_artefacts/aggregate.csv`.")

    with open(out_path, "w") as f:
        f.write("\n".join(lines))


def main():
    rows = build_cell_table()
    print(f"loaded {len(rows)} cell rows")
    write_csv(rows, os.path.join(ART, "aggregate.csv"))
    write_report(rows, os.path.join(ART, "report.md"))
    print(f"wrote {ART}/aggregate.csv and {ART}/report.md")


if __name__ == "__main__":
    main()
