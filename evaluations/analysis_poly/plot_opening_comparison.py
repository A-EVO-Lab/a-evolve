#!/usr/bin/env python3
"""Opening figure for the README: PolyBench cumulative return over time.

Tells the headline story in one plot — the *same* solver under a range of
harnesses, all replayed on the *same* temporally-ordered PolyBench stream
(5,075 Polymarket prediction markets, $10 lot):

    No-evolution solvers : Sonnet / Haiku / Kimi / GLM (static harness)
    Naive evolution      : A-Evolve (single-agent)
    Adaptive Auto-Harness: ours (multi-agent evolution + solve-time routing)

The curve is the **cumulative net profit-and-loss** (equity curve) in dollars,
accumulated trade-by-trade in market-resolution order:

    PnL_t = sum(profit_1..t)

Unlike a confidence-weighted *ratio* (CWR), an equity curve only steps up or
down by each trade's realized profit, so it has no ratio-denominator spikes —
it reads directly as "how much money this harness made over the stream."

Per-trade profit comes from the *exact same* order-book fill simulator the
benchmark uses to score runs
(``agent_evolve.benchmarks.polybench.polybench``):

    investment = lot x confidence,  filled against the live order book;
    profit     = (shares x $1 - spent) if correct else -spent.

So the curve is consistent with the CWR / Return numbers in the paper (Return
= Coverage x CWR); we plot dollars here purely for a clean, spike-free visual.

This is a presentation script, kept separate from ``analyze_all.py`` (the full
diagnostic report). It only reads finished run artifacts + the dataset; it
never calls an LLM.

Usage
-----
    python evaluations/analysis_poly/plot_opening_comparison.py \
        --results-root results \
        --db data/polymarket_analysis.db

The figure is written to ``evaluations/analysis_poly/performance_over_time.png``
(next to this script) regardless of where it is invoked from.
"""
from __future__ import annotations

import argparse
import glob
import json
import sqlite3
from datetime import datetime
from pathlib import Path

# Reuse the benchmark's authoritative trade simulation so the plotted profit is
# identical to how runs are scored (no parallel re-implementation to drift).
from agent_evolve.benchmarks.polybench.polybench import (
    CONFIDENCE_GATE,
    LOT_SIZE,
    _fallback_price,
    _simulate_trade,
)

SCRIPT_DIR = Path(__file__).resolve().parent
OUT_PATH = SCRIPT_DIR / "performance_over_time.png"

# Each line: (result dir under --results-root, legend label, color, zorder).
# The no-evolution solver row is drawn in a muted family; A-Evolve (naive
# evolution) in blue; ours in red on top. Ordered so the legend reads as the
# narrative arc from weakest baseline to ours.
SERIES = [
    ("polybench_baseline_kimi", "Kimi (no evo)", "#c7c9cc", 3),
    ("polybench_baseline_glm-4.7", "GLM (no evo)", "#aab0b6", 3),
    ("polybench_baseline", "Sonnet (no evo)", "#7f8c99", 4),
    ("polybench_baseline_haiku", "Haiku (no evo)", "#5b6770", 4),
    ("polybench_full_evo", "A-Evolve (naive evolution)", "#1f77b4", 7),
    ("polybench_structured_nav",
     "Adaptive Auto-Harness (ours)", "#d62728", 9),
]


def load_market_index(db_path: Path) -> dict[str, dict]:
    """snapshot_id -> {order book, prices, winning outcome, resolved_at}.

    task_id is ``{event_id}_{market_id}_{snapshot_id}``; the prediction
    filename ends in the snapshot_id, which keys the snapshot row uniquely.
    """
    con = sqlite3.connect(str(db_path))
    rows = con.execute(
        """
        SELECT ms.id, ms.order_book_snapshot, m.outcome_prices,
               r.winning_outcome, r.resolved_at
        FROM market_snapshots ms
        JOIN markets m      ON ms.market_id = m.id
        JOIN resolutions r  ON ms.market_id = r.market_id
        WHERE ms.ready_for_analysis = 1
        """
    ).fetchall()
    con.close()
    index: dict[str, dict] = {}
    for sid, ob, prices, win, resolved_at in rows:
        index[str(sid)] = {
            "ob": ob or "",
            "prices": prices or "[]",
            "win": (win or "").strip().upper(),
            "resolved_at": resolved_at or "",
        }
    return index


