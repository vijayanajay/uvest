#!/usr/bin/env python3
"""Curator — seed golden-eval records from recorded stage-2 bundles (EVAL-001).

Evidence rule (Zero Hallucination): every `out_1_math.json` / `out_2_market.json`
payload embedded in the dataset is a recorded pipeline stdout for that record's
canonical trade — never hand-typed. A record whose artifacts the proof root owns
still carries no copy at all: it holds `{"$ref": "tests/proof/…"}` and the bytes
are read from their one frozen home (`tests/evals/dataset_refs.py`). The
extract-a-subset path below remains only for records with no proof-root bundle,
and disappears as those bundles are recorded (`docs/agents/scenario-manifest-schema.md` §8). Trades are *authored* (no Univest archive
exists), but prices are real (yfinance daily bars, one fetch per symbol-month,
cached under `tests/fixtures/history/`), ATR sizing derives from stage 2's own
frozen convention, and forward outcomes come from the symbol's real closes.

Class plan against the frozen quotas (meta.class_counts_expected, 50 total):
  clean_bullish 15 · inverted_rr 10 · tight_stop 5   ← fillable from stages 0–2
  pledge_hazard 5 · earnings_hazard 5  ← seeded from the frozen catalog's own
      hazard rows with recorded stage-6 evidence (tests/evals/seed_hazard_records.py,
      which authors the fixtures, records `out_6_fundamental.json` and adds the
      records); nothing here hand-types a breaker value the runner would grade.
  institutional_distribution 10  ← its class breaker is not in the runner's
      registry, so the slots stay untouched (honest [SKIP] / mid-curation).

Existing records: 5 kept as committed (GB-2022-0001 … GB-2025-0005); the single
hand-typed tight_stop record (GB-2025-0005) is REBASED onto recorded evidence
below. Final dataset: 40/50 records.

Outcome conventions frozen here (derived from data, never asserted by fiat):
  Resolution: the frozen order-aware rule (`dataset_refs.resolution_of`, the
  same rule the dataset's `$derive` resolves through) — bars are scanned in
  order and the first of stop/target to print decides; a bar carrying both
  levels resolves to the worse one (LOSS), because daily bars cannot order
  touches within a bar. Until 2026-10-11 the seed scan was window-level (any
  stop touch inside the window was a LOSS); the two disagreed exactly when the
  target printed first and both levels sat inside the window, and the frozen
  rule now governs both admission and the stored resolution.
  Class acceptance scans: clean_bullish accepts WIN forwards only,
  inverted_rr and tight_stop accept LOSS forwards only (matching each class's
  historical characterization in docs/agents/evals.md §2).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import dataset_refs as dr   # the frozen resolution rule, shared with $derive

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS = REPO_ROOT / "scripts"
HIST_DIR = REPO_ROOT / "tests" / "fixtures" / "history"
DATASET_FILE = REPO_ROOT / "tests" / "evals" / "golden_dataset.json"

FORWARD = 22            # sessions (BRD §5.1 forward window)
MIN_SESSIONS = 200      # stage 2's frozen floor (TICK-013)
WINDOW_DAYS = 330       # calendar days fetched back from the rec date (≥200 sessions)

# Liquid names only (alnum symbols; lots are the real NSE contract sizes).
LOT_SIZES = {"RELIANCE": 250, "TATASTEEL": 550, "SBIN": 750, "ITC": 1200}
SYMBOLS = ["RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN", "INFY", "TCS", "LT",
           "TATASTEEL", "ITC", "TITAN", "MARUTI", "AXISBANK",
           "BAJFINANCE", "SUNPHARMA"]
ANCHORS = ["2022-04-15", "2022-09-15", "2023-03-15", "2023-10-15",
           "2024-04-15", "2024-09-15", "2025-01-15", "2025-05-15", "2025-10-15"]

# Class-shape parameters (curation choices, engine values remain authoritative).
CLEAN = {"risk_atr": 2.0, "rr": 2.2}      # stop = live − 2.0·ATR; target ⇒ live_rr 2.2
INVERTED = {"risk_atr": 1.2, "rr": 0.9}   # ratio 1.2 ⇒ NOT tight; live_rr 0.9
TIGHT = {"risk_atr": 0.6, "rr": None}     # ratio 0.6 < 0.75 ⇒ tight breaker fires
REBASE_TIGHT = {"risk_atr": 0.6, "rr": None}  # GB-2025-0005 rebase (same shape)


def fetch(symbol: str, start: str, end: str) -> list[dict] | None:
    try:
        import yfinance as yf
        import pandas as pd
    except ImportError:
        print("yfinance/pandas not available; curation needs them once",
              file=sys.stderr)
        return None
    try:
        df = yf.download(f"{symbol}.NS", start=start, end=end, progress=False,
                         auto_adjust=False, threads=False)
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.dropna(subset=["Close"])
        sessions = []
        for idx, row in df.iterrows():
            sessions.append({
                "date": idx.strftime("%Y-%m-%d"),
                "open": round(float(row["Open"]), 4),
                "high": round(float(row["High"]), 4),
                "low": round(float(row["Low"]), 4),
                "close": round(float(row["Close"]), 4),
                "volume": int(row["Volume"]),
            })
        return sessions
    except Exception as exc:  # one-time curation fetch; tolerate and skip
        print(f"  fetch failed {symbol} {start}..{end}: {exc}", file=sys.stderr)
        return None


_DEAD_SYMBOLS: set[str] = set()  # memoized fetch failures (delisted, etc.)


def load_history(symbol: str, rec_date: str,
                 fetch_symbol: str | None = None) -> list[dict] | None:
    """Cached full window (prefix ≤ rec_date + 22-session forward) per symbol-month.

    `fetch_symbol` overrides only the network ticker (issuer renames): the cache
    file and payload keep the point-in-time `symbol`, and the payload records the
    ticker actually fetched in `source_ticker`.
    """
    if symbol in _DEAD_SYMBOLS:
        return None
    ym = rec_date[:7].replace("-", "")
    path = HIST_DIR / f"{symbol}_{ym}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))["sessions"]
    rec = date.fromisoformat(rec_date)
    start = (rec - timedelta(days=WINDOW_DAYS)).isoformat()
    end = (rec + timedelta(days=40)).isoformat()
    sessions = fetch(fetch_symbol or symbol, start, end)
    if not sessions:
        print(f"  {symbol}: no usable history (marked dead for this run)",
              file=sys.stderr)
        _DEAD_SYMBOLS.add(symbol)
        return None
    payload = {"symbol": symbol, "exchange": "NSE", "interval": "1d",
               "source": "yfinance", "rec_date": rec_date, "sessions": sessions}
    if fetch_symbol and fetch_symbol != symbol:
        payload["source_ticker"] = f"{fetch_symbol}.NS"
    HIST_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return sessions


def entry_band(live: float) -> tuple[float, float]:
    """Non-degenerate recommended band: ±0.1% around live (midpoint == live)."""
    half = max(round(live * 0.001, 2), 1.0)
    return round(live - half, 2), round(live + half, 2)


def atr14_simple_mean(sessions: list[dict]) -> float:
    """Stage 2's frozen convention mirror (curation sizing only)."""
    trs: list[float] = []
    prev_close: float | None = None
    for row in sessions:
        h, l = float(row["high"]), float(row["low"])
        if prev_close is None:
            trs.append(h - l)
        else:
            trs.append(max(h - l, abs(h - prev_close), abs(l - prev_close)))
        prev_close = float(row["close"])
    return sum(trs[-14:]) / 14.0


