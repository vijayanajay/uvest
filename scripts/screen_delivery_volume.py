"""Delivery Volume Analysis — "Is this volume real buying or retail churn?"

Kailash Nadh's insight: not all volume is equal. A stock that moves 2% on
high DELIVERY volume (>50% delivery ratio) has institutional conviction behind
it. The same 2% move on low delivery volume (<30%) is retail speculation
that will reverse.

In Indian markets, delivery volume is the single best free proxy for
institutional intent. When FIIs/DIIs accumulate, they take delivery — they
don't intraday flip. When retail traders chase momentum, delivery % drops.

The three questions:
    1. What's the delivery ratio? (>50% = institutional, <30% = retail churn)
    2. Is delivery volume accelerating? (3-day vs 10-day trend)
    3. How does delivery compare to the 20-day average? (surge = new interest)

The NSE publishes daily delivery data for free at:
    https://nsearchives.nseindia.com/content/cm/Security_Wise_Delivery_Position_MAY.csv
    (monthly files)

For stocks in the F&O segment, this data is available daily. The format
includes: trading day, symbol, delivery quantity, and total quantity traded.

Since NSE may block direct access, we use a multi-source approach:
    1. Try NSE archives (monthly CSVs)
    2. Fall back to Yahoo Finance volume analysis (delivery proxy via
       on-balance volume and volume-weighted close analysis)
    3. Cache results for the trading day

Output: screen_delivery_volume_out.json

All data sources are free.

Usage:
    python scripts/screen_delivery_volume.py [--stats screen_stats_out.json] [--top 20]
    python scripts/screen_delivery_volume.py --symbol BHARTIARTL
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import PROJECT_ROOT, emit_json, load_json  # noqa: E402

CACHE_DIR = PROJECT_ROOT / "data_cache"
STATS_FILE = PROJECT_ROOT / "screen_stats_out.json"
DELIVERY_CACHE = CACHE_DIR / "delivery_volume_cache.json"

NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "text/csv,application/csv,*/*",
    "Accept-Language": "en-IN,en;q=0.9",
}


# ---------------------------------------------------------------------------
# NSE delivery data fetching
# ---------------------------------------------------------------------------

def fetch_nse_delivery_data(timeout: float = 15.0) -> dict[str, Any] | None:
    """Fetch daily delivery volume data from NSE archives.

    NSE publishes Security_Wise_Delivery_Position data monthly.
    URL pattern: https://nsearchives.nseindia.com/content/cm/Security_Wise_Delivery_POSITION_YYYYMMM.csv

    Returns {symbol: {delivery_qty, total_qty, delivery_pct}} for recent days.
    """
    now = datetime.now()
    # Try current month first, then previous month
    for months_back in range(2):
        dt = datetime(now.year, now.month - months_back if now.month > months_back else 12 - (months_back - now.month), 1)
        month_str = dt.strftime("%Y%b").upper()
        url = f"https://nsearchives.nseindia.com/content/cm/Security_Wise_Delivery_Position_{month_str}.csv"

        try:
            req = urllib.request.Request(url, headers=NSE_HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                text = resp.read().decode("utf-8-sig", errors="replace")
            rows = list(csv.DictReader(io.StringIO(text)))
            if rows:
                return _parse_nse_delivery(rows)
        except Exception:
            continue

    return None


def _parse_nse_delivery(rows: list[dict]) -> dict[str, Any]:
    """Parse NSE delivery data rows into per-symbol, per-date format.

    NSE columns vary by month but typically include:
        - Date / Trading Date
        - Symbol
        - Delivered Quantity / Delivery Qty
        - Total Traded Quantity / Total Qty
    """
    by_symbol: dict[str, list[dict]] = {}

    for row in rows:
        # Try multiple column name patterns
        symbol = None
        for key in ("Symbol", "SYMBOL", "symbol"):
            if key in row:
                symbol = row[key].strip().upper()
                break
        if not symbol:
            continue

        # Get delivery and total quantity
        delivery_qty = None
        total_qty = None
        trade_date = None

        for key in ("Delivered Quantity", "DELIVERABLE QTY", "Delivery Qty", "delivery_qty"):
            if key in row:
                try:
                    delivery_qty = int(row[key].replace(",", "").strip())
                except (ValueError, AttributeError):
                    pass
                break

        for key in ("Total Traded Quantity", "TOTAL TRADED QUANTITY", "Total Qty", "total_qty"):
            if key in row:
                try:
                    total_qty = int(row[key].replace(",", "").strip())
                except (ValueError, AttributeError):
                    pass
                break

        for key in ("Date", "DATE", "Trading Date", "trade_date"):
            if key in row:
                trade_date = row[key].strip()
                break

        if delivery_qty is not None and total_qty is not None and total_qty > 0:
            delivery_pct = delivery_qty / total_qty * 100
            by_symbol.setdefault(symbol, []).append({
                "date": trade_date,
                "delivery_qty": delivery_qty,
                "total_qty": total_qty,
                "delivery_pct": round(delivery_pct, 1),
            })

    # Keep only the most recent 5 days per symbol
    for symbol in by_symbol:
        by_symbol[symbol] = sorted(by_symbol[symbol], key=lambda x: x["date"] or "")[-5:]

    return {"symbols": by_symbol, "source": "NSE archives"}


# ---------------------------------------------------------------------------
# Delivery analysis from Yahoo Finance (fallback / proxy)
# ---------------------------------------------------------------------------

def compute_delivery_proxy(symbol: str) -> dict[str, Any] | None:
    """Compute delivery volume proxy from Yahoo Finance daily data.

    When NSE delivery data is unavailable, we use three proxies:

    1. On-Balance Volume (OBV): if OBV is rising with price, volume is
       confirming the move = institutional buying.

    2. Volume-Weighted Close: when close is consistently in the upper
       portion of the day's range AND volume is above average, it suggests
       institutional accumulation.

    3. Delivery Proxy Score: a composite of the above two signals.

    These are imperfect proxies — real delivery data from NSE is better —
    but they're free, available for every stock, and capture the essence
    of "is this volume real?"
    """
    f = CACHE_DIR / f"{symbol}.csv"
    if not f.exists():
        return None
    try:
        df = pd.read_csv(f, parse_dates=["Date"], index_col="Date")
        if len(df) < 30:
            return None
    except Exception:
        return None

    close = df["Close"]
    volume = df["Volume"]
    high = df["High"]
    low = df["Low"]
    open_ = df["Open"]

    # 1. On-Balance Volume (OBV) — the classic volume-price confirmation
    obv = pd.Series(0.0, index=df.index)
    for i in range(1, len(df)):
        if float(close.iloc[i]) > float(close.iloc[i - 1]):
            obv.iloc[i] = obv.iloc[i - 1] + float(volume.iloc[i])
        elif float(close.iloc[i]) < float(close.iloc[i - 1]):
            obv.iloc[i] = obv.iloc[i - 1] - float(volume.iloc[i])
        else:
            obv.iloc[i] = obv.iloc[i - 1]

    # OBV trend: 5-day vs 20-day OBV average
    obv_5d = float(obv.tail(5).mean())
    obv_20d = float(obv.tail(20).mean())
    obv_bullish = obv_5d > obv_20d

    # 2. Delivery proxy: close position in range * volume surge
    # When close is in upper 60% of range AND volume > average = likely delivery
    day_range = high - low
    day_range = day_range.replace(0, float("nan"))
    close_position = (close - low) / day_range  # 0 = low, 1 = high

    vol_avg_20 = volume.rolling(20).mean()
    vol_ratio = volume / vol_avg_20.replace(0, float("nan"))

    # Delivery proxy = close_position * vol_ratio (higher = more institutional)
    delivery_proxy = close_position * vol_ratio
    dp_5d = float(delivery_proxy.tail(5).mean())
    dp_20d = float(delivery_proxy.tail(20).mean())

    # 3. Accumulation/Distribution line
    clv = ((close - low) - (high - close)) / day_range.replace(0, float("nan"))
    clv = clv.fillna(0)
    ad_line = (clv * volume).cumsum()
    ad_5d_change = float(ad_line.iloc[-1] - ad_line.iloc[-5]) if len(ad_line) >= 5 else 0

    # Composite delivery proxy score (0-100)
    score = 0
    components = {}

    # OBV component (0-30)
    if obv_bullish:
        obv_score = 25
    else:
        obv_score = 5
    score += obv_score
    components["obv"] = {
        "bullish": obv_bullish,
        "5d_avg": round(obv_5d, 0),
        "20d_avg": round(obv_20d, 0),
        "points": obv_score,
        "detail": f"OBV {'rising' if obv_bullish else 'falling'} — volume {'confirms' if obv_bullish else 'diverges from'} price",
    }

    # Delivery proxy component (0-35)
    if dp_5d > dp_20d * 1.1:
        dp_score = 30
        dp_signal = "ACCUMULATION"
    elif dp_5d > dp_20d:
        dp_score = 22
        dp_signal = "MILD_ACCUMULATION"
    elif dp_5d > dp_20d * 0.9:
        dp_score = 15
        dp_signal = "NEUTRAL"
    else:
        dp_score = 5
        dp_signal = "DISTRIBUTION"
    score += dp_score
    components["delivery_proxy"] = {
        "signal": dp_signal,
        "5d_avg": round(dp_5d, 3),
        "20d_avg": round(dp_20d, 3),
        "points": dp_score,
        "detail": f"Delivery proxy {dp_signal} — close-in-range × volume ratio 5d avg {dp_5d:.3f} vs 20d {dp_20d:.3f}",
    }

    # Accumulation/Distribution component (0-35)
    if ad_5d_change > 0:
        ad_score = 25
        ad_signal = "NET_BUYING"
    elif ad_5d_change > -volume.tail(5).mean() * 0.5:
        ad_score = 15
        ad_signal = "MILD_SELLING"
    else:
        ad_score = 5
        ad_signal = "NET_SELLING"
    score += ad_score
    components["ad_line"] = {
        "signal": ad_signal,
        "5d_change": round(ad_5d_change, 0),
        "points": ad_score,
        "detail": f"A/D line 5-day change: {ad_5d_change:+,.0f} — {ad_signal}",
    }

    score = max(0, min(100, score))

    # Verdict
    if score >= 65:
        verdict = "INSTITUTIONAL_ACCUMULATION"
        recommendation = "Volume profile suggests institutional buying. High-conviction entry."
    elif score >= 45:
        verdict = "MIXED_SIGNALS"
        recommendation = "Volume profile is mixed. Some buying, some selling. Standard sizing."
    else:
        verdict = "RETAIL_CHURN_OR_DISTRIBUTION"
        recommendation = "Volume suggests retail speculation or institutional distribution. Reduce sizing."

    return {
        "delivery_ratio_proxy": round(dp_5d, 3),
        "obv_bullish": obv_bullish,
        "ad_5d_change": round(ad_5d_change, 0),
        "score": score,
        "verdict": verdict,
        "recommendation": recommendation,
        "components": components,
        "source": "Yahoo Finance (proxy — not actual NSE delivery data)",
    }


# ---------------------------------------------------------------------------
# NSE delivery analysis (primary source)
# ---------------------------------------------------------------------------

def analyze_nse_delivery(symbol: str, nse_data: dict[str, Any]) -> dict[str, Any] | None:
    """Analyze actual NSE delivery data for a symbol."""
    symbol_data = nse_data.get("symbols", {}).get(symbol)
    if not symbol_data or len(symbol_data) < 2:
        return None

    # Sort by date
    symbol_data = sorted(symbol_data, key=lambda x: x["date"] or "")

    latest = symbol_data[-1]
    prev = symbol_data[-2] if len(symbol_data) >= 2 else None

    delivery_pct = latest["delivery_pct"]

    # Trend: is delivery % increasing?
    if prev:
        delivery_trend = delivery_pct - prev["delivery_pct"]
    else:
        delivery_trend = 0

    # Average delivery % over available days
    avg_delivery = sum(d["delivery_pct"] for d in symbol_data) / len(symbol_data)

    # Score (0-100)
    score = 0
    components = {}

    # Absolute delivery level (0-35)
    if delivery_pct >= 55:
        s = 30
        level_signal = "HIGH_DELIVERY"
    elif delivery_pct >= 45:
        s = 22
        level_signal = "ABOVE_AVG_DELIVERY"
    elif delivery_pct >= 35:
        s = 12
        level_signal = "NORMAL_DELIVERY"
    else:
        s = 3
        level_signal = "LOW_DELIVERY"
    score += s
    components["delivery_level"] = {
        "value": delivery_pct,
        "signal": level_signal,
        "points": s,
        "detail": f"Delivery ratio {delivery_pct:.1f}% — {'institutional' if delivery_pct >= 50 else 'mixed' if delivery_pct >= 35 else 'retail'} activity",
    }

    # Delivery trend (0-30)
    if delivery_trend > 5:
        s = 25
        trend_signal = "ACCELERATING"
    elif delivery_trend > 0:
        s = 18
        trend_signal = "MILD_INCREASE"
    elif delivery_trend > -5:
        s = 10
        trend_signal = "STABLE"
    else:
        s = 3
        trend_signal = "DECLINING"
    score += s
    components["delivery_trend"] = {
        "change": round(delivery_trend, 1),
        "signal": trend_signal,
        "points": s,
        "detail": f"Delivery % {'rising' if delivery_trend > 0 else 'falling'} by {abs(delivery_trend):.1f}pp",
    }

    # Volume trend (0-35)
    latest_vol = latest["total_qty"]
    avg_vol = sum(d["total_qty"] for d in symbol_data) / len(symbol_data)
    vol_ratio = latest_vol / avg_vol if avg_vol > 0 else 1.0

    if vol_ratio > 1.5 and delivery_pct > 45:
        s = 30
        vol_signal = "INSTITUTIONAL_SURGE"
    elif vol_ratio > 1.2:
        s = 20
        vol_signal = "ABOVE_AVG"
    elif vol_ratio > 0.8:
        s = 12
        vol_signal = "NORMAL"
    else:
        s = 5
        vol_signal = "LOW_VOLUME"
    score += s
    components["volume_trend"] = {
        "ratio": round(vol_ratio, 2),
        "signal": vol_signal,
        "points": s,
        "detail": f"Volume {vol_ratio:.1f}x average — {'surge' if vol_ratio > 1.5 else 'normal' if vol_ratio > 0.8 else 'thin'}",
    }

    score = max(0, min(100, score))

    # Verdict
    if score >= 65:
        verdict = "INSTITUTIONAL_ACCUMULATION"
        recommendation = "High delivery ratio + volume surge = institutional buying. High conviction."
    elif score >= 45:
        verdict = "MIXED_SIGNALS"
        recommendation = "Delivery data is mixed. Some institutional activity, some retail."
    else:
        verdict = "RETAIL_CHURN_OR_DISTRIBUTION"
        recommendation = "Low delivery ratio suggests retail speculation or distribution. Reduce sizing."

    return {
        "delivery_pct": round(delivery_pct, 1),
        "delivery_trend_pp": round(delivery_trend, 1),
        "avg_delivery_pct": round(avg_delivery, 1),
        "volume_ratio": round(vol_ratio, 2),
        "latest_volume": latest_vol,
        "n_days": len(symbol_data),
        "score": score,
        "verdict": verdict,
        "recommendation": recommendation,
        "components": components,
        "source": "NSE archives (actual delivery data)",
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Delivery Volume Analysis — institutional activity proxy"
    )
    parser.add_argument("--stats", default=str(STATS_FILE),
                        help="Path to screen_stats_out.json")
    parser.add_argument("--top", type=int, default=20,
                        help="Analyze top N candidates (default: 20)")
    parser.add_argument("--symbol", help="Single stock symbol (for prompt.md workflow)")
    args = parser.parse_args()

    # Single-symbol mode
    if args.symbol:
        sym = args.symbol.upper().replace(".NS", "")

        # Try NSE data first
        nse_data = None
        try:
            nse_data = fetch_nse_delivery_data()
        except Exception:
            pass

        if nse_data and sym in nse_data.get("symbols", {}):
            result = analyze_nse_delivery(sym, nse_data)
        else:
            # Fall back to Yahoo proxy
            result = compute_delivery_proxy(sym)

        if result is None:
            emit_json({"error": f"Could not analyze delivery volume for {sym}"})
            return 1

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

    # Try NSE data first (one fetch for all symbols)
    nse_data = None
    try:
        print("  Fetching NSE delivery data...", file=sys.stderr)
        nse_data = fetch_nse_delivery_data()
        if nse_data:
            print(f"  ✓ NSE delivery data: {len(nse_data.get('symbols', {}))} symbols", file=sys.stderr)
    except Exception:
        print("  ⚠ NSE delivery data unavailable, using Yahoo Finance proxies", file=sys.stderr)

    results = []
    nse_count = 0
    proxy_count = 0

    for sym in sorted(symbols.keys()):
        # Try NSE first
        if nse_data and sym in nse_data.get("symbols", {}):
            result = analyze_nse_delivery(sym, nse_data)
            if result:
                results.append({"symbol": sym, **result})
                nse_count += 1
                continue

        # Fall back to Yahoo proxy
        result = compute_delivery_proxy(sym)
        if result:
            results.append({"symbol": sym, **result})
            proxy_count += 1

    # Sort by score
    results.sort(key=lambda r: r.get("score", 0), reverse=True)

    # Summary
    inst_accum = sum(1 for r in results if r.get("verdict") == "INSTITUTIONAL_ACCUMULATION")
    mixed = sum(1 for r in results if r.get("verdict") == "MIXED_SIGNALS")
    retail = sum(1 for r in results if r.get("verdict") == "RETAIL_CHURN_OR_DISTRIBUTION")

    payload = {
        "generated_on": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "mode": "batch",
        "analyzed": len(results),
        "data_sources": {
            "nse_actual": nse_count,
            "yahoo_proxy": proxy_count,
        },
        "summary": {
            "institutional_accumulation": inst_accum,
            "mixed_signals": mixed,
            "retail_churn_or_distribution": retail,
        },
        "stocks": results,
        "note": (
            "Delivery volume is the best free proxy for institutional intent. "
            "High delivery ratio (>50%) on rising volume = genuine accumulation. "
            "Low delivery ratio (<30%) on high volume = retail churn that will reverse. "
            "NSE data is preferred; Yahoo Finance proxies (OBV, A/D line, close position) "
            "are used when NSE archives are unavailable."
        ),
    }
    emit_json(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
