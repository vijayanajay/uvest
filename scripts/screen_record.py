"""Screen 4: Auto-recorder + learning loop for the daily screen.

Two responsibilities, both idempotent:

1. RESOLVE (always): every OPEN pick recorded on earlier days is re-checked
   against live daily bars. Scanning from the day after the pick date, the
   first trigger wins — stoploss first on same-bar ambiguity (conservative,
   mirroring scripts/5_track_and_resolve.py). Triggers are only honoured up
   to the pick's horizon (horizon_bars); if neither target nor stop is hit
   and the data covers the horizon end, the pick is settled at the
   horizon-end close vs entry. P&L is net of modeled transaction costs
   (FUT rate, common.py).

2. RECORD (--record): today's ranked watchlist (top of screen_rank_out.json)
   is upserted as OPEN rows, one per symbol, keyed by (pick_date, symbol).
   Rows whose symbol also appears in screen_picks.json (written by the
   daily prompt) get is_pick=1 plus the sentiment score, verdict and thesis
   so outcomes are attributable to the call.

Finally it prints the learning report and writes the standing learning
journal (screen_learning.md — compact one-liners by default, detail blocks
only for outliers that acted wildly from the norm):
   * resolved this run (symbol -> WIN/LOSS, P&L)
   * calibration per (horizon, hit): predicted win rate vs realized, with
     a Wilson CI — the recorder re-takes the horizon call from live data
   * sentiment audit: sentiment-picked rows vs the rest of the recorded
     list (the free control group) — does the news overlay add value?
   * score/verdict accuracy: win rate per recorded verdict bucket
   * outliers: EDGE CONFIRMED that lost / NO EDGE that won (n >= 30) or
     settled |P&L| >= 5% — with prediction, outcome and pick context

Usage:
    python scripts/screen_record.py [--record]
        [--rank screen_rank_out.json] [--picks screen_picks.json] [--offline]
"""

from __future__ import annotations

import argparse
import math
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    CAPITAL_INR,
    COST_RATE_FUT,
    MAX_RISK_PCT,
    MAX_TOTAL_DEPLOYMENT_PCT,
    MIN_DEPLOYMENT_PCT,
    PROJECT_ROOT,
    RISK_PER_TRADE_PCT,
    emit_json,
    load_json,
    wilson_ci,
)

DB_FILE = PROJECT_ROOT / "screen_history.db"
RANK_FILE = PROJECT_ROOT / "screen_rank_out.json"
PICKS_FILE = PROJECT_ROOT / "screen_picks.json"
JOURNAL_FILE = PROJECT_ROOT / "screen_learning.md"

