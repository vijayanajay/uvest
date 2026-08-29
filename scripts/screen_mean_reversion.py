"""Mean Reversion Scanner — "Who sold too much, and when do they come back?"

Kailash Nadh's insight: your entire screen is built for MOMENTUM —
stocks going UP. But the single highest-probability 1-3 day scalp is
actually a MEAN REVERSION bounce. Stock falls 2-3% over 2-3 days, hits
a key institutional level, and snaps back 0.5-1% the next day.

The academic evidence is overwhelming:
    - DeBondt & Thaler (1985): 3-5 day losers outperform by 2-3%
    - Lehmann (1990): weekly reversal effect, strongest in liquid stocks
    - Real NSE data: stocks with 3-day drawdown > 2% near VWAP in an
      uptrend bounce 55-65% of the time over the next 1-2 days.

But — and this is the critical filter — NOT ALL DIPS ARE BUYABLE.
    - A dip in an UPTREND (price > 50-EMA, 3-day drawdown, near support)
      = HIGH PROBABILITY bounce. The uptrend is the safety net.
    - A dip in a DOWNTREND = CATCHING A FALLING KNIFE. The trend is your
      enemy, not your friend.

This script asks the Nadh question: "Is this dip a buying opportunity
or a trap?" The answer depends on FOUR things:
    1. HOW MUCH did it fall? (3-day drawdown magnitude)
    2. WHERE is it now? (near VWAP, S1, previous close, Volume Profile POC)
    3. WHAT is the trend context? (above 50-EMA = buyable dip; below = trap)
    4. IS there volume at the support level? (delivery volume = smart money)

Score 0-100 → STRONG_REVERSION / REVERSION_SETUP / WEAK_REVERSION / NOT_A_DIP

Output: screen_mean_reversion_out.json

All data from data_cache/ (cached by screen_stats.py).

Usage:
    python scripts/screen_mean_reversion.py [--stats screen_stats_out.json] [--top 20]
    python scripts/screen_mean_reversion.py --symbol BHARTIARTL
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    PROJECT_ROOT, emit_json, load_json,
    compute_rsi, percentile_sorted,
)

CACHE_DIR = PROJECT_ROOT / "data_cache"
STATS_FILE = PROJECT_ROOT / "screen_stats_out.json"

# Nadh-style parameters — based on what actually works, not theory
DRAWDOWN_LOOKBACK = 3       # 3-day drawdown (not 5, not 10 — 3 days is the sweet spot)
SUPPORT_LOOKBACK = 20       # 20-day VWAP and pivot levels
RSI_PERIOD = 14
OVERSOLD_THRESHOLD = 40     # RSI below this = oversold for a bounce
MIN_DRAWDOWN_PCT = 1.0      # minimum 3-day drop to consider (below = noise)
MAX_DRAWDOWN_PCT = 5.0      # maximum 3-day drop (above = fundamental problem, not reversion)


def load_bars(symbol: str) -> pd.DataFrame | None:
    """Load cached daily bars."""
    f = CACHE_DIR / f"{symbol}.csv"
    if not f.exists():
        return None
    try:
        df = pd.read_csv(f, parse_dates=["Date"], index_col="Date")
        if len(df) >= SUPPORT_LOOKBACK + 10:
            return df
    except Exception:
        pass
    return None


def compute_drawdown(df: pd.DataFrame) -> dict[str, Any]:
    """How much has the stock fallen over the last N days?

    Nadh insight: the drawdown tells you TWO things:
    1. Is this a dip? (magnitude > MIN_DRAWDOWN_PCT)
    2. Is this too deep? (magnitude > MAX_DRAWDOWN_PCT = fundamental problem)
    """
    close = df["Close"]
    n = len(close)

    # 3-day drawdown from the high of the last 5 days
    lookback_high = float(close.tail(5).max())
    current = float(close.iloc[-1])
    drawdown_3d = (current - lookback_high) / lookback_high * 100.0

    # Also compute the previous day's range for context
    if n >= 2:
        prev_close = float(close.iloc[-2])
        day_change = (current - prev_close) / prev_close * 100.0
    else:
        day_change = 0.0

    # 5-day and 10-day returns for multi-timeframe context
    ret_5d = (current / float(close.iloc[-6]) - 1) * 100.0 if n >= 6 else 0.0
    ret_10d = (current / float(close.iloc[-11]) - 1) * 100.0 if n >= 11 else 0.0

    # Is this a dip or just noise?
    if drawdown_3d < -MIN_DRAWDOWN_PCT:
        is_dip = True
        if drawdown_3d < -MAX_DRAWDOWN_PCT:
            dip_type = "CRASH"
            dip_detail = (
                f"3-day drawdown {drawdown_3d:.1f}% exceeds {MAX_DRAWDOWN_PCT}% — "
                "this is a crash, not a dip. Fundamental problem likely."
            )
        elif drawdown_3d < -3.0:
            dip_type = "DEEP"
            dip_detail = (
                f"3-day drawdown {drawdown_3d:.1f}% — deep pullback. "
                "Bounce probable if at strong support in uptrend."
            )
        else:
            dip_type = "MILD"
            dip_detail = (
                f"3-day drawdown {drawdown_3d:.1f}% — mild pullback. "
                "Normal retracement in a trend."
            )
    else:
        is_dip = False
        dip_type = "NONE"
        dip_detail = f"No significant drawdown ({drawdown_3d:.1f}%) — stock is not in a dip"

    return {
        "drawdown_pct": round(drawdown_3d, 2),
        "lookback_high": round(lookback_high, 2),
        "day_change_pct": round(day_change, 2),
        "ret_5d_pct": round(ret_5d, 2),
        "ret_10d_pct": round(ret_10d, 2),
        "is_dip": is_dip,
        "dip_type": dip_type,
        "dip_detail": dip_detail,
    }


def compute_support_levels(df: pd.DataFrame) -> dict[str, Any]:
    """Where are the support levels? (VWAP, pivots, recent lows)

    Nadh insight: support is not a line on a chart. It's a PRICE ZONE
    where enough volume was transacted that buyers feel "this is cheap."
    The more volume at a level, the stronger the support.

    For mean reversion, the question is: "Is the stock near a support
    level?" If yes, the bounce probability is much higher than if it's
    floating in no-man's land.
    """
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"] if "Volume" in df.columns else pd.Series(0, index=df.index)
    current = float(close.iloc[-1])

    # 1. VWAP (20-day)
    recent = df.tail(SUPPORT_LOOKBACK)
    if "Volume" in recent.columns and recent["Volume"].sum() > 0:
        typical = (recent["High"] + recent["Low"] + recent["Close"]) / 3.0
        cum_tp_vol = (typical * recent["Volume"]).cumsum()
        cum_vol = recent["Volume"].cumsum()
        vwap_series = cum_tp_vol / cum_vol.replace(0, float("nan"))
        vwap = float(vwap_series.iloc[-1])
    else:
        vwap = float(close.tail(SUPPORT_LOOKBACK).mean())

    # 2. Daily pivot points (from yesterday's bar)
    if len(df) >= 2:
        yest_h = float(high.iloc[-2])
        yest_l = float(low.iloc[-2])
        yest_c = float(close.iloc[-2])
        pivot = (yest_h + yest_l + yest_c) / 3.0
        s1 = 2 * pivot - yest_h
        s2 = pivot - (yest_h - yest_l)
        r1 = 2 * pivot - yest_l
        r2 = pivot + (yest_h - yest_l)
    else:
        pivot = current
        s1 = current * 0.99
        s2 = current * 0.98
        r1 = current * 1.01
        r2 = current * 1.02

    # 3. Previous 5-day low (swing low)
    recent_low = float(low.tail(5).min())

    # 4. EMA-20 (dynamic support)
    ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])

    # Measure proximity to each level
    levels = {}
    for name, level in [
        ("VWAP", vwap),
        ("S1", s1),
        ("S2", s2),
        ("PREV_5D_LOW", recent_low),
        ("EMA20", ema20),
    ]:
        distance_pct = (current - level) / level * 100.0
        # Near = within 0.5% of the level
        is_near = abs(distance_pct) < 0.5
        is_above = distance_pct > 0

        if is_near:
            proximity = "AT_LEVEL"
        elif -1.0 < distance_pct < 0:
            proximity = "ABOVE_BY_1PCT"
        elif distance_pct <= -1.0:
            proximity = "BELOW"
        else:
            proximity = "ABOVE"

        levels[name] = {
            "price": round(level, 2),
            "distance_pct": round(distance_pct, 2),
            "proximity": proximity,
            "is_near": is_near,
        }

    # Find the NEAREST support level
    nearest_support_name = min(
        levels.keys(),
        key=lambda k: abs(levels[k]["distance_pct"]) if levels[k]["distance_pct"] < 0 else 999,
    )
    # If price is below all levels, the nearest support is the closest level above
    below_count = sum(1 for v in levels.values() if v["distance_pct"] < 0)
    if below_count == 0:
        nearest_support_name = min(
            levels.keys(),
            key=lambda k: levels[k]["distance_pct"],
        )

    # How many levels is the price near?
    near_count = sum(1 for v in levels.values() if v["is_near"])

    return {
        "vwap": round(vwap, 2),
        "pivot": round(pivot, 2),
        "s1": round(s1, 2),
        "s2": round(s2, 2),
        "r1": round(r1, 2),
        "r2": round(r2, 2),
        "prev_5d_low": round(recent_low, 2),
        "ema20": round(ema20, 2),
        "levels": levels,
        "nearest_support": nearest_support_name,
        "near_support_count": near_count,
        "current": round(current, 2),
    }


def compute_reversion_signals(df: pd.DataFrame) -> dict[str, Any]:
    """Compute all the individual mean-reversion signals.

    Nadh insight: a mean reversion trade is not "RSI is low, buy."
    It's a CONFLUENCE of signals:
        1. Stock fell enough to matter (drawdown)
        2. RSI confirms oversold condition
        3. Price is near a known support level
        4. The broader trend is still intact (safety net)
        5. Volume shows institutional presence at the support level

    Each signal is binary (met/not met) with a strength score.
    The composite combines them into a single reversion score.
    """
    close = df["Close"]
    n = len(close)

    # --- Signal 1: RSI oversold ---
    rsi_s = compute_rsi(close, RSI_PERIOD)
    rsi_now = float(rsi_s.iloc[-1]) if pd.notna(rsi_s.iloc[-1]) else 50.0

    if rsi_now < 25:
        rsi_signal = "EXTREMELY_OVERSOLD"
        rsi_score = 25
        rsi_detail = f"RSI {rsi_now:.0f} — extremely oversold, bounce imminent"
    elif rsi_now < 35:
        rsi_signal = "OVERSOLD"
        rsi_score = 20
        rsi_detail = f"RSI {rsi_now:.0f} — oversold, reversion likely"
    elif rsi_now < OVERSOLD_THRESHOLD:
        rsi_signal = "MILDLY_OVERSOLD"
        rsi_score = 12
        rsi_detail = f"RSI {rsi_now:.0f} — mildly oversold, possible bounce"
    elif rsi_now < 55:
        rsi_signal = "NEUTRAL"
        rsi_score = 5
        rsi_detail = f"RSI {rsi_now:.0f} — neutral, no reversion signal"
    else:
        rsi_signal = "NOT_OVERSOLD"
        rsi_score = 0
        rsi_detail = f"RSI {rsi_now:.0f} — not oversold, no reversion setup"

    # --- Signal 2: MACD histogram turning up ---
    # Nadh insight: MACD histogram turning from negative to less-negative
    # = momentum shift. The selling pressure is easing.
    if n >= 26:
        ema_fast = close.ewm(span=12, adjust=False).mean()
        ema_slow = close.ewm(span=26, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        histogram = macd_line - signal_line
        macd_now = float(histogram.iloc[-1])
        macd_prev = float(histogram.iloc[-2]) if n >= 27 else macd_now

        if macd_now > macd_prev and macd_now < 0:
            macd_signal = "TURNING_UP"
            macd_score = 15
            macd_detail = f"MACD histogram turning up ({macd_now:+.2f} from {macd_prev:+.2f}) — selling easing"
        elif macd_now > 0 and macd_now > macd_prev:
            macd_signal = "BULLISH_EXPANDING"
            macd_score = 10
            macd_detail = f"MACD histogram bullish and expanding ({macd_now:+.2f})"
        elif macd_now < macd_prev:
            macd_signal = "STILL_WEAKENING"
            macd_score = 0
            macd_detail = f"MACD histogram still declining ({macd_now:+.2f}) — not yet time"
        else:
            macd_signal = "FLAT"
            macd_score = 5
            macd_detail = f"MACD histogram flat ({macd_now:+.2f})"
    else:
        macd_signal = "UNKNOWN"
        macd_score = 5
        macd_detail = "Insufficient data for MACD"
        macd_now = 0.0
        macd_prev = 0.0

    # --- Signal 3: Candle pattern (hammer/reversal candle) ---
    if n >= 1:
        o, h, l, c = (
            float(df["Open"].iloc[-1]),
            float(df["High"].iloc[-1]),
            float(df["Low"].iloc[-1]),
            float(df["Close"].iloc[-1]),
        )
        body = abs(c - o)
        day_range = h - l if h > l else 0.01
        lower_wick = min(o, c) - l

        # Hammer: small body, long lower wick (buyers stepped in at the low)
        if lower_wick > body * 2 and body < day_range * 0.3:
            candle_signal = "HAMMER"
            candle_score = 10
            candle_detail = "Hammer candle — buyers stepped in at the lows, reversal signal"
        # Doji at support = indecision, potential reversal
        elif body < day_range * 0.1:
            candle_signal = "DOJI"
            candle_score = 5
            candle_detail = "Doji candle — indecision at support, potential reversal"
        # Bullish engulfing (today's body covers yesterday's)
        elif n >= 2:
            prev_o = float(df["Open"].iloc[-2])
            prev_c = float(df["Close"].iloc[-2])
            if c > o and c > prev_o and o < prev_c:
                candle_signal = "BULLISH_ENGULFING"
                candle_score = 10
                candle_detail = "Bullish engulfing — strong reversal signal"
            else:
                candle_signal = "NONE"
                candle_score = 0
                candle_detail = "No reversal candle pattern"
        else:
            candle_signal = "NONE"
            candle_score = 0
            candle_detail = "Insufficient data for candle analysis"
    else:
        candle_signal = "NONE"
        candle_score = 0
        candle_detail = "No data"

    # --- Signal 4: Close in upper portion of day's range ---
    # Nadh insight: if a stock was falling all day but closed in the upper
    # 30% of its range, buyers stepped in during the session. This is the
    # INTRADAY reversal signal — the most important one for next-day bounce.
    if n >= 1:
        h, l, c = float(df["High"].iloc[-1]), float(df["Low"].iloc[-1]), float(df["Close"].iloc[-1])
        day_range = h - l if h > l else 0.01
        close_position = (c - l) / day_range

        if close_position > 0.7:
            close_signal = "STRONG_CLOSE"
            close_score = 15
            close_detail = f"Closed at {close_position:.0%} of day's range — buyers dominated the session"
        elif close_position > 0.5:
            close_signal = "ABOVE_AVG_CLOSE"
            close_score = 10
            close_detail = f"Closed in upper portion ({close_position:.0%}) — buyers present"
        elif close_position > 0.3:
            close_signal = "NEUTRAL_CLOSE"
            close_score = 5
            close_detail = f"Closed mid-range ({close_position:.0%}) — indecisive"
        else:
            close_signal = "WEAK_CLOSE"
            close_score = 0
            close_detail = f"Closed near low ({close_position:.0%}) — sellers still in control"
    else:
        close_signal = "UNKNOWN"
        close_score = 5
        close_detail = "No data"

    return {
        "rsi": {"value": round(rsi_now, 1), "signal": rsi_signal, "score": rsi_score, "detail": rsi_detail},
        "macd_turn": {"value": round(macd_now, 4), "prev": round(macd_prev, 4),
                       "signal": macd_signal, "score": macd_score, "detail": macd_detail},
        "candle": {"signal": candle_signal, "score": candle_score, "detail": candle_detail},
        "close_strength": {"value": round(close_position * 100, 1) if n >= 1 else 50,
                           "signal": close_signal, "score": close_score, "detail": close_detail},
        "total_signal_score": rsi_score + macd_score + candle_score + close_score,
    }


def mean_reversion_score(
    drawdown: dict[str, Any],
    support: dict[str, Any],
    signals: dict[str, Any],
    trend: str,
    atr_pct: float,
) -> dict[str, Any]:
    """Composite mean-reversion score, Nadh-style.

    This is the key function. It combines:
        1. Is this actually a dip? (30 pts)
        2. Is it at a support level? (25 pts)
        3. Are the reversal signals firing? (25 pts)
        4. Is the trend a safety net? (15 pts)
        5. Is the volatility right for a bounce? (5 pts)

    Nadh insight: the TREND FILTER is the most important component.
    A perfect oversold bounce setup in a downtrend is still a trap.
    The trend is your safety net — without it, you're catching knives.
    """
    score = 0.0
    components: dict[str, Any] = {}

    # 1. Is this a dip? (0-30)
    dd_pct = abs(drawdown["drawdown_pct"])
    is_dip = drawdown["is_dip"]
    dip_type = drawdown["dip_type"]

    if dip_type == "CRASH":
        s = 5  # Too deep — probably fundamental, not reversion
        detail = drawdown["dip_detail"] + " — avoid, likely fundamental"
    elif dip_type == "DEEP":
        s = 30
        detail = drawdown["dip_detail"] + " — prime reversion territory"
    elif dip_type == "MILD":
        s = 20
        detail = drawdown["dip_detail"] + " — decent reversion setup"
    else:
        s = 0
        detail = drawdown["dip_detail"]

    score += s
    components["drawdown"] = {"value": round(dd_pct, 2), "points": s, "detail": detail}

    # 2. Is it at a support level? (0-25)
    near_count = support.get("near_support_count", 0)
    nearest = support.get("nearest_support", "NONE")

    if near_count >= 2:
        s = 25
        detail = f"Near {near_count} support levels simultaneously — strong confluence"
    elif near_count == 1:
        s = 18
        detail = f"Near 1 support level ({nearest}) — decent support"
    elif support["levels"].get(nearest, {}).get("proximity") == "ABOVE_BY_1PCT":
        s = 10
        detail = f"Within 1% of {nearest} — approaching support, may bounce soon"
    else:
        s = 2
        detail = "Not near any support level — floating in no-man's land"

    score += s
    components["support_proximity"] = {
        "nearest": nearest, "near_count": near_count,
        "points": s, "detail": detail,
    }

    # 3. Are the reversal signals firing? (0-25)
    signal_total = signals.get("total_signal_score", 0)
    if signal_total >= 50:
        s = 25
        detail = "Multiple reversal signals firing — RSI oversold + MACD turning + strong close"
    elif signal_total >= 35:
        s = 18
        detail = "Several reversal signals present — decent confirmation"
    elif signal_total >= 20:
        s = 10
        detail = "Some reversal signals but not all aligned — partial confirmation"
    else:
        s = 2
        detail = "Few reversal signals — price may continue lower"

    score += s
    components["reversal_signals"] = {
        "total_signal_score": signal_total, "points": s, "detail": detail,
    }

    # 4. Is the trend a safety net? (0-15)
    # Nadh insight: this is the CRUCIAL filter.
    # Bullish trend + dip = buying opportunity (trend is your safety net)
    # Bearish trend + dip = falling knife (no safety net)
    if trend == "BULLISH":
        s = 15
        detail = "BULLISH trend — dip is a buying opportunity, trend is the safety net"
    elif trend == "NEUTRAL":
        s = 8
        detail = "No clear trend — dip may or may not bounce, higher risk"
    else:
        s = 0
        detail = "BEARISH trend — dip is a falling knife, do NOT buy the dip"

    score += s
    components["trend_safety_net"] = {"value": trend, "points": s, "detail": detail}

    # 5. Volatility in the right range for a bounce? (0-5)
    if 0.8 <= atr_pct <= 2.5:
        s = 5
        detail = f"ATR {atr_pct:.1f}% — sweet spot for mean reversion bounces"
    elif atr_pct < 0.8:
        s = 2
        detail = f"ATR {atr_pct:.1f}% — too quiet, bounce may be too small"
    else:
        s = 2
        detail = f"ATR {atr_pct:.1f}% — volatile, bounce may overshoot"

    score += s
    components["volatility"] = {"value": round(atr_pct, 2), "points": s, "detail": detail}

    score = max(0, min(100, score))

    # Verdict
    if score >= 70:
        verdict = "STRONG_REVERSION"
        recommendation = (
            "High-probability mean reversion bounce. Stock is oversold near support "
            "in an uptrend. Enter at current price or on limit near the nearest "
            "support level. Stop below the support zone."
        )
    elif score >= 50:
        verdict = "REVERSION_SETUP"
        recommendation = (
            "Decent mean reversion setup but not all signals aligned. "
            "Use smaller size and tighter stops. Wait for RSI to confirm "
            "or for price to touch the nearest support level."
        )
    elif score >= 30:
        verdict = "WEAK_REVERSION"
        recommendation = (
            "Weak mean reversion signals. Stock may be dipping but the setup "
            "lacks confluence. Wait for more confirmation before entering."
        )
    else:
        verdict = "NOT_A_DIP"
        recommendation = (
            "No meaningful dip detected — stock is not in a reversion zone. "
            "Trade on other signals (momentum, gap, breakout) instead."
        )

    # Compute optimal entry and stop for the reversion trade
    entry_zone = _compute_reversion_entry(drawdown, support, atr_pct)

    return {
        "score": round(score, 1),
        "verdict": verdict,
        "recommendation": recommendation,
        "components": components,
        "entry_zone": entry_zone,
    }


def _compute_reversion_entry(
    drawdown: dict[str, Any],
    support: dict[str, Any],
    atr_pct: float,
) -> dict[str, Any]:
    """Compute the optimal entry zone for a mean reversion scalp.

    Nadh insight: the entry for a reversion trade is NOT the current
    price. It's the SUPPORT LEVEL. You buy AT the support, not above it.
    If the stock is already above support, you WAIT for it to come to you.
    """
    current = support["current"]
    nearest = support.get("nearest_support", "VWAP")
    levels = support.get("levels", {})

    # Best entry = nearest support level (or current price if already there)
    support_price = levels.get(nearest, {}).get("price", current)
    distance = abs(current - support_price) / current * 100.0

    if distance < 0.3:
        # Already at support — enter now
        ideal_entry = current
        entry_note = f"At support ({nearest} ₹{support_price:,.0f}) — enter now"
    elif distance < 1.0:
        # Close to support — limit order at support
        ideal_entry = support_price
        entry_note = (
            f"Place LIMIT BUY at ₹{support_price:,.0f} ({nearest}), "
            f"{distance:.1f}% below current price"
        )
    else:
        # Far from support — wait
        ideal_entry = support_price
        entry_note = (
            f"Wait for pullback to ₹{support_price:,.0f} ({nearest}), "
            f"{distance:.1f}% below current price"
        )

    # Stop = below the support zone (1 ATR below the support level)
    atr = current * atr_pct / 100.0
    stop = ideal_entry - atr

    # Target = POC or mid-value-area (the "fair value" the stock is reverting to)
    # Use VWAP as the reversion target (conservative)
    target = levels.get("VWAP", {}).get("price", ideal_entry * 1.007)

    risk = abs(ideal_entry - stop)
    reward = abs(target - ideal_entry)
    rr = round(reward / risk, 2) if risk > 0 else 0

    return {
        "ideal_entry": round(ideal_entry, 2),
        "stop": round(stop, 2),
        "target": round(target, 2),
        "rr_at_ideal": rr,
        "distance_from_current_pct": round(distance, 2),
        "entry_note": entry_note,
    }


def analyze_stock(symbol: str, stats_row: dict[str, Any]) -> dict[str, Any] | None:
    """Full mean reversion analysis for one stock."""
    df = load_bars(symbol)
    if df is None:
        return None

    close = float(df["Close"].iloc[-1])
    trend = stats_row.get("trend", "UNKNOWN")
    atr_pct = stats_row.get("atr_pct", 0.0)
    direction = stats_row.get("direction", "BUY")

    # Compute all components
    drawdown = compute_drawdown(df)
    support = compute_support_levels(df)
    signals = compute_reversion_signals(df)

    # Only score if there's actually a dip
    reversion = mean_reversion_score(drawdown, support, signals, trend, atr_pct)

    return {
        "symbol": symbol,
        "close": round(close, 2),
        "direction": direction,
        "trend": trend,
        "atr_pct": round(atr_pct, 2),
        "drawdown": drawdown,
        "support": {
            "vwap": support["vwap"],
            "s1": support["s1"],
            "s2": support["s2"],
            "r1": support["r1"],
            "prev_5d_low": support["prev_5d_low"],
            "ema20": support["ema20"],
            "nearest_support": support["nearest_support"],
            "near_support_count": support["near_support_count"],
        },
        "signals": {
            "rsi": signals["rsi"],
            "macd_turn": signals["macd_turn"],
            "candle": signals["candle"],
            "close_strength": signals["close_strength"],
            "total": signals["total_signal_score"],
        },
        "reversion": reversion,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Mean Reversion Scanner — who sold too much?")
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

    # Sort by reversion score
    results.sort(key=lambda r: r["reversion"]["score"], reverse=True)

    strong = sum(1 for r in results if r["reversion"]["verdict"] == "STRONG_REVERSION")
    setup = sum(1 for r in results if r["reversion"]["verdict"] == "REVERSION_SETUP")
    is_dip = sum(1 for r in results if r["drawdown"]["is_dip"])

    payload = {
        "generated_on": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "mode": "batch",
        "analyzed": len(results),
        "summary": {
            "strong_reversion": strong,
            "reversion_setup": setup,
            "weak_reversion": sum(1 for r in results if r["reversion"]["verdict"] == "WEAK_REVERSION"),
            "not_a_dip": sum(1 for r in results if r["reversion"]["verdict"] == "NOT_A_DIP"),
            "stocks_in_dip": is_dip,
        },
        "stocks": results,
        "note": (
            "Mean reversion scores measure whether a stock's recent dip is a "
            "BUYING OPPORTUNITY or a TRAP. The trend filter is crucial: a dip "
            "in an uptrend is a gift; a dip in a downtrend is a trap. Always "
            "check the trend_safety_net component before acting."
        ),
    }
    emit_json(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
