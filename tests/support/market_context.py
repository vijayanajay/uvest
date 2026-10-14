"""Deterministic `uvest-market-context/1` bundles for stage-7 seams (ADR-010).

The evidence schema stage 7 reads: one JSON bundle whose series are keyed by
Yahoo-style index tickers (`^NSEI`, `^INDIAVIX`, the trade's sectoral index)
plus the `NSE500_BREADTH` and `FII_DII_NET` aggregates, each carrying `unit`
and `values: [{date, value}]`.

Everything here is *authored, disclosed* arithmetic — deterministic weekday
dates and smooth trajectories with exactly placed tail values so every frozen
boundary (VIX bands, breadth regimes, flow bias, RRG quadrants) is reachable
without a random seed. It is test scaffolding, never market evidence: the
provenance block says so, and no graded gate reads it.

Run this file directly to regenerate the committed fixture:

    python tests/support/market_context.py
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import json

SCHEMA = "uvest-market-context/1"
AS_OF = date(2026, 10, 9)
DEFAULT_SESSIONS = 210  # >= 200 for the NIFTY 200 DMA, >= 55 for MRS + momentum

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "market_context_20261009.json"

# The five series every bundle carries, in the stage's frozen check order.
NSEI = "^NSEI"
INDIA_VIX = "^INDIAVIX"
BREADTH = "NSE500_BREADTH"
FLOW = "FII_DII_NET"
SECTOR = "^CNXENERGY"  # NIFTY ENERGY — RELIANCE's parent index (RELIANCE_20261009 bundle)

PROVENANCE_NOTE = (
    "Authored deterministic arithmetic series for offline stage-7 seams "
    "(tests/support/market_context.py). Not market evidence; no graded gate reads it."
)


def weekday_dates(end: date, n: int) -> list[str]:
    """The `n` weekdays ending on `end` (inclusive), ascending."""
    dates: list[str] = []
    d = end
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d.isoformat())
        d -= timedelta(days=1)
    dates.reverse()
    return dates


def _series(name: str, unit: str, values: list[float], dates: list[str]) -> dict:
    return {
        "name": name,
        "unit": unit,
        "values": [{"date": dt, "value": round(v, 6)} for dt, v in zip(dates, values)],
    }


def build_context(
    *,
    n: int = DEFAULT_SESSIONS,
    as_of: date = AS_OF,
    nifty_start: float = 24000.0,
    nifty_step: float = 10.0,
    ratio_tail_days: int = 50,
    ratio_tail_end: float = 1.05,
    vix_tail: float = 15.6,
    breadth_tail: float = 57.0,
    fii_unit: float = 240.0,
    sector_ticker: str = SECTOR,
    drop: tuple[str, ...] = (),
    misalign_sector: bool = False,
) -> dict:
    """Build a full bundle with controllable tail values.

    Shapes:
    * NIFTY rises arithmetically from `nifty_start`, so its last close is above
      both the 50 and the 200 SMA → `nifty_trend: ABOVE_50_200_DMA`.
    * The sector ratio is flat at 1.0 until the last `ratio_tail_days`, then
      ramps to `ratio_tail_end` → MRS level > 0 and 5-session momentum > 0
      (LEADING) when `ratio_tail_end` > 1.
    * `vix_tail` / `breadth_tail` / `fii_unit` pin the exact final values the
      band classifiers grade (flow is a constant series, so the last-5 sum is
      `5 * fii_unit`).
    * `drop` removes series (absent-input waiver cases); `misalign_sector`
      shifts the sector axis off the NIFTY axis (misalignment waiver).
    """
    dates = weekday_dates(as_of, n)
    nifty = [nifty_start + nifty_step * i for i in range(n)]
    ratios = [
        1.0
        if i < n - ratio_tail_days
        else 1.0 + (ratio_tail_end - 1.0) * (i - (n - ratio_tail_days) + 1) / ratio_tail_days
        for i in range(n)
    ]
    sector = [c * r for c, r in zip(nifty, ratios)]
    vix = [vix_tail + 2.0 * (1.0 - i / (n - 1)) for i in range(n)]
    breadth = [breadth_tail + 8.0 * (1.0 - i / (n - 1)) for i in range(n)]
    flow = [fii_unit] * n

    sector_dates = dates[1:] if misalign_sector else dates
    sector_values = sector[1:] if misalign_sector else sector

    series = {
        NSEI: _series("NIFTY 50", "index_close", nifty, dates),
        sector_ticker: _series(sector_ticker, "index_close", sector_values, sector_dates),
        INDIA_VIX: _series("INDIA VIX", "index_close", vix, dates),
        BREADTH: _series("NSE 500 % above 200 DMA", "percent", breadth, dates),
        FLOW: _series("FII + DII combined net cash flow", "inr_cr", flow, dates),
    }
    for key in drop:
        del series[key]

    return {
        "schema": SCHEMA,
        "as_of": as_of.isoformat(),
        "provenance": {
            "origin": "authored",
            "source": "tests/support/market_context.py",
            "note": PROVENANCE_NOTE,
        },
        "series": series,
    }


def write_default_fixture() -> Path:
    """Regenerate the committed fixture (deterministic, byte-stable)."""
    DEFAULT_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_FIXTURE.write_text(
        json.dumps(build_context(), indent=2) + "\n", encoding="utf-8"
    )
    return DEFAULT_FIXTURE


if __name__ == "__main__":
    print(write_default_fixture())
