#!/usr/bin/env python3
"""UVEST stage 6 — Fundamental & Corporate Governance Auditor (TICK-006).

Sole owner of the fundamental-quality and governance-hazard verdicts (BRD §6,
§9.1 row 6). No quantitative setup survives accounting fraud, promoter debt
liquidation, or a surprise earnings collapse, so this stage audits three risk
dimensions before the decision engine scores them:

1. **Financial health (BRD §6.1).**
   - Piotroski F-Score (0–9): positive ROA, positive CFO, ROA improved,
     CFO > net profit (the accrual test), long-term-debt/assets down,
     current ratio up, no share dilution, gross margin up, asset turnover up.
     A score of `<= 4` yields the §6.1 score penalty (`piotoski_penalty`,
     −15 pts) for the composite model (BRD §10.2 module 4) to apply.
   - Altman Z''-score (emerging-market, 4-factor):
     `Z'' = 6.56·X1 + 3.26·X2 + 6.72·X3 + 1.05·X4`
     with X1 = working capital/TA, X2 = retained earnings/TA, X3 = EBIT/TA,
     X4 = book equity/total liabilities. `< 1.1` distress, `> 2.6` safe.
   - Beneish M-Score (8-variable earnings-manipulation screen):
     `M = −4.84 + 0.920·DSRI + 0.528·GMI + 0.404·AQI + 0.892·SGI
           + 0.115·DEPI − 0.172·SGAI + 4.679·TATA − 0.327·LVGI`
     with `M > −1.78` the red flag.
2. **Cash-flow quality (BRD §6.2).** `cfo_pat_ratio = CFO / PAT`; `< 0.6`
   flags the accrual anomaly (paper profits on uncollected receivables).
3. **Governance kill switches & calendar hazards (BRD §6.3, §6.4).**
   - `promoter_pledged_pct > 15%` → severe warning; `> 25.0%` →
     `pledge_circuit_breaker: true`, the BRD §10.3 #1 hard kill switch
     (strictly greater: exactly 25.0% does not trip — the eval harness grades
     the same boundary).
   - Promoter holding down `> 2%` over two quarters without an OFS/capex
     disclosure → `insider_selling_flag`; retail up while FII *and* DII
     contract → `ownership_divergence_flag`; a non-routine auditor resignation
     within 12 months → `auditor_resignation_flag`.
   - `days_to_earnings < 7` on a Futures trade → `earnings_hazard_flag: true`
     (BRD §10.3 #4, fatal gap-down risk); `< 10` on Cash Equities →
     `earnings_size_cap_pct: 50` (BRD §6.4, `GO_WEAK`).

**Tri-state discipline (BRD §10.4, ADR-003).** Absence never becomes a
default: a metric whose statement inputs are missing is `null` and the missing
names are disclosed in `missing_inputs`, never graded as `0`. A missing
statement bundle takes the waiver path — `waived: true`, every analytical field
`null`, exit 0.

**Data policy (ADR-003, Tier-1 free-only).** In production the statements come
from Screener.in / NSE corporate filings. Offline — the recorded-bundle
architecture this repo runs on — `--financials-fixture` points at a recorded
statement bundle (`tests/fixtures/reliance_fundamentals.json`); without it the
waiver path is honored. The stage never calls the network.

Reads `--input current_trade.json`; writes `out_6_fundamental.json` to stdout.
Supports `--record <dir>` for deterministic replay-bundle recording.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _recorder import add_record_arg, record_artifact, resolve_bundle_dir  # noqa: E402

ERR_PARSE = "ERR_PARSE"

REQUIRED_FIELDS = ["symbol", "instrument_type"]

# BRD §6.3 #1 — the Indian circuit breaker (strict inequalities, frozen).
PLEDGE_WARNING_PCT = 15.0
PLEDGE_CIRCUIT_PCT = 25.0
# BRD §6.1 #1 — `F <= 4` automatic score penalty for the composite model.
PIOTOSKI_PENALTY_FLOOR = 4
PIOTOSKI_PENALTY_PTS = -15.0
# BRD §6.2 — CFO/PAT below this flags the accrual anomaly.
CFO_PAT_ACCRUAL_FLOOR = 0.6
# BRD §6.1 #2 — Altman Z'' zones.
ALTMAN_DISTRESS_MAX = 1.1
ALTMAN_SAFE_MIN = 2.6
# BRD §6.1 #3 — Beneish red flag.
BENEISH_RED_FLAG_MIN = -1.78
# BRD §6.3 #2 — promoter-holding decline over two quarters.
HOLDING_DECLINE_PCT = 2.0
# BRD §6.4 / §10.3 #4 — calendar hazards.
FUT_EARNINGS_BLOCK_DAYS = 7
CASH_EARNINGS_CAP_DAYS = 10
CASH_EARNINGS_CAP_PCT = 50

CURRENT = "statement_fy_current"
PRIOR = "statement_fy_prior"

# Frozen Piotroski signal roster, in evaluation order (BRD §6.1 #1 names eight
# of them; the ninth is the standard ROA-improvement signal the 0–9 scale
# implies). The earned names are disclosed so a score is auditable.
PIOTROSKI_SIGNALS = (
    "positive_roa",
    "positive_cfo",
    "roa_improved",
    "cfo_exceeds_net_income",
    "leverage_decreased",
    "current_ratio_improved",
    "no_share_dilution",
    "gross_margin_improved",
    "asset_turnover_improved",
)

FUTURES_TOKENS = ("FUT", "FUTURES")
CASH_TOKENS = ("CASH", "EQ", "EQUITY", "CNC", "MIS")


def die(code: str, exit_code: int = 1, hint: str = "") -> None:
    print(f"{code}: {hint}" if hint else code, file=sys.stderr)
    sys.exit(exit_code)


def load_payload(path: str) -> dict:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        die(ERR_PARSE, 1, f"input file not found: {path}")
    except json.JSONDecodeError as exc:
        die(ERR_PARSE, 1, f"input is not valid JSON: {exc}")
    if not isinstance(payload, dict):
        die(ERR_PARSE, 1, "input must be a JSON object")
    missing = [key for key in REQUIRED_FIELDS if key not in payload]
    if missing:
        die(ERR_PARSE, 1, f"missing required field(s): {', '.join(missing)}")
    return payload


def load_financials(path: str | None) -> dict | None:
    """Load the statement bundle, or None for the waiver path.

    An unreadable or non-object bundle is *absence*, exactly like an omitted
    fixture: the stage never half-reads a corrupted statement into a verdict.
    """
    if not path:
        return None
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return document if isinstance(document, dict) else None


class StatementInputs:
    """Resolves a metric's inputs, recording every one the statements lack.

    A metric whose inputs are incomplete is `null` — never `0.0` — and the
    dotted names it could not read are disclosed in `missing_inputs`, so a null
    is auditable instead of silently gradeable (BRD §10.4, ADR-003).
    """

    def __init__(self, documents: dict[str, dict]):
        self.documents = documents
        self.missing: set[str] = set()

    def get(self, document: str, key: str) -> float | None:
        value = (self.documents.get(document) or {}).get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            self.missing.add(f"{document}.{key}")
            return None
        return float(value)


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    """`numerator / denominator`, or None when either side is unknown/zero."""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def piotroski_f_score(ins: StatementInputs) -> tuple[int | None, list[str], list[str]]:
    """`(score, earned_signals, undecided_signals)` — BRD §6.1 #1.

    A 0–9 score is only honest when all nine signals are decidable: an
    undecidable signal returns `None` for the score (with the undecided names
    listed) rather than counting an unknown as a miss.
    """
    roa_now = _ratio(ins.get(CURRENT, "net_income"), ins.get(CURRENT, "total_assets"))
    roa_prior = _ratio(ins.get(PRIOR, "net_income"), ins.get(PRIOR, "total_assets"))
    cfo_now = ins.get(CURRENT, "cfo")
    ni_now = ins.get(CURRENT, "net_income")
    leverage_now = _ratio(ins.get(CURRENT, "long_term_debt"),
                          ins.get(CURRENT, "total_assets"))
    leverage_prior = _ratio(ins.get(PRIOR, "long_term_debt"),
                            ins.get(PRIOR, "total_assets"))
    ratio_now = _ratio(ins.get(CURRENT, "current_assets"),
                       ins.get(CURRENT, "current_liabilities"))
    ratio_prior = _ratio(ins.get(PRIOR, "current_assets"),
                         ins.get(PRIOR, "current_liabilities"))
    shares_now = ins.get(CURRENT, "shares_outstanding")
    shares_prior = ins.get(PRIOR, "shares_outstanding")
    margin_now = _ratio(ins.get(CURRENT, "gross_profit"), ins.get(CURRENT, "revenue"))
    margin_prior = _ratio(ins.get(PRIOR, "gross_profit"), ins.get(PRIOR, "revenue"))
    turnover_now = _ratio(ins.get(CURRENT, "revenue"), ins.get(CURRENT, "total_assets"))
    turnover_prior = _ratio(ins.get(PRIOR, "revenue"), ins.get(PRIOR, "total_assets"))

    checks: list[tuple[str, bool | None]] = [
        ("positive_roa", None if roa_now is None else roa_now > 0),
        ("positive_cfo", None if cfo_now is None else cfo_now > 0),
        ("roa_improved",
         None if None in (roa_now, roa_prior) else roa_now > roa_prior),
        ("cfo_exceeds_net_income",
         None if None in (cfo_now, ni_now) else cfo_now > ni_now),
        ("leverage_decreased",
         None if None in (leverage_now, leverage_prior)
         else leverage_now < leverage_prior),
        ("current_ratio_improved",
         None if None in (ratio_now, ratio_prior) else ratio_now > ratio_prior),
        ("no_share_dilution",
         None if None in (shares_now, shares_prior) else shares_now <= shares_prior),
        ("gross_margin_improved",
         None if None in (margin_now, margin_prior) else margin_now > margin_prior),
        ("asset_turnover_improved",
         None if None in (turnover_now, turnover_prior)
         else turnover_now > turnover_prior),
    ]
    earned = [name for name, earned_ok in checks if earned_ok]
    undecided = [name for name, earned_ok in checks if earned_ok is None]
    return (None if undecided else len(earned)), earned, undecided


def altman_z_double_prime(ins: StatementInputs) -> float | None:
    """Emerging-market Altman Z'' (BRD §6.1 #2); None when components are absent."""
    assets = ins.get(CURRENT, "total_assets")
    current_assets = ins.get(CURRENT, "current_assets")
    current_liabilities = ins.get(CURRENT, "current_liabilities")
    working_capital = (None if None in (current_assets, current_liabilities)
                       else current_assets - current_liabilities)
    x1 = _ratio(working_capital, assets)
    x2 = _ratio(ins.get(CURRENT, "retained_earnings"), assets)
    x3 = _ratio(ins.get(CURRENT, "ebit"), assets)
    x4 = _ratio(ins.get(CURRENT, "book_equity"),
                ins.get(CURRENT, "total_liabilities"))
    if None in (x1, x2, x3, x4):
        return None
    return 6.56 * x1 + 3.26 * x2 + 6.72 * x3 + 1.05 * x4


def altman_zone(score: float | None) -> str | None:
    """`DISTRESS` below 1.1, `SAFE` above 2.6, else the grey zone (BRD §6.1 #2)."""
    if score is None:
        return None
    if score < ALTMAN_DISTRESS_MAX:
        return "DISTRESS"
    if score > ALTMAN_SAFE_MIN:
        return "SAFE"
    return "GREY"


def beneish_m_score(ins: StatementInputs) -> float | None:
    """Eight-variable Beneish M-Score (BRD §6.1 #3); None when inputs are absent."""
    def days_sales_receivables(document: str) -> float | None:
        return _ratio(ins.get(document, "receivables"), ins.get(document, "revenue"))

    def gross_margin(document: str) -> float | None:
        return _ratio(ins.get(document, "gross_profit"), ins.get(document, "revenue"))

    def asset_quality(document: str) -> float | None:
        assets = ins.get(document, "total_assets")
        current = ins.get(document, "current_assets")
        ppe = ins.get(document, "ppe")
        if None in (assets, current, ppe):
            return None
        return _ratio(assets - current - ppe, assets)

    def depreciation_rate(document: str) -> float | None:
        depreciation = ins.get(document, "depreciation")
        ppe = ins.get(document, "ppe")
        if None in (depreciation, ppe):
            return None
        return _ratio(depreciation, depreciation + ppe)

    def leverage(document: str) -> float | None:
        assets = ins.get(document, "total_assets")
        current = ins.get(document, "current_liabilities")
        long_term = ins.get(document, "long_term_debt")
        if None in (assets, current, long_term):
            return None
        return _ratio(current + long_term, assets)

    def sga_intensity(document: str) -> float | None:
        return _ratio(ins.get(document, "sga_expense"), ins.get(document, "revenue"))

    dsri = _ratio(days_sales_receivables(CURRENT), days_sales_receivables(PRIOR))
    gmi = _ratio(gross_margin(PRIOR), gross_margin(CURRENT))
    aqi = _ratio(asset_quality(CURRENT), asset_quality(PRIOR))
    sgi = _ratio(ins.get(CURRENT, "revenue"), ins.get(PRIOR, "revenue"))
    depi = _ratio(depreciation_rate(PRIOR), depreciation_rate(CURRENT))
    sgai = _ratio(sga_intensity(CURRENT), sga_intensity(PRIOR))
    lvgi = _ratio(leverage(CURRENT), leverage(PRIOR))
    ni_now, cfo_now = ins.get(CURRENT, "net_income"), ins.get(CURRENT, "cfo")
    tata = _ratio(None if None in (ni_now, cfo_now) else ni_now - cfo_now,
                  ins.get(CURRENT, "total_assets"))
    if None in (dsri, gmi, aqi, sgi, depi, sgai, lvgi, tata):
        return None
    return (-4.84
            + 0.920 * dsri
            + 0.528 * gmi
            + 0.404 * aqi
            + 0.892 * sgi
            + 0.115 * depi
            - 0.172 * sgai
            + 4.679 * tata
            - 0.327 * lvgi)


def cfo_pat_ratio(ins: StatementInputs) -> float | None:
    """Operating cash flow over reported profit after tax (BRD §6.2)."""
    return _ratio(ins.get(CURRENT, "cfo"), ins.get(CURRENT, "net_income"))


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _shift(document: dict, low_key: str, high_key: str) -> float | None:
    """Change across the window (`q_latest` − `q_minus_3`), or None if unknown."""
    low, high = _number(document.get(low_key)), _number(document.get(high_key))
    if low is None or high is None:
        return None
    return high - low


def governance_flags(governance: dict) -> dict:
    """Promoter, ownership and auditor red flags (BRD §6.3)."""
    pledge = _number(governance.get("promoter_pledged_pct"))
    holding = governance.get("promoter_holding_pct") or {}
    held_then, held_now = _number(holding.get("q_minus_2")), _number(holding.get("q_latest"))
    decline = None if None in (held_then, held_now) else round(held_then - held_now, 4)
    disclosed = governance.get("holding_change_disclosed") is True

    fii = _shift(governance.get("fii_pct") or {}, "q_minus_3", "q_latest")
    dii = _shift(governance.get("dii_pct") or {}, "q_minus_3", "q_latest")
    retail = _shift(governance.get("retail_pct") or {}, "q_minus_3", "q_latest")
    divergence = None
    if None not in (fii, dii, retail):
        divergence = retail > 0 and fii < 0 and dii < 0

    auditor = (governance.get("auditor_resigned_within_12m") is True
               and governance.get("auditor_resignation_routine_rotation") is not True)

    return {
        "promoter_pledge_pct": pledge,
        # BRD §6.3 #1 — a severe warning above 15%, the hard kill switch above 25%.
        "promoter_pledge_warning": None if pledge is None else pledge > PLEDGE_WARNING_PCT,
        "pledge_circuit_breaker": None if pledge is None else pledge > PLEDGE_CIRCUIT_PCT,
        "promoter_holding_decline_pct": decline,
        # A disclosed OFS/capex is the one thing that explains a decline away.
        "insider_selling_flag": (None if decline is None
                                 else decline > HOLDING_DECLINE_PCT and not disclosed),
        "ownership_divergence_flag": divergence,
        "auditor_resignation_flag": auditor,
    }


def instrument_kind(trade: dict) -> str:
    """`FUTURES`, `CASH`, or `OTHER` — the §6.4 hazard split."""
    token = str(trade.get("instrument_type") or trade.get("instrument") or "").upper()
    if token in FUTURES_TOKENS:
        return "FUTURES"
    if token in CASH_TOKENS:
        return "CASH"
    return "OTHER"


def _weekdays_between(start: str | None, end: str | None) -> int | None:
    """Whole weekday sessions after `start` up to and including `end` (Mon–Fri).

    The disclosure fallback when a bundle states no session count of its own:
    the frozen convention counts Mon–Fri, never the exchange calendar, and the
    payload names the basis so a reader can tell the two apart.
    """
    try:
        first, last = date.fromisoformat(str(start)), date.fromisoformat(str(end))
    except (TypeError, ValueError):
        return None
    if last < first:
        return None
    return sum(1 for offset in range(1, (last - first).days + 1)
               if (first + timedelta(days=offset)).weekday() < 5)


def days_to_earnings(trade: dict, earnings: dict) -> tuple[int | None, str | None]:
    """`(days, basis)` — the bundle's own session count when it states one."""
    stated = earnings.get("sessions_to_earnings")
    if isinstance(stated, int) and not isinstance(stated, bool) and stated >= 0:
        return stated, "sessions"
    counted = _weekdays_between(trade.get("recommended_date"),
                                earnings.get("next_earnings_date"))
    return (None, None) if counted is None else (counted, "weekdays")


def compute_fundamental_audit(trade: dict, financials: dict | None) -> dict:
    """The `out_6_fundamental.json` contract payload (TICK-006 §2)."""
    symbol = trade.get("symbol")
    if financials is None:
        return {
            "symbol": symbol,
            "as_of": None,
            "piotoski_score": None,
            "piotoski_signals": None,
            "piotoski_penalty": None,
            "altman_z_score": None,
            "altman_zone": None,
            "beneish_m_score": None,
            "beneish_red_flag": None,
            "cfo_pat_ratio": None,
            "accrual_warning": None,
            "promoter_pledge_pct": None,
            "promoter_pledge_warning": None,
            "pledge_circuit_breaker": None,
            "promoter_holding_decline_pct": None,
            "insider_selling_flag": None,
            "ownership_divergence_flag": None,
            "auditor_resignation_flag": None,
            "days_to_earnings": None,
            "days_to_earnings_basis": None,
            "earnings_hazard_flag": None,
            "earnings_size_cap_pct": None,
            "missing_inputs": None,
            "waived": True,
        }

    # No usable statements at all is the waiver case the ticket names; partial
    # statements are null metrics with the gaps disclosed instead.
    if not any(isinstance(financials.get(key), dict)
               for key in (CURRENT, PRIOR)):
        return {**compute_fundamental_audit(trade, None), "waived": True}

    ins = StatementInputs({CURRENT: financials.get(CURRENT) or {},
                           PRIOR: financials.get(PRIOR) or {}})
    score, earned, undecided = piotroski_f_score(ins)
    altman = altman_z_double_prime(ins)
    beneish = beneish_m_score(ins)
    cash_flow = cfo_pat_ratio(ins)
    flags = governance_flags(financials.get("governance") or {})
    days, basis = days_to_earnings(trade, financials.get("earnings") or {})
    kind = instrument_kind(trade)

    # BRD §6.4 / §10.3 #4: the futures hazard is binary and fatal, the cash
    # hazard is a size cap. Both stay tri-state when the calendar is unknown.
    hazard = (None if days is None
              else kind == "FUTURES" and days < FUT_EARNINGS_BLOCK_DAYS)
    size_cap = (None if days is None or kind != "CASH" or days >= CASH_EARNINGS_CAP_DAYS
                else CASH_EARNINGS_CAP_PCT)

    return {
        "symbol": symbol,
        "as_of": financials.get("as_of"),
        "piotoski_score": score,
        # BRD §6.1 #1's penalty is emitted here and applied by the composite
        # model (BRD §10.2 module 4), which owns the 100-point total.
        "piotoski_signals": earned,
        "piotoski_penalty": (PIOTOSKI_PENALTY_PTS
                             if score is not None and score <= PIOTOSKI_PENALTY_FLOOR
                             else (None if score is None else 0.0)),
        "altman_z_score": None if altman is None else round(altman, 4),
        "altman_zone": altman_zone(altman),
        "beneish_m_score": None if beneish is None else round(beneish, 4),
        "beneish_red_flag": (None if beneish is None
                             else beneish > BENEISH_RED_FLAG_MIN),
        "cfo_pat_ratio": None if cash_flow is None else round(cash_flow, 4),
        "accrual_warning": (None if cash_flow is None
                            else cash_flow < CFO_PAT_ACCRUAL_FLOOR),
        "promoter_holding_decline_pct": flags["promoter_holding_decline_pct"],
        "days_to_earnings": days,
        "days_to_earnings_basis": basis,
        "earnings_hazard_flag": hazard,
        "earnings_size_cap_pct": size_cap,
        # Undecided Piotroski signals are disclosed alongside the missing
        # statement inputs, so a null score always names its obstacle.
        "missing_inputs": sorted(ins.missing) + [
            f"piotroski_signal.{name}" for name in undecided],
        "waived": False,
        **{key: flags[key] for key in ("promoter_pledge_pct",
                                       "promoter_pledge_warning",
                                       "pledge_circuit_breaker",
                                       "insider_selling_flag",
                                       "ownership_divergence_flag",
                                       "auditor_resignation_flag")},
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="UVEST Stage 6: Fundamental & Corporate Governance Auditor")
    parser.add_argument("--input", default="current_trade.json",
                        help="Path to canonical current_trade.json")
    parser.add_argument("--financials-fixture", default=None,
                        help="Path to a recorded financial-statement bundle JSON "
                             "(offline mode; omit for the waiver path)")
    add_record_arg(parser)

    args = parser.parse_args()

    payload = load_payload(args.input)
    financials = load_financials(args.financials_fixture)
    result = compute_fundamental_audit(payload, financials)

    output_str = json.dumps(result, indent=2) + "\n"

    bundle = resolve_bundle_dir(args.record, payload.get("symbol") or "UNSPECIFIED",
                                payload.get("recommended_date"))
    if bundle is not None:
        record_artifact(bundle, 6, output_str)

    sys.stdout.write(output_str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
