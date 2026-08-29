"""Regime Classifier — "What kind of day is this?"

Kailash Nadh's key insight: you don't predict WHERE the market goes,
you classify WHAT ENVIRONMENT you're in, then deploy the strategy that
works in that environment. A trend follower who trades on a mean-reversion
day loses money. A mean-reversion trader who holds on a trending day leaves
money on the table.

The regime is NOT a prediction — it's a diagnosis. The market tells you
what it wants to do if you listen.

Four regimes:

    TRENDING (ADX > 20, majority above MA, momentum aligned)
        → Trend-following setups work. Hold winners longer.
        → Scalp by riding momentum, not fighting it.

    MEAN_REVERSION (yesterday was extreme, VIX elevated, extremes in breadth)
        → Pullback entries work. Buy dips, sell rips.
        → Scalp by entering after overshoots, not breakouts.

    BREAKOUT (Bollinger squeeze across many stocks, VIX compression)
        → Breakout setups work. Wait for the expansion.
        → Scalp the first move out of the squeeze.

    CHOPPY (conflicting signals, no clear edge)
        → Nothing reliably works. Preserve capital.
        → Either don't trade, or trade minimal size.

The rate-of-change question (Nadh layer):
    Not "is VIX 18?" but "was VIX 22 yesterday?" → regime is IMPROVING.
    Not "is breadth 55%?" but "was it 45% yesterday?" → regime is THRUSTING.

Output: screen_regime_classify_out.json

All data sources are free:
    - Daily bars from data_cache/ (yfinance, already cached)
    - India VIX from yfinance ^INDIAVIX
    - NIFTY 50 from data_cache/NIFTY50.csv

Usage:
    python scripts/screen_regime_classify.py [--stats screen_stats_out.json]
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

NIFTY_CACHE = PROJECT_ROOT / "data_cache" / "NIFTY50.csv"
CACHE_DIR = PROJECT_ROOT / "data_cache"
REGIME_HISTORY = CACHE_DIR / "regime_history.json"


def _load_regime_history() -> dict[str, Any]:
    """Load previous regime classifications for momentum overlay."""
    try:
        return json.loads(REGIME_HISTORY.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_regime_history(data: dict[str, Any]) -> None:
    """Save regime classification for next run's momentum comparison."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    REGIME_HISTORY.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def compute_adx(df: pd.DataFrame, period: int = 14) -> float | None:
    """Compute Average Directional Index (ADX) from daily bars.

    ADX measures trend strength regardless of direction.
    >25 = trending, <20 = choppy/range-bound.
    Nadh insight: ADX is the "is the market doing anything?" filter.
    """
    if len(df) < period * 2:
        return None

    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    # True Range
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    # Directional Movement
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low

    plus_dm = pd.Series(0.0, index=df.index)
    minus_dm = pd.Series(0.0, index=df.index)

    plus_dm[(up_move > down_move) & (up_move > 0)] = up_move
    minus_dm[(down_move > up_move) & (down_move > 0)] = down_move

    # Wilder smoothing (EWM with alpha=1/period)
    atr_w = tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    plus_dm_w = plus_dm.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    minus_dm_w = minus_dm.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    plus_di = 100.0 * plus_dm_w / atr_w.replace(0, float("nan"))
    minus_di = 100.0 * minus_dm_w / atr_w.replace(0, float("nan"))

    di_sum = plus_di + minus_di
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum.replace(0, float("nan"))
    adx = dx.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    last_adx = adx.iloc[-1]
    return round(float(last_adx), 2) if last_adx == last_adx else None  # NaN check


