#!/usr/bin/env python3
"""Combined 1x2 overview figure for the README.

Left  : the learning curve (per-batch accuracy as the harness evolves).
Right : the Return / accuracy frontier across all 11 methods.

Both panels are drawn by the ``draw()`` functions in plot_learning_curve.py
and plot_pareto_frontier.py, so this file only handles layout — there is no
duplicated plotting logic, and the standalone single-panel scripts stay in
sync automatically.

The figure is sized wide-and-short (so it does not run long in a README) while
keeping fonts large enough to read inline on GitHub.

Usage
-----
    python evaluations/analysis_poly/plot_overview.py \
        --results-root results --db data/polymarket_analysis.db
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _polybench_data import load_market_index            # noqa: E402
import plot_learning_curve as lc                          # noqa: E402
import plot_pareto_frontier as pf                         # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent
# README-displayed figure lives in the repo's top-level assets/ folder.
ASSETS_DIR = SCRIPT_DIR.parent.parent / "assets"
OUT_PATH = ASSETS_DIR / "polybench_overview.png"


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
    # Two panels side by side; height bumped up so each panel is closer to
    # square (better L:H for a README; less stretched-out).
    fig, (ax_l, ax_r) = plt.subplots(1, 2, figsize=(12.4, 5.4), dpi=170)
    fig.patch.set_facecolor("white")

    FS = 12.5  # shared base font size across both panels
    lc.draw(ax_l, results_root, market, fs=FS)
    pf.draw(ax_r, results_root, market, fs=FS)

    fig.tight_layout(pad=0.6, w_pad=2.0)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_PATH, bbox_inches="tight", pad_inches=0.03,
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
    print(f"Building 1x2 overview from {args.results_root} ...")
    out = make_figure(args.results_root, args.db)
    if out:
        print(f"Wrote {out}")


if __name__ == "__main__":
    main()
