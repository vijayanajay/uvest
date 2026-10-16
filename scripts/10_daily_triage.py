#!/usr/bin/env python3
"""UVEST stage 10 — Daily Position Triage & Adverse Movement Tracker (TICK-011).

Owns the trade store (`uvest_history.db`) and the daily stewardship loop's
**macro-premise half** (BRD §11.1 #1 calls it premise verification: "sector MRS
leading" is one of the day-1 premises). Two actions:

* `--action track` — the canonical chain invocation (BRD §9.1 rows 10 and the
  pipeline block). Freezes the day-1 thesis into the `trades` table exactly
  once (BRD §11: "the Day-1 thesis is frozen in `uvest_history.db`"), then
  audits it against the regime evidence the same run already produced
  (`--payload out_7_regime.json` / `out_9_decision.json`). Offline: no
  network, no second fetcher.
* `--action triage` — the daily mode. For **every open tracked position** it
  reruns stage 7 against live market context, one bundle per position (the
  position's own symbol decides its sectoral index), records the audit in
  `triage_history`, and publishes the aggregate report. `--action status` is
  accepted as a documented alias (the vocabulary the `daily-triage` skill
  prints).

**Rerunning stage 7 means running it**: the mode spawns
`scripts/7_sector_macro_regime.py` through its frozen CLI with a freshly
fetched `uvest-market-context/1` bundle, exactly as the chain runs it, and
transcribes the payload it returns. No regime arithmetic is re-implemented
here, and none is invented.

**What this stage does NOT compute, and says so.** TICK-011's other half needs
the position's own price path (drawdown in ATR, close vs 20 EMA, delivery
Z-score) — evidence this mode does not fetch. Those keys (`adverse_level`,
`trailing_stop_update`) are emitted `null` and every row names them in
`pending_inputs`, the same disclosure idiom stage 7 uses for `missing_inputs`
and stage 9 for `confidence_deficit`: a gap is named, never silently zeroed.
A waived regime contributes no premise change (BRD §10.4 — absence never
fabricates a rejection) and is disclosed as `regime:waived` in the row.

**Macro-premise mapping (documented, deterministic).** The day-1 premise BRD
§11.1 #1 names is "sector MRS leading", so its RRG quadrant is premise
evidence, and both of the other signals are macro premise evidence too. From
one regime payload:

    broken  := panic_regime_breaker OR vix_regime == PANIC
               OR sector_rrg_state == LAGGING
    weaken  := breadth_regime == RISK_OFF OR fii_dii_flow_bias == NET_SELLING
               OR sector_rrg_state in {WEAKENING, IMPROVING}

Every frozen RRG token is accounted for: LEADING is the premise holding, the
two rolling quadrants weaken it, LAGGING breaks it. `LIQUIDATE` when broken
(BRD §10.3 #6 forces AVOID on every long setup, and §11.2's Level 3 liquidates
on premise invalidation), `TIGHTEN_STOP` when it only weakened (§11.2's Level
2 premise warning), otherwise `HOLD`. The aggregate published at the top level
is the most severe action across the open positions. `TRAIL_STOP` is the
profit-harvesting action and belongs to the price half, so this mode never
emits it.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sqlite3
import subprocess
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _recorder import (  # noqa: E402
    add_record_arg,
    bundle_name,
    record_artifact,
    resolve_bundle_dir,
)

ERR_PARSE = "ERR_PARSE"
ERR_STORE = "ERR_STORE"

REPO_ROOT = Path(__file__).resolve().parent.parent
STAGE7_SCRIPT = Path(__file__).resolve().parent / "7_sector_macro_regime.py"

REQUIRED_FIELDS = ["symbol", "live_price", "stoploss", "target"]

DB_NAME = "uvest_history.db"
STORE_SCHEMA = "uvest-history/1"

ACTIONS = ("track", "triage", "status")
TRIAGE_ALIASES = {"status": "triage"}

# The macro-premise verdict, most severe first — the aggregate is the first
# entry any open row's action matches.
SEVERITY = ("LIQUIDATE", "TIGHTEN_STOP", "HOLD")

# Evidence the price half of BRD §11 needs and this mode cannot see. Named on
# every row so a reader can tell "not computed" from "computed as nothing".
PENDING_INPUTS = (
    "position_price:drawdown_vs_entry",
    "atr_14:drawdown_in_atr",
    "close_vs_ema20:premise_evidence",
    "delivery_zscore:premise_evidence",
)

# The frozen TICK-011 §2 payload contract — exactly these keys, no more.
PAYLOAD_KEYS = (
    "open_trades_status", "action_required", "trailing_stop_update",
    "premise_invalidated", "adverse_level",
)

TRADES_DDL = """
CREATE TABLE IF NOT EXISTS trades (
    trade_id        TEXT PRIMARY KEY,
    symbol          TEXT NOT NULL,
    instrument_type TEXT NOT NULL,
    entry_price     REAL NOT NULL,
    stoploss        REAL NOT NULL,
    target          REAL NOT NULL,
    lot_size        INTEGER,
    prior_p_win     REAL,
    tracked_on      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'OPEN',
    last_triage_on  TEXT,
    last_action     TEXT
)
"""

TRIAGE_DDL = """
CREATE TABLE IF NOT EXISTS triage_history (
    trade_id              TEXT NOT NULL,
    audited_on            TEXT NOT NULL,
    action_required       TEXT NOT NULL,
    premise_invalidated   INTEGER NOT NULL,
    vix_regime            TEXT,
    breadth_regime        TEXT,
    nifty_trend           TEXT,
    sector_rrg_state      TEXT,
    panic_regime_breaker  INTEGER,
    regime_waived         INTEGER NOT NULL,
    PRIMARY KEY (trade_id, audited_on)
)
"""


def die(code: str, exit_code: int, hint: str = "") -> None:
    print(f"{code}: {hint}" if hint else code, file=sys.stderr)
    sys.exit(exit_code)


# --------------------------------------------------------------------------
# The trade store (BRD §11)
# --------------------------------------------------------------------------

def open_store(path: str | Path, *, create: bool) -> sqlite3.Connection:
    """Open (and, when asked, initialize) `uvest_history.db`."""
    db_path = Path(path)
    if not create and not db_path.exists():
        die(ERR_STORE, 1, f"trade store not found: {db_path} — log a trade "
                          f"with `--action track` before running triage")
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    if create:
        connection.execute(TRADES_DDL)
        connection.execute(TRIAGE_DDL)
        connection.commit()
    return connection


def trade_id_for(symbol: str, recommended_date: str | None) -> str:
    """`{SYMBOL}_{YYYYMMDD}` — the repo's per-trade identifier, reused.

    The replay bundle (`_recorder.bundle_name`) and the report both name a
    trade this way, so TICK-012's `--resolve <TRADE_ID>` addresses the same
    row the bundle was recorded for.
    """
    return bundle_name(str(symbol).upper(), recommended_date)


def track_trade(connection: sqlite3.Connection, payload: dict,
                prior_p_win: float | None) -> str:
    """Freeze the day-1 thesis — exactly once (BRD §11).

    A second `track` for the same trade is a no-op on the frozen geometry: the
    entry the thesis was judged on cannot be quietly rewritten later.
    """
    symbol = str(payload["symbol"]).strip().upper()
    trade_id = trade_id_for(symbol, payload.get("recommended_date"))
    tracked_on = str(payload.get("recommended_date")
                     or datetime.now().strftime("%Y-%m-%d"))
    connection.execute(
        "INSERT OR IGNORE INTO trades (trade_id, symbol, instrument_type, "
        "entry_price, stoploss, target, lot_size, prior_p_win, tracked_on, "
        "status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')",
        (trade_id, symbol, str(payload.get("instrument_type") or "CASH"),
         float(payload["live_price"]), float(payload["stoploss"]),
         float(payload["target"]),
         int(payload["lot_size"]) if payload.get("lot_size") is not None else None,
         prior_p_win, tracked_on),
    )
    connection.commit()
    return trade_id


def open_positions(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(connection.execute(
        "SELECT * FROM trades WHERE status = 'OPEN' ORDER BY trade_id"))


def record_audit(connection: sqlite3.Connection, trade_id: str, audited_on: str,
                 action: str, premise_invalidated: bool, regime: dict | None,
                 regime_waived: bool) -> None:
    regime = regime or {}
    connection.execute(
        "INSERT OR REPLACE INTO triage_history (trade_id, audited_on, "
        "action_required, premise_invalidated, vix_regime, breadth_regime, "
        "nifty_trend, sector_rrg_state, panic_regime_breaker, regime_waived) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (trade_id, audited_on, action, int(premise_invalidated),
         regime.get("vix_regime"), regime.get("breadth_regime"),
         regime.get("nifty_trend"), regime.get("sector_rrg_state"),
         None if "panic_regime_breaker" not in regime
         else int(bool(regime["panic_regime_breaker"])),
         int(regime_waived)),
    )
    connection.execute(
        "UPDATE trades SET last_triage_on = ?, last_action = ? "
        "WHERE trade_id = ?", (audited_on, action, trade_id))
    connection.commit()


# --------------------------------------------------------------------------
# The macro-premise verdict (documented mapping, no price half)
# --------------------------------------------------------------------------

def audit_regime(regime: dict | None) -> tuple[str, bool]:
    """(action_required, premise_invalidated) from one regime payload.

    A waived/absent payload carries no premise change — absence never
    fabricates a rejection (BRD §10.4). The caller discloses the gap.
    """
    if not regime or regime.get("waived"):
        return "HOLD", False
    broken = bool(regime.get("panic_regime_breaker")) \
        or regime.get("vix_regime") == "PANIC" \
        or regime.get("sector_rrg_state") == "LAGGING"
    if broken:
        return "LIQUIDATE", True
    weakened = regime.get("breadth_regime") == "RISK_OFF" \
        or regime.get("fii_dii_flow_bias") == "NET_SELLING" \
        or regime.get("sector_rrg_state") in ("WEAKENING", "IMPROVING")
    return ("TIGHTEN_STOP", False) if weakened else ("HOLD", False)


def aggregate_action(rows: list[dict]) -> str:
    """The most severe action across the open positions (SEVERITY order)."""
    for action in SEVERITY:
        if any(row["action_required"] == action for row in rows):
            return action
    return "HOLD"


def build_row(trade: sqlite3.Row, regime: dict | None, audited_on: str) -> dict:
    action, invalidated = audit_regime(regime)
    pending = list(PENDING_INPUTS)
    if not regime or regime.get("waived"):
        pending.append("regime:waived")
    return {
        "trade_id": trade["trade_id"],
        "symbol": trade["symbol"],
        "instrument_type": trade["instrument_type"],
        "entry_price": trade["entry_price"],
        "stoploss": trade["stoploss"],
        "target": trade["target"],
        "tracked_on": trade["tracked_on"],
        "audited_on": audited_on,
        "action_required": action,
        "premise_invalidated": invalidated,
        "adverse_level": None,
        "regime": regime,
        "pending_inputs": pending,
    }


def build_payload(rows: list[dict]) -> dict:
    """Exactly the TICK-011 §2 contract keys."""
    return {
        "open_trades_status": rows,
        "action_required": aggregate_action(rows),
        "trailing_stop_update": None,
        "premise_invalidated": any(r["premise_invalidated"] for r in rows),
        "adverse_level": None,
    }


# --------------------------------------------------------------------------
# Rerunning stage 7 (the real stage, through its frozen CLI)
# --------------------------------------------------------------------------

def rerun_stage7(symbol: str, as_of: str | None, evidence: str | None) -> dict | None:
    """Run `7_sector_macro_regime.py` for one symbol against `evidence`.

    `evidence` is a `uvest-market-context/1` bundle path, or None to let the
    fetcher build one live (tier-1 sources, one bundle per position — the
    position's own symbol decides which sectoral index it carries). Returns
    the stage's payload verbatim, or None when the rerun could not be made.
    """
    bundle = evidence
    if bundle is None:
        try:
            import fetch_market_context as fmc
            with tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / f"context_{symbol}.json"
                argv = ["--symbol", symbol, "--output", str(output)]
                if as_of:
                    argv += ["--as-of", as_of]
                print(f"[FETCH] market context: {symbol} as of "
                      f"{as_of or 'today'} (tier-1: Yahoo + NSE)", file=sys.stderr)
                # The fetcher reports its per-source progress on stdout. Here it
                # runs *inside* a stage whose stdout is the artifact, so that
                # chatter is redirected to stderr: the payload on stdout must
                # stay parseable JSON (the runner refuses a stage whose stdout
                # is not), while the disclosure survives on the log.
                with contextlib.redirect_stdout(sys.stderr):
                    fetch_code = fmc.main(argv)
                if fetch_code != 0 or not output.exists():
                    print(f"[WARN] no live bundle for {symbol} — stage 7 takes "
                          "the waiver path", file=sys.stderr)
                    return None
                bundle = json.loads(output.read_text(encoding="utf-8"))
        except (Exception, SystemExit) as exc:  # noqa: BLE001 — a fetch failure
            print(f"[WARN] market-context fetch failed for {symbol}: "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)
            return None
    return run_stage7_cli(symbol, bundle)


def run_stage7_cli(symbol: str, bundle) -> dict | None:
    """Spawn stage 7 with `--input`/`--regime-fixture`; parse its stdout."""
    with tempfile.TemporaryDirectory() as tmp:
        input_path = Path(tmp) / "current_trade.json"
        input_path.write_text(json.dumps({"symbol": symbol}), encoding="utf-8")
        if isinstance(bundle, (str, Path)):
            # Absolute before the spawn: stage 7 runs from the repo root, so a
            # caller-relative bundle path would silently miss and waive.
            fixture = str(Path(bundle).resolve())
        else:
            fixture = str(Path(tmp) / "bundle.json")
            Path(fixture).write_text(json.dumps(bundle), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(STAGE7_SCRIPT), "--input", str(input_path),
             "--regime-fixture", fixture],
            capture_output=True, text=True, encoding="utf-8", check=False,
            cwd=str(REPO_ROOT))
        if proc.returncode != 0:
            print(f"[WARN] stage 7 rerun failed for {symbol} "
                  f"(exit {proc.returncode}): {proc.stderr.strip()[:200]}",
                  file=sys.stderr)
            return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(f"[WARN] stage 7 rerun for {symbol} emitted no payload",
              file=sys.stderr)
        return None


# --------------------------------------------------------------------------
# Actions
# --------------------------------------------------------------------------

def load_payload(path: str) -> dict:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        die(ERR_PARSE, 1, f"input file not found: {path}")
    except json.JSONDecodeError as exc:
        die(ERR_PARSE, 1, f"input is not valid JSON: {exc}")
    if not isinstance(payload, dict):
        die(ERR_PARSE, 1, "input must be a JSON object")
    missing = [k for k in REQUIRED_FIELDS if payload.get(k) is None]
    if missing:
        die(ERR_PARSE, 1, f"missing required field(s): {', '.join(missing)}")
    return payload


def payloads_by_name(paths: list[str]) -> dict[str, dict]:
    documents: dict[str, dict] = {}
    for path in paths:
        try:
            document = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(document, dict):
            documents[Path(path).name] = document
    return documents


def do_track(connection: sqlite3.Connection, payload: dict,
             documents: dict[str, dict], audited_on: str) -> dict:
    """Record the day-1 thesis, audit it against this run's own evidence."""
    decision = documents.get("out_9_decision.json") or {}
    prior = decision.get("p_win")
    trade_id = track_trade(connection, payload,
                           float(prior) if prior is not None else None)
    regime = documents.get("out_7_regime.json")
    trade = connection.execute("SELECT * FROM trades WHERE trade_id = ?",
                               (trade_id,)).fetchone()
    row = build_row(trade, regime, audited_on)
    record_audit(connection, trade_id, audited_on, row["action_required"],
                 row["premise_invalidated"], regime,
                 bool(regime and regime.get("waived")))
    return build_payload([row])


def do_triage(connection: sqlite3.Connection, evidence: str | None,
              as_of: str | None, audited_on: str) -> dict:
    """Rerun stage 7 against live context for every open tracked position."""
    rows = []
    for trade in open_positions(connection):
        symbol = trade["symbol"]
        print(f"[TRIAGE] {trade['trade_id']}: rerunning stage 7 for {symbol}",
              file=sys.stderr)
        regime = rerun_stage7(symbol, as_of, evidence)
        row = build_row(trade, regime, audited_on)
        record_audit(connection, trade["trade_id"], audited_on,
                     row["action_required"], row["premise_invalidated"],
                     regime, bool(regime and regime.get("waived")))
        rows.append(row)
    return build_payload(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="UVEST Stage 10: Daily Position Triage & Adverse Movement Tracker")
    parser.add_argument("--action", default="track", choices=ACTIONS,
                        help="track (record a confirmed trade, the chain's "
                             "invocation), triage (daily loop over every open "
                             "position) or its documented alias 'status'")
    parser.add_argument("--input", default="current_trade.json",
                        help="Path to canonical current_trade.json")
    parser.add_argument("--payload", action="append", default=[],
                        help="An upstream out_*.json artifact this run already "
                             "produced (out_7_regime.json for the audit, "
                             "out_9_decision.json for the entry prior)")
    parser.add_argument("--db", default=DB_NAME,
                        help=f"trade store path (default: {DB_NAME})")
    parser.add_argument("--context-fixture", default=None,
                        help="Recorded uvest-market-context/1 bundle applied to "
                             "every position (offline triage); omit to fetch a "
                             "live bundle per position")
    parser.add_argument("--as-of", default=None,
                        help="Session date the live bundles are truncated at "
                             "(default: today)")
    add_record_arg(parser)

    args = parser.parse_args()
    action = TRIAGE_ALIASES.get(args.action, args.action)
    documents = payloads_by_name(args.payload)
    audited_on = args.as_of or date.today().isoformat()

    if action == "track":
        payload = load_payload(args.input)
        connection = open_store(args.db, create=True)
        result = do_track(connection, payload, documents, audited_on)
        symbol = str(payload.get("symbol") or "UNSPECIFIED").upper()
        recommended_date = payload.get("recommended_date")
    else:
        connection = open_store(args.db, create=False)
        result = do_triage(connection, args.context_fixture, args.as_of,
                           audited_on)
        symbol, recommended_date = "TRIAGE", audited_on
    connection.close()

    output_str = json.dumps(result, indent=2) + "\n"
    bundle = resolve_bundle_dir(args.record, symbol, recommended_date)
    if bundle is not None:
        record_artifact(bundle, 10, output_str)

    statuses = result["open_trades_status"]
    # ASCII only: a Windows console codepage would emit a byte a UTF-8 reader
    # (the runner's harness) cannot decode, and this line is read by tooling.
    print(f"[stage 10] {action}: {len(statuses)} position(s) audited - "
          f"action {result['action_required']}", file=sys.stderr)
    sys.stdout.write(output_str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
