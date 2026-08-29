"""Volume Profile — "Where did the big money actually transact?"

Kailash Nadh's insight: every indicator you look at — RSI, MACD,
Bollinger, moving averages — is derived from price. Volume Profile is
derived from VOLUME AT PRICE. It tells you where institutions actually
built positions, not where retail traders think support is.

The Point of Control (POC) is the price level with the most volume over
the lookback period. It is the market's "fair value" — price gravitates
toward it. When price is above POC, sellers eventually push it back
down. When price is below POC, buyers eventually push it back up.

The Value Area (VA) contains 70% of all volume. It is the range where
the market agrees on value. Price outside the VA is "overvalued" or
"undervalued" relative to recent consensus.

For 1-3 day scalping, volume profile answers the question nobody else
asks: "Am I buying near where institutions accumulated, or near where
they distributed?"

Three scenarios for a BUY scalp:

    1. Price near POC in an uptrend → bounce to VAH. The institutional
       floor is right there. High-probability 0.5-1% scalp.

    2. Price just broke above VAH → continuation to next resistance.
       The breakout has volume behind it. Momentum scalp.

    3. Price fell to VAL in an uptrend → mean reversion back to POC.
       The value area acts as a trampoline. Oversold bounce.

What you DON'T want: buying near VAH in a downtrend. That's buying
at the top of the institutional distribution — the worst place to enter.

Questions this answers:
    1. WHERE is institutional support/resistance? (POC, VAH, VAL)
    2. WHERE is the stock relative to these levels? (above/below/near)
    3. HOW concentrated is volume at these levels? (strong = many
       transactions = real support; weak = thin = price blows through)

Output: screen_volume_profile_out.json

All data from data_cache/ (cached by screen_stats.py).
No external dependencies beyond pandas.

Usage:
    python scripts/screen_volume_profile.py [--stats screen_stats_out.json] [--top 20]
    python scripts/screen_volume_profile.py --symbol BHARTIARTL
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import PROJECT_ROOT, emit_json, load_json  # noqa: E402

CACHE_DIR = PROJECT_ROOT / "data_cache"
STATS_FILE = PROJECT_ROOT / "screen_stats_out.json"

# Volume profile parameters — Nadh-style: simple, robust, no overfitting
PROFILE_LOOKBACK = 20       # 20 trading days ≈ 1 calendar month
N_BINS = 50                 # price bins for the distribution
VALUE_AREA_PCT = 0.70       # 70% of volume = value area (industry standard)


def load_bars(symbol: str) -> pd.DataFrame | None:
    """Load cached daily bars."""
    f = CACHE_DIR / f"{symbol}.csv"
    if not f.exists():
        return None
    try:
        df = pd.read_csv(f, parse_dates=["Date"], index_col="Date")
        if len(df) >= PROFILE_LOOKBACK:
            return df
    except Exception:
        pass
    return None


def compute_volume_profile(df: pd.DataFrame, lookback: int = PROFILE_LOOKBACK) -> dict[str, Any]:
    """Build the volume-at-price distribution over the last `lookback` bars.

    Nadh insight: the volume profile is not a "technical indicator" — it is
    a census of where transactions happened. POC is the price where the most
    shares changed hands. VAH/VAL are the boundaries of the 70% value area.

    The key number for a scalper is NOT the absolute POC — it's the
    DISTANCE from the current price to the POC, expressed as a % of price.
    Price within 0.3% of POC = at fair value (mean reversion unlikely).
    Price 1-2% below POC = undervalued (bounce probable). Price 2%+ above
    POC = overvalued (pullback probable).
    """
    recent = df.tail(lookback)
    if len(recent) < 10:
        return {"error": "insufficient data", "bars": len(recent)}

    # Price range for binning
    high_max = float(recent["High"].max())
    low_min = float(recent["Low"].min())
    price_range = high_max - low_min

    if price_range <= 0:
        return {"error": "zero price range"}

    bin_size = price_range / N_BINS
    bin_edges = np.linspace(low_min, high_max, N_BINS + 1)
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2.0

    # Distribute each bar's volume across the price range it covered.
    # A bar with High=100, Low=98, Volume=1000 distributes volume
    # proportionally to all bins between 98 and 100. This is more
    # accurate than assigning volume only to the close-price bin.
    volume_at_price = np.zeros(N_BINS)

    for _, bar in recent.iterrows():
        h, l, v = float(bar["High"]), float(bar["Low"]), float(bar["Volume"])
        if v <= 0 or h <= l:
            continue
        # Which bins does this bar's range cover?
        for b in range(N_BINS):
            bin_lo, bin_hi = bin_edges[b], bin_edges[b + 1]
            # Overlap between bar range and bin range
            overlap_lo = max(l, bin_lo)
            overlap_hi = min(h, bin_hi)
            if overlap_hi > overlap_lo:
                # Proportional share of volume
                fraction = (overlap_hi - overlap_lo) / (h - l)
                volume_at_price[b] += v * fraction

    total_volume = volume_at_price.sum()
    if total_volume <= 0:
        return {"error": "zero total volume"}

    # Point of Control: the price bin with the highest volume
    poc_idx = int(np.argmax(volume_at_price))
    poc_price = float(bin_centers[poc_idx])
    poc_volume = float(volume_at_price[poc_idx])
    poc_pct_of_total = poc_volume / total_volume * 100.0

    # Value Area: expand outward from POC until 70% of volume is captured
    va_volume_target = total_volume * VALUE_AREA_PCT
    va_volume = poc_volume
    va_lo_idx = poc_idx
    va_hi_idx = poc_idx

    while va_volume < va_volume_target and (va_lo_idx > 0 or va_hi_idx < N_BINS - 1):
        # Expand toward the side with more volume
        vol_below = volume_at_price[va_lo_idx - 1] if va_lo_idx > 0 else 0
        vol_above = volume_at_price[va_hi_idx + 1] if va_hi_idx < N_BINS - 1 else 0
        if vol_below >= vol_above and va_lo_idx > 0:
            va_lo_idx -= 1
            va_volume += volume_at_price[va_lo_idx]
        elif va_hi_idx < N_BINS - 1:
            va_hi_idx += 1
            va_volume += volume_at_price[va_hi_idx]
        else:
            break

    val_price = float(bin_edges[va_lo_idx])   # Value Area Low
    vah_price = float(bin_edges[va_hi_idx + 1])  # Value Area High
    va_width_pct = (vah_price - val_price) / poc_price * 100.0

    # Current price context
    close = float(df["Close"].iloc[-1])

    # Distance from POC (the #1 number for a scalper)
    poc_distance_pct = (close - poc_price) / poc_price * 100.0

    # Where is price relative to the value area?
    if close > vah_price:
        position = "ABOVE_VA"
        position_detail = f"Price {abs(poc_distance_pct):.2f}% above POC — overvalued vs recent volume consensus"
    elif close < val_price:
        position = "BELOW_VA"
        position_detail = f"Price {abs(poc_distance_pct):.2f}% below POC — undervalued vs recent volume consensus"
    else:
        position = "INSIDE_VA"
        position_detail = f"Price inside value area ({val_price:.0f}-{vah_price:.0f}) — at fair value"

    # Volume concentration at POC (how "strong" is this level?)
    # Higher concentration = more transactions = stronger support/resistance
    poc_concentration = poc_pct_of_total
    if poc_concentration >= 8:
        concentration_label = "HIGH"
        concentration_detail = "POC has very high volume concentration — strong institutional level"
    elif poc_concentration >= 5:
        concentration_label = "MODERATE"
        concentration_detail = "POC has moderate concentration — decent support/resistance"
    else:
        concentration_label = "LOW"
        concentration_detail = "POC has low concentration — price may blow through this level"

    # High-volume nodes (HVNs) — other price levels with significant volume
    # These are secondary support/resistance levels
    volume_threshold = np.percentile(volume_at_price, 80)
    hvn_indices = [i for i in range(N_BINS) if volume_at_price[i] >= volume_threshold and i != poc_idx]
    hvn_levels = [round(float(bin_centers[i]), 2) for i in sorted(hvn_indices)]

    # Low-volume nodes (LVNs) — gaps in volume where price moves fast
    # Price tends to "jump" through LVNs quickly
    lvn_threshold = np.percentile(volume_at_price, 20)
    lvn_indices = [i for i in range(N_BINS) if volume_at_price[i] <= lvn_threshold]
    lvn_count = len(lvn_indices)

    # Volume profile shape — what does the distribution tell us?
    # 1. P-shaped: high volume at low prices = accumulation (bullish)
    # 2. b-shaped: high volume at high prices = distribution (bearish)
    # 3. D-shaped: symmetric = balanced (neutral)
    below_poc_vol = float(volume_at_price[:poc_idx].sum())
    above_poc_vol = float(volume_at_price[poc_idx + 1:].sum())
    vol_ratio = above_poc_vol / max(below_poc_vol, 1.0)

    if vol_ratio < 0.7:
        shape = "P_SHAPED"
        shape_detail = "More volume below POC — accumulation pattern (bullish)"
    elif vol_ratio > 1.4:
        shape = "b_SHAPED"
        shape_detail = "More volume above POC — distribution pattern (bearish)"
    else:
        shape = "D_SHAPED"
        shape_detail = "Balanced distribution — no clear accumulation/distribution"

    return {
        "lookback_days": len(recent),
        "poc": {
            "price": round(poc_price, 2),
            "volume": round(poc_volume, 0),
            "pct_of_total": round(poc_concentration, 1),
            "concentration": concentration_label,
        },
        "value_area": {
            "high": round(vah_price, 2),
            "low": round(val_price, 2),
            "width_pct": round(va_width_pct, 2),
            "volume_captured_pct": round(va_volume / total_volume * 100, 1),
        },
        "close": round(close, 2),
        "position": position,
        "poc_distance_pct": round(poc_distance_pct, 2),
        "shape": shape,
        "shape_detail": shape_detail,
        "hvn_levels": hvn_levels[:5],  # top 5 secondary levels
        "lvn_count": lvn_count,
        "concentration_detail": concentration_detail,
        "position_detail": position_detail,
    }


def volume_profile_score(profile: dict[str, Any], trend: str, atr_pct: float) -> dict[str, Any]:
    """Score the volume profile for a BUY scalp setup, Nadh-style.

    Nadh insight: volume profile alone doesn't give you a trade. It gives
    you LEVELS. The score measures how favorable the current price position
    is relative to those levels, given the trend context.

    Score components:
        1. Position relative to POC (30 pts) — near POC = neutral, below = bounce,
           above = overextended
        2. Distance from value area (25 pts) — outside VA = opportunity,
           inside = wait
        3. Trend alignment (20 pts) — bounce in uptrend = high prob,
           bounce in downtrend = catching a knife
        4. Volume concentration at POC (15 pts) — strong level = reliable,
           weak level = unreliable
        5. Profile shape (10 pts) — accumulation (P-shaped) supports bullish,
           distribution (b-shaped) warns against
    """
    score = 0.0
    components: dict[str, Any] = {}

    poc_dist = profile.get("poc_distance_pct", 0)
    position = profile.get("position", "INSIDE_VA")
    shape = profile.get("shape", "D_SHAPED")
    concentration = profile.get("poc", {}).get("concentration", "LOW")

    # 1. Position relative to POC (0-30)
    # For a BUY: negative distance (price below POC) = better entry
    # Price near POC = fair value, no edge
    # Price 1-2% below POC = sweet spot for mean reversion bounce
    if poc_dist < -1.5:
        # Deep below POC — strong mean reversion setup
        s = 30
        detail = f"Price {poc_dist:+.2f}% below POC — deep value, strong bounce candidate"
    elif poc_dist < -0.5:
        # Moderately below POC — decent setup
        s = 22
        detail = f"Price {poc_dist:+.2f}% below POC — approaching value, bounce probable"
    elif poc_dist < 0.3:
        # Near POC — fair value, neutral
        s = 12
        detail = f"Price near POC ({poc_dist:+.2f}%) — at fair value, no edge from levels"
    elif poc_dist < 1.5:
        # Above POC — overvalued relative to volume
        s = 5
        detail = f"Price {poc_dist:+.2f}% above POC — overvalued, higher risk for new longs"
    else:
        # Far above POC — distribution zone
        s = 0
        detail = f"Price {poc_dist:+.2f}% above POC — distribution zone, avoid new longs"

    score += s
    components["poc_position"] = {"value": round(poc_dist, 2), "points": s, "detail": detail}

    # 2. Distance from value area (0-25)
    # Below VAL = "undervalued" = high-probability bounce zone
    # Above VAH = "overvalued" = pullback risk
    if position == "BELOW_VA":
        s = 25
        detail = "Price below Value Area — undervalued, institutional buying zone"
    elif position == "INSIDE_VA":
        va = profile.get("value_area", {})
        # How far inside VA? Closer to VAL = better for BUY
        va_mid = (va.get("high", 0) + va.get("low", 0)) / 2
        if poc_dist < 0:
            s = 15
            detail = "Inside VA but below midline — mild buying opportunity"
        else:
            s = 8
            detail = "Inside VA but above midline — neutral, no strong edge"
    else:  # ABOVE_VA
        s = 0
        detail = "Price above Value Area — overvalued, distribution risk"

    score += s
    components["value_area_position"] = {"value": position, "points": s, "detail": detail}

    # 3. Trend alignment (0-20)
    # Nadh insight: buying a dip in an uptrend = high probability.
    # Buying a dip in a downtrend = catching a falling knife.
    # This is the CRUCIAL filter that separates smart mean reversion from
    # blind dip-buying.
    if trend == "BULLISH":
        if poc_dist < -0.5:
            s = 20
            detail = "BULLISH trend + below POC — premium mean reversion setup"
        else:
            s = 14
            detail = "BULLISH trend — level is acceptable but not compelling"
    elif trend == "BEARISH":
        s = 2
        detail = "BEARISH trend — buying below POC is catching a falling knife"
    else:
        s = 8
        detail = "No clear trend — level setup is uncertain"

    score += s
    components["trend_alignment"] = {"value": trend, "points": s, "detail": detail}

    # 4. Volume concentration at POC (0-15)
    # A POC with 8%+ of total volume is a STRONG institutional level.
    # Price will respect it. A POC with 3% is a weak level — price
    # may blow through it without bouncing.
    if concentration == "HIGH":
        s = 15
        detail = "High volume concentration at POC — strong institutional level, reliable"
    elif concentration == "MODERATE":
        s = 10
        detail = "Moderate concentration — decent level but not bulletproof"
    else:
        s = 3
        detail = "Low concentration at POC — weak level, price may not respect it"

    score += s
    components["poc_strength"] = {"value": concentration, "points": s, "detail": detail}

    # 5. Profile shape (0-10)
    if shape == "P_SHAPED":
        s = 10
        detail = "P-shaped profile — accumulation at lower prices (bullish)"
    elif shape == "D_SHAPED":
        s = 6
        detail = "D-shaped profile — balanced, no clear bias"
    else:
        s = 1
        detail = "b-shaped profile — distribution at higher prices (bearish)"

    score += s
    components["profile_shape"] = {"value": shape, "points": s, "detail": detail}

    score = max(0, min(100, score))

    # Verdict
    if score >= 70:
        verdict = "STRONG_LEVEL"
        recommendation = (
            "Price is at a strong institutional level with favorable trend context. "
            "High-probability entry zone for a 0.5-1% scalp."
        )
    elif score >= 50:
        verdict = "FAVORABLE_LEVEL"
        recommendation = (
            "Price is near a decent institutional level. Acceptable entry but "
            "use limit orders and tighter stops."
        )
    elif score >= 30:
        verdict = "NEUTRAL_LEVEL"
        recommendation = (
            "Price is at fair value relative to volume — no strong edge from levels. "
            "Trade on other signals only."
        )
    else:
        verdict = "UNFAVORABLE_LEVEL"
        recommendation = (
            "Price is in an unfavorable position relative to institutional levels. "
            "Avoid new longs — wait for a pullback to POC or VAL."
        )

    return {
        "score": round(score, 1),
        "verdict": verdict,
        "recommendation": recommendation,
        "components": components,
    }


def analyze_stock(symbol: str, stats_row: dict[str, Any]) -> dict[str, Any] | None:
    """Full volume profile analysis for one stock."""
    df = load_bars(symbol)
    if df is None:
        return None

    close = float(df["Close"].iloc[-1])
    trend = stats_row.get("trend", "UNKNOWN")
    atr_pct = stats_row.get("atr_pct", 0.0)

    profile = compute_volume_profile(df)
    if "error" in profile:
        return None

    vp_score = volume_profile_score(profile, trend, atr_pct)

    return {
        "symbol": symbol,
        "close": round(close, 2),
        "direction": stats_row.get("direction", "BUY"),
        "trend": trend,
        "atr_pct": round(atr_pct, 2),
        "profile": profile,
        "score": vp_score,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Volume Profile — where did the big money transact?")
    parser.add_argument("--stats", default=str(STATS_FILE),
                        help="Path to screen_stats_out.json")
    parser.add_argument("--top", type=int, default=20,
                        help="Analyze top N candidates (default: 20)")
    parser.add_argument("--symbol",
                        help="Single stock symbol (for prompt.md workflow)")
    args = parser.parse_args()

    # Single-symbol mode
    if args.symbol:
        sym = args.symbol.upper().replace(".NS", "")
        stats_row = {"atr_pct": 0.0, "direction": "BUY", "trend": "UNKNOWN"}
        df = load_bars(sym)
        if df is not None and len(df) >= 14:
            close_series = df["Close"]
            prev_close = close_series.shift(1)
            tr = pd.concat([
                df["High"] - df["Low"],
                (df["High"] - prev_close).abs(),
                (df["Low"] - prev_close).abs(),
            ], axis=1).max(axis=1)
            atr = float(tr.rolling(14).mean().iloc[-1])
            stats_row["atr_pct"] = round(atr / float(close_series.iloc[-1]) * 100, 2)
            ema50 = float(close_series.ewm(span=50, adjust=False).mean().iloc[-1])
            stats_row["direction"] = "BUY" if float(close_series.iloc[-1]) > ema50 else "SELL"
            stats_row["trend"] = "BULLISH" if float(close_series.iloc[-1]) > ema50 else "BEARISH"

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

    for sym, st in list(symbols.items())[:args.top]:
        result = analyze_stock(sym, st)
        if result is not None:
            results.append(result)

    # Sort by score
    results.sort(key=lambda r: r["score"]["score"], reverse=True)

    strong = sum(1 for r in results if r["score"]["verdict"] == "STRONG_LEVEL")
    favorable = sum(1 for r in results if r["score"]["verdict"] == "FAVORABLE_LEVEL")

    payload = {
        "generated_on": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "mode": "batch",
        "analyzed": len(results),
        "summary": {
            "strong_level": strong,
            "favorable_level": favorable,
            "neutral": sum(1 for r in results if r["score"]["verdict"] == "NEUTRAL_LEVEL"),
            "unfavorable": sum(1 for r in results if r["score"]["verdict"] == "UNFAVORABLE_LEVEL"),
        },
        "stocks": results,
        "note": (
            "Volume Profile reveals where institutions actually transacted. "
            "The POC is the market's fair value. Price below POC in an uptrend "
            "= high-probability mean reversion scalp. Price above POC = overvalued, "
            "higher risk for new longs."
        ),
    }
    emit_json(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