def split_history(sessions: list[dict], rec_date: str):
    """(prefix ≤ rec_date for stage 2, forward list strictly after rec_date)."""
    prefix = [s for s in sessions if s["date"] <= rec_date]
    forward = [s for s in sessions if s["date"] > rec_date][:FORWARD]
    return prefix, forward


def outcome(forward: list[dict], live: float, target: float, stop: float) -> dict | None:
    """What the forward window did, by the frozen order-aware rule.

    The resolution comes from `dataset_refs.resolution_of` — the very rule the
    dataset's `$derive` uses — so a record can never be admitted for one
    outcome and derive to another.
    """
    if len(forward) < FORWARD:
        return None
    hi = max(s["high"] for s in forward)
    lo = min(s["low"] for s in forward)
    res, _basis = dr.resolution_of(forward, stop, target)
    return {"resolution_date": forward[-1]["date"], "resolution_outcome": res,
            "forward_prices_22d": {"min": round(lo, 4), "max": round(hi, 4)}}


def run_stage(script: Path, argv: list[str]):
    proc = subprocess.run([sys.executable, str(script), *argv],
                          capture_output=True, text=True, encoding="utf-8",
                          check=False, cwd=REPO_ROOT)
    try:
        payload = json.loads(proc.stdout) if proc.stdout.strip() else None
    except json.JSONDecodeError:
        payload = None
    return payload, proc.returncode, proc.stderr.strip()