SCHEMA = """
CREATE TABLE IF NOT EXISTS screen_picks (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    pick_date           TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    direction           TEXT NOT NULL,
    horizon_bars        INTEGER NOT NULL,
    hit_pct             REAL NOT NULL,
    entry_price         REAL,
    stop_price          REAL,
    target_price        REAL,
    atr_pct             REAL,
    rr                  REAL,
    predicted_hit_pct   REAL,
    predicted_win_pct   REAL,
    ci_low              REAL,
    ci_high             REAL,
    sample_n            INTEGER,
    verdict             TEXT,
    risk                TEXT,
    is_pick             INTEGER NOT NULL DEFAULT 0,
    sentiment_score     REAL,
    sentiment_verdict   TEXT,
    thesis              TEXT,
    outcome             TEXT NOT NULL DEFAULT 'OPEN' CHECK (outcome IN ('OPEN','WIN','LOSS')),
    pnl_pct             REAL,
    resolved_on         TEXT,
    -- 5L capital layer: sizing at record time, INR P&L at resolution
    notional            REAL,
    qty                 INTEGER,
    risk_amount         REAL,
    deployment_pct      REAL,
    risk_pct            REAL,
    pnl_inr             REAL,
    -- predicted forward-return range (from the screen's distribution)
    fwd_median          REAL,
    fwd_p10             REAL,
    fwd_p90             REAL,
    fwd_pct_positive    REAL,
    UNIQUE (pick_date, symbol)
);
CREATE INDEX IF NOT EXISTS idx_screen_open ON screen_picks (outcome);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_FILE))
    conn.executescript(SCHEMA)
    # v1 databases predate sample_n / ci_high; v2 predates the capital layer
    # (notional/qty/risk/pnl_inr) and the forward-return range columns. Add
    # them in place so journals can judge prediction strength and capital
    # return instead of trusting a verdict blindly.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(screen_picks)")}
    for col, ddl in (
        ("sample_n", "INTEGER"),
        ("ci_high", "REAL"),
        ("notional", "REAL"),
        ("qty", "INTEGER"),
        ("risk_amount", "REAL"),
        ("deployment_pct", "REAL"),
        ("risk_pct", "REAL"),
        ("pnl_inr", "REAL"),
        ("fwd_median", "REAL"),
        ("fwd_p10", "REAL"),
        ("fwd_p90", "REAL"),
        ("fwd_pct_positive", "REAL"),
    ):
        if col not in cols:
            conn.execute(f"ALTER TABLE screen_picks ADD COLUMN {col} {ddl}")
    conn.commit()
    return conn


# ---- position sizing (5L capital, risk-first with a 30% floor) -------------
def size_position(entry: float, stop_pct: float) -> dict[str, Any]:
    """Risk-first quantity for a pick on CAPITAL_INR.

    qty = max(risk-based qty, 30%-floor qty), so a pick never deploys less
    than MIN_DEPLOYMENT_PCT of capital. The floor usually binds: 1% risk /
    ~4% stop is only ~25% deployment, so the floor lifts deployment to 30%
    (₹1.5L) and effective risk to ~1.2% of capital. Returns {} for inputs
    that cannot be sized (defensive — the caller skips sizing).
    """
    if entry <= 0 or stop_pct <= 0:
        return {}
    risk_per_share = entry * stop_pct / 100.0
    qty_risk = int(CAPITAL_INR * RISK_PER_TRADE_PCT // risk_per_share)
    qty_floor = math.ceil(CAPITAL_INR * MIN_DEPLOYMENT_PCT / entry)
    qty = max(qty_risk, qty_floor)
    notional = qty * entry
    return {
        "qty": qty,
        "notional": round(notional, 2),
        "risk_amount": round(qty * risk_per_share, 2),
        "deployment_pct": round(notional / CAPITAL_INR * 100.0, 1),
        "risk_pct": round(qty * risk_per_share / CAPITAL_INR * 100.0, 2),
        "floor_applied": qty_floor > qty_risk,
        "risk_per_share": round(risk_per_share, 2),
    }


def sizing_warnings(sized: list[dict[str, Any]]) -> list[str]:
    """Portfolio-level warnings across today's picks (deployment / risk caps)."""
    warnings: list[str] = []
    total_deploy = sum(s.get("deployment_pct", 0.0) for s in sized)
    total_risk = sum(s.get("risk_pct", 0.0) for s in sized)
    if total_deploy > MAX_TOTAL_DEPLOYMENT_PCT * 100.0:
        worst = max(sized, key=lambda s: s.get("stop_pct", 0))
        warnings.append(
            f"picks would deploy {total_deploy:.0f}% of capital (cap {MAX_TOTAL_DEPLOYMENT_PCT * 100:.0f}%) — "
            f"drop one pick, ideally {worst.get('symbol')} (widest stop {worst.get('stop_pct')}%)."
        )
    if total_risk > MAX_RISK_PCT * 100.0 * len(sized) and len(sized) > 1:
        warnings.append(
            f"combined stop-loss exposure {total_risk:.1f}% of capital across {len(sized)} picks — "
            "above the 1.5%/pick comfort band; prefer fewer picks."
        )
    return warnings


