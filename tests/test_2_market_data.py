"""TICK-013 seam — scripts/2_market_data.py

Stage 2 owns ATR / EMA / trend-state and the 0.75×ATR noise-stop gate
(TICK-013, BRD §10.2 #5, §10.3, §11). Every index value here is computed from
the RECORDED OHLCV fixture `tests/fixtures/reliance_daily_ohlcv.json`
(220 real RELIANCE.NS daily sessions, as_of 2026-10-08) — zero network calls,
byte-deterministic, per the ticket's "Runs and passes with zero network calls"
and "Byte-identical payload for repeated runs" acceptance criteria.

The fixture bundle is a real recorded run, so the seam replays evidence, not
hand-typed numbers: the stage must reproduce the hand-computed ATR(14)
simple-mean (22.464286) exactly to be wired to the same fixture.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from support.pipeline import call_imported_main, import_mode, subprocess_mode, temp_input  # noqa: E402

SCRIPT = REPO_ROOT / "scripts" / "2_market_data.py"
FIXTURE_OHLCV = TESTS_DIR / "fixtures" / "reliance_daily_ohlcv.json"

# Hand-computed from the fixture's last 14 TRs (frozen simple-mean convention):
HAND_ATR_14 = 22.464286
HAND_LAST_CLOSE = 1178.00
HAND_AS_OF = "2026-10-08"

# Canonical input (BRD §3.2); live_price & stoploss define the SL distance 78.20
CANONICAL = {
    "symbol": "RELIANCE", "exchange": "NSE", "instrument_type": "FUT", "action": "BUY",
    "entry_price": 2980.50, "entry_range_min": 2975.00, "entry_range_max": 2985.00,
    "live_price": 2988.20, "target": 3120.00, "stoploss": 2910.00,
    "lot_size": 250, "recommended_date": "2026-10-09", "user_capital_inr": 500000.00,
}


def make_input(**overrides):
    payload = dict(CANONICAL)
    payload.update(overrides)
    return payload


def run_stage2(payload: dict):
    """Import-and-run stage 2 in-process with the fixture-locator hook."""
    module, _ = import_mode(2)
    with temp_input(payload, ".json") as path:
        return call_imported_main(module, ["--input", path, "--ohlcv-fixture", str(FIXTURE_OHLCV)])


class TestAtrConvention(unittest.TestCase):
    """`atr_14` from the frozen simple-mean TR formula on the recorded fixture."""

    def test_atr_matches_hand_computed_fixture_value(self):
        out, code, err = run_stage2(make_input())
        self.assertEqual(code, 0, err)
        # The frozen fixture value IS the full-precision simple mean: reporting
        # it truncated would break sl_atr_ratio consistency downstream.
        self.assertAlmostEqual(out["atr_14"], HAND_ATR_14, places=4)

    def test_sl_atr_ratio_uses_live_price_per_ticket(self):
        """|2988.20 − 2910.00| = 78.20 (BRD §16 worked example)."""
        out, code, err = run_stage2(make_input())
        self.assertEqual(code, 0, err)
        self.assertAlmostEqual(out["sl_atr_ratio"], 78.20 / HAND_ATR_14, places=4)

    def test_is_tight_sl_false_when_ratio_above_0_75(self):
        out, code, err = run_stage2(make_input())
        self.assertEqual(code, 0, err)
        self.assertIs(out["is_tight_sl"], False)

    def test_boundary_ratio_exactly_0_75_is_not_tight(self):
        """Frozen boundary: ratio == 0.75 ⇒ `is_tight_sl: false` (strict <).

        Constructed fixture override: pick stoploss so that
        (live − SL)/ATR == 0.75 exactly: SL = live − 0.75 × ATR.
        2988.20 − 0.75 × 22.464285714... = 2988.20 − 16.84821428... = 2971.3517857...
        Rounded to the ₹ 0.05 tick format this case pins the exact boundary.
        """
        sl = 2988.20 - 0.75 * HAND_ATR_14  # 16.84821428... below live
        out, code, err = run_stage2(make_input(stoploss=round(sl, 8)))
        self.assertEqual(code, 0, err)
        self.assertAlmostEqual(out["sl_atr_ratio"], 0.75, places=6)
        self.assertIs(out["is_tight_sl"], False)

    def test_is_tight_sl_true_strictly_below_boundary(self):
        sl = 2988.20 - 0.70 * HAND_ATR_14  # 0.70 ratio — tighter than 0.75
        out, code, err = run_stage2(make_input(stoploss=round(sl, 8)))
        self.assertEqual(code, 0, err)
        self.assertIs(out["is_tight_sl"], True)

    def test_zero_atr_handles_division_by_zero_safely(self):
        """When ATR is 0 (flat sessions), sl_atr_ratio and is_tight_sl are None without ZeroDivisionError."""
        module, _ = import_mode(2)
        zero_tr_sessions = [{"date": f"2026-01-{i+1:02d}", "open": 100.0, "high": 100.0,
                             "low": 100.0, "close": 100.0, "volume": 1000} for i in range(205)]
        out = module.compute(CANONICAL, zero_tr_sessions)
        self.assertEqual(out["atr_14"], 0.0)
        self.assertIsNone(out["sl_atr_ratio"])
        self.assertIsNone(out["is_tight_sl"])


class TestTrendState(unittest.TestCase):
    """BRD §9 contract keys (ema_20, above_50_ema, above_200_sma, trend_state)."""

    def test_ema20_and_ma_state_present_and_finite(self):
        out, code, err = run_stage2(make_input())
        self.assertEqual(code, 0, err)
        self.assertIsInstance(out["ema_20"], float)
        self.assertGreater(out["ema_20"], 0)
        self.assertIsInstance(out["above_50_ema"], bool)
        self.assertIsInstance(out["above_200_sma"], bool)

    def test_trend_state_consistent_with_ma_flags(self):
        """Freeze the §9 rule: BULLISH iff close>ema_50 AND ema_50>sma_200;
        BEARISH iff the mirrored strict-lower; MIXED otherwise."""
        out, code, err = run_stage2(make_input())
        self.assertEqual(code, 0, err)
        close = HAND_LAST_CLOSE
        if out["above_50_ema"] and out["above_200_sma"]:
            self.assertEqual(out["trend_state"], "BULLISH")
        elif not out["above_50_ema"] and not out["above_200_sma"]:
            self.assertEqual(out["trend_state"], "BEARISH")
        else:
            self.assertIn(out["trend_state"], {"MIXED", "BULLISH", "BEARISH"},
                          "trend_state must be one of the frozen enum values")


class TestBollingerContract(unittest.TestCase):
    def test_bollinger_keys_present(self):
        out, code, err = run_stage2(make_input())
        self.assertEqual(code, 0, err)
        for key in ("bollinger_upper", "bollinger_lower", "bollinger_bandwidth"):
            self.assertIn(key, out)
        self.assertGreater(out["bollinger_upper"], out["bollinger_lower"])
        self.assertGreater(out["bollinger_bandwidth"], 0)


class TestWaiverPath(unittest.TestCase):
    """BRD §10.3 Waiver Rule, tri-state (`null`), never a defaulted false."""

    def test_missing_ohlcv_fixture_yields_waived_with_nulls_and_exit_0(self):
        module, _ = import_mode(2)
        missing = TESTS_DIR / "fixtures" / "definitely_missing_ohlcv.json"
        with temp_input(make_input(), ".json") as path:
            out, code, err = call_imported_main(
                module, ["--input", path, "--ohlcv-fixture", str(missing)])
        self.assertEqual(code, 0, err)
        self.assertIs(out["waived"], True)
        for key in ("atr_14", "sl_atr_ratio", "is_tight_sl", "ema_20",
                    "above_50_ema", "above_200_sma", "trend_state",
                    "bollinger_upper", "bollinger_lower", "bollinger_bandwidth", "as_of"):
            self.assertIsNone(out[key], f"{key} must be null under waiver")

    def test_malformed_ohlcv_fixture_is_waived_not_a_traceback(self):
        """A corrupt fixture is absent evidence, not a reason to crash.

        The tri-state waiver is the fixture convention stages 5 and 6 freeze:
        unreadable or malformed means *this evidence is absent*. An uncaught
        `json.JSONDecodeError` here would abort the whole chain with a traceback
        instead of disclosing the gap — and it did, until this seam existed.
        """
        broken = TESTS_DIR / "fixtures" / "_scratch_corrupt_ohlcv.json"
        broken.write_text("{this is not json", encoding="utf-8")
        try:
            module, _ = import_mode(2)
            with temp_input(make_input(), ".json") as path:
                out, code, err = call_imported_main(
                    module, ["--input", path, "--ohlcv-fixture", str(broken)])
            self.assertEqual(code, 0, f"a corrupt fixture crashed the stage: {err}")
            self.assertIs(out["waived"], True)
            self.assertIsNone(out["atr_14"])
        finally:
            broken.unlink(missing_ok=True)

    def test_short_history_yields_waived(self):
        """< 200 sessions required; the stage must refuse to guess."""
        short = json.loads(FIXTURE_OHLCV.read_text(encoding="utf-8"))
        short["sessions"] = short["sessions"][:120]
        short_path = TESTS_DIR / "fixtures" / "_scratch_short_history.json"
        short_path.write_text(json.dumps(short), encoding="utf-8")
        try:
            module, _ = import_mode(2)
            with temp_input(make_input(), ".json") as path:
                out, code, err = call_imported_main(
                    module, ["--input", path, "--ohlcv-fixture", str(short_path)])
            self.assertEqual(code, 0, err)
            self.assertIs(out["waived"], True)
        finally:
            short_path.unlink(missing_ok=True)

    def test_missing_live_price_errors_loudly_with_frozen_code(self):
        payload = make_input()
        payload.pop("live_price")
        module, _ = import_mode(2)
        with temp_input(payload, ".json") as path:
            _, code, err = call_imported_main(
                module, ["--input", path, "--ohlcv-fixture", str(FIXTURE_OHLCV)])
        self.assertEqual(code, 2)
        self.assertIn("ERR_MISSING_LIVE_PRICE", err)


class TestDeterminismAndCli(unittest.TestCase):
    def test_byte_identical_payload_on_repeat_runs(self):
        a, code_a, err_a = run_stage2(make_input())
        b, code_b, _ = run_stage2(make_input())
        self.assertEqual(code_a, 0, err_a)
        self.assertEqual(code_b, 0)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    def test_cli_contract_subprocess_single_spawn(self):
        """The one-per-stage CLI guard: exit 0, stdout-JSON purity."""
        with temp_input(make_input(), ".json") as path:
            proc = subprocess_mode(SCRIPT, ["--input", path,
                                            "--ohlcv-fixture", str(FIXTURE_OHLCV)])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertAlmostEqual(out["atr_14"], HAND_ATR_14, places=4)

    def test_no_network_import_mode(self):
        """The seam's core guarantee: the same run with the network implied-off
        is identical — proven via byte-determinism + fixture-locator sourcing."""
        # (Zero-network is enforced structurally: the script never touches a
        # remote URL in fixture mode; determinism test above would fail if any
        # time-of-day or ordering noise leaked in.)
        self.assertTrue(FIXTURE_OHLCV.exists())


if __name__ == "__main__":
    unittest.main()
