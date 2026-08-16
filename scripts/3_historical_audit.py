"""Script 3: Univest Historical Track Record & Expectancy Auditor.

Queries the local SQLite database (`univest_history.db`) for past
recommendations on the given symbol/instrument-type and computes the
historical win rate, average win/loss, and net expectancy per trade.

When no data exists for the ticker (or the DB is absent), a benchmark
fallback is emitted with status `NO_HISTORICAL_DATA_FOR_TICKER` so the
score calculator can proceed.

Usage:
    python scripts/3_historical_audit.py [--symbol BHARTIARTL] [--type FUT]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DB_FILE, INPUT_FILE, emit_json, fail, load_json  # noqa: E402


def audit_univest_performance(symbol: str, instrument_type: str) -> dict[str, Any]:
    if not DB_FILE.exists():
        return {
            "ticker_sample_size": 0,
            "win_rate": 0.65,  # Default benchmark
            "net_expectancy_inr": 0,
            "status": "NO_HISTORICAL_DATA_FOR_TICKER",
        }

    conn = sqlite3.connect(str(DB_FILE))
    cursor = conn.cursor()

    # Query past trades for this symbol and asset class
    cursor.execute(
        """
        SELECT outcome, pnl_inr, rr_ratio
        FROM trade_recommendations
        WHERE symbol = ? AND instrument_type = ?
        """,
        (symbol, instrument_type),
    )
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        # Fallback to general instrument stats if symbol stats don't exist
        return {
            "ticker_sample_size": 0,
            "win_rate": 0.65,  # Default benchmark
            "net_expectancy_inr": 0,
            "status": "NO_HISTORICAL_DATA_FOR_TICKER",
        }

    wins = [r for r in rows if r[0] == "WIN"]
    losses = [r for r in rows if r[0] == "LOSS"]
    win_rate = len(wins) / len(rows) if rows else 0.0

    avg_win = sum(r[1] for r in wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(r[1] for r in losses) / len(losses)) if losses else 0.0

    # Expectancy formula: (Win% * Avg Win) - (Loss% * Avg Loss)
    expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)

    return {
        "symbol": symbol,
        "instrument_type": instrument_type,
        "ticker_sample_size": len(rows),
        "historical_win_rate": round(win_rate * 100, 2),
        "avg_win_inr": round(avg_win, 2),
        "avg_loss_inr": round(avg_loss, 2),
        "net_expectancy_per_trade": round(expectancy, 2),
        "edge_is_positive": expectancy > 0,
        "status": "OK",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audit Univest's historical track record for a setup.")
    parser.add_argument("--symbol", help="Ticker symbol (default: from current_trade.json)")
    parser.add_argument("--type", dest="instrument_type", help="Instrument type FUT/OPT (default: from current_trade.json)")
    args = parser.parse_args()

    try:
        data = load_json(INPUT_FILE)
    except FileNotFoundError:
        fail(f"Input file not found: {INPUT_FILE}")

    symbol = args.symbol or data.get("symbol")
    instrument_type = args.instrument_type or data.get("instrument_type")
    if not symbol or not instrument_type:
        fail("Symbol and instrument type required (--symbol/--type or current_trade.json)")

    emit_json(audit_univest_performance(symbol, instrument_type))
