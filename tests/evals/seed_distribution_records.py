#!/usr/bin/env python3
"""Distribution seed — the `institutional_distribution` golden records (EVAL-001 §2).

    python tests/evals/seed_distribution_records.py              # dry run: scan + plan
    python tests/evals/seed_distribution_records.py --apply      # author fixtures, rows, records
    python tests/evals/seed_distribution_records.py --apply --force   # re-author in place

The last unseeded class asks for ten *Institutional Distribution Traps*:
**price rising on falling Futures OI and low Delivery % (<25%), resulting in
subsequent sharp sell-offs** (docs/agents/evals.md §2). No stage 0–2 artifact
carries that signature — it lives in the exchange's own daily files — so this
tool sources it the free-first way the BRD mandates (§10.3: NSE Bhavcopy
first) and authors the class end to end from evidence:

1. **The signature is measured, never asserted.** For every admissible `T_rec`
   (a cached-tape session with >= 200 prefix and 22 forward bars) the scan
   reads NSE's archives:

   * `sec_bhavdata_full_<DDMMYYYY>.csv` — the security-wise file, EQ series,
     carrying `DELIV_PER`, `PREV_CLOSE` and `CLOSE_PRICE`;
   * the F&O bhavcopy — the legacy `fo<DDMONYYYY>bhav.csv.zip` through its
     2024 cut-off, then the UDiFF `BhavCopy_NSE_FO_0_0_0_<YYYYMMDD>_F_0000.csv.zip`
     — carrying the front-month stock futures' `OPEN_INT`/`CHG_IN_OI`.

   A candidate qualifies only where those files say `price_change_pct > 0`,
   `delivery_pct < 25` and the front-month OI change `<= -5%` on that very
   session. Both files are day-verified before use (`DATE1`/`TIMESTAMP`/
   `TradDt` must equal the requested date): the equity archive serves the
   *previous* trading day's file when asked for a holiday, and a mismatched
   day is skipped, never silently used as if it were the session.

2. **The tape decides admission.** A candidate is admitted only when its own
   point-in-time tape (`tests/fixtures/history/`, one fetch per symbol-month
   through the curator's cache) realizes the class's documented outcome under
   the frozen **crisis** policy (`scaffold_catalog.POLICY`: the AVOID-class
   advisory entry at the T_rec session OPEN, 0.5x ATR stop, 2.0R target) —
   resolving `LOSS` first under the frozen order-aware rule
   (`dataset_refs.resolution_of`, the same rule `$derive` resolves through).
   A candidate whose forward window does not stop out first stays unauthored
   and is printed: the class is seeded from realized sell-offs or not at all,
   and the geometry is never retuned to manufacture one (E8).

3. **The evidence is recorded, not typed.** Each admitted row commits
   `fixtures/<id>/{current_trade,ohlcv_prefix,forward_tape,bhavcopy}.json`,
   then records real stdout for stages 1, 2 **and 5** through the repo's own
   `--record` mechanism (`5_institutional_flow.py --bhavcopy-fixture …`), so
   the class-defining numbers a reader would otherwise have to trust are the
   engine's own bytes — byte-compared to a fresh offline run by V10
   (`tests/test_proof_manifest.py`). The golden record then `$ref`s those
   artifacts and `$derive`s every tape outcome (E1: one copy, one provenance).

4. **The fixture discloses its sources.** `bhavcopy.json` carries the raw
   inputs stage 5 reads plus, under `authoring`, the exact source file names,
   archive URLs and their SHA-256, the 20-session delivery history behind the
   z-score, and the front-month contract rows — so every fixture number is
   recomputable from data committed beside it, and the class invariant
   (`SHORT_COVERING_TRAP` matrix, delivery < 25%, OI <= -5%) is checkable
   against the recorded payload rather than taken on trust.

What this tool deliberately does NOT do:

* **It never writes a breaker value.** `institutional_distribution` has no
  registered breaker in `run_evals.BREAKER_BY_CLASS` (the class is a matrix
  verdict, not a hard gate), so gate 2 discloses it as
  `no registered breaker (10 record(s), quota 10)` and the rule-exercise
  census is untouched — the frozen floor neither shrinks nor is inflated by
  records that grade no rule.
* **It never tunes geometry or picks outcomes.** The policy is chosen per
  class (`policy_for("AVOID", "LOSS")`), never per scenario; a candidate the
  tape refuses stays unauthored and the shortfall is printed.
* **It never touches the quarantine register** (E5's meter may only move on
  its own owner-visible command) and never re-freezes the catalog.
* **The scan needs its sources.** `data_cache/nse/` (gitignored) holds the raw
  NSE files and is refilled from the archives on a machine with network; the
  committed fixtures, rows and records are fully verifiable offline, which is
  what the seams test.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import shutil
import statistics
import subprocess
import sys
import time
import urllib.request
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "tests" / "evals"))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

import curate  # noqa: E402  (tape cache + frozen ATR convention)
import dataset_refs as dr  # noqa: E402  (the frozen resolution rules)
import scaffold_catalog as sc  # noqa: E402  (fixture/hash/baseline bookkeeping)

PROOF_ROOT = REPO_ROOT / "tests" / "proof"
GOLDEN = REPO_ROOT / "tests" / "evals" / "golden_dataset.json"
SCRIPTS = REPO_ROOT / "scripts"
CACHE_DIR = REPO_ROOT / "data_cache" / "nse"

CLASS = "institutional_distribution"
QUOTA = 10
FIXTURE_FILE = "bhavcopy.json"
FIXTURE_SCHEMA = "uvest-bhavcopy/1"
SEED_SOURCE = "distribution-seed/1"

# The class signature (docs/agents/evals.md §2), measured off the exchange files.
DELIVERY_MAX = 25.0      # low Delivery % (<25%)
OI_MAX = -5.0            # falling futures OI (stage 5's oi_falling arm)
DELIVERY_WINDOW = 20     # sessions behind the 20-day delivery z-score

# The dataset's roster summary is one note, replaced in place by each curation
# pass, never stacked (same rule curate.py and seed_hazard_records.py use).
COUNT_NOTE_RE = re.compile(r"^\d+ of \d+ records")
VERIFIED_NOTE_PREFIX = "distribution_verified=false until"

USER_AGENT = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://www.nseindia.com/",
}
EQUITY_URL = ("https://archives.nseindia.com/products/content/"
              "sec_bhavdata_full_{ddmmyyyy}.csv")
FO_LEGACY_URL = ("https://archives.nseindia.com/content/historical/DERIVATIVES/"
                 "{year}/{mon}/fo{ddmonyyy}bhav.csv.zip")
FO_UDIFF_URL = ("https://archives.nseindia.com/content/fo/"
                "BhavCopy_NSE_FO_0_0_0_{yyyymmdd}_F_0000.csv.zip")


class Blocked(Exception):
    """The evidence cannot support the slot — report it, never invent it."""


# --------------------------------------------------------------------------
# Sourcing: NSE archives, day-verified, cached under data_cache/nse/ (gitignored)
# --------------------------------------------------------------------------
def _cached_or_fetch(url: str, dest: Path) -> tuple[bytes | None, bool]:
    if dest.exists():
        return dest.read_bytes(), True
    try:
        request = urllib.request.Request(url, headers=USER_AGENT)
        data = urllib.request.urlopen(request, timeout=30).read()
    except Exception:
        return None, False
    time.sleep(0.2)  # polite: one archive at a time
    return data, False


def equity_session(day: date) -> dict | None:
    """The EQ-series security-wise file for exactly this session, or None.

    The archive serves the nearest previous session's file for a holiday, so
    `DATE1` must equal the requested day — a mismatched file is neither cached
    nor read (a stale day used as T_rec would poison every measurement).
    """
    url = EQUITY_URL.format(ddmmyyyy=day.strftime("%d%m%Y"))
    dest = CACHE_DIR / f"sec_{day.strftime('%Y%m%d')}.csv"
    raw, cached = _cached_or_fetch(url, dest)
    if raw is None:
        return None
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8", "replace"), newline=""),
                               skipinitialspace=True))
    if not rows:
        return None
    date1 = rows[0].get("DATE1")
    if date1 != day.strftime("%d-%b-%Y"):
        return None
    if not cached:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
    return {
        "file": dest.name,
        "url": url,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "date1": date1,
        "rows": {r["SYMBOL"]: r for r in rows if r.get("SERIES") == "EQ"},
    }


def _contract(row: dict, expiry_key: str, close_key: str,
              oi_key: str, chg_key: str) -> dict | None:
    try:
        return {
            "expiry": row[expiry_key],
            "close": float(row[close_key]),
            "open_interest": float(row[oi_key]),
            "change_in_oi": float(row[chg_key]),
        }
    except (KeyError, TypeError, ValueError):
        return None


def futures_session(day: date) -> dict | None:
    """Stock-futures contracts for this session: legacy bhavcopy, then UDiFF.

    Both are day-verified (`TIMESTAMP` / `TradDt` must equal the requested
    date); the legacy file ends in 2024 and the UDiFF file covers where it
    does not exist, so the two formats span the whole 2022–2026 benchmark.
    """
    iso = day.strftime("%Y%m%d")
    legacy_dest = CACHE_DIR / f"fo_{iso}.zip"
    legacy_url = FO_LEGACY_URL.format(year=day.year, mon=day.strftime("%b").upper(),
                                      ddmonyyy=day.strftime("%d%b%Y").upper())
    raw, cached = _cached_or_fetch(legacy_url, legacy_dest)
    if raw is not None:
        rows: list[dict] = []
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                name = z.namelist()[0]
                rows = list(csv.DictReader(
                    io.TextIOWrapper(z.open(name), "utf-8", newline=""),
                    skipinitialspace=True))
        except (zipfile.BadZipFile, OSError, csv.Error):
            rows = []
        stamp = (rows[0].get("TIMESTAMP") or "").strip().upper() if rows else ""
        if rows and stamp == day.strftime("%d-%b-%Y").upper():
            if not cached:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                legacy_dest.write_bytes(raw)
            contracts: dict[str, list[dict]] = {}
            for r in rows:
                if r.get("INSTRUMENT") != "FUTSTK":
                    continue
                got = _contract(r, "EXPIRY_DT", "CLOSE", "OPEN_INT", "CHG_IN_OI")
                if got is not None:
                    contracts.setdefault(r["SYMBOL"], []).append(got)
            if contracts:
                return {"format": "legacy", "file": legacy_dest.name,
                        "url": legacy_url,
                        "sha256": hashlib.sha256(raw).hexdigest(),
                        "contracts": contracts}

    udiff_dest = CACHE_DIR / f"fo_{iso}_new.zip"
    udiff_url = FO_UDIFF_URL.format(yyyymmdd=iso)
    raw, cached = _cached_or_fetch(udiff_url, udiff_dest)
    if raw is None:
        return None
    rows = []
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            name = z.namelist()[0]
            rows = list(csv.DictReader(
                io.StringIO(z.open(name).read().decode("utf-8", "replace"),
                            newline="")))
    except (zipfile.BadZipFile, OSError, csv.Error):
        return None
    if not rows or rows[0].get("TradDt") != day.isoformat():
        return None
    if not cached:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        udiff_dest.write_bytes(raw)
    contracts = {}
    for r in rows:
        if r.get("FinInstrmTp") != "STF":
            continue
        got = _contract(r, "XpryDt", "ClsPric", "OpnIntrst", "ChngInOpnIntrst")
        if got is not None:
            contracts.setdefault(r["TckrSymb"], []).append(got)
    if not contracts:
        return None
    return {"format": "udiff", "file": udiff_dest.name, "url": udiff_url,
            "sha256": hashlib.sha256(raw).hexdigest(), "contracts": contracts}


def _expiry_of(text: str) -> date | None:
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y", "%d-%b-%y", "%d-%b-%Y %H:%M:%S"):
        try:
            return datetime.strptime(str(text).strip(), fmt).date()
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------
# The scan: admissible T_rec days -> measured class signature
# --------------------------------------------------------------------------
def admissible_dates() -> list[str]:
    """Cached-tape sessions that can carry a row: >=200 prefix, >=22 forward.

    Derived from the committed per-anchor tape cache (the trading calendar all
    NSE symbols share), restricted to the anchor months whose symbol-month
    cache file is committed, so the grid itself is reproducible offline.
    """
    found: set[str] = set()
    for anchor in curate.ANCHORS:
        ym = anchor[:7].replace("-", "")
        path = curate.HIST_DIR / f"RELIANCE_{ym}.json"
        if not path.exists():
            continue
        sessions = json.loads(path.read_text(encoding="utf-8"))["sessions"]
        for s in sessions:
            if not s["date"].startswith(anchor[:7]):
                continue
            prefix = sum(1 for x in sessions if x["date"] <= s["date"])
            forward = sum(1 for x in sessions if x["date"] > s["date"])
            if prefix >= curate.MIN_SESSIONS and forward >= curate.FORWARD:
                found.add(s["date"])
    return sorted(found)


def signature(symbol: str, day: str, equity: dict, futures: dict) -> dict | None:
    """The measured class signature for one symbol-session, or None.

    `price_change_pct` comes from the equity file's own previous/current
    close, `delivery_pct` from its `DELIV_PER` (EQ series), and
    `oi_change_pct` from the nearest listed contract expiring after T_rec —
    `CHG_IN_OI / (OPEN_INT - CHG_IN_OI) * 100` (the session's OI change
    against the OI it opened with).
    """
    row = equity["rows"].get(symbol)
    listed = futures["contracts"].get(symbol) or []
    if row is None or not listed:
        return None
    try:
        prev_close = float(row["PREV_CLOSE"])
        close = float(row["CLOSE_PRICE"])
        delivery_pct = float(row["DELIV_PER"])
        delivery_qty = float(row["DELIV_QTY"])
        traded_qty = float(row["TTL_TRD_QNTY"])
    except (KeyError, TypeError, ValueError):
        return None
    if prev_close <= 0:
        return None
    d = date.fromisoformat(day)
    contracts = [(_expiry_of(c["expiry"]), c) for c in listed]
    contracts = [(exp, c) for exp, c in contracts if exp is not None and exp > d]
    if not contracts:
        return None
    expiry, contract = min(contracts, key=lambda pair: pair[0])
    prior_oi = contract["open_interest"] - contract["change_in_oi"]
    if prior_oi <= 0:
        return None
    price_change_pct = round((close - prev_close) / prev_close * 100.0, 4)
    oi_change_pct = round(contract["change_in_oi"] / prior_oi * 100.0, 4)
    if not (price_change_pct > 0 and delivery_pct < DELIVERY_MAX
            and oi_change_pct <= OI_MAX):
        return None
    return {
        "symbol": symbol,
        "date": day,
        "price_change_pct": price_change_pct,
        "delivery_pct": round(delivery_pct, 2),
        "oi_change_pct": oi_change_pct,
        "prev_close": prev_close,
        "close": round(close, 2),
        "delivery_qty": int(delivery_qty),
        "traded_qty": int(traded_qty),
        "expiry": expiry.isoformat(),
        "futures_close": contract["close"],
        "open_interest": contract["open_interest"],
        "change_in_oi": contract["change_in_oi"],
        "equity_file": {k: equity[k] for k in ("file", "url", "sha256", "date1")},
        "derivatives_file": {k: futures[k] for k in ("file", "url", "sha256", "format")},
    }


def occupied_pairs() -> set[tuple[str, str]]:
    """`(SYMBOL, date)` already claimed by a dataset record — its report name
    is `report_{SYMBOL}_{YYYYMMDD}.html`, so one pair may name one record."""
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    return {(str(t.get("symbol", "")).upper(), str(t.get("recommendation_date")))
            for t in data.get("trades", [])}


# --------------------------------------------------------------------------
# Tape admission: crisis geometry, LOSS first, no collisions
# --------------------------------------------------------------------------
def evaluate(cand: dict, taken: set[tuple[str, str]]) -> tuple[dict | None, str]:
    """Tape-check a measured candidate under the frozen crisis policy."""
    pair = (cand["symbol"].upper(), cand["date"])
    if pair in taken:
        return None, f"report name already claimed by an existing record {pair}"
    sessions = sc.load_tape(cand["symbol"], cand["date"])
    if sessions is None:
        return None, "no usable point-in-time tape"
    prefix, forward = curate.split_history(sessions, cand["date"])
    if len(prefix) < curate.MIN_SESSIONS:
        return None, f"prefix is {len(prefix)} sessions < {curate.MIN_SESSIONS}"
    if len(forward) < curate.FORWARD:
        return None, f"tape ends {len(forward)} sessions after T_rec"
    policy = sc.policy_for("AVOID", "LOSS")   # the class's frozen geometry
    entry = round(float(prefix[-1]["open"]), 2)
    atr = sc.atr14(prefix)
    stop = round(entry - policy["risk_atr"] * atr, 2)
    target = round(entry + policy["rr"] * (entry - stop), 2)
    if stop <= 0 or target <= entry:
        return None, f"non-positive geometry (entry {entry}, stop {stop})"
    outcome, basis = sc.resolve(forward, target, stop)
    if outcome != "LOSS":
        return None, (f"tape says {outcome} ({basis}) — the class documents a "
                      "subsequent sharp sell-off; unauthored, never retuned")
    cand.update({
        "entry_price": entry,
        "atr_14": atr,
        "stop_loss": stop,
        "target_price": target,
        "resolution_basis": basis,
        "resolution_date": forward[-1]["date"],
        "forward_min": min(b["low"] for b in forward),
        "forward_max": max(b["high"] for b in forward),
        "prefix_sessions": len(prefix),
        "forward_sessions": len(forward),
        "policy": policy,
    })
    return cand, (f"LOSS ({basis}) at entry {entry} / stop {stop} / "
                  f"target {target}")


def build_plan(limit: int = QUOTA) -> tuple[list[dict], list[tuple[dict, str]]]:
    """Scan -> measure -> tape-admit the first `limit` candidates, deterministically."""
    occupied = occupied_pairs()
    accepted: list[dict] = []
    refused: list[tuple[dict, str]] = []
    for day in admissible_dates():
        if len(accepted) >= limit:
            break
        equity = equity_session(date.fromisoformat(day))
        if equity is None:
            continue
        futures = futures_session(date.fromisoformat(day))
        if futures is None:
            continue
        for symbol in sorted(futures["contracts"]):
            if len(accepted) >= limit:
                break
            cand = signature(symbol, day, equity, futures)
            if cand is None:
                continue
            taken = set(occupied) | {(a["symbol"].upper(), a["date"])
                                     for a in accepted}
            admitted, why = evaluate(cand, taken)
            if admitted is None:
                refused.append((cand, why))
                continue
            accepted.append(admitted)
    return accepted, refused


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
def delivery_history(symbol: str, day: str) -> list[dict]:
    """The `DELIVERY_WINDOW` EQ sessions strictly before T_rec, sourced."""
    out: list[dict] = []
    cursor = date.fromisoformat(day) - timedelta(days=1)
    guard = 0
    while len(out) < DELIVERY_WINDOW and guard < 120:
        guard += 1
        if cursor.weekday() < 5:                      # skip weekends outright
            session = equity_session(cursor)
            if session is not None:
                row = session["rows"].get(symbol)
                if row is not None:
                    try:
                        out.append({"session": cursor.isoformat(),
                                    "delivery_pct": round(float(row["DELIV_PER"]), 2)})
                    except (KeyError, TypeError, ValueError):
                        pass
        cursor -= timedelta(days=1)
    if len(out) < DELIVERY_WINDOW:
        raise Blocked(f"{symbol}@{day}: only {len(out)} delivery sessions "
                      f"behind T_rec, {DELIVERY_WINDOW} required")
    out.reverse()                                     # chronological order
    return out


def fixture_doc(cand: dict) -> dict:
    """Stage 5's input bundle: the sourced numbers and their provenance."""
    history = delivery_history(cand["symbol"], cand["date"])
    values = [h["delivery_pct"] for h in history]
    days_to_expiry = (date.fromisoformat(cand["expiry"])
                      - date.fromisoformat(cand["date"])).days
    return {
        "schema": FIXTURE_SCHEMA,
        "symbol": cand["symbol"],
        "exchange": "NSE",
        "source": "nse-archives",
        # Point-in-time: everything the fixture states is as of T_rec.
        "as_of": cand["date"],
        "delivery_pct": cand["delivery_pct"],
        "delivery_mean_20d": round(statistics.fmean(values), 4),
        "delivery_std_20d": round(statistics.pstdev(values), 4),  # population, ddof=0
        "price_change_pct": cand["price_change_pct"],
        "spot_price": cand["close"],
        "futures_price": cand["futures_close"],
        "days_to_expiry": days_to_expiry,
        "oi_change_pct": cand["oi_change_pct"],
        "authoring": {
            "source": SEED_SOURCE,
            "trade_class": CLASS,
            "signature": {
                "rule": ("price_change_pct > 0 and delivery_pct < 25 and "
                         "oi_change_pct <= -5 on T_rec (docs/agents/evals.md §2)"),
                "price_change_pct": cand["price_change_pct"],
                "delivery_pct": cand["delivery_pct"],
                "oi_change_pct": cand["oi_change_pct"],
            },
            "equity_file": {
                **cand["equity_file"],
                "series": "EQ",
                "prev_close": cand["prev_close"],
                "close": cand["close"],
                "delivery_qty": cand["delivery_qty"],
                "traded_qty": cand["traded_qty"],
            },
            "derivatives_file": {
                **cand["derivatives_file"],
                "contract": {
                    "expiry": cand["expiry"],
                    "close": cand["futures_close"],
                    "open_interest": cand["open_interest"],
                    "change_in_oi": cand["change_in_oi"],
                },
            },
            "delivery_window": {
                "sessions": DELIVERY_WINDOW,
                "ddof": 0,
                "first": history[0]["session"],
                "last": history[-1]["session"],
            },
            "delivery_history": history,
            "rules": (
                "delivery_mean_20d/delivery_std_20d = population mean/stdev over "
                f"the {DELIVERY_WINDOW} EQ sessions strictly before T_rec "
                "(authoring.delivery_history); delivery_pct is T_rec's own "
                "DELIV_PER; price_change_pct = (close - prev_close)/prev_close*100; "
                "oi_change_pct = change_in_oi/(open_interest - change_in_oi)*100 "
                "on the nearest listed contract expiring after T_rec"
            ),
        },
    }


