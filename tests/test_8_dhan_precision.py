"""Stage-8 seams: the precision execution zone, offline-first (TICK-008).

Stage 8 is the Tier-2 precision layer (BRD §4 Core Prediction 3, §8/§8.1), and
it follows the tier rule exactly: the **daily** tier computes the execution
zone from the same free OHLCV prefix stage 2 reads — Anchored VWAP from the
swing-low pivot, the nearest demand order-block support, and the limit band
between them — while the Dhan half (intraday pivot refinement, top-5 depth,
spread/impact cost) runs only when an operator asks for it (`--live`) *and*
credentials exist; any Dhan failure waives only that half (`waived: true`,
`spread_impact_cost_pct: null`), never the daily computation and never the
process (exit 0, the frozen tri-state).

The grading contract this stage feeds: gate 6 (evals.md §3 #6) compares the
tape's worst low against the engine's limit price (`limit_entry_price`, read
out of the recorded artifact by `record_stage8_evidence.py`) and the market
entry via the `$derive` rule `limit_mae_pct` — an *unfilled* limit derives
`null` and is excluded from the denominator (no position, no MAE), so a deep
limit that never trades can never inflate the pass rate. And since the signed
proposal B (2026-10-16, docs/agents/proposal-limit-band-policy.md) the limit
claim itself carries a **discount clamp**: a filled limit beats market MAE
iff `limit < entry`, so a midpoint at or above the entry is a market order,
not a limit entry — it ships as `optimal_limit_buy: null` with
`zone_above_market: true` (the band's levels still ship), and gate 6's
criterion is the better-fraction *plus* a ≥ 60% fill-rate floor.

Modes per tests/support/pipeline.py: in-process import for behaviour, one
subprocess for the CLI contract.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
for _splice in (str(TESTS_DIR), str(TESTS_DIR / "evals")):
    if _splice not in sys.path:
        sys.path.insert(0, _splice)

import dataset_refs  # noqa: E402  (sibling eval module on the path)
from support.pipeline import (  # noqa: E402
    call_imported_main,
    import_mode,
    subprocess_mode,
)

SCEN_FIXTURE = REPO_ROOT / "tests" / "proof" / "fixtures" / "SCEN-001"

CONTRACT_KEYS = {
    "anchored_vwap", "optimal_limit_buy", "optimal_limit_range",
    "spread_impact_cost_pct", "orderblock_support", "cache_hit", "waived",
    "zone_above_market",
}


def session(date, o, h, l, c, v=1_000):
    return {"date": date, "open": o, "high": h, "low": l, "close": c,
            "volume": v}


def trade_doc(**overrides):
    doc = {"symbol": "RELIANCE", "instrument_type": "CASH", "entry_price": 100.0,
           "entry_range_min": 99.0, "entry_range_max": 101.0, "target": 112.0,
           "stoploss": 95.0, "recommended_date": "2020-01-01"}
    doc.update(overrides)
    return doc


# Dhan's documented v2 shapes (plan-tier2-dhan-collection.md §2, researched
# 2026-10-16): parallel OHLCV arrays with epoch timestamps for candles, and
# the authed 5-level book under data.<segment>.<securityId>.depth for quotes.
# The client is pinned to *these*, never to whatever it used to send.
INTRADAY_DOC = {
    "timestamp": [1578030000, 1578030300],   # 2020-01-03 11:10 / 11:15 IST
    "open": [100, 100], "high": [101, 102], "low": [97, 99],
    "close": [100, 101], "volume": [5, 5],
}
BOOK_DOC = {"data": {"NSE_EQ": {"1333": {"depth": {
    "buy": [{"price": 100.5, "quantity": 10, "orders": 2}],
    "sell": [{"price": 100.6, "quantity": 5, "orders": 1}],
}}}}}
# Same two-bar shape as INTRADAY_DOC but with a deeper swing low (90) at the
# anchor bar, so the trailing AVWAP lands strictly below INTRADAY_DOC's 100.0:
# typical(bar0)=(101+90+100)/3=97.0, typical(bar1)=(102+99+101)/3=100.667,
# equal-volume mean → 98.8333. The pivot is the first lowest-low (bar 0),
# so both bars enter the window.
DEEP_INTRADAY_DOC = {
    "timestamp": [1578030000, 1578030300],   # 2020-01-03 11:10 / 11:15 IST
    "open": [100, 100], "high": [101, 102], "low": [90, 99],
    "close": [100, 101], "volume": [5, 5],
}


def write_inputs(tmp: Path, bars, trade=None):
    prefix = tmp / "ohlcv_prefix.json"
    prefix.write_text(json.dumps({"symbol": "RELIANCE", "sessions": bars}),
                      encoding="utf-8")
    canonical = tmp / "current_trade.json"
    canonical.write_text(json.dumps(trade or trade_doc()), encoding="utf-8")
    return canonical, prefix


class TestTheDailyPrecisionZone(unittest.TestCase):
    """AVWAP, the demand block, and the limit band — offline, hand-checkable."""

    @classmethod
    def setUpClass(cls):
        cls.stage8, _ = import_mode(8)

    def test_avwap_is_the_volume_weighted_typical_price_from_the_pivot(self):
        """Anchor = the lowest low's session; typical price (H+L+C)/3."""
        bars = [
            session("2020-01-01", 100, 102, 90, 101, 10),   # the pivot (low 90)
            session("2020-01-02", 101, 105, 99, 104, 20),
            session("2020-01-03", 104, 106, 102, 105, 30),
        ]
        pivot = self.stage8.swing_low_pivot(bars)
        self.assertEqual(pivot, 0, "the argmin low is the anchor")
        vwap = self.stage8.anchored_vwap_from(bars, pivot)
        expected = (sum(((b["high"] + b["low"] + b["close"]) / 3.0) * b["volume"]
                        for b in bars) / sum(b["volume"] for b in bars))
        self.assertAlmostEqual(vwap, expected, places=6)
        # Vacuity guard: a later anchor gives a different number, so the test
        # pins *which* sessions enter the average.
        self.assertNotAlmostEqual(
            self.stage8.anchored_vwap_from(bars, 1), vwap, places=6)

    def test_zero_volume_falls_back_to_the_plain_average(self):
        bars = [session("2020-01-0%d" % i, 100, 101, 99, 100, 0)
                for i in (1, 2, 3)]
        vwap = self.stage8.anchored_vwap_from(bars, 0)
        expected = sum((b["high"] + b["low"] + b["close"]) / 3.0
                       for b in bars) / len(bars)
        self.assertAlmostEqual(vwap, expected, places=6)

    def test_orderblock_support_is_the_last_demand_block_low(self):
        """The last bearish→bullish pair's *own* low, else the window's min low."""
        bars = [
            session("2020-01-01", 100, 101, 95, 96),   # bearish ...
            session("2020-01-02", 96, 100, 94, 99),    # ... bullish → block low 95
            session("2020-01-03", 99, 101, 97, 100),
            session("2020-01-04", 100, 102, 98, 97),   # bearish ...
            session("2020-01-05", 97, 101, 96, 100),   # ... bullish → low 98 (last)
        ]
        self.assertAlmostEqual(self.stage8.orderblock_support(bars), 98.0)
        # No pair inside the lookback: the trailing min low is the fallback.
        up_only = [session(f"2020-01-0{i}", 100 + i, 101 + i, 99 + i, 100 + i)
                   for i in (1, 2, 3)]
        self.assertAlmostEqual(self.stage8.orderblock_support(up_only),
                               float(min(b["low"] for b in up_only)))

    def test_the_limit_zone_is_the_sorted_support_band_with_a_midpoint_buy(self):
        bars = [session("2020-01-0%d" % i, 100, 101, 90 + i, 99 + i)
                for i in (1, 2, 3)]
        payload = self.stage8.precision_payload(bars, trade_doc())
        low = min(payload["optimal_limit_range"])
        high = max(payload["optimal_limit_range"])
        self.assertEqual(payload["optimal_limit_range"], [low, high])
        self.assertAlmostEqual(payload["optimal_limit_buy"],
                               round((low + high) / 2.0, 2))
        self.assertAlmostEqual(payload["optimal_limit_buy"], round(
            (payload["anchored_vwap"] + payload["orderblock_support"]) / 2.0,
            2), "the buy is the midpoint of AVWAP and the demand block")
        self.assertIsNone(payload["spread_impact_cost_pct"],
                          "no depth offline — the Dhan metric stays null")
        self.assertFalse(payload["cache_hit"])
        self.assertIn("daily_tier1", payload["precision_source"])


