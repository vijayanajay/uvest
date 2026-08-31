"""Track-record database: schema, migration, and shared queries.

The database is the memory of the whole learning loop:

  * Every analyzed recommendation is upserted as an OPEN row
    (`5_track_and_resolve.py`) with its score and decision, along with its
    parsed expiry date.
  * On later runs, OPEN rows are re-checked against live prices and
    resolved to WIN / LOSS — either by hitting target/stop, or at expiry
    against the last available close. `pnl_inr` is net of modeled
    transaction costs (see `common.estimate_costs`).
  * `3_historical_audit.py` reads resolved rows to compute the real
    track record; `is_seed` rows are only a bootstrap until real data
    exists.

Schema is versioned with `PRAGMA user_version`. v1 databases (seed-only,
no tracking columns) are rebuilt automatically — they contain no
user-tracked data. v2 databases gain the expiry/costs columns via ALTER
(v3); v3 databases gain the reco-identity columns (source, re-analysis
counters) via ALTER (v4).
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

from common import DB_FILE, parse_expiry  # noqa: E402

SCHEMA_VERSION = 5

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
    expiry          TEXT,
    expiry_date     TEXT,
    costs_inr       REAL,
    is_seed         INTEGER NOT NULL DEFAULT 0,
    source          TEXT,                   -- where the reco came from (univest / other)
    analysis_count  INTEGER NOT NULL DEFAULT 1,  -- how many times this reco was analyzed
    last_analyzed_on TEXT,                  -- date of the most recent re-analysis
    vix_regime      TEXT,                   -- LOW / NORMAL / HIGH at time of entry
    nifty_trend     TEXT,                   -- BULL / RANGE / BEAR vs 200-DMA at entry
    breadth_regime  TEXT,                   -- RISK_ON / CAUTIOUS / RISK_OFF at entry
    UNIQUE (symbol, instrument_type, action, entry_price, target, stoploss)
);
CREATE INDEX idx_symbol_type ON trade_recommendations (symbol, instrument_type);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create or migrate the schema.

    v1 (seed-only) DBs are rebuilt losslessly. v2 DBs are upgraded in place
    with the expiry/costs columns added via ALTER TABLE.
    """
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='trade_recommendations'"
    ).fetchone()
    if version >= SCHEMA_VERSION and exists:
        return
    if not exists:
        conn.executescript(SCHEMA)
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.commit()
        return
    if version < 2:
        # v1 held only seed rows (tracking did not exist), so a rebuild is lossless.
        conn.execute("DROP TABLE trade_recommendations")
        conn.executescript(SCHEMA)
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.commit()
        return
    if version == 2:
        conn.execute("ALTER TABLE trade_recommendations ADD COLUMN expiry TEXT")
        conn.execute("ALTER TABLE trade_recommendations ADD COLUMN expiry_date TEXT")
        conn.execute("ALTER TABLE trade_recommendations ADD COLUMN costs_inr REAL")
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.commit()
        return
    if version == 3:
        conn.execute("ALTER TABLE trade_recommendations ADD COLUMN source TEXT")
        conn.execute("ALTER TABLE trade_recommendations ADD COLUMN analysis_count INTEGER NOT NULL DEFAULT 1")
        conn.execute("ALTER TABLE trade_recommendations ADD COLUMN last_analyzed_on TEXT")
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.commit()
        return
    if version == 4:
        conn.execute("ALTER TABLE trade_recommendations ADD COLUMN vix_regime TEXT")
        conn.execute("ALTER TABLE trade_recommendations ADD COLUMN nifty_trend TEXT")
        conn.execute("ALTER TABLE trade_recommendations ADD COLUMN breadth_regime TEXT")
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.commit()


def connect(db_path: Path = DB_FILE) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    ensure_schema(conn)
    return conn