# ---- resolution -------------------------------------------------------------
def resolve_open(conn: sqlite3.Connection, offline: bool) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, symbol, direction, entry_price, target_price, stop_price, "
        "pick_date, horizon_bars, hit_pct, notional FROM screen_picks WHERE outcome='OPEN'"
    ).fetchall()
    if not rows:
        return []
    if offline:
        return []

    import yfinance as yf

    resolved: list[dict[str, Any]] = []
    by_symbol: dict[str, list[tuple]] = {}
    for r in rows:
        by_symbol.setdefault(r[1], []).append(r)

    for symbol, recs in by_symbol.items():
        earliest = min(r[6] for r in recs)[:10]
        try:
            df = yf.Ticker(f"{symbol}.NS").history(start=earliest, interval="1d")
        except Exception:
            continue
        if df.empty:
            continue
        bars = [
            (ts.date(), float(b["Low"]), float(b["High"]), float(b["Close"]))
            for ts, b in df.iterrows()
        ]

        for r in recs:
            rid, sym, direction, entry, target, sl, pick_on, hz, hit_pct, notional = r
            try:
                pick_day = date.fromisoformat(pick_on[:10])
            except ValueError:
                continue
            if entry is None or entry <= 0 or target is None or sl is None:
                continue

            outcome: str | None = None
            final: float | None = None
            bars_after = 0
            for day, low, high, _close in bars:
                if day <= pick_day:
                    continue
                bars_after += 1
                if bars_after > hz:
                    break
                if direction == "SELL":
                    if high >= sl:           # price rose to the stop first
                        outcome, final = "LOSS", sl
                        break
                    if low <= target:
                        outcome, final = "WIN", target
                        break
                else:
                    if low <= sl:            # stop first (conservative)
                        outcome, final = "LOSS", sl
                        break
                    if high >= target:
                        outcome, final = "WIN", target
                        break

            if outcome is None:
                # No trigger and at least horizon_bars bars are in: settle at
                # the horizon-end close (mirrors expiry settlement).
                after = [b for b in bars if b[0] > pick_day][:hz]
                if len(after) < hz:
                    continue  # too fresh / data too short — stays OPEN
                final = after[-1][3]
                gross = (final - entry) if direction == "BUY" else (entry - final)
                outcome = "WIN" if gross > 0 else "LOSS"

            gross_pct = ((final - entry) / entry if direction == "BUY"
                         else (entry - final) / entry)
            pnl = round(gross_pct * 100 - COST_RATE_FUT * 100, 3)
            pnl_inr = round(pnl / 100.0 * notional, 2) if notional else None
            conn.execute(
                "UPDATE screen_picks SET outcome=?, pnl_pct=?, pnl_inr=?, resolved_on=? WHERE id=?",
                (outcome, pnl, pnl_inr, date.today().isoformat(), rid),
            )
            resolved.append({
                "symbol": sym, "direction": direction, "outcome": outcome,
                "pnl_pct": pnl, "pnl_inr": pnl_inr, "notional": notional,
                "final_price": round(final, 2), "resolved_on": date.today().isoformat(),
            })
    conn.commit()
    return resolved


