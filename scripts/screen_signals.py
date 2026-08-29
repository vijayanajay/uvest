"""Standalone Signal Analysis — momentum, entry zones, and pivot points.

Two modes:
    1. Single symbol: python scripts/screen_signals.py --symbol BHARTIARTL
       Downloads bars from yfinance, computes full signal analysis.
       Designed for use with prompt.md (trade validation pipeline).

    2. Batch from rank: python scripts/screen_signals.py --rank screen_rank_out.json
       Enriches the ranked watchlist with signals.
       Designed for use with run_screen.py (daily screen pipeline).

Both modes output:
    - Detailed RSI, MACD, Bollinger, Volume analysis
    - VWAP (Volume-Weighted Average Price)
    - Daily pivot points (P, S1, S2, R1, R2)
    - Optimal entry zone (where R:R is best)
    - Composite signal score (0-100)
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    PROJECT_ROOT, emit_json, load_json,
    compute_rsi, compute_macd, compute_bollinger, compute_volume_surge,
    momentum_composite, get_sector,
)

RANK_FILE = PROJECT_ROOT / "screen_rank_out.json"
CACHE_DIR = PROJECT_ROOT / "data_cache"


def load_bars(symbol: str, allow_download: bool = False):
    """Load cached daily bars for a symbol.

    If allow_download=True and cache is missing, fetches from yfinance
    (used in single-symbol mode for prompt.md workflow).
    """
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

    # Download from yfinance
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


def compute_vwap(df, lookback: int = 20):
    """Compute VWAP over the last `lookback` trading days.

    VWAP = cumulative(typical_price * volume) / cumulative(volume)
    Typical price = (High + Low + Close) / 3
    """
    import pandas as pd
    recent = df.tail(lookback)
    if "Volume" not in recent.columns or recent["Volume"].sum() == 0:
        return float(recent["Close"].iloc[-1])  # fallback to last close

    typical = (recent["High"] + recent["Low"] + recent["Close"]) / 3.0
    cum_tp_vol = (typical * recent["Volume"]).cumsum()
    cum_vol = recent["Volume"].cumsum()
    vwap_series = cum_tp_vol / cum_vol.replace(0, float("nan"))
    vwap = vwap_series.iloc[-1]
    return round(float(vwap), 2) if vwap == vwap else float(recent["Close"].iloc[-1])


def compute_pivots(df):
    """Compute classic daily pivot points from the last bar.

    P  = (High + Low + Close) / 3
    R1 = 2P - Low,    S1 = 2P - High
    R2 = P + (High - Low), S2 = P - (High - Low)
    """
    last = df.iloc[-1]
    h, l, c = float(last["High"]), float(last["Low"]), float(last["Close"])
    p = (h + l + c) / 3.0
    r1 = 2 * p - l
    s1 = 2 * p - h
    r2 = p + (h - l)
    s2 = p - (h - l)
    return {
        "pivot": round(p, 2),
        "r1": round(r1, 2), "r2": round(r2, 2),
        "s1": round(s1, 2), "s2": round(s2, 2),
    }


def entry_zone(close: float, atr_pct: float, direction: str,
               vwap: float, pivots: dict[str, float]) -> dict[str, Any]:
    """Compute the optimal entry zone based on VWAP and pivots.

    For a BUY:
        Ideal entry: max(below VWAP, near S1) where R:R is best
        Stop: below S1 or at S2
        Target: R1 or above VWAP
    For a SELL:
        Ideal entry: max(above VWAP, near R1)
        Stop: above R1 or at R2
        Target: S1 or below VWAP
    """
    atr = close * atr_pct / 100.0

    if direction == "BUY":
        # Ideal: buy below VWAP, stop at S1 (or S2 if S1 is too close)
        ideal_entry = min(close, vwap, pivots["s1"])
        stop = pivots["s2"] if (pivots["s1"] - ideal_entry) < atr * 0.5 else pivots["s1"]
        stop = min(stop, ideal_entry - atr * 1.0)  # ensure at least 1 ATR below
        target = max(pivots["r1"], vwap + atr * 1.5)
    else:
        ideal_entry = max(close, vwap, pivots["r1"])
        stop = pivots["r2"] if (ideal_entry - pivots["r1"]) < atr * 0.5 else pivots["r1"]
        stop = max(stop, ideal_entry + atr * 1.0)
        target = min(pivots["s1"], vwap - atr * 1.5)

    risk = abs(ideal_entry - stop)
    reward = abs(target - ideal_entry)
    rr = round(reward / risk, 2) if risk > 0 else 0.0

    return {
        "ideal_entry": round(ideal_entry, 2),
        "stop": round(stop, 2),
        "target": round(target, 2),
        "rr_at_ideal": rr,
        "risk_pct": round(risk / ideal_entry * 100, 2),
        "reward_pct": round(reward / ideal_entry * 100, 2),
    }


def analyze_stock(symbol: str, rank_row: dict[str, Any],
                  allow_download: bool = False) -> dict[str, Any] | None:
    """Full signal analysis for one stock."""
    df = load_bars(symbol, allow_download=allow_download)
    if df is None:
        return None

    close = float(df["Close"].iloc[-1])
    atr_pct = rank_row.get("atr_pct", 0.0)
    direction = rank_row.get("direction", "BUY")

    # Momentum indicators
    rsi_s = compute_rsi(df["Close"], 14)
    macd_l, sig_l, hist_s = compute_macd(df["Close"])
    bb_mid, bb_up, bb_lo, bb_w = compute_bollinger(df["Close"])
    vol_r = compute_volume_surge(df["Volume"]) if "Volume" in df.columns else None

    bb_width_avg = bb_w.rolling(50).mean() if bb_w is not None else None

    rsi_val = float(rsi_s.iloc[-1]) if rsi_s.iloc[-1] == rsi_s.iloc[-1] else 50.0
    macd_hist = float(hist_s.iloc[-1]) if hist_s.iloc[-1] == hist_s.iloc[-1] else 0.0
    bb_width_val = float(bb_w.iloc[-1]) if bb_w is not None and bb_w.iloc[-1] == bb_w.iloc[-1] else 5.0
    # NaN-safe: only access .iloc[-1] if the Series exists and the value is not NaN
    if bb_width_avg is not None and len(bb_width_avg) > 0:
        _bb_avg_last = bb_width_avg.iloc[-1]
        bb_avg_val = float(_bb_avg_last) if _bb_avg_last == _bb_avg_last else bb_width_val
    else:
        bb_avg_val = bb_width_val
    vol_ratio = float(vol_r.iloc[-1]) if vol_r is not None and vol_r.iloc[-1] == vol_r.iloc[-1] else 1.0
    ema_bullish = close > float(df["Close"].ewm(span=50, adjust=False).mean().iloc[-1])

    # Composite score
    composite = momentum_composite(
        rsi=rsi_val, macd_hist=macd_hist,
        bb_width=bb_width_val, bb_width_avg=bb_avg_val,
        vol_ratio=vol_ratio, ema_bullish=ema_bullish,
        atr_pct=float(atr_pct) / 100.0 if atr_pct else 0.02,
    )

    # VWAP and pivots
    vwap = compute_vwap(df)
    pivots = compute_pivots(df)

    # Entry zone
    zone = entry_zone(close, atr_pct, direction, vwap, pivots)

    return {
        "symbol": symbol,
        "close": round(close, 2),
        "direction": direction,
        "sector": get_sector(symbol),
        "composite_score": composite["score"],
        "signal_direction": composite["direction"],
        "rsi": round(rsi_val, 1),
        "macd_histogram": round(macd_hist, 4),
        "bollinger_width": round(bb_width_val, 2),
        "volume_ratio": round(vol_ratio, 2),
        "vwap": vwap,
        "pivots": pivots,
        "entry_zone": zone,
        "components": composite["components"],
    }





def main() -> int:
    parser = argparse.ArgumentParser(description="Standalone signal analysis for ranked candidates or single stocks.")
    parser.add_argument("--symbol", help="Single stock symbol (e.g. BHARTIARTL) — for prompt.md workflow")
    parser.add_argument("--rank", default=str(RANK_FILE),
                        help="Path to screen_rank_out.json (batch mode)")
    parser.add_argument("--top", type=int, default=10,
                        help="Analyze the top N candidates in batch mode (default: 10)")
    args = parser.parse_args()

    # --- Single-symbol mode (prompt.md workflow) ---
    if args.symbol:
        sym = args.symbol.upper().replace(".NS", "")
        # Minimal rank_row for single-stock analysis
        rank_row = {"direction": "BUY", "atr_pct": 0.0}
        result = analyze_stock(sym, rank_row, allow_download=True)
        if result is None:
            emit_json({"error": f"Could not analyze {sym} — no price data available"})
            return 1
        payload = {
            "generated_on": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "params": {"symbol": sym, "mode": "single"},
            "signals": [result],
            "note": (
                "Signal analysis for a single stock. The composite score is a 0-100 "
                "measure of momentum strength, not a prediction of future returns."
            ),
        }
        emit_json(payload)
        return 0

    # --- Batch mode (run_screen.py workflow) ---
    try:
        rank = load_json(Path(args.rank))
    except FileNotFoundError:
        emit_json({"error": f"Rank file not found: {args.rank} (run screen_rank.py first)"})
        return 1

    top = rank.get("top", [])[:args.top]
    signals: list[dict[str, Any]] = []

    for row in top:
        sym = row["symbol"]
        result = analyze_stock(sym, row)
        if result is not None:
            signals.append(result)

    signals.sort(key=lambda s: s["composite_score"], reverse=True)

    payload = {
        "generated_on": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "params": {
            "rank_file": str(args.rank),
            "top": args.top,
            "analyzed": len(signals),
        },
        "signals": signals,
        "note": (
            "Signal analysis enriches the ranked watchlist with momentum indicators, "
            "VWAP, pivot points, and entry zones."
        ),
    }
    emit_json(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