class TestTheDiscountClamp(unittest.TestCase):
    """Signed proposal B (2026-10-16): the midpoint is a limit entry only
    when it is a strict discount below the record's entry.

    The lemma the clamp implements: a *filled* limit beats market MAE iff
    `limit < entry` — so a zone at/above the entry is a market order that can
    only grade worse, and grading it as limit execution is a category error.
    """

    @classmethod
    def setUpClass(cls):
        cls.stage8, _ = import_mode(8)
        # Hand-checkable band: pivot at session 2 (low 94), demand block at
        # session 1's low (95), AVWAP from the pivot = mean of the typical
        # prices 97.6667 and 99.3333 = 98.5 → band [95.0, 98.5],
        # midpoint 96.75.
        cls.bars = [
            session("2020-01-01", 100, 101, 95, 96),
            session("2020-01-02", 96, 100, 94, 99),
            session("2020-01-03", 99, 101, 97, 100),
        ]

    def test_a_zone_below_the_entry_ships_unchanged(self):
        payload = self.stage8.precision_payload(
            self.bars, trade_doc(entry_price=100.0))
        self.assertFalse(payload["zone_above_market"],
                         "a strict discount is a limit entry")
        self.assertAlmostEqual(payload["optimal_limit_buy"], 96.75)

    def test_a_zone_at_or_above_the_entry_is_not_a_limit_entry(self):
        payload = self.stage8.precision_payload(
            self.bars, trade_doc(entry_price=96.0))
        self.assertIsNone(payload["optimal_limit_buy"],
                          "a midpoint at/above the entry is a market order")
        self.assertTrue(payload["zone_above_market"])
        # The levels still ship: the clamp removes the *claim*, not the zone.
        self.assertEqual(payload["optimal_limit_range"], [95.0, 98.5])
        self.assertIsNotNone(payload["anchored_vwap"])
        self.assertIsNotNone(payload["orderblock_support"])
        # A clamp is a disclosure, not a whole-payload waiver: no input was
        # missing (`waived` here is the Dhan half's usual offline flag).
        self.assertEqual(payload["missing_inputs"], [])
        self.assertEqual(payload["waived_reason"], "dhan:not-requested")

    def test_equality_is_not_a_strict_discount(self):
        """`limit < entry` is strict: a limit priced AT the market fills
        instantly with zero discount and must clamp."""
        payload = self.stage8.precision_payload(
            self.bars, trade_doc(entry_price=96.75))
        self.assertIsNone(payload["optimal_limit_buy"])
        self.assertTrue(payload["zone_above_market"])

    def test_an_unevaluable_entry_discloses_rather_than_clamps(self):
        """No numeric entry_price ⇒ the clamp cannot be evaluated: the limit
        ships and `zone_above_market` is null (tri-state, never a guess)."""
        trade = trade_doc()
        del trade["entry_price"]
        payload = self.stage8.precision_payload(self.bars, trade)
        self.assertAlmostEqual(payload["optimal_limit_buy"], 96.75)
        self.assertIsNone(payload["zone_above_market"])


