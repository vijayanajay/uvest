#!/usr/bin/env python3
"""UVEST stage 7 — Sector Relative Strength & Macro VIX Regime (TICK-007).

Sole owner of the Sector, Industry Index & Macro Regime Engine (BRD §7,
§9.1 row 7). Consumes `current_trade.json`, loads one recorded
`uvest-market-context/1` evidence bundle (ADR-010), and emits:

* Mansfield RS of the trade's NSE sectoral index vs NIFTY 50, plus its
  5-session momentum — the two RRG axes (BRD §7.1), mapping to
  LEADING / WEAKENING / LAGGING / IMPROVING (zero counts as positive side).
* `nifty_trend` in the frozen BRD §7.2 tokens (200-DMA test first; a close
  equal to an average counts as above it).
* NSE 500 breadth regime, India VIX band, 5-day FII/DII flow bias.
* `panic_regime_breaker` (BRD §10.3 #6): VIX > 24.0 AND breadth < 30.0 —
  strict on both arms, so exactly 24.0 or exactly 30.0 never trips it.

**Evidence policy (ADR-010): one fixture, one waiver.** The stage runs
entirely from `--regime-fixture`; any absent, short, malformed or misaligned
input — including a symbol absent from `SECTOR_INDEX_MAP` — waives the WHOLE
payload (`waived: true`, every metric `null`, `missing_inputs` naming each
cause in the frozen check order, exit 0). BRD §10.4's waiver rule is
payload-granular: stage 9 scores this artifact as one source, so a mid-value
default for one missing component would be exactly the inflation §10.4
forbids. The stage never fabricates an index it cannot name.

Offline by design: zero network calls (the live NIFTY-Indices/yfinance
connector is the separate BRD §8 open item).

Reads `--input current_trade.json`; writes `out_7_regime.json` to stdout.
Supports `--record <dir>` for deterministic replay bundle recording.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _recorder import add_record_arg, record_artifact, resolve_bundle_dir  # noqa: E402

ERR_PARSE = "ERR_PARSE"

REQUIRED_FIELDS = ["symbol"]

SCHEMA = "uvest-market-context/1"

NSEI = "^NSEI"
INDIA_VIX = "^INDIAVIX"
BREADTH = "NSE500_BREADTH"
FLOW = "FII_DII_NET"

# Symbol → NSE sectoral index ticker. Conservative by design: a symbol whose
# membership is not pinned here is *unmapped* and waives the stage — never a
# guessed index (ADR-010 decision 5; the stage-0 OPTIONS_TYPES precedent of
# domain data living in code, pinned by tests).
SECTOR_INDEX_MAP = {
    # NIFTY ENERGY
    "RELIANCE": "^CNXENERGY", "ONGC": "^CNXENERGY", "IOC": "^CNXENERGY",
    "BPCL": "^CNXENERGY", "GAIL": "^CNXENERGY",
    # NIFTY BANK
    "HDFCBANK": "^NSEBANK", "ICICIBANK": "^NSEBANK", "SBIN": "^NSEBANK",
    "KOTAKBANK": "^NSEBANK", "AXISBANK": "^NSEBANK", "INDUSINDBK": "^NSEBANK",
    # NIFTY IT
    "INFY": "^CNXIT", "TCS": "^CNXIT", "HCLTECH": "^CNXIT", "WIPRO": "^CNXIT",
    "TECHM": "^CNXIT", "LTIM": "^CNXIT",
    # NIFTY AUTO
    "MARUTI": "^CNXAUTO", "TATAMOTORS": "^CNXAUTO", "M&M": "^CNXAUTO",
    "BAJAJ-AUTO": "^CNXAUTO", "HEROMOTOCO": "^CNXAUTO",
    "EICHERMOT": "^CNXAUTO", "ASHOKLEY": "^CNXAUTO",
    # NIFTY PHARMA
    "SUNPHARMA": "^CNXPHARMA", "DRREDDY": "^CNXPHARMA", "CIPLA": "^CNXPHARMA",
    "DIVISLAB": "^CNXPHARMA", "LUPIN": "^CNXPHARMA", "TORNTPHARM": "^CNXPHARMA",
    # NIFTY FMCG
    "HINDUNILVR": "^CNXFMCG", "ITC": "^CNXFMCG", "NESTLEIND": "^CNXFMCG",
    "BRITANNIA": "^CNXFMCG", "DABUR": "^CNXFMCG", "MARICO": "^CNXFMCG",
    "TATACONSUM": "^CNXFMCG", "GODREJCP": "^CNXFMCG",
    # NIFTY METAL
    "TATASTEEL": "^CNXMETAL", "JSWSTEEL": "^CNXMETAL", "HINDALCO": "^CNXMETAL",
    "VEDL": "^CNXMETAL", "JINDALSTEL": "^CNXMETAL",
    # NIFTY REALTY
    "DLF": "^CNXREALTY", "GODREJPROP": "^CNXREALTY", "OBEROIRLTY": "^CNXREALTY",
    # NIFTY PSU BANK
    "BANKBARODA": "^CNXBANK", "PNB": "^CNXBANK", "CANBK": "^CNXBANK",
    # NIFTY INFRA (thematic). Membership pinned — not guessed — from four
    # constituent lists read 2026-10-14: TradingView NSE:CNXINFRA components,
    # Economic Times' Nifty Infrastructure constituents, Investing.com
    # cnx-infrastructure components, and Yahoo's ^CNXINFRA quote/history
    # (the live Yahoo ticker this map addresses). Both LT and ADANIPORTS
    # appear in all four.
    "LT": "^CNXINFRA", "ADANIPORTS": "^CNXINFRA",
}

# Frozen check order for missing_inputs (TICK-007 §2): fixture, symbol,
# series presence (index series first), axis alignment, then per-series
# minimums in the same series order.
FROZEN_SERIES_ORDER = (NSEI, "SECTOR", INDIA_VIX, BREADTH, FLOW)

MIN_SESSIONS = {NSEI: 200, INDIA_VIX: 1, BREADTH: 1, FLOW: 5}
MIN_SECTOR_SESSIONS = 55  # 50-SMA window + 5-session momentum lag

MRS_SMA = 50
MOMENTUM_LAG = 5

FLOW_WINDOW = 5
FLOW_SELLING_CR = -5000.0  # BRD §7.4 (strictly below)
FLOW_BUYING_CR = 5000.0    # the ticket's documented symmetric completion

VIX_PANIC = 24.0           # BRD §7.3 / §10.3 #6: strictly above
VIX_ELEVATED_FLOOR = 18.0
VIX_NORMAL_FLOOR = 12.0
BREADTH_PANIC = 30.0       # strictly below
RISK_ON_FLOOR = 65.0       # strictly above (BRD §7.2); NEUTRAL is 40–65 inclusive
RISK_OFF_CEIL = 40.0

PAYLOAD_KEYS = [
    "sector_index", "sector_mrs", "sector_mrs_mom", "sector_rrg_state",
    "nifty_trend", "market_breadth_pct", "breadth_regime", "vix_value",
    "vix_regime", "fii_dii_flow_bias", "panic_regime_breaker",
    "waived", "as_of", "missing_inputs",
]


def die(msg: str, exit_code: int = 1) -> None:
    print(f"{ERR_PARSE}: {msg}", file=sys.stderr)
    sys.exit(exit_code)


def load_payload(path: str) -> dict:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        die(f"input file not found: {path}")
    except json.JSONDecodeError as exc:
        die(f"input is not valid JSON: {exc}")
    if not isinstance(payload, dict):
        die("input must be a JSON object")
    missing = [k for k in REQUIRED_FIELDS if k not in payload]
    if missing:
        die(f"missing required field(s): {', '.join(missing)}")
    return payload


def load_context(path: str | None):
    """Read the evidence bundle → (context, fixture_reason).

    Exactly one of the two is set: an unreadable/mis-shaped bundle is a data
    gap to disclose, never a traceback (the stage-2/3/5 waiver convention).
    """
    if not path:
        return None, "fixture:not-provided"
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, "fixture:unreadable"
    if not isinstance(raw, dict):
        return None, "fixture:unreadable"
    if raw.get("schema") != SCHEMA:
        return None, "fixture:schema"
    return raw, None


def mrs_moments(ratio: list[float]) -> tuple[float, float]:
    """(MRS level, 5-session momentum) of a sector/NIFTY ratio series.

    MRS_t = (r_t / SMA50(r)_t - 1) * 100; momentum = MRS_t - MRS_{t-5}
    (percentage points). Needs >= 55 sessions: 50 for the SMA, 5 more so the
    earlier moment exists. Raises ValueError below that — never a guess.
    """
    if len(ratio) < MRS_SMA + MOMENTUM_LAG:
        raise ValueError(f"ratio series shorter than {MRS_SMA + MOMENTUM_LAG}")
    sma_now = sum(ratio[-MRS_SMA:]) / MRS_SMA
    level = (ratio[-1] / sma_now - 1.0) * 100.0
    sma_prev = sum(ratio[-(MRS_SMA + MOMENTUM_LAG):-MOMENTUM_LAG]) / MRS_SMA
    prev = (ratio[-1 - MOMENTUM_LAG] / sma_prev - 1.0) * 100.0
    return level, level - prev


def rrg_state(level: float, momentum: float) -> str:
    """RRG quadrant from the two axes; zero counts as the positive side."""
    if level >= 0.0:
        return "LEADING" if momentum >= 0.0 else "WEAKENING"
    return "IMPROVING" if momentum >= 0.0 else "LAGGING"


def classify_vix(value: float) -> str:
    """LOW < 12; NORMAL [12, 18); ELEVATED [18, 24]; PANIC > 24 (TICK-007)."""
    if value < VIX_NORMAL_FLOOR:
        return "LOW"
    if value < VIX_ELEVATED_FLOOR:
        return "NORMAL"
    if value <= VIX_PANIC:
        return "ELEVATED"
    return "PANIC"


def classify_breadth(value: float) -> str:
    """RISK_ON > 65; NEUTRAL 40–65 inclusive; RISK_OFF < 40 (BRD §7.2)."""
    if value > RISK_ON_FLOOR:
        return "RISK_ON"
    if value < RISK_OFF_CEIL:
        return "RISK_OFF"
    return "NEUTRAL"


def classify_flow(total: float) -> str:
    """5-day combined net: < -5000 NET_SELLING; > +5000 NET_BUYING (BRD §7.4)."""
    if total < FLOW_SELLING_CR:
        return "NET_SELLING"
    if total > FLOW_BUYING_CR:
        return "NET_BUYING"
    return "NEUTRAL"


def classify_nifty_trend(closes: list[float]) -> str:
    """Frozen BRD §7.2 tokens: 200-DMA test first; equal counts as above."""
    if len(closes) < 200:
        raise ValueError("nifty_trend needs >= 200 sessions")
    close = closes[-1]
    sma200 = sum(closes[-200:]) / 200.0
    if close < sma200:
        return "BELOW_200_DMA"
    sma50 = sum(closes[-50:]) / 50.0
    if close < sma50:
        return "BELOW_50_ABOVE_200"
    return "ABOVE_50_200_DMA"


def panic_breaker(vix_value: float, breadth_pct: float) -> bool:
    """BRD §10.3 #6 — strict on both arms: > 24.0 AND < 30.0."""
    return vix_value > VIX_PANIC and breadth_pct < BREADTH_PANIC


