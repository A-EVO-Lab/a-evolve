"""PolyBench prediction-market benchmark adapter.

Loads resolved Polymarket events from a PolyBench SQLite database and
evaluates agent predictions against actual outcomes.

Evaluation follows the official PolyBench evaluate_mimo.py methodology:
- Order-book fill simulation (VWAP against ask side)
- Non-CWR return: flat $lot_size investment per trade
- CWR return: investment = lot_size × confidence
- APY: raw_return × (365 / days_held)
- Accuracy, F1, ECE, Sharpe computed at analysis time
- Confidence gate at 0.6 (below = treated as SKIP)

Reference: https://github.com/PolyBench/PolyBench/blob/main/evaluation/evaluate_mimo.py
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from ...types import Feedback, Task, Trajectory
from ..base import BenchmarkAdapter

logger = logging.getLogger(__name__)

CONFIDENCE_GATE = 0.6
LOT_SIZE = 10.0


class PolyBenchBenchmark(BenchmarkAdapter):
    """PolyBench prediction-market benchmark adapter.

    Loads resolved markets from a PolyBench SQLite database, sorted by
    snapshot timestamp for chronological evolution.
    """

    def __init__(
        self,
        db_path: str,
        shuffle: bool = False,
        holdout_ratio: float = 0.0,
    ):
        self.db_path = db_path
        self.shuffle = shuffle
        self.holdout_ratio = holdout_ratio
        self._cache: dict[str, list[dict]] = {}
        self._split_done = False
        # Ground truth kept OUT of Task.metadata so it never reaches the
        # solver, the observation JSONL, or the evolver. evaluate() reads
        # the answer from here, re-deriving by task_id from the DB when the
        # adapter is reconstructed in a solver worker process.
        self._ground_truth: dict[str, dict[str, str]] = {}

    def get_tasks(self, split: str = "test", limit: int = 0) -> list[Task]:
        rows = self._load_split(split)
        if limit > 0:
            rows = rows[:limit]
        tasks = []
        for row in rows:
            task_id = f"{row['event_id']}_{row['market_id']}_{row['snapshot_id']}"
            task_input = self._format_input(row)
            # Source-provided event taxonomy (Polymarket tags) is the
            # regime signal the navigation engine stratifies on. An event
            # carries an ordered tag list (e.g. ["Politics","Elections"]);
            # the first tag is the primary regime, the full set the domain.
            tag_list = _safe_json(row.get("event_tags", "[]"))
            if not isinstance(tag_list, list):
                tag_list = []
            category = tag_list[0] if tag_list else "unknown"
            domain = ",".join(str(t) for t in tag_list) if tag_list else "unknown"
            resolved = row.get("resolved_at") or ""
            year = str(resolved)[:4] if resolved else ""
            # Stash ground truth (answer key + resolution time) keyed by
            # task_id — deliberately NOT placed in Task.metadata.
            self._ground_truth[task_id] = {
                "winning_outcome": row["winning_outcome"],
                "resolved_at": row["resolved_at"],
            }
            tasks.append(Task(
                id=task_id,
                input=task_input,
                metadata={
                    "market_id": row["market_id"],
                    "event_id": row["event_id"],
                    "snapshot_id": row["snapshot_id"],
                    # Market state at snapshot time — legitimately visible to
                    # the solver (it must price/trade from these). NOT the
                    # answer: winning_outcome and raw resolved_at are withheld.
                    "outcomes": row["outcomes"],
                    "outcome_prices": row["outcome_prices"],
                    "order_book_snapshot": row["order_book_snapshot"],
                    "timestamp": row["timestamp"],
                    # Regime signal for navigation/branching:
                    "category": category,
                    "domain": domain,
                    "tags": tag_list,
                    "year": year,
                },
            ))
        return tasks

    def _resolve_ground_truth(self, task_id: str) -> dict[str, str]:
        """Return {winning_outcome, resolved_at} for a task.

        Uses the in-memory map populated by get_tasks; falls back to a
        direct DB lookup by (event_id, market_id, snapshot_id) so a freshly
        reconstructed adapter in a solver worker can still evaluate.
        """
        gt = self._ground_truth.get(task_id)
        if gt is not None:
            return gt
        # task_id == f"{event_id}_{market_id}_{snapshot_id}"; the winning
        # outcome is per-market in the resolutions table.
        parts = task_id.rsplit("_", 2)
        market_id = parts[1] if len(parts) == 3 else task_id
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            row = cur.execute(
                "SELECT winning_outcome, resolved_at FROM resolutions "
                "WHERE market_id = ?", (market_id,),
            ).fetchone()
        finally:
            conn.close()
        gt = {"winning_outcome": row[0], "resolved_at": row[1]} if row else \
             {"winning_outcome": "", "resolved_at": ""}
        self._ground_truth[task_id] = gt
        return gt

    def evaluate(self, task: Task, trajectory: Trajectory) -> Feedback:
        """Evaluate a prediction against the resolved outcome.

        Follows the official PolyBench evaluate_mimo.py methodology:
        1. Parse agent output → decision / side / confidence
        2. Gate on confidence < 0.6 → SKIP
        3. Simulate OB fill at two budget levels (unit and CWR)
        4. Compute profit = (shares × $1 − spent) if correct, else −spent
        5. Compute APY = raw_return × (365 / days_held)
        """
        output = trajectory.output.strip()
        metadata = task.metadata
        # Ground truth is sourced benchmark-side, never from task.metadata
        # (which is visible to the solver/evolver).
        gt = self._resolve_ground_truth(task.id)
        winning = gt["winning_outcome"]

        if not output:
            return self._skip_feedback(task.id, "empty_output")

        try:
            parsed = json.loads(output)
        except json.JSONDecodeError:
            parsed = self._parse_freeform(output)

        decision = parsed.get("decision", "").upper()
        side = parsed.get("side", "").upper()
        confidence = float(parsed.get("confidence", 0.5))

        # Confidence gate (official: predictions below c < 0.6 are discarded)
        if confidence < CONFIDENCE_GATE:
            return Feedback(
                success=False, score=0.0,
                detail=f"Confidence {confidence:.2f} below gate ({CONFIDENCE_GATE})",
                raw={"task_id": task.id, "decision": decision, "side": side,
                     "confidence": confidence, "gated": True},
            )

        if decision == "SKIP" or not side:
            return self._skip_feedback(task.id, "agent_skip", decision=decision,
                                       side=side, confidence=confidence)

        # Correctness: BUY side=YES wins if YES wins; SELL side=YES wins if YES loses
        win_norm = winning.strip().upper()
        is_correct = (win_norm == side) if decision == "BUY" else (win_norm != side)

        # --- Official PolyBench fill simulation ---
        ob_json = metadata.get("order_book_snapshot", "")
        prices_json = metadata.get("outcome_prices", "[]")
        fallback_price = _fallback_price(side, prices_json)

        unit_inv, unit_profit = _simulate_trade(
            ob_json, side, LOT_SIZE, fallback_price, is_correct,
        )
        cwr_inv, cwr_profit = _simulate_trade(
            ob_json, side, LOT_SIZE * confidence, fallback_price, is_correct,
        )

        # Raw return and APY (official: apy = raw_ret × 365 / days_held)
        raw_return = (unit_profit / unit_inv) if unit_inv > 0 else 0.0
        apy = _compute_apy(raw_return, metadata.get("timestamp"),
                           gt["resolved_at"])

        # Score = accuracy (1/0) for aggregation; CWR for ranking
        score = 1.0 if is_correct else 0.0

        return Feedback(
            success=is_correct,
            score=score,
            detail=(
                f"{'Correct' if is_correct else 'Wrong'}: {decision} {side} "
                f"(conf={confidence:.2f}), winning={winning}, "
                f"return={raw_return:+.1%}, cwr={cwr_profit:+.2f}"
            ),
            raw={
                "task_id": task.id,
                "decision": decision,
                "side": side,
                "confidence": confidence,
                "winning_outcome": winning,
                "is_correct": is_correct,
                "unit_investment": unit_inv,
                "unit_profit": unit_profit,
                "raw_return": raw_return,
                "cwr_investment": cwr_inv,
                "cwr_profit": cwr_profit,
                "apy": apy,
            },
        )

    @staticmethod
    def _skip_feedback(task_id: str, reason: str, **extra) -> Feedback:
        return Feedback(
            success=False, score=0.0,
            detail=f"{reason} for {task_id}",
            raw={"task_id": task_id, "reason": reason, **extra},
        )

    # -- Internals --------------------------------------------------------

    def _load_split(self, split: str) -> list[dict]:
        if not self._split_done:
            self._do_split()
        if split in self._cache:
            return self._cache[split]
        return self._cache.get("train", [])

    def _do_split(self) -> None:
        import random

        rows = self._load_from_db()

        if self.shuffle:
            random.shuffle(rows)
        else:
            # Sort by resolution date (meaningful temporal ordering) with
            # snapshot timestamp as tiebreaker.
            rows.sort(key=lambda r: (r.get("resolved_at", ""), r.get("timestamp", "")))

        n_holdout = 0 if self.holdout_ratio == 0.0 else max(1, int(len(rows) * self.holdout_ratio))
        self._cache["holdout"] = rows[:n_holdout]
        self._cache["train"] = rows[n_holdout:]
        self._cache["test"] = rows

        self._split_done = True
        logger.info(
            "Loaded %d tasks from %s (train=%d, holdout=%d)",
            len(rows), self.db_path,
            len(self._cache["train"]), len(self._cache["holdout"]),
        )

    def _load_from_db(self) -> list[dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        tables = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

        if "market_snapshots" in tables:
            c.execute("""
                SELECT
                    ms.id            AS snapshot_id,
                    ms.market_id,
                    ms.event_id,
                    ms.timestamp,
                    ms.news_context,
                    ms.order_book_snapshot,
                    ms.market_prices,
                    m.question,
                    m.description    AS market_description,
                    m.outcomes,
                    m.outcome_prices,
                    m.volume,
                    m.liquidity,
                    e.title          AS event_title,
                    e.description    AS event_description,
                    e.start_date,
                    e.end_date,
                    e.tags           AS event_tags,
                    r.winning_outcome,
                    r.resolved_at
                FROM market_snapshots ms
                JOIN markets m     ON ms.market_id = m.id
                JOIN events e      ON ms.event_id  = e.id
                JOIN resolutions r ON ms.market_id = r.market_id
                WHERE ms.ready_for_analysis = 1
                  AND LENGTH(ms.order_book_snapshot) > 5
                  AND m.outcome_prices NOT IN ('["0", "1"]', '["1", "0"]', '[]')
                ORDER BY r.resolved_at, ms.timestamp
            """)
        else:
            # Fallback: no snapshots table — use markets + events + resolutions
            logger.info("No market_snapshots table; falling back to markets/events/resolutions")
            has_predictions = "predictions" in tables
            if has_predictions:
                # Use predictions for order book / news if available
                c.execute("""
                    SELECT
                        COALESCE(p.id, m.rowid) AS snapshot_id,
                        m.id                    AS market_id,
                        m.event_id,
                        COALESCE(p.timestamp, m.last_updated) AS timestamp,
                        p.news_context,
                        p.order_book_snapshot,
                        m.outcome_prices        AS market_prices,
                        m.question,
                        m.description           AS market_description,
                        m.outcomes,
                        m.outcome_prices,
                        m.volume,
                        m.liquidity,
                        e.title                 AS event_title,
                        e.description           AS event_description,
                        e.start_date,
                        e.end_date,
                        e.tags                  AS event_tags,
                        r.winning_outcome,
                        r.resolved_at
                    FROM markets m
                    JOIN events e      ON m.event_id = e.id
                    JOIN resolutions r ON m.id = r.market_id
                    LEFT JOIN predictions p ON m.id = p.market_id
                    GROUP BY m.id
                    ORDER BY r.resolved_at
                """)
            else:
                c.execute("""
                    SELECT
                        m.rowid                 AS snapshot_id,
                        m.id                    AS market_id,
                        m.event_id,
                        m.last_updated          AS timestamp,
                        NULL                    AS news_context,
                        NULL                    AS order_book_snapshot,
                        m.outcome_prices        AS market_prices,
                        m.question,
                        m.description           AS market_description,
                        m.outcomes,
                        m.outcome_prices,
                        m.volume,
                        m.liquidity,
                        e.title                 AS event_title,
                        e.description           AS event_description,
                        e.start_date,
                        e.end_date,
                        e.tags                  AS event_tags,
                        r.winning_outcome,
                        r.resolved_at
                    FROM markets m
                    JOIN events e      ON m.event_id = e.id
                    JOIN resolutions r ON m.id = r.market_id
                    ORDER BY r.resolved_at
                """)

        rows = [dict(row) for row in c.fetchall()]
        conn.close()
        return rows

    def _format_input(self, row: dict) -> str:
        """Format task input matching PolyBench backtest prompt structure."""
        market_id = row["market_id"]
        outcomes = _safe_json(row.get("outcomes", "[]"))
        prices = _safe_json(row.get("outcome_prices", "[]"))
        prices_str = str(prices)

        # Market option section
        market_section = f"\n## Option ID: {market_id}\n"
        market_section += f"Question: {row['question']}\n"
        market_section += f"Last Known Prices: {prices_str}\n"

        # Order book formatted for AI
        ob_text = ""
        if row.get("order_book_snapshot"):
            try:
                ob_data = json.loads(row["order_book_snapshot"])
                if isinstance(ob_data, dict):
                    ob_lines = []
                    for outcome, ob in ob_data.items():
                        if not isinstance(ob, dict):
                            continue
                        ob_lines.append(f"Outcome: {outcome}")
                        best_bid = ob.get("best_bid", 0)
                        best_ask = ob.get("best_ask", 1)
                        spread = ob.get("spread")
                        mid = ob.get("mid_price")
                        bid_depth = ob.get("bid_depth_top3", 0)
                        ask_depth = ob.get("ask_depth_top3", 0)
                        # Handle raw asks/bids format (from test DBs)
                        if "asks" in ob and not best_bid:
                            asks = ob.get("asks", [])
                            bids = ob.get("bids", [])
                            if asks:
                                best_ask = float(asks[0]["price"]) if asks else 1.0
                            if bids:
                                best_bid = float(bids[0]["price"]) if bids else 0.0
                            spread = best_ask - best_bid if best_ask and best_bid else None
                            mid = (best_ask + best_bid) / 2 if spread and spread < 0.2 else None
                            bid_depth = sum(float(b.get("size", 0)) for b in bids[:3])
                            ask_depth = sum(float(a.get("size", 0)) for a in asks[:3])
                        ob_lines.append(f"  - Best Bid: {best_bid:.4f}, Best Ask: {best_ask:.4f}")
                        if spread:
                            ob_lines.append(f"  - Spread: {spread:.4f} ({spread*100:.2f}%)")
                        if mid:
                            ob_lines.append(f"  - Mid Price (implied probability): {mid:.4f}")
                        ob_lines.append(f"  - Bid liquidity (top 3): ${bid_depth:.2f}")
                        ob_lines.append(f"  - Ask liquidity (top 3): ${ask_depth:.2f}")
                    ob_text = "\n".join(ob_lines)
            except (json.JSONDecodeError, TypeError):
                ob_text = row["order_book_snapshot"]

        if ob_text:
            market_section += f"\nHistorical Order Book Analysis:\n{ob_text}\n"

        if row.get("volume"):
            try:
                market_section += f"Final Trading Volume: ${float(row['volume']):,.2f}\n"
            except (ValueError, TypeError):
                pass
        if row.get("liquidity"):
            try:
                market_section += f"Liquidity: ${float(row['liquidity']):,.0f}\n"
            except (ValueError, TypeError):
                pass

        # News section
        news = row.get("news_context", "") or ""

        # Assemble in PolyBench backtest format
        parts = [
            f"Current Date: {row['timestamp']}",
            f"Event: {row['event_title']}",
            f"Description & Rules: {row.get('event_description', '') or row.get('market_description', '')}",
            "",
            "=== MARKET OPTIONS WITH HISTORICAL ORDER BOOK DATA ===",
            market_section,
            "=== EVIDENCE & CONTEXT AT SNAPSHOT TIME ===",
            news if news else "No historical news context provided.",
            "",
            "TASK:",
            "Analyze the historical evidence AND historical order book data. Identify ALL options that are mispriced.",
            "The stated prices imply probabilities. Order book mid-prices show actual market state.",
            "Signal a trade for ANY option where your estimated probability differs significantly from price.",
        ]
        return "\n".join(parts)

    def _parse_freeform(self, text: str) -> dict:
        """Extract decision/side/confidence from free-form text."""
        result: dict[str, Any] = {"decision": "BUY", "confidence": 0.5}
        upper = text.upper()
        if "SKIP" in upper:
            result["decision"] = "SKIP"
        if "YES" in upper:
            result["side"] = "YES"
        elif "NO" in upper:
            result["side"] = "NO"
        import re
        m = re.search(r"confidence[:\s]*([\d.]+)", text, re.IGNORECASE)
        if m:
            try:
                result["confidence"] = min(1.0, max(0.0, float(m.group(1))))
            except ValueError:
                pass
        return result

    # Kept for backward-compat with existing tests
    @staticmethod
    def _get_fill_price(ob_json: str, side: str, prices_json: str,
                        budget: float = 10.0) -> float:
        spent, shares, vwap, ob_exists = simulate_order_book_fill(
            ob_json, side, budget)
        if ob_exists and vwap is not None:
            return vwap
        return _fallback_price(side, prices_json)

    @staticmethod
    def _simulate_return(ob_json: str, side: str, confidence: float,
                         is_correct: bool, prices_json: str) -> float:
        fill = PolyBenchBenchmark._get_fill_price(ob_json, side, prices_json)
        return (1.0 / fill) - 1.0 if is_correct else -1.0


# -- Trade simulation (matches evaluate_mimo.py) ----------------------------

def _fallback_price(side: str, prices_json: str) -> float:
    """Extract market mid-price for the given side, defaulting to 0.5."""
    try:
        prices = json.loads(prices_json) if prices_json else []
        if isinstance(prices, list) and len(prices) >= 2:
            p = float(prices[0]) if side.upper() == "YES" else float(prices[1])
            if 0.0 < p < 1.0:
                return p
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    return 0.5


def _simulate_trade(
    ob_json: str, side: str, budget: float,
    fallback_price: float, is_correct: bool,
) -> tuple[float, float]:
    """Simulate a single trade. Returns (investment, profit).

    Uses OB fill when available, else the fallback mid-price.
    Matches the official PolyBench dual-path logic:
      - OB path:  profit = (shares × $1 − spent) if correct, else −spent
      - Fallback:  profit = investment × (1/price − 1)  if correct, else −investment
    """
    spent, shares, _vwap, ob_exists = simulate_order_book_fill(
        ob_json, side, budget)

    if ob_exists and shares > 0:
        profit = (shares - spent) if is_correct else -spent
        return spent, profit

    # Fallback (no OB or no fills)
    inv = budget
    if is_correct:
        return inv, inv * ((1.0 / fallback_price) - 1.0)
    return inv, -inv


def _compute_apy(raw_return: float, pred_ts: str | None,
                 resolved_ts: str | None) -> float | None:
    """APY = raw_return × (365 / days_held). Returns None if dates unavailable."""
    if not pred_ts or not resolved_ts:
        return None
    try:
        p = datetime.strptime(str(pred_ts)[:19], "%Y-%m-%d %H:%M:%S")
        r = datetime.strptime(str(resolved_ts)[:19], "%Y-%m-%d %H:%M:%S")
        days = max(1, (r - p).days)
        return raw_return * (365.0 / days)
    except (ValueError, TypeError):
        return None


def simulate_order_book_fill(
    order_book_json: str, pred_side: str, budget: float,
) -> tuple[float, float, float | None, bool]:
    """Simulate filling a market buy against the ask side.

    Returns (spent, shares, vwap, ob_exists).
    Matches PolyBench evaluate_mimo.py methodology.
    """
    if not order_book_json or budget <= 0:
        return 0.0, 0.0, None, False
    try:
        ob_data = json.loads(order_book_json)
    except (json.JSONDecodeError, TypeError):
        return 0.0, 0.0, None, False
    if not ob_data or not isinstance(ob_data, dict):
        return 0.0, 0.0, None, False

    # Find the right outcome's order book
    target_ob = None
    for key in ob_data:
        if key.strip().upper() == pred_side.strip().upper():
            target_ob = ob_data[key]
            break
    if target_ob is None:
        if pred_side.upper() == "YES":
            target_ob = (ob_data.get("Yes") or ob_data.get("yes")
                         or ob_data.get("YES"))
            if target_ob is None:
                keys = list(ob_data.keys())
                if keys:
                    target_ob = ob_data[keys[0]]
        elif pred_side.upper() == "NO":
            target_ob = (ob_data.get("No") or ob_data.get("no")
                         or ob_data.get("NO"))
            if target_ob is None:
                keys = list(ob_data.keys())
                if len(keys) >= 2:
                    target_ob = ob_data[keys[1]]
    if not target_ob:
        return 0.0, 0.0, None, True

    asks = target_ob.get("asks", [])
    if not asks:
        return 0.0, 0.0, None, True

    asks_sorted = sorted(asks, key=lambda x: float(x["price"]))
    remaining, total_shares, total_spent = budget, 0.0, 0.0
    for level in asks_sorted:
        p = float(level["price"])
        s = float(level["size"])
        if p <= 0.0 or p >= 1.0:
            continue
        buy = min(remaining / p, s)
        cost = buy * p
        total_shares += buy
        total_spent += cost
        remaining -= cost
        if remaining <= 0.001:
            break
    if total_shares <= 0:
        return 0.0, 0.0, None, True
    return total_spent, total_shares, total_spent / total_shares, True


def _safe_json(s: str | None) -> list:
    if not s:
        return []
    try:
        val = json.loads(s)
        return val if isinstance(val, list) else []
    except (json.JSONDecodeError, TypeError):
        return []
