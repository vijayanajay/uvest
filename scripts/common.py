"""Shared helpers for the trade validation pipeline.

All paths are resolved relative to the project root so every script
works identically no matter which directory it is invoked from.
"""

from __future__ import annotations

import calendar
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Files the pipeline reads/writes, all at the project root.
INPUT_FILE = PROJECT_ROOT / "current_trade.json"
OUT1_FILE = PROJECT_ROOT / "script1_out.json"
OUT2_FILE = PROJECT_ROOT / "script2_out.json"
OUT3_FILE = PROJECT_ROOT / "script3_out.json"
OUT4_FILE = PROJECT_ROOT / "script4_out.json"
OUT5_FILE = PROJECT_ROOT / "script5_out.json"
OUT6_FILE = PROJECT_ROOT / "script6_out.json"
DB_FILE = PROJECT_ROOT / "univest_history.db"
REPORT_FILE = PROJECT_ROOT / "decision_report.md"


def load_json(path: Path) -> dict[str, Any]:
    """Load a JSON file into a dict. Raises on missing/invalid input."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict[str, Any]) -> None:
    """Write a dict as pretty JSON to a file, creating parent dirs."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def emit_json(data: dict[str, Any]) -> None:
    """Print a dict as JSON to stdout — the machine-readable contract
    every script in this suite honours."""
    # Windows consoles default to cp1252; the JSON contract may carry
    # non-ASCII text (₹, ≥, → …), so force UTF-8 on stdout. Idempotent.
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print(json.dumps(data, indent=2, ensure_ascii=False))


def fail(message: str) -> None:
    """Emit a JSON error payload and exit non-zero."""
    emit_json({"error": message})
    sys.exit(1)


def indian_currency(value: float) -> str:
    """Format a number as Indian-rupee currency, e.g. 26647.5 -> ₹26,647.50."""
    return f"₹{value:,.2f}"


# ---- Expiry parsing -------------------------------------------------------
# Univest supplies expiry as a label like "25 AUG" (occasionally with a year
# or in ISO form). Parsing it into a real date lets the pipeline do two honest
# things: (1) score event risk by days-to-expiry, and (2) resolve OPEN
# recommendations that never hit target or stoploss once expiry passes.

_MONTHS = {m: i for i, m in enumerate([
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
], start=1)}


def parse_expiry(expiry_text: str | None, today: date | None = None) -> date | None:
    """Parse a Univest expiry label into a date.

    Accepted forms: "25 AUG", "25AUG", "25 AUG 2026", "25-AUG-2026",
    "2026-08-25". A missing year means the current year; if the resulting
    date is already in the past it rolls to the next year. Returns None when
    the text cannot be parsed (the caller treats that as "expiry unknown" and
    waives the event-safety check rather than guessing).
    """
    if not expiry_text:
        return None
    today = today or date.today()
    text = str(expiry_text).strip().upper().replace("/", "-")

    m = re.match(r"^(\d{1,2})[\s-]*([A-Z]{3,9})(?:[\s-]+(\d{4}))?$", text)
    if m:
        day = int(m.group(1))
        month_num = _MONTHS.get(m.group(2)[:3])
        if not month_num:
            return None
        year = int(m.group(3)) if m.group(3) else today.year
        try:
            parsed = date(year, month_num, day)
        except ValueError:
            return None
        if not m.group(3) and parsed < today:
            # No year given and the date has passed — assume next year.
            year += 1
            parsed = date(year, month_num, min(day, calendar.monthrange(year, month_num)[1]))
        return parsed

    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None

    return None


def days_to_expiry(expiry_text: str | None, today: date | None = None) -> tuple[date | None, int | None]:
    """Return (expiry_date, days_remaining) or (None, None) when unparsable."""
    parsed = parse_expiry(expiry_text, today)
    if parsed is None:
        return None, None
    return parsed, (parsed - (today or date.today())).days


def flip_prices(sl: float, target: float, range_min: float | None = None, range_max: float | None = None) -> dict[str, Any]:
    """Price thresholds that flip the price-dependent checks of a reco.

    The price P at which live (reward)/(risk) equals r is the same formula
    for BUY and SELL: P = (target + r*sl) / (1 + r). A BUY improves at or
    below P (buy lower -> more reward, less risk); a SELL improves at or
    above P. Callers apply the direction.
    """
    return {
        "rr_1_5_price": round((target + 1.5 * sl) / 2.5, 2),
        "rr_1_0_price": round((target + sl) / 2.0, 2),
        "range_min": range_min,
        "range_max": range_max,
    }


# ---- Transaction-cost model ------------------------------------------------
# Resolution P&L must not flatter itself: brokerage, STT, exchange charges and
# GST are real. This is a deliberately simple, conservative percentage-of-
# notional model (notional = entry price x lot size) — good enough to keep the
# tracked record honest, tunable in one place.
#   FUT: STT 0.0125% (sell) + exchange ~0.002% x2 + brokerage ~0.03% x2 + GST
#       -> ~0.09% of notional round-trip.
#   OPT: STT 0.0625% (sell) + exchange ~0.035% x2 + brokerage ~0.03% x2 + GST
#       -> ~0.25% of notional (premium) round-trip.

COST_RATE_FUT = 0.0009   # ~0.09% of notional, round-trip
COST_RATE_OPT = 0.0025   # ~0.25% of notional, round-trip


def estimate_costs(entry_price: float, lot_size: float, instrument_type: str | None) -> float:
    """Model transaction costs for one trade, in INR."""
    if not lot_size or lot_size <= 0:
        return 0.0
    notional = entry_price * lot_size
    rate = COST_RATE_OPT if (instrument_type or "").upper() == "OPT" else COST_RATE_FUT
    return round(notional * rate, 2)
