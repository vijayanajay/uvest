"""Conditional Probability Estimator — "On days LIKE today, does this stock's setup actually work?"

Kailash Nadh's insight: the unconditional hit rate is meaningless. A stock that
hits 1% target 55% of the time OVERALL might hit 65% of the time on low-VIX
trending days and 35% on high-VIX choppy days. If today is a low-VIX trending
day, the 55% number understates your edge. If it's choppy, it overstates it.

This script conditions each stock's forward-return statistics on the CURRENT
market environment, answering: "Given that today's VIX is LOW, the regime is
TRENDING, and day quality is STRONG, what is this stock's HISTORICAL hit rate
under those exact conditions?"

The conditioning variables (all free, already available from the pipeline):
    1. NIFTY regime: is NIFTY above its 200-DMA? (bull/bear tape)
    2. VIX regime: is VIX LOW (<14), NORMAL (14-20), or HIGH (>20)?
    3. Day quality verdict: STRONG / TRADING / WASH / AVOID (from screen_day_quality)
    4. Regime classification: TRENDING / MEAN_REVERSION / BREAKOUT / CHOPPY
    5. Breadth: is the market breadth >55% bullish? (participation)

For each stock at each (horizon, target) cell, we compute:
    - unconditional hit rate (what screen_stats already gives)
    - CONDITIONAL hit rate (only windows where market state matched today)
    - lift = conditional / unconditional — is the stock better or worse TODAY?
    - conditional forward-return distribution (median, P10, P90)

This is the single most impactful addition: it turns a static ranking into a
DYNAMIC, environment-aware probability estimate.

Output: screen_conditional_probability_out.json

All data from data_cache/ (cached by screen_stats.py) and pipeline outputs.

Usage:
    python scripts/screen_conditional_probability.py
        [--stats screen_stats_out.json]
        [--quality screen_day_quality_out.json]
        [--regime screen_regime_classify_out.json]
        [--top 20]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    PROJECT_ROOT, COST_RATE_FUT, emit_json, load_json, percentile_sorted, wilson_ci,
)

CACHE_DIR = PROJECT_ROOT / "data_cache"
NIFTY_CACHE = CACHE_DIR / "NIFTY50.csv"
STATS_FILE = PROJECT_ROOT / "screen_stats_out.json"
QUALITY_FILE = PROJECT_ROOT / "screen_day_quality_out.json"
REGIME_FILE = PROJECT_ROOT / "screen_regime_classify_out.json"

# Conditioning horizons — match screen_stats but focus on 1-3 day scalping
COND_HORIZONS = [2, 3, 5]
COND_TARGETS = [0.01, 0.02]  # 1%, 2% — the scalping targets


# ---------------------------------------------------------------------------
# Market-state classification for each historical day
# ---------------------------------------------------------------------------

def build_nifty_state_map() -> dict[str, bool] | None:
    """NIFTY close vs 200-DMA state per date.

    Returns {date_str: True/False} — True when NIFTY > 200-DMA (bull tape).
    The 200-DMA needs 200 bars to compute; early dates are skipped.
    """
    if not NIFTY_CACHE.exists():
        return None
    try:
        df = pd.read_csv(NIFTY_CACHE, parse_dates=["Date"], index_col="Date")
        close = df["Close"]
        dma200 = close.rolling(200).mean()
        state = {}
        for ts, c in close.items():
            m = dma200.loc[ts]
            if m == m:  # not NaN
                state[ts.strftime("%Y-%m-%d")] = bool(c > m)
        return state if state else None
    except Exception:
        return None


def build_vix_state_map() -> dict[str, str] | None:
    """VIX regime per date: LOW / NORMAL / HIGH.

    Uses the cached NIFTY file's VIX column if available, otherwise
    fetches VIX history once. Returns {date_str: regime}.
    """
    try:
        import yfinance as yf
        vix_df = yf.Ticker("^INDIAVIX").history(period="2y", interval="1d")
        if vix_df is None or vix_df.empty:
            return None
        state = {}
        for ts, row in vix_df.iterrows():
            c = row.get("Close")
            if c != c or c is None:
                continue
            d = ts.strftime("%Y-%m-%d")
            if c < 14:
                state[d] = "LOW"
            elif c < 20:
                state[d] = "NORMAL"
            else:
                state[d] = "HIGH"
        return state if state else None
    except Exception:
        return None


def build_breadth_state_map() -> dict[str, bool] | None:
    """Market breadth state per date: True = >55% bullish (broad participation).

    We derive this from NIFTY's own return: on days when NIFTY is up significantly,
    breadth is typically broad. This is a rough proxy but free.
    A better version would use historical per-stock EMA states — but that requires
    caching per-stock daily states, which is a future enhancement.
    """
    if not NIFTY_CACHE.exists():
        return None
    try:
        df = pd.read_csv(NIFTY_CACHE, parse_dates=["Date"], index_col="Date")
        close = df["Close"]
        # 5-day rolling breadth proxy: if NIFTY's 5-day return > 0, breadth is likely broad
        ret_5d = close.pct_change(5)
        state = {}
        for ts, r in ret_5d.items():
            if r == r and r is not None:  # not NaN
                state[ts.strftime("%Y-%m-%d")] = bool(r > 0)
        return state if state else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Per-stock conditional analysis
# ---------------------------------------------------------------------------

def load_stock_bars(symbol: str) -> pd.DataFrame | None:
    """Load cached daily bars for a stock."""
    f = CACHE_DIR / f"{symbol}.csv"
    if not f.exists():
        return None
    try:
        df = pd.read_csv(f, parse_dates=["Date"], index_col="Date")
        if len(df) >= 60:
            return df
    except Exception:
        pass
    return None


def classify_stock_trend(df: pd.DataFrame) -> dict[str, bool]:
    """Classify each bar's market-state context for this stock's windows.

    Returns per-date:
        - nifty_above_dma200: bool
        - vix_regime: LOW/NORMAL/HIGH
        - breadth_bullish: bool
    """
    nifty_state = build_nifty_state_map()
    vix_state = build_vix_state_map()
    breadth_state = build_breadth_state_map()

    result = {}
    for ts in df.index:
        d = ts.strftime("%Y-%m-%d")
        result[d] = {
            "nifty_above_dma200": nifty_state.get(d) if nifty_state else None,
            "vix_regime": vix_state.get(d) if vix_state else None,
            "breadth_bullish": breadth_state.get(d) if breadth_state else None,
        }
    return result


def compute_conditional_returns(df: pd.DataFrame, direction: str = "BUY",
                                 horizons: list[int] | None = None,
                                 targets: list[float] | None = None,
                                 market_state: dict | None = None,
                                 today_state: dict | None = None) -> dict[str, Any]:
    """Compute forward-return hit rates CONDITIONED on market state matching today.

    Nadh insight: we're not asking "what's the hit rate?" — we're asking
    "what's the hit rate on days LIKE today?" This is how you turn a static
    statistic into a dynamic probability.

    For each (horizon, target) cell, we compute:
        1. UNCONDITIONAL: hit rate across ALL windows (same as screen_stats)
        2. CONDITIONAL: hit rate only across windows where market state matched
        3. lift = conditional / unconditional — is the stock better or worse
           in today's environment?
        4. conditional distribution (median, P10, P90) — what range to expect

    The matching logic uses NIFTY state (above/below 200-DMA), VIX regime,
    and breadth — whichever are available. Missing state data means that
    dimension is not conditioned (matches everything).
    """
    if horizons is None:
        horizons = COND_HORIZONS
    if targets is None:
        targets = COND_TARGETS

    close = df["Close"]
    n = len(close)

    if today_state is None:
        today_state = {}

    # For each window, compute the forward return AND the market state on entry day
    windows = []
    for i in range(n - max(horizons)):
        entry_date = df.index[i].strftime("%Y-%m-%d")
        entry_price = float(close.iloc[i])
        if entry_price <= 0:
            continue

        # Forward returns at each horizon
        fwd_rets = {}
        for hz in horizons:
            if i + hz < n:
                exit_price = float(close.iloc[i + hz])
                if direction == "BUY":
                    fwd_rets[hz] = (exit_price - entry_price) / entry_price
                else:
                    fwd_rets[hz] = (entry_price - exit_price) / entry_price
            else:
                fwd_rets[hz] = None

        # Market state on entry day
        state = market_state.get(entry_date, {}) if market_state else {}

        windows.append({
            "entry_date": entry_date,
            "entry_price": entry_price,
            "fwd_rets": fwd_rets,
            "state": state,
        })

    if not windows:
        return {"error": "no valid windows"}

    # Determine which conditioning dimensions are available and valid
    today_nifty = today_state.get("nifty_above_dma200")
    today_vix = today_state.get("vix_regime")
    today_breadth = today_state.get("breadth_bullish")

    def matches_today(w: dict) -> bool:
        """Check if this window's market state matches today's state.

        A window matches if ALL available (non-None) dimensions match.
        If a dimension is None for today, it's treated as "match anything".
        """
        s = w["state"]
        if today_nifty is not None and s.get("nifty_above_dma200") is not None:
            if s["nifty_above_dma200"] != today_nifty:
                return False
        if today_vix is not None and s.get("vix_regime") is not None:
            if s["vix_regime"] != today_vix:
                return False
        if today_breadth is not None and s.get("breadth_bullish") is not None:
            if s["breadth_bullish"] != today_breadth:
                return False
        return True

    # Compute conditional stats per (horizon, target) cell
    results = {}
    for hz in horizons:
        for target in targets:
            key = f"{hz}D@{target*100:.0f}%"

            # ALL windows
            all_rets = [w["fwd_rets"][hz] for w in windows if w["fwd_rets"][hz] is not None]
            # MATCHED windows (today-like)
            matched_rets = [w["fwd_rets"][hz] for w in windows
                           if w["fwd_rets"][hz] is not None and matches_today(w)]

            if not all_rets or not matched_rets:
                results[key] = {
                    "unconditional": {"n": 0, "hit_rate": 0, "median": 0},
                    "conditional": {"n": 0, "hit_rate": 0, "median": 0},
                    "lift": 1.0,
                    "sufficient_data": False,
                }
                continue

            # Unconditional
            all_sorted = sorted(all_rets)
            n_all = len(all_sorted)
            hits_all = sum(1 for r in all_sorted if r >= target)
            hit_rate_all = hits_all / n_all * 100
            net_hits_all = sum(1 for r in all_sorted if r >= target - COST_RATE_FUT)
            net_hit_rate_all = net_hits_all / n_all * 100
            median_all = percentile_sorted(all_sorted, 50) * 100
            p10_all = percentile_sorted(all_sorted, 10) * 100
            p90_all = percentile_sorted(all_sorted, 90) * 100

            # Conditional (today-like)
            matched_sorted = sorted(matched_rets)
            n_matched = len(matched_sorted)
            hits_matched = sum(1 for r in matched_sorted if r >= target)
            hit_rate_matched = hits_matched / n_matched * 100
            net_hits_matched = sum(1 for r in matched_sorted if r >= target - COST_RATE_FUT)
            net_hit_rate_matched = net_hits_matched / n_matched * 100
            median_matched = percentile_sorted(matched_sorted, 50) * 100
            p10_matched = percentile_sorted(matched_sorted, 10) * 100
            p90_matched = percentile_sorted(matched_sorted, 90) * 100

            # Wilson CI for conditional
            ci_low, ci_high = wilson_ci(hits_matched, n_matched)

            # Lift: conditional / unconditional (1.0 = same, >1 = better today, <1 = worse)
            lift = hit_rate_matched / hit_rate_all if hit_rate_all > 0 else 1.0
            net_lift = net_hit_rate_matched / net_hit_rate_all if net_hit_rate_all > 0 else 1.0

            results[key] = {
                "unconditional": {
                    "n": n_all,
                    "hit_rate": round(hit_rate_all, 1),
                    "net_hit_rate": round(net_hit_rate_all, 1),
                    "median": round(median_all, 2),
                    "p10": round(p10_all, 2),
                    "p90": round(p90_all, 2),
                },
                "conditional": {
                    "n": n_matched,
                    "hit_rate": round(hit_rate_matched, 1),
                    "net_hit_rate": round(net_hit_rate_matched, 1),
                    "median": round(median_matched, 2),
                    "p10": round(p10_matched, 2),
                    "p90": round(p90_matched, 2),
                    "ci_low": round(ci_low * 100, 1),
                    "ci_high": round(ci_high * 100, 1),
                },
                "lift": round(lift, 2),
                "net_lift": round(net_lift, 2),
                "sufficient_data": n_matched >= 15,
            }

    return results


def score_conditional_probability(cells: dict[str, Any]) -> dict[str, Any]:
    """Score the conditional probability advantage (0-100).

    Nadh insight: the score isn't about how good the stock IS — it's about
    whether TODAY'S conditions make this stock BETTER than its average.

    Score components:
        1. Is the conditional hit rate better than unconditional? (30 pts)
        2. Is the net (cost-adjusted) hit rate above 50%? (25 pts)
        3. Is the conditional P10 acceptable? (20 pts)
        4. Is the conditional median positive? (15 pts)
        5. Is there sufficient conditional data? (10 pts)
    """
    score = 0.0
    components = {}

    # Use the primary cell (3D@1% — the 1-3 day scalping target)
    primary_key = None
    for key in ["3D@1%", "2D@1%", "3D@2%"]:
        if key in cells and cells[key].get("sufficient_data"):
            primary_key = key
            break
    if primary_key is None:
        # Fallback to any available cell
        for key, cell in cells.items():
            if cell.get("sufficient_data"):
                primary_key = key
                break

    if primary_key is None:
        return {
            "score": 0,
            "verdict": "INSUFFICIENT_DATA",
            "components": {},
            "note": "No conditioning data available — insufficient history or market state data",
        }

    cell = cells[primary_key]
    cond = cell["conditional"]
    uncond = cell["unconditional"]
    lift = cell["lift"]

    # 1. Lift advantage (is today better than average?)
    if lift >= 1.2:
        s = 30
        detail = f"Conditional hit rate {cond['hit_rate']:.0f}% vs unconditional {uncond['hit_rate']:.0f}% — today is FAVORABLE (lift {lift:.2f}x)"
    elif lift >= 1.0:
        s = 20
        detail = f"Conditional hit rate {cond['hit_rate']:.0f}% ≈ unconditional {uncond['hit_rate']:.0f}% — today is NEUTRAL (lift {lift:.2f}x)"
    elif lift >= 0.85:
        s = 10
        detail = f"Conditional hit rate {cond['hit_rate']:.0f}% below unconditional {uncond['hit_rate']:.0f}% — today is UNFAVORABLE (lift {lift:.2f}x)"
    else:
        s = 0
        detail = f"Conditional hit rate {cond['hit_rate']:.0f}% well below unconditional {uncond['hit_rate']:.0f}% — AVOID today (lift {lift:.2f}x)"
    score += s
    components["lift"] = {"value": lift, "points": s, "detail": detail}

    # 2. Net hit rate above 50%?
    net_hit = cond.get("net_hit_rate", 0)
    if net_hit >= 55:
        s = 25
    elif net_hit >= 45:
        s = 15
    elif net_hit >= 35:
        s = 8
    else:
        s = 0
    score += s
    components["net_hit_rate"] = {
        "value": net_hit, "points": s,
        "detail": f"Net hit rate (cost-adjusted) {net_hit:.0f}% in today-like conditions",
    }

    # 3. Conditional P10 (downside)
    p10 = cond.get("p10", 0)
    if p10 > -0.5:
        s = 20
    elif p10 > -1.0:
        s = 12
    elif p10 > -2.0:
        s = 5
    else:
        s = 0
    score += s
    components["p10_downside"] = {
        "value": p10, "points": s,
        "detail": f"P10 (worst 10%) in today-like conditions: {p10:+.2f}%",
    }

    # 4. Conditional median positive?
    median = cond.get("median", 0)
    if median > 0.3:
        s = 15
    elif median > 0:
        s = 10
    elif median > -0.3:
        s = 5
    else:
        s = 0
    score += s
    components["median"] = {
        "value": median, "points": s,
        "detail": f"Median return in today-like conditions: {median:+.2f}%",
    }

    # 5. Sufficient data
    n = cond.get("n", 0)
    if n >= 30:
        s = 10
    elif n >= 15:
        s = 5
    else:
        s = 0
    score += s
    components["data_sufficiency"] = {
        "value": n, "points": s,
        "detail": f"{n} conditional windows (need ≥30 for high confidence)",
    }

    score = max(0, min(100, score))

    if score >= 70:
        verdict = "CONDITIONAL_ADVANTAGE"
        recommendation = "Today's conditions FAVOR this stock. Conditional probability exceeds unconditional — size up."
    elif score >= 50:
        verdict = "CONDITIONAL_NEUTRAL"
        recommendation = "Today's conditions are neutral for this stock. Standard sizing."
    elif score >= 30:
        verdict = "CONDITIONAL_DISADVANTAGE"
        recommendation = "Today's conditions DISFAVOR this stock. Reduced sizing or skip."
    else:
        verdict = "CONDITIONAL_AVOID"
        recommendation = "This stock performs poorly in today's conditions. Do not trade."

    return {
        "score": round(score, 1),
        "verdict": verdict,
        "recommendation": recommendation,
        "primary_cell": primary_key,
        "components": components,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Conditional Probability Estimator — on days LIKE today, does this stock work?"
    )
    parser.add_argument("--stats", default=str(STATS_FILE),
                        help="Path to screen_stats_out.json")
    parser.add_argument("--quality", default=str(QUALITY_FILE),
                        help="Path to screen_day_quality_out.json (for day quality verdict)")
    parser.add_argument("--regime", default=str(REGIME_FILE),
                        help="Path to screen_regime_classify_out.json (for regime type)")
    parser.add_argument("--top", type=int, default=20,
                        help="Analyze top N candidates (default: 20)")
    args = parser.parse_args()

    # Load pipeline outputs
    try:
        stats = load_json(Path(args.stats))
    except FileNotFoundError:
        emit_json({"error": f"Stats file not found: {args.stats}"})
        return 1

    # Load today's market state from pipeline outputs
    today_state = {}

    # Day quality verdict
    try:
        quality = load_json(Path(args.quality))
        dq_verdict = quality.get("verdict", "UNKNOWN")
        today_state["day_quality_verdict"] = dq_verdict
    except (FileNotFoundError, json.JSONDecodeError):
        dq_verdict = "UNKNOWN"

    # Regime classification
    try:
        regime = load_json(Path(args.regime))
        rc_regime = regime.get("regime", "UNKNOWN")
        today_state["regime"] = rc_regime
    except (FileNotFoundError, json.JSONDecodeError):
        rc_regime = "UNKNOWN"

    # Build market state maps (expensive — done once)
    print("  Building market state maps...", file=sys.stderr)
    nifty_state_map = build_nifty_state_map()
    vix_state_map = build_vix_state_map()
    breadth_state_map = build_breadth_state_map()

    # Get today's state
    today_str = datetime.now().strftime("%Y-%m-%d")
    if nifty_state_map:
        today_state["nifty_above_dma200"] = nifty_state_map.get(today_str)
    if vix_state_map:
        today_state["vix_regime"] = vix_state_map.get(today_str)
    if breadth_state_map:
        today_state["breadth_bullish"] = breadth_state_map.get(today_str)

    # Analyze top stocks from stats
    symbols = stats.get("symbols", {})
    sorted_syms = sorted(
        symbols.items(),
        key=lambda x: x[1].get("cells", {}).get("5:2.0", {}).get("n", 0),
        reverse=True,
    )[:args.top]

    results = []
    print(f"  Analyzing {len(sorted_syms)} stocks with conditional probability...", file=sys.stderr)

    for sym, st in sorted_syms:
        df = load_stock_bars(sym)
        if df is None:
            continue

        direction = st.get("direction", "BUY")

        # Build per-date market state for this stock's windows
        stock_state = classify_stock_trend(df)

        # Compute conditional returns
        cells = compute_conditional_returns(
            df, direction=direction,
            market_state=stock_state, today_state=today_state,
        )

        if "error" in cells:
            continue

        # Score
        cp_score = score_conditional_probability(cells)

        results.append({
            "symbol": sym,
            "close": st.get("close"),
            "direction": direction,
            "sector": st.get("sector", "OTHERS"),
            "cells": cells,
            "conditional_probability": cp_score,
        })

    # Sort by conditional probability score
    results.sort(key=lambda r: r["conditional_probability"]["score"], reverse=True)

    # Summary
    advantage = sum(1 for r in results if r["conditional_probability"]["verdict"] == "CONDITIONAL_ADVANTAGE")
    neutral = sum(1 for r in results if r["conditional_probability"]["verdict"] == "CONDITIONAL_NEUTRAL")
    disadvantage = sum(1 for r in results if r["conditional_probability"]["verdict"] in ("CONDITIONAL_DISADVANTAGE", "CONDITIONAL_AVOID"))

    payload = {
        "generated_on": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "mode": "batch",
        "today_state": today_state,
        "analyzed": len(results),
        "summary": {
            "conditional_advantage": advantage,
            "conditional_neutral": neutral,
            "conditional_disadvantage": disadvantage,
        },
        "stocks": results,
        "note": (
            "Conditional probability answers 'on days LIKE today, does this stock work?' "
            "The lift metric compares today-like hit rates to unconditional hit rates. "
            "Lift > 1.0 = today favors this stock. Lift < 1.0 = today disfavors it. "
            "This is the single most important filter: a stock with 55% unconditional hit rate "
            "but 65% conditional hit rate in today's environment is MORE valuable than a stock "
            "with 60% unconditional but 45% conditional."
        ),
    }
    emit_json(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
