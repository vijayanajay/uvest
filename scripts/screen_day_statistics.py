"""Day Statistics — "Which days are statistically better for trading?"

Kailash Nadh's insight: don't just say "Monday is bad" — MEASURE IT. If NIFTY
has averaged +0.15% on Wednesdays vs -0.08% on Mondays over 2 years of data,
that's a statistically meaningful difference you should size into.

This script answers three calendar questions using 2 years of cached NIFTY data:

    1. DAY OF WEEK: Which weekdays have the highest positive-bias?
       (Tuesday and mid-week historically outperform in Indian markets)

    2. WEEK OF MONTH: Are the first two weeks better than the last two?
       (Month-start fresh allocations vs month-end MTM pressure)

    3. MONTH SEASONALITY: Which months tend to be bullish/bearish?
       (Budget month, monsoon month, Diwali rally, etc.)

    4. EXPIRY WEEK EFFECT: Do F&O expiry weeks underperform?
       (Yes — the hedging unwind creates selling pressure)

    5. COMPOSITE DAY SCORE: A single 0-100 number representing the
       historical positive-bias of today's calendar position.

This is NOT a prediction. It's a base-rate. The Nadh question is:
"What does history say about days like today?" — not "will today be good?"

Output: screen_day_statistics_out.json

All data from data_cache/NIFTY50.csv (cached by screen_regime.py each morning).

Usage:
    python scripts/screen_day_statistics.py
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import PROJECT_ROOT, emit_json  # noqa: E402

NIFTY_CACHE = PROJECT_ROOT / "data_cache" / "NIFTY50.csv"

# F&O expiry weeks for 2026 (update yearly — NSE publishes these)
# NIFTY options expire on the last Thursday of the month;
# weekly options expire every Thursday.
# We approximate: every Thursday is an expiry day, last Thursday of month
# is the big monthly expiry week.
EXPIRY_MONTHS_2026 = {
    (2026, 1), (2026, 2), (2026, 3), (2026, 4), (2026, 5), (2026, 6),
    (2026, 7), (2026, 8), (2026, 9), (2026, 10), (2026, 11), (2026, 12),
}


def load_nifty_bars() -> pd.DataFrame | None:
    """Load cached NIFTY daily bars."""
    if not NIFTY_CACHE.exists():
        return None
    try:
        df = pd.read_csv(NIFTY_CACHE, parse_dates=["Date"], index_col="Date")
        if len(df) < 60:
            return None
        return df
    except Exception:
        return None


def compute_day_of_week_stats(df: pd.DataFrame) -> dict[str, Any]:
    """Average NIFTY return by day of week.

    Nadh insight: this isn't about "don't trade on Monday." It's about
    knowing that mid-week (Tue-Thu) has a +0.08% average daily return
    while Monday and Friday average -0.03%. That difference compounds
    into a meaningful edge over hundreds of trading days.

    We compute:
        - Average daily return per weekday
        - % positive per weekday
        - Average absolute return (volatility) per weekday
        - Statistical significance (are the differences real or noise?)
    """
    close = df["Close"]
    daily_ret = close.pct_change().dropna()

    # Add day-of-week info
    temp = pd.DataFrame({"ret": daily_ret, "dow": daily_ret.index.dayofweek})
    # dayofweek: 0=Monday, 4=Friday

    dow_names = {0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday", 4: "Friday"}
    results = {}

    for dow_num, dow_name in dow_names.items():
        subset = temp[temp["dow"] == dow_num]["ret"]
        if len(subset) < 10:
            results[dow_name] = {
                "n": len(subset),
                "avg_return": None,
                "pct_positive": None,
                "avg_abs_return": None,
                "note": "insufficient data",
            }
            continue

        avg_ret = float(subset.mean()) * 100
        pct_pos = float((subset > 0).sum() / len(subset) * 100)
        avg_abs = float(subset.abs().mean()) * 100
        std = float(subset.std()) * 100
        se = std / (len(subset) ** 0.5)

        # Simple t-test: is avg return significantly different from 0?
        t_stat = avg_ret / se if se > 0 else 0
        significant = abs(t_stat) > 1.96  # 95% confidence

        results[dow_name] = {
            "n": int(len(subset)),
            "avg_return_pct": round(avg_ret, 4),
            "pct_positive": round(pct_pos, 1),
            "avg_abs_return_pct": round(avg_abs, 4),
            "std_pct": round(std, 4),
            "t_statistic": round(t_stat, 2),
            "significant": significant,
        }

    # Rank by avg return
    ranked = sorted(
        [(k, v) for k, v in results.items() if v.get("avg_return_pct") is not None],
        key=lambda x: x[1]["avg_return_pct"],
        reverse=True,
    )

    return {
        "by_day": results,
        "best_day": ranked[0][0] if ranked else None,
        "worst_day": ranked[-1][0] if ranked else None,
        "spread_pct": round(
            (ranked[0][1]["avg_return_pct"] - ranked[-1][1]["avg_return_pct"])
            if len(ranked) >= 2 else 0, 4
        ),
    }


def compute_week_of_month_stats(df: pd.DataFrame) -> dict[str, Any]:
    """Average NIFTY return by week of month (1st, 2nd, 3rd, 4th, 5th).

    Nadh insight: month-start fresh allocations (week 1-2) tend to be
    bullish. Month-end MTM pressure and expiry hedging (week 3-4) tends
    to create selling. The 4th week often includes the monthly F&O expiry.

    This is one of the most robust calendar effects in Indian markets.
    """
    close = df["Close"]
    daily_ret = close.pct_change().dropna()

    # Determine week of month (1-5)
    temp = pd.DataFrame({
        "ret": daily_ret,
        "day": daily_ret.index.day,
    })

    # Simple week-of-month: days 1-7 = week1, 8-14 = week2, etc.
    # But F&O expiry is typically 3rd Thursday, so we use actual week number
    temp["week"] = ((temp["day"] - 1) // 7) + 1  # 1-based week of month

    results = {}
    for week in range(1, 6):
        subset = temp[temp["week"] == week]["ret"]
        if len(subset) < 5:
            continue

        avg_ret = float(subset.mean()) * 100
        pct_pos = float((subset > 0).sum() / len(subset) * 100)

        results[f"Week {week}"] = {
            "n": int(len(subset)),
            "avg_return_pct": round(avg_ret, 4),
            "pct_positive": round(pct_pos, 1),
        }

    ranked = sorted(results.items(), key=lambda x: x[1]["avg_return_pct"], reverse=True)

    return {
        "by_week": results,
        "best_week": ranked[0][0] if ranked else None,
        "worst_week": ranked[-1][0] if ranked else None,
    }


def compute_month_seasonality(df: pd.DataFrame) -> dict[str, Any]:
    """Average NIFTY return by calendar month.

    Nadh insight: some months have structural biases:
        - January: new year allocations (bullish)
        - March: fiscal year end (tax selling, volatile)
        - May: sell in May (mildly bearish)
        - July-August: monsoon uncertainty (choppy)
        - October-November: Diwali rally (bullish)
        - December: window dressing (bullish)
    """
    close = df["Close"]
    monthly_ret = close.resample("ME").last().pct_change().dropna()

    month_names = {
        1: "January", 2: "February", 3: "March", 4: "April",
        5: "May", 6: "June", 7: "July", 8: "August",
        9: "September", 10: "October", 11: "November", 12: "December",
    }

    results = {}
    for month_num, month_name in month_names.items():
        subset = monthly_ret[monthly_ret.index.month == month_num]
        if len(subset) < 2:
            continue

        avg_ret = float(subset.mean()) * 100
        pct_pos = float((subset > 0).sum() / len(subset) * 100)

        results[month_name] = {
            "n": int(len(subset)),
            "avg_return_pct": round(avg_ret, 2),
            "pct_positive": round(pct_pos, 1),
        }

    ranked = sorted(results.items(), key=lambda x: x[1]["avg_return_pct"], reverse=True)

    return {
        "by_month": results,
        "best_month": ranked[0][0] if ranked else None,
        "worst_month": ranked[-1][0] if ranked else None,
    }


def compute_expiry_week_effect(df: pd.DataFrame) -> dict[str, Any]:
    """Does NIFTY underperform during F&O expiry weeks?

    The monthly F&O expiry (last Thursday of month) creates hedging-related
    selling pressure in the 2-3 days before expiry. Weekly expiry (every
    Thursday) has a smaller effect.

    Nadh insight: expiry weeks are when market makers unwind hedges. The
    mechanical selling from delta hedging creates a known headwind. Knowing
    this lets you size down during expiry and up in non-expiry weeks.
    """
    close = df["Close"]
    daily_ret = close.pct_change().dropna()

    # Identify expiry weeks: every Thursday is expiry day,
    # the week containing the last Thursday of the month is the big expiry week
    temp = pd.DataFrame({"ret": daily_ret})

    is_expiry_day = temp.index.dayofweek == 3  # Thursday

    # Monthly expiry: last Thursday of each month
    monthly_expiry_dates = set()
    for ym in temp.index.to_period("M").unique():
        month_data = temp[temp.index.to_period("M") == ym]
        thursdays = month_data[month_data.index.dayofweek == 3]
        if len(thursdays) > 0:
            monthly_expiry_dates.add(thursdays.index[-1])

    # Mark expiry week (3 days before and including Thursday)
    is_monthly_expiry_week = pd.Series(False, index=temp.index)
    for exp_date in monthly_expiry_dates:
        # Mark the 4 days ending on expiry Thursday
        for offset in range(4):
            d = exp_date - pd.Timedelta(days=offset)
            if d in is_monthly_expiry_week.index:
                is_monthly_expiry_week.loc[d] = True

    # Also mark all Thursdays as weekly expiry days
    is_weekly_expiry = is_expiry_day

    # Compare returns
    expiry_returns = temp.loc[is_monthly_expiry_week, "ret"]
    non_expiry_returns = temp.loc[~is_monthly_expiry_week, "ret"]

    weekly_expiry_returns = temp.loc[is_weekly_expiry, "ret"]
    non_weekly_returns = temp.loc[~is_weekly_expiry, "ret"]

    def _summarize(series, label):
        if len(series) < 5:
            return {"n": len(series), "label": label, "note": "insufficient data"}
        return {
            "n": int(len(series)),
            "label": label,
            "avg_return_pct": round(float(series.mean()) * 100, 4),
            "pct_positive": round(float((series > 0).sum() / len(series) * 100), 1),
            "avg_abs_return_pct": round(float(series.abs().mean()) * 100, 4),
        }

    monthly_expiry_stats = _summarize(expiry_returns, "Monthly Expiry Week")
    non_monthly_expiry_stats = _summarize(non_expiry_returns, "Non-Expiry Week")

    # Compute the expiry drag
    drag = None
    if (monthly_expiry_stats.get("avg_return_pct") is not None and
            non_monthly_expiry_stats.get("avg_return_pct") is not None):
        drag = round(
            monthly_expiry_stats["avg_return_pct"] - non_monthly_expiry_stats["avg_return_pct"],
            4,
        )

    return {
        "monthly_expiry": monthly_expiry_stats,
        "non_expiry": non_monthly_expiry_stats,
        "expiry_drag_pct": drag,
        "weekly_expiry": _summarize(weekly_expiry_returns, "Weekly Expiry Day"),
        "non_weekly": _summarize(non_weekly_returns, "Non-Expiry Day"),
    }


def compute_day_score(dow_stats: dict, wom_stats: dict, month_stats: dict,
                      expiry_stats: dict) -> dict[str, Any]:
    """Composite day probability score (0-100).

    Nadh insight: don't use calendar effects as hard gates — use them as
    PROBABILITY ADJUSTMENTS. A Wednesday in October during a non-expiry week
    gets the benefit of all three positive biases. A Monday in March during
    expiry week gets penalized by all three.

    Score components (each 0-25, total 0-100):
        1. Day of week bias (25 pts)
        2. Week of month bias (25 pts)
        3. Month seasonality (25 pts)
        4. Expiry week penalty (25 pts)
    """
    today = datetime.now().date()
    today_dow = today.strftime("%A")
    today_week = ((today.day - 1) // 7) + 1
    today_month = today.strftime("%B")

    score = 0
    components = {}
    effects = []

    # 1. Day of week (0-25)
    dow_data = dow_stats.get("by_day", {}).get(today_dow, {})
    dow_avg = dow_data.get("avg_return_pct")
    if dow_avg is not None:
        best_dow = dow_stats.get("best_day")
        worst_dow = dow_stats.get("worst_day")
        best_avg = dow_stats.get("by_day", {}).get(best_dow, {}).get("avg_return_pct", 0)
        worst_avg = dow_stats.get("by_day", {}).get(worst_dow, {}).get("avg_return_pct", 0)

        if best_avg != worst_avg:
            # Normalize to 0-25 based on where today falls in the range
            normalized = (dow_avg - worst_avg) / (best_avg - worst_avg) * 25
        else:
            normalized = 12.5

        s = max(0, min(25, round(normalized)))
        score += s
        pct_pos = dow_data.get("pct_positive", 50)
        sig = "★" if dow_data.get("significant") else ""
        effects.append(f"{today_dow}: avg {dow_avg:+.3f}% · {pct_pos:.0f}% positive ({dow_data.get('n', 0)} samples){sig}")
    else:
        # Non-trading day (weekend or holiday) - 0 points, not neutral
        s = 0
        score += s
        effects.append(f"{today_dow} is not a trading day - no historical data")

    components["day_of_week"] = {"points": s, "detail": effects[-1] if effects else f"{today_dow} — no data"}

    # 2. Week of month (0-25)
    wom_data = wom_stats.get("by_week", {}).get(f"Week {today_week}", {})
    wom_avg = wom_data.get("avg_return_pct")
    if wom_avg is not None:
        best_wom = wom_stats.get("best_week")
        worst_wom = wom_stats.get("worst_week")
        best_avg = wom_stats.get("by_week", {}).get(best_wom, {}).get("avg_return_pct", 0)
        worst_avg = wom_stats.get("by_week", {}).get(worst_wom, {}).get("avg_return_pct", 0)

        if best_avg != worst_avg:
            normalized = (wom_avg - worst_avg) / (best_avg - worst_avg) * 25
        else:
            normalized = 12.5

        s = max(0, min(25, round(normalized)))
        score += s
        effects.append(f"Week {today_week}: avg {wom_avg:+.3f}% · {wom_data.get('pct_positive', 50):.0f}% positive ({wom_data.get('n', 0)} samples)")
    else:
        s = 12
        score += s

    components["week_of_month"] = {"points": s, "detail": effects[-1] if effects else f"Week {today_week} — no data"}

    # 3. Month seasonality (0-25)
    month_data = month_stats.get("by_month", {}).get(today_month, {})
    month_avg = month_data.get("avg_return_pct")
    if month_avg is not None:
        best_month = month_stats.get("best_month")
        worst_month = month_stats.get("worst_month")
        best_avg = month_stats.get("by_month", {}).get(best_month, {}).get("avg_return_pct", 0)
        worst_avg = month_stats.get("by_month", {}).get(worst_month, {}).get("avg_return_pct", 0)

        if best_avg != worst_avg:
            normalized = (month_avg - worst_avg) / (best_avg - worst_avg) * 25
        else:
            normalized = 12.5

        s = max(0, min(25, round(normalized)))
        score += s
        effects.append(f"{today_month}: avg {month_avg:+.2f}% · {month_data.get('pct_positive', 50):.0f}% positive ({month_data.get('n', 0)} samples)")
    else:
        s = 12
        score += s

    components["month_seasonality"] = {"points": s, "detail": effects[-1] if effects else f"{today_month} — no data"}

    # 4. Expiry week (0-25, penalty for expiry weeks)
    drag = expiry_stats.get("expiry_drag_pct")
    if drag is not None:
        # Negative drag = expiry underperforms = penalty
        if drag < -0.15:
            s = 5  # heavy penalty
            effects.append(f"Monthly expiry week drag: {drag:+.3f}% (significant underperformance)")
        elif drag < -0.05:
            s = 12  # moderate penalty
            effects.append(f"Monthly expiry week drag: {drag:+.3f}% (mild underperformance)")
        elif drag > 0.05:
            s = 22  # rare but possible positive bias
            effects.append(f"Expiry week bias: {drag:+.3f}% (slight outperformance)")
        else:
            s = 15  # neutral
            effects.append(f"Expiry week drag: {drag:+.3f}% (negligible)")
    else:
        s = 12
        effects.append("Expiry effect — no data")

    score += s
    components["expiry_week"] = {"points": s, "detail": effects[-1]}

    # Check if today is actually in an expiry week (Thursday or day before)
    is_thursday = today.weekday() == 3
    is_wednesday = today.weekday() == 2
    is_expiry_day_or_adjacent = is_thursday or is_wednesday
    if is_expiry_day_or_adjacent:
        # Additional penalty for being ON or near expiry day
        score = max(0, score - 3)
        effects.append("⚠ Today is near expiry day (additional -3 penalty)")

    score = max(0, min(100, score))

    # Verdict
    if score >= 70:
        verdict = "STATISTICAL_ADVANTAGE"
        recommendation = (
            "History says days like today have a strong positive bias. "
            "Calendar effects favor directional trades. Full conviction sizing."
        )
    elif score >= 55:
        verdict = "STATISTICAL_NEUTRAL"
        recommendation = (
            "Calendar effects are neutral today. No structural bias to exploit. "
            "Trade on other signals."
        )
    elif score >= 40:
        verdict = "STATISTICAL_DISADVANTAGE"
        recommendation = (
            "History says days like today slightly underperform. "
            "Reduce sizing by 25%. Tighter stops."
        )
    else:
        verdict = "STATISTICAL_AVOID"
        recommendation = (
            "Calendar effects strongly favor NOT trading today. "
            "Consider sitting out or minimal sizing."
        )

    return {
        "score": score,
        "verdict": verdict,
        "recommendation": recommendation,
        "components": components,
        "effects": effects,
        "date": today.isoformat(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Day Statistics — which days are statistically better for trading?"
    )
    args = parser.parse_args()

    df = load_nifty_bars()
    if df is None:
        emit_json({
            "error": "NIFTY data not available (run screen_regime.py first to cache NIFTY50.csv)",
            "generated_on": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        })
        return 1

    print(f"  Analyzing {len(df)} days of NIFTY data...", file=sys.stderr)

    dow_stats = compute_day_of_week_stats(df)
    wom_stats = compute_week_of_month_stats(df)
    month_stats = compute_month_seasonality(df)
    expiry_stats = compute_expiry_week_effect(df)

    day_score = compute_day_score(dow_stats, wom_stats, month_stats, expiry_stats)

    payload = {
        "generated_on": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "data_source": "data_cache/NIFTY50.csv (cached daily bars)",
        "n_bars": len(df),
        "date_range": f"{df.index[0].strftime('%Y-%m-%d')} to {df.index[-1].strftime('%Y-%m-%d')}",
        "day_of_week": dow_stats,
        "week_of_month": wom_stats,
        "month_seasonality": month_stats,
        "expiry_week": expiry_stats,
        "day_score": day_score,
        "note": (
            "This is a statistical base-rate, not a prediction. It says 'history shows "
            "days like today have X bias' — not 'today WILL be good/bad'. Use it as one "
            "input among many, not as a standalone signal. Calendar effects are real but "
            "small (~0.05-0.15% per day); they matter because they compound over hundreds "
            "of trades."
        ),
    }
    emit_json(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
