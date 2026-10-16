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
            intraday = [{"date": "2020-01-03 10:00", "open": 100, "high": 101,
                         "low": 97, "close": 100, "volume": 5},
                        {"date": "2020-01-03 10:05", "open": 100, "high": 102,
                         "low": 99, "close": 101, "volume": 5}]
            answers = iter([{"candles": intraday},
                            {"best_bid": 100.5, "best_ask": 100.6}])
            env = dict(os.environ, DHAN_CLIENT_ID="x", DHAN_ACCESS_TOKEN="y")
            with tempfile.TemporaryDirectory() as cache_dir, \
                 mock.patch.dict(os.environ, env, clear=True), \
                 mock.patch.object(self.stage8, "http_post_json",
                                   side_effect=lambda *a, **k: next(answers)) as post:
                payload, code, _ = call_imported_main(self.stage8, [
                    "--input", str(canonical), "--ohlcv-fixture", str(prefix),
                    "--live", "--dhan-security-id", "1333",
                    "--cache-db", str(Path(cache_dir) / "dhan_cache.db")])
        self.assertEqual(code, 0)
        self.assertFalse(payload["waived"])
        self.assertEqual(payload["precision_source"], "dhan_intraday")
        self.assertEqual(post.call_count, 2)
        self.assertAlmostEqual(payload["spread_impact_cost_pct"], 100.0 *
                               (100.6 - 100.5) / 100.55, places=4)
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