# ---- recording --------------------------------------------------------------
def record_today(conn: sqlite3.Connection, rank: dict[str, Any], picks: dict[str, Any]) -> dict[str, Any]:
    today = date.today().isoformat()
    top = rank.get("top", [])
    pick_map = {p.get("symbol"): p for p in picks.get("picks", []) if p.get("symbol")}
    horizon = int(rank.get("params", {}).get("horizon", 10))
    hit = float(rank.get("params", {}).get("hit", 4.0))

    recorded = 0
    updated = 0
    warnings: list[str] = []
    sized_picks: list[dict[str, Any]] = []
    for row in top:
        sym = row["symbol"]
        direction = row["direction"]
        entry = row["close"]
        stop_pct_pct = row["stop_pct"]
        stop_pct = stop_pct_pct / 100.0
        hit_pct = hit / 100.0
        stop = entry * (1 - stop_pct) if direction == "BUY" else entry * (1 + stop_pct)
        target = entry * (1 + hit_pct) if direction == "BUY" else entry * (1 - hit_pct)
        cell_key = f"{horizon}:{hit:.1f}"
        cell = row.get("cells", {}).get(cell_key, {})
        sample_n = cell.get("n") if isinstance(cell, dict) else None
        ci_high = cell.get("ci_high") if isinstance(cell, dict) else None

        p = pick_map.get(sym)
        is_pick = 1 if p else 0
        sentiment_score = p.get("sentiment_score") if p else None
        sentiment_verdict = p.get("sentiment_verdict") if p else None
        thesis = p.get("thesis") if p else None

        # 5L capital layer: size only what the prompt actually picked. The
        # rest of the list is the free control group — no sizing, no qty.
        sizing: dict[str, Any] = {}
        fwd_cols: dict[str, Any] = {}
        if is_pick:
            sizing = size_position(entry, stop_pct_pct)
            rr = p.get("return_range") or {}
            if rr.get("median") is not None:
                fwd_cols = {
                    "fwd_median": rr.get("median"),
                    "fwd_p10": rr.get("p10"),
                    "fwd_p90": rr.get("p90"),
                    "fwd_pct_positive": rr.get("pct_positive"),
                }
            else:
                r = (row.get("returns") or {}).get(str(horizon))
                if r:
                    fwd_cols = {
                        "fwd_median": r.get("median"),
                        "fwd_p10": r.get("p10"),
                        "fwd_p90": r.get("p90"),
                        "fwd_pct_positive": r.get("pct_positive"),
                    }
            sized_picks.append({"symbol": sym, "stop_pct": stop_pct_pct, **sizing})

        existing = conn.execute(
            "SELECT id FROM screen_picks WHERE pick_date=? AND symbol=?",
            (today, sym),
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE screen_picks SET direction=?, horizon_bars=?, hit_pct=?, entry_price=?,
                   stop_price=?, target_price=?, atr_pct=?, rr=?, predicted_hit_pct=?,
                   predicted_win_pct=?, ci_low=?, ci_high=?, sample_n=?, verdict=?, risk=?,
                   is_pick=?, sentiment_score=?, sentiment_verdict=?, thesis=?, notional=?,
                   qty=?, risk_amount=?, deployment_pct=?, risk_pct=?, fwd_median=?,
                   fwd_p10=?, fwd_p90=?, fwd_pct_positive=? WHERE id=?""",
                (direction, horizon, hit, entry, stop, target, row["atr_pct"], row["rr"],
                 row["p_hit"], row["win_rate"], row["ci_low"], ci_high, sample_n,
                 row["verdict"], row["risk"], is_pick, sentiment_score, sentiment_verdict,
                 thesis, sizing.get("notional"), sizing.get("qty"), sizing.get("risk_amount"),
                 sizing.get("deployment_pct"), sizing.get("risk_pct"), fwd_cols.get("fwd_median"),
                 fwd_cols.get("fwd_p10"), fwd_cols.get("fwd_p90"), fwd_cols.get("fwd_pct_positive"),
                 existing[0]),
            )
            updated += 1
        else:
            conn.execute(
                """INSERT INTO screen_picks (pick_date, symbol, direction, horizon_bars, hit_pct,
                   entry_price, stop_price, target_price, atr_pct, rr, predicted_hit_pct,
                   predicted_win_pct, ci_low, ci_high, sample_n, verdict, risk, is_pick,
                   sentiment_score, sentiment_verdict, thesis, notional, qty, risk_amount,
                   deployment_pct, risk_pct, fwd_median, fwd_p10, fwd_p90, fwd_pct_positive)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (today, sym, direction, horizon, hit, entry, stop, target, row["atr_pct"],
                 row["rr"], row["p_hit"], row["win_rate"], row["ci_low"], ci_high, sample_n,
                 row["verdict"], row["risk"], is_pick, sentiment_score, sentiment_verdict, thesis,
                 sizing.get("notional"), sizing.get("qty"), sizing.get("risk_amount"),
                 sizing.get("deployment_pct"), sizing.get("risk_pct"), fwd_cols.get("fwd_median"),
                 fwd_cols.get("fwd_p10"), fwd_cols.get("fwd_p90"), fwd_cols.get("fwd_pct_positive")),
            )
            recorded += 1

    # Sanity: picks that are not in today's list.
    top_symbols = {r["symbol"] for r in top}
    for p in picks.get("picks", []):
        if p.get("symbol") not in top_symbols:
            warnings.append(f"pick {p.get('symbol')} is not in today's top list — not recorded as picked")

    sizing_payload: dict[str, Any] = {
        "capital_inr": CAPITAL_INR,
        "risk_per_trade_inr": round(CAPITAL_INR * RISK_PER_TRADE_PCT, 2),
        "min_deployment_inr": round(CAPITAL_INR * MIN_DEPLOYMENT_PCT, 2),
        "per_pick": sized_picks,
        "total_deployment_pct": round(sum(s.get("deployment_pct", 0.0) for s in sized_picks), 1),
        "total_risk_pct": round(sum(s.get("risk_pct", 0.0) for s in sized_picks), 2),
    }
    sizing_payload["warnings"] = sizing_warnings(sized_picks)
    warnings += sizing_payload["warnings"]

    conn.commit()
    return {"recorded": recorded, "updated": updated, "warnings": warnings, "sizing": sizing_payload}


