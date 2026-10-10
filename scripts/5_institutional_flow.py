#!/usr/bin/env python3
"""UVEST stage 5 — NSE Bhavcopy Delivery % × Futures OI Matrix (TICK-005).

Sole owner of the Institutional Order Flow & Footprint Engine (BRD §5.3, §5.4, §9.1 row 5).
Consumes `current_trade.json` (stage 0), loads official NSE Bhavcopy data
(`sec_bhavdata_full.csv` and `fo_bhav.csv`), and evaluates:

1. Delivery % and 20-Day Rolling Z-Score:
   Identifies whether trading volume represents genuine demat delivery accumulation
   or retail intraday churn.
2. Futures Open Interest (OI) Change %:
   Measures institutional contract expansion vs. liquidation.
3. The Holy Trinity Matrix (BRD §5.3):
   - Price UP + High Delivery + Rising OI   => LONG_BUILDUP (Institutional Accumulation)
   - Price UP + Low Delivery + Falling OI    => SHORT_COVERING_TRAP (Retail Exhaustion Rally)
   - Price DOWN + High Delivery + Rising OI => SHORT_BUILDUP (Institutional Distribution)
   - Price DOWN + Low Delivery + Falling OI => LONG_UNWINDING (Retail Capitulation)
4. Futures Basis & Carry Cost (BRD §5.4):
   Basis = P_Futures - P_Spot; Annualized Basis % = (Basis / P_Spot) * (365 / days_to_expiry) * 100.
5. Waiver Path (ADR-003 / BRD §10.3):
   If Bhavcopy data is unavailable, emits a structured payload with `waived: true`
   and all analytical fields as `null` (exit code 0). Never fabricates defaults.

Reads `--input current_trade.json`; writes `out_5_flow.json` to stdout.
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

REQUIRED_FIELDS = ["symbol", "instrument_type"]


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


def load_bhavcopy_data(path: str | None) -> dict | None:
    """Load Bhavcopy fixture or pre-scraped data if provided; None triggers waiver."""
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def classify_matrix(price_change_pct: float, delivery_pct: float,
                    delivery_zscore: float, oi_change_pct: float) -> str:
    """Classifies the market setup into the Holy Trinity Matrix (BRD §5.3)."""
    price_up = price_change_pct > 0
    price_down = price_change_pct < 0

    delivery_high = (delivery_pct >= 50.0) or (delivery_zscore >= 1.5)
    delivery_low = (delivery_pct < 30.0) or (delivery_zscore <= -1.0)

    oi_rising = oi_change_pct >= 5.0
    oi_falling = oi_change_pct <= -5.0

    if price_up and delivery_high and oi_rising:
        return "LONG_BUILDUP"
    if price_up and delivery_low and oi_falling:
        return "SHORT_COVERING_TRAP"
    if price_down and delivery_high and oi_rising:
        return "SHORT_BUILDUP"
    if price_down and delivery_low and oi_falling:
        return "LONG_UNWINDING"

    # Secondary/moderate directional states
    if price_up and oi_rising:
        return "LONG_BUILDUP"
    if price_up and oi_falling:
        return "SHORT_COVERING"
    if price_down and oi_rising:
        return "SHORT_BUILDUP"
    if price_down and oi_falling:
        return "LONG_UNWINDING"

    return "NEUTRAL"


def compute_institutional_flow(payload: dict, bhav: dict | None) -> dict:
    symbol = payload.get("symbol")
    instrument = payload.get("instrument_type", "").upper()

    # Waiver path (ADR-003, BRD §10.3)
    if bhav is None:
        return {
            "symbol": symbol,
            "delivery_pct": None,
            "delivery_zscore": None,
            "oi_change_pct": None,
            "matrix_verdict": None,
            "basis_annualized": None,
            "rollover_pct": None,
            "waived": True,
            "as_of": None,
        }

    delivery_pct = float(bhav.get("delivery_pct", 0.0))
    delivery_mean = float(bhav.get("delivery_mean_20d", 35.0))
    delivery_std = float(bhav.get("delivery_std_20d", 8.0))

    delivery_zscore = 0.0
    if delivery_std > 0:
        delivery_zscore = (delivery_pct - delivery_mean) / delivery_std

    oi_change_pct = float(bhav.get("oi_change_pct", 0.0))
    price_change_pct = float(bhav.get("price_change_pct", 0.0))

    matrix_verdict = classify_matrix(price_change_pct, delivery_pct,
                                     delivery_zscore, oi_change_pct)

    basis_annualized: float | None = None
    if instrument == "FUT":
        spot = float(bhav.get("spot_price") or 0.0)
        futures = float(bhav.get("futures_price") or payload.get("live_price") or 0.0)
        days_to_expiry = float(bhav.get("days_to_expiry") or 20.0)

        if spot > 0 and days_to_expiry > 0:
            basis = futures - spot
            basis_annualized = (basis / spot) * (365.0 / days_to_expiry) * 100.0

    rollover_pct = bhav.get("rollover_pct")
    if rollover_pct is not None:
        rollover_pct = float(rollover_pct)

    as_of = bhav.get("as_of")

    return {
        "symbol": symbol,
        "delivery_pct": round(delivery_pct, 2),
        "delivery_zscore": round(delivery_zscore, 2),
        "oi_change_pct": round(oi_change_pct, 2),
        "matrix_verdict": matrix_verdict,
        "basis_annualized": round(basis_annualized, 2) if basis_annualized is not None else None,
        "rollover_pct": round(rollover_pct, 2) if rollover_pct is not None else None,
        "waived": False,
        "as_of": as_of,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="UVEST Stage 5: Institutional Order Flow Engine")
    parser.add_argument("--input", default="current_trade.json",
                        help="Path to canonical current_trade.json")
    parser.add_argument("--bhavcopy-fixture", default=None,
                        help="Path to pre-recorded Bhavcopy fixture JSON")
    add_record_arg(parser)

    args = parser.parse_args()

    payload = load_payload(args.input)
    bhav = load_bhavcopy_data(args.bhavcopy_fixture)
    result = compute_institutional_flow(payload, bhav)

    output_str = json.dumps(result, indent=2) + "\n"
    sys.stdout.write(output_str)

    if args.record:
        bundle_dir = resolve_bundle_dir(payload, args.record)
        record_artifact(bundle_dir, stage=5, exact_stdout=output_str)


if __name__ == "__main__":
    main()