def classify_regime(stats: dict[str, Any] | None) -> dict[str, Any]:
    """Classify today's market regime using multi-factor Nadh-style diagnosis.

    This is NOT a prediction. It's a reading of the current environment.
    "What does the market want to do today?" — then deploy accordingly.
    """
    # --- Load NIFTY data for ADX and momentum ---
    nifty_df = None
    if NIFTY_CACHE.exists():
        try:
            nifty_df = pd.read_csv(NIFTY_CACHE, parse_dates=["Date"], index_col="Date")
        except Exception:
            pass

    # --- Factor 1: NIFTY ADX (trend strength) ---
    nifty_adx = None
    nifty_momentum = None
    nifty_above_ma20 = None
    nifty_above_ma50 = None

    if nifty_df is not None and len(nifty_df) >= 50:
        nifty_adx = compute_adx(nifty_df)

        close = nifty_df["Close"]
        ma20 = close.rolling(20).mean()
        ma50 = close.rolling(50).mean()

        if len(close) >= 20:
            nifty_above_ma20 = bool(float(close.iloc[-1]) > float(ma20.iloc[-1]))
        if len(close) >= 50:
            nifty_above_ma50 = bool(float(close.iloc[-1]) > float(ma50.iloc[-1]))

        # Nadh insight: momentum = rate of change of the 5-day return
        if len(close) >= 6:
            ret_5d = float(close.iloc[-1]) / float(close.iloc[-6]) - 1.0
            ret_5d_prev = float(close.iloc[-6]) / float(close.iloc[-11]) - 1.0 if len(close) >= 11 else 0.0
            nifty_momentum = {
                "return_5d": round(ret_5d * 100, 2),
                "return_5d_prev": round(ret_5d_prev * 100, 2),
                "acceleration": round((ret_5d - ret_5d_prev) * 100, 2),
            }

    # --- Factor 2: VIX regime (from yfinance) ---
    vix_regime = "UNKNOWN"
    vix_compression = False
    try:
        import yfinance as yf
        vix_df = yf.Ticker("^INDIAVIX").history(period="1mo", interval="1d")
        if vix_df is not None and not vix_df.empty and len(vix_df) >= 3:
            vix_close = vix_df["Close"].dropna()
            vix_now = float(vix_close.iloc[-1])
            vix_3d_avg = float(vix_close.tail(3).mean())
            vix_prev = float(vix_close.iloc[-2]) if len(vix_close) >= 2 else vix_now

            # Nadh question: is VIX falling or rising?
            vix_change = vix_now - vix_prev
            vix_compression = vix_change < -0.3 or vix_now < vix_3d_avg

            if vix_now < 13:
                vix_regime = "VERY_LOW"
            elif vix_now < 16:
                vix_regime = "LOW"
            elif vix_now < 20:
                vix_regime = "NORMAL"
            elif vix_now < 25:
                vix_regime = "ELEVATED"
            else:
                vix_regime = "HIGH"
    except Exception:
        pass

    # --- Factor 3: Universe breadth (from screen_stats_out.json) ---
    breadth_pct = None
    breadth_prev = None
    breadth_improving = False
    squeeze_count = 0
    squeeze_pct = 0.0
    total_stocks = 0

    if stats and "symbols" in stats:
        symbols = stats["symbols"]
        total_stocks = len(symbols)

        if total_stocks > 0:
            n_bull = sum(1 for s in symbols.values() if s.get("trend") == "BULLISH")
            breadth_pct = round(n_bull / total_stocks * 100, 1)

            # Count stocks in Bollinger squeeze (bandwidth < 0.7x avg)
            for s in symbols.values():
                mom = s.get("momentum", {})
                bb = mom.get("bollinger_width")
                # We don't have bb_width_avg in stats, but we can use the
                # composite score's squeeze component as a proxy
                if bb is not None and bb < 4.0:  # tight Bollinger = squeeze
                    squeeze_count += 1

            squeeze_pct = round(squeeze_count / total_stocks * 100, 1) if total_stocks > 0 else 0.0

    # Load previous breadth for momentum
    try:
        from screen_day_quality import _load_json_cache, BREADTH_HISTORY_FILE
        prev_breadth_data = _load_json_cache(BREADTH_HISTORY_FILE)
        breadth_prev = prev_breadth_data.get("pct_bullish")
    except Exception:
        pass

    if breadth_pct is not None and breadth_prev is not None:
        breadth_improving = breadth_pct > breadth_prev + 2.0

    # --- Factor 4: Yesterday's NIFTY move (for mean reversion) ---
    yesterday_extreme = False
    yesterday_move_pct = 0.0
    if nifty_df is not None and len(nifty_df) >= 2:
        close = nifty_df["Close"]
        ret_1d = (float(close.iloc[-1]) / float(close.iloc[-2]) - 1.0) * 100
        yesterday_move_pct = round(ret_1d, 2)
        yesterday_extreme = abs(ret_1d) > 1.2  # >1.2% move = extreme

    # --- Factor 5: Previous regime (for momentum overlay) ---
    prev_regime_data = _load_regime_history()
    prev_regime = prev_regime_data.get("regime", "UNKNOWN")
    regime_momentum = "STABLE"
    if prev_regime != "UNKNOWN":
        if prev_regime == "CHOPPY" and (nifty_adx or 0) > 20:
            regime_momentum = "IMPROVING"  # choppy → trending
        elif prev_regime == "TRENDING" and (nifty_adx or 30) < 18:
            regime_momentum = "DETERIORATING"  # trending → choppy

    # --- CLASSIFICATION LOGIC (Nadh-style: simple, robust, overfitting-resistant) ---

    regime = "CHOPPY"  # default
    confidence = 0.0
    reasons: list[str] = []
    strategy = "preserve_capital"

    # TRENDING: ADX strong, breadth decent, momentum aligned
    if nifty_adx is not None and nifty_adx > 20:
        if breadth_pct is not None and breadth_pct > 50:
            if nifty_above_ma50 is not None and nifty_above_ma50:
                regime = "TRENDING"
                confidence = min(1.0, (nifty_adx - 20) / 20.0 + (breadth_pct - 50) / 50.0)
                reasons = [
                    f"NIFTY ADX {nifty_adx:.1f} (>20 = trending)",
                    f"Breadth {breadth_pct:.0f}% (>50% = majority bullish)",
                    "NIFTY above 50-DMA (uptrend confirmed)",
                ]
                strategy = "ride_momentum"
            elif nifty_above_ma50 is not None and not nifty_above_ma50:
                # ADX high but NIFTY below 50-DMA = downtrend
                if breadth_pct < 40:
                    regime = "TRENDING"  # bearish trending
                    confidence = min(1.0, (nifty_adx - 20) / 20.0)
                    reasons = [
                        f"NIFTY ADX {nifty_adx:.1f} (strong trend)",
                        f"Breadth {breadth_pct:.0f}% (majority bearish)",
                        "NIFTY below 50-DMA (downtrend)",
                    ]
                    strategy = "short_momentum"

    # MEAN_REVERSION: yesterday was extreme, VIX elevated
    if regime == "CHOPPY" and yesterday_extreme and vix_regime in ("ELEVATED", "HIGH"):
        regime = "MEAN_REVERSION"
        confidence = 0.6 + (0.2 if vix_regime == "HIGH" else 0.0)
        reasons = [
            f"NIFTY moved {yesterday_move_pct:+.1f}% yesterday (extreme)",
            f"VIX {vix_regime} — elevated fear = reversion setups",
        ]
        strategy = "buy_dips_sell_rips"

    # BREAKOUT: many stocks in Bollinger squeeze, VIX compressing
    if regime == "CHOPPY" and squeeze_pct > 25 and vix_compression:
        regime = "BREAKOUT"
        confidence = 0.5 + min(0.3, squeeze_pct / 100.0)
        reasons = [
            f"{squeeze_pct:.0f}% of universe in Bollinger squeeze",
            "VIX compression (falling / below 3d avg)",
        ]
        strategy = "wait_for_expansion"

    # CHOPPY: nothing clearly works
    if regime == "CHOPPY":
        confidence = 0.5
        reasons = [
            f"NIFTY ADX {nifty_adx or 'N/A'} (needs >20 for trending)" if nifty_adx else "ADX unavailable",
            f"Breadth {breadth_pct or 'N/A'}%" if breadth_pct is not None else "Breadth unavailable",
            "No clear regime — conflicting signals",
        ]
        strategy = "preserve_capital"

    # Compute regime score (0-100) — how favorable is this environment for scalping?
    if regime == "TRENDING":
        score = 80 + int(confidence * 20)  # 80-100
    elif regime == "MEAN_REVERSION":
        score = 60 + int(confidence * 20)  # 60-80
    elif regime == "BREAKOUT":
        score = 55 + int(confidence * 25)  # 55-80
    else:  # CHOPPY
        score = 20 + int(confidence * 20)  # 20-40
    score = max(0, min(100, score))

    # --- Build output ---
    regime_emoji = {"TRENDING": "🟢", "MEAN_REVERSION": "🟡", "BREAKOUT": "🟠", "CHOPPY": "🔴"}.get(regime, "⚪")

    output = {
        "generated_on": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "regime": regime,
        "emoji": regime_emoji,
        "score": score,
        "confidence": round(confidence, 2),
        "strategy": strategy,
        "reasons": reasons,
        "factors": {
            "nifty_adx": nifty_adx,
            "nifty_above_ma20": nifty_above_ma20,
            "nifty_above_ma50": nifty_above_ma50,
            "nifty_momentum": nifty_momentum,
            "vix_regime": vix_regime,
            "vix_compression": vix_compression,
            "breadth_pct": breadth_pct,
            "breadth_improving": breadth_improving,
            "squeeze_pct": squeeze_pct,
            "squeeze_count": squeeze_count,
            "total_stocks": total_stocks,
            "yesterday_move_pct": yesterday_move_pct,
            "yesterday_extreme": yesterday_extreme,
        },
        "momentum": {
            "previous_regime": prev_regime,
            "regime_momentum": regime_momentum,
        },
        "scalping_guidance": _scalping_guidance(regime, vix_regime, breadth_pct),
        "note": (
            "Regime is a diagnosis, not a prediction. It says 'the market is in "
            "a trending environment right now' — not 'the market will go up'. "
            "Deploy the strategy that works in this regime."
        ),
    }

    # Save for next run's momentum comparison
    _save_regime_history({
        "regime": regime,
        "score": score,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "nifty_adx": nifty_adx,
        "breadth_pct": breadth_pct,
    })

    return output