# --------------------------------------------------------------------------
# Proof row + recorded evidence
# --------------------------------------------------------------------------
def next_gb_seq(rows: list[dict]) -> int:
    nums = [int(str(r.get("scenario_id", "GB-0000-0000")).split("-")[2])
            for r in rows if str(r.get("scenario_id", "")).startswith("GB-")]
    return max(nums, default=0) + 1


def build_row(cand: dict, sid: str, *, today: str) -> dict:
    policy = cand["policy"]
    entry = cand["entry_price"]
    band_min, band_max = curate.entry_band(entry)
    return {
        "scenario_id": sid,
        "schema_version": sc.SCHEMA_NAME,
        "origin": "scan_generated",
        "created_on": today,
        "authoring": {
            "source": SEED_SOURCE,
            "class_signature": ("price rising on falling front-month futures OI "
                                "with delivery < 25% on T_rec, resolving in a "
                                "sharp sell-off (docs/agents/evals.md §2)"),
            "measured": {
                "price_change_pct": cand["price_change_pct"],
                "delivery_pct": cand["delivery_pct"],
                "oi_change_pct": cand["oi_change_pct"],
            },
            "sourcing": (
                "NSE archives, free-first (BRD §10.3): sec_bhavdata_full "
                "(EQ delivery %, prev/current close) + the F&O bhavcopy "
                "(front-month stock futures OI); both source files are "
                "day-verified and their sha256 recorded in the fixture"
            ),
            "policy": policy["why"],
            "entry_basis": policy["entry_basis"],
            "shape": {"risk_atr": policy["risk_atr"], "rr": policy["rr"]},
            "atr_14": cand["atr_14"],
            "expiry_rule": ("the nearest listed NSE stock-futures contract "
                            "expiry after T_rec, read from the F&O bhavcopy — "
                            "the listing itself, not a calendar convention"),
            "lot_size_rule": (f"₹{int(sc.LOT_NOTIONAL_INR)} notional band on the "
                              "authored entry, rounded to a 25-share grid; "
                              "historical NSE lot revisions are not archived "
                              "offline"),
            "realized_outcome_basis": cand["resolution_basis"],
            "distribution_evidence": {
                "fixture": f"fixtures/{sid}/{FIXTURE_FILE}",
                "delivery_pct": cand["delivery_pct"],
                "oi_change_pct": cand["oi_change_pct"],
                "price_change_pct": cand["price_change_pct"],
                "disclosure": (
                    f"Measured exchange inputs on {cand['date']} (EQ series): "
                    f"delivery {cand['delivery_pct']}% (<25%), front-month OI "
                    f"{cand['oi_change_pct']}% (<= -5%), price "
                    f"{cand['price_change_pct']}% (> 0) — the class signature, "
                    "sourced from NSE's own daily files (sha256 in the fixture) "
                    "and recorded through 5_institutional_flow.py, never typed"
                ),
            },
        },
        "classification_tags": ["DISTRIBUTION_SEED"],
        "gate_focus": ["ZERO_HALLUCINATION"],
        "symbol": cand["symbol"],
        "exchange": "NSE",
        "instrument": "FUT",
        "action": "BUY",
        "recommendation_date": cand["date"],
        "expiry": cand["expiry"],
        "entry_price": entry,
        "entry_range_min": band_min,
        "entry_range_max": band_max,
        "target_price": cand["target_price"],
        "stop_loss": cand["stop_loss"],
        "lot_size": sc.derive_lot_size(entry),
        "user_capital_inr": sc.USER_CAPITAL_INR,
        "replay_inputs": {
            "canonical_trade": f"fixtures/{sid}/current_trade.json",
            "truncated_fixtures": [f"fixtures/{sid}/ohlcv_prefix.json"],
            "bhavcopy_fixture": f"fixtures/{sid}/{FIXTURE_FILE}",
            "truncation": {"cutoff": cand["date"], "rule": "date <= T_rec",
                           "min_bars": curate.MIN_SESSIONS},
        },
        "recorded_artifacts": [
            {"stage": 1, "artifact": "out_1_math.json",
             "path": f"fixtures/{sid}/recorded/out_1_math.json",
             "evidence": "recorded"},
            {"stage": 2, "artifact": "out_2_market.json",
             "path": f"fixtures/{sid}/recorded/out_2_market.json",
             "evidence": "recorded"},
            {"stage": 5, "artifact": "out_5_flow.json",
             "path": f"fixtures/{sid}/recorded/out_5_flow.json",
             "evidence": "recorded"},
        ],
        "scoring": {
            "forward_tape": f"fixtures/{sid}/forward_tape.json",
            "bars": cand["forward_sessions"],
            "resolution_date": cand["resolution_date"],
            "resolution_outcome": "LOSS",
            "resolution_basis": cand["resolution_basis"],
            "forward_derived_cache": {"min": cand["forward_min"],
                                      "max": cand["forward_max"]},
            "quadrant_target": "TN",   # AVOID expected, LOSS realized
        },
        "expected_verdict": "AVOID",
    }


