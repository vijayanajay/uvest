"""Test suite validating structural integrity, gating rules, and contracts of prompt.md and BRD.md.

Fast, zero-dependency linting and contract assertion to prevent regressions in prompt.md.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
PROMPT_FILE = ROOT_DIR / "prompt.md"
BRD_FILE = ROOT_DIR / "BRD.md"


class TestPromptIntegrity(unittest.TestCase):
    """Asserts that prompt.md and BRD.md contain all mandatory sections, gating rules, and schemas."""

    @classmethod
    def setUpClass(cls):
        cls.assertTrue(PROMPT_FILE.exists(), f"prompt.md not found at {PROMPT_FILE}")
        cls.assertTrue(BRD_FILE.exists(), f"BRD.md not found at {BRD_FILE}")
        cls.prompt_content = PROMPT_FILE.read_text(encoding="utf-8")
        cls.brd_content = BRD_FILE.read_text(encoding="utf-8")

    def test_core_sections_exist(self):
        """Ensure all mandatory sections in prompt.md are intact."""
        required_sections = [
            "ROLE & CORE OBJECTIVE",
            "EXECUTION PIPELINE & TOOL ORCHESTRATION",
            "GATING RULES & HARD CIRCUIT BREAKERS",
            "STANDARDIZED CANONICAL SCHEMAS",
            "STANDARDIZED OUTPUT REPORT FORMAT",
            "DAILY POSITION TRIAGE & ADVERSE MOVEMENT PROTOCOL",
            "CONTINUOUS LEARNING LOOP & FAILURE AUTOPSY",
        ]
        for section in required_sections:
            self.assertIn(section, self.prompt_content, f"Missing mandatory section header: '{section}'")

    def test_three_core_predictions(self):
        """Ensure the Three Core Predictions are explicitly defined."""
        required_predictions = [
            "Direction (1-Month Swing Trajectory)",
            "Expected Range (1-Month Volatility Cone)",
            "Optimal Price Range (Precision Execution Zone)",
        ]
        for pred in required_predictions:
            self.assertIn(pred, self.prompt_content, f"Missing core prediction: '{pred}'")

    def test_options_prohibition_rule(self):
        """Ensure strict prohibition of options trading is enforced."""
        self.assertIn("NO OPTIONS EVER", self.prompt_content)
        self.assertIn("NO OPTIONS TRADING EVER", self.brd_content)

    def test_critical_circuit_breakers(self):
        """Ensure all critical gates and risk-containment rules are documented."""
        required_gates = [
            "Options Prohibition",
            "F&O Ban List",
            "Promoter Pledging",
            "Inverted Risk-Reward",
            "Earnings Shock Hazard",
            "Volatility Noise Stop",
            "Panic Volatility Regime",
        ]
        for gate in required_gates:
            self.assertIn(gate, self.prompt_content, f"Missing critical gate in gating table: '{gate}'")

    def test_canonical_input_schema_fields(self):
        """Ensure canonical current_trade.json schema contains all required fields."""
        required_fields = [
            "symbol",
            "exchange",
            "instrument_type",
            "expiry",
            "action",
            "entry_price",
            "entry_range_min",
            "entry_range_max",
            "live_price",
            "target",
            "stoploss",
            "lot_size",
            "margin",
            "recommended_date",
            "app_warning",
            "source",
            "user_capital_inr",
        ]
        for field in required_fields:
            self.assertIn(f'"{field}"', self.prompt_content, f"Missing canonical input field: '{field}'")

    def test_tool_pipeline_scripts(self):
        """Ensure all required script tool executions are defined in prompt.md."""
        required_scripts = [
            "0_ingest_reco.py",
            "1_math_and_rr.py",
            "2_market_data.py",
            "3_dtw_pattern.py",
            "4_ml_direction.py",
            "5_institutional_flow.py",
            "6_fundamental_audit.py",
            "7_sector_macro_regime.py",
            "8_dhan_precision.py",
            "9_decision_engine.py",
            "10_daily_triage.py",
            "12_generate_html_report.py",
        ]
        for script in required_scripts:
            self.assertIn(script, self.prompt_content, f"Missing script execution step: '{script}'")

    def test_html_visualizer_report_defined(self):
        """Ensure HTML report generation is part of the output."""
        self.assertIn("12_generate_html_report.py", self.prompt_content)
        self.assertIn("reports/report_", self.prompt_content)

    def test_zero_hallucination_guarantee(self):
        """Ensure zero-hallucination constraint is strictly stated."""
        self.assertIn("Zero Hallucination", self.prompt_content)
        self.assertIn("Zero Hallucination", self.brd_content)


if __name__ == "__main__":
    unittest.main()