# ---- learning report ---------------------------------------------------------
def _bucket_stats(conn: sqlite3.Connection, where: str) -> dict[str, Any]:
    rows = conn.execute(
        f"SELECT outcome, pnl_pct FROM screen_picks WHERE outcome IN ('WIN','LOSS') AND {where}"
    ).fetchall()
    n = len(rows)
    if n == 0:
        return {"n": 0, "win_rate": None, "ci_low": None, "ci_high": None,
                "avg_pnl_pct": None, "wins": 0, "losses": 0}
    wins = sum(1 for r in rows if r[0] == "WIN")
    ci_low, ci_high = wilson_ci(wins, n)
    return {
        "n": n, "wins": wins, "losses": n - wins,
        "win_rate": round(wins / n * 100, 1),
        "ci_low": round(ci_low * 100, 1),
        "ci_high": round(ci_high * 100, 1),
        "avg_pnl_pct": round(sum(r[1] for r in rows) / n, 2),
    }


def calibration(conn: sqlite3.Connection) -> dict[str, Any]:
    out: dict[str, Any] = {}
    rows = conn.execute(
        "SELECT horizon_bars, hit_pct, predicted_win_pct, outcome FROM screen_picks "
        "WHERE outcome IN ('WIN','LOSS')"
    ).fetchall()
    by_cell: dict[tuple, list] = {}
    for hz, hit, pred, outcome in rows:
        by_cell.setdefault((hz, hit), []).append((pred, outcome))
    for (hz, hit), recs in sorted(by_cell.items()):
        n = len(recs)
        wins = sum(1 for _, o in recs if o == "WIN")
        ci_low, ci_high = wilson_ci(wins, n)
        pred_avg = sum(p for p, _ in recs if p is not None) / max(1, sum(1 for p, _ in recs if p is not None))
        realized = wins / n * 100
        out[f"{hz}:{hit:.1f}"] = {
            "n": n,
            "predicted_win_rate_avg": round(pred_avg, 1) if pred_avg else None,
            "realized_win_rate": round(realized, 1),
            "ci_low": round(ci_low * 100, 1),
            "ci_high": round(ci_high * 100, 1),
            "on_track": abs(realized - pred_avg) <= 15 if pred_avg else None,
        }
    return out


def sentiment_audit(conn: sqlite3.Connection) -> dict[str, Any]:
    picked = _bucket_stats(conn, "is_pick=1")
    rest = _bucket_stats(conn, "is_pick=0")
    note = None
    if picked["n"] >= 5 and rest["n"] >= 5:
        if picked["win_rate"] and rest["win_rate"]:
            delta = picked["win_rate"] - rest["win_rate"]
            if delta > 5:
                note = "sentiment-picked rows are winning more than the stats-only list so far."
            elif delta < -5:
                note = "sentiment-picked rows are UNDERPERFORMING the stats-only list — the news overlay is subtracting value; consider dropping it."
            else:
                note = "sentiment picks are statistically indistinguishable from the stats-only list so far."
    elif picked["n"] > 0:
        note = f"insufficient resolved sentiment picks ({picked['n']}) for a verdict — keep recording."
    return {"picked": picked, "not_picked": rest, "note": note}


def verdict_accuracy(conn: sqlite3.Connection) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for verdict in ("EDGE CONFIRMED", "POSITIVE BUT UNCONFIRMED", "NO EDGE"):
        out[verdict] = _bucket_stats(conn, f"verdict='{verdict}'")
    return out


