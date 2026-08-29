"""Entry Readiness — should I enter this stock TODAY?

For 1-3 day directional trades targeting 0.5-1%, the question isn't
"are the indicators bullish?" but "is this stock ready to MOVE today?"

Uses daily OHLCV (free, from yfinance or cache) to compute five
Nadh-style pragmatic signals:

    1. Opening position: where did the stock open vs yesterday's range?
       (gap-up in uptrend = continuation; gap-down = pullback entry)
    2. Close strength: (Close - Low) / (High - Low) — how much of the
       day's range did buyers capture? Upper 60% = strong close.
    3. Volume confirmation: volume ratio vs 20-day average. >1.5x = real
       interest, not just drift.
    4. Momentum acceleration: is the 3-day return faster than the
       10-day return? Acceleration = the move is building.
    5. Trend alignment: is the stock above its 5-day and 20-day EMA?
       Multiple timeframe confirmation from daily bars alone.

Composite score 0-100. >70 = READY, 40-69 = CONDITIONAL, <40 = NOT READY.

Output: screen_entry_readiness_out.json

Usage:
    python scripts/screen_entry_readiness.py --rank screen_rank_out.json [--top 10]
    python scripts/screen_entry_readiness.py --symbol BHARTIARTL
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    PROJECT_ROOT, emit_json, load_json, get_sector,
    compute_rsi, compute_macd, compute_bollinger, compute_volume_surge,
)

RANK_FILE = PROJECT_ROOT / "screen_rank_out.json"
CACHE_DIR = PROJECT_ROOT / "data_cache"


def load_bars(symbol: str, allow_download: bool = False):
    """Load cached daily bars, or download if allowed."""
    import pandas as pd

    f = CACHE_DIR / f"{symbol}.csv"
    if f.exists():
        try:
            df = pd.read_csv(f, parse_dates=["Date"], index_col="Date")
            if len(df) >= 50:
                return df
        except Exception:
            pass

    if not allow_download:
        return None

    try:
        import yfinance as yf
        df = yf.Ticker(f"{symbol}.NS").history(period="1y", interval="1d")
        if df is not None and not df.empty and len(df) >= 50:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            df.to_csv(CACHE_DIR / f"{symbol}.csv", date_format="%Y-%m-%d")
            return df
    except Exception:
        pass
    return None


def opening_position(df) -> dict[str, Any]:
    """Signal 1: Where did today's open fall vs yesterday's range?

    Nadh insight: the open tells you what happened overnight — was there
    a gap? Did it fill? A stock that gaps up 0.3% and holds = continuation.
    A stock that gaps up 1% and fills the gap = failed breakout.

    Score 0-20:
        - Open in upper 50% of yesterday's range = bullish = +15-20
        - Open in middle = neutral = +8-12
        - Open in lower 50% = bearish = +0-5 (but if trending UP,
          a pullback to yesterday's low = entry opportunity)
    """
    if len(df) < 2:
        return {"score": 10, "signal": "UNKNOWN", "detail": "Insufficient data"}

    today = df.iloc[-1]
    yesterday = df.iloc[-2]

    today_open = float(today["Open"])
    yest_high = float(yesterday["High"])
    yest_low = float(yesterday["Low"])
    yest_range = yest_high - yest_low

    if yest_range <= 0:
        return {"score": 10, "signal": "UNKNOWN", "detail": "Yesterday had no range"}

    # Position within yesterday's range (0 = at low, 1 = at high)
    position = (today_open - yest_low) / yest_range

    # Gap from yesterday's close
    yest_close = float(yesterday["Close"])
    gap_pct = (today_open - yest_close) / yest_close * 100

    # Score: upper position = bullish continuation; lower = pullback
    if position >= 0.7:
        score, signal = 18, "GAP_UP_CONTINUATION"
        detail = f"Opened at {position:.0%} of yesterday's range (gap {gap_pct:+.2f}%) — buyers in control"
    elif position >= 0.5:
        score, signal = 14, "UPPER_RANGE"
        detail = f"Opened in upper half of yesterday's range (gap {gap_pct:+.2f}%)"
    elif position >= 0.3:
        score, signal = 10, "MID_RANGE"
        detail = f"Opened mid-range (gap {gap_pct:+.2f}%) — neutral"
    else:
        score, signal = 5, "PULLBACK_OPEN"
        detail = f"Opened at {position:.0%} of yesterday's range (gap {gap_pct:+.2f}%) — potential pullback entry if trend is up"

    return {
        "score": score, "signal": signal,
        "position_in_range": round(position * 100, 1),
        "gap_pct": round(gap_pct, 2),
        "detail": detail,
    }


def close_strength(df) -> dict[str, Any]:
    """Signal 2: How much of today's range did buyers capture?

    (Close - Low) / (High - Low) = close_strength.
    >0.6 = strong close (buyers dominated the session)
    <0.4 = weak close (sellers dominated)
    This is the single best daily-bar predictor of next-day continuation
    for trending stocks.
    """
    if len(df) < 1:
        return {"score": 10, "signal": "UNKNOWN", "detail": "No data"}

    today = df.iloc[-1]
    o, h, l, c = float(today["Open"]), float(today["High"]), float(today["Low"]), float(today["Close"])
    day_range = h - l

    if day_range <= 0:
        return {"score": 10, "signal": "DOJI", "detail": "No range — doji day"}

    strength = (c - l) / day_range

    if strength >= 0.75:
        score, signal = 20, "STRONG_CLOSE"
        detail = f"Closed at {strength:.0%} of day's range — buyers dominated"
    elif strength >= 0.6:
        score, signal = 16, "ABOVE_AVG_CLOSE"
        detail = f"Closed in upper portion of range ({strength:.0%}) — bullish"
    elif strength >= 0.4:
        score, signal = 10, "NEUTRAL_CLOSE"
        detail = f"Closed mid-range ({strength:.0%}) — indecisive"
    elif strength >= 0.25:
        score, signal = 5, "BELOW_AVG_CLOSE"
        detail = f"Closed in lower portion ({strength:.0%}) — sellers present"
    else:
        score, signal = 2, "WEAK_CLOSE"
        detail = f"Closed near low ({strength:.0%}) — sellers dominated"

    # Bonus: if close > open (green candle), add context
    if c > o:
        candle = "bullish candle"
    elif c < o:
        candle = "bearish candle"
        score = max(0, score - 3)
    else:
        candle = "doji"

    return {
        "score": score, "signal": signal,
        "close_strength": round(strength * 100, 1),
        "candle": candle,
        "detail": f"{detail} ({candle})",
    }


def volume_confirmation(df) -> dict[str, Any]:
    """Signal 3: Is volume confirming the price move?

    Volume > 1.5x 20-day average = real institutional interest.
    Volume < 0.7x = drift, no conviction.
    For 1-3 day trades, volume confirmation is critical — a move on
    low volume is likely to reverse.
    """
    if len(df) < 20 or "Volume" not in df.columns:
        return {"score": 10, "signal": "UNKNOWN", "detail": "Insufficient volume data"}

    vol = df["Volume"]
    current_vol = float(vol.iloc[-1])
    avg_vol_20 = float(vol.tail(20).mean())

    if avg_vol_20 <= 0:
        return {"score": 10, "signal": "UNKNOWN", "detail": "Zero average volume"}

    ratio = current_vol / avg_vol_20

    if ratio >= 2.5:
        score, signal = 20, "INSTITUTIONAL_SURGE"
        detail = f"Volume {ratio:.1f}x average — institutional interest"
    elif ratio >= 1.5:
        score, signal = 16, "ABOVE_AVG_VOLUME"
        detail = f"Volume {ratio:.1f}x average — real interest"
    elif ratio >= 1.0:
        score, signal = 10, "NORMAL_VOLUME"
        detail = f"Volume {ratio:.1f}x average — standard"
    elif ratio >= 0.7:
        score, signal = 5, "LOW_VOLUME"
        detail = f"Volume {ratio:.1f}x average — thin, no conviction"
    else:
        score, signal = 2, "VERY_LOW_VOLUME"
        detail = f"Volume {ratio:.1f}x average — drift, avoid"

    return {
        "score": score, "signal": signal,
        "volume_ratio": round(ratio, 2),
        "detail": detail,
    }


def momentum_acceleration(df) -> dict[str, Any]:
    """Signal 4: Is the move accelerating?

    3-day return vs 10-day return — if the short-term return is larger
    per day than the medium-term, the move is accelerating.
    Acceleration = the trend is strengthening, not just persisting.

    For 1-3 day trades, you want to enter DURING acceleration, not
    after it peaks.
    """
    if len(df) < 15:
        return {"score": 10, "signal": "UNKNOWN", "detail": "Insufficient history"}

    closes = df["Close"]
    ret_3d = (float(closes.iloc[-1]) / float(closes.iloc[-4]) - 1) * 100 if len(closes) >= 4 else 0
    ret_10d = (float(closes.iloc[-1]) / float(closes.iloc[-11]) - 1) * 100 if len(closes) >= 11 else 0

    # Per-day rates
    daily_3d = ret_3d / 3.0
    daily_10d = ret_10d / 10.0

    if daily_10d == 0:
        accel = daily_3d  # can't compare, just use short-term
    else:
        accel = daily_3d - daily_10d

    # RSI for context
    rsi_s = compute_rsi(closes, 14)
    rsi_now = float(rsi_s.iloc[-1]) if rsi_s.iloc[-1] == rsi_s.iloc[-1] else 50.0

    if accel > 0.5:
        score, signal = 20, "ACCELERATING"
        detail = f"3d return {ret_3d:+.1f}% outpacing 10d {ret_10d:+.1f}% — momentum building"
    elif accel > 0.1:
        score, signal = 15, "MILD_ACCEL"
        detail = f"3d return {ret_3d:+.1f}% slightly above 10d trend {ret_10d:+.1f}%"
    elif accel > -0.1:
        score, signal = 10, "STEADY"
        detail = f"3d return {ret_3d:+.1f}% in line with 10d trend {ret_10d:+.1f}%"
    elif accel > -0.5:
        score, signal = 5, "DECELERATING"
        detail = f"3d return {ret_3d:+.1f}% slowing vs 10d trend {ret_10d:+.1f}%"
    else:
        score, signal = 2, "ROLLING_OVER"
        detail = f"3d return {ret_3d:+.1f}% sharply below 10d trend {ret_10d:+.1f}% — momentum dying"

    # Penalize overbought for BUY setups
    if rsi_now > 75 and accel > 0:
        score = max(0, score - 3)
        detail += f" (RSI {rsi_now:.0f} — overbought, limited upside)"

    return {
        "score": score, "signal": signal,
        "return_3d": round(ret_3d, 2),
        "return_10d": round(ret_10d, 2),
        "acceleration": round(accel, 2),
        "rsi_14": round(rsi_now, 1),
        "detail": detail,
    }


def trend_alignment(df) -> dict[str, Any]:
    """Signal 5: Multi-timeframe confirmation from daily bars.

    Price above 5-day EMA AND 20-day EMA = aligned uptrend.
    Price above 5-day but below 20-day = short-term bounce in downtrend
    (risky for 1-3 day trades).
    Price below both = downtrend, don't buy.

    This is the "free multi-timeframe confirmation" — 5-day = ~1 week,
    20-day = ~1 month, from daily data alone.
    """
    if len(df) < 25:
        return {"score": 10, "signal": "UNKNOWN", "detail": "Insufficient history"}

    closes = df["Close"]
    ema_5 = closes.ewm(span=5, adjust=False).mean()
    ema_20 = closes.ewm(span=20, adjust=False).mean()
    ema_50 = closes.ewm(span=50, adjust=False).mean()

    price = float(closes.iloc[-1])
    e5 = float(ema_5.iloc[-1])
    e20 = float(ema_20.iloc[-1])
    e50 = float(ema_50.iloc[-1])

    above_5 = price > e5
    above_20 = price > e20
    above_50 = price > e50

    if above_5 and above_20 and above_50:
        score, signal = 20, "FULLY_ALIGNED"
        detail = f"Above all EMAs (5d ₹{e5:.0f}, 20d ₹{e20:.0f}, 50d ₹{e50:.0f}) — strong multi-TF alignment"
    elif above_5 and above_20:
        score, signal = 15, "SHORT_MID_ALIGNED"
        detail = f"Above 5d and 20d EMA — short+mid term aligned, near 50d ₹{e50:.0f}"
    elif above_5:
        score, signal = 8, "SHORT_ONLY"
        detail = f"Above 5d EMA only — short-term bounce, but below 20d ₹{e20:.0f} (risky for 1-3d)"
    elif above_20:
        score, signal = 6, "MID_ONLY"
        detail = f"Above 20d but below 5d — pullback within uptrend"
    else:
        score, signal = 2, "DOWNTREND"
        detail = f"Below all EMAs — downtrend, avoid longs"

    return {
        "score": score, "signal": signal,
        "above_ema5": above_5, "above_ema20": above_20, "above_ema50": above_50,
        "ema5": round(e5, 2), "ema20": round(e20, 2), "ema50": round(e50, 2),
        "detail": detail,
    }


def compute_entry_readiness(df, symbol: str) -> dict[str, Any] | None:
    """Compute composite entry readiness score for one stock.

    Returns the full breakdown or None if insufficient data.
    """
    if df is None or len(df) < 50:
        return None

    opening = opening_position(df)
    closing = close_strength(df)
    volume = volume_confirmation(df)
    accel = momentum_acceleration(df)
    trend = trend_alignment(df)

    # Weighted composite (max 100)
    # Opening: 20, Close: 20, Volume: 20, Acceleration: 20, Trend: 20
    total = opening["score"] + closing["score"] + volume["score"] + accel["score"] + trend["score"]
    total = max(0, min(100, total))

    if total >= 70:
        verdict = "READY"
        recommendation = "Entry conditions are favorable. Multiple signals confirm today is a good entry day."
    elif total >= 40:
        verdict = "CONDITIONAL"
        recommendation = "Some conditions are met but others are missing. Use smaller size, tighter stops."
    else:
        verdict = "NOT_READY"
        recommendation = "Entry conditions are weak. Wait for better setup — don't force the trade."

    return {
        "symbol": symbol,
        "score": total,
        "verdict": verdict,
        "recommendation": recommendation,
        "signals": {
            "opening_position": opening,
            "close_strength": closing,
            "volume_confirmation": volume,
            "momentum_acceleration": accel,
            "trend_alignment": trend,
        },
        "max_points": {"opening": 20, "close": 20, "volume": 20, "acceleration": 20, "trend": 20},
        "sector": get_sector(symbol),
        "close": round(float(df["Close"].iloc[-1]), 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Entry Readiness — should I enter this stock TODAY?")
    parser.add_argument("--symbol", help="Single stock symbol (e.g. BHARTIARTL)")
    parser.add_argument("--rank", default=str(RANK_FILE),
                        help="Path to screen_rank_out.json (batch mode)")
    parser.add_argument("--top", type=int, default=10,
                        help="Analyze the top N candidates in batch mode (default: 10)")
    args = parser.parse_args()

    # Single-symbol mode
    if args.symbol:
        sym = args.symbol.upper().replace(".NS", "")
        df = load_bars(sym, allow_download=True)
        if df is None:
            emit_json({"error": f"Could not load bars for {sym}"})
            return 1
        result = compute_entry_readiness(df, sym)
        if result is None:
            emit_json({"error": f"Insufficient data for {sym}"})
            return 1
        payload = {
            "generated_on": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "mode": "single",
            "readiness": [result],
        }
        emit_json(payload)
        return 0

    # Batch mode
    try:
        rank = load_json(Path(args.rank))
    except FileNotFoundError:
        emit_json({"error": f"Rank file not found: {args.rank}"})
        return 1

    top = rank.get("top", [])[:args.top]
    results: list[dict[str, Any]] = []

    for row in top:
        sym = row["symbol"]
        df = load_bars(sym)
        if df is None:
            continue
        result = compute_entry_readiness(df, sym)
        if result is not None:
            results.append(result)

    # Sort by readiness score
    results.sort(key=lambda r: r["score"], reverse=True)

    payload = {
        "generated_on": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "mode": "batch",
        "analyzed": len(results),
        "readiness": results,
        "note": (
            "Entry readiness measures whether a stock is ready to MOVE today, "
            "not just whether the indicators are bullish. For 1-3 day trades, "
            "timing the entry is as important as picking the right stock."
        ),
    }
    emit_json(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
