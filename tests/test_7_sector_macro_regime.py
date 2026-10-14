"""TICK-007 seam — scripts/7_sector_macro_regime.py

Stage 7 owns the Sector Relative Strength & Macro VIX Regime classifier
(BRD §7, §9.1 row 7):
* Mansfield RS (50-SMA) of the trade's NSE sectoral index vs NIFTY 50, plus
  its 5-session momentum — the two RRG axes (BRD §7.1, ADR-010 decision 2)
* RRG quadrant: LEADING / WEAKENING / LAGGING / IMPROVING
* `nifty_trend` in the frozen TICK-007 / BRD §7.2 tokens
* NSE 500 breadth regime, India VIX band, 5-day FII/DII flow bias
* `panic_regime_breaker` (BRD §10.3 #6): VIX > 24.0 AND breadth < 30.0,
  strict on both arms

Evidence (ADR-010): one `uvest-market-context/1` bundle via
`--regime-fixture`. Waiver is **all-or-nothing at the payload level**: any
absent, short, malformed or misaligned input — including an unmapped symbol —
yields `waived: true`, every metric `null`, `missing_inputs` naming each cause
in the frozen check order, exit 0 (BRD §10.4).

Runtime strategy (support/pipeline.py):
* In-process module import for behavior & logic contracts
* Subprocess invocations guarding the CLI contract, determinism and --record
* Zero network dependencies: offline fixtures only
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from support.pipeline import (  # noqa: E402
    call_imported_main,
    import_mode,
    subprocess_mode,
    temp_input,
)
from support.market_context import (  # noqa: E402
    BREADTH,
    DEFAULT_FIXTURE,
    FLOW,
    INDIA_VIX,
    NSEI,
    SECTOR,
    build_context,
)

SCRIPT = REPO_ROOT / "scripts" / "7_sector_macro_regime.py"
FIXTURE_CONTEXT = DEFAULT_FIXTURE

# The frozen payload contract (TICK-007 §2 / BRD §9 inventory row 7).
CONTRACT_KEYS = {
    "sector_index", "sector_mrs", "sector_mrs_mom", "sector_rrg_state",
    "nifty_trend", "market_breadth_pct", "breadth_regime", "vix_value",
    "vix_regime", "fii_dii_flow_bias", "panic_regime_breaker",
    "waived", "as_of", "missing_inputs",
}
METRIC_KEYS = CONTRACT_KEYS - {"waived", "missing_inputs"}

CANONICAL = {
    "symbol": "RELIANCE",
    "exchange": "NSE",
    "instrument_type": "FUT",
    "action": "BUY",
    "entry_price": 2980.50,
    "entry_range_min": 2975.00,
    "entry_range_max": 2985.00,
    "live_price": 2988.20,
    "target": 3120.00,
    "stoploss": 2910.00,
    "lot_size": 250,
    "recommended_date": "2026-10-09",
    "user_capital_inr": 500000.00,
}


def make_input(**overrides):
    payload = dict(CANONICAL)
    payload.update(overrides)
    return payload


def run_stage7(payload: dict, fixture_path: Path | None = FIXTURE_CONTEXT):
    """Import and run stage 7 in-process with the fixture argument."""
    module, _ = import_mode(7)
    with temp_input(payload, ".json") as path:
        argv = ["--input", path]
        if fixture_path:
            argv.extend(["--regime-fixture", str(fixture_path)])
        return call_imported_main(module, argv)


def run_on_context(payload: dict, context: dict):
    """Run stage 7 against an in-memory context bundle (the waiver lab)."""
    with temp_input(context, ".json") as fix_path:
        return run_stage7(payload, fixture_path=Path(fix_path))


class TestMansfieldAndRRG(unittest.TestCase):
    """Frozen TICK-007 conventions: MRS formula, momentum axis, quadrants."""

    def test_constant_ratio_has_zero_level_and_zero_momentum(self):
        module, _ = import_mode(7)
        level, mom = module.mrs_moments([2.0] * 60)
        self.assertEqual(level, 0.0)
        self.assertEqual(mom, 0.0)

    def test_mrs_is_the_documented_formula(self):
        """55 sessions at ratio 1.0 then 5 at 1.1: the 50-window at t is
        45×1.0 + 5×1.1 (mean 1.01), while the window at t-5 is all 1.0."""
        module, _ = import_mode(7)
        level, mom = module.mrs_moments([1.0] * 55 + [1.1] * 5)
        self.assertAlmostEqual(level, (1.1 / 1.01 - 1.0) * 100.0, places=4)
        # At t-5 the 50-window is still all 1.0, so momentum equals the level.
        self.assertAlmostEqual(mom, level, places=4)

    def test_short_ratio_series_is_an_error_not_a_guess(self):
        module, _ = import_mode(7)
        with self.assertRaises(ValueError):
            module.mrs_moments([1.0] * 54)

    def test_all_four_quadrants_and_the_zero_boundaries(self):
        module, _ = import_mode(7)
        cases = {
            (1.5, 0.4): "LEADING",
            (0.0, 0.0): "LEADING",      # zero counts as the positive side
            (1.5, -0.4): "WEAKENING",
            (0.0, -0.4): "WEAKENING",
            (-1.5, -0.4): "LAGGING",
            (-1.5, 0.0): "IMPROVING",
            (-1.5, 0.4): "IMPROVING",
        }
        for (level, mom), expected in cases.items():
            with self.subTest(level=level, mom=mom):
                self.assertEqual(module.rrg_state(level, mom), expected)


class TestFrozenBandBoundaries(unittest.TestCase):
    """Every frozen boundary of the four classifiers, exactly at the edge."""

    def test_vix_bands(self):
        module, _ = import_mode(7)
        cases = [
            (11.99, "LOW"), (12.0, "NORMAL"), (17.99, "NORMAL"),
            (18.0, "ELEVATED"), (23.99, "ELEVATED"), (24.0, "ELEVATED"),
            (24.01, "PANIC"), (31.5, "PANIC"),
        ]
        for value, expected in cases:
            with self.subTest(v=value):
                self.assertEqual(module.classify_vix(value), expected)

    def test_breadth_regimes(self):
        module, _ = import_mode(7)
        cases = [
            (39.9, "RISK_OFF"), (40.0, "NEUTRAL"), (65.0, "NEUTRAL"),
            (65.01, "RISK_ON"),
        ]
        for value, expected in cases:
            with self.subTest(b=value):
                self.assertEqual(module.classify_breadth(value), expected)

    def test_flow_bias_symmetric_thresholds(self):
        module, _ = import_mode(7)
        cases = [
            (-5001.0, "NET_SELLING"), (-5000.0, "NEUTRAL"),
            (5000.0, "NEUTRAL"), (5001.0, "NET_BUYING"),
        ]
        for value, expected in cases:
            with self.subTest(total=value):
                self.assertEqual(module.classify_flow(value), expected)

    def test_nifty_trend_precedence_and_equality(self):
        module, _ = import_mode(7)
        # close == both averages (all equal) counts as above → ABOVE_50_200_DMA
        self.assertEqual(module.classify_nifty_trend([100.0] * 200),
                         "ABOVE_50_200_DMA")
        # A late spike then a one-session dip: close 200 sits BELOW its 50-SMA
        # (218) while far ABOVE its 200-SMA (129.5) → the middle token.
        soft_dip = [100.0] * 170 + [300.0] * 29 + [200.0]
        self.assertEqual(module.classify_nifty_trend(soft_dip), "BELOW_50_ABOVE_200")
        # A three-session bounce inside a long decline: close 300 is ABOVE its
        # (falling) 50-SMA (150.5) but BELOW its 200-SMA (508.25) — the 200-DMA
        # test must win, because the vocabulary has no ABOVE_50_BELOW_200.
        bounce = [1000.0 - 5.0 * i for i in range(196)] + [300.0] * 4
        self.assertEqual(module.classify_nifty_trend(bounce), "BELOW_200_DMA")

    def test_nifty_trend_requires_two_hundred_sessions(self):
        module, _ = import_mode(7)
        with self.assertRaises(ValueError):
            module.classify_nifty_trend([100.0] * 199)

    def test_panic_breaker_is_strict_on_both_arms(self):
        module, _ = import_mode(7)
        cases = [
            (24.0, 29.0, False),   # VIX arm must be strictly > 24
            (24.01, 29.99, True),
            (24.01, 30.0, False),  # breadth arm must be strictly < 30
            (31.0, 12.0, True),
            (24.0, 30.0, False),
        ]
        for v, b, expected in cases:
            with self.subTest(vix=v, breadth=b):
                self.assertEqual(module.panic_breaker(v, b), expected)


class TestFullPayload(unittest.TestCase):
    """End-to-end against the committed fixture: exact tokens, exact keys."""

    def test_reliance_context_yields_the_frozen_payload(self):
        out, code, err = run_stage7(make_input())
        self.assertEqual(code, 0, err)
        self.assertFalse(out["waived"])
        self.assertEqual(set(out), CONTRACT_KEYS)
        self.assertEqual(out["missing_inputs"], [])
        self.assertEqual(out["sector_index"], SECTOR)
        self.assertEqual(out["nifty_trend"], "ABOVE_50_200_DMA")
        self.assertEqual(out["sector_rrg_state"], "LEADING")
        self.assertGreater(out["sector_mrs"], 0.0)
        self.assertGreater(out["sector_mrs_mom"], 0.0)
        self.assertEqual(out["vix_value"], 15.6)
        self.assertEqual(out["vix_regime"], "NORMAL")
        self.assertEqual(out["market_breadth_pct"], 57.0)
        self.assertEqual(out["breadth_regime"], "NEUTRAL")
        self.assertEqual(out["fii_dii_flow_bias"], "NEUTRAL")
        self.assertIs(out["panic_regime_breaker"], False)
        self.assertEqual(out["as_of"], "2026-10-09")
        for key in METRIC_KEYS:
            self.assertIsNotNone(out[key], f"{key} must be non-null when not waived")

    def test_panic_context_trips_the_breaker(self):
        ctx = build_context(vix_tail=26.4, breadth_tail=28.5)
        out, code, err = run_on_context(make_input(), ctx)
        self.assertEqual(code, 0, err)
        self.assertFalse(out["waived"])
        self.assertEqual(out["vix_regime"], "PANIC")
        self.assertEqual(out["breadth_regime"], "RISK_OFF")
        self.assertIs(out["panic_regime_breaker"], True)

    def test_severe_flow_context_reports_net_selling(self):
        ctx = build_context(fii_unit=-1100.0)  # last-5 sum = -5500 < -5000
        out, code, err = run_on_context(make_input(), ctx)
        self.assertEqual(code, 0, err)
        self.assertEqual(out["fii_dii_flow_bias"], "NET_SELLING")

    def test_non_numeric_values_waive_not_traceback(self):
        ctx = build_context()
        for point in ctx["series"]["^INDIAVIX"]["values"][-3:]:
            point["value"] = "corrupt"
        out, code, err = run_on_context(make_input(), ctx)
        self.assertEqual(code, 0, err)
        self.assertTrue(out["waived"])
        self.assertEqual(out["missing_inputs"], ["fixture:unreadable"])


class TestWaiver(unittest.TestCase):
    """ADR-010 / BRD §10.4: one payload, one waiver, causes named."""

    def assert_waived(self, out, code, err, expected_reasons):
        self.assertEqual(code, 0, err)
        self.assertTrue(out["waived"])
        self.assertEqual(set(out), CONTRACT_KEYS)
        self.assertEqual(out["missing_inputs"], expected_reasons)
        for key in METRIC_KEYS | {"sector_index", "as_of"}:
            self.assertIsNone(out[key], f"{key} must be null when waived")

    def test_missing_fixture_yields_waived_with_nulls_and_exit_0(self):
        out, code, err = run_stage7(make_input(), fixture_path=None)
        self.assert_waived(out, code, err, ["fixture:not-provided"])

    def test_unreadable_fixture_file_yields_waived(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as fh:
            fh.write("{not json")
            path = fh.name
        try:
            out, code, err = run_stage7(make_input(), fixture_path=Path(path))
        finally:
            Path(path).unlink(missing_ok=True)
        self.assert_waived(out, code, err, ["fixture:unreadable"])

    def test_wrong_schema_yields_waived(self):
        ctx = build_context()
        ctx["schema"] = "uvest-market-context/0"
        out, code, err = run_on_context(make_input(), ctx)
        self.assert_waived(out, code, err, ["fixture:schema"])

    def test_unmapped_symbol_waives_never_guesses_an_index(self):
        out, code, err = run_stage7(make_input(symbol="MYSTERYCO"))
        self.assert_waived(out, code, err, ["symbol:unmapped:MYSTERYCO"])

    def test_sector_map_pins_sourced_infra_membership(self):
        """LT and ADANIPORTS resolve to NIFTY INFRA (^CNXINFRA).

        Both are constituents of Nifty Infrastructure (TradingView NSE:CNXINFRA
        components, Economic Times' Nifty Infrastructure constituents,
        Investing.com cnx-infrastructure components and Yahoo's ^CNXINFRA
        quote/history — four sources read 2026-10-14, cited in the map's own
        comment). The pin is test-held so a membership change is a deliberate,
        reviewed edit — never a fetch-time guess (ADR-010 decision 5).
        """
        module, _ = import_mode(7)
        self.assertEqual(module.SECTOR_INDEX_MAP.get("LT"), "^CNXINFRA")
        self.assertEqual(module.SECTOR_INDEX_MAP.get("ADANIPORTS"), "^CNXINFRA")
        # The unmapped-symbol guarantee is unaffected: neither member of the
        # infra group may silently widen to a symbol nobody pinned.
        self.assertNotIn("MYSTERYCO", module.SECTOR_INDEX_MAP)

    def test_absent_sector_series_waives(self):
        out, code, err = run_on_context(make_input(), build_context(drop=(SECTOR,)))
        self.assert_waived(out, code, err, [f"series:{SECTOR}:absent"])

    def test_short_nifty_series_waives(self):
        out, code, err = run_on_context(make_input(), build_context(n=199))
        self.assert_waived(out, code, err, [f"series:{NSEI}:short"])

    def test_short_flow_series_waives(self):
        ctx = build_context()
        ctx["series"][FLOW]["values"] = ctx["series"][FLOW]["values"][:3]
        out, code, err = run_on_context(make_input(), ctx)
        self.assert_waived(out, code, err, [f"series:{FLOW}:short"])

    def test_two_absent_series_are_named_in_frozen_check_order(self):
        ctx = build_context(drop=(INDIA_VIX, BREADTH))
        out, code, err = run_on_context(make_input(), ctx)
        self.assert_waived(out, code, err,
                           [f"series:{INDIA_VIX}:absent", f"series:{BREADTH}:absent"])

    def test_misaligned_sector_axis_waives(self):
        ctx = build_context(misalign_sector=True)
        out, code, err = run_on_context(make_input(), ctx)
        self.assert_waived(out, code, err, ["index-axis:misaligned"])


class TestRequiredInputContract(unittest.TestCase):
    """Stage 0's payload is a contract: no symbol, no run (chain contract)."""

    def test_missing_symbol_exits_non_zero(self):
        payload = make_input()
        del payload["symbol"]
        module, _ = import_mode(7)
        with temp_input(payload, ".json") as path:
            out, code, err = call_imported_main(module, ["--input", path])
        # The parser refuses before touching evidence; stderr carries ERR_PARSE.
        self.assertNotEqual(code, 0)
        self.assertIn("ERR_PARSE", err)


