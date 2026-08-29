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
When the regime cache exists (data_cache/NIFTY50.csv, written by
screen_regime.py), each cell also splits its outcomes by whether NIFTY
closed above its 200-DMA on the signal day (`regime.bull` / `regime.bear`)
so the daily prompt can see whether an edge holds in both tapes.

Per stock the payload also carries `returns`: the forward-return
*distribution* (median, IQR, P10/P90, % positive, best/worst, net of
costs) for every trend-matched window at 1W/2W/4W — the raw buy-and-hold
range of returns, separate from the managed stop/target geometry.

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
from common import (  # noqa: E402
    COST_RATE_FUT, PROJECT_ROOT, emit_json, load_json, percentile_sorted, wilson_ci,
    get_sector, compute_rsi, compute_macd, compute_bollinger, compute_volume_surge,
    momentum_composite,
)

HISTORY_BARS = 50          # bars needed before the EMA(50) is meaningful (matches script 6)
HORIZONS = [2, 3, 5, 10, 20]   # 2D / 3D / 1W / 2W / 4W in trading bars
HIT_LEVELS = [0.01, 0.02, 0.03, 0.04]  # 1% / 2% / 3% / 4% — 1% captures the 0.5-1% daily target
CACHE_DIR = PROJECT_ROOT / "data_cache"
NIFTY_CACHE = CACHE_DIR / "NIFTY50.csv"   # written by screen_regime.py each morning


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


# ---- market regime conditioning (NIFTY 50 close vs 200-DMA) -----------------
def load_nifty_regime_state() -> dict | None:
    """NIFTY close-vs-200DMA state per date from the regime cache.

    Returns {date: True/False} — True when NIFTY closed above its 200-DMA
    (bull tape that day) — or None when the cache is missing. The cache is
    written by scripts/screen_regime.py each morning; without it the
    per-cell regime split is simply skipped (never fatal).
    """
    if not NIFTY_CACHE.exists():
        return None
    import pandas as pd

    try:
        df = pd.read_csv(NIFTY_CACHE, parse_dates=["Date"], index_col="Date")
        close = df["Close"]
        dma200 = close.rolling(200).mean()
        state: dict = {}
        for ts, c in close.items():
            m = dma200.loc[ts]
            if m == m:  # 200-DMA needs 200 bars of history; skip NaNs
                state[ts.date()] = bool(c > m)
        return state or None
    except Exception:
        return None


def load_nifty_close_series():
    """Load NIFTY close series for relative strength benchmarking."""
    if not NIFTY_CACHE.exists():
        return None
    import pandas as pd

    try:
        df = pd.read_csv(NIFTY_CACHE, parse_dates=["Date"], index_col="Date")
        return df["Close"].sort_index()
    except Exception:
        return None


def compute_relative_strength(df, nifty_close) -> dict[str, float]:
    """Compute rolling Relative Strength (RS) over 20 and 50 bars vs NIFTY 50."""
    if nifty_close is None or len(df) < 50:
        return {"rs_20": 0.0, "rs_50": 0.0}
    try:
        stock_close = df["Close"]
        common_dates = stock_close.index.intersection(nifty_close.index)
        if len(common_dates) < 50:
            return {"rs_20": 0.0, "rs_50": 0.0}
        s_aligned = stock_close.loc[common_dates]
        n_aligned = nifty_close.loc[common_dates]

        s_ret20 = (s_aligned.iloc[-1] / s_aligned.iloc[-21] - 1.0) if len(s_aligned) >= 21 else 0.0
        n_ret20 = (n_aligned.iloc[-1] / n_aligned.iloc[-21] - 1.0) if len(n_aligned) >= 21 else 0.0
        rs_20 = (s_ret20 - n_ret20) * 100.0

        s_ret50 = (s_aligned.iloc[-1] / s_aligned.iloc[-51] - 1.0) if len(s_aligned) >= 51 else 0.0
        n_ret50 = (n_aligned.iloc[-1] / n_aligned.iloc[-51] - 1.0) if len(n_aligned) >= 51 else 0.0
        rs_50 = (s_ret50 - n_ret50) * 100.0

        return {"rs_20": round(rs_20, 2), "rs_50": round(rs_50, 2)}
    except Exception:
        return {"rs_20": 0.0, "rs_50": 0.0}


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


