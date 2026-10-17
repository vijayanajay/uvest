#!/usr/bin/env python3
"""UVEST stage 9 — Consolidated Multi-Factor Decision Engine (TICK-009).

Sole owner of the synthesized pre-entry verdict (BRD §10, §9.1 row 9). It
aggregates the upstream artifacts (`out_1_math.json` … `out_8_dhan.json`,
whichever are supplied), applies the 100-point calibration model (§10.2), the
six hard circuit breakers (§10.3), the waiver rule (§10.4) and emits the
canonical 7-state verdict with its supporting evidence on stdout as
`out_9_decision.json`.

**Scoring sources and weights** (BRD §10.2; overridable via `thresholds.json`):

    out_1_math.json          20.0   R:R geometry & slippage
    out_3_dtw.json           12.5   DTW analog confidence  ─┐ Quant & ML
    out_4_ml.json            12.5   P(target) calibration  ─┘  edge: 25
    out_5_flow.json          20.0   Holy Trinity + delivery z
    out_6_fundamental.json   15.0   Piotroski/Altman/CFO/pledge
    out_7_regime.json        15.0   RRG + NIFTY trend + VIX band
    out_8_dhan.json           5.0   Optimal limit & spread

`out_2_market.json` is consumed for the tight-stop breaker but carries no
scored weight — the 0.75×ATR stop is a hard breaker (§10.3 #5), not a
calibration input. A source is **waived** when its artifact is absent or
carries `waived: true`: its weight is deducted from `max_possible_score`,
the remaining sources grade against the reduced maximum, and >= 1 waived
source caps the verdict at GO_WEAK (§10.4). Absence never fabricates a
rejection; it only lowers confidence.

**P(win) calibration** (§10.1): the advisory anchor is the frozen 55% floor;
the machine's conditional improvement is a linear function of the effective
score, ±0.4pp per effective point around the 50-point midpoint, clamped to
[5%, 95%]: `p_win = clamp(0.55 + (effective_score_pct − 50) × 0.004)`.

**Expectancy** (§10.1): `E = p_win × live_rr − (1 − p_win)` in R, so the
frozen worked example (P=55%, R:R 1:2) yields exactly +0.65R.

**Verdict ladder** (§10.3, §10.4, evaluated in order):
1. Any hard breaker other than inverted R:R → AVOID.
2. Inverted R:R alone → WAIT when a limit order at the optimal/recommended
   band restores R:R ≥ 1.5, else AVOID.
3. Negative expectancy (E ≤ 0) → AVOID.
4. P(win) < 55% floor with positive expectancy → GO_WEAK (confidence deficit).
5. Positive expectancy + ≥1 waived source → GO_WEAK.
6. Positive expectancy, P(win) ≥ 55%, but stage 6 reports a size-capping
   corporate-calendar hazard (§6.4: earnings inside 10 sessions on a Cash
   Equity) → GO_WEAK at half size (confidence deficit). Only a verdict that
   would otherwise be GO is capped: the hazard never rescues AVOID/WAIT.
7. Positive expectancy, P(win) ≥ 55%, full evidence → GO.

Every non-GO verdict carries `what_would_flip_me` (the Flip Condition
Invariant, §10.4); GO carries none. The composite score is a calibration
input, never a cutoff.

Reads `--input current_trade.json` plus repeatable `--payload out_N_*.json`;
writes `out_9_decision.json` to stdout. Supports `--record <dir>` for
deterministic replay bundle recording and `--thresholds <path>` for weight
overrides (BRD §12.2; stage 11 is the sole future writer of that file, this
stage only reads it).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Windows consoles default to a legacy codepage; the §16 markdown carries
# Unicode verdict glyphs, so both streams must be UTF-8 (repo convention).
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _recorder import add_record_arg, record_artifact, resolve_bundle_dir  # noqa: E402

ERR_PARSE = "ERR_PARSE"

REQUIRED_FIELDS = ["symbol", "entry_price", "target", "stoploss"]

# Frozen module weights (BRD §10.2), keyed by the artifact that feeds them.
DEFAULT_WEIGHTS: dict[str, float] = {
    "out_1_math.json": 20.0,
    "out_3_dtw.json": 12.5,
    "out_4_ml.json": 12.5,
    "out_5_flow.json": 20.0,
    "out_6_fundamental.json": 15.0,
    "out_7_regime.json": 15.0,
    "out_8_dhan.json": 5.0,
}

P_BASE = 0.55        # the frozen advisory floor (BRD §10.1 "55% Floor")
P_SLOPE = 0.004      # conditional improvement per effective score point
P_MIN, P_MAX = 0.05, 0.95
GO_WIN_FLOOR = 0.55
GO_RR_FLOOR = 1.5

# Canonical pre-entry verdict tokens (BRD §10.4, ADR-006). Post-entry states
# (HOLD/PRESSURE/EXIT) belong to stage 10 and are never emitted here.
VERDICTS = ("GO", "GO_WEAK", "WAIT", "AVOID")

BREAKERS = (
    "promoter_pledge_gt_25pct",
    "inverted_risk_reward",
    "f_and_o_ban",
    "earnings_within_7d",
    "tighter_than_0_75x_atr",
    "panic_regime",
)


def die(msg: str, exit_code: int = 1) -> None:
    print(f"{ERR_PARSE}: {msg}", file=sys.stderr)
    sys.exit(exit_code)


def load_trade(path: str) -> dict:
    try:
        trade = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        die(f"input file not found: {path}")
    except json.JSONDecodeError as exc:
        die(f"input is not valid JSON: {exc}")
    if not isinstance(trade, dict):
        die("input must be a JSON object")
    missing = [k for k in REQUIRED_FIELDS if k not in trade]
    if missing:
        die(f"missing required field(s): {', '.join(missing)}")
    return trade


def load_payloads(paths: list[str]) -> dict[str, dict]:
    """Key each supplied artifact by its canonical basename."""
    payloads: dict[str, dict] = {}
    for raw in paths:
        path = Path(raw)
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            die(f"unreadable payload {raw}: {exc}")
        if not isinstance(doc, dict):
            die(f"payload is not a JSON object: {raw}")
        payloads[path.name] = doc
    return payloads


def load_weights(path: str | None) -> dict[str, float]:
    """Module weights from thresholds.json (BRD §12.2), else the frozen set."""
    if not path or not Path(path).exists():
        return dict(DEFAULT_WEIGHTS)
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_WEIGHTS)  # unreadable thresholds never crash a run
    overrides = doc.get("module_weights") if isinstance(doc, dict) else None
    weights = dict(DEFAULT_WEIGHTS)
    if isinstance(overrides, dict):
        for name, value in overrides.items():
            if name in weights and isinstance(value, (int, float)) and value > 0:
                weights[name] = float(value)
    return weights


# --------------------------------------------------------------------------
# Scoring — every source grades to [0, 1] against its own weight
# --------------------------------------------------------------------------
def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def score_math(doc: dict) -> float:
    """R:R geometry (dominant) with a slippage deduction."""
    live_rr = float(doc.get("live_rr") or 0.0)
    slip = abs(float(doc.get("outside_range_pct") or 0.0))
    rr_component = _clip((live_rr - 0.5) / 2.5)   # 0.5 -> 0; 3.0 -> 1
    slip_component = 1.0 - _clip(slip / 3.0)      # 3% slip erases the bonus
    return 0.75 * rr_component + 0.25 * slip_component


def score_dtw(doc: dict) -> float:
    return _clip(float(doc.get("analog_confidence") or 0.0))


def score_ml(doc: dict) -> float:
    return _clip(float(doc.get("prob_target_hit_22d") or 0.0))


def score_flow(doc: dict) -> float:
    verdict = doc.get("matrix_verdict")
    base = {"LONG_BUILDUP": 0.6, "SHORT_COVERING_TRAP": 0.35,
            "LONG_UNWINDING": 0.2, "SHORT_BUILDUP": 0.1}.get(verdict, 0.2)
    z_bonus = 0.25 * _clip((float(doc.get("delivery_zscore") or 0.0) + 1.0) / 3.0)
    oi_bonus = 0.15 * _clip((float(doc.get("oi_change_pct") or 0.0) + 5.0) / 15.0)
    return _clip(base + z_bonus + oi_bonus)


def score_fundamental(doc: dict) -> float:
    piotroski = _clip(float(doc.get("piotoski_score") or 0) / 9.0)
    altman = _clip((float(doc.get("altman_z_score") or 0) - 0.5) / 2.5)
    cfo = _clip(float(doc.get("cfo_pat_ratio") or 0.0) / 1.0)
    pledge = float(doc.get("promoter_pledge_pct") or 0.0)
    pledge_factor = 1.0 - _clip(pledge / 25.0)    # 25% pledge erases all
    return _clip((0.4 * piotroski + 0.3 * altman + 0.3 * cfo)) * pledge_factor


def score_regime(doc: dict) -> float:
    rrg = {"LEADING": 0.45, "IMPROVING": 0.35,
           "WEAKENING": 0.15, "LAGGING": 0.05}.get(doc.get("sector_rrg_state"), 0.15)
    # `nifty_trend` speaks the frozen TICK-007 / BRD §7.2 vocabulary — the
    # stage-7 producer can emit nothing else, so scoring any other token set
    # (the retired BULLISH/NEUTRAL/BEARISH keys this once used) silently took
    # the 0.2 default on every real run (ADR-010 decision 1).
    nifty = {"ABOVE_50_200_DMA": 0.3, "BELOW_50_ABOVE_200": 0.2,
             "BELOW_200_DMA": 0.0}.get(doc.get("nifty_trend"), 0.2)
    vix = {"LOW": 0.25, "NORMAL": 0.2,
           "ELEVATED": 0.05, "PANIC": 0.0}.get(doc.get("vix_regime"), 0.2)
    return _clip(rrg + nifty + vix)


def score_precision(doc: dict) -> float:
    if doc.get("optimal_limit_buy") is None:
        return 0.0
    # Absence is deducted, never rewarded (BRD §10.4; plan-tier2 §1, the
    # Phase-1.3 honesty fix). The old `float(doc.get(...) or 0.0)` read "the
    # spread was never measured" as "the spread cost nothing" and paid out a
    # perfect 1.0 for evidence nobody collected — and blew up outright on a
    # non-numeric value. Only a real number is a measurement; the key missing,
    # a null, or junk all score 0.0, exactly like an unusable zone.
    spread = doc.get("spread_impact_cost_pct")
    if isinstance(spread, bool) or not isinstance(spread, (int, float)):
        return 0.0
    return _clip(1.0 - _clip(float(spread) / 0.5))


SOURCE_SCORE = {
    "out_1_math.json": score_math,
    "out_3_dtw.json": score_dtw,
    "out_4_ml.json": score_ml,
    "out_5_flow.json": score_flow,
    "out_6_fundamental.json": score_fundamental,
    "out_7_regime.json": score_regime,
    "out_8_dhan.json": score_precision,
}


# --------------------------------------------------------------------------
# Hard circuit breakers (BRD §10.3) — each reads only its own artifact
# --------------------------------------------------------------------------
def tripped_breakers(trade: dict, payloads: dict[str, dict]) -> list[str]:
    tripped: list[str] = []

    math_p = payloads.get("out_1_math.json") or {}
    if math_p and not math_p.get("waived"):
        live_rr = math_p.get("live_rr")
        if math_p.get("inverted_rr_flag") is True or (
                isinstance(live_rr, (int, float)) and live_rr < 1.0):
            tripped.append("inverted_risk_reward")

    market_p = payloads.get("out_2_market.json") or {}
    if market_p.get("is_tight_sl") is True:
        tripped.append("tighter_than_0_75x_atr")

    fund_p = payloads.get("out_6_fundamental.json") or {}
    if fund_p and not fund_p.get("waived"):
        if fund_p.get("pledge_circuit_breaker") is True or (
                float(fund_p.get("promoter_pledge_pct") or 0.0) > 25.0):
            tripped.append("promoter_pledge_gt_25pct")
        if (str(trade.get("instrument_type", "")).upper() == "FUT" and (
                fund_p.get("earnings_hazard_flag") is True or (
                    isinstance(fund_p.get("days_to_earnings"), (int, float))
                    and fund_p["days_to_earnings"] < 7))):
            tripped.append("earnings_within_7d")

    regime_p = payloads.get("out_7_regime.json") or {}
    if regime_p.get("panic_regime_breaker") is True:
        tripped.append("panic_regime")

    return tripped


def _limit_claim(payloads: dict[str, dict]) -> tuple[float | None, str | None]:
    """(limit price stage 8 stands behind, unavailability reason).

    Stage 8 owns the limit claim (signed proposal B, 2026-10-16): when its
    payload exists, `optimal_limit_buy` is the only price a limit leg may be
    computed from. A clamped zone (`zone_above_market: true`) or a wholly
    waived one carries **no** limit, and substituting the reco's band low
    would fabricate exactly what the engine just disowned — a limit leg in
    the flip table and a "restores R:R" rescue for the inverted-RR
    exception. Only a chain with no stage 8 at all keeps the legacy band
    reference.
    """
    dhan_p = payloads.get("out_8_dhan.json")
    if dhan_p is None:
        return None, None          # stage 8 absent — legacy fallback applies
    limit = dhan_p.get("optimal_limit_buy")
    if isinstance(limit, (int, float)) and not isinstance(limit, bool):
        return float(limit), None
    if dhan_p.get("zone_above_market") is True:
        return None, "zone_above_market"
    return None, "zone_unavailable"


def limit_rr(trade: dict, payloads: dict[str, dict]) -> float | None:
    """R:R if filled at stage 8's optimal limit (or the reco band low when
    stage 8 is absent from the chain entirely).

    Returns None when there is no limit claim at all — a clamped or waived
    zone is not a limit, and a fabricated R:R here would silently drive both
    the flip table and the inverted-RR WAIT exception. Geometry that cannot
    price the claim (non-numeric levels, risk <= 0) also returns None.
    """
    limit, reason = _limit_claim(payloads)
    if limit is None and reason is None:
        # Stage 8 absent from the chain — the legacy band reference. A
        # *named* unavailability (clamped/waived zone) substitutes nothing.
        limit = trade.get("entry_range_min")
    if limit is None:
        return None
    try:
        limit = float(limit)
        target = float(trade["target"])
        stop = float(trade["stoploss"])
    except (TypeError, ValueError):
        return None
    risk = limit - stop
    if risk <= 0:
        return None
    return (target - limit) / risk


# --------------------------------------------------------------------------
# Calibration (BRD §10.1)
# --------------------------------------------------------------------------
def calibrated_p_win(effective_score_pct: float) -> float:
    """Advisory anchor 55% + conditional improvement from the effective score."""
    raw = P_BASE + (effective_score_pct - 50.0) * P_SLOPE
    return round(_clip(raw, P_MIN, P_MAX), 4)


def expectancy(p_win: float, live_rr: float) -> float:
    """E = P(Win) × R:R − (1 − P(Win)), in R. Frozen example: 0.55, 2.0 → 0.65."""
    return round(p_win * live_rr - (1.0 - p_win), 4)


def flip_condition(trade: dict, payloads: dict[str, dict],
                   tripped: list[str]) -> list[str]:
    """Actionable, evidence-named conditions for the current refusal/wait."""
    conditions: list[str] = []
    if "inverted_risk_reward" in tripped:
        band_low = trade.get("entry_range_min")
        conditions.append(
            f"price returns to the limit band (<= {band_low}) so a limit order "
            "restores R:R >= 1.5")
    if "promoter_pledge_gt_25pct" in tripped:
        conditions.append("promoter pledge disclosed below 25% of equity")
    if "tighter_than_0_75x_atr" in tripped:
        conditions.append("a stop placed at or beyond 0.75x daily ATR is available")
    if "earnings_within_7d" in tripped:
        conditions.append("quarterly results are published (binary risk cleared)")
    cap = (payloads.get("out_6_fundamental.json") or {}).get("earnings_size_cap_pct")
    if isinstance(cap, (int, float)) and not isinstance(cap, bool) and cap > 0:
        conditions.append("quarterly results are published (the size-capping "
                          "earnings window clears)")
    if "panic_regime" in tripped:
        conditions.append("India VIX cools below 24 with breadth recovery")
    if not conditions:
        conditions.append(
            "live price improves to the limit band or the model/flow evidence "
            "strengthens P(win) above the 55% floor")
    return conditions


def synthesize(trade: dict, payloads: dict[str, dict],
               weights: dict[str, float]) -> dict:
    # --- score against available sources (waiver rule, BRD §10.4) ---
    final_score = 0.0
    max_possible = 0.0
    waived: list[str] = []
    for name, weight in weights.items():
        doc = payloads.get(name)
        if doc is None or doc.get("waived") is True:
            waived.append(name)
            continue
        max_possible += weight
        final_score += weight * SOURCE_SCORE[name](doc)

    final_score = round(final_score, 2)
    max_possible = round(max_possible, 2)
    effective = round(final_score / max_possible * 100.0, 2) if max_possible > 0 else 0.0
    p_win = calibrated_p_win(effective)

    # --- geometry ---
    math_p = payloads.get("out_1_math.json") or {}
    live_rr = math_p.get("live_rr")
    if not isinstance(live_rr, (int, float)):
        entry = float(trade.get("entry_price") or 0.0)
        stop = float(trade["stoploss"])
        live_rr = (float(trade["target"]) - entry) / (entry - stop) if entry > stop else 0.0
    live_rr = round(float(live_rr), 4)
    e = expectancy(p_win, live_rr)

    # --- breakers (BRD §10.3) ---
    tripped = tripped_breakers(trade, payloads)
    limit_view = limit_rr(trade, payloads)
    recoverable = (limit_view is not None and limit_view >= GO_RR_FLOOR)

    # Stage 6's corporate-calendar size cap (BRD §6.4 / §10.4): a hazard the
    # trade survives at half size rather than not at all. Only bytes stage 6
    # actually stands behind count — a waived payload caps nothing.
    fund_evidence = payloads.get("out_6_fundamental.json") or {}
    size_cap: float | None = None
    cap_reasons: list[str] = []
    if fund_evidence and fund_evidence.get("waived") is not True:
        cap = fund_evidence.get("earnings_size_cap_pct")
        if isinstance(cap, (int, float)) and not isinstance(cap, bool) and cap > 0:
            size_cap = float(cap)
            days = fund_evidence.get("days_to_earnings")
            window = (f"{days} session(s)" if isinstance(days, int)
                      else "the holding window")
            cap_reasons.append(
                f"quarterly earnings in {window} "
                f"(BRD §6.4 corporate-calendar hazard)")

    # Stage 7's macro sizing gates (BRD §7.2, §7.4; ADR-010 decision 6): a
    # Risk-Off breadth regime or sustained combined FII+DII net selling sizes
    # the trade down — the same half-size lever the §6.4 calendar cap uses —
    # and never vetoes it: the fatal breaker stays exactly §10.3 #6 (VIX > 24
    # AND breadth < 30). A waived or absent regime payload caps nothing.
    regime_evidence = payloads.get("out_7_regime.json") or {}
    if regime_evidence and regime_evidence.get("waived") is not True:
        if regime_evidence.get("breadth_regime") == "RISK_OFF":
            cap_reasons.append(
                "market breadth in the RISK_OFF regime, under 40% of NSE 500 "
                "above their 200 DMA (BRD §7.2 gates all long setups)")
        if regime_evidence.get("fii_dii_flow_bias") == "NET_SELLING":
            cap_reasons.append(
                "combined FII+DII 5-day net selling beyond -₹5,000 Cr "
                "(BRD §7.4 cautious sizing)")
    if cap_reasons and size_cap is None:
        size_cap = 50.0

    decision: str
    avoid_reason: str | None = None
    confidence_deficit: str | None = None
    fatal = [b for b in tripped if b != "inverted_risk_reward"]

    if fatal:
        decision = "AVOID"
        avoid_reason = "hard circuit breaker(s) tripped: " + ", ".join(fatal)
    elif "inverted_risk_reward" in tripped:
        if recoverable:
            decision = "WAIT"
        else:
            decision = "AVOID"
            avoid_reason = ("inverted R:R at market and no limit order "
                            f"restores R:R >= 1.5 (limit view: {limit_view})")
    elif e <= 0.0:
        decision = "AVOID"
        avoid_reason = (f"negative expectancy ({e:+.2f}R) at live R:R {live_rr} "
                        f"and P(win) {p_win:.1%}")
    elif p_win < GO_WIN_FLOOR:
        decision = "GO_WEAK"
        confidence_deficit = (f"P(win) {p_win:.1%} below the 55% floor with "
                              f"positive expectancy ({e:+.2f}R)")
    elif waived:
        decision = "GO_WEAK"
        confidence_deficit = ("waived source(s) reduced the evidence base: "
                              + ", ".join(sorted(waived)))
    elif live_rr < GO_RR_FLOOR and recoverable:
        decision = "WAIT"
    elif size_cap is not None:
        decision = "GO_WEAK"
        confidence_deficit = (f"size capped at {size_cap:g}% — "
                              + "; ".join(cap_reasons))
    else:
        decision = "GO"

    # Flip Condition Invariant (BRD §10.4): mandatory for every non-GO.
    what_would_flip_me = (None if decision == "GO"
                          else flip_condition(trade, payloads, tripped))

    return {
        "symbol": trade.get("symbol"),
        "final_score": final_score,
        "max_possible_score": max_possible,
        "effective_score_pct": effective,
        "decision": decision,
        "verdict": decision,
        "p_win": p_win,
        "expectancy": e,
        "circuit_breakers_tripped": tripped,
        "three_predictions": {
            "direction_1m": _direction(payloads),
            "expected_range": _range_cone(payloads),
            "optimal_price_range": _optimal_zone(trade, payloads),
        },
        "flip_table": _flip_table(trade, payloads, p_win),
        "what_would_flip_me": what_would_flip_me,
        "avoid_reason": avoid_reason,
        "confidence_deficit": confidence_deficit,
    }


def _direction(payloads: dict[str, dict]) -> dict:
    dtw_p = payloads.get("out_3_dtw.json") or {}
    ml_p = payloads.get("out_4_ml.json") or {}
    return {
        "analog_confidence": dtw_p.get("analog_confidence"),
        "dtw_win_rate_22d": dtw_p.get("dtw_win_rate_22d"),
        "prob_target_hit_22d": ml_p.get("prob_target_hit_22d"),
    }


def _range_cone(payloads: dict[str, dict]) -> dict:
    ml_p = payloads.get("out_4_ml.json") or {}
    return {
        "price_cone_p10_p50_p90": ml_p.get("price_cone_p10_p50_p90"),
        "mae_projected": ml_p.get("mae_projected"),
    }


def _optimal_zone(trade: dict, payloads: dict[str, dict]) -> dict:
    dhan_p = payloads.get("out_8_dhan.json") or {}
    return {
        "optimal_limit_buy": dhan_p.get("optimal_limit_buy"),
        "zone_above_market": dhan_p.get("zone_above_market"),
        "recommended_band_low": trade.get("entry_range_min"),
        "recommended_band_high": trade.get("entry_range_max"),
    }


def _flip_table(trade: dict, payloads: dict[str, dict], p_win: float) -> dict:
    """Market order vs limit order comparison (BRD §16).

    The limit leg is stage 8's claim or it does not exist: a clamped or
    waived zone ships `entry: null`, `r_r: null` and an `unavailable` reason
    (`zone_above_market` / `zone_unavailable`) instead of the reco band's
    low wearing the limit's hat. Only a chain with no stage 8 payload at all
    keeps the legacy band reference.
    """
    math_p = payloads.get("out_1_math.json") or {}
    live = math_p.get("live_rr")
    limit_view = limit_rr(trade, payloads)
    limit_entry, unavailable = _limit_claim(payloads)
    if "out_8_dhan.json" in payloads:
        leg_entry = limit_entry
    else:
        leg_entry = trade.get("entry_range_min")  # legacy: stage 8 absent
    return {
        "market_order": {"entry": trade.get("live_price"),
                         "r_r": live if isinstance(live, (int, float)) else None},
        "limit_order": {"entry": leg_entry,
                        "r_r": round(limit_view, 4) if limit_view is not None else None,
                        "unavailable": unavailable},
        "p_win": p_win,
    }


def render_markdown(out: dict) -> str:
    """The compact §16 terminal report, written to stderr (stdout stays JSON)."""
    verdict = out["decision"]
    lines = [
        "### 🛡️ UVEST DECISION ENGINE",
        f"**{out['symbol']}** · SCORE: {out['final_score']}/{out['max_possible_score']}"
        f" ({out['effective_score_pct']:.1f}%) · P(WIN): {out['p_win']:.1%}"
        f" · EXPECTANCY: {out['expectancy']:+.2f}R",
        f"**VERDICT: {verdict}**"
        + (f" — {out['avoid_reason']}" if out["avoid_reason"] else "")
        + (f" — {out['confidence_deficit']}" if out["confidence_deficit"] else ""),
    ]
    if out["circuit_breakers_tripped"]:
        lines.append("Breakers: " + ", ".join(out["circuit_breakers_tripped"]))
    if out["what_would_flip_me"]:
        lines.append("Flip conditions:")
        lines += [f"- {c}" for c in out["what_would_flip_me"]]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="UVEST Stage 9: Consolidated Decision Engine")
    parser.add_argument("--input", default="current_trade.json",
                        help="Path to canonical current_trade.json")
    parser.add_argument("--payload", action="append", default=[],
                        help="An upstream artifact JSON (repeatable), e.g. "
                             "out_1_math.json … out_8_dhan.json")
    parser.add_argument("--thresholds", default=None,
                        help="Path to thresholds.json weight overrides")
    add_record_arg(parser)
    args = parser.parse_args(argv)

    trade = load_trade(args.input)
    payloads = load_payloads(args.payload)
    weights = load_weights(args.thresholds)

    out = synthesize(trade, payloads, weights)

    output_str = json.dumps(out, indent=2) + "\n"
    print(render_markdown(out), file=sys.stderr)

    bundle = resolve_bundle_dir(args.record,
                                str(trade.get("symbol") or "UNSPECIFIED"),
                                trade.get("recommended_date"))
    if bundle is not None:
        record_artifact(bundle, 9, output_str)

    sys.stdout.write(output_str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
