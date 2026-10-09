"""TICK-002 seam — scripts/1_math_and_rr.py

Stage 1 owns the R:R / slippage / margin math per BRD §9.1 row 1 and TICK-002:
raw vs. live R:R, slippage penalty, risk/reward in ₹ per lot, capital
allocation percentage, and the frozen inverted-RR circuit breaker
(`live_rr < 1.0` ⇒ breaker observation, BRD §10.2 #1; strict, boundary 1.0
passes).

Runtime strategy (tests/support/pipeline.py): behavior contract in-process
via import + ONE subprocess test guarding the frozen BRD §9.1 invocation.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))

from support.pipeline import (  # noqa: E402
    import_mode, run_stage1_json, subprocess_mode, temp_input,
)

REPO_ROOT = TESTS_DIR.parent
SCRIPT = REPO_ROOT / "scripts" / "1_math_and_rr.py"

# Canonical BRD §3.2 input — RELIANCE FUT, the same fixture used everywhere.
CANONICAL_INPUT = {
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
    "margin": 145000.00,
    "recommended_date": "2026-10-09",
    "source": "univest",
    "user_capital_inr": 500000.00,
}


def make_input(**overrides):
    payload = dict(CANONICAL_INPUT)
    payload.update(overrides)
    return payload


class TestRawVsLiveCola(unittest.TestCase):
    """R:R comes in exactly two variants the tests can hand-verify by hand."""

    def test_raw_rr_uses_recommended_band_midpoint(self):
        """Band midpoint 2980.00: risk 70.00, reward 140.00 ⇒ raw R:R = 2.00."""
        out, code, err = run_stage1_json(make_input())
        self.assertEqual(code, 0, err)
        self.assertAlmostEqual(out["raw_rr"], (3120.00 - 2980.00) / (2980.00 - 2910.00), places=6)

    def test_live_rr_uses_live_price_not_recommended(self):
        """Live price 2988.20: risk 78.20, reward 131.80 ⇒ live R:R ≈ 1.6854."""
        out, code, err = run_stage1_json(make_input())
        self.assertEqual(code, 0, err)
        self.assertAlmostEqual(out["live_rr"], (3120.00 - 2988.20) / (2988.20 - 2910.00), places=6)
        self.assertLess(out["live_rr"], out["raw_rr"])


class TestSlippage(unittest.TestCase):
    """Univest says 'enter here'; the user arrives later. Slippage policy is frozen."""

    def test_outside_range_pct_when_live_above_range(self):
        # (2988.20 - 2985.00) / (2985.00 - 2975.00) = 32% above the band
        out, code, err = run_stage1_json(make_input())
        self.assertEqual(code, 0, err)
        self.assertAlmostEqual(out["outside_range_pct"], 32.0, places=6)

    def test_inside_range_is_zero_not_negative(self):
        out, code, err = run_stage1_json(make_input(live_price=2980.00))
        self.assertEqual(code, 0, err)
        self.assertEqual(out["outside_range_pct"], 0.0)


class TestRiskRewardCapital(unittest.TestCase):
    """₹-per-lot math and the capital-allocation percentage."""

    def test_risk_and_reward_inr_per_futures_lot(self):
        out, code, err = run_stage1_json(make_input())
        self.assertEqual(code, 0, err)
        self.assertAlmostEqual(out["risk_inr"], 78.20 * 250, places=6)
        self.assertAlmostEqual(out["reward_inr"], 131.80 * 250, places=6)

    def test_cash_equity_uses_lot_size_one(self):
        cash = make_input(instrument_type="CASH", lot_size=None, entry_price=820.00,
                          entry_range_min=815.00, entry_range_max=825.00,
                          live_price=823.00, target=890.00, stoploss=790.00)
        out, code, err = run_stage1_json(cash)
        self.assertEqual(code, 0, err)
        self.assertAlmostEqual(out["risk_inr"], (823.00 - 790.00) * 1, places=6)
        self.assertAlmostEqual(out["reward_inr"], (890.00 - 823.00) * 1, places=6)


class TestInvertedRRBreaker(unittest.TestCase):
    """BRD §10.2 #1: live R:R < 1.0 ⇒ NOT a trade. No warnings, no re-scoring."""

    def test_inverted_rr_flags_circuit_breaker(self):
        # Risk 80, reward 70 ⇒ live R:R 0.875 hard negative expectancy.
        out, code, err = run_stage1_json(
            make_input(live_price=2998.00, target=3070.00, stoploss=2918.00))
        self.assertEqual(code, 0, err)
        self.assertTrue(out["inverted_rr_flag"])
        self.assertLess(out["live_rr"], 1.0)

    def test_boundary_case_rr_exactly_one_is_not_inverted(self):
        """Frozen boundary: R:R = 1.0 must NOT trip the breaker (strict '< 1.0').

        live=2988.00, target=3068.00, stop=2908.00: reward = risk = 80 exactly,
        so live_rr == 1.0 at the breaker's strict-inequality boundary.
        """
        out, code, err = run_stage1_json(
            make_input(live_price=2988.00, entry_range_max=2988.00,
                       target=3068.00, stoploss=2908.00))
        self.assertEqual(code, 0, err)
        self.assertFalse(out["inverted_rr_flag"])
        self.assertAlmostEqual(out["live_rr"], 1.0, places=6)


class TestMissingInputs(unittest.TestCase):
    def test_missing_stoploss_errors_cleanly(self):
        payload = make_input()
        payload.pop("stoploss")
        _, code, err = run_stage1_json(payload)
        self.assertNotEqual(code, 0)
        self.assertIn("ERR_PARSE", err)


class TestCliContract(unittest.TestCase):
    """ONE subprocess invocation per stage — the frozen BRD §9.1 shape."""

    def test_canonical_invocation_via_real_interpreter(self):
        """`python scripts/1_math_and_rr.py --input current_trade.json > out_1_math.json`
        must behave identically to the imported mode (exit code, stderr routing,
        stdout-JSON purity)."""
        with temp_input(make_input(), ".json") as path:
            proc = subprocess_mode(SCRIPT, ["--input", path])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        import json as _json
        payload = _json.loads(proc.stdout)
        self.assertAlmostEqual(payload["live_rr"], 131.80 / 78.20, places=6)

    # (An inverted-breaker subprocess case was removed: the flag comes from the
    # same main() both modes invoke, and the canonical-path subprocess test
    # already proves exit codes and stdout-JSON purity. One spawn per stage.)


if __name__ == "__main__":
    unittest.main()