def _resolved_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cols = ["pick_date", "symbol", "direction", "horizon_bars", "hit_pct", "entry_price",
            "stop_price", "target_price", "predicted_hit_pct", "predicted_win_pct", "ci_low",
            "ci_high", "sample_n", "verdict", "risk", "is_pick", "sentiment_score",
            "sentiment_verdict", "thesis", "outcome", "pnl_pct", "pnl_inr", "resolved_on"]
    rows = conn.execute(
        "SELECT pick_date, symbol, direction, horizon_bars, hit_pct, entry_price, stop_price, "
        "target_price, predicted_hit_pct, predicted_win_pct, ci_low, ci_high, sample_n, verdict, "
        "risk, is_pick, sentiment_score, sentiment_verdict, thesis, outcome, pnl_pct, pnl_inr, "
        "resolved_on "
        "FROM screen_picks WHERE outcome IN ('WIN','LOSS') ORDER BY resolved_on DESC, id DESC"
    ).fetchall()
    return [dict(zip(cols, r)) for r in rows]


def outlier_kind(row: dict[str, Any]) -> str | None:
    """A stock that acted wildly from the norm: either it contradicted a
    strong prediction, or it moved far past its own target/stop geometry."""
    n = row.get("sample_n")
    n_ok = n is None or n >= 30
    if n_ok and row["verdict"] == "EDGE CONFIRMED" and row["outcome"] == "LOSS":
        return "contradiction_confirmed_lost"
    if n_ok and row["verdict"] == "NO EDGE" and row["outcome"] == "WIN":
        return "contradiction_noedge_won"
    if abs(row.get("pnl_pct") or 0.0) >= 5.0:
        return "wild_move"
    return None


_OUTLIER_NOTES = {
    "contradiction_confirmed_lost": (
        "Strong prediction contradicted: an EDGE CONFIRMED pick lost. Check the news/events "
        "around the pick date before trusting this name again — this is exactly the case the "
        "sentiment layer exists to catch."
    ),
    "contradiction_noedge_won": (
        "Predicted NO EDGE but won: a missed catalyst. Re-score this stock's sentiment next "
        "time it appears — the statistics missed whatever happened."
    ),
    "wild_move": (
        "Moved far past its own target/stop geometry (settled |P&L| >= 5%): an unusual "
        "single-move name. Treat its historical stats with extra caution."
    ),
}