def record_for(trade_id: str, trade_class: str, symbol: str, rec_date: str,
               risk_atr: float, rr: float | None) -> dict | None:
    """Author the trade from real ATR, then record stages 1–2 for it."""
    sessions = load_history(symbol, rec_date)
    if sessions is None:
        return None
    prefix, forward = split_history(sessions, rec_date)
    if len(prefix) < MIN_SESSIONS or len(forward) < FORWARD:
        print(f"  {trade_id}: history too short ({len(prefix)}/{len(forward)})"
              f" for {symbol}@{rec_date}", file=sys.stderr)
        return None
    atr = atr14_simple_mean(prefix)
    if atr <= 0:
        return None
    live = round(prefix[-1]["close"], 2)
    stop = round(live - risk_atr * atr, 2)
    if stop <= 0:
        return None
    if rr is not None:
        target = round(live + rr * (live - stop), 2)
    else:
        target = round(live + 2.5 * (live - stop), 2)
    out = outcome(forward, live, target, stop)
    if out is None:
        return None

    band_min, band_max = entry_band(live)
    canonical = {
        "symbol": symbol, "exchange": "NSE", "instrument_type": "FUT",
        "action": "BUY",
        "entry_price": live, "entry_range_min": band_min, "entry_range_max": band_max,
        "live_price": live, "target": target, "stoploss": stop,
        "lot_size": LOT_SIZES.get(symbol, 250),
        "recommended_date": rec_date, "user_capital_inr": 500000.0,
    }
    with tempfile.TemporaryDirectory() as td:
        inp = Path(td) / "trade.json"
        inp.write_text(json.dumps(canonical), encoding="utf-8")
        fx = Path(td) / "ohlcv.json"
        fx.write_text(json.dumps({"sessions": prefix}), encoding="utf-8")
        stage1, c1, e1 = run_stage(SCRIPTS / "1_math_and_rr.py",
                                   ["--input", str(inp), "--record", td])
        stage2, c2, e2 = run_stage(SCRIPTS / "2_market_data.py",
                                   ["--input", str(inp), "--record", td,
                                    "--ohlcv-fixture", str(fx)])
    if stage1 is None or stage2 is None:
        print(f"  {trade_id}: stage failure ({c1}/{c2}) {e1} {e2}", file=sys.stderr)
        return None

    return {
        "trade_id": trade_id,
        "trade_class": trade_class,
        "symbol": symbol,
        "instrument": "FUT",
        "recommendation_date": rec_date,
        "resolution_date": out["resolution_date"],
        "resolution_outcome": out["resolution_outcome"],
        "entry_price": live,
        "stop_loss": stop,
        "target_price": target,
        "script_outputs": {
            # No `current_trade.json` echo: the canonical input is not a stage
            # output, and a subset typed into the dataset is not evidence (E1).
            # Gate 1 reads the record's own `instrument` field instead.
            "out_1_math.json": {
                "raw_rr": round(stage1["raw_rr"], 6),
                "live_rr": round(stage1["live_rr"], 6),
                "inverted_rr_flag": bool(stage1["inverted_rr_flag"]),
            },
            "out_2_market.json": {
                "atr_14": stage2["atr_14"],
                "sl_atr_ratio": round(stage2["sl_atr_ratio"], 6),
                "is_tight_sl": stage2["is_tight_sl"],
            },
        },
        "forward_prices_22d": out["forward_prices_22d"],
    }