class TestTheWaiverContract(unittest.TestCase):
    """TICK-008 §3 / BRD §8.1: graceful degradation, always exit 0."""

    @classmethod
    def setUpClass(cls):
        cls.stage8, cls.script = import_mode(8)

    def cleared_env(self):
        return mock.patch.dict(os.environ, {}, clear=False)

    def test_default_run_never_touches_the_network(self):
        """No `--live`: the Dhan half does not exist for this run at all."""
        with tempfile.TemporaryDirectory() as tmp:
            canonical, prefix = write_inputs(Path(tmp), [
                session("2020-01-0%d" % i, 100, 101, 99, 100) for i in (1, 2, 3)])
            with mock.patch.object(self.stage8, "http_post_json") as post, \
                 self.cleared_env():
                payload, code, _ = call_imported_main(self.stage8, [
                    "--input", str(canonical), "--ohlcv-fixture", str(prefix)])
        self.assertEqual(code, 0)
        self.assertTrue(payload["waived"])
        self.assertEqual(payload["waived_reason"], "dhan:not-requested")
        post.assert_not_called()
        self.assertIsNotNone(payload["anchored_vwap"],
                             "the daily half still computes")

    def test_live_without_credentials_waives_only_the_dhan_half(self):
        with tempfile.TemporaryDirectory() as tmp:
            canonical, prefix = write_inputs(Path(tmp), [
                session("2020-01-0%d" % i, 100, 101, 99, 100) for i in (1, 2, 3)])
            env = {k: v for k, v in os.environ.items()
                   if k not in ("DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN")}
            with mock.patch.dict(os.environ, env, clear=True):
                payload, code, _ = call_imported_main(self.stage8, [
                    "--input", str(canonical), "--ohlcv-fixture", str(prefix),
                    "--live"])
        self.assertEqual(code, 0)
        self.assertTrue(payload["waived"])
        self.assertIn("credentials", payload["waived_reason"])
        self.assertIsNotNone(payload["anchored_vwap"])

    def test_a_live_http_failure_waives_only_the_dhan_half(self):
        import urllib.error
        with tempfile.TemporaryDirectory() as tmp:
            canonical, prefix = write_inputs(Path(tmp), [
                session("2020-01-0%d" % i, 100, 101, 99, 100) for i in (1, 2, 3)])

            def rate_limited(url, headers, body):
                raise urllib.error.HTTPError(url, 429, "Too Many Requests", {},
                                             None)

            env = dict(os.environ, DHAN_CLIENT_ID="x", DHAN_ACCESS_TOKEN="y")
            with mock.patch.dict(os.environ, env, clear=True), \
                 mock.patch.object(self.stage8, "http_post_json",
                                   side_effect=rate_limited):
                payload, code, _ = call_imported_main(self.stage8, [
                    "--input", str(canonical), "--ohlcv-fixture", str(prefix),
                    "--live", "--dhan-security-id", "1333"])
        self.assertEqual(code, 0)
        self.assertTrue(payload["waived"])
        self.assertIn("429", payload["waived_reason"])
        self.assertIsNone(payload["spread_impact_cost_pct"])
        self.assertIsNotNone(payload["anchored_vwap"],
                             "Tier 1 completes the validation (BRD §8.1)")

    def test_a_live_success_refines_the_zone_and_reports_depth(self):
        with tempfile.TemporaryDirectory() as tmp:
            canonical, prefix = write_inputs(Path(tmp), [
                session("2020-01-0%d" % i, 100, 101, 99, 100) for i in (1, 2, 3)])
            env = dict(os.environ, DHAN_CLIENT_ID="x", DHAN_ACCESS_TOKEN="y")
            with tempfile.TemporaryDirectory() as cache_dir, \
                 mock.patch.dict(os.environ, env, clear=True), \
                 mock.patch.object(self.stage8, "http_post_json",
                                   side_effect=iter([INTRADAY_DOC,
                                                     BOOK_DOC])) as post:
                payload, code, _ = call_imported_main(self.stage8, [
                    "--input", str(canonical), "--ohlcv-fixture", str(prefix),
                    "--live", "--dhan-security-id", "1333",
                    "--cache-db", str(Path(cache_dir) / "dhan_cache.db")])
        self.assertEqual(code, 0)
        self.assertFalse(payload["waived"])
        self.assertEqual(payload["precision_source"], "dhan_intraday")
        self.assertEqual(post.call_count, 2)
        self.assertAlmostEqual(payload["spread_impact_cost_pct"], 100.0 *
                               (100.6 - 100.5) / 100.55, places=3)
        # The intraday pivot (low 97) refines the anchor below the daily one.
        self.assertLess(payload["anchored_vwap"], 101.0)

    def test_missing_or_short_fixture_waives_the_whole_payload(self):
        """The frozen tri-state: absent evidence waives, it never crashes."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            canonical, missing = tmp / "current_trade.json", tmp / "nope.json"
            canonical.write_text(json.dumps(trade_doc()), encoding="utf-8")
            payload, code, _ = call_imported_main(self.stage8, [
                "--input", str(canonical), "--ohlcv-fixture", str(missing)])
            self.assertEqual(code, 0)
            self.assertTrue(payload["waived"])
            for key in CONTRACT_KEYS - {"waived", "cache_hit"}:
                self.assertIsNone(payload[key], key)
            self.assertTrue(any("ohlcv" in r for r in payload["missing_inputs"]))
            # Too-short prefix (below the demand-block floor) waives equally.
            _, short = write_inputs(tmp, [session("2020-01-01", 100, 101, 99, 100)])
            payload, code, _ = call_imported_main(self.stage8, [
                "--input", str(canonical), "--ohlcv-fixture", str(short)])
            self.assertEqual(code, 0)
            self.assertTrue(payload["waived"])


class TestTheDhanCache(unittest.TestCase):
    """`dhan_cache.db`: 15-minute TTL, injected clock, no live calls needed."""

    @classmethod
    def setUpClass(cls):
        cls.stage8, _ = import_mode(8)

    def test_round_trip_then_expiry(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "dhan_cache.db"
            t0 = 1_700_000_000.0
            self.stage8.put_cached("k", {"a": 1}, now=t0, db_path=db)
            self.assertEqual(self.stage8.get_cached("k", now=t0 + 60, db_path=db),
                             {"a": 1}, "a minute later: hit")
            self.assertEqual(
                self.stage8.get_cached("k", now=t0 + 14 * 60, db_path=db),
                {"a": 1}, "inside 15 minutes: hit")
            self.assertIsNone(
                self.stage8.get_cached("k", now=t0 + 15 * 60 + 1, db_path=db),
                "past the TTL: miss, the API would be re-queried")
            self.assertIsNone(self.stage8.get_cached("absent", now=t0,
                                                     db_path=db))


class TestTheLiveClientSpeaksTheDocumentedApi(unittest.TestCase):
    """Tier-2 plan Phase 1 (plan-tier2-dhan-collection.md §2/§4): before any
    recorded fixture can pin a real response, the shipped client must match
    the documented one — host, path, body keys, candle shape, book shape.
    The old client sent the *daily* route to a host that isn't Dhan's, with
    invented body keys and a scalar depth the docs never serve.
    """

    @classmethod
    def setUpClass(cls):
        cls.stage8, cls.script = import_mode(8)

    def run_live(self, answers):
        """One live run over a mocked transport; returns (payload, calls)."""
        calls = []

        def capture(url, headers, body):
            calls.append((url, dict(headers), body))
            return next(answers)

        with tempfile.TemporaryDirectory() as tmp:
            canonical, prefix = write_inputs(Path(tmp), [
                session("2020-01-0%d" % i, 100, 101, 99, 100)
                for i in (1, 2, 3)])
            env = dict(os.environ, DHAN_CLIENT_ID="x", DHAN_ACCESS_TOKEN="y")
            with tempfile.TemporaryDirectory() as cache_dir, \
                 mock.patch.dict(os.environ, env, clear=True), \
                 mock.patch.object(self.stage8, "http_post_json",
                                   side_effect=capture):
                payload, code, _ = call_imported_main(self.stage8, [
                    "--input", str(canonical), "--ohlcv-fixture", str(prefix),
                    "--live", "--dhan-security-id", "1333",
                    "--cache-db", str(Path(cache_dir) / "dhan_cache.db")])
        self.assertEqual(code, 0)
        return payload, calls

    def test_the_candles_request_is_the_documented_endpoint_and_body(self):
        _, calls = self.run_live(iter([INTRADAY_DOC, BOOK_DOC]))
        url, headers, body = calls[0]
        self.assertEqual(url, "https://api.dhan.co/v2/charts/intraday",
                         "the documented intraday route, on Dhan's host")
        self.assertEqual(
            set(body), {"securityId", "exchangeSegment", "instrument",
                        "interval", "oi", "fromDate", "toDate"},
            "the documented body keys — not the invented security_id/interval/oi")
        self.assertEqual(body["securityId"], "1333")
        self.assertEqual(body["interval"], "5",
                         "the documented set is {1,5,15,25,60}; 10 does not exist")
        self.assertEqual(body["fromDate"], "2020-01-03 09:15:00",
                         "the graded session's own day, market open")
        self.assertEqual(body["toDate"], "2020-01-03 15:30:00")
        self.assertEqual((headers.get("access-token")
                          or headers.get("ACCESS-TOKEN")), "y",
                         "the documented auth header carries the token")

    def test_the_depth_request_is_the_authed_quote_for_our_symbol(self):
        _, calls = self.run_live(iter([INTRADAY_DOC, BOOK_DOC]))
        url, headers, body = calls[1]
        self.assertEqual(url, "https://api.dhan.co/v2/marketfeed/quote")
        self.assertEqual(body, {"NSE_EQ": ["1333"]},
                         "the documented segment→ids request, authed")
        self.assertEqual((headers.get("access-token")
                          or headers.get("ACCESS-TOKEN")), "y")

    def test_parallel_array_candles_with_epoch_timestamps_become_bars(self):
        """The documented v2 candle shape → normalized bars, epoch in IST
        (deterministic on any machine's clock settings)."""
        bars = self.stage8._candles_to_bars(INTRADAY_DOC)
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0], {"date": "2020-01-03 11:10", "open": 100.0,
                                   "high": 101.0, "low": 97.0, "close": 100.0,
                                   "volume": 5.0})
        self.assertEqual(bars[1]["date"], "2020-01-03 11:15")
        # A millisecond feed is still an epoch; a ragged payload is a miss.
        ms = dict(INTRADAY_DOC, timestamp=[t * 1000 for t in
                                           INTRADAY_DOC["timestamp"]])
        self.assertEqual(self.stage8._candles_to_bars(ms)[0]["date"],
                         "2020-01-03 11:10")
        ragged = dict(INTRADAY_DOC, volume=[1])
        self.assertIsNone(self.stage8._candles_to_bars(ragged),
                          "mismatched array lengths are not candles")

    def test_the_documented_book_shape_feeds_the_spread(self):
        pct, reason = self.stage8.spread_from_quote(
            BOOK_DOC, security_id="1333")
        self.assertIsNone(reason)
        self.assertAlmostEqual(pct, 100.0 * (100.6 - 100.5) / 100.55, places=4)


