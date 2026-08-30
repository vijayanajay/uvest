"""Test suite validating quantitative decision matrices, scoring invariants, and +ve / -ve trade outputs.

Zero external dependencies; executes in milliseconds with deterministic JSON fixtures.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import importlib

ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT_DIR / "scripts"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

sys.path.insert(0, str(SCRIPTS_DIR))
math_mod = importlib.import_module("1_math_and_rr")
score_mod = importlib.import_module("4_score_calculator")

analyze_math = math_mod.analyze_math
calculate_score = score_mod.calculate_score
decide = score_mod.decide
net_rr_after_costs = score_mod.net_rr_after_costs
break_even_win_rate = score_mod.break_even_win_rate


def load_fixture(name: str) -> dict:
    with open(FIXTURES_DIR / name, "r", encoding="utf-8") as f:
        return json.load(f)


class TestDecisionMatrix(unittest.TestCase):
    """Asserts that all +ve, -ve, and boundary scenarios produce exact expected decisions."""

    # -------------------------------------------------------------------------
    # 1. Positive (+ve) Decision Invariants
    # -------------------------------------------------------------------------

    def test_fixture_pos_clean_breakout(self):
        """Pristine setup in fallback mode must yield 🟢 GO (EXECUTE FULL SIZE)."""
        fixture = load_fixture("scenario_pos_clean_breakout.json")
        result = calculate_score(
            fixture["math_res"],
            fixture["market_res"],
            fixture["audit_res"],
            fixture["backtest_res"],
        )
        self.assertEqual(result["decision"], fixture["expected_decision"])
        self.assertGreaterEqual(result["effective_score"], fixture["min_effective_score"])
        self.assertEqual(result["max_possible_score"], 100)

    def test_fixture_pos_empirical_edge(self):
        """Empirical mode setup with Wilson CI lower > break-even must yield 🟢 GO."""
        fixture = load_fixture("scenario_pos_empirical_edge.json")
        result = calculate_score(
            fixture["math_res"],
            fixture["market_res"],
            fixture["audit_res"],
            fixture["backtest_res"],
        )
        self.assertEqual(result["decision"], fixture["expected_decision"])
        self.assertGreaterEqual(result["effective_score"], fixture["min_effective_score"])
        self.assertTrue(result["empirical_mode"])

    # -------------------------------------------------------------------------
    # 2. Negative (-ve) Decision Invariants
    # -------------------------------------------------------------------------

    def test_fixture_neg_inverted_rr(self):
        """Setup with slippage causing inverted R:R (< 1.0) must yield 🔴 NO-GO."""
        fixture = load_fixture("scenario_neg_inverted_rr.json")
        result = calculate_score(
            fixture["math_res"],
            fixture["market_res"],
            fixture["audit_res"],
            fixture["backtest_res"],
        )
        self.assertEqual(result["decision"], fixture["expected_decision"])
        self.assertLessEqual(result["effective_score"], fixture["max_effective_score"])

    def test_fixture_neg_volatility_trap(self):
        """Setup with stoploss tighter than 1.0x ATR must yield 🔴 NO-GO."""
        fixture = load_fixture("scenario_neg_volatility_trap.json")
        result = calculate_score(
            fixture["math_res"],
            fixture["market_res"],
            fixture["audit_res"],
            fixture["backtest_res"],
        )
        self.assertEqual(result["decision"], fixture["expected_decision"])
        self.assertLessEqual(result["effective_score"], fixture["max_effective_score"])

    def test_fixture_neg_downtrend_trap(self):
        """Setup below 50 EMA with negative historical edge must yield 🔴 NO-GO."""
        fixture = load_fixture("scenario_neg_downtrend_trap.json")
        result = calculate_score(
            fixture["math_res"],
            fixture["market_res"],
            fixture["audit_res"],
            fixture["backtest_res"],
        )
        self.assertEqual(result["decision"], fixture["expected_decision"])
        self.assertLessEqual(result["effective_score"], fixture["max_effective_score"])

    def test_fixture_neg_empirical_no_edge(self):
        """Empirical setup where backtest win rate fails break-even hurdle must yield 🔴 NO-GO."""
        fixture = load_fixture("scenario_neg_empirical_no_edge.json")
        result = calculate_score(
            fixture["math_res"],
            fixture["market_res"],
            fixture["audit_res"],
            fixture["backtest_res"],
        )
        self.assertEqual(result["decision"], fixture["expected_decision"])
        self.assertLessEqual(result["effective_score"], fixture["max_effective_score"])

    # -------------------------------------------------------------------------
    # 3. Boundary & Policy Invariants
    # -------------------------------------------------------------------------

    def test_fixture_bnd_expiry_gamma_cap(self):
        """High-score setup (< 3 days to expiry) must be capped to 🟡 CONDITIONAL GO."""
        fixture = load_fixture("scenario_bnd_expiry_gamma_cap.json")
        result = calculate_score(
            fixture["math_res"],
            fixture["market_res"],
            fixture["audit_res"],
            fixture["backtest_res"],
        )
        self.assertEqual(result["decision"], fixture["expected_decision"])
        self.assertGreaterEqual(result["effective_score"], fixture["min_effective_score"])

    def test_fixture_bnd_waiver_missing_data(self):
        """Missing market data and track record must scale denominator without penalty."""
        fixture = load_fixture("scenario_bnd_waiver_missing_data.json")
        result = calculate_score(
            fixture["math_res"],
            fixture["market_res"],
            fixture["audit_res"],
            fixture["backtest_res"],
        )
        self.assertEqual(result["decision"], fixture["expected_decision"])
        self.assertEqual(result["max_possible_score"], fixture["expected_max_possible_score"])
        self.assertEqual(result["effective_score"], fixture["expected_effective_score"])
        self.assertGreater(len(result["waived"]), 0)


class TestScoringMathematics(unittest.TestCase):
    """Validates mathematical helper functions and boundary rules."""

    def test_decide_scoring_bands(self):
        """Verify strict 80 / 65 / 0 decision boundaries."""
        # 80%+ -> GO
        decision, effective = decide(80, 100, days=10)
        self.assertEqual(decision, "GO (EXECUTE FULL SIZE)")
        self.assertEqual(effective, 80.0)

        # 65-79.9% -> CONDITIONAL GO
        decision, effective = decide(65, 100, days=10)
        self.assertEqual(decision, "CONDITIONAL GO (REDUCE SIZE 50%)")
        self.assertEqual(effective, 65.0)

        decision, effective = decide(79.9, 100, days=10)
        self.assertEqual(decision, "CONDITIONAL GO (REDUCE SIZE 50%)")
        self.assertEqual(effective, 79.9)

        # < 65% -> NO-GO
        decision, effective = decide(64.9, 100, days=10)
        self.assertEqual(decision, "NO-GO (REJECT)")
        self.assertEqual(effective, 64.9)

    def test_decide_expiry_policy_cap(self):
        """Days < 3 must cap any GO to CONDITIONAL GO."""
        decision, _ = decide(100, 100, days=2)
        self.assertEqual(decision, "CONDITIONAL GO (REDUCE SIZE 50%)")

        decision, _ = decide(100, 100, days=0)
        self.assertEqual(decision, "CONDITIONAL GO (REDUCE SIZE 50%)")

    def test_net_rr_after_costs(self):
        """Transaction costs must properly reduce net R:R."""
        math_res = {
            "live_price": 1000.0,
            "stoploss": 900.0,   # risk = 100
            "target": 1200.0,   # reward = 200 -> raw R:R = 2.0
        }
        net_rr = net_rr_after_costs(math_res, instrument_type="FUT")
        self.assertIsNotNone(net_rr)
        # Net R:R should be slightly less than 2.0 due to cost rate deduction
        self.assertLess(net_rr, 2.0)
        self.assertGreater(net_rr, 1.95)

    def test_break_even_win_rate(self):
        """p* = 1 / (1 + net_rr). With R:R=1, p* should be ~50%."""
        math_res = {
            "live_price": 1000.0,
            "stoploss": 900.0,
            "target": 1100.0,
        }
        p_star = break_even_win_rate(math_res, instrument_type="FUT")
        self.assertIsNotNone(p_star)
        self.assertGreater(p_star, 0.50)  # slightly > 50% due to friction

    def test_math_and_rr_script(self):
        """Verify analyze_math correctly detects inverted R:R and range breaches."""
        trade_data = {
            "entry_price": 1000.0,
            "entry_range_min": 995.0,
            "entry_range_max": 1005.0,
            "live_price": 1020.0,
            "stoploss": 950.0,
            "target": 1050.0,
            "lot_size": 100,
            "expiry": "29 SEP",
        }
        res = analyze_math(trade_data)
        self.assertTrue(res["outside_entry_range"])
        self.assertTrue(res["inverted_rr_flag"])
        self.assertLess(res["live_rr"], 1.0)


if __name__ == "__main__":
    unittest.main()