def render_learning_journal(conn: sqlite3.Connection) -> str:
    """The standing, compact learning document (screen_learning.md).

    Compact by default — one line per bucket. Detail blocks appear only
    for outliers: resolved picks that contradicted a strong prediction or
    moved wildly past their own geometry.
    """
    rows = _resolved_rows(conn)
    total = conn.execute("SELECT COUNT(*) FROM screen_picks").fetchone()[0]
    open_n = conn.execute("SELECT COUNT(*) FROM screen_picks WHERE outcome='OPEN'").fetchone()[0]
    resolved_n = len(rows)

    wins = sum(1 for r in rows if r["outcome"] == "WIN")
    wr = f"{wins / resolved_n * 100:.1f}% ({wins}/{resolved_n})" if resolved_n else "n/a (no resolutions yet)"
    avg_pnl = sum(r["pnl_pct"] or 0 for r in rows) / resolved_n if resolved_n else None
    avg_s = f"{avg_pnl:+.2f}%" if avg_pnl is not None else "n/a"

    realized_inr = conn.execute(
        "SELECT COALESCE(SUM(pnl_inr), 0) FROM screen_picks WHERE outcome IN ('WIN','LOSS') AND pnl_inr IS NOT NULL"
    ).fetchone()[0]
    cap_ret = realized_inr / CAPITAL_INR * 100.0
    n_inr = conn.execute(
        "SELECT COUNT(*) FROM screen_picks WHERE outcome IN ('WIN','LOSS') AND pnl_inr IS NOT NULL"
    ).fetchone()[0]

    lines = [
        "# 📚 Screen Learning Journal",
        "",
        f"*Updated {date.today().isoformat()} · {total} recorded · {open_n} open · {resolved_n} resolved · "
        "net of 0.09% modeled costs*",
        "",
        "## Track record",
        f"- Overall win rate: **{wr}** · avg P&L: **{avg_s}** per resolved pick",
        f"- **Capital: ₹{CAPITAL_INR:,.0f}** · realized ₹ P&L: **{realized_inr:+,.0f}** "
        f"({n_inr} sized resolutions) · **capital return {cap_ret:+.2f}%**",
        "",
        "## Calibration — predicted vs realized win rate",
        "",
    ]
    cal = calibration(conn)
    if not cal:
        lines += ["_No resolutions yet — calibration activates as picks resolve (2–4 weeks per horizon)._", ""]
    else:
        lines += ["| cell | n | predicted | realized | 95% CI | status |",
                  "| :-- | --: | --: | --: | --: | :-- |"]
        for key, c in sorted(cal.items(), key=lambda kv: (int(kv[0].split(":")[0]), float(kv[0].split(":")[1]))):
            pred = c["predicted_win_rate_avg"] if c["predicted_win_rate_avg"] is not None else "—"
            status = "✅ on track" if c.get("on_track") else "⚠️ off track"
            lines.append(f"| {key} | {c['n']} | {pred} | {c['realized_win_rate']} | "
                         f"{c['ci_low']}–{c['ci_high']} | {status} |")
        lines.append("")

    lines += ["## Verdict accuracy", ""]
    va = verdict_accuracy(conn)
    any_va = False
    for verdict in ("EDGE CONFIRMED", "POSITIVE BUT UNCONFIRMED", "NO EDGE"):
        b = va.get(verdict, {})
        if b.get("n"):
            any_va = True
            lines.append(f"- **{verdict}:** {b['n']} resolved · win rate {b['win_rate']}% · avg {b['avg_pnl_pct']:+.2f}%")
    if not any_va:
        lines.append("_No resolutions yet._")
    lines.append("")

    lines += ["## Sentiment audit (picks vs the stats-only control group)", ""]
    sa = sentiment_audit(conn)
    p, r = sa["picked"], sa["not_picked"]
    if p["n"] or r["n"]:
        lines.append(f"- picks: **{p['n']}** resolved · {p['win_rate']}% win" if p["n"] else "- picks: none resolved yet")
        lines.append(f"- control: **{r['n']}** resolved · {r['win_rate']}% win" if r["n"] else "- control: none resolved yet")
        if sa.get("note"):
            lines.append(f"- **Verdict:** {sa['note']}")
    else:
        lines.append("_No resolutions yet — the non-picked top-list rows are the control group._")
    lines.append("")

    lines += ["## Recent resolutions", "",
              "| resolved | symbol | dir | outcome | pnl% | pnl ₹ | predicted P(hit) | verdict |",
              "| :-- | :-- | :-- | :-- | --: | --: | --: | :-- |"]
    for r in rows[:10]:
        star = "★ " if r["is_pick"] else ""
        inr = f"{r.get('pnl_inr'):+,.0f}" if r.get("pnl_inr") is not None else "—"
        lines.append(f"| {r['resolved_on']} | {star}{r['symbol']} | {r['direction']} | {r['outcome']} | "
                     f"{r['pnl_pct']:+.2f} | {inr} | {r['predicted_hit_pct']} | {r['verdict']} |")
    lines.append("")

    # Range calibration: did realized P&L land inside the predicted band?
    range_rows = conn.execute(
        "SELECT pnl_pct, fwd_p10, fwd_p90 FROM screen_picks "
        "WHERE outcome IN ('WIN','LOSS') AND fwd_p10 IS NOT NULL AND fwd_p90 IS NOT NULL AND pnl_pct IS NOT NULL"
    ).fetchall()
    if range_rows:
        inside = sum(1 for p, lo, hi in range_rows if lo <= p <= hi)
        lines += ["## Range calibration — realized vs predicted P10–P90 band", ""]
        lines.append(
            f"- Realized P&L landed inside the predicted forward-return P10–P90 band: "
            f"**{inside}/{len(range_rows)} ({inside / len(range_rows) * 100:.0f}%)** "
            "— the screen's return ranges are honest if this stays high."
        )
        lines.append("")

    outliers = [r for r in rows if outlier_kind(r)]
    if outliers:
        lines += ["## ⚠ Outliers — acted wildly from the norm", ""]
        for r in outliers[:5]:
            kind = outlier_kind(r)
            n_txt = f"n={r['sample_n']}" if r.get("sample_n") else "n=unrecorded"
            lines.append(f"### {r['symbol']} ({r['direction']} {r['horizon_bars']}-bar × {r['hit_pct']}%, "
                         f"picked {r['pick_date']})")
            lines.append(f"- Predicted: **{r['verdict']}** · P(hit) {r['predicted_hit_pct']}% · "
                         f"win {r['predicted_win_pct']}% (CI {r['ci_low']}–{r['ci_high']}, {n_txt})")
            lines.append(f"- Outcome: **{r['outcome']}** · {r['pnl_pct']:+.2f}% net")
            if r["is_pick"]:
                score = r.get("sentiment_score")
                score_txt = f"{int(score):+d}" if score is not None else "?"
                lines.append(f"- Sentiment pick ({score_txt}): {r['thesis']}")
            lines.append(f"- {_OUTLIER_NOTES.get(kind, '')}")
            lines.append("")
        if len(outliers) > 5:
            lines.append(f"_…and {len(outliers) - 5} more outlier(s) this run._")
            lines.append("")

    return "\n".join(lines)


