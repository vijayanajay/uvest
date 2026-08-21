"""Script 2: Live Market Context & Technical Indicator Fetcher.

Fetches 60 days of daily bars from Yahoo Finance for the symbol
(appended with .NS for Indian equities), then computes the 14-day ATR,
50 EMA, and whether the stop-loss distance is tight relative to daily
volatility.

Usage:
    python scripts/2_market_data.py [--symbol BHARTIARTL] [--expiry 25AUG]

Defaults to symbol/expiry from `current_trade.json`. On network failure a
JSON error payload is emitted (exit code 1) so downstream steps can
degrade gracefully.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import INPUT_FILE, emit_json, fail, load_json  # noqa: E402


def fetch_market_context(symbol: str, sl_price: float, entry_price: float) -> dict[str, Any]:
    import pandas as pd
    import yfinance as yf

    # Append .NS for Indian Stock Exchange on yfinance
    ticker = yf.Ticker(f"{symbol}.NS")
    df = ticker.history(period="60d", interval="1d")
    df = df.dropna(subset=["Close", "High", "Low"])

    if df.empty:
        fail(f"Failed to fetch ticker data for {symbol}.NS")

    # Calculate Daily ATR (14) using Wilder's true range definition
    df["TR"] = pd.DataFrame(
        [
            df["High"] - df["Low"],
            (df["High"] - df["Close"].shift(1)).abs(),
            (df["Low"] - df["Close"].shift(1)).abs(),
        ]
    ).max()
    atr_daily = df["TR"].rolling(14).mean().iloc[-1]

    # Calculate 50 EMA and the latest close
    ema_50 = df["Close"].ewm(span=50, adjust=False).mean().iloc[-1]
    close_price = df["Close"].iloc[-1]
    high_52w = float(df["High"].max())

    sl_distance = abs(entry_price - sl_price)
    atr_ratio = sl_distance / atr_daily if atr_daily and atr_daily > 0 else 0.0

    # Coerce numpy scalars to native Python types so json.dumps works.
    atr_daily = float(atr_daily)
    close_price = float(close_price)
    ema_50 = float(ema_50)
    atr_ratio = float(atr_ratio)

    return {
        "symbol": symbol,
        "current_atr": round(atr_daily, 2),
        "close_price": round(close_price, 2),
        "52w_high": round(high_52w, 2),
        "sl_distance": round(sl_distance, 2),
        "atr_sl_ratio": round(atr_ratio, 2),
        "is_tight_sl": bool(atr_ratio < 1.0),
        "ema_50": round(ema_50, 2),
        "above_50_ema": bool(close_price > ema_50),
        "daily_trend": "BULLISH" if close_price > ema_50 else "BEARISH",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch live market context for a trade setup.")
    parser.add_argument("--symbol", help="Ticker symbol (default: from current_trade.json)")
    parser.add_argument("--expiry", help="Expiry label (accepted for blueprint parity, unused in math)")
    args = parser.parse_args()

    try:
        data = load_json(INPUT_FILE)
    except FileNotFoundError:
        fail(f"Input file not found: {INPUT_FILE}")

    symbol = args.symbol or data.get("symbol")
    if not symbol:
        fail("No symbol provided via --symbol or current_trade.json")

    try:
        emit_json(fetch_market_context(symbol, float(data["stoploss"]), float(data["entry_price"])))
    except Exception as exc:  # network / parse / yfinance failures must not crash the suite
        fail(f"Market data fetch failed for {symbol}: {exc}")
