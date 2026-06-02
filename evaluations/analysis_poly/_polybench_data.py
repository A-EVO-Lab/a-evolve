"""Shared PolyBench result loader for the README figures.

Centralizes the one thing every opening figure needs: turn a run's
``prediction_*.json`` files into scored trades, using the *exact* order-book
fill simulator the benchmark itself uses
(``agent_evolve.benchmarks.polybench.polybench``) so every figure is
consistent with each other and with the paper's numbers.

A "trade" record carries everything the figures slice on:
    snapshot_id, batch_num, resolved_at, traded (bool), correct (bool),
    investment ($), profit ($).

Gated / SKIP predictions are kept as records with ``traded=False`` and
$0 investment/profit so coverage (= traded / seen) is well-defined.

No LLM calls; reads finished artifacts + the SQLite dataset only.
"""
from __future__ import annotations

import glob
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from agent_evolve.benchmarks.polybench.polybench import (
    CONFIDENCE_GATE,
    LOT_SIZE,
    _fallback_price,
    _simulate_trade,
)


@dataclass
class Trade:
    snapshot_id: str
    batch_num: int | None
    resolved_at: str
    traded: bool
    correct: bool
    investment: float
    profit: float


def load_market_index(db_path: Path) -> dict[str, dict]:
    """snapshot_id -> {order book, prices, winning outcome, resolved_at}."""
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
    return {
        str(sid): {"ob": ob or "", "prices": prices or "[]",
                   "win": (win or "").strip().upper(),
                   "resolved_at": resolved_at or ""}
        for sid, ob, prices, win, resolved_at in rows
    }


def _batch_by_snapshot(results_dir: Path) -> dict[str, int]:
    """snapshot_id (3rd segment of instance_id) -> batch_num, from results.jsonl."""
    out: dict[str, int] = {}
    path = results_dir / "results.jsonl"
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        iid = r.get("instance_id", "")
        if iid:
            out[iid.split("_")[-1]] = r.get("batch_num")
    return out


def load_trades(results_dir: Path, market: dict[str, dict]) -> list[Trade]:
    """Score every prediction in a run directory into Trade records."""
    batch = _batch_by_snapshot(results_dir)
    trades: list[Trade] = []
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
        conf = float(pred.get("confidence", 0.5))

        traded = conf >= CONFIDENCE_GATE and decision != "SKIP" and bool(side)
        correct = inv = profit = 0.0
        if traded:
            correct = (
                (rec["win"] == side) if decision == "BUY"
                else (rec["win"] != side)
            )
            inv, profit = _simulate_trade(
                rec["ob"], side, LOT_SIZE * conf,
                _fallback_price(side, rec["prices"]), bool(correct),
            )
        trades.append(Trade(
            snapshot_id=sid, batch_num=batch.get(sid),
            resolved_at=rec["resolved_at"], traded=bool(traded),
            correct=bool(correct), investment=inv, profit=profit,
        ))
    return trades


def summarize(trades: list[Trade]) -> dict:
    """Final headline metrics for one run (matches the paper's definitions)."""
    seen = len(trades)
    traded = [t for t in trades if t.traded]
    n_tr = len(traded)
    inv = sum(t.investment for t in traded)
    profit = sum(t.profit for t in traded)
    correct = sum(1 for t in traded if t.correct)
    coverage = n_tr / seen if seen else 0.0
    cwr = (100.0 * profit / inv) if inv > 0 else 0.0
    return {
        "seen": seen,
        "traded": n_tr,
        "coverage": coverage,                       # fraction of stream traded
        "accuracy": (correct / seen) if seen else 0.0,   # correct / ALL seen
        "trade_accuracy": (correct / n_tr) if n_tr else 0.0,  # win rate
        "cwr": cwr,                                 # profit / invested, %
        "return": coverage * cwr,                   # paper's coverage-scaled return
        "profit": profit,
        "invested": inv,
    }
