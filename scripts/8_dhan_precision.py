#!/usr/bin/env python3
"""Stage 8: precision execution zone — Tier-2 Dhan intraday & depth
(TICK-008; BRD §4 Core Prediction 3, §8, §8.1, §9.1 row 8).

Frozen definitions (each one pinned by `tests/test_8_dhan_precision.py`):

* `anchored_vwap` — the volume-weighted mean of the typical price (H+L+C)/3
  from the **swing-low pivot** (the prefix's lowest-low session) to the last
  session. A zero-volume prefix falls back to the plain typical-price mean —
  a division is never a guess.
* `orderblock_support` — the low of the most recent *demand block*: the last
  bearish session (close < open) followed by a bullish one, inside
  `OB_LOOKBACK` trailing sessions; with no such pair the window's minimum low
  is the disclosed fallback.
* `optimal_limit_range` — the sorted band [`anchored_vwap`,
  `orderblock_support`]; `optimal_limit_buy` is its midpoint (2dp), and it is
  a limit entry **only when it is a strict discount** below the record's
  `entry_price` — the signed-proposal-B discount clamp (2026-10-16,
  docs/agents/proposal-limit-band-policy.md): a midpoint at or above the entry
  is a market order that can only grade worse (a *filled* limit beats market
  MAE iff `limit < entry`), so it ships as `optimal_limit_buy: null` with
  `zone_above_market: true` — the band's levels still ship; a clamp is a
  disclosure, not a waiver. A non-numeric entry cannot be evaluated: the
  limit ships and `zone_above_market` is null (the tri-state, never a guess).
  This is the Tier-1 answer to Core Prediction 3's limit zone, computed from
  the same free daily OHLCV prefix stage 2 reads.
* **The Dhan half is operator-gated and atomic**: it runs only when `--live`
  is passed *and* `DHAN_CLIENT_ID` + `DHAN_ACCESS_TOKEN` + a
  `--dhan-security-id` are available. Any failure (missing credentials, HTTP
  429/5xx, an unexpected payload) waives **only that half** — `waived: true`,
  `spread_impact_cost_pct: null`, `waived_reason` naming the cause — and the
  daily zone still ships, exactly BRD §8.1's "completes the validation using
  daily OHLCV from Tier 1". The validation chain passes no `--live`, so every
  recorded artifact is byte-deterministic (the proof root's rerun contract).
* `dhan_cache.db` — intraday candle queries cache for `CACHE_TTL_SECONDS`
  (15 minutes, BRD §8.1) so an evaluation window never re-hits the API.
  Depth is real-time by definition and is never cached.
* Everything exits **0**. An absent or too-short fixture waives the *whole*
  payload (`missing_inputs` names the cause) — the frozen tri-state: absence
  is disclosed, never fatal, never invented.

Grading contract (gate 6, evals.md §3 #6): `record_stage8_evidence.py` reads
`optimal_limit_buy` out of this artifact as the record's `limit_entry_price`,
and the `$derive` rule `limit_mae_pct` measures the pinned tape's worst low
against that limit. An **unfilled** limit derives `null` and is excluded from
the gate's denominator — no position, no MAE — so a limit that never trades
can never inflate the pass rate. A **clamped** zone (`zone_above_market:
true`) ships no limit claim at all, and the gate's criterion is the
better-fraction *plus* a ≥ 60% fill-rate floor over the records that do —
signed proposal B, the falsifiable half a clamped better-fraction no longer
is.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _recorder import add_record_arg, record_artifact, resolve_bundle_dir  # noqa: E402

# BRD §8.1 endpoints (Tier 2, operator-gated above).
DHAN_CHARTS_URL = "https://api.dhantrade.com/v2/charts/historical"
DHAN_MARKETFEED_URL = "https://api.dhantrade.com/v2/marketfeed/quote"
CACHE_DB = "dhan_cache.db"
CACHE_TTL_SECONDS = 15 * 60

# The demand-block search needs one bearish→bullish pair; below this the
# payload waives whole (a zone from two bars is a coin, not a level).
MIN_SESSIONS = 3
OB_LOOKBACK = 10


# ---------------------------------------------------------------------------
# Tier-1 inputs (the recorded-bundle convention every stage shares)
# ---------------------------------------------------------------------------
def load_payload(path: str) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def load_ohlcv(path: str | None) -> list[dict] | None:
    """The recorded daily sessions, or None on any absence (waiver, not crash)."""
    if not path:
        return None
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    sessions = doc.get("sessions") if isinstance(doc, dict) else doc
    if not isinstance(sessions, list) or len(sessions) < MIN_SESSIONS:
        return None
    if not all(isinstance(s, dict) and {"high", "low", "close"} <= set(s)
               for s in sessions):
        return None
    return sessions


# ---------------------------------------------------------------------------
# The frozen zone (daily tier — always computable offline)
# ---------------------------------------------------------------------------
def swing_low_pivot(bars: list[dict]) -> int:
    """Index of the lowest low — the anchor session (first on a tie)."""
    return min(range(len(bars)), key=lambda i: bars[i]["low"])


def _typical(bar: dict) -> float:
    return (float(bar["high"]) + float(bar["low"]) + float(bar["close"])) / 3.0


def _vwap_from(bars: list[dict], pivot: int) -> float:
    window = bars[pivot:]
    volume = sum(float(b.get("volume") or 0.0) for b in window)
    if volume > 0:
        return sum(_typical(b) * float(b.get("volume") or 0.0) for b in window) \
            / volume
    # Zero-volume fallback: the plain average of the same window — disclosed
    # by construction (no invented weight), never a crash.
    return sum(_typical(b) for b in window) / len(window)


def anchored_vwap_from(bars: list[dict], pivot: int) -> float | None:
    if pivot >= len(bars):
        return None
    # Full precision (the stage-2 convention): the seam hand-checks the
    # frozen weighted mean; rounding happens only where money changes hands
    # (the band and the midpoint).
    return _vwap_from(bars, pivot)


def orderblock_support(bars: list[dict],
                       lookback: int = OB_LOOKBACK) -> float | None:
    window = bars[-lookback:]
    if not window:
        return None
    for i in range(len(window) - 2, -1, -1):
        bearish = window[i]["close"] < window[i]["open"]
        bullish = window[i + 1]["close"] > window[i + 1]["open"]
        if bearish and bullish:
            return round(float(window[i]["low"]), 2)
    return round(min(float(b["low"]) for b in window), 2)


def _zone(bars: list[dict], refined_vwap: float | None = None) -> dict:
    """The execution band from one AVWAP and the demand block."""
    vwap = refined_vwap if refined_vwap is not None \
        else anchored_vwap_from(bars, swing_low_pivot(bars))
    support = orderblock_support(bars)
    low = high = limit = None
    if vwap is not None and support is not None:
        low, high = sorted((round(vwap, 2), round(support, 2)))
        limit = round((low + high) / 2.0, 2)
    return {
        "anchored_vwap": vwap,
        "orderblock_support": support,
        "optimal_limit_range": [low, high] if low is not None else None,
        "optimal_limit_buy": limit,
    }


# ---------------------------------------------------------------------------
# Tier 2 — the Dhan half (operator-gated, cached, waivable)
# ---------------------------------------------------------------------------
def http_post_json(url: str, headers: dict, body: dict) -> dict:
    """The one live call this stage may make. Tests replace this hook."""
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def get_cached(key: str, *, now: float, db_path: str = CACHE_DB) -> dict | None:
    """The fresher-than-TTL entry, or None (miss, expiry, absent table)."""
    try:
        db = sqlite3.connect(db_path)
        try:
            row = db.execute(
                "SELECT payload, fetched_at FROM dhan_cache WHERE key = ?",
                (key,)).fetchone()
        finally:
            # Closed explicitly: `with connect()` commits but never closes,
            # and an open handle keeps the file locked on Windows.
            db.close()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    payload, fetched_at = row
    if now - float(fetched_at) > CACHE_TTL_SECONDS:
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def put_cached(key: str, payload: dict, *, now: float,
               db_path: str = CACHE_DB) -> None:
    try:
        db = sqlite3.connect(db_path)
        try:
            db.execute(
                "CREATE TABLE IF NOT EXISTS dhan_cache ("
                "key TEXT PRIMARY KEY, payload TEXT NOT NULL, "
                "fetched_at REAL NOT NULL)")
            db.execute(
                "INSERT OR REPLACE INTO dhan_cache (key, payload, fetched_at) "
                "VALUES (?, ?, ?)",
                (key, json.dumps(payload), now))
            db.commit()
        finally:
            db.close()
    except sqlite3.Error:
        # A cache that cannot write is a cache miss, never a stage failure.
        return None


def _candles_to_bars(raw) -> list[dict] | None:
    """Dhan candles as dicts or v2 arrays → normalized bars."""
    bars = []
    for candle in raw if isinstance(raw, list) else []:
        if isinstance(candle, dict) and {"high", "low", "close"} <= set(candle):
            bars.append({"date": candle.get("date"), "high": float(candle["high"]),
                         "low": float(candle["low"]),
                         "close": float(candle["close"]),
                         "volume": float(candle.get("volume") or 0.0)})
        elif isinstance(candle, (list, tuple)) and len(candle) >= 5:
            bars.append({"date": candle[0], "high": float(candle[2]),
                         "low": float(candle[3]), "close": float(candle[4]),
                         "volume": float(candle[5]) if len(candle) > 5 else 0.0})
        else:
            return None
    return bars or None


def _dhan_half(symbol: str, session_date: str, security_id: str,
               creds: dict[str, str], *, live: bool, now: float,
               db_path: str, fetcher) -> tuple[dict | None, str | None, bool]:
    """(intraday_bars_or_None, waived_reason_or_None, cache_hit)."""
    if not live:
        return None, "dhan:not-requested", False
    missing = [name for name in ("DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN")
               if not creds.get(name)]
    if missing:
        return None, f"dhan:credentials missing ({', '.join(missing)})", False
    if not security_id:
        return None, "dhan:--dhan-security-id required for live intraday", False
    headers = {"ACCESS-TOKEN": creds["DHAN_ACCESS_TOKEN"],
               "CLIENT-ID": creds["DHAN_CLIENT_ID"]}
    cache_key = f"dhan:intraday:{security_id}:{session_date}"
    cached = get_cached(cache_key, now=now, db_path=db_path)
    if cached is not None:
        bars = _candles_to_bars(cached.get("candles"))
        if bars:
            return bars, None, True
    try:
        doc = fetcher(DHAN_CHARTS_URL, headers,
                      {"security_id": security_id, "interval": "5",
                       "oi": False})
    except urllib.error.HTTPError as exc:
        return None, f"dhan:HTTP {exc.code} on charts/historical", False
    except Exception as exc:  # noqa: BLE001 — any transport failure waives
        return None, f"dhan:{type(exc).__name__} on charts/historical", False
    bars = _candles_to_bars(doc.get("candles") if isinstance(doc, dict) else None)
    if not bars:
        return None, "dhan:unexpected candles payload", False
    put_cached(cache_key, {"candles": bars}, now=now, db_path=db_path)
    return bars, None, False


def spread_impact_pct(bars: list[dict], *, now: float, db_path: str,
                      fetcher) -> tuple[float | None, str | None]:
    """(spread_pct, waived_reason): top-of-book spread as % of the mid."""
    try:
        doc = fetcher(DHAN_MARKETFEED_URL, {}, {})
    except urllib.error.HTTPError as exc:
        return None, f"dhan:HTTP {exc.code} on marketfeed/quote"
    except Exception as exc:  # noqa: BLE001
        return None, f"dhan:{type(exc).__name__} on marketfeed/quote"
    data = doc.get("data", doc) if isinstance(doc, dict) else {}
    # Two real shapes: the v2 feed keyed by instrument id
    # (data.<id>.bid/ask) or a flat quote carrying best_bid/best_ask.
    candidates: list[dict] = []
    if isinstance(data, dict):
        if "best_bid" in data or "bid" in data:
            candidates.append(data)
        candidates.extend(v for v in data.values() if isinstance(v, dict))
    bid = ask = None
    for depth in candidates:
        try:
            bid = float(depth["best_bid"] if "best_bid" in depth else depth["bid"])
            ask = float(depth["best_ask"] if "best_ask" in depth else depth["ask"])
            break
        except (TypeError, KeyError, ValueError):
            continue
    if bid is None or ask is None:
        return None, "dhan:unexpected depth payload"
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return None, "dhan:degenerate depth (mid <= 0)"
    return round((ask - bid) / mid * 100.0, 4), None


def _waived_payload(reasons: list[str]) -> dict:
    return {
        "anchored_vwap": None,
        "optimal_limit_buy": None,
        "optimal_limit_range": None,
        "spread_impact_cost_pct": None,
        "orderblock_support": None,
        "cache_hit": False,
        "waived": True,
        "precision_source": None,
        "waived_reason": "; ".join(reasons) if reasons else None,
        "missing_inputs": reasons,
        "zone_above_market": None,
    }


def _apply_discount_clamp(zone: dict, entry) -> tuple[dict, bool | None]:
    """Signed proposal B (2026-10-16): the discount clamp.

    The midpoint is a limit entry only when it sits strictly below the
    record's `entry_price` — the lemma the clamp implements is that a
    *filled* limit beats market MAE iff `limit < entry`, so a zone at or
    above the entry is a market order wearing a limit's name and can only
    grade worse. Returns the (possibly rewritten) zone and the disclosure
    flag: True = clamped, False = shipped as a strict discount, None = not
    evaluable (no numeric entry — the tri-state, never a guess).
    """
    limit = zone.get("optimal_limit_buy")
    if limit is None:
        return zone, None
    if isinstance(entry, bool) or not isinstance(entry, (int, float)):
        return zone, None
    if limit < float(entry):
        return zone, False
    return {**zone, "optimal_limit_buy": None}, True


def precision_payload(bars: list[dict] | None, trade: dict, *,
                      live: bool = False, security_id: str | None = None,
                      now: float | None = None, db_path: str = CACHE_DB,
                      fetcher=None) -> dict:
    """The canonical `out_8_dhan.json` body — never raises, always exits 0."""
    fetcher = fetcher or http_post_json
    now = time.time() if now is None else now
    if bars is None:
        return _waived_payload(
            ["ohlcv_fixture: absent, malformed or shorter than "
             f"{MIN_SESSIONS} sessions"])
    zone = _zone(bars)
    if zone["optimal_limit_buy"] is None:
        return _waived_payload(["ohlcv_fixture: no computable zone"])
    intraday, reason, cache_hit = _dhan_half(
        str(trade.get("symbol") or ""), str(bars[-1].get("date") or ""),
        security_id or "", dict(os.environ), live=live, now=now,
        db_path=db_path, fetcher=fetcher)
    spread, depth_reason = (None, None)
    source = "daily_tier1"
    if intraday:
        # The intraday pivot refines the anchor; the demand block stays the
        # daily level (Tier 1) until a 5m block rule supersedes it.
        zone = _zone(bars, refined_vwap=anchored_vwap_from(
            intraday, swing_low_pivot(intraday)))
        source = "dhan_intraday"
        spread, depth_reason = spread_impact_pct(
            intraday, now=now, db_path=db_path, fetcher=fetcher)
    reasons = [r for r in (reason, depth_reason) if r]
    # The clamp rides on the FINAL zone (post-refinement): the claim is what
    # the Dhan tier may have moved, and gate 6 grades the shipped claim.
    zone, above_market = _apply_discount_clamp(zone, trade.get("entry_price"))
    return {
        **zone,
        "spread_impact_cost_pct": spread,
        "cache_hit": bool(cache_hit),
        "waived": bool(reasons),
        "precision_source": source,
        "waived_reason": "; ".join(reasons) if reasons else None,
        "missing_inputs": [],
        "zone_above_market": above_market,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stage 8: precision execution zone (Dhan intraday & depth)")
    parser.add_argument("--input", default="current_trade.json",
                        help="path to canonical current_trade.json")
    parser.add_argument("--ohlcv-fixture", default=None,
                        help="recorded daily-OHLCV bundle (offline mode); "
                             "omit for the waiver path")
    parser.add_argument("--live", action="store_true",
                        help="opt in to the Dhan Tier-2 half (BRD §8.1); "
                             "the validation chain never passes this")
    parser.add_argument("--dhan-security-id", default=None,
                        help="Dhan instrument token for the live half")
    parser.add_argument("--cache-db", default=CACHE_DB,
                        help=f"intraday cache file (default: {CACHE_DB})")
    add_record_arg(parser)
    args = parser.parse_args()

    payload_doc = load_payload(args.input)
    bars = load_ohlcv(args.ohlcv_fixture)
    trade = payload_doc if isinstance(payload_doc, dict) else {}
    if not trade.get("symbol"):
        payload = _waived_payload(["current_trade.json: absent or symbolless"])
    else:
        payload = precision_payload(
            bars, trade, live=args.live,
            security_id=args.dhan_security_id, db_path=args.cache_db)

    stdout_text = json.dumps(payload, indent=2) + "\n"
    bundle = resolve_bundle_dir(args.record, trade.get("symbol") or "UNSPECIFIED",
                                trade.get("recommended_date"))
    if bundle is not None:
        record_artifact(bundle, 8, stdout_text)
    sys.stdout.write(stdout_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