def load_trades(results_dir: Path, market: dict[str, dict]) -> list[dict]:
    """Read prediction_*.json for one run, scoring each via the benchmark sim.

    Returns one record per task that maps to the dataset, carrying the
    per-trade realized profit (dollars) and the market resolution date used for
    temporal ordering. Gated / SKIP predictions contribute $0 profit but still
    occupy a point on the stream.
    """
    trades: list[dict] = []
    for f in glob.glob(str(results_dir / "prediction_*.json")):
        sid = Path(f).stem.split("_")[-1]
        rec = market.get(sid)
        if rec is None:
            continue
        try:
            pred = json.loads(Path(f).read_text())
        except (json.JSONDecodeError, OSError):
            continue

        decision = (pred.get("decision") or "").upper()
        side = (pred.get("side") or "").upper()
        confidence = float(pred.get("confidence", 0.5))

        profit = 0.0
        if confidence >= CONFIDENCE_GATE and decision != "SKIP" and side:
            is_correct = (
                (rec["win"] == side) if decision == "BUY"
                else (rec["win"] != side)
            )
            _, profit = _simulate_trade(
                rec["ob"], side, LOT_SIZE * confidence,
                _fallback_price(side, rec["prices"]), is_correct,
            )
        trades.append({
            "resolved_at": rec["resolved_at"],
            "profit": profit,
        })
    return trades


def _parse_dt(s: str) -> datetime | None:
    try:
        return datetime.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


def build_curve(trades: list[dict]):
    """Return (x_indices, cumulative_pnl, final_pnl) ordered by resolution."""
    trades = sorted(trades, key=lambda t: t["resolved_at"] or "")
    xs, pnl = [], []
    cum = 0.0
    for i, t in enumerate(trades):
        cum += t["profit"]
        xs.append(i)
        pnl.append(cum)
    return xs, pnl, (pnl[-1] if pnl else 0.0)


def _fmt_money(v: float) -> str:
    """Compact dollar label, e.g. +$133k / +$678 / -$0.8k."""
    if abs(v) >= 1000:
        return f"{'+' if v >= 0 else '-'}${abs(v) / 1000:.0f}k"
    return f"{'+' if v >= 0 else '-'}${abs(v):.0f}"


