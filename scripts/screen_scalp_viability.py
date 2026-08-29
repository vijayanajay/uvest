"""Scalp Viability — "Can I actually get 0.5-1% from this stock?"

Kailash Nadh's insight: don't optimize for a target the stock can't
reach. The first question for a 1-3 day scalper isn't "is the stock
bullish?" but "does this stock MOVE enough to justify the trade?"

A stock with 55% hit rate at 4% target might have 65% hit rate at 0.5%
target — but you'd never know if you only measure at 4%.

This script answers three questions for each candidate:

    1. PROBABILITY: What's the chance of a 0.5% / 1.0% / 1.5% move
       in my direction over 1 / 2 / 3 days?
    2. VIABILITY: After transaction costs (~0.09% FUT round-trip), is
       the target realistic? What's the cost-adjusted net probability?
    3. GEOMETRY: What stop gives the best risk-reward for THIS target?
       (Not 1.5x ATR — maybe 0.7x ATR for a 0.5% target.)

For scalping, the edge isn't about picking winners. It's about picking
stocks whose daily behavior produces enough movement to cover costs and
leave profit. A low-volatility stock that moves 0.3%/day is unscalable
regardless of direction.

Output: screen_scalp_viability_out.json

All data from data_cache/ (already cached by screen_stats.py).

Usage:
    python scripts/screen_scalp_viability.py [--stats screen_stats_out.json] [--top 20]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    PROJECT_ROOT, COST_RATE_FUT, emit_json, load_json, percentile_sorted,
)

CACHE_DIR = PROJECT_ROOT / "data_cache"
STATS_FILE = PROJECT_ROOT / "screen_stats_out.json"

# Scalping targets — the actual question a 1-3 day trader asks
SCALP_TARGETS = [0.005, 0.01, 0.015]  # 0.5%, 1.0%, 1.5%
SCALP_HORIZONS = [1, 2, 3]  # 1, 2, 3 trading days


def load_bars(symbol: str) -> pd.DataFrame | None:
    """Load cached daily bars."""
    f = CACHE_DIR / f"{symbol}.csv"
    if not f.exists():
        return None
    try:
        df = pd.read_csv(f, parse_dates=["Date"], index_col="Date")
        if len(df) >= 60:  # need enough history for distribution
            return df
    except Exception:
        pass
    return None


def compute_scalp_distribution(df: pd.DataFrame, direction: str = "BUY") -> dict[str, Any]:
    """Compute the forward-return distribution at scalping horizons.

    For each (horizon, target) pair, answers:
        - What % of windows produce a move of at least `target` in `direction`?
        - What's the median move? P10? P90?
        - What's the cost-adjusted net probability?

    Nadh insight: we're not looking for the best stock. We're looking for
    stocks where the DISTRIBUTION of forward returns is wide enough to
    include our target profit with reasonable probability.
    """
    close = df["Close"]
    n = len(close)

    results: dict[str, Any] = {}

    for hz in SCALP_HORIZONS:
        if n < hz + 5:
            continue

        fwd_returns: list[float] = []
        for i in range(n - hz):
            entry = float(close.iloc[i])
            exit_ = float(close.iloc[i + hz])
            if entry <= 0:
                continue
            if direction == "BUY":
                fwd = (exit_ - entry) / entry
            else:
                fwd = (entry - exit_) / entry
            fwd_returns.append(fwd)

        if not fwd_returns:
            continue

        sorted_rets = sorted(fwd_returns)
        n_windows = len(sorted_rets)

        # Distribution stats
        median = percentile_sorted(sorted_rets, 50) * 100
        p10 = percentile_sorted(sorted_rets, 10) * 100
        p25 = percentile_sorted(sorted_rets, 25) * 100
        p75 = percentile_sorted(sorted_rets, 75) * 100
        p90 = percentile_sorted(sorted_rets, 90) * 100
        mean = sum(sorted_rets) / n_windows * 100

        # Hit rates at each scalp target
        target_hits: dict[str, dict[str, Any]] = {}
        for target in SCALP_TARGETS:
            hits = sum(1 for r in fwd_returns if r >= target)
            hit_rate = hits / n_windows

            # Cost-adjusted: net profit = target - costs
            net_target = target - COST_RATE_FUT
            net_hits = sum(1 for r in fwd_returns if r >= net_target)
            net_hit_rate = net_hits / n_windows

            # Stop-adjusted: what if we use a stop? (assume stop = target/1.5 for 1.5:1 R:R)
            stop_distance = target / 1.5
            # A "scalp win" = hit target before hitting stop (simplified: look at
            # the distribution of returns; positive returns >= target are wins,
            # negative returns <= -stop are losses)
            scalp_wins = sum(1 for r in fwd_returns if r >= target)
            scalp_losses = sum(1 for r in fwd_returns if r <= -stop_distance)
            scalp_other = n_windows - scalp_wins - scalp_losses

            target_hits[f"{target * 100:.1f}%"] = {
                "gross_hit_rate": round(hit_rate * 100, 1),
                "net_hit_rate": round(net_hit_rate * 100, 1),
                "net_target": round(net_target * 100, 2),
                "scalp_wins": scalp_wins,
                "scalp_losses": scalp_losses,
                "scalp_other": scalp_other,
            }

        results[str(hz)] = {
            "n_windows": n_windows,
            "median": round(median, 2),
            "p10": round(p10, 2),
            "p25": round(p25, 2),
            "p75": round(p75, 2),
            "p90": round(p90, 2),
            "mean": round(mean, 2),
            "pct_positive": round(sum(1 for r in fwd_returns if r > 0) / n_windows * 100, 1),
            "targets": target_hits,
        }

    return results


def compute_optimal_stop(close: float, atr_pct: float, target_pct: float,
                         direction: str = "BUY") -> dict[str, Any]:
    """Compute the optimal stop for a given target, Nadh-style.

    Nadh insight: the stop should be based on the STOCK'S BEHAVIOR,
    not an arbitrary multiplier. If a stock's average daily range is
    1.2% and you want 0.5% profit, a stop at 0.75% (below the average
    daily range) gives you room to breathe.

    Returns the stop price, the R:R, and whether the trade is geometrically
    viable for the given target.
    """
    atr = close * atr_pct / 100.0
    target = close * target_pct

    # Option 1: 1x ATR stop (standard)
    stop_1atr = atr
    rr_1atr = round(target / stop_1atr, 2) if stop_1atr > 0 else 0

    # Option 2: 0.7x ATR stop (tighter, for scalping)
    stop_07atr = atr * 0.7
    rr_07atr = round(target / stop_07atr, 2) if stop_07atr > 0 else 0

    # Option 3: below previous day's low proxy (1.2x ATR)
    stop_12atr = atr * 1.2
    rr_12atr = round(target / stop_12atr, 2) if stop_12atr > 0 else 0

    # Best R:R among viable stops (must be >= 1.0)
    options = [
        {"stop_atr_mult": 0.7, "stop_distance": round(stop_07atr, 2), "rr": rr_07atr},
        {"stop_atr_mult": 1.0, "stop_distance": round(stop_1atr, 2), "rr": rr_1atr},
        {"stop_atr_mult": 1.2, "stop_distance": round(stop_12atr, 2), "rr": rr_12atr},
    ]
    viable = [o for o in options if o["rr"] >= 1.0]
    best = max(viable, key=lambda o: o["rr"]) if viable else options[0]

    return {
        "target_pct": round(target_pct * 100, 2),
        "target_price": round(close + target if direction == "BUY" else close - target, 2),
        "options": options,
        "best_stop": best,
        "geometrically_viable": best["rr"] >= 1.0,
    }


def scalp_viability_score(distribution: dict[str, Any], atr_pct: float) -> dict[str, Any]:
    """Compute a single 0-100 scalp viability score.

    Nadh-style: simple, interpretable, no overfitting.

    Score components:
        1. Can the stock reach 0.5% in 1 day? (30 pts)
        2. Can the stock reach 1.0% in 2 days? (25 pts)
        3. Is the median return positive at 2-day horizon? (20 pts)
        4. Is the P10 (downside) acceptable? (15 pts)
        5. Is volatility in the right range? (10 pts)
    """
    score = 0.0
    components: dict[str, Any] = {}

    # 1. 1-day hit rate at 0.5% (the bread-and-butter scalp)
    d1 = distribution.get("1", {})
    t05_d1 = d1.get("targets", {}).get("0.5%", {})
    hit_05_1d = t05_d1.get("net_hit_rate", 0)
    if hit_05_1d >= 55:
        s = 30
    elif hit_05_1d >= 45:
        s = 20
    elif hit_05_1d >= 35:
        s = 10
    else:
        s = 0
    score += s
    components["1d_0.5pct_hit"] = {"value": hit_05_1d, "points": s,
                                    "detail": f"{hit_05_1d:.0f}% net hit rate at 0.5% in 1 day"}

    # 2. 2-day hit rate at 1.0%
    d2 = distribution.get("2", {})
    t10_d2 = d2.get("targets", {}).get("1.0%", {})
    hit_10_2d = t10_d2.get("net_hit_rate", 0)
    if hit_10_2d >= 50:
        s = 25
    elif hit_10_2d >= 40:
        s = 15
    elif hit_10_2d >= 30:
        s = 8
    else:
        s = 0
    score += s
    components["2d_1.0pct_hit"] = {"value": hit_10_2d, "points": s,
                                    "detail": f"{hit_10_2d:.0f}% net hit rate at 1.0% in 2 days"}

    # 3. Median 2-day return is positive
    median_2d = d2.get("median", 0)
    if median_2d > 0.5:
        s = 20
    elif median_2d > 0:
        s = 12
    elif median_2d > -0.3:
        s = 5
    else:
        s = 0
    score += s
    components["median_2d_return"] = {"value": median_2d, "points": s,
                                       "detail": f"Median 2-day return {median_2d:+.2f}%"}

    # 4. P10 (downside) — how bad can it get?
    p10_2d = d2.get("p10", 0)
    if p10_2d > -0.5:
        s = 15  # even the bad trades are small losses
    elif p10_2d > -1.0:
        s = 10
    elif p10_2d > -2.0:
        s = 5
    else:
        s = 0  # downside is too large for scalping
    score += s
    components["p10_downside"] = {"value": p10_2d, "points": s,
                                   "detail": f"P10 (worst 10%) at 2 days: {p10_2d:+.2f}%"}

    # 5. Volatility in the sweet spot (ATR 0.8-2.5% is ideal for scalping)
    if 0.8 <= atr_pct <= 2.5:
        s = 10
    elif 0.5 <= atr_pct <= 3.5:
        s = 5
    else:
        s = 0  # too quiet or too wild
    score += s
    components["volatility_sweet_spot"] = {"value": round(atr_pct, 2), "points": s,
                                            "detail": f"ATR {atr_pct:.1f}% — {'sweet spot' if 0.8 <= atr_pct <= 2.5 else 'suboptimal'}"}

    score = max(0, min(100, score))

    # Verdict
    if score >= 65:
        verdict = "SCALPABLE"
        recommendation = "This stock moves enough for 0.5-1% scalping. Good geometry."
    elif score >= 40:
        verdict = "MARGINAL"
        recommendation = "Possible but tighter. Reduce size, use limit orders only."
    else:
        verdict = "NOT_SCALPABLE"
        recommendation = "This stock doesn't move enough for reliable 0.5-1% scalps. Skip."

    return {
        "score": round(score, 1),
        "verdict": verdict,
        "recommendation": recommendation,
        "components": components,
    }


def analyze_stock(symbol: str, stats_row: dict[str, Any]) -> dict[str, Any] | None:
    """Full scalp viability analysis for one stock."""
    df = load_bars(symbol)
    if df is None:
        return None

    close = float(df["Close"].iloc[-1])
    atr_pct = stats_row.get("atr_pct", 0.0)
    direction = stats_row.get("direction", "BUY")

    # Forward return distribution at scalp horizons
    distribution = compute_scalp_distribution(df, direction)

    # Optimal stop for the primary target (0.75% — the sweet spot)
    stop_analysis = compute_optimal_stop(close, atr_pct, 0.0075, direction)

    # Scalp viability score
    viability = scalp_viability_score(distribution, atr_pct)

    return {
        "symbol": symbol,
        "close": round(close, 2),
        "direction": direction,
        "atr_pct": round(atr_pct, 2),
        "viability": viability,
        "distribution": distribution,
        "stop_analysis": stop_analysis,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Scalp Viability — can I get 0.5-1% from this?")
    parser.add_argument("--stats", default=str(STATS_FILE),
                        help="Path to screen_stats_out.json")
    parser.add_argument("--top", type=int, default=20,
                        help="Analyze top N candidates (default: 20)")
    parser.add_argument("--symbol", help="Single stock symbol (for prompt.md workflow)")
    args = parser.parse_args()

    # Single-symbol mode
    if args.symbol:
        sym = args.symbol.upper().replace(".NS", "")
        # Minimal stats row
        stats_row = {"atr_pct": 0.0, "direction": "BUY"}
        # Try to get ATR from cached bars
        df = load_bars(sym)
        if df is not None and len(df) >= 14:
            import pandas as pd
            high, low, close = df["High"], df["Low"], df["Close"]
            prev_close = close.shift(1)
            tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
            atr = float(tr.rolling(14).mean().iloc[-1])
            stats_row["atr_pct"] = round(atr / float(close.iloc[-1]) * 100, 2)
            stats_row["direction"] = "BUY" if float(close.iloc[-1]) > float(close.ewm(span=50, adjust=False).mean().iloc[-1]) else "SELL"

        result = analyze_stock(sym, stats_row)
        if result is None:
            emit_json({"error": f"Could not analyze {sym} — no price data"})
            return 1
        payload = {
            "generated_on": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "mode": "single",
            "stocks": [result],
        }
        emit_json(payload)
        return 0

    # Batch mode
    try:
        stats = load_json(Path(args.stats))
    except FileNotFoundError:
        emit_json({"error": f"Stats file not found: {args.stats}"})
        return 1

    symbols = stats.get("symbols", {})
    results: list[dict[str, Any]] = []

    # Analyze top stocks by momentum score (most likely to move)
    sorted_syms = sorted(
        symbols.items(),
        key=lambda x: x[1].get("momentum", {}).get("composite_score", 50),
        reverse=True,
    )[:args.top]

    for sym, st in sorted_syms:
        result = analyze_stock(sym, st)
        if result is not None:
            results.append(result)

    # Sort by viability score
    results.sort(key=lambda r: r["viability"]["score"], reverse=True)

    # Summary stats
    scalapable = sum(1 for r in results if r["viability"]["verdict"] == "SCALPABLE")
    marginal = sum(1 for r in results if r["viability"]["verdict"] == "MARGINAL")

    payload = {
        "generated_on": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "mode": "batch",
        "analyzed": len(results),
        "summary": {
            "scalpable": scalapable,
            "marginal": marginal,
            "not_scalpable": len(results) - scalapable - marginal,
        },
        "stocks": results,
        "note": (
            "Scalp viability measures whether a stock's daily behavior produces "
            "enough movement for 0.5-1% scalping after costs. A stock can be "
            "BULLISH but NOT_SCALPABLE if it doesn't move enough."
        ),
    }
    emit_json(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
