#!/usr/bin/env python3
"""UVEST stage 0 — Canonical Ingestion Parser & Gatekeeper (TICK-001).

Parses a raw Univest recommendation paste into the canonical `current_trade.json`
(BRD §3.2) and enforces three structural gates before any quantitative script
runs (BRD §3.3):

1. Options prohibition (ADR-001): CE/PE/OPT terminate instantly, exit 2,
   stderr `ERR_OPTIONS_PROHIBITED`. The fatal token is detected wherever a
   paste can declare an instrument (`Type:` and `Symbol:`) after Unicode
   look-alikes are folded and invisible characters stripped, so a cash label
   over an option-shaped payload is refused too (fail closed).
   The instrument domain is **closed** (ADR-001: "only equities and futures
   are permitted"): anything the gate cannot recognize as `CASH` or `FUT`
   leaves with `ERR_INSTRUMENT_UNRECOGNIZED`, exit 2 — a homoglyph option
   written with Cyrillic letters is not the token `CE`, so it must not be
   admitted as an unknown instrument that downstream stages would treat as
   cash.
2. NSE F&O MWPL ban check for Futures — mock list offline (BRD Tier-1/scrape
   fills it in production; the fixture contract keeps the seam deterministic).
3. SEBI ASM/GSM Stage 2+ surveillance flag — same offline-mock policy.

Wrapping the parser in a script (not a library module) keeps the pipeline
contract honest: stage N is invoked as `python scripts/N_*.py`, never imported.

A paste that omits `Recommended Date:` is refused here as well (see
`require_recommended_date`): stage 12 cannot name its report without it, and
the refusal has to come cheap - before the chain exists, not at its last stage.

Output: canonical JSON to stdout. Errors: stderr + non-zero exit, never stdout.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path

# Shared --record/--replay bundle helper lives next door; importable because
# scripts/ is on sys.path when the stage script runs from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _recorder import add_record_arg, record_artifact, resolve_bundle_dir  # noqa: E402

# Frozen error codes (BRD §3.3, TICK-001 §3)
ERR_OPTIONS_PROHIBITED = "ERR_OPTIONS_PROHIBITED"
ERR_INSTRUMENT_UNRECOGNIZED = "ERR_INSTRUMENT_UNRECOGNIZED"
ERR_PARSE = "ERR_PARSE"

# BRD ADR-001 (docs/agents/domain.md): "only Equities and Futures are
# permitted" — so the instrument domain is *closed* and its vocabulary is the
# one downstream stages actually implement (`CASH` and `FUT`; stages 1/5/9 key
# off `FUT` alone). `FUTURES` is deliberately not an alias: accepting the long
# form would hand those stages a futures trade they read as cash. An
# instrument the gate cannot recognize — an invented label, or an option
# written with look-alike letters (`СЕ`) that is not the ASCII token — is
# refused rather than assumed to be an equity.
RECOGNIZED_INSTRUMENTS = ("CASH", "FUT")

# BRD ADR-001 — instant hard reject at ingestion.
OPTIONS_TYPES = {"CE", "PE", "OPT", "OPTION", "CALL", "PUT"}

# Invisible format characters (zero-width space/joiner, word joiner, BOM, soft
# hyphen) are deleted before parsing: `C\u200bE` is the string "CE" to anyone
# reading the pasted card, and ADR-001 must see what the human sees.
_INVISIBLE = dict.fromkeys(ord(ch) for ch in "\u200b\u200c\u200d\u2060\ufeff\u00ad")

# A paste declares its instrument in `Type:` (the canonical `instrument_type`)
# and, when the instrument is an option, in `Symbol:` too ("NIFTY 25000 CE").
# Tokens split on every non-alphanumeric character, so `CE`, `(CE)` and
# `CASH (CE)` all carry the token `CE`, while `CENTURYPLY` carries none.
_TOKEN_RE = re.compile(r"[A-Z0-9]+")


def normalize_paste(raw: str) -> str:
    """Fold Unicode look-alikes and drop invisibles before any field parsing.

    NFKC turns fullwidth punctuation and letters (`Type：ＣＥ`) into their ASCII
    form, so a paste cannot smuggle a fatal token past the parser with a
    look-alike separator, and `_INVISIBLE` removes the zero-width characters
    that could hide one inside the letters of a token.
    """
    return unicodedata.normalize("NFKC", raw).translate(_INVISIBLE)


def instrument_tokens(text: str) -> set[str]:
    """The instrument tokens a paste's declaration site carries."""
    return set(_TOKEN_RE.findall(text.upper()))


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


