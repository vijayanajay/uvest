"""Track-record database: schema, migration, and shared queries.

The database is the memory of the whole learning loop:

  * Every analyzed recommendation is upserted as an OPEN row
    (`5_track_and_resolve.py`) with its score and decision.
  * On later runs, OPEN rows are re-checked against live prices and
    resolved to WIN / LOSS.
  * `3_historical_audit.py` reads resolved rows to compute the real
    track record; `is_seed` rows are only a bootstrap until real data
    exists.

Schema is versioned with `PRAGMA user_version`. v1 databases (seed-only,
no tracking columns) are rebuilt automatically — they contain no
user-tracked data.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from common import DB_FILE  # noqa: E402

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE trade_recommendations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT NOT NULL,
    instrument_type TEXT NOT NULL,
    action          TEXT,
    outcome         TEXT NOT NULL CHECK (outcome IN ('WIN', 'LOSS', 'OPEN')),
    pnl_inr         REAL,
    rr_ratio        REAL,
    entry_price     REAL,
    target          REAL,
    stoploss        REAL,
    entry_range_min REAL,
    entry_range_max REAL,
    live_price      REAL,
    lot_size        REAL,
    margin          REAL,
    confidence_score INTEGER,
    decision        TEXT,
    recommended_on  TEXT,
    resolved_on     TEXT,
    final_price     REAL,
    is_seed         INTEGER NOT NULL DEFAULT 0,
    UNIQUE (symbol, instrument_type, action, entry_price, target, stoploss)
);
CREATE INDEX idx_symbol_type ON trade_recommendations (symbol, instrument_type);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create or migrate the schema. v1 (seed-only) DBs are rebuilt."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='trade_recommendations'"
    ).fetchone()
    if version >= SCHEMA_VERSION and exists:
        return
    if exists:
        # v1 held only seed rows (tracking did not exist), so a rebuild is lossless.
        conn.execute("DROP TABLE trade_recommendations")
    conn.executescript(SCHEMA)
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    conn.commit()


def connect(db_path: Path = DB_FILE) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    ensure_schema(conn)
    return conn


def upsert_tracked_trade(conn: sqlite3.Connection, data: dict[str, Any],
                         score: int | None = None, decision: str | None = None) -> tuple[bool, int]:
    """Record (or refresh) the recommendation currently under analysis.

    Returns (was_inserted, row_id). The natural key is the setup itself,
    so re-analyzing the same reco updates it instead of duplicating.
    `recommended_on` is preserved on conflict — the outcome window starts
    from the first time the setup was analyzed.
    """
    symbol = data["symbol"]
    inst = data.get("instrument_type", "FUT")
    action = data.get("action", "BUY")
    entry = float(data["entry_price"])
    target = float(data["target"])
    sl = float(data["stoploss"])
    lot = float(data.get("lot_size", 0))
    margin = float(data.get("margin", 0))
    live = float(data.get("live_price", entry))
    er_min = float(data.get("entry_range_min", entry))
    er_max = float(data.get("entry_range_max", entry))

    # R:R at the recommended entry (not live)
    risk = entry - sl if action == "BUY" else sl - entry
    reward = target - entry if action == "BUY" else entry - target
    rr = round(reward / risk, 2) if risk > 0 else None

    existing = conn.execute(
        "SELECT id FROM trade_recommendations WHERE symbol=? AND instrument_type=? AND action=? "
        "AND entry_price=? AND target=? AND stoploss=?",
        (symbol, inst, action, entry, target, sl),
    ).fetchone()

    if existing:
        row_id = existing[0]
        conn.execute(
            """
            UPDATE trade_recommendations
            SET entry_range_min=?, entry_range_max=?, live_price=?, lot_size=?, margin=?,
                confidence_score=?, decision=?, rr_ratio=?
            WHERE id=?
            """,
            (er_min, er_max, live, lot, margin, score, decision, rr, row_id),
        )
        inserted = False
    else:
        cursor = conn.execute(
            """
            INSERT INTO trade_recommendations
                (symbol, instrument_type, action, entry_price, target, stoploss,
                 entry_range_min, entry_range_max, live_price, lot_size, margin,
                 outcome, confidence_score, decision, rr_ratio, recommended_on, is_seed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, datetime('now'), 0)
            """,
            (symbol, inst, action, entry, target, sl, er_min, er_max, live, lot, margin,
             score, decision, rr),
        )
        row_id = cursor.lastrowid
        inserted = True

    conn.commit()
    return inserted, row_id


def compute_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """Aggregate the self-tracked record and decision accuracy."""
    tracked = conn.execute(
        "SELECT outcome, pnl_inr, decision FROM trade_recommendations WHERE is_seed=0"
    ).fetchall()

    resolved = [t for t in tracked if t[0] in ("WIN", "LOSS")]
    open_count = sum(1 for t in tracked if t[0] == "OPEN")
    wins = [t for t in resolved if t[0] == "WIN"]
    losses = [t for t in resolved if t[0] == "LOSS"]
    n = len(resolved)

    win_rate = len(wins) / n * 100 if n else 0.0
    avg_win = sum(t[1] for t in wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(t[1] for t in losses) / len(losses)) if losses else 0.0
    expectancy = (win_rate / 100 * avg_win) - ((1 - win_rate / 100) * avg_loss) if n else 0.0
    net_pnl = sum(t[1] for t in resolved)

    approved = [t for t in resolved if t[2] and "GO" in t[2]]
    rejected = [t for t in resolved if t[2] and "NO-GO" in t[2]]
    approved_wins = [t for t in approved if t[0] == "WIN"]
    rejected_wins = [t for t in rejected if t[0] == "WIN"]
    rejected_losses = [t for t in rejected if t[0] == "LOSS"]

    return {
        "total_tracked": len(tracked),
        "open_count": open_count,
        "resolved_count": n,
        "track_record": {
            "sample_size": n,
            "win_rate": round(win_rate, 2),
            "avg_win_inr": round(avg_win, 2),
            "avg_loss_inr": round(avg_loss, 2),
            "net_expectancy_per_trade": round(expectancy, 2),
            "net_pnl_inr": round(net_pnl, 2),
        },
        "decision_accuracy": {
            "approved_resolved": len(approved),
            "approved_wins": len(approved_wins),
            "approved_hit_rate": round(len(approved_wins) / len(approved) * 100, 2) if approved else None,
            "rejected_resolved": len(rejected),
            "rejected_wins_missed": len(rejected_wins),
            "rejected_losses_avoided": len(rejected_losses),
            "missed_profit_inr": round(sum(t[1] for t in rejected_wins), 2),
            "avoided_losses_inr": round(abs(sum(t[1] for t in rejected_losses)), 2),
        },
    }