def upsert_tracked_trade(conn: sqlite3.Connection, data: dict[str, Any],
                         score: int | None = None, decision: str | None = None,
                         regime: dict[str, Any] | None = None) -> tuple[bool, int, dict[str, Any]]:
    """Record (or refresh) the recommendation currently under analysis.

    Returns (was_inserted, row_id, identity). A recommendation is identified
    two ways, checked in order:

    1. APP IDENTITY (when the input carries `recommended_on` — the date the
       reco was made in Univest or any other app): the row for
       (symbol, instrument_type, action, expiry, reco date). Re-pasting the
       same reco on a later day is recognised as the SAME recommendation:
       live price / score / decision are refreshed, `analysis_count` is
       incremented, and the original reco date is preserved.
    2. SETUP IDENTITY (fallback): the natural key of the trade geometry
       (symbol, instrument_type, action, entry_price, target, stoploss) — so
       identical setups always merge into one row regardless of paste date.

    On an identity match only the mutable fields are refreshed: the originally
    recommended contract (entry/target/stoploss) and the reco date are never
    rewritten, so resolution windows and the track record stay honest.
    """
    # Regime context at time of entry — passed in by the caller (5_track_and_resolve.py)
    # so the validator can later audit performance by regime.
    vix_regime = (regime or {}).get("vix_regime")
    nifty_trend = (regime or {}).get("nifty_trend")
    breadth_regime = (regime or {}).get("breadth_regime")

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
    expiry_text = data.get("expiry")
    expiry_date = parse_expiry(expiry_text)
    source = data.get("source")
    today = date.today().isoformat()

    # The app's reco date when the input carries one; otherwise the reco is
    # dated today. Stored date-only so identity matches are stable across days.
    reco_date = date.today()
    if data.get("recommended_on"):
        try:
            reco_date = date.fromisoformat(str(data["recommended_on"]).strip()[:10])
        except ValueError:
            reco_date = date.today()

    # R:R at the recommended entry (not live)
    risk = entry - sl if action == "BUY" else sl - entry
    reward = target - entry if action == "BUY" else entry - target
    rr = round(reward / risk, 2) if risk > 0 else None

    row_id = None
    matched_by = None
    expiry_iso = expiry_date.isoformat() if expiry_date else None
    if data.get("recommended_on"):
        # App identity: the same reco (same stock, type, action, contract and
        # reco date) re-pasted on any later day lands on the same row.
        row = conn.execute(
            """
            SELECT id FROM trade_recommendations
            WHERE symbol=? AND instrument_type=? AND action=?
              AND (expiry_date = ? OR (expiry_date IS NULL AND ? IS NULL))
              AND substr(recommended_on, 1, 10) = ?
            ORDER BY id LIMIT 1
            """,
            (symbol, inst, action, expiry_iso, expiry_iso, reco_date.isoformat()),
        ).fetchone()
        if row:
            row_id, matched_by = row[0], "reco_date"
    if row_id is None:
        row = conn.execute(
            "SELECT id FROM trade_recommendations WHERE symbol=? AND instrument_type=? AND action=? "
            "AND entry_price=? AND target=? AND stoploss=?",
            (symbol, inst, action, entry, target, sl),
        ).fetchone()
        if row:
            row_id, matched_by = row[0], "setup"

    if row_id is not None:
        conn.execute(
            """
            UPDATE trade_recommendations
            SET entry_range_min=?, entry_range_max=?, live_price=?, lot_size=?, margin=?,
                confidence_score=?, decision=?, rr_ratio=?, expiry=?, expiry_date=?,
                source=COALESCE(source, ?),
                analysis_count = analysis_count + 1,
                last_analyzed_on=?,
                vix_regime=COALESCE(?, vix_regime),
                nifty_trend=COALESCE(?, nifty_trend),
                breadth_regime=COALESCE(?, breadth_regime)
            WHERE id=?
            """,
            (er_min, er_max, live, lot, margin, score, decision, rr, expiry_text,
             expiry_iso, source, today, vix_regime, nifty_trend, breadth_regime, row_id),
        )
        inserted = False
    else:
        cursor = conn.execute(
            """
            INSERT INTO trade_recommendations
                (symbol, instrument_type, action, entry_price, target, stoploss,
                 entry_range_min, entry_range_max, live_price, lot_size, margin,
                 outcome, confidence_score, decision, rr_ratio, recommended_on,
                 expiry, expiry_date, is_seed, source, analysis_count, last_analyzed_on,
                 vix_regime, nifty_trend, breadth_regime)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, 0, ?, 1, ?, ?, ?, ?)
            """,
            (symbol, inst, action, entry, target, sl, er_min, er_max, live, lot, margin,
             score, decision, rr, reco_date.isoformat(), expiry_text, expiry_iso,
             source, today, vix_regime, nifty_trend, breadth_regime),
        )
        row_id = cursor.lastrowid
        inserted = True
        matched_by = "new"

    conn.commit()

    info = conn.execute(
        "SELECT recommended_on, source, analysis_count, last_analyzed_on "
        "FROM trade_recommendations WHERE id=?",
        (row_id,),
    ).fetchone()
    identity = {
        "recommended_on": info[0],
        "source": info[1],
        "analysis_count": info[2],
        "last_analyzed_on": info[3],
        "matched_by": matched_by,
        "is_repeat": (not inserted) and bool(info[2] and info[2] > 1),
    }
    return inserted, row_id, identity