# BRD 3.2 carries `recommended_date` in the canonical schema, and stage 12
# builds its artifact name from it: `report_{SYMBOL}_{DATE}.html` (TICK-010),
# which is also how the eval harness finds the report. A date the calendar
# does not have (`2026-02-30`) or a human-format one (`09/10/2026`) is as fatal
# as no date at all - the report path would not even be a path.
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def require_recommended_date(payload: dict) -> None:
    """Refuse a trade stage 12 could not render a report for (BRD 13).

    Called *after* the ADR-001 gates so a policy refusal always keeps its own
    verdict (an option card with no date is still an option refusal), and
    before any artifact exists: here the refusal is free, at stage 12 it has
    cost eleven written files and a half-graded run.
    """
    value = payload.get("recommended_date")
    if not value:
        die(ERR_PARSE, 1,
            "missing required field: recommended_date - stage 12 names "
            "reports/report_{SYMBOL}_{DATE}.html from it")
    text = str(value).strip()
    if not ISO_DATE_RE.match(text):
        die(ERR_PARSE, 1,
            f"recommended_date={value!r} is not an ISO date (YYYY-MM-DD)")
    try:
        date.fromisoformat(text)
    except ValueError:
        die(ERR_PARSE, 1,
            f"recommended_date={value!r} is not a real calendar date")


def gatekeeper_checks(payload: dict) -> dict:
    """BRD §3.3 gates 2+3: F&O ban and ASM/GSM flags (offline mock lists)."""
    symbol = payload["symbol"]
    # The closed domain above leaves exactly two possibilities here, so this is
    # an equality rather than a vocabulary list that could drift from it.
    is_futures = payload["instrument_type"] == "FUT"

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

    raw = normalize_paste(Path(args.input).read_text(encoding="utf-8")).strip()
    if not raw:
        die(ERR_PARSE, 1, "input file is empty")

    payload = parse_plain_text(raw)

    # ADR-001 gate 1 — hard reject BEFORE any downstream stage exists. The
    # declaration sites are the `Type:` value and the `Symbol:` value: a card
    # that labels itself `CASH` while its symbol reads "NIFTY 25000 CE" is an
    # option recommendation wearing a cash label, and a safety gate fails
    # closed on that ambiguity instead of trusting the label. Token comparison
    # (not string equality) is what makes `CASH (CE)` fatal too.
    fatal = (instrument_tokens(payload["instrument_type"])
             | instrument_tokens(str(payload.get("symbol") or ""))) & OPTIONS_TYPES
    if fatal:
        die(ERR_OPTIONS_PROHIBITED, 2,
            f"option token(s) {', '.join(sorted(fatal))} declared in "
            f"instrument_type={payload['instrument_type']!r} / "
            f"symbol={payload['symbol']!r}")

    # ADR-001's other half — the domain is closed. Checked *after* the option
    # tokens so an actual CE/PE/OPT paste is always reported as what it is,
    # never as merely "unrecognized": the distinction is the difference between
    # a policy refusal and a paste the parser could not classify.
    if payload["instrument_type"] not in RECOGNIZED_INSTRUMENTS:
        # ASCII only: this line is written to whatever stderr the console
        # gives the stage (a legacy Windows codepage included), and a
        # non-ASCII glyph here would either be mangled to mojibake or, worse,
        # raise while formatting the refusal. The value is rendered with `!r`
        # for the same reason — a Cyrillic `Type:` escapes to ASCII.
        die(ERR_INSTRUMENT_UNRECOGNIZED, 2,
            f"instrument_type={payload['instrument_type']!r} is not one of "
            f"{', '.join(RECOGNIZED_INSTRUMENTS)}; ADR-001 permits equities "
            f"and futures only")

    # The field stage 12 builds its artifact name from. Refused here, where the
    # chain has not started yet, instead of at its last stage, where eleven
    # files are already on disk. After the ADR-001 gates so a policy refusal
    # still owns its own verdict.
    require_recommended_date(payload)

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