def momentum_indicators(df):
    """Compute RSI, MACD, Bollinger, Volume surge from a bar DataFrame.

    Returns a dict with the latest values of each indicator, ready to
    feed into momentum_composite(). Returns None on insufficient data.
    """
    import pandas as pd

    if df is None or len(df) < 50:
        return None
    close = df["Close"]
    volume = df["Volume"] if "Volume" in df.columns else pd.Series(0, index=df.index)

    rsi_s = compute_rsi(close, 14)
    macd_line, signal_line, histogram = compute_macd(close)
    bb_mid, bb_upper, bb_lower, bb_width = compute_bollinger(close)
    vol_ratio_s = compute_volume_surge(volume)

    # Compute rolling average of BB width for squeeze detection
    bb_width_avg = bb_width.rolling(50).mean()

    rsi_val = float(rsi_s.iloc[-1]) if rsi_s.iloc[-1] == rsi_s.iloc[-1] else 50.0
    macd_hist_val = float(histogram.iloc[-1]) if histogram.iloc[-1] == histogram.iloc[-1] else 0.0
    bb_width_val = float(bb_width.iloc[-1]) if bb_width.iloc[-1] == bb_width.iloc[-1] else 5.0
    bb_width_avg_val = float(bb_width_avg.iloc[-1]) if pd.notna(bb_width_avg.iloc[-1]) else bb_width_val
    vol_ratio_val = float(vol_ratio_s.iloc[-1]) if vol_ratio_s.iloc[-1] == vol_ratio_s.iloc[-1] else 1.0

    return {
        "rsi": round(rsi_val, 1),
        "macd_histogram": round(macd_hist_val, 4),
        "bollinger_width": round(bb_width_val, 2),
        "bollinger_width_avg": round(bb_width_avg_val, 2),
        "volume_ratio": round(vol_ratio_val, 2),
    }