def _scalping_guidance(regime: str, vix_regime: str, breadth_pct: float | None) -> dict[str, Any]:
    """Nadh-style practical guidance for scalpers based on regime."""
    if regime == "TRENDING":
        return {
            "hold_time": "1-3 days (ride the trend)",
            "entry_style": "Buy breakouts and pullbacks to 5-EMA",
            "stop_style": "Below the previous day's low (let the trend work)",
            "target": "1-2% (trends overshoot)",
            "position_size": "Full (regime supports conviction)",
            "avoid": "Don't fight the trend with counter-trend scalps",
        }
    elif regime == "MEAN_REVERSION":
        return {
            "hold_time": "1-2 days (reversion is fast)",
            "entry_style": "Buy oversold bounces, sell overbought fades",
            "stop_style": "Tight (below today's low, not yesterday's)",
            "target": "0.5-1% (reversion targets are modest)",
            "position_size": "50-75% (less conviction in reversal)",
            "avoid": "Don't chase momentum — it's reversing, not continuing",
        }
    elif regime == "BREAKOUT":
        return {
            "hold_time": "Wait for the breakout, then 1-2 days",
            "entry_style": "Buy the first 1% move out of the squeeze",
            "stop_style": "Below the squeeze low (tight, since breakout is binary)",
            "target": "1-3% (breakouts have range)",
            "position_size": "50% (breakout might fail)",
            "avoid": "Don't enter before the breakout — the squeeze can last days",
        }
    else:  # CHOPPY
        return {
            "hold_time": "Minimal (if you must trade)",
            "entry_style": "Very tight entries only at key levels (VWAP, S1/R1)",
            "stop_style": "Very tight (0.3% max)",
            "target": "0.3-0.5% (small, quick, get out)",
            "position_size": "25% maximum (capital preservation)",
            "avoid": "Most setups — the edge isn't there today",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Regime Classifier — what kind of day is this?")
    parser.add_argument("--stats", default=str(PROJECT_ROOT / "screen_stats_out.json"),
                        help="Path to screen_stats_out.json (for breadth)")
    args = parser.parse_args()

    stats = None
    try:
        stats = load_json(Path(args.stats))
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    result = classify_regime(stats)
    emit_json(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
