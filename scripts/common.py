"""Shared helpers for the trade validation pipeline.

All paths are resolved relative to the project root so every script
works identically no matter which directory it is invoked from.
"""

from __future__ import annotations

import calendar
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Files the pipeline reads/writes, all at the project root.
INPUT_FILE = PROJECT_ROOT / "current_trade.json"
OUT1_FILE = PROJECT_ROOT / "script1_out.json"
OUT2_FILE = PROJECT_ROOT / "script2_out.json"
OUT3_FILE = PROJECT_ROOT / "script3_out.json"
OUT4_FILE = PROJECT_ROOT / "script4_out.json"
OUT5_FILE = PROJECT_ROOT / "script5_out.json"
OUT6_FILE = PROJECT_ROOT / "script6_out.json"
DB_FILE = PROJECT_ROOT / "univest_history.db"
REPORT_FILE = PROJECT_ROOT / "decision_report.md"


def load_json(path: Path) -> dict[str, Any]:
    """Load a JSON file into a dict. Raises on missing/invalid input."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict[str, Any]) -> None:
    """Write a dict as pretty JSON to a file, creating parent dirs."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def emit_json(data: dict[str, Any]) -> None:
    """Print a dict as JSON to stdout — the machine-readable contract
    every script in this suite honours."""
    # Windows consoles default to cp1252; the JSON contract may carry
    # non-ASCII text (₹, ≥, → …), so force UTF-8 on stdout. Idempotent.
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print(json.dumps(data, indent=2, ensure_ascii=False))


def fail(message: str) -> None:
    """Emit a JSON error payload and exit non-zero."""
    emit_json({"error": message})
    sys.exit(1)


def indian_currency(value: float) -> str:
    """Format a number as Indian-rupee currency, e.g. 26647.5 -> ₹26,647.50."""
    return f"₹{value:,.2f}"


