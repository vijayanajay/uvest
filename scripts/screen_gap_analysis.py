"""Gap Analysis — "What did the market do overnight, and what does it mean?"

Kailash Nadh's insight: gaps are the market's overnight opinion. They
contain more information per unit than any technical indicator because
they represent what ALL participants decided while you were sleeping.

For 1-3 day scalping, gap behavior is the #1 predictor:

    - Stock gaps up 1% and HOLDS above the gap → high probability
      continuation. The gap represents institutional buying that hasn't
      finished.

    - Stock gaps up 1% and FILLS the gap → failed breakout. The gap
      was a trap. But a filled gap in an uptrend = pullback entry.

    - Stock gaps down 1% and holds → distribution. Don't catch the
      falling knife.

    - Stock gaps down 1% and fills → classic mean reversion. The gap
      was overreaction.

The three questions:
    1. What happened yesterday? (gap size, direction, fill status)
    2. What usually happens after this kind of gap? (historical rates)
    3. Is today a follow-through day or a reversal day?

Output: screen_gap_analysis_out.json

All data from data_cache/ (cached by screen_stats.py).

Usage:
    python scripts/screen_gap_analysis.py [--stats screen_stats_out.json] [--top 20]
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
from common import PROJECT_ROOT, emit_json, load_json  # noqa: E402

CACHE_DIR = PROJECT_ROOT / "data_cache"
STATS_FILE = PROJECT_ROOT / "screen_stats_out.json"

# Gap classification thresholds
GAP_MIN = 0.3    # minimum gap to consider (below this = noise)
GAP_MEDIUM = 0.8 # medium gap
GAP_LARGE = 1.5  # large gap (institutional)


def load_bars(symbol: str) -> pd.DataFrame | None:
    """Load cached daily bars."""
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


def classify_gap(open_price: float, prev_close: float, high: float, low: float,
                 close: float) -> dict[str, Any]:
    """Classify yesterday's gap and its resolution.

    Nadh insight: the gap itself is just the opening opinion. The CLOSING
    opinion (did it hold or fill?) is what matters for the next day.

    A gap that holds = conviction. A gap that fills = indecision.
    """
    if prev_close <= 0:
        return {"has_gap": False, "reason": "no previous close"}

    gap_pct = (open_price - prev_close) / prev_close * 100
    abs_gap = abs(gap_pct)

    if abs_gap < GAP_MIN:
        return {
            "has_gap": False,
            "gap_pct": round(gap_pct, 2),
            "reason": f"Gap {gap_pct:+.2f}% is below threshold ({GAP_MIN}%)",
        }

    # Direction
    direction = "UP" if gap_pct > 0 else "DOWN"

    # Gap fill check: did the price trade through the gap during the day?
    # A gap "fills" when price trades back to (or through) the previous close
    if direction == "UP":
        filled = low <= prev_close
        # Partial fill: how much of the gap was filled?
        fill_pct = max(0, (open_price - prev_close) - max(0, low - prev_close)) / max(0.01, open_price - prev_close) * 100
    else:
        filled = high >= prev_close
        fill_pct = max(0, (prev_close - open_price) - max(0, prev_close - high)) / max(0.01, prev_close - open_price) * 100

    # Close position relative to gap
    if direction == "UP":
        # Close above open = held the gap (strong)
        # Close below open = gave back some gains
        # Close below prev_close = full reversal
        if close > open_price:
            close_position = "HELD_AND_EXPANDED"
        elif close > prev_close:
            close_position = "HELD_PARTIAL"
        else:
            close_position = "REVERSED"
    else:
        if close < open_price:
            close_position = "HELD_AND_EXPANDED"
        elif close < prev_close:
            close_position = "HELD_PARTIAL"
        else:
            close_position = "REVERSED"

    # Gap size category
    if abs_gap >= GAP_LARGE:
        size = "LARGE"
    elif abs_gap >= GAP_MEDIUM:
        size = "MEDIUM"
    else:
        size = "SMALL"

    return {
        "has_gap": True,
        "gap_pct": round(gap_pct, 2),
        "abs_gap_pct": round(abs_gap, 2),
        "direction": direction,
        "size": size,
        "filled": filled,
        "fill_pct": round(fill_pct, 1),
        "close_position": close_position,
        "prev_close": round(prev_close, 2),
        "open": round(open_price, 2),
        "high": round(high, 2),
        "low": round(low, 2),
        "close": round(close, 2),
    }


def historical_gap_rates(df: pd.DataFrame, direction: str = "BUY") -> dict[str, Any]:
    """Compute historical gap behavior rates.

    For each gap type (UP/DOWN × size), what happens next?

    Nadh insight: you don't need a model. You need a base rate.
    "When a stock gaps up 1% and holds, what % of the time does it
    continue up the next day?" That's the number that matters.
    """
    if len(df) < 30:
        return {"insufficient_data": True}

    closes = df["Close"].tolist()
    opens = df["Open"].tolist()
    highs = df["High"].tolist()
    lows = df["Low"].tolist()
    n = len(df)

    # Categorize all historical gaps
    gap_up_hold = []      # gaps up that held (close > open)
    gap_up_fill = []      # gaps up that filled (close < prev_close)
    gap_down_hold = []    # gaps down that held (close < open)
    gap_down_fill = []    # gaps down that filled (close > prev_close)

    for i in range(1, n):
        prev_c = closes[i - 1]
        if prev_c <= 0:
            continue

        gap_pct = (opens[i] - prev_c) / prev_c * 100
        abs_gap = abs(gap_pct)

        if abs_gap < GAP_MIN:
            continue

        # Did it fill? (price traded back to prev_close)
        if gap_pct > 0:  # gap up
            filled = lows[i] <= prev_c
            held = closes[i] > opens[i]
        else:  # gap down
            filled = highs[i] >= prev_c
            held = closes[i] < opens[i]

        # Forward return (next day)
        if i + 1 < n:
            fwd = (closes[i + 1] - closes[i]) / closes[i]
        else:
            fwd = 0.0

        record = {
            "gap_pct": round(gap_pct, 2),
            "filled": filled,
            "held": held,
            "fwd_return": round(fwd * 100, 2),
            "fwd_positive": fwd > 0,
        }

        if gap_pct > 0:
            if held:
                gap_up_hold.append(record)
            else:
                gap_up_fill.append(record)
        else:
            if held:
                gap_down_hold.append(record)
            else:
                gap_down_fill.append(record)

    def _summarize(group: list[dict], label: str) -> dict[str, Any]:
        if not group:
            return {"count": 0, "label": label}
        n = len(group)
        fwd_rets = [r["fwd_return"] for r in group]
        return {
            "count": n,
            "label": label,
            "next_day_continuation": round(sum(1 for r in group if r["fwd_positive"]) / n * 100, 1),
            "next_day_avg_return": round(sum(fwd_rets) / n, 2),
            "next_day_median_return": round(sorted(fwd_rets)[n // 2], 2),
        }

    return {
        "insufficient_data": False,
        "gap_up_held": _summarize(gap_up_hold, "Gap UP + HELD (conviction)"),
        "gap_up_filled": _summarize(gap_up_fill, "Gap UP + FILLED (trap/pullback)"),
        "gap_down_held": _summarize(gap_down_hold, "Gap DOWN + HELD (distribution)"),
        "gap_down_filled": _summarize(gap_down_fill, "Gap DOWN + FILLED (reversal)"),
    }


def compute_gap_signal(df: pd.DataFrame, direction: str = "BUY") -> dict[str, Any]:
    """Compute the gap signal for today's trading decision.

    Combines:
    1. Yesterday's gap classification
    2. Historical base rates for this gap type
    3. Current trend context (is the gap aligned with the trend?)

    Nadh insight: a gap up in an uptrend = continuation. A gap up in a
    downtrend = dead cat bounce. Context is everything.
    """
    if len(df) < 3:
        return {"error": "insufficient data"}

    # Yesterday's gap
    today = df.iloc[-1]
    yesterday = df.iloc[-2]

    gap = classify_gap(
        open_price=float(today["Open"]),
        prev_close=float(yesterday["Close"]),
        high=float(today["High"]),
        low=float(today["Low"]),
        close=float(today["Close"]),
    )

    # Historical rates
    hist = historical_gap_rates(df, direction)

    # Trend context
    close = df["Close"]
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    price = float(close.iloc[-1])
    above_20 = price > float(ema20.iloc[-1])
    above_50 = price > float(ema50.iloc[-1])

    trend_aligned = False
    trend_detail = ""
    if gap.get("has_gap"):
        if gap["direction"] == "UP" and above_20 and above_50:
            trend_aligned = True
            trend_detail = "Gap UP aligned with uptrend — high continuation probability"
        elif gap["direction"] == "DOWN" and not above_20 and not above_50:
            trend_aligned = True
            trend_detail = "Gap DOWN aligned with downtrend — high continuation probability"
        elif gap["direction"] == "UP" and not above_20:
            trend_detail = "Gap UP against downtrend — possible dead cat bounce"
        elif gap["direction"] == "DOWN" and above_20:
            trend_detail = "Gap DOWN in uptrend — possible pullback entry"
        else:
            trend_detail = "Gap direction mixed with trend — lower conviction"

    # Signal score (0-100)
    signal_score = 50  # neutral
    signal_type = "NO_GAP"

    if gap.get("has_gap"):
        gap_type = f"gap_{gap['direction'].lower()}_{'held' if gap['close_position'] in ('HELD_AND_EXPANDED', 'HELD_PARTIAL') else 'filled' if gap['close_position'] == 'REVERSED' else 'partial'}"

        # Look up historical continuation rate for this gap type
        hist_key = f"gap_{gap['direction'].lower()}_{'held' if gap['close_position'] in ('HELD_AND_EXPANDED', 'HELD_PARTIAL') else 'filled'}"
        hist_data = hist.get(hist_key, {})
        continuation_rate = hist_data.get("next_day_continuation", 50)

        # Score based on continuation rate + trend alignment
        signal_score = continuation_rate  # base score from historical rate
        if trend_aligned:
            signal_score = min(100, signal_score + 15)  # bonus for trend alignment
        if gap["close_position"] == "HELD_AND_EXPANDED":
            signal_score = min(100, signal_score + 10)  # bonus for strong close
        elif gap["close_position"] == "REVERSED":
            signal_score = max(0, signal_score - 20)  # penalty for reversal

        if signal_score >= 70:
            signal_type = "STRONG_FOLLOW_THROUGH"
        elif signal_score >= 55:
            signal_type = "FOLLOW_THROUGH_LIKELY"
        elif signal_score >= 40:
            signal_type = "UNCERTAIN"
        else:
            signal_type = "REVERSAL_LIKELY"

    return {
        "gap": gap,
        "trend_aligned": trend_aligned,
        "trend_detail": trend_detail,
        "signal_score": round(signal_score, 1),
        "signal_type": signal_type,
        "historical_rates": hist,
        "scalping_note": _gap_scalping_note(gap, signal_type, trend_aligned),
    }


def _gap_scalping_note(gap: dict, signal_type: str, trend_aligned: bool) -> str:
    """Nadh-style practical note for scalpers."""
    if not gap.get("has_gap"):
        return "No significant gap — trade on technicals only."

    direction = gap["direction"]
    size = gap["size"]

    if signal_type == "STRONG_FOLLOW_THROUGH":
        return (
            f"Gap {direction} ({size}, {gap['gap_pct']:+.1f}%) HELD and expanded. "
            f"High probability of continuation. Scalp in the gap direction."
        )
    elif signal_type == "FOLLOW_THROUGH_LIKELY":
        return (
            f"Gap {direction} ({size}) held. Likely continuation but not guaranteed. "
            f"Enter on pullback to gap level for better R:R."
        )
    elif signal_type == "REVERSAL_LIKELY":
        return (
            f"Gap {direction} was FILLED. Market rejected the move. "
            f"{'Look for mean-reversion scalp in opposite direction.' if trend_aligned else 'Avoid — chaotic.'}"
        )
    else:
        return (
            f"Gap {direction} ({size}) is unclear. Historical rates are mixed. "
            f"Wait for confirmation before entering."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Gap Analysis — what did the overnight move mean?")
    parser.add_argument("--stats", default=str(STATS_FILE),
                        help="Path to screen_stats_out.json")
    parser.add_argument("--top", type=int, default=20,
                        help="Analyze top N candidates (default: 20)")
    parser.add_argument("--symbol", help="Single stock symbol (for prompt.md workflow)")
    args = parser.parse_args()

    # Single-symbol mode
    if args.symbol:
        sym = args.symbol.upper().replace(".NS", "")
        df = load_bars(sym)
        if df is None:
            emit_json({"error": f"Could not load bars for {sym}"})
            return 1
        result = compute_gap_signal(df, "BUY")
        payload = {
            "generated_on": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "mode": "single",
            "stocks": [{"symbol": sym, **result}],
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

    # Analyze top stocks by momentum score
    sorted_syms = sorted(
        symbols.items(),
        key=lambda x: x[1].get("momentum", {}).get("composite_score", 50),
        reverse=True,
    )[:args.top]

    for sym, st in sorted_syms:
        df = load_bars(sym)
        if df is None:
            continue
        direction = st.get("direction", "BUY")
        result = compute_gap_signal(df, direction)
        results.append({
            "symbol": sym,
            "direction": direction,
            "sector": st.get("sector", "OTHERS"),
            **result,
        })

    # Summary: how many stocks have actionable gap signals?
    actionable = sum(1 for r in results
                     if r.get("signal_type") in ("STRONG_FOLLOW_THROUGH", "FOLLOW_THROUGH_LIKELY"))

    payload = {
        "generated_on": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "mode": "batch",
        "analyzed": len(results),
        "summary": {
            "actionable_gaps": actionable,
            "strong_follow_through": sum(1 for r in results if r.get("signal_type") == "STRONG_FOLLOW_THROUGH"),
            "reversal_likely": sum(1 for r in results if r.get("signal_type") == "REVERSAL_LIKELY"),
        },
        "stocks": results,
        "note": (
            "Gap analysis measures the market's overnight opinion. A gap that holds "
            "= conviction. A gap that fills = indecision. For 1-3 day scalping, "
            "gaps are the strongest short-term signal available from free data."
        ),
    }
    emit_json(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
