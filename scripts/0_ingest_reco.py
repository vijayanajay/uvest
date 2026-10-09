#!/usr/bin/env python3
"""UVEST stage 0 — Canonical Ingestion Parser & Gatekeeper (TICK-001).

Parses a raw Univest recommendation paste into the canonical `current_trade.json`
(BRD §3.2) and enforces three structural gates before any quantitative script
runs (BRD §3.3):

1. Options prohibition (ADR-001): CE/PE/OPT terminate instantly, exit 2,
   stderr `ERR_OPTIONS_PROHIBITED`.
2. NSE F&O MWPL ban check for Futures — mock list offline (BRD Tier-1/scrape
   fills it in production; the fixture contract keeps the seam deterministic).
3. SEBI ASM/GSM Stage 2+ surveillance flag — same offline-mock policy.

Wrapping the parser in a script (not a library module) keeps the pipeline
contract honest: stage N is invoked as `python scripts/N_*.py`, never imported.

Output: canonical JSON to stdout. Errors: stderr + non-zero exit, never stdout.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Shared --record/--replay bundle helper lives next door; importable because
# scripts/ is on sys.path when the stage script runs from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _recorder import add_record_arg, record_artifact, resolve_bundle_dir  # noqa: E402

# Frozen error codes (BRD §3.3, TICK-001 §3)
ERR_OPTIONS_PROHIBITED = "ERR_OPTIONS_PROHIBITED"
ERR_PARSE = "ERR_PARSE"

# BRD ADR-001 — instant hard reject at ingestion.
OPTIONS_TYPES = {"CE", "PE", "OPT", "OPTION", "CALL", "PUT"}

# Mock MWPL ban / surveillance lists (deterministic offline seam; production
# fills these from the NSE Bhavcopy fo_bhav.csv + SEBI ASM/GSM scrapes).
MOCK_FO_BAN_LIST = {"TESTBANNED", "BSE", "IDEA", "GUJAMBUJA"}
MOCK_ASM_GSM_LIST = {"TESTASM2", "AMBER", "YESBANK"}

# Keys exactly as they appear in a raw paste (`Entry Range:` is split further
# down; the canonical schema's *_min/*_max are derived, not required up front).
CANONICAL_REQUIRED = ["symbol", "instrument_type", "live_price", "target", "stoploss"]

# Known lot sizes for common NSE F&O contracts (TICK-001 gate: validate lots).
KNOWN_LOT_SIZES = {250: "RELIANCE", 550: "TATASTEEL", 750: "SBIN", 1200: "ITC"}


def die(code: str, exit_code: int, hint: str = "") -> "NoReturn":  # type: ignore[name-defined]
    print(f"{code}: {hint}" if hint else code, file=sys.stderr)
    sys.exit(exit_code)


def parse_plain_text(raw: str) -> dict:
    """Extract the universal Univest field set from copypasted notification text.

    Accepts `Key: value` lines in any order, allows ₹/₹₹/commas, and tolerates
    the letter case Univest notifications actually use.
    """
    fields: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower().replace(" ", "_")
        # Univest cards use `Type:`; the canonical schema key is `instrument_type`.
        if key == "type":
            key = "instrument_type"
        fields[key] = value.strip().replace("₹", "").replace(",", "").strip()

    missing = [k for k in CANONICAL_REQUIRED if not fields.get(k)]
    # The `entry_range_min`/`entry_range_max` pair arrives as a combined
    # `Entry Range:` line; the split happens below, after the presence check.
    if not fields.get("entry_range"):
        missing.append("entry_range")
    if missing:
        die(ERR_PARSE, 1, f"missing required fields: {', '.join(missing)}")

    def as_float(text: str, name: str) -> float:
        try:
            return float(text)
        except ValueError as exc:
            die(ERR_PARSE, 1, f"{name}={text!r} is not a number")

    entry_range_raw = fields.get("entry_range")
    m = re.match(r"^([\d.]+)\s*-\s*([\d.]+)$", entry_range_raw or "")
    if not m:
        die(ERR_PARSE, 1, f"entry_range={entry_range_raw!r} does not match 'min-max'")

    instrument_type = fields.get("instrument_type", "").upper()

    lot_size: int | None = None
    if fields.get("lot_size"):
        try:
            lot_size = int(float(fields["lot_size"]))
        except ValueError as exc:
            die(ERR_PARSE, 1, f"lot_size={fields['lot_size']!r} is not an integer")

    symbol = fields["symbol"].upper()
    action = "BUY"  # Univest advisory cards in v1 are long-only recommendations

    result = {
        "symbol": symbol,
        "exchange": "NSE",
        "instrument_type": instrument_type,
        "action": action,
        "entry_price": (as_float(m.group(1), "entry_price") + as_float(m.group(2), "entry_price")) / 2.0,
        "entry_range_min": as_float(m.group(1), "entry_range_min"),
        "entry_range_max": as_float(m.group(2), "entry_range_max"),
        "live_price": as_float(fields["live_price"], "live_price"),
        "target": as_float(fields["target"], "target"),
        "stoploss": as_float(fields["stoploss"], "stoploss"),
        "lot_size": lot_size,
        "source": fields.get("source", "univest"),
        "app_warning": fields.get("app_warning"),
        "recommended_date": fields.get("recommended_date"),
    }
    if lot_size is not None:
        result["lot_size"] = lot_size
    if fields.get("expiry"):
        result["expiry"] = fields["expiry"]
    if fields.get("margin"):
        result["margin"] = float(fields["margin"])
    return result


def gatekeeper_checks(payload: dict) -> dict:
    """BRD §3.3 gates 2+3: F&O ban and ASM/GSM flags (offline mock lists)."""
    symbol = payload["symbol"]
    is_futures = payload["instrument_type"] in ("FUT", "FUTURES")

    # Stage 0 never rejects on ban/ASM; it *flags* for the decision engine
    # (the score-0 gate enforcement belongs to stage 9, per BRD §10.2).
    ban = symbol in MOCK_FO_BAN_LIST
    asm = symbol in MOCK_ASM_GSM_LIST

    margin_exposure = None
    if is_futures and payload.get("lot_size"):
        # Conservative floor: full notional at live price (no leverage credit).
        margin_exposure = round(payload["lot_size"] * payload["live_price"], 2)
    elif payload["instrument_type"] == "CASH":
        margin_exposure = None  # cash equities have no lot-based notional flag

    return {
        "ban_status": bool(ban),
        "asm_gsm_flagged": bool(asm),
        "margin_exposure_inr": margin_exposure,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 0: parse recommendation into current_trade.json")
    parser.add_argument("--input", required=True, help="path to raw recommendation text file")
    add_record_arg(parser)  # shared --record flag, from scripts/_recorder.py
    args = parser.parse_args()

    raw = Path(args.input).read_text(encoding="utf-8").strip()
    if not raw:
        die(ERR_PARSE, 1, "input file is empty")

    payload = parse_plain_text(raw)

    # ADR-001 gate 1 — hard reject BEFORE any downstream stage exists.
    if payload["instrument_type"] in OPTIONS_TYPES:
        die(ERR_OPTIONS_PROHIBITED, 2, f"instrument_type={payload['instrument_type']}")

    payload["gatekeeper"] = gatekeeper_checks(payload)

    # ONE serialisation, written to BOTH stdout and the bundle — the recorded
    # artifact is byte-identical to what the pipeline actually emits.
    stdout_text = json.dumps(payload, indent=2) + "\n"

    bundle = resolve_bundle_dir(args.record, payload["symbol"],
                                payload.get("recommended_date"))
    if bundle is not None:
        record_artifact(bundle, 0, stdout_text)

    sys.stdout.write(stdout_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