def _points(series: dict) -> list[tuple[str, float]]:
    """(date, float value) rows of one series; ValueError on any non-number."""
    rows = []
    for point in series.get("values", []):
        value = point["value"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"non-numeric value at {point.get('date')!r}")
        rows.append((str(point["date"]), float(value)))
    return rows


def _waived_payload(reasons: list[str]) -> dict:
    payload = {key: None for key in PAYLOAD_KEYS}
    payload["waived"] = True
    payload["missing_inputs"] = reasons
    return payload


def compute_regime(payload: dict, context: dict | None,
                   fixture_reason: str | None) -> dict:
    symbol = str(payload.get("symbol") or "").strip().upper()
    sector_ticker = SECTOR_INDEX_MAP.get(symbol)

    reasons: list[str] = []
    if fixture_reason:
        reasons.append(fixture_reason)
    if sector_ticker is None:
        reasons.append(f"symbol:unmapped:{symbol}")

    series = context.get("series") if context is not None else None
    if context is not None and not isinstance(series, dict):
        return _waived_payload(["fixture:unreadable"])

    # Presence, in the frozen order (the sector slot is checked only when the
    # symbol resolved to a ticker — unmapped already carries its own reason).
    wanted = [NSEI, sector_ticker, INDIA_VIX, BREADTH, FLOW] if sector_ticker \
        else [NSEI, INDIA_VIX, BREADTH, FLOW]
    present: dict[str, dict] = {}
    if context is not None:
        for key in wanted:
            entry = series.get(key)
            if not isinstance(entry, dict) or not isinstance(entry.get("values"), list):
                reasons.append(f"series:{key}:absent")
            else:
                present[key] = entry

    # One date axis across the index series (only among present ones).
    axes = [(key, [p.get("date") for p in present[key]["values"]])
            for key in (NSEI, sector_ticker, INDIA_VIX) if key in present]
    if axes and any(dates != axes[0][1] for _, dates in axes[1:]):
        reasons.append("index-axis:misaligned")

    # Per-series minimums, same order as presence.
    for key in wanted:
        if key not in present:
            continue
        required = MIN_SECTOR_SESSIONS if key == sector_ticker \
            else MIN_SESSIONS[key]
        if len(present[key]["values"]) < required:
            reasons.append(f"series:{key}:short")

    if reasons:
        return _waived_payload(reasons)

    try:
        nifty = _points(present[NSEI])
        sector = _points(present[sector_ticker])
        vix = _points(present[INDIA_VIX])
        breadth = _points(present[BREADTH])
        flow = _points(present[FLOW])
        ratio = [s / n for (_d, n), (_d2, s) in zip(nifty, sector)]
        level, momentum = mrs_moments(ratio)
        level_r = round(level, 4)
        mom_r = round(momentum, 4)
        vix_value = round(vix[-1][1], 2)
        breadth_pct = round(breadth[-1][1], 2)
        flow_total = round(sum(v for _d, v in flow[-FLOW_WINDOW:]), 2)
        nifty_trend = classify_nifty_trend([v for _d, v in nifty])
    except (ValueError, ZeroDivisionError, IndexError):
        # A bundle that does not parse as evidence is a data gap, not a crash.
        return _waived_payload(["fixture:unreadable"])

    return {
        "sector_index": sector_ticker,
        "sector_mrs": level_r,
        "sector_mrs_mom": mom_r,
        "sector_rrg_state": rrg_state(level_r, mom_r),
        "nifty_trend": nifty_trend,
        "market_breadth_pct": breadth_pct,
        "breadth_regime": classify_breadth(breadth_pct),
        "vix_value": vix_value,
        "vix_regime": classify_vix(vix_value),
        "fii_dii_flow_bias": classify_flow(flow_total),
        "panic_regime_breaker": panic_breaker(vix_value, breadth_pct),
        "waived": False,
        "as_of": nifty[-1][0],
        "missing_inputs": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="UVEST Stage 7: Sector Relative Strength & Macro VIX Regime")
    parser.add_argument("--input", default="current_trade.json",
                        help="Path to canonical current_trade.json")
    parser.add_argument("--regime-fixture", default=None,
                        help="Recorded uvest-market-context/1 bundle JSON "
                             "(omit for the waiver path)")
    add_record_arg(parser)

    args = parser.parse_args()

    payload = load_payload(args.input)
    context, fixture_reason = load_context(args.regime_fixture)
    result = compute_regime(payload, context, fixture_reason)

    output_str = json.dumps(result, indent=2) + "\n"

    bundle = resolve_bundle_dir(args.record, payload.get("symbol") or "UNSPECIFIED",
                                payload.get("recommended_date"))
    if bundle is not None:
        record_artifact(bundle, 7, output_str)

    if result["waived"]:
        print(f"[waived] stage 7: {', '.join(result['missing_inputs'])}",
              file=sys.stderr)

    sys.stdout.write(output_str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