def record_stages(row: dict) -> bool:
    """Run stages 1, 2 and 5 through `--record` over the row's own fixtures."""
    sid = row["scenario_id"]
    fixture_dir = PROOF_ROOT / "fixtures" / sid
    bundle = fixture_dir / "recorded"
    if bundle.exists():
        shutil.rmtree(bundle)
    canonical = fixture_dir / "current_trade.json"
    prefix = fixture_dir / "ohlcv_prefix.json"
    bhavcopy = fixture_dir / FIXTURE_FILE
    stages = [
        ("1_math_and_rr.py",
         ["--input", str(canonical), "--record", str(bundle)]),
        ("2_market_data.py",
         ["--input", str(canonical), "--ohlcv-fixture", str(prefix),
          "--record", str(bundle)]),
        ("5_institutional_flow.py",
         ["--input", str(canonical), "--bhavcopy-fixture", str(bhavcopy),
          "--record", str(bundle)]),
    ]
    for script, argv in stages:
        proc = subprocess.run([sys.executable, str(SCRIPTS / script), *argv],
                              capture_output=True, text=True, encoding="utf-8",
                              check=False, cwd=REPO_ROOT)
        if proc.returncode != 0:
            print(f"  {sid}: {script} failed rc={proc.returncode} "
                  f"{proc.stderr.strip()}")
            return False
    return True


