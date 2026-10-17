"""Seam — the report-vs-JSON differ is the hallucination check (evals.md §3.1).

The retired `reported_numbers_match_json` boolean was a curator's claim about
a report. This seam freezes the check that replaces it:

1. The differ's rule on controlled inputs — visible-text extraction (CSS/JS
   are not claims), exact-match backing, `/100` scale collapsing, quarantined
   payloads (E5) backing nothing.
2. The runner's gate wiring — graded 100.0 on a faithful stage-12 report,
   0.0 the moment one number is invented, honest [SKIP] when no report exists,
   and honest exclusion when a report exists but no run record proves it.
3. The committed prototype (`reports/sample_report.html`) as a real-HTML
   extraction fixture: its cards are scanned, its stylesheet is not.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
SAMPLE_REPORT = REPO_ROOT / "reports" / "sample_report.html"
STAGE12 = REPO_ROOT / "scripts" / "12_generate_html_report.py"

sys.path.insert(0, str(TESTS_DIR / "evals"))
sys.path.insert(0, str(TESTS_DIR))

import dataset_refs  # noqa: E402  (sibling eval modules, spliced above)
import provenance  # noqa: E402
import report_diff  # noqa: E402
import report_regen  # noqa: E402
import run_evals  # noqa: E402


def load_stage12():
    """Stage 12 as a module: the fixture pins its numbers with stage 12's `fmt`."""
    spec = importlib.util.spec_from_file_location("stage12_for_diff", STAGE12)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prove(*artifacts: Path) -> None:
    """Write the run record a stage-12 replay would leave beside `artifacts`.

    The artifact-graded gates read bytes a run record proves, so a synthetic
    report needs the ledger a real replay writes (`provenance.row`, the runner's
    row shape) before it can be graded at all — that is the point of the
    provenance contract, not a test inconvenience.
    """
    reports = artifacts[0].parent
    rows = [provenance.row(12, "ran", artifact=path.name, artifact_path=path,
                           artifact_sha256=provenance.sha256_of(path), exit_code=0)
            for path in artifacts]
    record = provenance.build_record(
        rows, runner="tests/test_report_diff.py", workdir=reports,
        started_at=provenance.utc_now(), finished_at=provenance.utc_now())
    (reports / provenance.RECORD_NAME).write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8")


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

    def test_indicator_names_and_indices_are_not_claims(self):
        text = wrap("NIFTY 50 above 50 & 200 DMA; NSE 500 RSI 14; 22-Day Cone; P10/P90 bands; EMA(20); stage 12")
        self.assertEqual(report_diff.claims(text), [])

    def test_score_label_and_gauge_ticks_do_not_leak_100(self):
        self.assertEqual(report_diff.claims(wrap("Score / 100 · 0% to 100% · / 9")), [])
        self.assertEqual(report_diff.claims(wrap("Piotroski 8/9")), ["8"])


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

    def test_standard_rounded_numbers_are_backed(self):
        # 8.85% is the standard 2-decimal rounded display of 8.8502%.
        html = wrap("Entry ₹2,974.00 · MAE 8.85% · R:R 1:1.68")
        self.assertEqual(report_diff.diff(html, dict(self.TRADE)), [])

    def test_invented_numbers_are_discrepancies(self):
        html = wrap("Entry ₹2,974.00; target 3,120.00; MAE 15.50%")
        problems = report_diff.diff(html, dict(self.TRADE))
        self.assertEqual(len(problems), 2, problems)
        self.assertIn("3,120.00", problems[0])
        self.assertIn("15.50", problems[1])

    def test_fragments_of_json_numbers_do_not_back_themselves(self):
        # 85 and 8502 are digits *inside* the JSON's 8.8502 — not matches.
        problems = report_diff.diff(wrap("MAE 85.02%"), dict(self.TRADE))
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("85.02", problems[0])

    def test_a_registered_payload_numbers_back_nothing(self):
        """A payload's numbers are evidence only while the register lets them be.

        The register's one row (GB-2023-0003's legacy stage-6 pledge payload) was
        retired once stage 6 could record real evidence, so the E5 rule is pinned
        against a synthetic register instead of a live entry: the *same bytes*
        back nothing while registered and back the report's number once the
        registration is gone. That is what makes the exclusion a property of the
        register — never a hardcoded trade id — and what makes a retired payload's
        bytes gradeable rather than visible-but-inert.
        """
        trade = {
            "trade_id": "GB-TEST-9",
            "symbol": "HDFCBANK",
            "recommendation_date": "2023-11-20",
            "script_outputs": {"out_6_fundamental.json": {"promoter_pledge_pct": 54.2}},
        }
        register = json.dumps({"schema": "uvest-quarantine-register/1",
                               "rows": [{"scenario_id": "GB-TEST-9", "stage": 6,
                                         "artifact": "out_6_fundamental.json"}]})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests" / "proof").mkdir(parents=True)
            path = root / "tests" / "proof" / "quarantine_register.json"
            path.write_text(register, encoding="utf-8")
            with mock.patch.object(dataset_refs, "REPO_ROOT", root):
                self.assertEqual(dataset_refs.quarantined_in(trade),
                                 ["out_6_fundamental.json"])
                problems = report_diff.diff(wrap("Promoter pledge: 54.2%"), trade)
                self.assertEqual(len(problems), 1, problems)
                self.assertIn("54.2", problems[0])
                path.write_text(json.dumps({"schema": "uvest-quarantine-register/1",
                                            "rows": []}), encoding="utf-8")
                self.assertEqual(dataset_refs.quarantined_in(trade), [])
                self.assertEqual(report_diff.diff(wrap("Promoter pledge: 54.2%"),
                                                  trade), [],
                                 "retired bytes are evidence, not decoration")


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
            prove(report)
            clean = run_evals.gate_hallucination([dict(self.TRADE)],
                                                 reports_dir=reports)
            self.assertEqual(clean.value, 100.0)
            self.assertIn("1 graded, 0 with discrepancies", clean.detail)

            # Re-prove the edited bytes: the differ is what is under test here,
            # and the gate only reads bytes a record stands behind.
            report.write_text(wrap("Entry ₹2,974.00 · target ₹9,999.99"),
                              encoding="utf-8")
            prove(report)
            dirty = run_evals.gate_hallucination([dict(self.TRADE)],
                                                 reports_dir=reports)
            self.assertEqual(dirty.value, 0.0)
            self.assertIn("with discrepancies", dirty.detail)
            self.assertIn("9,999.99", dirty.detail)
            status, _ = dirty.evaluate()
            self.assertEqual(status, "[FAIL]")

    def test_an_unproven_report_is_excluded_not_graded(self):
        """Bytes no run record proves are disclosed and excluded.

        A leftover or hand-written report reads exactly like a produced one, so
        grading it would be a claim about a run that cannot be pointed at. It is
        ungraded (never a fake green) and the reason names the missing proof.
        """
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            (reports / run_evals.report_path_for(self.TRADE).name).write_text(
                wrap("Entry ₹2,974.00 · R:R 1:1.68"), encoding="utf-8")
            gate = run_evals.gate_hallucination([dict(self.TRADE)],
                                                reports_dir=reports)
            self.assertTrue(gate.skipped, "unproven bytes must not be graded")
            self.assertIn("proven by a run record", gate.detail)
            self.assertIn("no run record shows stage 12", gate.detail)

    def test_records_without_reports_are_disclosed_not_graded(self):
        """A record with no stage-12 artifact is ungraded; the detail says so."""
        with tempfile.TemporaryDirectory() as tmp:
            gate = run_evals.gate_hallucination([dict(self.TRADE)],
                                                reports_dir=Path(tmp))
        self.assertIn("no stage-12 reports generated yet", gate.detail)