def rebase_tight_stop(row: dict | None) -> dict | None:
    """Rebase the hand-typed GB-2025-0005 onto a recorded HDFCBANK bundle.

    The standing record's trade shape (entry 1655, target 1700) is kept; the
    hand-typed stop (1640) and ATR (22.0) are replaced by real values: stop is
    re-derived at live − 0.6·ATR(14) from the recorded history ending on the
    recommendation date, and every out_2_market field becomes stage 2's stdout.
    """
    rec_date = "2025-01-15"
    sessions = load_history("HDFCBANK", rec_date)
    if sessions is None:
        return None
    prefix, forward = split_history(sessions, rec_date)
    live = round(prefix[-1]["close"], 2)   # replaces the historic entry 1655 too
    atr = atr14_simple_mean(prefix)
    stop = round(live - REBASE_TIGHT["risk_atr"] * atr, 2)
    target = float(row["target_price"])
    out = outcome(forward, live, target, stop)
    if out is None:
        return None
    band_min, band_max = entry_band(live)
    canonical = {
        "symbol": "HDFCBANK", "exchange": "NSE", "instrument_type": "FUT",
        "action": "BUY", "entry_price": live, "entry_range_min": band_min,
        "entry_range_max": band_max, "live_price": live, "target": target,
        "stoploss": stop, "lot_size": 250, "recommended_date": rec_date,
        "user_capital_inr": 500000.0,
    }
    with tempfile.TemporaryDirectory() as td:
        inp = Path(td) / "trade.json"
        inp.write_text(json.dumps(canonical), encoding="utf-8")
        fx = Path(td) / "ohlcv.json"
        fx.write_text(json.dumps({"sessions": prefix}), encoding="utf-8")
        stage1, c1, _ = run_stage(SCRIPTS / "1_math_and_rr.py",
                                  ["--input", str(inp), "--record", td])
        stage2, c2, _ = run_stage(SCRIPTS / "2_market_data.py",
                                  ["--input", str(inp), "--record", td,
                                   "--ohlcv-fixture", str(fx)])
    if stage1 is None or stage2 is None:
        return None
    rebased = {
        "trade_id": "GB-2025-0005", "trade_class": "tight_stop",
        "symbol": "HDFCBANK", "instrument": "FUT",
        "recommendation_date": rec_date,
        "resolution_date": out["resolution_date"],
        "resolution_outcome": out["resolution_outcome"],
        "entry_price": live, "stop_loss": stop, "target_price": target,
        "script_outputs": {
            "out_1_math.json": {
                "raw_rr": round(stage1["raw_rr"], 6),
                "live_rr": round(stage1["live_rr"], 6),
                "inverted_rr_flag": bool(stage1["inverted_rr_flag"]),
            },
            "out_2_market.json": {
                "atr_14": stage2["atr_14"],
                "sl_atr_ratio": round(stage2["sl_atr_ratio"], 6),
                "is_tight_sl": stage2["is_tight_sl"],
            },
        },
        "forward_prices_22d": out["forward_prices_22d"],
    }
    # Class invariant: the tight-stop breaker must grade true on recorded evidence.
    assert rebased["script_outputs"]["out_2_market.json"]["is_tight_sl"] is True, (
        "GB-2025-0005 rebase failed the breaker: recorded is_tight_sl not true")
    return rebased


