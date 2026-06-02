#!/usr/bin/env python3
"""Opening figure A — the learning curve (sustained self-improvement).

Plots per-evolution-cycle PolyBench **accuracy** (fraction of all markets in
the batch traded correctly) as the harness evolves across the temporally-
ordered stream. This is the paper's core thesis made visible: with the *same*
solver, all methods start at parity on batch 1 (an unevolved harness), then
the adaptive harness climbs and sustains near-ceiling accuracy, while
no-evolution stays flat and naive single-agent evolution actually *degrades*.

Accuracy (bounded in [0, 100]) is used rather than Return for this curve
because Return is a ratio dominated by occasional longshot wins, which makes a
per-batch Return curve spiky and hard to read; accuracy isolates the
capability trend cleanly. The headline Return numbers live in the equity-curve
and Pareto figures. A light rolling mean overlays the raw per-batch points.

Usage
-----
    python evaluations/analysis_poly/plot_learning_curve.py \
        --results-root results --db data/polymarket_analysis.db
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _polybench_data import load_market_index, load_trades  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent
OUT_PATH = SCRIPT_DIR / "learning_curve.png"

# (result dir, label, color, is_ours, emphasis)
# emphasis "hi" -> bold solid line + raw points; "mid" -> medium; "lo" -> thin
# muted line (the flat / degrading also-rans, shown for completeness).
SERIES = [
    ("polybench_structured_nav", "Adaptive Auto-Harness (ours)", "#d62728", True, "hi"),
    ("polybench_mh_lite", "Meta-Harness", "#ff7f0e", False, "hi"),
    ("polybench_octo_expert", "OctoTools", "#2ca02c", False, "mid"),
    ("polybench_full_evo", "A-Evolve", "#1f77b4", False, "mid"),
    ("polybench_baseline", "Sonnet (no evolution)", "#555f6b", False, "mid"),
    ("polybench_skillos", "SkillOS", "#9467bd", False, "lo"),
    ("polybench_baseline_haiku", "Haiku", "#8c8c8c", False, "lo"),
    ("polybench_baseline_kimi", "Kimi", "#aab0b6", False, "lo"),
    ("polybench_baseline_glm-4.7", "GLM", "#aab0b6", False, "lo"),
    ("polybench_gepa_lite", "GEPA", "#aab0b6", False, "lo"),
    ("polybench_continual_harness", "Cont.Harness", "#aab0b6", False, "lo"),
]


def per_batch_accuracy(trades) -> tuple[list[int], list[float]]:
    """Return (batch_numbers, accuracy_per_batch %) over ALL markets seen."""
    agg = defaultdict(lambda: [0, 0])  # correct, seen
    for t in trades:
        if t.batch_num is None:
            continue
        a = agg[t.batch_num]
        a[1] += 1
        if t.traded and t.correct:
            a[0] += 1
    xs, ys = [], []
    for b in sorted(agg):
        correct, seen = agg[b]
        xs.append(b)
        ys.append(100.0 * correct / seen if seen else 0.0)
    return xs, ys


def _rolling(ys: list[float], w: int = 5) -> list[float]:
    out = []
    for i in range(len(ys)):
        lo = max(0, i - w + 1)
        window = ys[lo:i + 1]
        out.append(sum(window) / len(window))
    return out


# Per-emphasis line styling. "lo" lines are muted and label-less in the
# legend (folded into one "other baselines" entry) to avoid clutter.
_STYLE = {
    "hi":  {"lw": 3.0, "alpha": 0.95, "z": 8, "pts": True},
    "mid": {"lw": 2.0, "alpha": 0.9, "z": 6, "pts": False},
    "lo":  {"lw": 1.1, "alpha": 0.5, "z": 4, "pts": False},
}


def draw(ax, results_root: Path, market: dict, *, fs: float = 12.0) -> None:
    """Paint the learning curve onto ``ax``. ``fs`` is the base font size."""
    ax.set_facecolor("#fafafa")
    plotted = []
    lo_drawn = False
    for dirname, label, color, is_ours, emph in SERIES:
        rdir = results_root / dirname
        if not rdir.is_dir():
            print(f"  ! skipping {dirname} (not found)")
            continue
        xs, ys = per_batch_accuracy(load_trades(rdir, market))
        if not xs:
            continue
        st = _STYLE[emph]
        smooth = _rolling(ys, 3)
        if st["pts"]:
            ax.plot(xs, ys, color=color, lw=0, marker="o", ms=2.5,
                    alpha=0.25, zorder=st["z"] - 1)
        # One legend entry for the whole muted "lo" group; the rest labelled.
        lbl = None
        if emph != "lo":
            lbl = label
        elif not lo_drawn:
            lbl = "other baselines"
            lo_drawn = True
        ax.plot(xs, smooth, color=color, lw=st["lw"], alpha=st["alpha"],
                zorder=st["z"], label=lbl, solid_capstyle="round")
        plotted.append((label, color, smooth[-1], xs[-1], is_ours, ys[0], emph))

    if not plotted:
        return

    ax.set_xlabel("Evolution cycle  (100-market batch, stream order)",
                  fontsize=fs)
    ax.set_ylabel("Accuracy per batch  (% of all markets correct)",
                  fontsize=fs)
    ax.tick_params(labelsize=fs - 1.5)
    # Curves top out near 100; reserve a clear band above for the legend (top
    # row) and the parity callout (just under it) so neither overlaps data.
    ax.set_ylim(0, 168)
    ax.set_yticks([0, 20, 40, 60, 80, 100])
    ax.grid(True, axis="y", alpha=0.35, ls="--", lw=0.7)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    x_hi = max(last_x for *_, last_x, _i, _s, _e in plotted)
    ax.set_xlim(0, x_hi * 1.13)  # headroom for the inline "Ours" label

    # "All start low" callout in the clear top band (left side), pointing down
    # to the shared batch-1 origin. It sits above all curves -> no overlap.
    starts = [s for (*_, s, _e) in plotted]
    if starts:
        y0 = sum(starts) / len(starts)
        ax.annotate(
            "All 11 methods start low\nbefore evolution (batch 1)",
            xy=(0.6, y0 + 1), xytext=(2.0, 123),
            textcoords="data", fontsize=fs - 2, color="#555555", ha="left",
            va="center",
            bbox=dict(boxstyle="round,pad=0.4", fc="white", ec="#cccccc",
                      lw=0.8, alpha=0.95),
            arrowprops=dict(arrowstyle="->", color="#888888", lw=1.1,
                            connectionstyle="arc3,rad=0.0"),
        )

    # Inline end-of-run label for ours only (no value).
    for label, color, final, last_x, is_ours, _start, emph in plotted:
        if is_ours:
            ax.annotate("Ours", xy=(last_x, final), xytext=(6, 0),
                        textcoords="offset points", color=color, fontsize=fs,
                        fontweight="bold", va="center", ha="left")

    # Legend in the clear top band, right side, single row.
    ax.legend(loc="upper right", bbox_to_anchor=(1.0, 1.0), ncol=2,
              fontsize=fs - 2.5, frameon=True, framealpha=0.96,
              edgecolor="#dddddd", handlelength=1.5, borderpad=0.5,
              columnspacing=1.2, labelspacing=0.3)


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
    fig, ax = plt.subplots(figsize=(8.4, 4.8), dpi=170)
    fig.patch.set_facecolor("white")
    draw(ax, results_root, market, fs=12.0)
    ax.set_title(
        "Sustained self-improvement on PolyBench",
        fontsize=13, fontweight="bold", pad=8)
    fig.tight_layout(pad=0.4)
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
    print(f"Building learning curve from {args.results_root} ...")
    out = make_figure(args.results_root, args.db)
    if out:
        print(f"Wrote {out}")


if __name__ == "__main__":
    main()