def check_recorded(row: dict) -> None:
    """The recorded stage-5 bytes must carry the class invariant they stand for."""
    sid = row["scenario_id"]
    declared = [a for a in row["recorded_artifacts"]
                if a["artifact"] == "out_5_flow.json"]
    if len(declared) != 1 or declared[0]["evidence"] != "recorded":
        raise Blocked(f"{sid}: the row declares no single recorded stage-5 artifact")
    payload = json.loads((PROOF_ROOT / declared[0]["path"]).read_text(encoding="utf-8"))
    if payload.get("waived"):
        raise Blocked(f"{sid}: recorded stage-5 payload is a waiver — the bhavcopy "
                      "bundle did not reach the engine")
    if payload.get("matrix_verdict") != "SHORT_COVERING_TRAP":
        raise Blocked(f"{sid}: recorded matrix_verdict "
                      f"{payload.get('matrix_verdict')!r} is not the class's "
                      "SHORT_COVERING_TRAP (price up + low delivery + falling OI)")
    if not payload.get("delivery_pct", 999.0) < DELIVERY_MAX:
        raise Blocked(f"{sid}: recorded delivery {payload.get('delivery_pct')} "
                      f"is not < {DELIVERY_MAX}")
    if not payload.get("oi_change_pct", 0.0) <= OI_MAX:
        raise Blocked(f"{sid}: recorded OI change {payload.get('oi_change_pct')} "
                      f"is not <= {OI_MAX}")
    measured = row["authoring"]["distribution_evidence"]
    if payload["delivery_pct"] != measured["delivery_pct"]:
        raise Blocked(f"{sid}: recorded delivery {payload['delivery_pct']} is not "
                      f"the measured {measured['delivery_pct']}")
    if payload["oi_change_pct"] != round(measured["oi_change_pct"], 2):
        raise Blocked(f"{sid}: recorded OI change {payload['oi_change_pct']} is "
                      f"not the measured {measured['oi_change_pct']}")


