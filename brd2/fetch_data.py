"""01_fetch_data.py — Daily OHLCV fetcher for the BRD2 prediction engine.

Downloads ~3 years of daily bars for stocks in liquid_nse100.csv via yfinance,
caches them as CSVs under brd2/cache/, and emits a manifest JSON.

Design (Nadh): yfinance is free, unreliable, and good enough. Cache locally,
refresh on a flag, and never let a network failure kill the pipeline.

Usage:
    python brd2/01_fetch_data.py [--period 3y] [--symbols RELIANCE,HDFCBANK]
    python brd2/01_fetch_data.py                    # all stocks, 3y
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common_brd2 import (  # noqa: E402
    CACHE_DIR,
    DATA_CACHE_DIR,
    TOTAL_BARS,
    emit_json,
    ensure_dirs,
    load_universe,
    resolve_ohlcv_path,
)


def download_symbol(symbol: str, period: str, max_retries: int = 3) -> pd.DataFrame | None:
    """Download daily OHLCV for one NSE symbol. Retries on transient failures."""
    ticker = f"{symbol}.NS"
    for attempt in range(max_retries):
        try:
            df = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=True)
            df = df.dropna(subset=["Open", "High", "Low", "Close"])
            if len(df) < 60:  # need at least ~3 months for indicators
                return None
            return df
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(1.5 * (attempt + 1))
    return None


def save_to_cache(symbol: str, df: pd.DataFrame) -> Path:
    """Persist bars as CSV in the cache directory."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{symbol}.csv"
    df.to_csv(path, date_format="%Y-%m-%d")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch daily OHLCV for NSE stocks.")
    parser.add_argument("--period", default="3y", help="yfinance lookback (default: 3y)")
    parser.add_argument("--symbols", default="",
                        help="Comma-separated symbols to fetch (default: all from liquid_nse100.csv)")
    parser.add_argument("--limit", type=int, default=0,
                        help="Only fetch first N symbols (smoke test)")
    args = parser.parse_args()

    ensure_dirs()

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        universe = load_universe()
        symbols = [u["symbol"] for u in universe]

    if args.limit:
        symbols = symbols[:args.limit]

    results: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    t0 = time.time()

    for i, sym in enumerate(symbols):
        # Nadh: check data_cache/ first — archive the data, refresh with a flag
        existing = resolve_ohlcv_path(sym)
        if existing is not None:
            import pandas as pd
            df = pd.read_csv(existing, parse_dates=["Date"], index_col="Date")
            results.append({
                "symbol": sym,
                "bars": len(df),
                "first_date": df.index[0].strftime("%Y-%m-%d"),
                "last_date": df.index[-1].strftime("%Y-%m-%d"),
                "cache_path": str(existing),
                "source": "data_cache" if str(DATA_CACHE_DIR) in str(existing) else "brd2_cache",
            })
            continue
        # Only download if not in any cache
        df = download_symbol(sym, args.period)
        if df is None or df.empty:
            skipped.append({"symbol": sym, "reason": "no data or insufficient history"})
            continue
        path = save_to_cache(sym, df)
        results.append({
            "symbol": sym,
            "bars": len(df),
            "first_date": df.index[0].strftime("%Y-%m-%d"),
            "last_date": df.index[-1].strftime("%Y-%m-%d"),
            "cache_path": str(path),
            "source": "downloaded",
        })
        # Progress every 10 symbols
        if (i + 1) % 10 == 0:
            elapsed = time.time() - t0
            print(f"  ... {i + 1}/{len(symbols)} fetched ({elapsed:.0f}s)", file=sys.stderr)

    elapsed = time.time() - t0
    manifest = {
        "generated_on": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "period": args.period,
        "fetched": len(results),
        "skipped": len(skipped),
        "elapsed_seconds": round(elapsed, 1),
        "symbols": results,
        "skipped_details": skipped,
    }
    emit_json(manifest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