class TestCliContract(unittest.TestCase):
    """Canonical invocation, determinism and recorder support (subprocess)."""

    def test_cli_contract_subprocess(self):
        with temp_input(CANONICAL, ".json") as path:
            proc = subprocess_mode(
                SCRIPT,
                ["--input", path, "--regime-fixture", str(FIXTURE_CONTEXT)],
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(set(payload), CONTRACT_KEYS)
        self.assertFalse(payload["waived"])
        self.assertEqual(payload["nifty_trend"], "ABOVE_50_200_DMA")

    def test_byte_identical_payload_on_repeat_runs(self):
        with temp_input(CANONICAL, ".json") as path:
            argv = ["--input", path, "--regime-fixture", str(FIXTURE_CONTEXT)]
            p1 = subprocess_mode(SCRIPT, argv)
            p2 = subprocess_mode(SCRIPT, argv)
        self.assertEqual(p1.returncode, 0, p1.stderr)
        self.assertEqual(p1.stdout, p2.stdout)

    def test_record_flag_creates_exact_artifact(self):
        """--record <dir> writes out_7_regime.json matching stdout byte-for-byte."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            bundle_path = Path(tmp_dir) / "bundle"
            with temp_input(CANONICAL, ".json") as path:
                proc = subprocess_mode(
                    SCRIPT,
                    ["--input", path, "--regime-fixture", str(FIXTURE_CONTEXT),
                     "--record", str(bundle_path)],
                )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            artifact = bundle_path / "out_7_regime.json"
            self.assertTrue(artifact.exists(), "recorded stage 7 artifact missing")
            self.assertEqual(artifact.read_text(encoding="utf-8"), proc.stdout)


if __name__ == "__main__":
    unittest.main()