def wilson_ci(wins: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    """Wilson 95% confidence interval for a proportion (wins/n).

    Returns (low, high) as raw proportions in [0, 1]. This is the honest
    uncertainty around an observed win rate: with small samples the interval
    is wide, and decision rules that gate on the lower bound (see
    `scripts/4_score_calculator.py`) automatically demand more evidence before
    calling an edge real.
    """
    if n <= 0:
        return 0.0, 0.0
    p = wins / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * (p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5 / denom
    return max(0.0, center - half), min(1.0, center + half)


# ---- Expiry parsing -------------------------------------------------------
# Univest supplies expiry as a label like "25 AUG" (occasionally with a year
# or in ISO form). Parsing it into a real date lets the pipeline do two honest
# things: (1) score event risk by days-to-expiry, and (2) resolve OPEN
# recommendations that never hit target or stoploss once expiry passes.

_MONTHS = {m: i for i, m in enumerate([
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
], start=1)}


def parse_expiry(expiry_text: str | None, today: date | None = None) -> date | None:
    """Parse a Univest expiry label into a date.

    Accepted forms: "25 AUG", "25AUG", "25 AUG 2026", "25-AUG-2026",
    "2026-08-25". A missing year means the current year; if the resulting
    date is already in the past it rolls to the next year. Returns None when
    the text cannot be parsed (the caller treats that as "expiry unknown" and
    waives the event-safety check rather than guessing).
    """
    if not expiry_text:
        return None
    today = today or date.today()
    text = str(expiry_text).strip().upper().replace("/", "-")

    m = re.match(r"^(\d{1,2})[\s-]*([A-Z]{3,9})(?:[\s-]+(\d{4}))?$", text)
    if m:
        day = int(m.group(1))
        month_num = _MONTHS.get(m.group(2)[:3])
        if not month_num:
            return None
        year = int(m.group(3)) if m.group(3) else today.year
        try:
            parsed = date(year, month_num, day)
        except ValueError:
            return None
        if not m.group(3) and parsed < today:
            # No year given and the date has passed — assume next year.
            year += 1
            parsed = date(year, month_num, min(day, calendar.monthrange(year, month_num)[1]))
        return parsed

    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None

    return None


def days_to_expiry(expiry_text: str | None, today: date | None = None) -> tuple[date | None, int | None]:
    """Return (expiry_date, days_remaining) or (None, None) when unparsable."""
    parsed = parse_expiry(expiry_text, today)
    if parsed is None:
        return None, None
    return parsed, (parsed - (today or date.today())).days


def flip_prices(sl: float, target: float, range_min: float | None = None, range_max: float | None = None) -> dict[str, Any]:
    """Price thresholds that flip the price-dependent checks of a reco.

    The price P at which live (reward)/(risk) equals r is the same formula
    for BUY and SELL: P = (target + r*sl) / (1 + r). A BUY improves at or
    below P (buy lower -> more reward, less risk); a SELL improves at or
    above P. Callers apply the direction.
    """
    return {
        "rr_1_5_price": round((target + 1.5 * sl) / 2.5, 2),
        "rr_1_0_price": round((target + sl) / 2.0, 2),
        "range_min": range_min,
        "range_max": range_max,
    }


# ---- Transaction-cost model ------------------------------------------------
# Resolution P&L must not flatter itself: brokerage, STT, exchange charges and
# GST are real. This is a deliberately simple, conservative percentage-of-
# notional model (notional = entry price x lot size) — good enough to keep the
# tracked record honest, tunable in one place.
#   FUT: STT 0.0125% (sell) + exchange ~0.002% x2 + brokerage ~0.03% x2 + GST
#       -> ~0.09% of notional round-trip.
#   OPT: STT 0.0625% (sell) + exchange ~0.035% x2 + brokerage ~0.03% x2 + GST
#       -> ~0.25% of notional (premium) round-trip.

COST_RATE_FUT = 0.0009   # ~0.09% of notional, round-trip
COST_RATE_OPT = 0.0025   # ~0.25% of notional, round-trip


# ---- Capital / position-sizing model (daily screen) -------------------------
# The screen plans trade size for a fixed capital base. Sizing is risk-first:
# target risk per pick is RISK_PER_TRADE_PCT of capital, but a pick must deploy
# at least MIN_DEPLOYMENT_PCT of capital (the user's floor) so positions are
# meaningful. MAX_RISK_PCT is a soft cap — when the 30% floor would push a pick
# past it (very wide stops), the recorder sizes to the floor anyway and warns.

CAPITAL_INR = 500_000            # hard capital base — 5L
RISK_PER_TRADE_PCT = 0.01        # target risk per pick: 1% = ₹5,000
MIN_DEPLOYMENT_PCT = 0.30        # floor: never deploy less than 30% = ₹1,50,000
MAX_RISK_PCT = 0.015             # soft per-pick risk cap: 1.5% = ₹7,500
MAX_TOTAL_DEPLOYMENT_PCT = 1.0   # portfolio cap: total deployed <= 100% of capital


def percentile_sorted(sorted_values: list[float], p: float) -> float:
    """Linear-interpolated percentile of an already-sorted list (0..100).

    Matches numpy's default for a sequence: position = (n-1)*p/100,
    interpolating between neighbours. Used for forward-return distributions
    (median, IQR, P10/P90) without pulling numpy into the hot path.
    """
    n = len(sorted_values)
    if n == 0:
        return 0.0
    if n == 1:
        return float(sorted_values[0])
    pos = (n - 1) * p / 100.0
    lo = int(pos)
    frac = pos - lo
    if lo + 1 >= n:
        return float(sorted_values[-1])
    return float(sorted_values[lo] + frac * (sorted_values[lo + 1] - sorted_values[lo]))


def estimate_costs(entry_price: float, lot_size: float, instrument_type: str | None) -> float:
    """Model transaction costs for one trade, in INR."""
    if not lot_size or lot_size <= 0:
        return 0.0
    notional = entry_price * lot_size
    rate = COST_RATE_OPT if (instrument_type or "").upper() == "OPT" else COST_RATE_FUT
    return round(notional * rate, 2)


# ---- Sector mapping for NSE-150 stocks ------------------------------------
# Approximate sector classification for ranking and rotation analysis.
# Used by screen_stats.py to compute sector-level RS vs NIFTY.

SECTOR_MAP: dict[str, str] = {
    # Banks & Financial Services
    "HDFCBANK": "BANKS", "ICICIBANK": "BANKS", "KOTAKBANK": "BANKS",
    "SBIN": "BANKS", "AXISBANK": "BANKS", "INDUSINDBK": "BANKS",
    "BANDHANBNK": "BANKS", "FEDERALBNK": "BANKS", "IDFCFIRSTB": "BANKS",
    "PNB": "BANKS", "BANKBARODA": "BANKS", "CANBK": "BANKS",
    "AUBANK": "BANKS", "MUTHOOTFIN": "BANKS", "HDFCLIFE": "BANKS",
    "SBILIFE": "BANKS", "BAJFINANCE": "BANKS", "BAJAJFINSV": "BANKS",
    "CHOLAFIN": "BANKS", "SRIRAMFIN": "BANKS", "ABFRL": "BANKS",
    # IT & Technology
    "TCS": "IT", "INFY": "IT", "WIPRO": "IT", "TECHM": "IT",
    "HCLTECH": "IT", "LTIM": "IT", "MPHASIS": "IT", "COFORGE": "IT",
    "PERSISTENT": "IT", "COGNIZANT": "IT", "LTTS": "IT", "DATAPATTNS": "IT",
    # Oil, Gas & Energy
    "RELIANCE": "ENERGY", "ONGC": "ENERGY", "IOC": "ENERGY",
    "BPCL": "ENERGY", "HINDPETRO": "ENERGY", "GAIL": "ENERGY",
    "ADANIGREEN": "ENERGY", "ADANIPORTS": "ENERGY", "TATAPOWER": "ENERGY",
    "NTPC": "ENERGY", "POWERGRID": "ENERGY", "ADANIENSOL": "ENERGY",
    # Metals & Mining
    "TATASTEEL": "METALS", "JSWSTEEL": "METALS", "HINDALCO": "METALS",
    "VEDL": "METALS", "NMDC": "METALS", "COALINDIA": "METALS",
    "SAIL": "METALS", "JSPL": "METALS", "HINDZINC": "METALS",
    # Automobiles
    "MARUTI": "AUTO", "M&M": "AUTO", "TATAMOTORS": "AUTO",
    "BAJAJ-AUTO": "AUTO", "HEROMOTOCO": "AUTO", "TVSMOTOR": "AUTO",
    "EICHERMOT": "AUTO", "ASHOKLEY": "AUTO", "TATAELXSI": "AUTO",
    "MOTHERSON": "AUTO", "SUNDRMFAST": "AUTO", "SONACOMS": "AUTO",
    "OLECTRA": "AUTO", "MRF": "AUTO", "BOSCHLTD": "AUTO",
    # Consumer Goods (Staples + Discretionary)
    "ITC": "CONS", "HUL": "CONS", "NESTLEIND": "CONS",
    "BRITANNIA": "CONS", "DABUR": "CONS", "MARICO": "CONS",
    "COLPAL": "CONS", "EMAMILTD": "CONS", "VBL": "CONS",
    "TATACONSUM": "CONS", "UBER": "CONS", "RADICO": "CONS",
    # Pharma & Healthcare
    "SUNPHARMA": "PHARMA", "DRREDDY": "PHARMA", "CIPLA": "PHARMA",
    "DIVISLAB": "PHARMA", "TORNTPHARM": "PHARMA", "AUROPHARMA": "PHARMA",
    "LALPATHLAB": "PHARMA", "ALKEM": "PHARMA", "GLENMARK": "PHARMA",
    "IPCALAB": "PHARMA", "ZYDUSLIFE": "PHARMA", "BIOCON": "PHARMA",
    # Real Estate
    "DLF": "REALESTATE", "GODREJPROP": "REALESTATE", "OBEROIRLTY": "REALESTATE",
    "PRESTIGE": "REALESTATE", "BRIGADE": "REALESTATE", "SOBHA": "REALESTATE",
    "LODHA": "REALESTATE",
    # Infrastructure & Capital Goods
    "LT": "INFRA", "ADANIENT": "INFRA", "SIEMENS": "INFRA",
    "ABB": "INFRA", "BHEL": "INFRA", "THERMAX": "INFRA",
    "CUMMINSIND": "INFRA", "HONAUT": "INFRA", "TATACOMM": "INFRA",
    # Telecom
    "BHARTIARTL": "TELECOM", "IDEA": "TELECOM",
    # Insurance
    "ICICIPRULI": "INSURANCE", "SBILIFE": "INSURANCE", "HDFCLIFE": "INSURANCE",
    # Media & Entertainment
    "ZOMATO": "MEDIA", "NYKAA": "MEDIA", "DELHIVERY": "MEDIA",
    # Chemicals
    "UPL": "CHEMICALS", "DEEPAKNTR": "CHEMICALS", "SRF": "CHEMICALS",
    "ATUL": "CHEMICALS", "NAVINFLUOR": "CHEMICALS", "TTKHEALTH": "CHEMICALS",
    "FLUOROCHEM": "CHEMICALS", "CLEAN": "CHEMICALS",
    # Miscellaneous / Conglomerates
    "TATAPOWER": "POWER", "ADANIGREEN": "POWER", "NTPC": "POWER",
    "POWERGRID": "POWER",
    # Textiles
    "GRASIM": "CEMENT", "ULTRACEMCO": "CEMENT", "AMBUJACEM": "CEMENT",
    "ACC": "CEMENT", "SHREECEM": "CEMENT",
}


def get_sector(symbol: str) -> str:
    """Return the sector for a symbol, or 'OTHERS' if unknown."""
    return SECTOR_MAP.get(symbol.upper(), "OTHERS")


# ---- Technical indicators for momentum/mean-reversion signals --------------
# All functions take a pandas DataFrame with columns Open, High, Low, Close, Volume.
# They are intentionally standalone (not class-based) for easy embedding.

def compute_rsi(close_series, period: int = 14):
    """RSI (Relative Strength Index) over `period` bars.

    Returns a Series aligned to the input. Classic Wilder smoothing
    (EWM with alpha=1/period).
    """
    delta = close_series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def compute_macd(close_series, fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD line, signal line, and histogram.

    Returns (macd_line, signal_line, histogram) as Series.
    """
    ema_fast = close_series.ewm(span=fast, adjust=False).mean()
    ema_slow = close_series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def compute_bollinger(close_series, period: int = 20, num_std: float = 2.0):
    """Bollinger Bands: middle (SMA), upper, lower, bandwidth (%).

    Bandwidth = (upper - lower) / middle — a volatility-expansion measure.
    Returns (middle, upper, lower, bandwidth) as Series.
    """
    middle = close_series.rolling(period).mean()
    std = close_series.rolling(period).std()
    upper = middle + num_std * std
    lower = middle - num_std * std
    bandwidth = ((upper - lower) / middle.replace(0, float("nan"))) * 100.0
    return middle, upper, lower, bandwidth


def compute_volume_surge(volume_series, period: int = 20):
    """Volume ratio vs its `period`-bar moving average.

    Returns a Series: volume_ratio = current_vol / avg_vol.
    Values > 2.0 are considered significant surges.
    """
    avg_vol = volume_series.rolling(period).mean()
    return volume_series / avg_vol.replace(0, float("nan"))


def momentum_composite(rsi: float, macd_hist: float, bb_width: float,
                       bb_width_avg: float, vol_ratio: float, ema_bullish: bool,
                       atr_pct: float) -> dict[str, Any]:
    """Composite momentum / mean-reversion signal (0–100).

    Components and weights:
        RSI position (0-25): extreme RSI (<30 or >70) for mean-reversion;
            neutral RSI (40-60) with momentum is best for trend-following.
        MACD histogram (0-25): positive and rising = strong momentum.
        Bollinger squeeze (0-20): low bandwidth = volatility expansion imminent.
        Volume surge (0-15): >1.5x average = institutional interest.
        Trend alignment (0-15): close > EMA50 = trend support.

    Returns {score, direction, components}.
    """
    score = 0.0
    components: dict[str, dict[str, Any]] = {}

    # 1. RSI signal (0-25)
    rsi_score = 0.0
    rsi_dir = "NEUTRAL"
    if rsi >= 70:
        rsi_score = 5.0  # overbought — risky for BUY
        rsi_dir = "OVERBOUGHT"
    elif rsi <= 30:
        rsi_score = 25.0  # oversold — mean-reversion BUY signal
        rsi_dir = "OVERSOLD"
    elif 50 <= rsi <= 60:
        rsi_score = 20.0  # healthy momentum zone
        rsi_dir = "BULLISH"
    elif rsi > 60:
        rsi_score = 15.0  # strong but approaching overbought
        rsi_dir = "STRONG_BULL"
    elif rsi < 40:
        rsi_score = 8.0  # weak — possible accumulation
        rsi_dir = "WEAK"
    else:
        rsi_score = 12.0  # 40-50: neutral
        rsi_dir = "NEUTRAL"
    score += rsi_score
    components["rsi"] = {"value": round(rsi, 1), "signal": rsi_dir, "points": round(rsi_score, 1)}

    # 2. MACD histogram signal (0-25)
    macd_score = 0.0
    macd_dir = "NEUTRAL"
    if macd_hist > 0:
        macd_score = min(25.0, 15.0 + abs(macd_hist) * 2.0)
        macd_dir = "BULLISH"
    elif macd_hist < 0:
        macd_score = max(0.0, 10.0 - abs(macd_hist) * 2.0)
        macd_dir = "BEARISH"
    else:
        macd_score = 12.0
        macd_dir = "NEUTRAL"
    score += macd_score
    components["macd"] = {"histogram": round(macd_hist, 4), "signal": macd_dir, "points": round(macd_score, 1)}

    # 3. Bollinger squeeze (0-20) — low bandwidth = volatility expansion imminent
    bb_score = 0.0
    bb_dir = "NORMAL"
    if bb_width_avg > 0:
        bb_ratio = bb_width / bb_width_avg
        if bb_ratio < 0.7:
            bb_score = 20.0  # strong squeeze
            bb_dir = "SQUEEZE"
        elif bb_ratio < 0.85:
            bb_score = 14.0  # mild squeeze
            bb_dir = "TIGHTENING"
        elif bb_ratio > 1.5:
            bb_score = 5.0  # expanded — less likely to break out
            bb_dir = "EXPANDED"
        else:
            bb_score = 10.0
            bb_dir = "NORMAL"
    else:
        bb_score = 10.0  # no history, neutral
    score += bb_score
    components["bollinger"] = {"width": round(bb_width, 2), "signal": bb_dir, "points": round(bb_score, 1)}

    # 4. Volume surge (0-15)
    vol_score = 0.0
    vol_dir = "NORMAL"
    if vol_ratio >= 2.5:
        vol_score = 15.0
        vol_dir = "SURGE"
    elif vol_ratio >= 1.5:
        vol_score = 12.0
        vol_dir = "ABOVE_AVG"
    elif vol_ratio >= 1.0:
        vol_score = 8.0
        vol_dir = "NORMAL"
    else:
        vol_score = 3.0
        vol_dir = "LOW"
    score += vol_score
    components["volume"] = {"ratio": round(vol_ratio, 2), "signal": vol_dir, "points": round(vol_score, 1)}

    # 5. Trend alignment (0-15)
    trend_score = 15.0 if ema_bullish else 3.0
    trend_dir = "BULLISH" if ema_bullish else "BEARISH"
    score += trend_score
    components["trend"] = {"above_ema50": ema_bullish, "signal": trend_dir, "points": round(trend_score, 1)}

    final_score = round(min(100.0, max(0.0, score)), 1)

    # Direction: BUY if bullish consensus (trend + RSI not overbought + MACD positive)
    if ema_bullish and macd_hist > 0 and rsi < 75:
        direction = "BUY"
    elif not ema_bullish and macd_hist < 0 and rsi > 25:
        direction = "SELL"
    else:
        direction = "NEUTRAL"

    return {
        "score": final_score,
        "direction": direction,
        "components": components,
    }