class TestSampleReportFixture(unittest.TestCase):
    """The committed prototype is a real HTML report — cards scan, CSS does not.

    It is a *regenerated* artifact (`report_regen.PROTOTYPE_TRADE_ID`'s own
    stage-12 report), so the numbers pinned here are read out of that record and
    rendered by stage 12's own formatter rather than retyped. The literals this
    replaces (`₹2,974.00` / `₹2,988.20`) belonged to a hand-written mock: pinning
    a number by hand is how that mock drifted years away from the frozen spec.
    """

    @classmethod
    def setUpClass(cls):
        if not SAMPLE_REPORT.exists():
            raise unittest.SkipTest("sample report not present")
        cls.html = SAMPLE_REPORT.read_text(encoding="utf-8")
        cls.stage12 = load_stage12()
        cls.canonical = json.loads(
            report_regen.prototype_job()["canonical"].read_text(encoding="utf-8"))

    def test_cards_are_scanned_and_the_stylesheet_is_not(self):
        found = report_diff.claims(self.html)
        for field in ("entry_price", "target", "stoploss"):
            token = self.stage12.fmt(self.canonical[field])
            with self.subTest(field=field):
                self.assertIn(token, found,
                              f"the record's own {field} ({token}) is a claim the "
                              "report makes, and visible-text extraction must see it")
        self.assertGreater(len(report_diff.claims(self.html)), len(found) - 1)
        raw_with_css = report_diff._NUMBER_RE.findall(self.html)
        self.assertGreater(len(raw_with_css), len(found),
                           "strip styles/scripts must drop stylesheet numbers")


if __name__ == "__main__":
    unittest.main()
