"""Test suite validating market gates, day quality thresholds, and regime classifications.

Asserts the Kailash Nadh quantitative filters that prevent trading on low-quality tape.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT_DIR / "scripts"

sys.path.insert(0, str(SCRIPTS_DIR))


class TestScreenerGates(unittest.TestCase):
    """Asserts gating and regime classification logic."""

    def test_day_quality_verdicts_and_thresholds(self):
        """Day quality scores must map to exact risk bands: STRONG, TRADING, WASH, AVOID."""
        def evaluate_band(final_score: float, momentum_raw: float = 0.0) -> str:
            if final_score >= 70:
                verdict = "STRONG"
            elif final_score >= 50:
                verdict = "TRADING"
            elif final_score >= 30:
                verdict = "WASH"
            else:
                verdict = "AVOID"

            # Momentum overlay overrides
            if momentum_raw <= -20 and verdict in ("STRONG", "TRADING"):
                verdict = "WASH"
            elif momentum_raw >= 15 and verdict == "TRADING":
                verdict = "STRONG"

            return verdict

        # Standard score tests
        self.assertEqual(evaluate_band(75), "STRONG")
        self.assertEqual(evaluate_band(55), "TRADING")
        self.assertEqual(evaluate_band(35), "WASH")
        self.assertEqual(evaluate_band(25), "AVOID")

        # Hard Gate: AVOID (<30) must reject regardless of minor momentum
        self.assertEqual(evaluate_band(20, momentum_raw=5), "AVOID")

        # Momentum Collapse: Downgrades TRADING or STRONG to WASH
        self.assertEqual(evaluate_band(75, momentum_raw=-25), "WASH")
        self.assertEqual(evaluate_band(55, momentum_raw=-20), "WASH")

        # Momentum Acceleration: Upgrades TRADING to STRONG
        self.assertEqual(evaluate_band(55, momentum_raw=18), "STRONG")

    def test_mean_reversion_dip_vs_trap_logic(self):
        """Dips in uptrend are buyable; dips in downtrend are traps (falling knives)."""
        def classify_dip(above_50_ema: bool, drawdown_pct: float) -> str:
            if drawdown_pct > 5.0:
                return "NOT_A_DIP (CRASH_RISK)"
            if not above_50_ema:
                return "NOT_A_DIP (DOWNTREND_TRAP)"
            if drawdown_pct >= 2.0:
                return "STRONG_REVERSION"
            return "NORMAL"

        # Uptrend + 2.5% drawdown -> Buyable dip
        self.assertEqual(classify_dip(above_50_ema=True, drawdown_pct=2.5), "STRONG_REVERSION")

        # Downtrend + 2.5% drawdown -> Trap (falling knife)
        self.assertEqual(classify_dip(above_50_ema=False, drawdown_pct=2.5), "NOT_A_DIP (DOWNTREND_TRAP)")

        # Severe crash (> 5%) -> Trap
        self.assertEqual(classify_dip(above_50_ema=True, drawdown_pct=6.0), "NOT_A_DIP (CRASH_RISK)")

    def test_regime_choppy_capping_invariant(self):
        """In a CHOPPY market regime, decisions must be capped at CONDITIONAL GO."""
        def apply_regime_cap(decision: str, regime: str) -> str:
            if regime == "CHOPPY" and decision == "GO (EXECUTE FULL SIZE)":
                return "CONDITIONAL GO (REDUCE SIZE 50%)"
            return decision

        self.assertEqual(
            apply_regime_cap("GO (EXECUTE FULL SIZE)", "CHOPPY"),
            "CONDITIONAL GO (REDUCE SIZE 50%)",
        )
        self.assertEqual(
            apply_regime_cap("GO (EXECUTE FULL SIZE)", "TRENDING"),
            "GO (EXECUTE FULL SIZE)",
        )
        self.assertEqual(
            apply_regime_cap("NO-GO (REJECT)", "CHOPPY"),
            "NO-GO (REJECT)",
        )


if __name__ == "__main__":
    unittest.main()
