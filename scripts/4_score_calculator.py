"""Script 4: Consolidated Decision Engine.

Combines the outputs of Scripts 1, 2 and 3 into a final 0-100 Confidence
Score and a Go / No-Go decision.

Scoring breakdown (100 pts):
    1. Math R:R check ................ 25 pts
    2. Price discipline / range ...... 20 pts
    3. Technical & volatility ........ 20 pts
    4. Historical edge ............... 20 pts
    5. Event safety .................. 15 pts (real check: days to expiry)

Waiver model (no free points): a check that cannot be evaluated — market
data unavailable, no track record for the ticker, expiry not supplied —
earns 0 and is labelled `waived`. Waived checks neither add nor subtract
points; the decision bands are applied to the share of *available* points
(`effective_score`), so missing data never inflates a score and never
silently caps a good one.

Output contract:
    final_confidence_score : int     (raw points earned, out of max_possible)
    max_possible_score     : int     (100 minus waived max points)
    effective_score        : float   (% of available points earned)
    waived                 : list[str]
    decision               : str
    rejection_reasons      : list[str]      (BRD parity — plain strings)
    rejection_details      : list[dict]     (rich form for the report renderer)

Usage:
    python scripts/4_score_calculator.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OUT1_FILE, OUT2_FILE, OUT3_FILE, emit_json, fail, load_json  # noqa: E402


def calculate_score(math_res: dict[str, Any], market_res: dict[str, Any], audit_res: dict[str, Any]) -> dict[str, Any]:
    score = 0
    component_scores: dict[str, Any] = {}
    details: list[dict[str, Any]] = []

    def record(category: str, key: str, earned: int, maximum: int, reason: str | None = None, waived: bool = False) -> None:
        component_scores[key] = {"earned": earned, "max": maximum, "waived": waived}
        if reason:
            details.append({"category": category, "earned": earned, "max": maximum, "reason": reason, "waived": waived})

    # 1. Math R:R Check (Max 25 pts)
    live_rr = math_res.get("live_rr", 0.0)
    if live_rr >= 1.5:
        score += 25
        record("R:R CHECK PASS", "rr_check", 25, 25)
    elif live_rr >= 1.0:
        score += 10
        record(
            "SUB-OPTIMAL R:R", "rr_check", 10, 25,
            "WARNING: R:R is between 1.0 and 1.5. Sub-optimal reward profile.",
        )
    else:
        record(
            "INVERTED RISK-TO-REWARD", "rr_check", 0, 25,
            f"CRITICAL: Inverted R:R ({live_rr}). Risking more than potential reward.",
        )

    # 2. Price Discipline / Range Check (Max 20 pts)
    if not math_res.get("outside_entry_range", False):
        score += 20
        record("PRICE DISCIPLINE PASS", "price_discipline", 20, 20)
    else:
        record(
            "PRICE DISCIPLINE VIOLATION", "price_discipline", 0, 20,
            "ENTRY VIOLATION: Current price is outside Univest's recommended range.",
        )

    # 3. Technical & Volatility Check (Max 20 pts) — vol 10 + trend 10
    market_available = "error" not in market_res
    if not market_available:
        record("VOLATILITY", "volatility", 0, 10, waived=True)
        record("TREND", "trend", 0, 10, waived=True)
        record(
            "MARKET DATA UNAVAILABLE", "market_data", 0, 20,
            "Could not fetch live ATR/EMA; volatility and trend checks waived.",
            waived=True,
        )
    else:
        if market_res.get("is_tight_sl", False):
            record(
                "VOLATILITY RISK", "volatility", 0, 10,
                "Stop Loss is tighter than 1.0 x Daily ATR (High noise stopout risk).",
            )
        else:
            score += 10
            record("VOLATILITY PASS", "volatility", 10, 10)

        if market_res.get("above_50_ema", False):
            score += 10
            record("TREND PASS", "trend", 10, 10)
        else:
            record("TREND CONFLICT", "trend", 0, 10, "Stock is trading below its 50 EMA.")

    # 4. Historical Edge Check (Max 20 pts) — no data is waived, never a win
    if audit_res.get("edge_is_positive", False):
        score += 20
        record("HISTORICAL EDGE PASS", "historical_edge", 20, 20)
    elif audit_res.get("status") == "NO_HISTORICAL_DATA_FOR_TICKER":
        record(
            "HISTORICAL EDGE (NO DATA)", "historical_edge", 0, 20,
            "No track record for this ticker yet - no points granted for missing data (waived).",
            waived=True,
        )
    else:
        record(
            "HISTORICAL EDGE FAIL", "historical_edge", 0, 20,
            "Univest signals for this setup/ticker have a negative net expectancy.",
        )

    # 5. Event Safety Check (Max 15 pts) — real check: days to expiry
    days = math_res.get("days_to_expiry")
    if days is None:
        record(
            "EVENT SAFETY (NO EXPIRY)", "event_safety", 0, 15,
            "Expiry not specified or unparsable - event-safety check waived rather than assumed clean.",
            waived=True,
        )
    elif days >= 5:
        score += 15
        record("EVENT SAFETY PASS", "event_safety", 15, 15)
    elif days >= 3:
        score += 10
        record(
            "EXPIRY PROXIMITY", "event_safety", 10, 15,
            f"Only {days} days to expiry - the trade window is shrinking and expiry-week whipsaw risk rises.",
        )
    elif days < 0:
        record(
            "EXPIRY ALREADY PASSED", "event_safety", 0, 15,
            f"Expiry is {abs(days)} day(s) in the past - this recommendation is stale; do not enter it.",
        )
    else:
        record(
            "EXPIRY DAY RISK", "event_safety", 0, 15,
            f"Only {days} day(s) to expiry - expiry-day whipsaw risk; decision capped at CONDITIONAL GO.",
        )

    # Decision — bands applied to the share of AVAILABLE points. Waived checks
    # grant nothing and are not penalized either.
    waived_max = sum(d["max"] for d in details if d.get("waived"))
    max_possible = 100 - waived_max
    effective = round(score / max_possible * 100, 1) if max_possible > 0 else 0.0

    decision = "NO-GO (REJECT)"
    if effective >= 80:
        decision = "GO (EXECUTE FULL SIZE)"
    elif effective >= 65:
        decision = "CONDITIONAL GO (REDUCE SIZE 50%)"

    # Expiry-day veto: never full-size into a trade with <3 days to expiry.
    if decision == "GO (EXECUTE FULL SIZE)" and days is not None and days < 3:
        decision = "CONDITIONAL GO (REDUCE SIZE 50%)"

    return {
        "final_confidence_score": score,
        "max_possible_score": max_possible,
        "effective_score": effective,
        "waived": [d["category"] for d in details if d.get("waived")],
        "decision": decision,
        "rejection_reasons": [d["reason"] for d in details if not d.get("waived")],
        "rejection_details": details,
        "component_scores": component_scores,
    }


if __name__ == "__main__":
    try:
        math_res = load_json(OUT1_FILE)
        market_res = load_json(OUT2_FILE)
        audit_res = load_json(OUT3_FILE)
    except FileNotFoundError as exc:
        fail(f"Missing script output: {exc}. Run scripts 1-3 first (or scripts/run_analysis.py).")

    emit_json(calculate_score(math_res, market_res, audit_res))