def main() -> int:
    data = json.loads(DATASET_FILE.read_text(encoding="utf-8"))
    trades = {t["trade_id"]: t for t in data["trades"]}

    # 1. Rebase the one hand-typed tight_stop record onto real evidence.
    rebased = rebase_tight_stop(trades.get("GB-2025-0005"))
    if rebased is None:
        print("rebase of GB-2025-0005 failed; keeping hand-typed record",
              file=sys.stderr)
    else:
        trades["GB-2025-0005"] = rebased
        print("rebased GB-2025-0005 onto recorded stage-2 evidence")

    # 2. Fill the three stage-0..2-supportable classes from the candidate scan.
    need = {"clean_bullish": 15, "inverted_rr": 10, "tight_stop": 5}
    have = {"clean_bullish": 0, "inverted_rr": 0, "tight_stop": 0}
    for t in trades.values():
        if t["trade_id"] == "GB-2025-0005":
            continue
        if t["trade_class"] in have:
            have[t["trade_class"]] += 1
    print("existing counts:", have)

    shapes = {"clean_bullish": (CLEAN, "WIN"), "inverted_rr": (INVERTED, "LOSS"),
              "tight_stop": (TIGHT, "LOSS")}
    candidates = [(sym, d) for d in ANCHORS for sym in SYMBOLS]
    counter = {"n": 5}
    for sym, rec_date in candidates:
        want = [cls for cls, n in need.items() if have[cls] < n]
        if not want:
            break
        sessions = load_history(sym, rec_date)
        if sessions is None:
            continue
        prefix, forward = split_history(sessions, rec_date)
        if len(prefix) < MIN_SESSIONS or len(forward) < FORWARD:
            continue
        atr = atr14_simple_mean(prefix)
        if atr <= 0:
            continue
        for cls in want:
            if have[cls] >= need[cls]:
                continue
            shape, want_outcome = shapes[cls]
            live = round(prefix[-1]["close"], 2)
            stop = round(live - shape["risk_atr"] * atr, 2)
            target = (round(live + shape["rr"] * (live - stop), 2)
                      if shape["rr"] else round(live + 2.5 * (live - stop), 2))
            out = outcome(forward, live, target, stop)
            if out is None or out["resolution_outcome"] != want_outcome:
                continue
            counter["n"] += 1
            tid = f"GB-{rec_date[:4]}-{counter['n']:04d}"
            row = record_for(tid, cls, sym, rec_date,
                             shape["risk_atr"], shape["rr"])
            if row is None:
                counter["n"] -= 1
                continue
            trades[tid] = row
            have[cls] += 1
            print(f"  added {tid} {cls} {sym}@{rec_date} -> "
                  f"{out['resolution_outcome']} (have {have})")
            break  # one record per candidate per pass

    final = [trades[k] for k in sorted(trades)]
    if any(h < n for h, n in zip(have.values(), need.values())):
        print("quota short: " + ", ".join(f"{c}:{have[c]}/{need[c]}"
                                          for c in need), file=sys.stderr)
        return 1
    data["trades"] = final
    notes = data.get("meta", {}).get("notes", [])
    notes = [n for n in notes
             if not re.match(r"^\d+ of 50 records", n.strip())]
    notes.append(
        "40 of 50 records: five of the six classes are seeded and their breakers "
        "grade from recorded stage-1/2/6 bundles (tests/fixtures/history/ and "
        "tests/proof/fixtures/). This curator fills the three stage-0..2 classes "
        "(clean_bullish 15/15, inverted_rr 10/10, tight_stop 5/5); the two hazard "
        "classes (pledge_hazard 5/5, earnings_hazard 5/5) are seeded from the "
        "frozen catalog's own hazard rows by tests/evals/seed_hazard_records.py, "
        "with an authored, disclosed stage-6 bundle per row. "
        "institutional_distribution 0/10 remains unauthored; every pre-proof "
        "record's legacy stage-6 payload is retired to absence (`waived`, the "
        "proof rows' declarations) and GB-2023-0003's is referenced from there as "
        "quarantined debt — disclosed, excluded from grading, and never grown "
        "(tests/evals/pin_legacy_records.py).")
    data["meta"]["notes"] = notes
    data["meta"]["generated_on"] = "2026-10-10"
    DATASET_FILE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"wrote dataset: {len(final)} records")
    return 0


if __name__ == "__main__":
    sys.exit(main())
