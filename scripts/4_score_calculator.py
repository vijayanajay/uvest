"""Script 4: Consolidated Decision Engine.

Combines the outputs of Scripts 1, 2 and 3 into a final 0-100 Confidence
Score and a Go / No-Go decision.

Scoring breakdown (100 pts):
    1. Math R:R check ................ 25 pts
    2. Price discipline / range ...... 20 pts
    3. Technical & volatility ........ 20 pts
    4. Historical edge ............... 20 pts
    5. Event safety .................. 15 pts (assumed clean unless flagged)

Output contract:
    final_confidence_score : int
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

    def reject(category: str, reason: str, earned: int, maximum: int) -> None:
        details.append({"category": category, "earned": earned, "max": maximum, "reason": reason})

    # 1. Math R:R Check (Max 25 pts)
    live_rr = math_res.get("live_rr", 0.0)
    if live_rr >= 1.5:
        score += 25
        component_scores["rr_check"] = {"earned": 25, "max": 25}
    elif live_rr >= 1.0:
        score += 10
        component_scores["rr_check"] = {"earned": 10, "max": 25}
        reject("SUB-OPTIMAL R:R", "WARNING: R:R is between 1.0 and 1.5. Sub-optimal reward profile.", 10, 25)
    else:
        component_scores["rr_check"] = {"earned": 0, "max": 25}
        reject(
            "INVERTED RISK-TO-REWARD",
            f"CRITICAL: Inverted R:R ({live_rr}). Risking more than potential reward.",
            0, 25,
        )

    # 2. Price Discipline / Range Check (Max 20 pts)
    if not math_res.get("outside_entry_range", False):
        score += 20
        component_scores["price_discipline"] = {"earned": 20, "max": 20}
    else:
        component_scores["price_discipline"] = {"earned": 0, "max": 20}
        reject(
            "PRICE DISCIPLINE VIOLATION",
            "ENTRY VIOLATION: Current price is outside Univest's recommended range.",
            0, 20,
        )

    # 3. Technical & Volatility Check (Max 20 pts)
    market_available = "error" not in market_res
    if market_available and not market_res.get("is_tight_sl", False):
        score += 10
        component_scores["volatility"] = {"earned": 10, "max": 10}
    elif not market_available:
        component_scores["volatility"] = {"earned": 0, "max": 10}
        component_scores["trend"] = {"earned": 0, "max": 10}
        reject(
            "MARKET DATA UNAVAILABLE",
            "Could not fetch live ATR/EMA; volatility and trend checks waived.",
            0, 20,
        )
    else:
        component_scores["volatility"] = {"earned": 0, "max": 10}
        reject(
            "VOLATILITY RISK",
            "Stop Loss is tighter than 1.0 x Daily ATR (High noise stopout risk).",
            0, 10,
        )

    if market_available and market_res.get("above_50_ema", True):
        score += 10
        component_scores["trend"] = {"earned": 10, "max": 10}
    elif market_available:
        component_scores["trend"] = {"earned": 0, "max": 10}
        reject("TREND CONFLICT", "Stock is trading below its 50 EMA.", 0, 10)

    # 4. Historical Edge Check (Max 20 pts)
    if audit_res.get("edge_is_positive", False) or audit_res.get("status") == "NO_HISTORICAL_DATA_FOR_TICKER":
        score += 20
        component_scores["historical_edge"] = {"earned": 20, "max": 20}
    else:
        component_scores["historical_edge"] = {"earned": 0, "max": 20}
        reject(
            "HISTORICAL EDGE FAIL",
            "Univest signals for this setup/ticker have a negative net expectancy.",
            0, 20,
        )

    # 5. Event Safety Check (Max 15 pts) - Assumed clean unless flagged
    score += 15
    component_scores["event_safety"] = {"earned": 15, "max": 15}

    # Decision Output
    decision = "NO-GO (REJECT)"
    if score >= 80:
        decision = "GO (EXECUTE FULL SIZE)"
    elif score >= 65:
        decision = "CONDITIONAL GO (REDUCE SIZE 50%)"

    return {
        "final_confidence_score": score,
        "decision": decision,
        "rejection_reasons": [d["reason"] for d in details],
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
