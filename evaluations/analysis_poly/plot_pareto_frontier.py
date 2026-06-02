#!/usr/bin/env python3
"""Opening figure B — the Return/accuracy frontier.

One dot per method on the *same* PolyBench stream: x = Return (the paper's
coverage-scaled CWR, the headline trading metric), y = Accuracy (fraction of
ALL markets traded correctly). Dot area encodes Coverage (fraction of the
stream actually traded).

This is the honest "why we win" figure. It includes the strong auto-harness
competitors (Meta-Harness, SkillOS, OctoTools, ...), not just the base
solvers. Most baselines bunch near zero Return; a couple (Haiku, SkillOS)
earn high Return only on a thin, lucky slice — visible as *small* dots
(low coverage) stranded at modest accuracy. Adaptive Auto-Harness sits alone
at the top-right with the *largest* dot: highest Return, highest accuracy,
and near-full coverage at once.

Usage
-----
    python evaluations/analysis_poly/plot_pareto_frontier.py \
        --results-root results --db data/polymarket_analysis.db
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _polybench_data import load_market_index, load_trades, summarize  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent
OUT_PATH = SCRIPT_DIR / "pareto_frontier.png"

# (result dir, label, group) — group drives color/legend.
#   solver = no-evolution base agents; auto = auto-harness baselines;
#   human  = human-designed system;    ours = us.
METHODS = [
    ("polybench_baseline", "Sonnet", "solver"),
    ("polybench_baseline_haiku", "Haiku", "solver"),
    ("polybench_baseline_kimi", "Kimi", "solver"),
    ("polybench_baseline_glm-4.7", "GLM", "solver"),
    ("polybench_full_evo", "A-Evolve", "auto"),
    ("polybench_gepa_lite", "GEPA", "auto"),
    ("polybench_mh_lite", "Meta-Harness", "auto"),
    ("polybench_continual_harness", "Cont.Harness", "auto"),
    ("polybench_skillos", "SkillOS", "auto"),
    ("polybench_octo_expert", "OctoTools", "human"),
    ("polybench_structured_nav", "Adaptive Auto-Harness (ours)", "ours"),
]

GROUP_STYLE = {
    "solver": {"color": "#9aa0a6", "label": "No-evolution solver"},
    "auto":   {"color": "#1f77b4", "label": "Auto-harness baseline"},
    "human":  {"color": "#2ca02c", "label": "Human-designed (OctoTools)"},
    "ours":   {"color": "#d62728", "label": "Adaptive Auto-Harness (ours)"},
}


def _collect(results_root: Path, market: dict) -> list[dict]:
    pts = []
    for dirname, label, group in METHODS:
        rdir = results_root / dirname
        if not rdir.is_dir():
            print(f"  ! skipping {dirname} (not found)")
            continue
        s = summarize(load_trades(rdir, market))
        pts.append({
            "label": label, "group": group,
            "x": s["return"], "y": 100 * s["accuracy"],
            "cov": 100 * s["coverage"],
        })
    return pts


def draw(ax, results_root: Path, market: dict, *, fs: float = 12.0) -> None:
    """Paint the Return/accuracy frontier onto ``ax``. ``fs`` = base font."""
    from matplotlib.lines import Line2D

    pts = _collect(results_root, market)
    if not pts:
        return
    ax.set_facecolor("#fafafa")

    # Dot area ~ Coverage (fraction of the stream actually traded).
    def area(cov):
        return 90 + 13.0 * cov   # px^2; 0% -> 90, 100% -> ~1390

    # Manual label placement (offset in points) with thin leader lines for the
    # labels we keep. The tightest near-zero-Return dots (GLM/GEPA/Kimi/
    # Cont.Harness) are left unlabelled and called out as a single group.
    LABEL_OFFSET = {
        "Sonnet":       (10, 20),
        "A-Evolve":     (12, 2),
        "Haiku":        (12, -16),
        "SkillOS":      (-12, 12),
        "OctoTools":    (12, 8),
        # Meta-Harness dot is large and sits near the right edge; pull its
        # label well to the upper-left (with a leader line) into open space.
        "Meta-Harness": (-62, 30),
    }
    NO_LABEL = {"GLM", "GEPA", "Kimi", "Cont.Harness"}
    for p in pts:
        st = GROUP_STYLE[p["group"]]
        is_ours = p["group"] == "ours"
        ax.scatter(p["x"], p["y"], s=area(p["cov"]), color=st["color"],
                   alpha=0.85 if is_ours else 0.72, zorder=8 if is_ours else 5,
                   edgecolors="white", linewidths=1.5)
        if p["label"] in NO_LABEL:
            continue
        if is_ours:
            dx, dy, ha = -16, 2, "right"
            arrow = None
        else:
            dx, dy = LABEL_OFFSET.get(p["label"], (11, 8))
            ha = "right" if dx < 0 else "left"
            arrow = dict(arrowstyle="-", color="#bbbbbb", lw=0.8,
                         shrinkA=0, shrinkB=4)
        # On the dot, ours shows a short "Ours" tag (full name is in the
        # legend); other methods show their name.
        text = "Ours" if is_ours else p["label"]
        ax.annotate(
            text, (p["x"], p["y"]), xytext=(dx, dy),
            textcoords="offset points", fontsize=fs + 1.5 if is_ours else fs,
            fontweight="bold" if is_ours else "normal",
            color=st["color"] if is_ours else "#222222", ha=ha, va="center",
            zorder=12, arrowprops=arrow)

    # Single grouped callout for the unlabelled low-return cluster.
    cluster = [p for p in pts if p["label"] in NO_LABEL]
    if cluster:
        cx = sum(p["x"] for p in cluster) / len(cluster)
        cy = sum(p["y"] for p in cluster) / len(cluster)
        ax.annotate(
            "GLM · GEPA · Kimi · Cont.Harness",
            xy=(cx, cy - 1.5), xytext=(26, -32), textcoords="offset points",
            fontsize=fs - 1, color="#666666", ha="left", va="center",
            arrowprops=dict(arrowstyle="-", color="#bbbbbb", lw=0.8,
                            shrinkA=0, shrinkB=2))

    ax.set_xlabel("Return  —  coverage-scaled CWR", fontsize=fs)
    ax.set_ylabel("Accuracy  —  % of ALL markets correct", fontsize=fs)
    ax.tick_params(labelsize=fs - 1.5)
    ax.grid(True, alpha=0.3, ls="--", lw=0.7)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    xmax = max(p["x"] for p in pts)
    ymax = max(p["y"] for p in pts)
    # Extra top headroom so the family legend gets a clear band above the
    # data points (the highest non-ours dot, Meta-Harness, is ~51% accuracy).
    ax.set_xlim(-0.03 * xmax, xmax * 1.10)
    ax.set_ylim(0, ymax * 1.30)
    ax.margins(0)

    ax.annotate("better\n(higher return, traded right)",
                xy=(0.985, 0.985), xycoords="axes fraction",
                ha="right", va="top", fontsize=fs - 1, color="#888888",
                style="italic")

    # Two legends placed in genuinely empty regions: method family in the
    # upper-middle clear band (above Meta-Harness, left of Ours), dot-size key
    # in the mid-right gap (above the Meta-Harness label, below the band).
    group_handles = [
        Line2D([0], [0], marker="o", ls="", markersize=10,
               markerfacecolor=GROUP_STYLE[g]["color"],
               markeredgecolor="white", label=GROUP_STYLE[g]["label"])
        for g in ("solver", "auto", "human", "ours")
        if any(p["group"] == g for p in pts)
    ]
    leg1 = ax.legend(handles=group_handles, loc="upper left",
                     bbox_to_anchor=(0.02, 1.0), fontsize=fs - 1.5,
                     frameon=True, framealpha=0.95, edgecolor="#dddddd",
                     title="Method family", title_fontsize=fs - 1.5,
                     labelspacing=0.35)
    leg1.set_zorder(20)
    ax.add_artist(leg1)
    size_handles = [
        Line2D([0], [0], marker="o", ls="", markerfacecolor="#bbbbbb",
               markeredgecolor="white",
               markersize=(area(c) ** 0.5) / 2.0, label=f"{c:.0f}% coverage")
        for c in (25, 100)
    ]
    # Lower-right corner — clear of all dots and the Meta-Harness label.
    leg2 = ax.legend(handles=size_handles, loc="lower right",
                     bbox_to_anchor=(1.0, 0.0), fontsize=fs - 2, frameon=True,
                     framealpha=0.95, edgecolor="#dddddd",
                     title="dot size = coverage", title_fontsize=fs - 2,
                     labelspacing=1.6, borderpad=0.9)
    leg2.set_zorder(20)


def make_figure(results_root: Path, db_path: Path) -> Path | None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    market = load_market_index(db_path)
    if not market:
        print("No market snapshots found; aborting.")
        return None

    plt.rcParams.update({"font.family": "DejaVu Sans",
                         "axes.edgecolor": "#444444", "axes.linewidth": 0.9})
    fig, ax = plt.subplots(figsize=(7.8, 4.6), dpi=170)
    fig.patch.set_facecolor("white")
    draw(ax, results_root, market, fs=12.0)
    fig.tight_layout(pad=0.3)
    fig.savefig(OUT_PATH, bbox_inches="tight", pad_inches=0.02,
                facecolor="white")
    plt.close(fig)
    return OUT_PATH


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results-root", type=Path, default=Path("results"))
    ap.add_argument("--db", type=Path, default=Path("data/polymarket_analysis.db"))
    args = ap.parse_args()
    if not args.db.exists():
        raise SystemExit(f"Dataset not found: {args.db}")
    print(f"Building Pareto frontier from {args.results_root} ...")
    out = make_figure(args.results_root, args.db)
    if out:
        print(f"Wrote {out}")


if __name__ == "__main__":
    main()
