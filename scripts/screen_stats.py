"""Screen 2: Per-stock similar-setup backtest over the liquid universe.

For every symbol this replays the 2-4% "hit" setup *shape* across its own
history using the exact conservative trigger rules of the trade-validation
pipeline (see scripts/6_backtest_setup.py — mirrored here rather than
imported so the existing pipeline stays untouched):

    * signal day D = a day whose 50-EMA state matches the stock's CURRENT
      trend (BUY when close > EMA50, SELL when below) — the trend-matched
      bucket, same conditioning the pipeline's empirical mode uses
    * entry = open of bar D+1 (no lookahead)
    * stop  = entry - k x ATR14 (BUY), entry + k x ATR14 (SELL)
    * target = entry x (1 +- hit) for hit in {2%, 3%, 4%}
    * resolve over the holding window: stop-first wins on same-bar
      ambiguity (conservative), target-first is a WIN, neither -> settle
      at the window-end close vs entry (mirrors expiry settlement)
    * P&L is net of modeled transaction costs (FUT rate, common.py)

Each cell (horizon, hit) reports: n, target-first rate, win rate with a
Wilson 95% CI, net expectancy, and the "did the market move hit% either
way" rate — the honesty stat that separates volatility from direction.

The setup's R:R and break-even win rate p* = 1/(1 + net_rr) are derived
from the stock's CURRENT ATR%, and each cell gets the same tri-state
verdict script 4 uses: EDGE CONFIRMED / POSITIVE BUT UNCONFIRMED / NO EDGE
(CI lower bound must clear p*).

Bars are cached per symbol under data_cache/ (refreshed daily), so the
morning screen is fast after the first run. Symbols with too little
history or failed downloads are skipped and counted, never fatal.

Usage:
    python scripts/screen_stats.py --universe screen_universe_out.json
        [--period 2y] [--stop-atr 1.5] [--limit N] [--no-cache]

Output contract (screen_stats_out.json): see emit at the bottom.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import COST_RATE_FUT, PROJECT_ROOT, emit_json, load_json, wilson_ci  # noqa: E402

HISTORY_BARS = 50          # bars needed before the EMA(50) is meaningful (matches script 6)
HORIZONS = [5, 10, 20]     # 1W / 2W / 4W in trading bars
HIT_LEVELS = [0.02, 0.03, 0.04]
CACHE_DIR = PROJECT_ROOT / "data_cache"


# ---- bars: cache + batched download ---------------------------------------
def load_bars_from_cache(symbol: str):
    f = CACHE_DIR / f"{symbol}.csv"
    if not f.exists():
        return None
    import pandas as pd

    try:
        df = pd.read_csv(f, parse_dates=["Date"], index_col="Date")
        if len(df) < HISTORY_BARS + HORIZONS[-1] + 1:
            return None
        # Cache is only trusted if written today; the screen is a daily routine.
        if datetime.fromtimestamp(f.stat().st_mtime).date().isoformat() != date.today().isoformat():
            return None
        return df
    except Exception:
        return None


def save_bars_to_cache(symbol: str, df) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(CACHE_DIR / f"{symbol}.csv", date_format="%Y-%m-%d")


def download_bars(symbols: list[str], period: str) -> dict[str, Any]:
    """Batch-download the missing symbols (chunked) and cache them."""
    import yfinance as yf

    got: dict[str, Any] = {}
    chunk_size = 40
    for i in range(0, len(symbols), chunk_size):
        chunk = symbols[i:i + chunk_size]
        try:
            d = yf.download(
                [s + ".NS" for s in chunk], period=period, interval="1d",
                group_by="ticker", threads=True, progress=False, auto_adjust=True,
            )
        except Exception:
            continue
        for s in chunk:
            try:
                sub = d[s + ".NS"] if len(chunk) > 1 else d
                sub = sub.dropna(subset=["Open", "High", "Low", "Close"])
                if len(sub) < HISTORY_BARS + HORIZONS[-1] + 1:
                    continue
                save_bars_to_cache(s, sub)
                got[s] = sub
            except Exception:
                continue
    return got


# ---- indicators (same definitions as script 2 / script 6) ------------------
def indicators(df):
    import pandas as pd

    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr = tr.rolling(14).mean()
    ema = close.ewm(span=50, adjust=False).mean()
    return atr, ema


# ---- the replay -------------------------------------------------------------
def replay_stock(symbol: str, df, k: float) -> dict[str, Any] | None:
    """Backtest the 2-4% hit shape for one stock. Returns the stats payload."""
    atr_s, ema_s = indicators(df)
    opens = df["Open"].tolist()
    highs = df["High"].tolist()
    lows = df["Low"].tolist()
    closes = df["Close"].tolist()
    n = len(df)
    if n < HISTORY_BARS + HORIZONS[-1] + 1:
        return None

    atr_now = atr_s.iloc[-1]
    close_now = float(closes[-1])
    ema_now = float(ema_s.iloc[-1])
    if not atr_now or atr_now <= 0 or close_now <= 0:
        return None

    atr_pct = atr_now / close_now
    bullish = close_now > ema_now
    direction = "BUY" if bullish else "SELL"

    cells: dict[str, dict[str, Any]] = {}
    for hz in HORIZONS:
        # Single pass per signal day per horizon; all hit levels resolved
        # from the same window scan (stop/target prices differ only by hit).
        agg: dict[float, list[tuple[bool, bool, float, bool]]] = {h: [] for h in HIT_LEVELS}
        for d in range(HISTORY_BARS - 1, n - hz):
            atr_d = atr_s.iloc[d]
            if not atr_d or atr_d <= 0:
                continue
            ema_d = float(ema_s.iloc[d])
            close_d = float(closes[d])
            if (direction == "BUY" and close_d <= ema_d) or (direction == "SELL" and close_d >= ema_d):
                continue  # only the trend-matched bucket, like script 4's empirical mode

            entry = float(opens[d + 1])
            if entry != entry or entry <= 0:
                continue
            risk = k * float(atr_d)
            stop = entry - risk if direction == "BUY" else entry + risk
            tgt = {h: entry * (1 + h) if direction == "BUY" else entry * (1 - h) for h in HIT_LEVELS}

            stop_bar: int | None = None
            tgt_bar: dict[float, int | None] = {h: None for h in HIT_LEVELS}
            hi_max = entry
            lo_min = entry
            for j in range(d + 1, d + hz + 1):
                lo, hi = lows[j], highs[j]
                if lo != lo or hi != hi:
                    continue
                hi_max = max(hi_max, hi)
                lo_min = min(lo_min, lo)
                if direction == "BUY":
                    if lo <= stop:            # stop first (conservative on same-bar ambiguity)
                        stop_bar = j
                        break
                    for h in HIT_LEVELS:
                        if tgt_bar[h] is None and hi >= tgt[h]:
                            tgt_bar[h] = j
                else:
                    if hi >= stop:
                        stop_bar = j
                        break
                    for h in HIT_LEVELS:
                        if tgt_bar[h] is None and lo <= tgt[h]:
                            tgt_bar[h] = j

            final_close = float(closes[d + hz])
            for h in HIT_LEVELS:
                if tgt_bar[h] is not None and (stop_bar is None or tgt_bar[h] < stop_bar):
                    outcome, triggered, final = True, True, tgt[h]
                elif stop_bar is not None:
                    outcome, triggered, final = False, True, stop
                else:
                    outcome, triggered, final = final_close > entry, False, final_close
                gross = (final - entry) / entry if direction == "BUY" else (entry - final) / entry
                moved = (hi_max >= entry * (1 + h)) or (lo_min <= entry * (1 - h))
                agg[h].append((outcome, triggered, gross - COST_RATE_FUT, moved))

        for h in HIT_LEVELS:
            recs = agg[h]
            nn = len(recs)
            if nn == 0:
                continue
            wins = sum(1 for r in recs if r[0])
            tf = sum(1 for r in recs if r[1] and r[0])
            ci_low, ci_high = wilson_ci(wins, nn)
            rr = (h - COST_RATE_FUT) / (k * atr_pct + COST_RATE_FUT)
            p_star = 1.0 / (1.0 + rr) if rr > 0 else 1.0
            win_rate = wins / nn
            if ci_low > p_star:
                verdict = "EDGE CONFIRMED"
            elif win_rate > p_star:
                verdict = "POSITIVE BUT UNCONFIRMED"
            else:
                verdict = "NO EDGE"
            cells[f"{hz}:{h * 100:.1f}"] = {
                "n": nn,
                "target_first": tf,
                "stop_first": sum(1 for r in recs if r[1] and not r[0]),
                "settled": sum(1 for r in recs if not r[1]),
                "wins": wins,
                "win_rate": round(win_rate * 100, 1),
                "ci_low": round(ci_low * 100, 1),
                "ci_high": round(ci_high * 100, 1),
                "avg_pnl_pct": round(sum(r[2] for r in recs) / nn * 100, 2),
                "expectancy_pct": round(sum(r[2] for r in recs) / nn * 100, 2),
                "p_moved_any": round(sum(1 for r in recs if r[3]) / nn * 100, 1),
                "rr": round(rr, 2),
                "p_star": round(p_star * 100, 1),
                "verdict": verdict,
            }

    return {
        "symbol": symbol,
        "close": round(close_now, 2),
        "atr_pct": round(atr_pct * 100, 2),
        "trend": "BULLISH" if bullish else "BEARISH",
        "direction": direction,
        "stop_pct": round(k * atr_pct * 100, 2),
        "cells": cells,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Per-stock similar-setup backtest for the daily screen.")
    parser.add_argument("--universe", default=PROJECT_ROOT / "screen_universe_out.json",
                        help="Path to screen_universe_out.json")
    parser.add_argument("--period", default="2y", help="yfinance lookback (default: 2y)")
    parser.add_argument("--stop-atr", type=float, default=1.5, help="Stop distance as a multiple of ATR14 (default: 1.5)")
    parser.add_argument("--limit", type=int, default=0, help="Only scan the first N symbols (smoke tests)")
    parser.add_argument("--no-cache", action="store_true", help="Ignore the bar cache (still rewrites it)")
    args = parser.parse_args()

    try:
        uni = load_json(args.universe)
    except FileNotFoundError:
        emit_json({"error": f"Universe file not found: {args.universe} (run scripts/screen_universe.py first)"})
        return 1

    symbols = [u["symbol"] for u in uni.get("universe", [])]
    if args.limit:
        symbols = symbols[:args.limit]

    cached: dict[str, Any] = {}
    if not args.no_cache:
        for s in symbols:
            df = load_bars_from_cache(s)
            if df is not None:
                cached[s] = df
    missing = [s for s in symbols if s not in cached]
    downloaded = download_bars(missing, args.period) if missing else {}
    bars = {**downloaded, **cached}

    stats: dict[str, Any] = {}
    skipped: list[dict[str, str]] = []
    for s in symbols:
        df = bars.get(s)
        if df is None:
            skipped.append({"symbol": s, "reason": "no price data"})
            continue
        res = replay_stock(s, df, args.stop_atr)
        if res is None:
            skipped.append({"symbol": s, "reason": "insufficient history"})
            continue
        stats[s] = res

    shortable = set(uni.get("shortable", []))
    payload = {
        "generated_on": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "params": {
            "period": args.period,
            "stop_atr": args.stop_atr,
            "horizons": HORIZONS,
            "hit_levels": [h * 100 for h in HIT_LEVELS],
            "cost_rate": COST_RATE_FUT,
            "long_only": False,
        },
        "scanned": len(stats),
        "skipped": skipped,
        "shortable": sorted(shortable),
        "symbols": stats,
    }
    emit_json(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