class TestOfflineTier2Fixtures(unittest.TestCase):
    """`--intraday-fixture` / `--depth-fixture` (plan Phase 1 item 2): the
    stage runs over recorded evidence offline, the way every other stage
    does — zero network, so the validation chain stays byte-deterministic
    and V10 keeps comparing.
    """

    @classmethod
    def setUpClass(cls):
        cls.stage8, cls.script = import_mode(8)

    def run_fixture(self, intraday=None, depth=None):
        """Run the CLI with the given fixture payloads; assert no transport.

        A payload of None means the flag is absent; anything else is written
        to a temp file and passed. Returns the payload.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            canonical, prefix = write_inputs(tmp, [
                session("2020-01-0%d" % i, 100, 101, 99, 100, 1_000)
                for i in (1, 2, 3)])
            argv = ["--input", str(canonical), "--ohlcv-fixture", str(prefix)]
            if intraday is not None:
                path = tmp / "intraday_prefix.json"
                path.write_text(json.dumps(intraday), encoding="utf-8")
                argv += ["--intraday-fixture", str(path)]
            if depth is not None:
                path = tmp / "depth_snapshot.json"
                path.write_text(json.dumps(depth), encoding="utf-8")
                argv += ["--depth-fixture", str(path)]
            with mock.patch.object(self.stage8, "http_post_json") as post:
                payload, code, _ = call_imported_main(self.stage8, argv)
                self.assertFalse(
                    post.called, "fixtures replace the network entirely")
            self.assertEqual(code, 0)
        return payload

    def test_the_intraday_fixture_refines_the_zone_with_zero_network(self):
        # A pivot whose trailing window anchors strictly below the daily
        # tier's 100.0: INTRADAY_DOC's two bars of typicals 99.33 and 100.67
        # average to 100.0; DEEP_INTRADAY_DOC drops the anchor bar's low to
        # 90 so the same construction lands at 98.83, proving the refinement.
        payload = self.run_fixture(intraday=INTRADAY_DOC)
        self.assertEqual(payload["precision_source"], "dhan_intraday")
        self.assertAlmostEqual(payload["anchored_vwap"], 100.0)
        deep = self.run_fixture(intraday=DEEP_INTRADAY_DOC)
        self.assertEqual(deep["precision_source"], "dhan_intraday")
        self.assertLess(deep["anchored_vwap"], payload["anchored_vwap"])
        # The daily tier alone stays at 100.0 with no intraday evidence, and
        # its source is the daily tier — the fixture is what moved the anchor.
        daily = self.run_fixture()
        self.assertEqual(daily["precision_source"], "daily_tier1")
        self.assertAlmostEqual(daily["anchored_vwap"], 100.0, places=6)
        # Depth was never requested offline: the half is named, not invented.
        self.assertTrue(payload["waived"])
        self.assertEqual(payload["waived_reason"], "dhan:not-requested")

    def test_both_fixtures_run_the_whole_tier2_offline(self):
        payload = self.run_fixture(intraday=INTRADAY_DOC, depth=BOOK_DOC)
        self.assertFalse(payload["waived"])
        self.assertIsNone(payload["waived_reason"])
        self.assertEqual(payload["precision_source"], "dhan_intraday")
        self.assertAlmostEqual(payload["spread_impact_cost_pct"],
                               100.0 * (100.6 - 100.5) / 100.55, places=3)
        self.assertIsNotNone(payload["optimal_limit_buy"],
                             "the refined band is still a strict discount")
        self.assertFalse(payload["zone_above_market"])
        self.assertFalse(payload["cache_hit"],
                         "fixtures are the record; the cache is a live thing")

    def test_a_recorded_bars_bundle_is_accepted_too(self):
        """The campaign's fixture file shape (§3 `intraday_prefix.json`):
        normalized bars, not a raw response."""
        bundle = {"symbol": "RELIANCE", "bars": [
            {"date": "2020-01-03 11:10", "open": 100, "high": 101,
             "low": 90, "close": 100, "volume": 5},
            {"date": "2020-01-03 11:15", "open": 100, "high": 102,
             "low": 99, "close": 101, "volume": 5}]}
        payload = self.run_fixture(intraday=bundle)
        self.assertEqual(payload["precision_source"], "dhan_intraday")
        # The swing-low bar's typical is 97.0, so the two-bar AVWAP is
        # 98.83 — strictly below the daily tier's 100.0, proving the bundle
        # was parsed and anchored, not silently ignored.
        self.assertAlmostEqual(payload["anchored_vwap"], 98.83333, places=4)

    def test_a_malformed_fixture_waives_naming_the_fixture(self):
        """E3: evidence that cannot be read is disclosed, never a crash and
        never silently ignored as if the flag were absent."""
        payload = self.run_fixture(intraday={"nonsense": True})
        self.assertTrue(payload["waived"])
        self.assertIn("intraday_fixture", payload["waived_reason"])
        self.assertEqual(payload["precision_source"], "daily_tier1",
                         "the daily tier completes the validation (BRD §8.1)")
        self.assertIsNotNone(payload["optimal_limit_buy"])
        payload = self.run_fixture(depth=["not", "a", "book"])
        self.assertTrue(payload["waived"])
        self.assertIn("dhan:not-requested", payload["waived_reason"])
        self.assertIsNone(payload["spread_impact_cost_pct"])


class TestTheLimitMaeDerivation(unittest.TestCase):
    """Gate 6's numbers: the tape's worst low against the engine's limit."""

    BARS = [{"date": "2020-02-03", "open": 100, "high": 101, "low": 92,
             "close": 99},
            {"date": "2020-02-04", "open": 99, "high": 100, "low": 96,
             "close": 98}]

    def test_a_filled_limit_scores_its_own_adverse_depth(self):
        trade = {"limit_entry_price": 95.0}
        value = dataset_refs.FORWARD_TAPE_RULES["limit_mae_pct"](self.BARS, trade)
        self.assertAlmostEqual(value, (95.0 - 92.0) / 95.0 * 100.0, places=4)

    def test_an_unfilled_limit_is_none_not_zero(self):
        """No touch, no position, no MAE — a deep limit must never win by
        staying unfilled; the gate excludes nulls from its denominator."""
        trade = {"limit_entry_price": 90.0}
        self.assertIsNone(
            dataset_refs.FORWARD_TAPE_RULES["limit_mae_pct"](self.BARS, trade))

    def test_the_market_rule_still_measures_from_the_entry(self):
        trade = {"entry_price": 100.0}
        self.assertAlmostEqual(
            dataset_refs.FORWARD_TAPE_RULES["market_mae_pct"](self.BARS, trade),
            8.0, places=4)