def record_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    total = conn.execute("SELECT COUNT(*) FROM screen_picks").fetchone()[0]
    open_n = conn.execute("SELECT COUNT(*) FROM screen_picks WHERE outcome='OPEN'").fetchone()[0]
    resolved = conn.execute("SELECT COUNT(*) FROM screen_picks WHERE outcome IN ('WIN','LOSS')").fetchone()[0]
    return {
        "total_recorded": total,
        "open": open_n,
        "resolved": resolved,
        "resolved_this_run": {"count": 0, "wins": 0, "losses": 0, "details": []},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Record and resolve the daily screen's picks.")
    parser.add_argument("--record", action="store_true", help="Record today's top list (+ screen_picks.json if present)")
    parser.add_argument("--rank", default=str(RANK_FILE), help="Path to screen_rank_out.json")
    parser.add_argument("--picks", default=str(PICKS_FILE), help="Path to screen_picks.json (the daily prompt's output)")
    parser.add_argument("--offline", action="store_true", help="Skip network resolution (rows stay OPEN)")
    args = parser.parse_args()

    conn = connect()
    try:
        resolved = resolve_open(conn, offline=args.offline)

        rec: dict[str, Any] = {"recorded": 0, "updated": 0, "warnings": []}
        if args.record:
            try:
                rank = load_json(args.rank)
            except FileNotFoundError:
                rank = None
            try:
                picks = load_json(args.picks)
            except FileNotFoundError:
                picks = {"picks": []}
            if rank is not None and "error" not in rank:
                rec = record_today(conn, rank, picks)
            else:
                rec["warnings"].append(
                    f"rank file not found or errored: {args.rank} — nothing recorded"
                )

        total = conn.execute("SELECT COUNT(*) FROM screen_picks").fetchone()[0]
        open_n = conn.execute("SELECT COUNT(*) FROM screen_picks WHERE outcome='OPEN'").fetchone()[0]
        resolved_n = conn.execute("SELECT COUNT(*) FROM screen_picks WHERE outcome IN ('WIN','LOSS')").fetchone()[0]
        realized_inr = conn.execute(
            "SELECT COALESCE(SUM(pnl_inr), 0) FROM screen_picks WHERE outcome IN ('WIN','LOSS') AND pnl_inr IS NOT NULL"
        ).fetchone()[0]

        journal = render_learning_journal(conn)
        JOURNAL_FILE.write_text(journal, encoding="utf-8")

        payload = {
            "generated_on": date.today().isoformat(),
            "resolved_this_run": {
                "count": len(resolved),
                "wins": sum(1 for r in resolved if r["outcome"] == "WIN"),
                "losses": sum(1 for r in resolved if r["outcome"] == "LOSS"),
                "details": resolved,
            },
            "recording": rec,
            "track_record": {
                "total_recorded": total,
                "open": open_n,
                "resolved": resolved_n,
                "realized_pnl_inr": round(realized_inr, 2),
                "capital_return_pct": round(realized_inr / CAPITAL_INR * 100.0, 2),
                "capital_inr": CAPITAL_INR,
            },
            "calibration": calibration(conn),
            "sentiment_audit": sentiment_audit(conn),
            "verdict_accuracy": verdict_accuracy(conn),
            "learning_journal": str(JOURNAL_FILE.relative_to(PROJECT_ROOT)),
        }
        emit_json(payload)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
