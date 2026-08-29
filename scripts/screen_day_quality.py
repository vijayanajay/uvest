"""Day Quality Filter — should I trade today?

Scores each trading day 0-100 based on four static factors plus a
conditions-momentum overlay:

STATIC (absolute level):
    1. VIX percentile rank (low VIX = better for directional trades)
    2. FII/DII net cash flow (positive flows = follow-through)
    3. Market breadth (from screen_stats_out.json)
    4. Calendar effects (expiry week, month-end, day-of-week patterns)

MOMENTUM (rate of change — the Kailash Nadh layer):
    5. Conditions momentum (0-30 bonus/penalty):
       - VIX compression (VIX falling = squeeze setup = imminent move)
       - Breadth thrust (breadth improving = follow-through)
       - FII flow acceleration (buying accelerating = conviction)

The key insight: a day where VIX is 18 but falling from 22 is BETTER
than a day where VIX is 16 but rising from 14. The absolute level says
"acceptable" — the momentum says "squeeze incoming."

Output: screen_day_quality_out.json with:
    score (0-130 before clamping to 100), verdict, factors breakdown,
    conditions_momentum overlay, and action recommendation

All data sources are free and open:
    - India VIX: yfinance ^INDIAVIX
    - FII/DII flows: NSE archives (nsearchives.nseindia.com)
    - Breadth: from screen_stats_out.json + cached previous breadth
    - Calendar: Python stdlib

Usage:
    python scripts/screen_day_quality.py
        [--stats screen_stats_out.json]
        [--regime screen_regime_out.json]
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import PROJECT_ROOT, emit_json, load_json  # noqa: E402

STATS_FILE = PROJECT_ROOT / "screen_stats_out.json"
REGIME_FILE = PROJECT_ROOT / "screen_regime_out.json"
NIFTY_CACHE = PROJECT_ROOT / "data_cache" / "NIFTY50.csv"
BREADTH_HISTORY_FILE = PROJECT_ROOT / "data_cache" / "breadth_history.json"
FII_HISTORY_FILE = PROJECT_ROOT / "data_cache" / "fii_history.json"

NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "text/csv,application/csv,*/*",
    "Accept-Language": "en-IN,en;q=0.9",
}

# ---- NSE exchange holiday list (2026 NSE holidays — update yearly) --------
NSE_HOLIDAYS_2026 = {
    date(2026, 1, 26),   # Republic Day
    date(2026, 3, 6),    # Holi
    date(2026, 4, 2),    # Id-Ul-Fitr
    date(2026, 4, 14),   # Dr Ambedkar Jayanti
    date(2026, 4, 15),   # Shri Mahavir Jayanti
    date(2026, 5, 1),    # Maharashtra Day
    date(2026, 6, 27),   # Eid-Ul-Adha
    date(2026, 8, 15),   # Independence Day
    date(2026, 8, 27),   # Janmashtami
    date(2026, 10, 2),   # Gandhi Jayanti
    date(2026, 10, 21),  # Dussehra
    date(2026, 11, 8),   # Diwali (Laxmi Pujan)
    date(2026, 11, 19),  # Gurunanak Jayanti
    date(2026, 12, 25),  # Christmas
}


# ---- History helpers for conditions momentum --------------------------------
def _load_json_cache(path: Path) -> dict[str, Any]:
    """Load a JSON cache file. Returns empty dict on any failure."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_json_cache(path: Path, data: dict[str, Any]) -> None:
    """Save a dict as JSON, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ---- Factor 1: VIX percentile rank ----------------------------------------
def vix_factor() -> dict[str, Any]:
    """Score 0-30 based on VIX level relative to its own history.

    Low VIX (<14) = directional trades work (score ~25-30)
    Normal VIX (14-20) = acceptable (score ~15-25)
    High VIX (>20) = choppy, avoid (score ~5-15)
    Very High VIX (>25) = danger zone (score ~0-5)

    Also returns prev_level and 5d_avg for conditions momentum.
    """
    try:
        import yfinance as yf
        df = yf.Ticker("^INDIAVIX").history(period="6mo", interval="1d")
        if df is None or df.empty or len(df) < 20:
            return {"score": 15, "level": None, "percentile": None,
                    "regime": "UNKNOWN", "detail": "VIX data unavailable",
                    "prev_level": None, "5d_avg": None}
        close = df["Close"].dropna()
        level = float(close.iloc[-1])
        # Percentile rank over the last 60 trading days
        lookback = close.tail(60)
        percentile = (lookback < level).sum() / len(lookback) * 100.0

        # Previous day and 5-day average for momentum
        prev_level = float(close.iloc[-2]) if len(close) >= 2 else None
        avg_5d = float(close.tail(5).mean()) if len(close) >= 5 else None

        if level < 12:
            score, regime = 28, "VERY_LOW"
        elif level < 14:
            score, regime = 25, "LOW"
        elif level < 17:
            score, regime = 20, "NORMAL"
        elif level < 20:
            score, regime = 15, "ELEVATED"
        elif level < 25:
            score, regime = 8, "HIGH"
        else:
            score, regime = 3, "EXTREME"

        return {
            "score": score, "level": round(level, 2),
            "percentile": round(percentile, 1), "regime": regime,
            "prev_level": round(prev_level, 2) if prev_level else None,
            "5d_avg": round(avg_5d, 2) if avg_5d else None,
            "detail": f"VIX {level:.1f} (P{percentile:.0f} of 60d range) → {regime}",
        }
    except Exception as e:
        return {"score": 15, "level": None, "percentile": None,
                "regime": "UNKNOWN", "detail": f"VIX fetch failed: {e}",
                "prev_level": None, "5d_avg": None}


# ---- Factor 2: FII/DII net cash flow --------------------------------------
def fetch_nse_fii_dii(timeout: float = 15.0) -> dict[str, Any] | None:
    """Fetch daily FII/DII cash market data from NSE archives."""
    url = "https://nsearchives.nseindia.com/content/fii/FII_DII_YEARLY_DATA.CSV"
    try:
        req = urllib.request.Request(url, headers=NSE_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8-sig", errors="replace")
        rows = list(csv.DictReader(io.StringIO(text)))
        if not rows:
            return None
        return {"days": rows[-5:], "source": "NSE archives"}
    except Exception:
        return None


def fii_dii_factor() -> dict[str, Any]:
    """Score 0-30 based on recent FII/DII net cash flow.

    Returns fii_3d_avg and fii_5d_avg for conditions momentum.
    """
    data = fetch_nse_fii_dii()
    if not data:
        return {"score": 15, "fii_net_5d": None, "dii_net_5d": None,
                "fii_trend": "UNKNOWN", "detail": "FII/DII data unavailable from NSE",
                "fii_3d_avg": None, "fii_5d_avg": None, "fii_daily": []}

    fii_total = 0.0
    dii_total = 0.0
    days_parsed = 0
    fii_daily: list[float] = []

    for row in data["days"]:
        try:
            fii_val = None
            dii_val = None
            for key in ("FII", "FII (Buy)", "FII_NET", "Net Purchase/Sales"):
                if key in row:
                    raw = row[key].replace(",", "").replace("₹", "").strip()
                    if raw:
                        fii_val = float(raw)
                        break
            for key in ("DII", "DII (Buy)", "DII_NET", "Net Purchase/Sales.1"):
                if key in row:
                    raw = row[key].replace(",", "").replace("₹", "").strip()
                    if raw:
                        dii_val = float(raw)
                        break
            if fii_val is not None:
                fii_total += fii_val
                fii_daily.append(fii_val)
                days_parsed += 1
            if dii_val is not None:
                dii_total += dii_val
        except (ValueError, KeyError):
            continue

    if days_parsed == 0:
        return {"score": 15, "fii_net_5d": None, "dii_net_5d": None,
                "fii_trend": "UNKNOWN", "detail": "Could not parse FII/DII data",
                "fii_3d_avg": None, "fii_5d_avg": None, "fii_daily": []}

    fii_net_5d = fii_total / 1e7  # Convert to Crores
    dii_net_5d = dii_total / 1e7

    # 3-day and 5-day averages for momentum
    fii_3d_avg = sum(fii_daily[-3:]) / len(fii_daily[-3:]) / 1e7 if len(fii_daily) >= 1 else None
    fii_5d_avg = fii_total / days_parsed / 1e7 if days_parsed > 0 else None

    if fii_net_5d > 2000:
        score = 28
        trend = "STRONG_BUY"
    elif fii_net_5d > 500:
        score = 22
        trend = "BUY"
    elif fii_net_5d > 0:
        score = 18
        trend = "MILD_BUY"
    elif fii_net_5d > -500:
        score = 15
        trend = "MILD_SELL"
    elif fii_net_5d > -2000:
        score = 10
        trend = "SELL"
    else:
        score = 4
        trend = "STRONG_SELL"

    if dii_net_5d > 1000 and fii_net_5d < 0:
        score = min(30, score + 5)

    return {
        "score": score, "fii_net_5d": round(fii_net_5d, 1),
        "dii_net_5d": round(dii_net_5d, 1), "fii_trend": trend,
        "days_parsed": days_parsed,
        "fii_3d_avg": round(fii_3d_avg, 1) if fii_3d_avg is not None else None,
        "fii_5d_avg": round(fii_5d_avg, 1) if fii_5d_avg is not None else None,
        "fii_daily": [round(f / 1e7, 1) for f in fii_daily],
        "detail": (
            f"FII 5d net: "
            f"{'₹' + f'{abs(fii_net_5d):,.0f}Cr' if abs(fii_net_5d) >= 1 else f'{fii_net_5d:.1f}'}"
            f" ({'buying' if fii_net_5d > 0 else 'selling'})"
            f" · DII: "
            f"{'₹' + f'{abs(dii_net_5d):,.0f}Cr' if abs(dii_net_5d) >= 1 else f'{dii_net_5d:.1f}'}"
            f" · Trend: {trend}"
        ),
    }


# ---- Factor 3: Market breadth (from stats) ---------------------------------
def breadth_factor(stats: dict[str, Any] | None) -> dict[str, Any]:
    """Score 0-25 based on % of stocks in bullish trend.

    Also returns prev_pct_bullish for conditions momentum (breadth thrust).
    """
    if not stats or "error" in stats:
        return {"score": 12, "pct_bullish": None, "detail": "Breadth data unavailable",
                "prev_pct_bullish": None}
    symbols = stats.get("symbols", {})
    n = len(symbols)
    if n == 0:
        return {"score": 12, "pct_bullish": None, "detail": "No symbols in universe",
                "prev_pct_bullish": None}
    n_bull = sum(1 for s in symbols.values() if s.get("trend") == "BULLISH")
    pct = n_bull / n * 100.0

    # Load previous breadth for momentum
    prev_pct = None
    hist = _load_json_cache(BREADTH_HISTORY_FILE)
    if "pct_bullish" in hist:
        prev_pct = hist["pct_bullish"]

    # Save current breadth for next run
    _save_json_cache(BREADTH_HISTORY_FILE, {
        "pct_bullish": round(pct, 1),
        "n_bullish": n_bull,
        "n_total": n,
        "date": date.today().isoformat(),
    })

    if pct >= 65:
        score = 25
    elif pct >= 55:
        score = 20
    elif pct >= 45:
        score = 15
    elif pct >= 35:
        score = 8
    else:
        score = 3

    return {
        "score": score, "pct_bullish": round(pct, 1),
        "prev_pct_bullish": prev_pct,
        "n_bullish": n_bull, "n_total": n,
        "detail": f"Breadth {pct:.0f}% ({n_bull}/{n} bullish) → "
                  f"{'strong' if pct >= 55 else 'weak' if pct < 40 else 'mixed'}",
    }


# ---- Factor 4: Calendar effects --------------------------------------------
def calendar_factor(today: date | None = None) -> dict[str, Any]:
    """Score 0-15 based on calendar effects."""
    today = today or date.today()
    score = 10
    effects: list[str] = []

    dow = today.weekday()
    if dow == 0:
        score -= 2
        effects.append("Monday open (gap risk)")
    elif dow == 4:
        score -= 3
        effects.append("Friday close (weekend risk)")
    elif dow in (2, 3):
        score += 3
        effects.append("Mid-week (best for directional trades)")
    elif dow == 1:
        score += 2
        effects.append("Tuesday (follow-through day)")

    if today.day <= 5:
        score += 2
        effects.append("Month-start (fresh allocations)")

    import calendar as cal
    _, last_day = cal.monthrange(today.year, today.month)
    if today.day >= last_day - 2:
        score -= 2
        effects.append("Month-end (MTM volatility)")

    if dow == 3:
        score -= 1
        effects.append("Weekly expiry day")

    for i in range(1, 4):
        check = date.fromordinal(today.toordinal() + i)
        if check in NSE_HOLIDAYS_2026:
            score -= 1
            effects.append(f"Near holiday ({check.strftime('%d %b')})")
            break

    score = max(0, min(15, score))

    return {
        "score": score,
        "day_of_week": today.strftime("%A"),
        "effects": effects,
        "detail": (f"{today.strftime('%A')} {today.strftime('%d %b')} · "
                   + "; ".join(effects) if effects else "Neutral calendar"),
    }


# ---- Factor 5: Conditions momentum (the Nadh layer) -----------------------
def conditions_momentum(vix: dict, fii_dii: dict, breadth: dict) -> dict[str, Any]:
    """Measure the rate of change of market conditions.

    The Kailash Nadh insight: a day where VIX is 18 but falling from 22
    is BETTER than a day where VIX is 16 but rising from 14. The absolute
    level says "acceptable" — the momentum says "squeeze incoming."

    Three components:
    1. VIX compression: is VIX falling? (falling = squeeze setup)
    2. Breadth thrust: is breadth improving? (improving = follow-through)
    3. FII flow acceleration: is buying accelerating? (accelerating = conviction)

    Each component is scored -10 to +10, total -30 to +30.
    Positive = conditions improving = bonus for directional trades.
    Negative = conditions deteriorating = penalty (even if absolute levels are OK).
    """
    components: dict[str, dict[str, Any]] = {}
    raw_score = 0.0

    # --- 1. VIX compression (-10 to +10) ---
    vix_level = vix.get("level")
    vix_prev = vix.get("prev_level")
    vix_5d = vix.get("5d_avg")

    if vix_level is not None and vix_prev is not None:
        vix_change = vix_level - vix_prev
        if vix_change < -1.5:
            vix_score, vix_dir = 10, "COMPRESSING_HARD"
            vix_detail = f"VIX falling sharply ({vix_prev:.1f} → {vix_level:.1f}, Δ{vix_change:+.1f}) — squeeze setup"
        elif vix_change < -0.5:
            vix_score, vix_dir = 7, "COMPRESSING"
            vix_detail = f"VIX falling ({vix_prev:.1f} → {vix_level:.1f}, Δ{vix_change:+.1f}) — volatility contracting"
        elif vix_change < 0.5:
            vix_score, vix_dir = 0, "STABLE"
            vix_detail = f"VIX stable ({vix_prev:.1f} → {vix_level:.1f}, Δ{vix_change:+.1f})"
        elif vix_change < 1.5:
            vix_score, vix_dir = -5, "EXPANDING"
            vix_detail = f"VIX rising ({vix_prev:.1f} → {vix_level:.1f}, Δ{vix_change:+.1f}) — chop incoming"
        else:
            vix_score, vix_dir = -10, "EXPANDING_HARD"
            vix_detail = f"VIX spiking ({vix_prev:.1f} → {vix_level:.1f}, Δ{vix_change:+.1f}) — danger zone"

        # Bonus: VIX below its 5-day average = sustained compression
        if vix_5d is not None and vix_level < vix_5d:
            vix_score = min(10, vix_score + 2)
            vix_detail += f" (below 5d avg {vix_5d:.1f})"
    else:
        vix_score, vix_dir = 0, "UNKNOWN"
        vix_detail = "VIX momentum unavailable (no previous data)"

    raw_score += vix_score
    components["vix_momentum"] = {
        "score": vix_score, "direction": vix_dir, "detail": vix_detail,
    }

    # --- 2. Breadth thrust (-10 to +10) ---
    breadth_pct = breadth.get("pct_bullish")
    breadth_prev = breadth.get("prev_pct_bullish")

    if breadth_pct is not None and breadth_prev is not None:
        breadth_change = breadth_pct - breadth_prev
        if breadth_change > 5:
            brd_score, brd_dir = 10, "THRUST"
            brd_detail = f"Breadth surging ({breadth_prev:.0f}% → {breadth_pct:.0f}%, +{breadth_change:.0f}pp) — strong follow-through expected"
        elif breadth_change > 2:
            brd_score, brd_dir = 6, "IMPROVING"
            brd_detail = f"Breadth improving ({breadth_prev:.0f}% → {breadth_pct:.0f}%, +{breadth_change:.0f}pp)"
        elif breadth_change > -2:
            brd_score, brd_dir = 0, "STABLE"
            brd_detail = f"Breadth stable ({breadth_prev:.0f}% → {breadth_pct:.0f}%, {breadth_change:+.0f}pp)"
        elif breadth_change > -5:
            brd_score, brd_dir = -5, "WEAKENING"
            brd_detail = f"Breadth weakening ({breadth_prev:.0f}% → {breadth_pct:.0f}%, {breadth_change:.0f}pp) — fewer stocks participating"
        else:
            brd_score, brd_dir = -10, "COLLAPSE"
            brd_detail = f"Breadth collapsing ({breadth_prev:.0f}% → {breadth_pct:.0f}%, {breadth_change:.0f}pp) — risk-off"
    else:
        brd_score, brd_dir = 0, "UNKNOWN"
        brd_detail = "Breadth momentum unavailable (no previous data — first run today)"

    raw_score += brd_score
    components["breadth_momentum"] = {
        "score": brd_score, "direction": brd_dir, "detail": brd_detail,
    }

    # --- 3. FII flow acceleration (-10 to +10) ---
    fii_3d = fii_dii.get("fii_3d_avg")
    fii_5d = fii_dii.get("fii_5d_avg")

    if fii_3d is not None and fii_5d is not None and fii_5d != 0:
        # Positive ratio = 3d average > 5d average = buying accelerating
        accel = fii_3d - fii_5d
        if accel > 500:
            fii_score, fii_dir = 10, "ACCELERATING"
            fii_detail = f"FII buying accelerating (3d avg ₹{fii_3d:.0f}Cr > 5d avg ₹{fii_5d:.0f}Cr)"
        elif accel > 100:
            fii_score, fii_dir = 5, "MILD_ACCEL"
            fii_detail = f"FII flow mildly accelerating (3d ₹{fii_3d:.0f} vs 5d ₹{fii_5d:.0f} Cr)"
        elif accel > -100:
            fii_score, fii_dir = 0, "STABLE"
            fii_detail = f"FII flow stable (3d ₹{fii_3d:.0f} vs 5d ₹{fii_5d:.0f} Cr)"
        elif accel > -500:
            fii_score, fii_dir = -5, "DECELERATING"
            fii_detail = f"FII buying decelerating (3d ₹{fii_3d:.0f} < 5d ₹{fii_5d:.0f} Cr)"
        else:
            fii_score, fii_dir = -10, "FLEEING"
            fii_detail = f"FII selling accelerating (3d ₹{fii_3d:.0f}Cr < 5d ₹{fii_5d:.0f}Cr)"
    else:
        fii_score, fii_dir = 0, "UNKNOWN"
        fii_detail = "FII momentum unavailable (insufficient daily data)"

    raw_score += fii_score
    components["fii_momentum"] = {
        "score": fii_score, "direction": fii_dir, "detail": fii_detail,
    }

    # --- Composite conditions momentum (-30 to +30) ---
    composite = max(-30, min(30, raw_score))

    if composite >= 15:
        verdict = "MOMENTUM_BUILDING"
        detail = "Conditions accelerating — market is waking up. Favorable for directional entries."
    elif composite >= 5:
        verdict = "MILD_IMPROVEMENT"
        detail = "Conditions mildly improving. Acceptable for standard setups."
    elif composite >= -5:
        verdict = "NEUTRAL"
        detail = "Conditions flat — no momentum edge. Trade on absolute levels only."
    elif composite >= -15:
        verdict = "MILD_DETERIORATION"
        detail = "Conditions deteriorating — reduce conviction. Only highest-conviction setups."
    else:
        verdict = "MOMENTUM_COLLAPSE"
        detail = "Conditions deteriorating sharply — avoid new entries. Wait for stabilization."

    # Normalize to 0-30 for display (shift from [-30,+30] to [0,30])
    normalized = round(composite + 30)  # [-30,+30] → [0,60], then scale
    display_score = max(0, min(30, round((composite + 30) / 2)))

    return {
        "raw_score": round(composite, 1),
        "display_score": display_score,
        "verdict": verdict,
        "detail": detail,
        "components": components,
        "note": (
            "Conditions momentum measures the rate of change of market conditions. "
            "Positive = conditions improving (squeeze/thrust/acceleration). "
            "Negative = conditions deteriorating (even if absolute levels look OK). "
            "This is the difference between 'VIX is low' and 'VIX is falling'."
        ),
    }


# ---- Composite scoring -----------------------------------------------------
def compute_day_quality(stats: dict[str, Any] | None) -> dict[str, Any]:
    """Combine all factors into a Day Quality Score with conditions momentum.

    Static factors (0-100):
        VIX:       30
        FII/DII:   30
        Breadth:   25
        Calendar:  15
        TOTAL:    100

    Conditions momentum (-30 to +30):
        Applied as a bonus/penalty on top of the static score.
        A STRONG static day with momentum building = even better.
        A TRADING static day with momentum collapsing = downgrade.

    Verdict (from static + momentum):
        >= 70 + positive momentum  → STRONG (full size)
        >= 50 + non-negative       → TRADING (standard size)
        >= 30                      → WASH (reduced size)
        <  30 or momentum collapse → AVOID (no new positions)

    The momentum layer can upgrade or downgrade:
        TRADING (55) + momentum +15 → STRONG (upgrade)
        TRADING (55) + momentum -15 → WASH (downgrade)
    """
    vix = vix_factor()
    fii_dii = fii_dii_factor()
    breadth = breadth_factor(stats)
    calendar = calendar_factor()
    momentum = conditions_momentum(vix, fii_dii, breadth)

    # Static score (same as before)
    static_score = vix["score"] + fii_dii["score"] + breadth["score"] + calendar["score"]
    static_score = max(0, min(100, static_score))

    # Final score: static + momentum (clamped to 0-100)
    final_score = max(0, min(100, static_score + momentum["raw_score"]))

    # Verdict: use final score for band thresholds, but momentum can override
    if final_score >= 70:
        verdict = "STRONG"
        recommendation = (
            "Full conviction. Market conditions favor directional trades "
            "and momentum is building. Take your best setups at full size."
        )
    elif final_score >= 50:
        verdict = "TRADING"
        recommendation = (
            "Standard conviction. Conditions are acceptable. "
            "Take setups at normal size, be disciplined on stops."
        )
    elif final_score >= 30:
        verdict = "WASH"
        recommendation = (
            "Reduced conviction. Mixed signals. Take 50% size max, "
            "only the highest-conviction setups, and tighten stops."
        )
    else:
        verdict = "AVOID"
        recommendation = (
            "Do not trade today. Market conditions are unfavorable "
            "for directional trades. Preserve capital."
        )

    # Momentum override: if momentum is collapsing hard, cap at WASH
    if momentum["raw_score"] <= -20 and verdict in ("STRONG", "TRADING"):
        verdict = "WASH"
        recommendation = (
            "Static conditions look acceptable but momentum is collapsing. "
            "Reduced conviction — wait for stabilization. 50% size max."
        )

    # Momentum upgrade: if static is TRADING but momentum is building hard
    if momentum["raw_score"] >= 15 and verdict == "TRADING":
        verdict = "STRONG"
        recommendation = (
            "Static conditions are standard BUT momentum is building strongly. "
            "Favorable for directional entries. Take best setups at full size."
        )

    return {
        "generated_on": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "score": final_score,
        "static_score": static_score,
        "verdict": verdict,
        "recommendation": recommendation,
        "factors": {
            "vix": vix,
            "fii_dii": fii_dii,
            "breadth": breadth,
            "calendar": calendar,
        },
        "conditions_momentum": momentum,
        "max_points": {"vix": 30, "fii_dii": 30, "breadth": 25, "calendar": 15,
                       "momentum": "−30 to +30 overlay"},
        "note": (
            "Day Quality is a risk gate, not an alpha claim. It answers "
            "'should I trade today?' — not 'what should I trade?'. "
            "The conditions momentum layer answers the Nadh question: "
            "'is today better than yesterday?' — not just 'is today OK?'."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Day Quality Filter — should I trade today?")
    parser.add_argument("--stats", default=str(STATS_FILE),
                        help="Path to screen_stats_out.json (for breadth)")
    args = parser.parse_args()

    stats = None
    try:
        stats = load_json(Path(args.stats))
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    result = compute_day_quality(stats)
    emit_json(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