# --------------------------------------------------------------------------
# Golden record (a view of the row: refs for bytes, derives for tape)
# --------------------------------------------------------------------------
def build_record(row: dict) -> dict:
    sid = row["scenario_id"]
    scoring = row["scoring"]
    tape_rel = scoring["forward_tape"]
    bars = json.loads((PROOF_ROOT / tape_rel).read_text(encoding="utf-8"))["bars"]
    outcome, basis = dr.resolution_of(bars, float(row["stop_loss"]),
                                      float(row["target_price"]))
    if outcome != scoring["resolution_outcome"] or basis != scoring["resolution_basis"]:
        raise Blocked(f"{sid}: the frozen rule reads {outcome}/{basis} while the "
                      f"row stored {scoring['resolution_outcome']}/"
                      f"{scoring['resolution_basis']}")
    rule = dr.FORWARD_TAPE_RULES
    if rule["forward_min_max"](bars, row) != scoring["forward_derived_cache"]:
        raise Blocked(f"{sid}: derived forward range disagrees with the row's cache")
    if rule["resolution_date"](bars, row) != scoring["resolution_date"]:
        raise Blocked(f"{sid}: derived horizon end disagrees with the row's")

    tape = f"tests/proof/{tape_rel}"

    def ref(artifact: str) -> dict:
        return {"$ref": f"tests/proof/fixtures/{sid}/recorded/{artifact}"}

    def derive(rule_name: str) -> dict:
        return {"$derive": {"tape": tape, "rule": rule_name}}

    return {
        "trade_id": sid,
        "trade_class": CLASS,
        "symbol": row["symbol"],
        "instrument": row["instrument"],
        "recommendation_date": row["recommendation_date"],
        "resolution_date": derive("resolution_date"),
        "resolution_outcome": derive("resolution_outcome"),
        "entry_price": row["entry_price"],
        "stop_loss": row["stop_loss"],
        "target_price": row["target_price"],
        "script_outputs": {
            "out_1_math.json": ref("out_1_math.json"),
            "out_2_market.json": ref("out_2_market.json"),
            "out_5_flow.json": ref("out_5_flow.json"),
        },
        "forward_prices_22d": derive("forward_min_max"),
        "cone_p10": None,
        "cone_p90": None,
        "market_mae_pct": derive("market_mae_pct"),
        "limit_mae_pct": None,
        "predicted_direction": None,
        "actual_direction": derive("realized_direction"),
    }


