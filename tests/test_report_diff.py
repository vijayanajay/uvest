"""Seam — the report-vs-JSON differ is the hallucination check (evals.md §3.1).

The retired `reported_numbers_match_json` boolean was a curator's claim about
a report. This seam freezes the check that replaces it:

1. The differ's rule on controlled inputs — visible-text extraction (CSS/JS
   are not claims), exact-match backing, `/100` scale collapsing, quarantined
   payloads (E5) backing nothing.
2. The runner's gate wiring — graded 100.0 on a faithful stage-12 report,
   0.0 the moment one number is invented, honest [SKIP] when no report exists.
3. The committed sample report (`reports/sample_report.html`) as a real-HTML
   extraction fixture: its cards are scanned, its stylesheet is not.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
SAMPLE_REPORT = REPO_ROOT / "reports" / "sample_report.html"

sys.path.insert(0, str(TESTS_DIR / "evals"))
sys.path.insert(0, str(TESTS_DIR))

import dataset_refs  # noqa: E402  (sibling eval modules, spliced above)
import report_diff  # noqa: E402
import run_evals  # noqa: E402


def wrap(body: str) -> str:
    """A minimal report whose head carries numbers that are not claims."""
    return (
        "<!doctype html><html><head>"
        "<style>.card { font-size: 13px; width: 999px; }</style>"
        "<script>const scratch = 777;</script>"
        "<!-- 555 hidden -->"
        "</head><body><p>" + body + "</p></body></html>"
    )


class TestVisibleTextExtraction(unittest.TestCase):
    """Only what a human reads can hallucinate."""

    def test_style_script_and_comment_numbers_are_not_claims(self):
        self.assertEqual(report_diff.claims(wrap("entry 100.5")), ["100.5"])

    def test_markup_attributes_are_not_claims(self):
        html = '<div class="col-3" data-n="42"><span>200</span></div>'
        self.assertEqual(report_diff.claims(html), ["200"])

    def test_scale_denominator_is_layout_and_ratios_keep_both_sides(self):
        self.assertEqual(report_diff.claims(wrap("Score 84/100 · R:R 1:1.68")),
                         ["84", "1", "1.68"])


class TestBackingRule(unittest.TestCase):
    """Exact match or discrepancy — the spec's word, byte for byte."""

    TRADE = {
        "trade_id": "GB-TEST-1",
        "symbol": "RELIANCE",
        "recommendation_date": "2026-06-23",
        "entry_price": 2974.0,
        "market_mae_pct": 8.8502,
        "score": 84,
        "script_outputs": {"out_1_math.json": {"live_rr": 1.68,
                                               "max_risk_inr": 16000.0}},
    }

    def test_decorated_backed_numbers_are_clean(self):
        html = wrap("Entry ₹2,974.00 · MAE 8.8502% · R:R 1:1.68 · "
                    "Risk ₹16,000 · on 2026-06-23 · Score 84/100")
        self.assertEqual(report_diff.diff(html, dict(self.TRADE)), [])

    def test_invented_and_rounded_numbers_are_discrepancies(self):
        html = wrap("Entry ₹2,974.00; target 3,120.00; MAE 8.85%")
        problems = report_diff.diff(html, dict(self.TRADE))
        self.assertEqual(len(problems), 2, problems)
        self.assertIn("3,120.00", problems[0])
        self.assertIn("8.85", problems[1])

    def test_fragments_of_json_numbers_do_not_back_themselves(self):
        # 85 and 8502 are digits *inside* the JSON's 8.8502 — not matches.
        problems = report_diff.diff(wrap("MAE 85.02%"), dict(self.TRADE))
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("85.02", problems[0])

    def test_quarantined_payload_numbers_back_nothing(self):
        """GB-2023-0003's stage-6 payload is registered quarantined (E5).

        Its numbers are visible in the record but are never evidence — the
        differ refuses them exactly as the breaker gate does.
        """
        trade = {
            "trade_id": "GB-2023-0003",
            "symbol": "HDFCBANK",
            "recommendation_date": "2023-11-20",
            "script_outputs": {"out_6_fundamental.json": {"promoter_pledge_pct": 54.2}},
        }
        self.assertEqual(dataset_refs.quarantined_in(trade),
                         ["out_6_fundamental.json"])
        problems = report_diff.diff(wrap("Promoter pledge: 54.2%"), trade)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("54.2", problems[0])


class TestHallucinationGate(unittest.TestCase):
    """The runner grades real report bytes, or honestly skips."""

    TRADE = dict(TestBackingRule.TRADE)

    def test_gate_skips_when_no_report_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = run_evals.gate_hallucination([dict(self.TRADE)],
                                                reports_dir=Path(tmp))
        self.assertTrue(gate.skipped, "no report must be ungraded, not a fake green")
        status, _ = gate.evaluate()
        self.assertEqual(status, "[SKIP]")

    def test_gate_grades_a_faithful_report_and_bites_on_an_invented_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            report = reports / run_evals.report_path_for(self.TRADE).name
            self.assertEqual(report.name, "report_RELIANCE_20260623.html")
            report.write_text(wrap("Entry ₹2,974.00 · R:R 1:1.68 · "
                                   "Score 84/100"), encoding="utf-8")
            clean = run_evals.gate_hallucination([dict(self.TRADE)],
                                                 reports_dir=reports)
            self.assertEqual(clean.value, 100.0)
            self.assertIn("1 graded, 0 with discrepancies", clean.detail)

            report.write_text(wrap("Entry ₹2,974.00 · target ₹9,999.99"),
                              encoding="utf-8")
            dirty = run_evals.gate_hallucination([dict(self.TRADE)],
                                                 reports_dir=reports)
            self.assertEqual(dirty.value, 0.0)
            self.assertIn("with discrepancies", dirty.detail)
            self.assertIn("9,999.99", dirty.detail)
            status, _ = dirty.evaluate()
            self.assertEqual(status, "[FAIL]")

    def test_records_without_reports_are_disclosed_not_graded(self):
        """A record with no stage-12 artifact is ungraded; the detail says so."""
        with tempfile.TemporaryDirectory() as tmp:
            gate = run_evals.gate_hallucination([dict(self.TRADE)],
                                                reports_dir=Path(tmp))
        self.assertIn("no stage-12 reports generated yet", gate.detail)


class TestSampleReportFixture(unittest.TestCase):
    """The committed sample is a real HTML report — cards scan, CSS does not."""

    @classmethod
    def setUpClass(cls):
        if not SAMPLE_REPORT.exists():
            raise unittest.SkipTest("sample report not present")
        cls.html = SAMPLE_REPORT.read_text(encoding="utf-8")

    def test_cards_are_scanned_and_the_stylesheet_is_not(self):
        found = report_diff.claims(self.html)
        self.assertIn("2,974.00", found, "the execution card number must be scanned")
        self.assertIn("2,988.20", found, "the live price must be scanned")
        self.assertGreater(len(report_diff.claims(self.html)), len(found) - 1)
        raw_with_css = report_diff._NUMBER_RE.findall(self.html)
        self.assertGreater(len(raw_with_css), len(found),
                           "strip styles/scripts must drop stylesheet numbers")


if __name__ == "__main__":
    unittest.main()
