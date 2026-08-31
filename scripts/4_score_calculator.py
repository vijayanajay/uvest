"""Script 4: Consolidated Decision Engine.

Combines the outputs of Scripts 1, 2 and 3 into a final 0-100 Confidence
Score and a Go / No-Go decision.

Scoring breakdown (100 pts):
    1. Math R:R check ................ 25 pts
    2. Price discipline / range ...... 20 pts
    3. Technical & volatility ........ 20 pts
    4. Historical edge ............... 20 pts
    5. Event safety .................. 15 pts (real check: days to expiry)

EMPIRICAL MODE (default when the similar-setup backtest from script6 is
available and its sample is large enough): the historical-edge check is
driven by the measured probability of success for this setup *shape* — the
backtest's win rate, read from the trend-matched bucket when possible —
gated on its Wilson 95% CI LOWER BOUND clearing the setup's net-of-costs
break-even win rate (p* = 1/(1+net_rr)). Because the backtest already
conditions on the 50-EMA trend context and on time-to-expiry (its holding
window derives from days-to-expiry), those two checks are WAIVED in
empirical mode to avoid double counting; the sub-3-day expiry policy cap
still applies. This replaces the rubric opinion with a measured probability:
GO only when the data rejects the "coin flip" null hypothesis.

FALLBACK MODE (backtest unavailable or sample too small): the original
rubric — historical edge from the ticker track record (script3), trend and
event safety scored as before.

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
from common import (  # noqa: E402
    COST_RATE_FUT,
    COST_RATE_OPT,
    OUT1_FILE,
    OUT2_FILE,
    OUT3_FILE,
    OUT6_FILE,
    PROJECT_ROOT,
    emit_json,
    fail,
    flip_prices,
    indian_currency,
    load_json,
)

# Smallest backtest sample (script6) that may drive the historical-edge check.
# Below this the empirical win rate is too noisy to score against, and the
# score falls back to the ticker track record (script3).
EMPIRICAL_MIN_N = 30

# Thresholds file written by 7_walkforward_validator.py after calibration.
_THRESHOLDS_FILE = PROJECT_ROOT / "thresholds.json"

# Default decision bands — used when thresholds.json is absent or stale.
_DEFAULT_GO_THRESHOLD = 80.0
_DEFAULT_COND_THRESHOLD = 65.0


def _load_thresholds() -> tuple[float, float]:
    """Load Bayesian-calibrated decision thresholds.

    Returns (go_threshold, cond_threshold) as effective-score percentages.
    Falls back to the fixed defaults if the file is missing, corrupt,
    or has a ``_generated_on`` older than 90 days (stale calibration).
    """
    try:
        t = load_json(_THRESHOLDS_FILE)
    except (FileNotFoundError, ValueError, OSError):
        return _DEFAULT_GO_THRESHOLD, _DEFAULT_COND_THRESHOLD
    # Guard against stale thresholds — re-calibrate periodically.
    gen = t.get("_generated_on")
    if gen:
        try:
            from datetime import date as _date
            gen_date = _date.fromisoformat(str(gen)[:10])
            age_days = (_date.today() - gen_date).days
            if age_days > 90:
                return _DEFAULT_GO_THRESHOLD, _DEFAULT_COND_THRESHOLD
        except (ValueError, TypeError):
            pass
    go = float(t.get("go_threshold", _DEFAULT_GO_THRESHOLD))
    cond = float(t.get("cond_threshold", _DEFAULT_COND_THRESHOLD))
    # Sanity clamp — never let thresholds go below 50 or above 95.
    go = max(50.0, min(95.0, go))
    cond = max(40.0, min(go - 1, cond))  # cond must be < go
    return go, cond


def decide(score: float, max_possible: float, days: int | None,
            go_threshold: float | None = None,
            cond_threshold: float | None = None) -> tuple[str, float]:
    """Map a raw score to a decision using the share-of-available-points bands,
    with the expiry-day veto (sub-3-day expiry never allows full size).

    When go_threshold / cond_threshold are None the Bayesian-calibrated
    values from thresholds.json are used (with fallback to 80/65).
    """
    if go_threshold is None or cond_threshold is None:
        go_threshold, cond_threshold = _load_thresholds()
    effective = round(score / max_possible * 100, 1) if max_possible > 0 else 0.0
    if effective >= go_threshold:
        decision = "GO (EXECUTE FULL SIZE)"
    elif effective >= cond_threshold:
        decision = "CONDITIONAL GO (REDUCE SIZE 50%)"
    else:
        decision = "NO-GO (REJECT)"
    if decision == "GO (EXECUTE FULL SIZE)" and days is not None and days < 3:
        decision = "CONDITIONAL GO (REDUCE SIZE 50%)"
    return decision, effective


def net_rr_after_costs(math_res: dict[str, Any], instrument_type: str | None) -> float | None:
    """Live reward:risk net of modeled transaction costs (per share)."""
    live = float(math_res["live_price"])
    sl = float(math_res["stoploss"])
    target = float(math_res["target"])
    risk = abs(live - sl)
    reward = abs(target - live)
    if risk <= 0 or reward <= 0:
        return None
    rate = COST_RATE_OPT if str(instrument_type or "").upper() == "OPT" else COST_RATE_FUT
    cost_per_share = live * rate
    net = (reward - cost_per_share) / (risk + cost_per_share)
    return round(net, 3) if net > 0 else None


def break_even_win_rate(math_res: dict[str, Any], instrument_type: str | None) -> float | None:
    """p* = 1 / (1 + net_rr): the win rate a setup needs (net of costs) for its
    expectancy to be non-negative. Gating the empirical win rate's CI lower
    bound against p* is what separates a measured edge from a coin flip."""
    net = net_rr_after_costs(math_res, instrument_type)
    return 1.0 / (1.0 + net) if net else None


def empirical_context(math_res: dict[str, Any], audit_res: dict[str, Any],
                      backtest_res: dict[str, Any] | None) -> tuple[bool, dict[str, Any] | None]:
    """Decide whether the similar-setup backtest (script6) can drive the
    historical-edge check and package the numbers the score and report need.

    Empirical mode requires a usable backtest payload with a large enough
    overall sample AND a computable net-of-costs break-even win rate from the
    live geometry. The base rate is read from the backtest's trend-matched
    bucket (the reco's own 50-EMA context, which the backtest conditions on),
    falling back to the overall sample when that bucket is too small.
    Returns (empirical_mode, context)."""
    p_star = break_even_win_rate(math_res, audit_res.get("instrument_type"))
    bt = backtest_res or {}
    if "error" in bt or p_star is None:
        return False, None
    s = bt.get("sample") or {}
    ov = s.get("overall") or {}
    if ov.get("n", 0) < EMPIRICAL_MIN_N:
        return False, None
    trend_state = bt.get("trend_state")
    bucket = None
    if trend_state:
        b = (s.get("by_trend") or {}).get(trend_state) or {}
        if b.get("n", 0) >= EMPIRICAL_MIN_N:
            bucket = b
    base = bucket or ov
    return True, {
        "n": base["n"],
        "bucket": (f"by_trend:{trend_state}" if base is not ov else "overall"),
        "trend_state": trend_state,
        "win_rate": base["win_rate"],
        "ci_low": base["ci_low"],
        "ci_high": base["ci_high"],
        "break_even_win_rate": round(p_star * 100, 1),
    }


def calculate_score(math_res: dict[str, Any], market_res: dict[str, Any], audit_res: dict[str, Any],
                    backtest_res: dict[str, Any] | None = None) -> dict[str, Any]:
    score = 0
    component_scores: dict[str, Any] = {}
    details: list[dict[str, Any]] = []
    empirical_mode, empirical = empirical_context(math_res, audit_res, backtest_res)

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

        if empirical_mode:
            # The backtest's trend-matched bucket already conditions on the
            # 50-EMA context; scoring the raw EMA position again would count
            # the same evidence twice.
            record(
                "TREND (EMBEDDED IN BACKTEST)", "trend", 0, 10,
                "Trend context is already embedded in the backtest's trend-matched success rate - "
                "not scored separately (no double counting).",
                waived=True,
            )
        elif market_res.get("above_50_ema", False):
            score += 10
            record("TREND PASS", "trend", 10, 10)
        else:
            record("TREND CONFLICT", "trend", 0, 10, "Stock is trading below its 50 EMA.")

    # 4. Historical Edge Check (Max 20 pts)
    #    Empirical mode (script6 available): the measured probability of success
    #    for this setup SHAPE, gated on its Wilson 95% CI lower bound clearing
    #    the net-of-costs break-even win rate. Fallback: ticker track record.
    if empirical_mode and empirical:
        e = empirical
        if e["ci_low"] / 100.0 > e["break_even_win_rate"] / 100.0:
            score += 20
            record(
                "EMPIRICAL EDGE CONFIRMED", "historical_edge", 20, 20,
                f"Similar-setup history wins {e['win_rate']}% of the time "
                f"(95% CI {e['ci_low']}–{e['ci_high']}%, n={e['n']}, bucket {e['bucket']}) - "
                f"statistically above the break-even win rate of {e['break_even_win_rate']}% "
                "(net of costs). The edge is confirmed.",
            )
        elif e["win_rate"] / 100.0 > e["break_even_win_rate"] / 100.0:
            score += 10
            record(
                "EMPIRICAL EDGE (UNCONFIRMED)", "historical_edge", 10, 20,
                f"Similar-setup win rate {e['win_rate']}% "
                f"(95% CI {e['ci_low']}–{e['ci_high']}%, n={e['n']}) vs break-even "
                f"{e['break_even_win_rate']}% - positive but NOT statistically confirmed; "
                "treat it as a coin-flip-plus, not a real edge.",
            )
        else:
            record(
                "NO EMPIRICAL EDGE", "historical_edge", 0, 20,
                f"Similar-setup win rate {e['win_rate']}% "
                f"(95% CI {e['ci_low']}–{e['ci_high']}%, n={e['n']}) is not above the break-even "
                f"{e['break_even_win_rate']}% - no confirmed edge even before costs.",
            )
    elif audit_res.get("edge_is_positive", False):
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
    if empirical_mode:
        # The backtest's holding window derives from days-to-expiry, so the
        # measured success rate already reflects the time available. Waive the
        # points, but keep the hard policy cap below (< 3 days caps the
        # decision at CONDITIONAL GO) - that rule is risk management, not
        # probability evidence.
        cap_note = (
            " The expiry-day policy cap (< 3 days to expiry) still applies."
            if days is not None and days < 3 else ""
        )
        record(
            "EVENT SAFETY (EMBEDDED IN BACKTEST)", "event_safety", 0, 15,
            "Time-to-expiry is already reflected in the backtest's expiry-derived holding window - "
            "not scored separately (no double counting)." + cap_note,
            waived=True,
        )
    elif days is None:
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
    # grant nothing and are not penalized either. Thresholds are loaded from
    # thresholds.json (written by 7_walkforward_validator.py) with fallback
    # to the fixed 80/65 defaults.
    waived_max = sum(d["max"] for d in details if d.get("waived"))
    max_possible = 100 - waived_max
    go_th, cond_th = _load_thresholds()
    decision, effective = decide(score, max_possible, days, go_th, cond_th)

    return {
        "final_confidence_score": score,
        "max_possible_score": max_possible,
        "effective_score": effective,
        "empirical_mode": empirical_mode,
        "empirical": empirical,
        "break_even_win_rate": empirical["break_even_win_rate"] if empirical else None,
        "waived": [d["category"] for d in details if d.get("waived")],
        "decision": decision,
        "rejection_reasons": [d["reason"] for d in details if not d.get("waived")],
        "rejection_details": details,
        "component_scores": component_scores,
        "thresholds_active": {
            "go_threshold": go_th,
            "cond_threshold": cond_th,
            "source": "thresholds.json" if _THRESHOLDS_FILE.exists() else "defaults",
        },
    }


def _rr_points(price: float, action: str, sl: float, target: float) -> int:
    """R:R component points (25/10/0) if the entry were at `price`."""
    risk = (sl - price) if action == "SELL" else (price - sl)
    reward = (price - target) if action == "SELL" else (target - price)
    if risk <= 0 or reward <= 0:
        return 0
    rr = reward / risk
    return 25 if rr >= 1.5 else (10 if rr >= 1.0 else 0)


def build_flip_table(math_res: dict[str, Any], market_res: dict[str, Any],
                     score_res: dict[str, Any]) -> dict[str, Any]:
    """What would change the decision: per-check flip thresholds, conflicts
    between checks, the best achievable outcome by entry timing alone, and
    the activation price for a standing limit order."""
    action = str(math_res.get("action") or "BUY").upper()
    entry = float(math_res["entry_price"])
    sl = float(math_res["stoploss"])
    target = float(math_res["target"])
    live = float(math_res["live_price"])
    rmin = math_res.get("entry_range_min")
    rmax = math_res.get("entry_range_max")
    days = math_res.get("days_to_expiry")
    score = int(score_res["final_confidence_score"])
    max_possible = int(score_res.get("max_possible_score", 100))
    comp = score_res.get("component_scores", {})
    market_available = "error" not in market_res

    fp = flip_prices(sl, target, rmin, rmax)
    p15, p10 = fp["rr_1_5_price"], fp["rr_1_0_price"]

    th = score_res.get("thresholds_active", {})
    go_th = th.get("go_threshold")
    cond_th = th.get("cond_threshold")

    def after(gain: int) -> tuple[int, str]:
        s = score + gain
        d, _ = decide(s, max_possible, days, go_th, cond_th)
        return s, d

    rows: list[dict[str, Any]] = []

    # 1. Risk:Reward — enter at a price where the live R:R clears the band.
    rr_earned = comp.get("rr_check", {}).get("earned", 0)
    if rr_earned < 25:
        direction = "below" if action == "BUY" else "above"
        if rr_earned < 10:
            s, d = after(10 - rr_earned)
            rows.append({
                "check": "rr_1_0", "label": "Risk:Reward \u2265 1.0", "earned": rr_earned, "max": 25,
                "gain": 10 - rr_earned, "kind": "price", "price": p10, "direction": direction,
                "achievable": True, "adjusts_trade": False, "score_after": s, "decision_after": d,
                "detail": f"Enter {'at or below' if action == 'BUY' else 'at or above'} the price where live R:R reaches 1.0",
            })
        s, d = after(25 - rr_earned)
        rows.append({
            "check": "rr_1_5", "label": "Risk:Reward \u2265 1.5", "earned": rr_earned, "max": 25,
            "gain": 25 - rr_earned, "kind": "price", "price": p15, "direction": direction,
            "achievable": True, "adjusts_trade": False, "score_after": s, "decision_after": d,
            "detail": f"Enter {'at or below' if action == 'BUY' else 'at or above'} the price where live R:R reaches 1.5",
        })

    # 2. Price discipline — wait for the price to re-enter the recommended range.
    pd_earned = comp.get("price_discipline", {}).get("earned", 0)
    if pd_earned < 20 and rmin is not None and rmax is not None:
        if live > rmax:
            range_detail = f"wait for the price to fall within {rmin}–{rmax}"
        elif live < rmin:
            range_detail = f"wait for the price to rise within {rmin}–{rmax}"
        else:
            range_detail = f"price inside {rmin}–{rmax}"
        s, d = after(20 - pd_earned)
        rows.append({
            "check": "price_discipline", "label": "Price discipline (entry range)", "earned": pd_earned,
            "max": 20, "gain": 20 - pd_earned, "kind": "range", "range_min": rmin, "range_max": rmax,
            "achievable": True, "adjusts_trade": False, "score_after": s, "decision_after": d,
            "detail": range_detail,
        })

    # 3. Volatility — the stop is tighter than 1x ATR; widen it (adjusts the trade).
    vol_earned = comp.get("volatility", {}).get("earned", 0)
    if vol_earned < 10 and market_available and market_res.get("current_atr"):
        atr = float(market_res["current_atr"])
        new_sl = (entry - atr) if action == "BUY" else (entry + atr)
        s, d = after(10 - vol_earned)
        rows.append({
            "check": "volatility", "label": "Volatility (SL vs ATR)", "earned": vol_earned, "max": 10,
            "gain": 10 - vol_earned, "kind": "structural", "achievable": True, "adjusts_trade": True,
            "score_after": s, "decision_after": d,
            "detail": f"Widen the stop so the SL distance is \u2265 1\u00d7ATR (\u20b9{atr:.2f}) \u2192 SL \u2248 \u20b9{new_sl:.2f} for this entry; widens risk and lowers R:R",
        })

    # 4. Trend — the 50-EMA state is a market condition, not an entry choice.
    #    Waived in empirical mode (embedded in the backtest), so no flip row.
    trend_earned = comp.get("trend", {}).get("earned", 0)
    if trend_earned < 10 and not comp.get("trend", {}).get("waived", False) \
            and market_available and market_res.get("ema_50"):
        ema = float(market_res["ema_50"])
        s, d = after(10 - trend_earned)
        rows.append({
            "check": "trend", "label": "Trend (50 EMA)", "earned": trend_earned, "max": 10,
            "gain": 10 - trend_earned, "kind": "market", "price": round(ema, 2), "direction": "above",
            "achievable": False, "adjusts_trade": False, "score_after": s, "decision_after": d,
            "detail": f"Price must close above the 50 EMA (\u20b9{ema:.2f}) - a market condition, not an entry-price choice",
        })

    # 5. Historical edge — not fixable by entry price or timing.
    hist_earned = comp.get("historical_edge", {}).get("earned", 0)
    if hist_earned < 20 and not comp.get("historical_edge", {}).get("waived", False):
        s, d = after(20 - hist_earned)
        empirical = score_res.get("empirical")
        if score_res.get("empirical_mode") and empirical:
            detail = (
                f"Empirical win rate {empirical['win_rate']}% "
                f"(95% CI {empirical['ci_low']}–{empirical['ci_high']}%, n={empirical['n']}) vs break-even "
                f"{empirical['break_even_win_rate']}% - the backtested probability is what it is; "
                "no entry or timing choice changes it."
            )
        else:
            no_data = any("HISTORICAL" in w for w in score_res.get("waived", []))
            detail = (
                "No track record for this ticker yet - the edge check is waived and activates as outcomes accumulate."
                if no_data else
                "No entry or timing choice fixes this - tracked recos for this setup have negative expectancy; skip it."
            )
        rows.append({
            "check": "historical_edge", "label": "Historical edge", "earned": hist_earned, "max": 20,
            "gain": 20 - hist_earned, "kind": "none", "achievable": False, "adjusts_trade": False,
            "score_after": s, "decision_after": d, "detail": detail,
        })

    # 6. Event safety — time decays it; it can only get worse by waiting.
    #    Waived in empirical mode (embedded in the backtest), so no flip row.
    ev_earned = comp.get("event_safety", {}).get("earned", 0)
    if ev_earned < 15 and not comp.get("event_safety", {}).get("waived", False):
        s, d = after(15 - ev_earned)
        detail = (
            "Time-decays: the closer to expiry, the lower this score; it cannot improve by waiting. "
            "A sub-3-day expiry caps the decision at CONDITIONAL GO." if days is not None else
            "No expiry supplied - check waived."
        )
        rows.append({
            "check": "event_safety", "label": "Event safety (days to expiry)", "earned": ev_earned, "max": 15,
            "gain": 15 - ev_earned, "kind": "none", "achievable": False, "adjusts_trade": False,
            "score_after": s, "decision_after": d, "detail": detail,
        })

    # ---- Conflicts between the price-based checks --------------------------
    conflicts: list[str] = []
    best_case: dict[str, Any] | None = None
    if rmin is not None and rmax is not None:
        best_price = rmin if action == "BUY" else rmax
        if action == "BUY":
            if p15 < rmin:
                conflicts.append(
                    f"R:R \u2265 1.5 requires entering at or below \u20b9{p15:.2f}, but price discipline requires "
                    f"entering within \u20b9{rmin:.2f}–\u20b9{rmax:.2f}. The two price-based checks can never "
                    "both pass - the recommended target/stoploss geometry caps the score."
                )
        elif p15 > rmax:
            conflicts.append(
                f"R:R \u2265 1.5 requires entering at or above \u20b9{p15:.2f}, but price discipline requires "
                f"entering within \u20b9{rmin:.2f}–\u20b9{rmax:.2f}. The two price-based checks can never "
                "both pass - the recommended target/stoploss geometry caps the score."
            )
        if rr_earned < 25:
            best_rr = _rr_points(best_price, action, sl, target)
            if best_rr < 25:
                conflicts.append(
                    "Even at the best in-range entry, the R:R component cannot earn full points "
                    "(best in-range R:R " + ("reaches 1.0–1.5" if best_rr >= 10 else "stays below 1.0") + ")."
                )

        # Best achievable by entry timing alone: sweep the candidate prices
        # that shape the decision landscape, pick the best decision, then the
        # best fill (highest for BUY, lowest for SELL) that still reaches it.
        rank = {"GO (EXECUTE FULL SIZE)": 0, "CONDITIONAL GO (REDUCE SIZE 50%)": 1, "NO-GO (REJECT)": 2}
        candidates = [live]
        for v in (rmin, rmax, p10, p15):
            if v is not None and v > 0:
                candidates.append(v)
        evals = []
        for p in candidates:
            if (action == "BUY" and p <= sl) or (action == "SELL" and p >= sl):
                continue  # an entry at/through the stop is meaningless
            rr_pts = _rr_points(p, action, sl, target)
            pd_pts = 20 if (rmin <= p <= rmax) else 0
            s = score - rr_earned - pd_earned + rr_pts + pd_pts
            d, _ = decide(s, max_possible, days)
            evals.append({"price": p, "score": s, "decision": d})
        if evals:
            best_rank_v = min(rank[e["decision"]] for e in evals)
            best_evals = [e for e in evals if rank[e["decision"]] == best_rank_v]
            if action == "BUY":
                best = max(best_evals, key=lambda e: e["price"])
                fill = best["price"]
                zone_lo = rmin if any(e["price"] == rmin for e in best_evals) else fill
                zone_hi = fill
            else:
                best = min(best_evals, key=lambda e: e["price"])
                fill = best["price"]
                zone_hi = rmax if any(e["price"] == rmax for e in best_evals) else fill
                zone_lo = fill
            zone = f"\u20b9{zone_lo:.2f}" if zone_hi <= zone_lo else f"\u20b9{zone_lo:.2f}–\u20b9{zone_hi:.2f}"
            best_dec = best["decision"]
            best_score = best["score"]
            if best_dec != "NO-GO (REJECT)":
                best_case = {
                    "price": round(fill, 2),
                    "zone": zone,
                    "score": best_score,
                    "decision": best_dec,
                    "note": (
                        f"A limit order anywhere in {zone} reaches {best_dec} ({best_score}/{max_possible}); "
                        f"\u20b9{fill:.2f} is the best fill. "
                        + ("Full GO needs the R:R \u2265 1.5 entry price, which lies outside the recommended "
                           "range - the target/stoploss geometry itself must change."
                           if best_dec == "CONDITIONAL GO (REDUCE SIZE 50%)" and rr_earned < 10 else "")
                    ),
                }

    activation: dict[str, Any] | None = None
    if best_case is not None:
        order = "LIMIT BUY" if action == "BUY" else "LIMIT SELL"
        activation = {
            "price": best_case["price"],
            "zone": best_case["zone"],
            "score": best_case["score"],
            "decision": best_case["decision"],
            "note": (
                f"Place a {order} in {best_case['zone']} \u2192 {best_case['decision']} "
                f"({best_case['score']}/{max_possible}); best fill {indian_currency(best_case['price'])}."
            ),
        }

    return {
        "rows": rows,
        "conflicts": conflicts,
        "best_case": best_case,
        "activation": activation,
        "note": (
            "Each row is a per-check hypothetical - only that check flips, everything else unchanged. "
            "The best-case row combines the price-dependent checks." if rows else
            "No failing checks - nothing to flip."
        ),
    }


if __name__ == "__main__":
    try:
        math_res = load_json(OUT1_FILE)
        market_res = load_json(OUT2_FILE)
        audit_res = load_json(OUT3_FILE)
        try:
            backtest_res = load_json(OUT6_FILE)
        except (FileNotFoundError, ValueError):
            backtest_res = None
    except FileNotFoundError as exc:
        fail(f"Missing script output: {exc}. Run scripts 1-3 first (or scripts/run_analysis.py).")

    score_res = calculate_score(math_res, market_res, audit_res, backtest_res)
    score_res["flip_table"] = build_flip_table(math_res, market_res, score_res)
    emit_json(score_res)
