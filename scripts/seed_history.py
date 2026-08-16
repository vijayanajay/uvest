"""Seed the Univest historical track-record database.

Creates `univest_history.db` at the project root (if absent) and loads it
with sample `trade_recommendations` rows so Script 3 has real data to
audit. The sample data intentionally spans three scenarios:

  * BHARTIARTL FUT  - >50% win rate but NEGATIVE expectancy (poor R:R) -> reject
  * RELIANCE  FUT   - POSITIVE expectancy                                  -> approve
  * NIFTY     OPT   - POSITIVE expectancy on options                        -> approve
  * HDFCBANK FUT    - intentionally NO rows -> exercises the fallback path

Usage:
    python scripts/seed_history.py [--fresh]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DB_FILE  # noqa: E402

SCHEMA = """
CREATE TABLE IF NOT EXISTS trade_recommendations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT NOT NULL,
    instrument_type TEXT NOT NULL,
    outcome         TEXT NOT NULL CHECK (outcome IN ('WIN', 'LOSS')),
    pnl_inr         REAL NOT NULL,
    rr_ratio        REAL,
    entry_price     REAL,
    target          REAL,
    stoploss        REAL,
    recommended_on  TEXT
);
CREATE INDEX IF NOT EXISTS idx_symbol_type ON trade_recommendations (symbol, instrument_type);
"""

# (symbol, type, outcome, pnl_inr, rr_ratio, entry, target, stoploss, date)
SAMPLE_ROWS = [
    # ---- BHARTIARTL FUT: 12 trades, 7W/5L, avg win 18,200 / avg loss 28,400
    # ---- expectancy = -1,216.67/trade -> classic "win rate ok, edge negative"
    ("BHARTIARTL", "FUT", "WIN", 18200.00, 0.92, 1938.50, 1986.50, 1894.00, "2026-06-02"),
    ("BHARTIARTL", "FUT", "WIN", 19600.00, 0.95, 1961.00, 2010.00, 1915.00, "2026-06-04"),
    ("BHARTIARTL", "FUT", "WIN", 17500.00, 0.88, 1974.00, 2020.00, 1928.00, "2026-06-09"),
    ("BHARTIARTL", "FUT", "WIN", 18900.00, 0.91, 1990.00, 2037.00, 1944.00, "2026-06-11"),
    ("BHARTIARTL", "FUT", "WIN", 20400.00, 0.97, 2005.00, 2053.00, 1959.00, "2026-06-16"),
    ("BHARTIARTL", "FUT", "WIN", 17200.00, 0.87, 2018.00, 2064.00, 1972.00, "2026-06-18"),
    ("BHARTIARTL", "FUT", "WIN", 15600.00, 0.82, 2031.00, 2076.00, 1985.00, "2026-06-23"),
    ("BHARTIARTL", "FUT", "LOSS", -28400.00, 0.89, 1993.50, 2045.00, 1939.00, "2026-06-25"),
    ("BHARTIARTL", "FUT", "LOSS", -28400.00, 0.90, 1945.00, 1995.00, 1891.00, "2026-06-30"),
    ("BHARTIARTL", "FUT", "LOSS", -28400.00, 0.86, 2010.00, 2060.00, 1956.00, "2026-07-02"),
    ("BHARTIARTL", "FUT", "LOSS", -28400.00, 0.93, 1982.00, 2034.00, 1928.00, "2026-07-07"),
    ("BHARTIARTL", "FUT", "LOSS", -28400.00, 0.88, 1998.00, 2048.00, 1944.00, "2026-07-09"),
    # ---- RELIANCE FUT: 10 trades, 7W/3L, avg win 25,000 / avg loss 15,000
    # ---- expectancy = +13,000/trade -> positive edge
    ("RELIANCE", "FUT", "WIN", 26000.00, 1.60, 2910.00, 3002.00, 2872.00, "2026-06-03"),
    ("RELIANCE", "FUT", "WIN", 24000.00, 1.52, 2935.00, 3024.00, 2898.00, "2026-06-05"),
    ("RELIANCE", "FUT", "WIN", 25500.00, 1.58, 2950.00, 3041.00, 2912.00, "2026-06-10"),
    ("RELIANCE", "FUT", "WIN", 23000.00, 1.47, 2968.00, 3055.00, 2931.00, "2026-06-12"),
    ("RELIANCE", "FUT", "WIN", 27000.00, 1.65, 2982.00, 3075.00, 2944.00, "2026-06-17"),
    ("RELIANCE", "FUT", "WIN", 24500.00, 1.54, 3005.00, 3096.00, 2968.00, "2026-06-19"),
    ("RELIANCE", "FUT", "WIN", 25000.00, 1.56, 3020.00, 3112.00, 2982.00, "2026-06-24"),
    ("RELIANCE", "FUT", "LOSS", -15000.00, 1.30, 2955.00, 3035.00, 2921.00, "2026-06-08"),
    ("RELIANCE", "FUT", "LOSS", -15000.00, 1.35, 3012.00, 3094.00, 2977.00, "2026-06-22"),
    ("RELIANCE", "FUT", "LOSS", -15000.00, 1.28, 3045.00, 3125.00, 3011.00, "2026-07-01"),
    # ---- NIFTY OPT: 8 trades, 5W/3L, expectancy = +11,937.50/trade
    ("NIFTY", "OPT", "WIN", 30000.00, 1.70, 25200.00, 25560.00, 24950.00, "2026-06-02"),
    ("NIFTY", "OPT", "WIN", 27500.00, 1.62, 25350.00, 25680.00, 25100.00, "2026-06-09"),
    ("NIFTY", "OPT", "WIN", 32000.00, 1.75, 25480.00, 25820.00, 25240.00, "2026-06-16"),
    ("NIFTY", "OPT", "WIN", 29000.00, 1.66, 25610.00, 25940.00, 25380.00, "2026-06-23"),
    ("NIFTY", "OPT", "WIN", 31500.00, 1.72, 25760.00, 26100.00, 25530.00, "2026-06-30"),
    ("NIFTY", "OPT", "LOSS", -18000.00, 1.40, 25320.00, 25610.00, 25080.00, "2026-06-05"),
    ("NIFTY", "OPT", "LOSS", -16500.00, 1.36, 25590.00, 25880.00, 25360.00, "2026-06-19"),
    ("NIFTY", "OPT", "LOSS", -20000.00, 1.45, 25820.00, 26140.00, 25570.00, "2026-07-03"),
    # NOTE: no HDFCBANK rows -> Script 3 falls back to NO_HISTORICAL_DATA_FOR_TICKER
]


def seed(database: Path, fresh: bool) -> None:
    database.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(database))
    cursor = conn.cursor()
    cursor.executescript(SCHEMA)

    if fresh:
        cursor.execute("DELETE FROM trade_recommendations")

    cursor.execute("SELECT COUNT(*) FROM trade_recommendations")
    existing = cursor.fetchone()[0]
    if existing:
        print(f"Seeding skipped: {database} already has {existing} rows (use --fresh to reseed).")
        conn.close()
        return

    cursor.executemany(
        """
        INSERT INTO trade_recommendations
            (symbol, instrument_type, outcome, pnl_inr, rr_ratio, entry_price, target, stoploss, recommended_on)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        SAMPLE_ROWS,
    )
    conn.commit()
    conn.close()
    print(f"Seeded {len(SAMPLE_ROWS)} sample trades into {database}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed the Univest historical audit database.")
    parser.add_argument("--fresh", action="store_true", help="Wipe existing rows before seeding")
    args = parser.parse_args()
    seed(DB_FILE, fresh=args.fresh)
