"""Script 5: Track & Resolve — the learning loop.

Two responsibilities, both idempotent:

1. RESOLVE: every previously recorded OPEN recommendation (is_seed=0) is
   re-checked against live daily bars from Yahoo Finance. Scanning day by
   day from the recommendation date, the first trigger wins: stoploss hit
   -> LOSS, target hit -> WIN. If a bar breaches both, it is resolved
   conservatively as a LOSS (stoploss assumed hit first). Nothing is
   resolved on the recommendation day itself.

   Expiry-aware: triggers are only honoured up to the recommendation's
   expiry date. If neither target nor stop is hit and the fetched data
   covers the expiry date, the reco is settled at the last close on/before
   expiry (WIN if that close is above entry for BUY, below for SELL, else
   LOSS) — so open rows can never outlive their contract.

   All resolved P&L is net of modeled transaction costs (see
   `common.estimate_costs`); `costs_inr` is stored alongside `pnl_inr`.

2. TRACK: the recommendation currently under analysis (current_trade.json)
   is upserted as an OPEN row with its confidence score and decision, so
   future runs can resolve it. Re-analyzing the same setup refreshes the
   row instead of duplicating it.

Finally it prints JSON stats: the accumulated track record, decision
accuracy, and the missed/avoided breakdown (wins on rejected recos =
missed profit; losses on rejected recos = avoided losses).

Usage:
    python scripts/5_track_and_resolve.py [--score 35] [--decision "NO-GO (REJECT)"]
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import INPUT_FILE, emit_json, estimate_costs, load_json  # noqa: E402
from db import compute_stats, connect, upsert_tracked_trade  # noqa: E402


def resolve_open_trades(conn, offline: bool = False) -> tuple[list[dict[str, Any]], str]:
    """Resolve OPEN recommendations against live prices. Returns (resolved, status)."""
    rows = conn.execute(
        "SELECT id, symbol, action, entry_price, target, stoploss, recommended_on, lot_size, "
        "instrument_type, expiry_date "
        "FROM trade_recommendations WHERE is_seed=0 AND outcome='OPEN'"
    ).fetchall()

    if not rows:
        return [], "no_open_rows"
    if offline:
        return [], "offline"

    import yfinance as yf

    resolved: list[dict[str, Any]] = []
    by_symbol: dict[str, list[tuple]] = {}
    for r in rows:
        by_symbol.setdefault(r[1], []).append(r)

    for symbol, recs in by_symbol.items():
        earliest = min(r[6] for r in recs)[:10]
        try:
            df = yf.Ticker(f"{symbol}.NS").history(start=earliest, interval="1d")
        except Exception:
            continue
        if df.empty:
            continue

        # Materialize bars once per symbol: (day, low, high, close)
        bars = [
            (ts.date(), float(bar["Low"]), float(bar["High"]), float(bar["Close"]))
            for ts, bar in df.iterrows()
        ]

        for r in recs:
            rid, sym, action, entry, target, sl, rec_on, lot, inst, expiry_text = r
            try:
                rec_day = date.fromisoformat(rec_on[:10])
            except ValueError:
                continue
            expiry = date.fromisoformat(expiry_text) if expiry_text else None

            outcome = final = None
            trigger_day = None
            # 1. Trigger scan — only bars strictly after the recommendation day,
            #    and never after expiry (a contract cannot be traded post-expiry).
            for day, low, high, _close in bars:
                if day <= rec_day:
                    continue  # the trade was placed intraday on rec_day; resolution starts the next bar
                if expiry and day > expiry:
                    break
                if action == "SELL":
                    if high >= sl:   # price rose to the stop first
                        outcome, final, trigger_day = "LOSS", sl, day
                        break
                    if low <= target:  # price fell to target
                        outcome, final, trigger_day = "WIN", target, day
                        break
                else:  # BUY
                    if low <= sl:   # price fell to the stop first (conservative on same-bar ambiguity)
                        outcome, final, trigger_day = "LOSS", sl, day
                        break
                    if high >= target:
                        outcome, final, trigger_day = "WIN", target, day
                        break

            # 2. Expiry settlement — no trigger and data covers the expiry date:
            #    close the position at the last close on/before expiry.
            if not outcome and expiry and lot:
                covered = [b for b in bars if b[0] <= expiry]
                if covered and bars and bars[-1][0] >= expiry:
                    final = covered[-1][3]  # last close on/before expiry
                    trigger_day = covered[-1][0]
                    gross = (final - entry) * lot if action == "BUY" else (entry - final) * lot
                    outcome = "WIN" if gross > 0 else "LOSS"

            if not outcome:
                continue

            lot = lot or 0
            costs = estimate_costs(entry, lot, inst)
            pnl = ((final - entry) * lot if action == "BUY" else (entry - final) * lot) - costs
            conn.execute(
                "UPDATE trade_recommendations SET outcome=?, pnl_inr=?, final_price=?, resolved_on=?, costs_inr=? "
                "WHERE id=?",
                (outcome, round(pnl, 2), final, str(trigger_day), costs, rid),
            )
            resolved.append({
                "symbol": sym, "action": action, "outcome": outcome,
                "pnl_inr": round(pnl, 2), "costs_inr": costs, "final_price": final,
                "resolved_on": str(trigger_day),
            })

    conn.commit()
    return resolved, "ok" if resolved else "no_triggers"


def track_and_stats(conn, data: dict[str, Any] | None, score: int | None, decision: str | None,
                    offline: bool) -> dict[str, Any]:
    resolved, res_status = resolve_open_trades(conn, offline=offline)
    stats = compute_stats(conn)

    newly_tracked = updated = False
    if data is not None:
        newly_tracked, _ = upsert_tracked_trade(conn, data, score=score, decision=decision)
        updated = not newly_tracked
        stats = compute_stats(conn)  # refresh counts with the new/updated row

    return {
        "tracking": {
            "newly_tracked": bool(newly_tracked),
            "updated_existing": bool(updated),
        },
        "resolved_this_run": {
            "count": len(resolved),
            "wins": sum(1 for r in resolved if r["outcome"] == "WIN"),
            "losses": sum(1 for r in resolved if r["outcome"] == "LOSS"),
            "details": resolved,
        },
        "resolution_status": res_status,
        **stats,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Track recommendations and resolve them against live prices.")
    parser.add_argument("--score", type=int, help="Confidence score of the current trade (0-100)")
    parser.add_argument("--decision", help="Decision label of the current trade")
    parser.add_argument("--no-track", action="store_true", help="Do not record the current trade")
    parser.add_argument("--offline", action="store_true", help="Skip network resolution (rows stay OPEN)")
    args = parser.parse_args()

    conn = connect()
    try:
        if args.no_track:
            data = None
        else:
            try:
                data = load_json(INPUT_FILE)
            except FileNotFoundError:
                data = None
        emit_json(track_and_stats(conn, data, args.score, args.decision, offline=args.offline))
    finally:
        conn.close()