class TestTheCliAndRecorder(unittest.TestCase):
    """One subprocess per suite run (the frozen harness rule)."""

    @classmethod
    def setUpClass(cls):
        cls.stage8, cls.script = import_mode(8)

    def test_the_cli_over_the_committed_scen_fixtures(self):
        record_dir = Path(tempfile.mkdtemp())
        proc = subprocess_mode(self.script, [
            "--input", str(SCEN_FIXTURE / "current_trade.json"),
            "--ohlcv-fixture", str(SCEN_FIXTURE / "ohlcv_prefix.json"),
            "--record", str(record_dir)])
        self.assertEqual(proc.returncode, 0, proc.stderr[-400:])
        payload = json.loads(proc.stdout)
        self.assertTrue(CONTRACT_KEYS <= set(payload),
                        f"missing: {CONTRACT_KEYS - set(payload)}")
        self.assertTrue(payload["waived"], "offline run: the Dhan half waives")
        self.assertIsNotNone(payload["optimal_limit_buy"])
        artifact = record_dir / "out_8_dhan.json"
        self.assertTrue(artifact.exists(), "the recorder wrote the artifact")
        self.assertEqual(json.loads(artifact.read_text(encoding="utf-8")),
                         payload, "recorded bytes are the stdout bytes")


if __name__ == "__main__":
    unittest.main()
