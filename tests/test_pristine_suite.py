"""Seam — the pristine-checkout proof (`tests/evals/pristine_suite.py`).

CI runs that script on a checkout nobody has warmed to prove the invariant suite
is self-sufficient: it must go green with no `reports/` at all, producing the
stage-12 evidence its own artifact gates grade. A green exit code alone does not
prove that — a suite that passed while producing nothing would be a false green —
so three things here must stay true:

1. **The verdict is read, not assumed.** The proof hangs off the runner's own
   footer counts; if that format drifts the parser must return nothing (and the
   proof must fail) rather than read a pass out of a line it no longer matches.
2. **Passing is not enough.** The evidence the run leaves behind is the second
   half of the claim, so every way it can be insufficient — no reports, no
   ledger, wrong row count, bytes that moved, a ledger that contradicts the disk
   — must be reported by name.
3. **A warm tree is refused before the suite is spent.** On a tree that already
   holds the artifacts the claim is vacuous, so the refusal has to come first.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent

sys.path.insert(0, str(TESTS_DIR / "evals"))
sys.path.insert(0, str(TESTS_DIR))

import cold_start  # noqa: E402  (sibling eval module, spliced above)
import pristine_suite  # noqa: E402
import provenance  # noqa: E402


class TestSuiteFooterCoupling(unittest.TestCase):
    """The proof reads the runner's own counts; that wording is the contract."""

    RUNNER = REPO_ROOT / "tests" / "run_all_tests.py"

    def test_the_parser_reads_the_runners_footer(self):
        line = ("  ⏱️  Execution Time: 12000.0ms (12.00s)\n"
                "📊 Total Tests: 207 | Passed: 207 | Failures: 0 | Errors: 0\n"
                "✅ ALL INVARIANTS & DECISION GATES PASSED.")
        self.assertEqual(pristine_suite.parse_suite_summary(line), (207, 207, 0, 0))

    def test_the_runners_footer_still_carries_every_field_parsed(self):
        source = self.RUNNER.read_text(encoding="utf-8")
        for segment in ("📊 Total Tests: ", " | Passed: ", " | Failures: ",
                        " | Errors: "):
            self.assertIn(segment, source,
                          f"run_all_tests.py no longer prints {segment!r} — "
                          "pristine_suite.py parses that line; update both together")

    def test_a_drifted_footer_is_not_read_as_a_pass(self):
        """No match must mean "cannot read the result", never a silent green."""
        for drifted in ("Total Tests: 207 | Passed: 207",
                        "Ran 207 tests in 12.000s\n\nOK",
                        "📊 Total Tests: 207 | Failures: 0 | Errors: 0"):
            self.assertIsNone(pristine_suite.parse_suite_summary(drifted), drifted)


def _write_record(reports: Path, rows: list[dict]) -> Path:
    ledger = reports / provenance.RECORD_NAME
    ledger.write_text(json.dumps(provenance.build_record(
        rows, runner="tests/run_all_tests.py", workdir=reports)),
        encoding="utf-8")
    return ledger


def _artifact(reports: Path, name: str) -> Path:
    path = reports / name
    path.write_text(f"<!doctype html><title>{name}</title>", encoding="utf-8")
    return path


