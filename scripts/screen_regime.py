"""Screen 1.5: Market regime — the state of the tape the screen runs in.

Fetches NIFTY 50 (^NSEI) and India VIX (^INDIAVIX) daily bars and combines
them with the screen's own breadth (share of the scanned universe above its
50-EMA, aggregated from screen_stats_out.json) into a single regime verdict
the daily prompt applies gates against:

    * NIFTY trend   : close vs 50-EMA and 200-DMA -> BULL / RANGE / BEAR
    * VIX level     : <14 LOW · 14-20 NORMAL · >20 HIGH
    * breadth       : % of scanned stocks with a BULLISH 50-EMA trend
    * overall       : RISK-ON / CAUTIOUS / RISK-OFF / UNKNOWN

The NIFTY bars are also written to data_cache/NIFTY50.csv so the stats pass
(screen_stats.py) can condition each stock's backtest windows on NIFTY
being above/below its 200-DMA — regime *conditioning*, not just a header.

Graceful degradation: if the network fails, the payload reports UNKNOWN
with the error and the prompt falls back to neutral rules — it never
invents a regime.

Usage:
    python scripts/screen_regime.py [--stats screen_stats_out.json]
        [--universe screen_universe_out.json]

Output contract (screen_regime_out.json):
    generated_on, source, nifty {close, ema50, dma200, trend},
    vix {level, regime}, breadth {pct, n_bullish, n_scanned, regime},
    overall {label, risk_off, reasons}, note
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import PROJECT_ROOT, emit_json, load_json  # noqa: E402

NIFTY_CACHE = PROJECT_ROOT / "data_cache" / "NIFTY50.csv"
STATS_FILE = PROJECT_ROOT / "screen_stats_out.json"

BREADTH_RISK_OFF = 40.0   # breadth below this -> RISK-OFF
BREADTH_CAUTIOUS = 50.0   # breadth below this (>= risk-off) -> CAUTIOUS
VIX_HIGH = 20.0
VIX_LOW = 14.0


def fetch_index_bars(symbol: str, period: str) -> Any | None:
    """Download daily bars for an index ticker. Returns a DataFrame or None."""
    import yfinance as yf

    try:
        df = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=True)
        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        return df if not df.empty else None
    except Exception:
        return None


def nifty_verdict(df) -> tuple[float | None, float | None, float | None, str]:
    """(close, ema50, dma200, trend) from NIFTY bars; None-able on failure."""
    if df is None or len(df) < 60:
        return None, None, None, "UNKNOWN"
    close = float(df["Close"].iloc[-1])
    ema50 = float(df["Close"].ewm(span=50, adjust=False).mean().iloc[-1])
    dma = df["Close"].rolling(200).mean()
    dma200 = float(dma.iloc[-1]) if len(df) >= 200 and dma.iloc[-1] == dma.iloc[-1] else None
    if dma200 is None:
        return close, ema50, None, "UNKNOWN"
    if close > ema50 and close > dma200:
        trend = "BULL"
    elif close < ema50 and close < dma200:
        trend = "BEAR"
    else:
        trend = "RANGE"
    return close, ema50, dma200, trend


def vix_verdict(df) -> tuple[float | None, str]:
    if df is None or df.empty:
        return None, "UNKNOWN"
    level = float(df["Close"].iloc[-1])
    if level >= VIX_HIGH:
        regime = "HIGH"
    elif level <= VIX_LOW:
        regime = "LOW"
    else:
        regime = "NORMAL"
    return level, regime


def breadth_verdict(stats: dict[str, Any] | None) -> dict[str, Any]:
    """Share of scanned stocks in a BULLISH 50-EMA trend (free from stats)."""
    if not stats or "error" in stats:
        return {"pct": None, "n_bullish": 0, "n_scanned": 0, "regime": None}
    symbols = stats.get("symbols", {})
    n = len(symbols)
    if n == 0:
        return {"pct": None, "n_bullish": 0, "n_scanned": 0, "regime": None}
    n_bull = sum(1 for s in symbols.values() if s.get("trend") == "BULLISH")
    pct = n_bull / n * 100.0
    if pct < BREADTH_RISK_OFF:
        regime = "RISK-OFF"
    elif pct < BREADTH_CAUTIOUS:
        regime = "CAUTIOUS"
    else:
        regime = "RISK-ON"
    return {"pct": round(pct, 1), "n_bullish": n_bull, "n_scanned": n, "regime": regime}


def main() -> int:
    parser = argparse.ArgumentParser(description="Market regime for the daily screen: NIFTY trend, VIX, breadth.")
    parser.add_argument("--stats", default=str(STATS_FILE), help="Path to screen_stats_out.json (for breadth)")
    args = parser.parse_args()

    nifty = fetch_index_bars("^NSEI", "2y")
    vix = fetch_index_bars("^INDIAVIX", "6mo")

    nifty_close, nifty_ema50, nifty_dma200, nifty_trend = nifty_verdict(nifty)
    vix_level, vix_regime = vix_verdict(vix)

    # Persist NIFTY bars for screen_stats.py's regime-conditioned backtest.
    saved_nifty = False
    if nifty is not None:
        try:
            NIFTY_CACHE.parent.mkdir(parents=True, exist_ok=True)
            nifty.to_csv(NIFTY_CACHE, date_format="%Y-%m-%d")
            saved_nifty = True
        except Exception:
            saved_nifty = False

    try:
        stats = load_json(Path(args.stats))
    except FileNotFoundError:
        stats = None
    breadth = breadth_verdict(stats)

    reasons: list[str] = []
    if nifty_trend == "BEAR":
        reasons.append("NIFTY below its 200-DMA and 50-EMA")
    if breadth.get("regime") == "RISK-OFF":
        reasons.append(f"breadth {breadth['pct']}% < 40%")
    if vix_regime == "HIGH":
        reasons.append(f"VIX {vix_level:.1f} >= 20")
    if nifty_trend == "UNKNOWN":
        reasons.append("NIFTY data unavailable")

    risk_off = bool(reasons) and nifty_trend != "UNKNOWN"
    if nifty_trend == "UNKNOWN" and breadth.get("regime") is None and vix_regime == "UNKNOWN":
        label = "UNKNOWN"
    elif risk_off:
        label = "RISK-OFF"
    elif nifty_trend == "RANGE" or breadth.get("regime") == "CAUTIOUS":
        label = "CAUTIOUS"
    else:
        label = "RISK-ON"

    payload = {
        "generated_on": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "source": "yfinance (^NSEI / ^INDIAVIX) + screen breadth",
        "nifty": {
            "close": round(nifty_close, 2) if nifty_close else None,
            "ema50": round(nifty_ema50, 2) if nifty_ema50 else None,
            "dma200": round(nifty_dma200, 2) if nifty_dma200 else None,
            "trend": nifty_trend,
            "bars_saved": saved_nifty,
        },
        "vix": {
            "level": round(vix_level, 2) if vix_level else None,
            "regime": vix_regime,
        },
        "breadth": breadth,
        "overall": {
            "label": label,
            "risk_off": risk_off,
            "reasons": reasons,
        },
        "note": (
            "Regime is a risk gate, not an alpha claim: it tells the daily prompt "
            "when to shrink exposure and how to read the backtest windows, it does "
            "not predict the market."
        ),
    }
    emit_json(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