# ---- the replay -------------------------------------------------------------
def replay_stock(symbol: str, df, k: float, nifty_state: dict | None = None, nifty_close: pd.Series | None = None) -> dict[str, Any] | None:
    """Backtest the setup shape for one stock including Triple Barrier Method evaluation."""
    atr_s, ema_s = indicators(df)
    opens = df["Open"].tolist()
    highs = df["High"].tolist()
    lows = df["Low"].tolist()
    closes = df["Close"].tolist()
    dates = list(df.index)
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
    rs = compute_relative_strength(df, nifty_close)

    # Momentum / mean-reversion indicators
    mom = momentum_indicators(df)
    if mom is not None:
        composite = momentum_composite(
            rsi=mom["rsi"], macd_hist=mom["macd_histogram"],
            bb_width=mom["bollinger_width"], bb_width_avg=mom["bollinger_width_avg"],
            vol_ratio=mom["volume_ratio"], ema_bullish=bullish,
            atr_pct=float(atr_pct),
        )
    else:
        composite = {"score": 50.0, "direction": "NEUTRAL", "components": {}}

    cells: dict[str, dict[str, Any]] = {}
    fwd_by_hz: dict[int, list[float]] = {hz: [] for hz in HORIZONS}
    tb_by_hz: dict[int, list[tuple[int, float]]] = {hz: [] for hz in HORIZONS}

    for hz in HORIZONS:
        # Single pass per signal day per horizon
        agg: dict[float, list[tuple[bool, bool, float, bool]]] = {h: [] for h in HIT_LEVELS}
        regime_agg: dict[float, dict[str, list]] = {
            h: {"bull": [], "bear": []} for h in HIT_LEVELS
        }
        pt_mult = 2.0  # Dynamic Triple Barrier: Target = 2.0 x ATR

        for d in range(HISTORY_BARS - 1, n - hz):
            atr_d = atr_s.iloc[d]
            if not atr_d or atr_d <= 0:
                continue
            ema_d = float(ema_s.iloc[d])
            close_d = float(closes[d])
            if (direction == "BUY" and close_d <= ema_d) or (direction == "SELL" and close_d >= ema_d):
                continue  # only the trend-matched bucket

            entry = float(opens[d + 1])
            if entry != entry or entry <= 0:
                continue
            risk = k * float(atr_d)
            stop = entry - risk if direction == "BUY" else entry + risk
            tgt = {h: entry * (1 + h) if direction == "BUY" else entry * (1 - h) for h in HIT_LEVELS}

            # Triple Barrier calculation
            tb_target = entry + pt_mult * float(atr_d) if direction == "BUY" else entry - pt_mult * float(atr_d)
            tb_stop = entry - k * float(atr_d) if direction == "BUY" else entry + k * float(atr_d)
            tb_outcome = 0  # 1 = WIN (upper barrier), -1 = LOSS (lower barrier), 0 = TIME-OUT (vertical barrier)

            stop_bar: int | None = None
            tgt_bar: dict[float, int | None] = {h: None for h in HIT_LEVELS}
            hi_max = entry
            lo_min = entry
            tb_resolved = False

            for j in range(d + 1, d + hz + 1):
                lo, hi = lows[j], highs[j]
                if lo != lo or hi != hi:
                    continue
                hi_max = max(hi_max, hi)
                lo_min = min(lo_min, lo)

                if not tb_resolved:
                    if direction == "BUY":
                        if lo <= tb_stop:
                            tb_outcome = -1
                            tb_resolved = True
                        elif hi >= tb_target:
                            tb_outcome = 1
                            tb_resolved = True
                    else:
                        if hi >= tb_stop:
                            tb_outcome = -1
                            tb_resolved = True
                        elif lo <= tb_target:
                            tb_outcome = 1
                            tb_resolved = True

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
            fwd = ((final_close - entry) / entry if direction == "BUY"
                   else (entry - final_close) / entry) - COST_RATE_FUT
            fwd_by_hz[hz].append(fwd)
            tb_by_hz[hz].append((tb_outcome, fwd))

            regime_key = None
            if nifty_state is not None:
                st = nifty_state.get(dates[d].date())
                if st is not None:
                    regime_key = "bull" if st else "bear"

            for h in HIT_LEVELS:
                if tgt_bar[h] is not None and (stop_bar is None or tgt_bar[h] < stop_bar):
                    outcome, triggered, final = True, True, tgt[h]
                elif stop_bar is not None:
                    outcome, triggered, final = False, True, stop
                else:
                    outcome, triggered, final = final_close > entry, False, final_close
                gross = (final - entry) / entry if direction == "BUY" else (entry - final) / entry
                moved = (hi_max >= entry * (1 + h)) or (lo_min <= entry * (1 - h))
                rec = (outcome, triggered, gross - COST_RATE_FUT, moved)
                agg[h].append(rec)
                if regime_key is not None:
                    regime_agg[h][regime_key].append(rec)

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
            cell: dict[str, Any] = {
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
            regime_stats: dict[str, Any] = {}
            for bucket in ("bull", "bear"):
                rrecs = regime_agg[h][bucket]
                rn = len(rrecs)
                if rn == 0:
                    continue
                rwins = sum(1 for r in rrecs if r[0])
                regime_stats[bucket] = {
                    "n": rn,
                    "target_first": sum(1 for r in rrecs if r[1] and r[0]),
                    "wins": rwins,
                    "avg_pnl_pct": round(sum(r[2] for r in rrecs) / rn * 100, 2),
                }
            if regime_stats:
                cell["regime"] = regime_stats
            cells[f"{hz}:{h * 100:.1f}"] = cell

    triple_barrier: dict[str, Any] = {}
    for hz in HORIZONS:
        recs = tb_by_hz[hz]
        tb_n = len(recs)
        if tb_n == 0:
            continue
        tb_wins = sum(1 for r in recs if r[0] == 1)
        tb_losses = sum(1 for r in recs if r[0] == -1)
        tb_timeouts = sum(1 for r in recs if r[0] == 0)
        ci_low, ci_high = wilson_ci(tb_wins, tb_n)
        triple_barrier[str(hz)] = {
            "n": tb_n,
            "pt_mult": 2.0,
            "sl_mult": k,
            "wins": tb_wins,
            "losses": tb_losses,
            "timeouts": tb_timeouts,
            "win_rate": round(tb_wins / tb_n * 100, 1),
            "loss_rate": round(tb_losses / tb_n * 100, 1),
            "timeout_rate": round(tb_timeouts / tb_n * 100, 1),
            "ci_low": round(ci_low * 100, 1),
            "ci_high": round(ci_high * 100, 1),
            "avg_pnl_pct": round(sum(r[1] for r in recs) / tb_n * 100, 2),
        }

    returns: dict[str, Any] = {}
    for hz in HORIZONS:
        vals = sorted(fwd_by_hz[hz])
        nn = len(vals)
        if nn == 0:
            continue
        returns[str(hz)] = {
            "n": nn,
            "median": round(percentile_sorted(vals, 50) * 100, 2),
            "p25": round(percentile_sorted(vals, 25) * 100, 2),
            "p75": round(percentile_sorted(vals, 75) * 100, 2),
            "p10": round(percentile_sorted(vals, 10) * 100, 2),
            "p90": round(percentile_sorted(vals, 90) * 100, 2),
            "pct_positive": round(sum(1 for v in vals if v > 0) / nn * 100, 1),
            "mean": round(sum(vals) / nn * 100, 2),
            "best": round(vals[-1] * 100, 2),
            "worst": round(vals[0] * 100, 2),
        }

    return {
        "symbol": symbol,
        "close": round(close_now, 2),
        "atr_pct": round(atr_pct * 100, 2),
        "trend": "BULLISH" if bullish else "BEARISH",
        "direction": direction,
        "stop_pct": round(k * atr_pct * 100, 2),
        "rs_20": rs["rs_20"],
        "rs_50": rs["rs_50"],
        "sector": get_sector(symbol),
        "momentum": {
            "composite_score": composite["score"],
            "direction": composite["direction"],
            "rsi": mom["rsi"] if mom else None,
            "macd_histogram": mom["macd_histogram"] if mom else None,
            "volume_ratio": mom["volume_ratio"] if mom else None,
            "bollinger_width": mom["bollinger_width"] if mom else None,
            "components": composite["components"],
        },
        "returns": returns,
        "triple_barrier": triple_barrier,
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

    nifty_state = load_nifty_regime_state()
    nifty_close = load_nifty_close_series()

    stats: dict[str, Any] = {}
    skipped: list[dict[str, str]] = []
    for s in symbols:
        df = bars.get(s)
        if df is None:
            skipped.append({"symbol": s, "reason": "no price data"})
            continue
        res = replay_stock(s, df, args.stop_atr, nifty_state, nifty_close)
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
            "nifty_regime_split": nifty_state is not None,
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
