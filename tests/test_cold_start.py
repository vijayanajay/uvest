"""Seam — the clean-checkout proof (`tests/evals/cold_start.py`).

CI runs that script on a fresh checkout to prove the provenance path no warm
developer machine ever exercises: reproduce every stage-12 report from the proof
root, write the ledger, grade what the ledger proves, then reuse it. Two things
here must stay true, or the CI step becomes a false green:

1. **A non-cold tree is refused, never graded.** The proof is only a proof if the
   tree was actually cold, so the precondition check must name what makes it
   warm — generated reports, or the ledger the cold run is supposed to write.
2. **The parser keeps matching the harness.** The script asserts on the harness's
   own printed lines (the replay line, the proven count, the completeness line);
   if the harness is reworded, the coupling must fail loudly here rather than
   turn into a silently weaker assertion at the CI step. Both halves of the
   completeness line are parsed, so the wording is pinned here: stage-12 evidence
   *and* the gate payloads every graded gate reads.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent

sys.path.insert(0, str(TESTS_DIR / "evals"))
sys.path.insert(0, str(TESTS_DIR))

import cold_start  # noqa: E402  (sibling eval module, spliced above)


class TestColdPrecondition(unittest.TestCase):
    """Only a checkout with no generated evidence counts as cold."""

    def test_a_clean_checkout_is_cold(self):
        """The committed sample report is not generated evidence."""
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            (reports / "sample_report.html").write_text("<!doctype html>",
                                                        encoding="utf-8")
            self.assertEqual(cold_start.check_cold(reports), [])
            self.assertNotIn("sample_report.html", cold_start.report_artifacts(reports))

    def test_a_missing_reports_directory_is_cold(self):
        """A bare checkout may not even have the directory yet."""
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(cold_start.check_cold(Path(tmp) / "reports"), [])

    def test_generated_reports_make_the_tree_warm(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            (reports / "report_RELIANCE_20220411.html").write_text("x", encoding="utf-8")
            problems = cold_start.check_cold(reports)
            self.assertEqual(len(problems), 1, problems)
            self.assertIn("generated report", problems[0])
            self.assertIn("report_RELIANCE_20220411.html", problems[0])

    def test_the_ledger_makes_the_tree_warm(self):
        """Reports without a ledger is NOT the cold path either: it is verify-mode."""
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            (reports / "run_record.json").write_text("{}", encoding="utf-8")
            problems = cold_start.check_cold(reports)
            self.assertEqual(len(problems), 1, problems)
            self.assertIn("run_record.json", problems[0])


class TestHarnessOutputCoupling(unittest.TestCase):
    """The lines the proof asserts on must still be printed by the harness."""

    HARNESS_SOURCE = REPO_ROOT / "tests" / "run_evals.py"

    def setUp(self):
        self.source = self.HARNESS_SOURCE.read_text(encoding="utf-8")

    def test_every_parsed_line_still_exists_in_the_harness(self):
        # Including the two halves of the completeness line: the regex below is a
        # literal sample, so the wording it samples has to exist in the harness or
        # the sample would be pinning a line nobody prints.
        for needle in ("↻ stage-12 replay: ", "Stage-12 reports proven: ",
                       "Evidence completeness: ", "FINAL EVALUATION VERDICT: ",
                       "carry proven stage-12 evidence",
                       "with no unregistered gap"):
            self.assertIn(needle, self.source,
                          f"run_evals.py no longer prints {needle!r} — "
                          "cold_start.py parses it; update both together")

    def test_the_proof_reads_back_what_it_print_reads(self):
        """The script's own summary must be traceable to the harness's wording."""
        cold = cold_start.COLD_REPLAY_RE
        sample = ("  ↻ stage-12 replay: 31 report(s) missing or unproven — "
                  "31 written, 0 verified by reproduction, before grading")
        self.assertEqual(cold.search(sample).groups(), ("31", "31", "0"))
        self.assertEqual(cold_start.PROVEN_RE.search(
            "  Stage-12 reports proven: 31/31").groups(), ("31", "31"))
        # Both halves of the roster: a cold run that reproduces every report but
        # leaves a gate payload unproven is not a complete cold path.
        self.assertEqual(cold_start.COVERAGE_RE.search(
            "  Evidence completeness: OK (31/31 record(s) carry proven stage-12 "
            "evidence; 31/31 record(s) carry gate payloads with no unregistered "
            "gap)").groups(),
            ("31", "31", "31", "31"))


if __name__ == "__main__":
    unittest.main()