def make_figure(results_root: Path, db_path: Path) -> Path | None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    market = load_market_index(db_path)
    if not market:
        print("No market snapshots found in DB; aborting.")
        return None

    # ---- Build each curve + collect a shared resolution-date axis ----
    curves = []
    date_axis: list[datetime] = []
    for dirname, label, color, z in SERIES:
        rdir = results_root / dirname
        if not rdir.is_dir():
            print(f"  ! skipping {dirname} (not found under {results_root})")
            continue
        trades = load_trades(rdir, market)
        if not trades:
            print(f"  ! skipping {dirname} (no scorable predictions)")
            continue
        xs, pnl, final = build_curve(trades)
        is_ours = dirname == "polybench_structured_nav"
        curves.append({
            "label": label, "color": color, "z": z, "xs": xs, "pnl": pnl,
            "final": final, "n": len(trades), "is_ours": is_ours,
        })
        # The fullest-coverage run defines the date tick positions.
        dts = [d for d in (_parse_dt(t["resolved_at"])
                           for t in sorted(trades, key=lambda t: t["resolved_at"] or ""))
               if d is not None]
        if len(dts) > len(date_axis):
            date_axis = dts

    if not curves:
        print("No curves to plot.")
        return None

    # ---- Draw ----
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "axes.edgecolor": "#444444",
        "axes.linewidth": 0.9,
    })
    fig, ax = plt.subplots(figsize=(11, 5.4), dpi=160)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("#fafafa")

    max_x = max((c["xs"][-1] for c in curves if c["xs"]), default=1)
    y_hi = max(c["final"] for c in curves)
    y_span = (y_hi - min(c["final"] for c in curves)) or 1.0

    for c in curves:
        lw = 3.0 if c["is_ours"] else (2.2 if c["color"] == "#1f77b4" else 1.6)
        ax.step(c["xs"], c["pnl"], where="post", color=c["color"], lw=lw,
                zorder=c["z"], alpha=0.95, solid_capstyle="round",
                label=c["label"])
        if c["xs"]:
            ax.plot(c["xs"][-1], c["final"], "o", color=c["color"],
                    ms=7 if c["is_ours"] else 5, zorder=c["z"] + 1,
                    markeredgecolor="white", markeredgewidth=1.1)

    ax.axhline(0, color="#333333", lw=1.0, zorder=2)
    ax.set_xlim(-max_x * 0.01, max_x * 1.12)

    # ---- Endpoint $ labels with greedy vertical de-collision ----
    # The weak baselines all finish near $0; place labels at their true value
    # then push any that overlap downward by a fixed slot so all stay legible.
    ax.relim(); ax.autoscale_view()
    lo, hi = ax.get_ylim()
    slot = 0.052 * (hi - lo)               # min vertical spacing between labels
    placed: list[float] = []
    for c in sorted(curves, key=lambda c: c["final"], reverse=True):
        y = c["final"]
        while any(abs(y - p) < slot for p in placed):
            y -= slot                       # nudge down into a free slot
        placed.append(y)
        ax.annotate(
            f" {_fmt_money(c['final'])}", (c["xs"][-1], c["final"]),
            xytext=(c["xs"][-1] + max_x * 0.015, y),
            color=c["color"], fontsize=11 if c["is_ours"] else 9.5,
            fontweight="bold" if c["is_ours"] else "normal",
            va="center", ha="left", zorder=30,
        )

    ax.set_xlabel("PolyBench task index  (ordered by market resolution date)",
                  fontsize=11.5)
    ax.set_ylabel("Cumulative net profit / loss  (USD, $10 lot)", fontsize=11.5)
    ax.yaxis.set_major_formatter(FuncFormatter(
        lambda v, _: f"${v/1000:.0f}k" if abs(v) >= 1000 else f"${v:.0f}"))
    ax.grid(True, axis="y", alpha=0.35, ls="--", lw=0.7)
    ax.grid(False, axis="x")
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)

    # Date ticks on a secondary top axis.
    if date_axis:
        ax2 = ax.twiny()
        ax2.set_xlim(ax.get_xlim())
        n_ticks = 6
        idxs = [int(i * (len(date_axis) - 1) / (n_ticks - 1))
                for i in range(n_ticks)]
        ax2.set_xticks(idxs)
        ax2.set_xticklabels([date_axis[i].strftime("%b %d") for i in idxs],
                            fontsize=8.5, color="#666666")
        ax2.tick_params(length=0)
        for spine in ("top", "right", "left", "bottom"):
            ax2.spines[spine].set_visible(False)

    # Legend ordered ours-first for emphasis.
    handles, labels = ax.get_legend_handles_labels()
    order = sorted(range(len(labels)),
                   key=lambda i: (0 if "ours" in labels[i] else 1, i))
    leg = ax.legend([handles[i] for i in order], [labels[i] for i in order],
                    loc="upper left", fontsize=10, frameon=True,
                    framealpha=0.95, edgecolor="#dddddd", handlelength=1.6,
                    borderpad=0.7, labelspacing=0.4)
    leg.set_zorder(20)

    ax.set_title(
        "Adaptive Auto-Harness on PolyBench — cumulative trading P&L over "
        "5,075 prediction markets",
        fontsize=12.5, fontweight="bold", pad=10,
    )

    fig.tight_layout()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_PATH, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return OUT_PATH


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results-root", type=Path, default=Path("results"),
                    help="Directory holding the polybench_* run folders.")
    ap.add_argument("--db", type=Path,
                    default=Path("data/polymarket_analysis.db"),
                    help="PolyBench SQLite dataset.")
    args = ap.parse_args()

    if not args.db.exists():
        raise SystemExit(f"Dataset not found: {args.db}")

    print(f"Building opening figure from {args.results_root} ...")
    out = make_figure(args.results_root, args.db)
    if out:
        print(f"Wrote {out}")


if __name__ == "__main__":
    main()