# --------------------------------------------------------------------------
# Dataset notes
# --------------------------------------------------------------------------
def distribution_note(records: list[dict], total: int, expected: int) -> str:
    ids = ", ".join(sorted(r["trade_id"] for r in records))
    return (
        f"{total} of {expected} records: all six classes are seeded and every "
        "class count matches class_counts_expected "
        "(distribution_verified=true). The three geometry classes "
        "(clean_bullish 15/15, inverted_rr 10/10, tight_stop 5/5) carry "
        "recorded stage-1/2 evidence over their pinned <=T_rec tape; the two "
        "hazard classes (pledge_hazard 5/5, earnings_hazard 7/7) are views of "
        "the frozen catalog's own hazard rows (business_test_cases.md §3) with "
        "recorded stage-6 evidence over an authored, disclosed bundle — the "
        "catalog's documented level verbatim where it states one and a "
        "disclosed value above the stated floor where it states only the "
        "inequality, statement blocks deliberately empty so stage 6 reports "
        "null ratios with missing_inputs naming every gap (E4: absence, never "
        "an invented filing); GB-2023-0003's legacy pledge payload was retired "
        "into recorded stage-6 evidence (tests/evals/retire_quarantine_records."
        "py), so the E5 meter is 0 and gate 2 has no abstention left. "
        "institutional_distribution 10/10 (" + ids + "): scan-authored by "
        "tests/evals/seed_distribution_records.py from NSE's own daily files — "
        "sec_bhavdata_full delivery % (EQ series) and the F&O bhavcopy's "
        "front-month open interest on each T_rec — admitting only sessions the "
        "files measure as price up, delivery < 25% and OI <= -5% (the class "
        "signature of docs/agents/evals.md §2) whose pinned tape then resolves "
        "LOSS under the frozen crisis geometry (T_rec open, 0.5x ATR stop, "
        "2.0R target); every fixture discloses its source files' sha256 and a "
        "20-session delivery window, and stage 5 is recorded through "
        "`5_institutional_flow.py --record`, so the class invariant "
        "(SHORT_COVERING_TRAP matrix, delivery < 25%, OI <= -5%) grades from "
        "proven bytes. Authored by tests/evals/seed_distribution_records.py."
    )


