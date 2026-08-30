"""Test suite validating structural integrity, gating rules, and contracts of prompt.md.

Fast, zero-dependency linting and contract assertion to prevent regressions in prompt.md.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
PROMPT_FILE = ROOT_DIR / "prompt.md"


class TestPromptIntegrity(unittest.TestCase):
    """Asserts that prompt.md contains all mandatory sections, gating rules, and schemas."""

    @classmethod
    def setUpClass(cls):
        cls.assertTrue(PROMPT_FILE.exists(), f"prompt.md not found at {PROMPT_FILE}")
        cls.content = PROMPT_FILE.read_text(encoding="utf-8")

    def test_core_sections_exist(self):
        """Ensure all 7 mandatory prompt sections are intact."""
        required_sections = [
            "ROLE & CORE OBJECTIVE",
            "WORKFLOWS & SCRIPT EXECUTION PIPELINE",
            "GATING & DISCIPLINE RULES",
            "THE HISTORICAL LEARNING LOOP",
            "STANDARDIZED INPUT SCHEMAS",
            "STANDARDIZED OUTPUT REPORT TEMPLATES",
            "EDGE CASES & GUARDRAILS",
        ]
        for section in required_sections:
            self.assertIn(section, self.content, f"Missing mandatory section header: '{section}'")

    def test_decision_scoring_bands(self):
        """Ensure exact score bands (80% GO, 65-79% CONDITIONAL, <65% NO-GO) are specified."""
        self.assertRegex(self.content, r"80\\?%", "Missing 80% GO threshold")
        self.assertRegex(self.content, r"65[–\-]79\\?%", "Missing 65-79% CONDITIONAL GO threshold")
        self.assertRegex(self.content, r"<\s*65\\?%", "Missing <65% NO-GO threshold")

    def test_critical_gating_rules_table(self):
        """Ensure all critical gates and risk-containment rules are documented."""
        required_gates = [
            "Day Quality Gate",
            "AVOID (<30)",
            "Regime Classification",
            "CHOPPY",
            "Entry Readiness Gate",
            "NOT_READY (<40)",
            "Scalp Viability Gate",
            "NOT_SCALPABLE (<40)",
            "Mean Reversion Safety",
            "NOT_A_DIP",
            "Expiry Day Cap",
            "Waiver Model",
        ]
        for gate in required_gates:
            self.assertIn(gate, self.content, f"Missing critical gate in gating table: '{gate}'")

    def test_canonical_input_schema_fields(self):
        """Ensure canonical current_trade.json schema contains all required fields."""
        required_fields = [
            "symbol",
            "expiry",
            "instrument_type",
            "action",
            "entry_price",
            "entry_range_min",
            "entry_range_max",
            "live_price",
            "target",
            "stoploss",
            "lot_size",
            "margin",
            "app_warning",
        ]
        for field in required_fields:
            self.assertIn(f'"{field}"', self.content, f"Missing canonical input field: '{field}'")

    def test_report_templates_contract(self):
        """Ensure report templates define mandatory analytical blocks."""
        required_template_blocks = [
            "TRADE VALIDATION REPORT",
            "FINAL CONFIDENCE SCORE",
            "DECISION:",
            "Day Quality & Macro Regime",
            "Quantitative & Risk Analysis",
            "Similar-Setup Backtest",
            "Technical Signals & Entry Timing",
            "What Would Change the Decision (Flip Table)",
            "Actionable Recommendation",
            "DAILY SCREEN REPORT",
        ]
        for block in required_template_blocks:
            self.assertIn(block, self.content, f"Missing report template block: '{block}'")

    def test_zero_hallucination_guarantee(self):
        """Ensure zero-guesswork / zero-hallucination constraint is strictly stated."""
        self.assertIn("Zero Guesswork", self.content)
        self.assertIn("Zero Hallucination Guarantee", self.content)


if __name__ == "__main__":
    unittest.main()