class TestEvidenceBehindTheGreenRun(unittest.TestCase):
    """Green is only half the claim: the run must also have produced the proof."""

    def test_a_green_run_that_produced_nothing_is_a_problem(self):
        with tempfile.TemporaryDirectory() as tmp:
            problems = pristine_suite.evidence_problems(Path(tmp), total=3)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("no generated stage-12 report", problems[0])

    def test_reports_without_a_ledger_prove_nothing(self):
        """Bytes on disk are not evidence until a record names their hashes."""
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            _artifact(reports, "report_RELIANCE_20220411.html")
            problems = pristine_suite.evidence_problems(reports, total=1)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("no ledger", problems[0])
        self.assertIn(provenance.RECORD_NAME, problems[0])

    def test_reports_with_a_matching_ledger_are_sufficient(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            rows = []
            for name in ("report_RELIANCE_20220411.html",
                         "report_INFY_20220411.html"):
                path = _artifact(reports, name)
                rows.append(provenance.row(
                    12, "ran", artifact=name, artifact_path=path,
                    artifact_sha256=provenance.sha256_of(path), exit_code=0))
            _write_record(reports, rows)
            self.assertEqual(pristine_suite.evidence_problems(reports, total=2), [])

    def test_a_short_ledger_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            rows = []
            for name in ("report_RELIANCE_20220411.html",
                         "report_INFY_20220411.html"):
                path = _artifact(reports, name)
                rows.append(provenance.row(
                    12, "ran", artifact=name, artifact_path=path,
                    artifact_sha256=provenance.sha256_of(path), exit_code=0))
            _write_record(reports, rows[:1])
            problems = pristine_suite.evidence_problems(reports, total=2)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("1 `ran` row(s)", problems[0])
        self.assertIn("expected 2 stage-12 row(s)", problems[0])

    def test_a_non_stage_12_row_does_not_count_as_report_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            path = _artifact(reports, "report_RELIANCE_20220411.html")
            _write_record(reports, [provenance.row(
                1, "ran", artifact=path.name, artifact_path=path,
                artifact_sha256=provenance.sha256_of(path), exit_code=0)])
            problems = pristine_suite.evidence_problems(reports, total=1)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("[1]", problems[0])

    def test_bytes_that_moved_after_the_ledger_was_written_are_reported(self):
        """An edited report is the case this whole proof exists to catch."""
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            path = _artifact(reports, "report_RELIANCE_20220411.html")
            rows = [provenance.row(12, "ran", artifact=path.name, artifact_path=path,
                                   artifact_sha256=provenance.sha256_of(path),
                                   exit_code=0)]
            _write_record(reports, rows)
            path.write_text("<!doctype html><!-- hand-edited -->", encoding="utf-8")
            problems = pristine_suite.evidence_problems(reports, total=1)
        self.assertEqual(len(problems), 2, problems)
        self.assertTrue(any("do not match the bytes on disk" in p for p in problems),
                        problems)
        self.assertTrue(any("contradicts the artifacts it names" in p for p in problems),
                        problems)

    def test_an_unreadable_ledger_is_reported_not_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            _artifact(reports, "report_RELIANCE_20220411.html")
            (reports / provenance.RECORD_NAME).write_text('{"schema": "other"}',
                                                          encoding="utf-8")
            problems = pristine_suite.evidence_problems(reports, total=1)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("unreadable ledger", problems[0])


class TestAGreenRunIsNotEnough(unittest.TestCase):
    """The failure this proof exists to catch: a green suite that left no evidence."""

    GREEN = ("⏱️  Execution Time: 12000.0ms (12.00s)\n"
             "📊 Total Tests: 223 | Passed: 223 | Failures: 0 | Errors: 0\n"
             "✅ ALL INVARIANTS & DECISION GATES PASSED.\n")

    def _main(self, code: int, output: str, reports: Path) -> tuple[int, str]:
        stderr = io.StringIO()
        with mock.patch.object(cold_start, "REPORTS_DIR", reports), \
                mock.patch.object(pristine_suite, "run_suite",
                                  return_value=(code, output)), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(stderr):
            return pristine_suite.main(), stderr.getvalue()

    def test_a_green_suite_that_produced_no_evidence_fails_the_proof(self):
        """Exit 0 and 223/223 on a tree left empty is not a proof of anything."""
        with tempfile.TemporaryDirectory() as tmp:
            code, err = self._main(0, self.GREEN, Path(tmp))
        self.assertEqual(code, 1)
        self.assertIn("no generated stage-12 report", err)

    def test_a_runner_exit_code_of_zero_does_not_override_failing_counts(self):
        """The counts are the claim, so they are read rather than assumed."""
        failed = self.GREEN.replace("223 | Passed: 223", "223 | Passed: 221").replace(
            "Failures: 0 | Errors: 0", "Failures: 2 | Errors: 0")
        with tempfile.TemporaryDirectory() as tmp:
            code, err = self._main(0, failed, Path(tmp))
        self.assertEqual(code, 1)
        self.assertIn("did not pass on a pristine checkout", err)

    def test_an_all_green_footer_with_a_nonzero_exit_is_not_trusted(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, err = self._main(1, self.GREEN, Path(tmp))
        self.assertEqual(code, 1)
        self.assertIn("did not pass on a pristine checkout", err)


class TestTheCiChainOrder(unittest.TestCase):
    """CI's step order is load-bearing: both proofs refuse a warm tree.

    Parsed with a line scanner rather than a YAML library on purpose — the suite
    is stdlib-only (requirements.txt serves the pipeline, not the tests), and the
    workflow's shape here is two keys per step. A reshape that defeats the scan
    fails this seam instead of passing it silently.
    """

    WORKFLOW = REPO_ROOT / ".github" / "workflows" / "uvest.yml"
    STEP_NAME_RE = re.compile(r"^      - name: (.+)$")
    RUN_RE = re.compile(r"^        run: (.+)$")

    def setUp(self):
        self.steps: list[tuple[str, str]] = []
        for line in self.WORKFLOW.read_text(encoding="utf-8").splitlines():
            name = self.STEP_NAME_RE.match(line)
            if name:
                self.steps.append((name.group(1).strip(), ""))
                continue
            run = self.RUN_RE.match(line)
            if run and self.steps:
                self.steps[-1] = (self.steps[-1][0], run.group(1).strip())
        self.assertIn("Install pinned runtime dependencies",
                      [name for name, _ in self.steps],
                      "the workflow no longer parses — update this scanner with it")

    def _index_of(self, marker: str) -> int:
        for index, (name, run) in enumerate(self.steps):
            if marker in name or marker in run:
                return index
        raise AssertionError(f"no CI step matches {marker!r}; steps: "
                             f"{[name for name, _ in self.steps]}")

    def test_nothing_warms_reports_before_the_pristine_proof(self):
        """Nothing may warm reports/ before the fresh-clone claim is made."""
        pristine = self._index_of("pristine_suite.py")
        warming = [name for name, run in self.steps[:pristine]
                   if any(marker in run for marker in
                          ("run_all_tests.py", "run_evals.py", "cold_start.py"))]
        self.assertEqual(
            warming, [],
            "a suite/eval step runs before the pristine proof — the proof would "
            "then be asserting a tree that was already warm")

    def test_the_pristine_proof_is_the_suites_only_ci_run(self):
        """It runs the suite itself, so a second plain suite step only duplicates it."""
        runners = [name for name, run in self.steps
                   if "run_all_tests.py" in run or "pristine_suite.py" in run]
        self.assertEqual(len(runners), 1, runners)

    def test_the_tree_is_returned_to_pristine_before_the_cold_proof(self):
        """The chain is one arrow: prove pristine → clean → prove cold."""
        clean = self.steps[self._index_of("git clean")]
        self.assertIn("-X", clean[1], "-X keeps tracked files (sample_report.html)")
        self.assertIn("reports", clean[1])
        self.assertLess(self._index_of("pristine_suite.py"), self._index_of("git clean"))
        self.assertLess(self._index_of("git clean"), self._index_of("cold_start.py"))

    def test_the_cold_provenance_proof_closes_the_chain(self):
        self.assertEqual(self.steps[-1][1], "python tests/evals/cold_start.py")


class TestWarmTreeIsRefusedBeforeTheSuiteRuns(unittest.TestCase):
    """A vacuous claim must be refused up front, not discovered afterwards."""

    def test_a_warm_tree_exits_2_without_spending_the_suite(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            _artifact(reports, "report_RELIANCE_20220411.html")
            stderr = io.StringIO()
            with mock.patch.object(cold_start, "REPORTS_DIR", reports), \
                    mock.patch.object(pristine_suite, "run_suite",
                                      side_effect=AssertionError(
                                          "the suite ran on a tree that was not "
                                          "pristine")), \
                    contextlib.redirect_stderr(stderr):
                code = pristine_suite.main()
        self.assertEqual(code, 2)
        self.assertIn("report_RELIANCE_20220411.html", stderr.getvalue())

    def test_a_ledger_only_tree_is_refused_too(self):
        """Reports-without-ledger is verify-mode; a ledger-without-reports tree is
        not the pristine path either."""
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            _write_record(reports, [provenance.row(12, "skipped",
                                                   skip_reason="not this run")])
            stderr = io.StringIO()
            with mock.patch.object(cold_start, "REPORTS_DIR", reports), \
                    mock.patch.object(pristine_suite, "run_suite",
                                      side_effect=AssertionError("suite ran")), \
                    contextlib.redirect_stderr(stderr):
                code = pristine_suite.main()
        self.assertEqual(code, 2)
        self.assertIn(provenance.RECORD_NAME, stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