def verified_note(total: int, expected: int) -> str:
    return (
        f"distribution_verified=true: all {expected} records are present and "
        "every class count matches class_counts_expected (flipped by "
        "tests/evals/seed_distribution_records.py when the tenth "
        "institutional_distribution row was authored)."
    )


# --------------------------------------------------------------------------
# Authoring flow
# --------------------------------------------------------------------------
def write_fixtures(row: dict, cand: dict, fixture: dict) -> None:
    fixture_dir = PROOF_ROOT / "fixtures" / row["scenario_id"]
    fixture_dir.mkdir(parents=True, exist_ok=True)
    sc.write_json(fixture_dir / "current_trade.json", sc.canonical_trade(row))
    sessions = sc.load_tape(row["symbol"], row["recommendation_date"])
    prefix, forward = curate.split_history(sessions, row["recommendation_date"])
    sc.write_json(fixture_dir / "ohlcv_prefix.json",
                  {"symbol": row["symbol"], "sessions": prefix}, indent=None)
    sc.write_json(fixture_dir / "forward_tape.json",
                  {"symbol": row["symbol"], "bars": forward}, indent=None)
    sc.write_json(fixture_dir / FIXTURE_FILE, fixture)


def print_plan(accepted: list[dict],
               refused: list[tuple[dict, str]]) -> None:
    print("=" * 72)
    print("DISTRIBUTION SEED PLAN — signature measured off NSE's own files")
    print("=" * 72)
    for cand in accepted:
        print(f"  {cand['scenario_id']} [{CLASS}] {cand['symbol']} FUT "
              f"@ {cand['date']}")
        print(f"    signature: price +{cand['price_change_pct']}% | delivery "
              f"{cand['delivery_pct']}% | front-month OI "
              f"{cand['oi_change_pct']}% -> expiry {cand['expiry']}")
        print(f"    tape: LOSS ({cand['resolution_basis']}) at entry "
              f"{cand['entry_price']} / stop {cand['stop_loss']} / target "
              f"{cand['target_price']}")
    if refused:
        print(f"\n  {len(refused)} measured candidate(s) refused before the "
              "quota filled (disclosed, never retuned):")
        for cand, why in refused:
            print(f"    · {cand['symbol']}@{cand['date']}: {why}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seed the institutional_distribution golden records from "
                    "NSE delivery/OI archives.")
    parser.add_argument("--apply", action="store_true",
                        help="Write fixtures, rows, hashes, baseline and the dataset.")
    parser.add_argument("--force", action="store_true",
                        help="Re-author even when the class quota is already met.")
    parser.add_argument("--limit", type=int, default=QUOTA,
                        help=f"class rows to admit (default {QUOTA})")
    args = parser.parse_args(argv)

    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    existing = [t for t in data["trades"] if t.get("trade_class") == CLASS]
    if len(existing) >= QUOTA and not args.force:
        print(f"{CLASS} already seeded ({len(existing)}/{QUOTA}); nothing to do "
              "(use --force to re-author in place)")
        return 0

    accepted, refused = build_plan(limit=args.limit)
    if len(accepted) < args.limit:
        print_plan(accepted, refused)
        print(f"\n⛔ only {len(accepted)}/{args.limit} candidate(s) survived the "
              "tape's LOSS check; nothing written", file=sys.stderr)
        return 1

    # Ids are assigned before anything is written, in deterministic order.
    rows_doc = json.loads((PROOF_ROOT / sc.MANIFEST_FILE).read_text(encoding="utf-8"))
    seq = next_gb_seq(rows_doc["scenarios"])
    for cand in accepted:
        cand["scenario_id"] = f"GB-{cand['date'][:4]}-{seq:04d}"
        seq += 1
    print_plan(accepted, refused)

    if not args.apply:
        print(f"\nDRY RUN — {len(accepted)} row(s) would be authored; "
              "nothing written")
        return 0

    today = date.today().isoformat()
    rows = {r["scenario_id"]: r for r in rows_doc["scenarios"]}
    authored: list[tuple[dict, dict]] = []
    for cand in accepted:
        sid = cand["scenario_id"]
        row = build_row(cand, sid, today=today)
        print(f"\n{sid}: authoring {cand['symbol']}@{cand['date']}")
        try:
            # The fixture is built first: its 20-session window is the one
            # thing the archives may not be able to cover for this candidate.
            fixture = fixture_doc(cand)
            write_fixtures(row, cand, fixture)
            if not record_stages(row):
                raise Blocked(f"{sid}: stage recording failed")
            check_recorded(row)
        except Blocked as exc:
            print(f"⛔ {exc}")
            shutil.rmtree(PROOF_ROOT / "fixtures" / sid, ignore_errors=True)
            print("   fixture dir removed; manifest and dataset untouched")
            return 1
        authored.append((row, cand))
        print("  ✓ measured signature + crisis geometry + recorded "
              "stage 1/2/5 verified")

    # Everything is proven before anything frozen: rows, hashes, baseline.
    for row, _cand in authored:
        rows[row["scenario_id"]] = row
    rows_doc["scenarios"] = [rows[k] for k in sorted(rows)]
    rows_doc["generated_on"] = today
    sc.write_json(PROOF_ROOT / sc.MANIFEST_FILE, rows_doc)
    hashed = sc.regenerate_hashes()
    sc.re_freeze_baseline()

    records = [build_record(row) for row, _cand in authored]
    trades = {t["trade_id"]: t for t in data["trades"]}
    for record in records:
        trades[record["trade_id"]] = record
    data["trades"] = [trades[k] for k in sorted(trades)]

    counts: dict[str, int] = {}
    for trade in data["trades"]:
        counts[trade["trade_class"]] = counts.get(trade["trade_class"], 0) + 1
    expected = data["meta"]["class_counts_expected"]
    mismatches = [f"{cls}: {counts.get(cls, 0)}/{quota}"
                  for cls, quota in expected.items()
                  if counts.get(cls, 0) != quota]
    if mismatches or len(data["trades"]) != data["meta"]["total_trades_expected"]:
        print(f"⛔ quota mismatch after authoring: {mismatches}", file=sys.stderr)
        print("   manifest/hashes/baseline were written; the dataset is NOT "
              "flipped — reconcile before committing", file=sys.stderr)
        return 1

    notes = [n for n in data["meta"]["notes"]
             if not COUNT_NOTE_RE.match(n.lstrip())
             and not n.startswith(VERIFIED_NOTE_PREFIX)]
    notes.append(distribution_note(records, len(data["trades"]),
                                   data["meta"]["total_trades_expected"]))
    notes.append(verified_note(len(data["trades"]),
                               data["meta"]["total_trades_expected"]))
    data["meta"]["notes"] = notes
    data["meta"]["distribution_verified"] = True
    sc.write_json(GOLDEN, data)

    print(f"\nauthored {len(authored)} row(s); manifest.sha256 now covers "
          f"{hashed} file(s); baseline re-frozen; dataset {len(data['trades'])}/"
          f"{data['meta']['total_trades_expected']}")
    for cls_name, quota in expected.items():
        mark = "✓" if counts.get(cls_name, 0) == quota else "·"
        print(f"  {mark} {cls_name}: {counts.get(cls_name, 0)}/{quota}")
    print("  ✓ distribution_verified=true (every class count matches)")
    print("  → next: python tests/evals/report_regen.py   (stage-12 reports)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
