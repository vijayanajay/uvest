"""Screen 1: Universe — the NSE liquid-stock list.

Fetches the official NSE archive CSVs (NIFTY 100 + NEXT 50 constituents,
which together are the ~150 most liquid names) plus the F&O lot-size list
for shortability. If NSE is unreachable — it often is from scripts — it
falls back to the vendored snapshot `scripts/universe_nse150.csv` so the
daily screen never dies to a network block (archive the data, refresh
with a flag: Kailash-style).

Output contract (screen_universe_out.json):
    source        : "nse" | "vendored"
    generated_on  : ISO date
    count         : number of symbols
    universe      : [{"symbol", "name"}]
    shortable     : [symbols] from the F&O list (best-effort)

Usage:
    python scripts/screen_universe.py
"""

from __future__ import annotations

import csv
import io
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import PROJECT_ROOT, emit_json  # noqa: E402

UNIVERSE_FILE = PROJECT_ROOT / "scripts" / "universe_nse150.csv"

NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "text/csv,application/csv,*/*",
    "Accept-Language": "en-IN,en;q=0.9",
}

NIFTY100_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty100list.csv"
NIFTY_NEXT50_URL = "https://nsearchives.nseindia.com/content/indices/ind_niftynext50list.csv"
FO_MKTLOTS_URL = "https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv"

MAX_SYMBOLS = 150


def fetch_csv(url: str, timeout: float = 15.0) -> list[dict[str, str]] | None:
    """Download an NSE archive CSV. Returns rows or None on any failure."""
    try:
        req = urllib.request.Request(url, headers=NSE_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8-sig", errors="replace")
        return list(csv.DictReader(io.StringIO(text)))
    except Exception:
        return None


def fetch_nse_universe() -> tuple[str, list[dict[str, str]], list[str]]:
    """NIFTY 100 + NEXT 50 constituents (the ~150 most liquid), deduped.

    Returns (source, universe, shortable_symbols). If both index lists
    fail, the caller falls back to the vendored snapshot.
    """
    seen: dict[str, str] = {}
    for url in (NIFTY100_URL, NIFTY_NEXT50_URL):
        rows = fetch_csv(url)
        if not rows:
            continue
        for r in rows:
            sym = (r.get("Symbol") or "").strip().upper()
            if sym and sym != "SYMBOL":
                seen.setdefault(sym, (r.get("Company Name") or sym).strip())

    if not seen:
        return "vendored", [], []

    shortable: list[str] = []
    fo = fetch_csv(FO_MKTLOTS_URL)
    if fo:
        for r in fo:
            sym = (r.get("Symbol") or "").strip().upper()
            if sym:
                shortable.append(sym)

    universe = [{"symbol": s, "name": n} for s, n in list(seen.items())[:MAX_SYMBOLS]]
    return "nse", universe, shortable


def load_vendored() -> list[dict[str, str]]:
    universe: list[dict[str, str]] = []
    with open(UNIVERSE_FILE, "r", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            sym = (r.get("symbol") or "").strip().upper()
            if sym:
                universe.append({"symbol": sym, "name": (r.get("name") or sym).strip()})
    return universe


def top_up(universe: list[dict[str, str]], max_syms: int) -> tuple[list[dict[str, str]], int]:
    """Top the NSE list up from the vendored snapshot so the daily screen
    always has ~max_syms names even when one of NSE's CSVs fails to load.
    Returns (universe, added_count)."""
    seen = {u["symbol"] for u in universe}
    added = 0
    for v in load_vendored():
        if len(universe) >= max_syms:
            break
        if v["symbol"] not in seen:
            universe.append(v)
            seen.add(v["symbol"])
            added += 1
    return universe, added


def main() -> int:
    source, universe, shortable = fetch_nse_universe()
    note = None
    if universe:
        universe, added = top_up(universe, MAX_SYMBOLS)
        if added:
            note = (
                f"NSE returned {len(universe) - added} symbols; topped up with {added} "
                f"from the vendored snapshot to reach {len(universe)}."
            )
    else:
        universe = load_vendored()
        source = "vendored"
        note = (
            "NSE archive CSVs unreachable; using the vendored snapshot "
            f"({UNIVERSE_FILE.name}). Re-run to retry the live list."
        )

    emit_json({
        "source": source,
        "generated_on": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "count": len(universe),
        "universe": universe,
        "shortable": shortable,
        "note": note,
    } if note else {
        "source": source,
        "generated_on": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "count": len(universe),
        "universe": universe,
        "shortable": shortable,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