def _calibration_row(wins: int, losses: int) -> dict[str, Any]:
    """Observed win rate with a Wilson 95% CI and a Bayesian-smoothed
    estimate (uniform Beta(1,1) prior) so small samples are not overstated."""
    n = wins + losses
    if n == 0:
        return {"n": 0, "wins": 0, "losses": 0, "win_rate": None,
                "ci_low": None, "ci_high": None, "smoothed_win_rate": None}
    p = wins / n
    z = 1.959963984540054  # 1.96, 95% two-sided
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * (p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5 / denom
    return {
        "n": n, "wins": wins, "losses": losses,
        "win_rate": round(p * 100, 1),
        "ci_low": round(max(0.0, center - half) * 100, 1),
        "ci_high": round(min(1.0, center + half) * 100, 1),
        "smoothed_win_rate": round((wins + 1) / (n + 2) * 100, 1),
    }


def compute_calibration(conn: sqlite3.Connection) -> dict[str, Any]:
    """Calibrate the confidence score against reality.

    Every resolved TRACKED recommendation is bucketed by its stored decision
    label and the observed win rate per bucket is reported with sample size,
    a Wilson 95% CI, and a Bayesian-smoothed estimate. This is what turns the
    rubric score into a measured probability - and what makes the decision
    bands re-tunable from real data once outcomes accumulate.
    """
    rows = conn.execute(
        "SELECT decision, outcome FROM trade_recommendations "
        "WHERE is_seed=0 AND outcome IN ('WIN', 'LOSS')"
    ).fetchall()
    buckets: dict[str, list[int]] = {}
    for decision, outcome in rows:
        key = decision or "UNKNOWN"
        bucket = buckets.setdefault(key, [0, 0])
        if outcome == "WIN":
            bucket[0] += 1
        else:
            bucket[1] += 1
    total_w = sum(b[0] for b in buckets.values())
    total_l = sum(b[1] for b in buckets.values())
    return {
        "buckets": {
            key: _calibration_row(w, l)
            for key, (w, l) in sorted(buckets.items(), key=lambda kv: -sum(kv[1]))
        },
        "total": _calibration_row(total_w, total_l),
        "resolved_tracked": total_w + total_l,
    }


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


def compute_regime_audit(conn: sqlite3.Connection) -> dict[str, Any]:
    """Regime-stratified performance audit for the walk-forward validator.

    Splits all resolved tracked outcomes by their stored regime tags
    (vix_regime, nifty_trend, breadth_regime) and reports win rate,
    expectancy, and sample size per regime bucket. The validator uses
    this to flag regime mismatches: e.g., a setup with positive edge
    only in BULL+LOW_VIX but currently running in RANGE+HIGH_VIX.
    """
    rows = conn.execute(
        """SELECT outcome, pnl_inr, decision,
                  vix_regime, nifty_trend, breadth_regime
           FROM trade_recommendations
           WHERE is_seed=0 AND outcome IN ('WIN', 'LOSS')"""
    ).fetchall()
    if not rows:
        return {"by_vix": {}, "by_nifty": {}, "by_breadth": {}, "total_resolved": 0}

    def _bucket(rows_subset: list[tuple]) -> dict[str, Any]:
        wins = sum(1 for r in rows_subset if r[0] == "WIN")
        losses = sum(1 for r in rows_subset if r[0] == "LOSS")
        n = wins + losses
        if n == 0:
            return {"n": 0, "wins": 0, "losses": 0, "win_rate": None,
                    "expectancy": None, "net_pnl": 0.0}
        wr = wins / n
        avg_w = sum(r[1] for r in rows_subset if r[0] == "WIN") / wins if wins else 0.0
        avg_l = abs(sum(r[1] for r in rows_subset if r[0] == "LOSS") / losses) if losses else 0.0
        exp = (wr * avg_w) - ((1 - wr) * avg_l)
        return {
            "n": n, "wins": wins, "losses": losses,
            "win_rate": round(wr * 100, 1),
            "expectancy": round(exp, 2),
            "net_pnl": round(sum(r[1] for r in rows_subset), 2),
        }

    by_vix: dict[str, list] = {}
    by_nifty: dict[str, list] = {}
    by_breadth: dict[str, list] = {}
    for r in rows:
        outcome, pnl, decision, vix, nifty, breadth = r
        row_data = (outcome, pnl, decision)
        by_vix.setdefault(vix or "UNKNOWN", []).append(row_data)
        by_nifty.setdefault(nifty or "UNKNOWN", []).append(row_data)
        by_breadth.setdefault(breadth or "UNKNOWN", []).append(row_data)

    return {
        "by_vix": {k: _bucket(v) for k, v in sorted(by_vix.items())},
        "by_nifty": {k: _bucket(v) for k, v in sorted(by_nifty.items())},
        "by_breadth": {k: _bucket(v) for k, v in sorted(by_breadth.items())},
        "total_resolved": len(rows),
    }
