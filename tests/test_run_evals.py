"""EVAL-001 seam — the eval runner and its dataset obey the frozen protocol.

Unlike the other seams (`test_[stage].py` per pipeline stage) this one does not
import any pipeline script: `tests/run_evals.py` is the harness for the whole
pipeline. What is frozen here is the *contract around* the runner:

1. The scorecard stays honest on a partial dataset — a gate whose inputs are
   absent reports [SKIP] with a concrete reason, never a hardcoded 100.00%
   ("Tests that can't run yet stay SKIP", never a fake green).
2. A graded regression flips the verdict red (exit 1) — the runner actually
   bites when a golden record turns hostile.
3. The dataset conforms to its own metadata contract (schema keys, breaker
   encoding, class quotas) so the runner never silently degrades.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
RUNNER_FILE = TESTS_DIR / "run_evals.py"
DATASET_FILE = TESTS_DIR / "evals" / "golden_dataset.json"
REPO_ROOT = TESTS_DIR.parent

sys.path.insert(0, str(TESTS_DIR / "evals"))
sys.path.insert(0, str(TESTS_DIR))

import dataset_refs  # noqa: E402  (sibling eval module, spliced onto the path above)
import run_evals  # noqa: E402  (the runner itself, for ensure_reports)


def run_run_evals(benchmark: Path) -> subprocess.CompletedProcess:
    """Execute `tests/run_evals.py` against `benchmark` in a clean interpreter.

    (`__file__` must exist, so this cannot go through `python -c`.)
    `run_evals.py` uses `Path(__file__).parent.parent` as its repo-anchor for
    resolving `DEFAULT_BENCHMARK`; here the overrides are all absolute paths
    passed via `--benchmark`, so the result is independent of cwd.
    """
    return subprocess.run(
        [sys.executable, str(RUNNER_FILE), "--benchmark", str(benchmark)],
        capture_output=True, check=False, cwd=REPO_ROOT,
    )


def stdout_lines(proc: subprocess.CompletedProcess) -> list[str]:
    return proc.stdout.decode("utf-8", "replace").splitlines()


class TestEvalRunnerContract(unittest.TestCase):
    """Every case below runs the real runner against a dataset copy on disk."""

    @classmethod
    def setUpClass(cls):
        if not RUNNER_FILE.exists():
            raise AssertionError(f"eval runner missing: {RUNNER_FILE}")
        if not DATASET_FILE.exists():
            raise AssertionError(f"golden dataset missing: {DATASET_FILE}")
        cls.dataset = json.loads(DATASET_FILE.read_text(encoding="utf-8"))

    def run_with_dataset(self, dataset: dict) -> subprocess.CompletedProcess:
        tmp = self.enterContext(
            tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, encoding="utf-8"))
        json.dump(dataset, tmp, indent=2)
        tmp.close()
        return run_run_evals(Path(tmp.name))

    def test_scorecard_reports_skip_with_reason_for_partial_dataset(self):
        """Partial coverage is honest, not fake green: gated gates stay [SKIP]
        until their inputs exist, and every [SKIP] names its blocking reason."""
        scorecard = "\n".join(stdout_lines(run_run_evals(DATASET_FILE)))
        self.assertIn("[SKIP] Option Rejection Guardrail", scorecard)
        self.assertIn("[SKIP] Directional Brier Score", scorecard)
        # Missing stage-12 artifacts are replayed from the proof root before
        # grading (run_evals.ensure_reports), so both artifact-graded gates
        # grade real bytes even on a fresh clone — never a silent skip. The
        # artifact, not a curator's claim, is what both gates read (ADR-007).
        self.assertIn("[PASS] Zero Hallucination Audit", scorecard)
        self.assertIn("[PASS] HTML Visualizer Validation", scorecard)
        self.assertIn("[PASS] Hard Circuit Breaker Accuracy", scorecard)
        self.assertLess(scorecard.count("[SKIP]"), 7,
                        "runner hardcoded a full-skip beat instead of grading")
        self.assertEqual(scorecard.count("[FAIL]"), 0)

    def test_a_deleted_report_is_regenerated_and_graded(self):
        """Fresh-clone proof: delete one canonical artifact; the runner must
        replay it from the proof root and grade it — not silently skip."""
        dataset = json.loads(DATASET_FILE.read_text(encoding="utf-8"))
        trade = dataset["trades"][0]
        path = REPO_ROOT / "reports" / run_evals.report_path_for(trade).name
        backup = path.read_text(encoding="utf-8") if path.exists() else None

        def _restore():
            if backup is not None and not path.exists():
                path.write_text(backup, encoding="utf-8")

        self.addCleanup(_restore)
        path.unlink(missing_ok=True)

        proc = run_run_evals(DATASET_FILE)
        scorecard = "\n".join(stdout_lines(proc))
        self.assertEqual(proc.returncode, 0, scorecard)
        self.assertTrue(path.exists(),
                        "runner must regenerate the report it cannot grade")
        self.assertIn("[PASS] Zero Hallucination Audit", scorecard)
        self.assertIn("[PASS] HTML Visualizer Validation", scorecard)

    def test_regeneration_failure_fails_the_run(self):
        """A record whose proof row cannot replay must fail the run loudly
        (exit 1) instead of quietly shrinking the graded set."""
        dataset = json.loads(DATASET_FILE.read_text(encoding="utf-8"))
        trade = dataset["trades"][0]
        trade["symbol"] = "ZZZNOTREAL"
        proc = self.run_with_dataset(dataset)
        out = (proc.stdout + proc.stderr).decode("utf-8", "replace")
        self.assertEqual(proc.returncode, 1, out)
        self.assertIn("regeneration", out.lower())
        self.assertIn(str(trade["trade_id"]), out)


import tempfile  # noqa: E402  (kept next to its only consumer)


class TestGoldenDatasetContract(unittest.TestCase):
    """The dataset enforces its own schema before the runner has to."""

    @classmethod
    def setUpClass(cls):
        if not DATASET_FILE.exists():
            raise AssertionError(f"golden dataset missing: {DATASET_FILE}")
        # Payloads may be `$ref`s into the proof root; resolve them so this seam
        # grades the same bytes the runner does. Quarantine provenance (which
        # gates must ignore) is the runner's and the proof seam's concern.
        cls.dataset, cls.ref_notes, cls.derive_notes = dataset_refs.materialize(
            json.loads(DATASET_FILE.read_text(encoding="utf-8")))
        cls.trades = cls.dataset.get("trades", [])
        cls.expected_counts = cls.dataset.get("meta", {}).get("class_counts_expected", {})

    def test_every_trapped_class_encodes_its_frozen_breaker(self):
        """The dataset itself must encode the breaker values the runner grades:
        pledge_hazard → pledge > 25%, inverted_rr → R:R < 1.0,
        earnings_hazard → days_to_earnings < 7, tight_stop → is_tight_sl true."""
        for trade in self.trades:
            cls_name = trade.get("trade_class")
            out = trade.get("script_outputs", {})
            if cls_name == "pledge_hazard":
                pledge = out.get("out_6_fundamental.json", {}).get("promoter_pledge_pct")
                self.assertIsNotNone(pledge, f"{trade['trade_id']}: pledge missing")
                self.assertGreater(pledge, 25.0, f"{trade['trade_id']}: pledge not > 25%")
            elif cls_name == "inverted_rr":
                rr = out.get("out_1_math.json", {}).get("live_rr")
                self.assertIsNotNone(rr, f"{trade['trade_id']}: R:R missing")
                self.assertLess(rr, 1.0, f"{trade['trade_id']}: R:R not < 1.0")
            elif cls_name == "earnings_hazard":
                days = out.get("out_6_fundamental.json", {}).get("days_to_earnings")
                self.assertIsNotNone(days, f"{trade[' trade_id']}: days_to_earnings missing")
                self.assertLess(days, 7.0, f"{trade['trade_id']}: days_to_earnings not < 7")
            elif cls_name == "tight_stop":
                tight = out.get("out_2_market.json", {}).get("is_tight_sl")
                self.assertIs(tight, True, f"{trade['trade_id']}: is_tight_sl not true")
            elif cls_name == "clean_bullish":
                stage2 = out.get("out_2_market.json", {})
                self.assertIs(
                    stage2.get("is_tight_sl"), False,
                    f"{trade['trade_id']}: clean trade must carry is_tight_sl=false "
                    "per the TICK-013 tri-state contract, or omit it entirely")

    def test_clean_trades_are_clean(self):
        """A clean_bullish trade must NOT trip any hard breaker.

        Breakers belong to stages that may not have run for a record: an
        ABSENT stage payload (`None`) is the §10.3 tri-state, not a trip —
        only a PRESENT value is graded, and only against its own threshold.
        """
        for trade in self.trades:
            if trade.get("trade_class") != "clean_bullish":
                continue
            out = trade.get("script_outputs", {})
            rr = out.get("out_1_math.json", {}).get("live_rr")
            pledge = out.get("out_6_fundamental.json", {}).get("promoter_pledge_pct")
            tight = out.get("out_2_market.json", {}).get("is_tight_sl")
            self.assertIsNotNone(rr, f"{trade['trade_id']}: clean trade missing live_rr")
            self.assertGreater(rr, 1.0, f"{trade['trade_id']}: clean trade has R:R <= 1.0")
            if pledge is not None:
                self.assertLess(pledge, 25.0,
                                f"{trade['trade_id']}: clean trade pledge >= 25%")
            self.assertFalse(tight, f"{trade['trade_id']}: clean trade is_tight_sl true")

    def test_no_class_exceeds_its_quota(self):
        """Mid-curation the only quota invariant is 'never exceed'; the final
        50/50 equality is lockable in by `distribution_verified` at curation's
        end (see evals.md §5 lockable invariants)."""
        counted: dict[str, int] = {}
        for trade in self.trades:
            cls_name = trade.get("trade_class")
            counted[cls_name] = counted.get(cls_name, 0) + 1
        for cls_name, quota in self.expected_counts.items():
            self.assertLessEqual(
                counted.get(cls_name, 0), quota,
                f"{cls_name} exceeded its frozen quota of {quota}")


class TestOnDemandReportRegeneration(unittest.TestCase):
    """`run_evals.ensure_reports` replays only what is missing, from the
    proof root, and names every replay it cannot perform."""

    @classmethod
    def setUpClass(cls):
        if not DATASET_FILE.exists():
            raise AssertionError(f"golden dataset missing: {DATASET_FILE}")
        cls.dataset = json.loads(DATASET_FILE.read_text(encoding="utf-8"))
        cls.trade = cls.dataset["trades"][0]

    def test_missing_report_is_regenerated_from_the_proof_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts, failures = run_evals.ensure_reports(
                [dict(self.trade)], reports_dir=Path(tmp))
            self.assertEqual(failures, [], "; ".join(failures))
            produced = Path(tmp) / run_evals.report_path_for(self.trade).name
            self.assertEqual(artifacts, [produced])
            self.assertTrue(produced.exists())
            self.assertTrue(
                produced.read_text(encoding="utf-8").lstrip().lower()
                .startswith("<!doctype html"),
                "the regenerated artifact must be a real standalone report")

    def test_existing_report_is_left_alone(self):
        """Only *missing* reports are replayed — never overwrite on disk."""
        with tempfile.TemporaryDirectory() as tmp:
            sentinel = Path(tmp) / run_evals.report_path_for(self.trade).name
            sentinel.write_text("SENTINEL", encoding="utf-8")
            artifacts, failures = run_evals.ensure_reports(
                [dict(self.trade)], reports_dir=Path(tmp))
            self.assertEqual((artifacts, failures), ([], []))
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "SENTINEL")

    def test_unreplayable_record_fails_loudly_not_silently(self):
        """Wrong identity for its proof row: no artifact, one named failure."""
        tampered = dict(self.trade, symbol="ZZZNOTREAL")
        with tempfile.TemporaryDirectory() as tmp:
            artifacts, failures = run_evals.ensure_reports(
                [tampered], reports_dir=Path(tmp))
            self.assertEqual(artifacts, [])
            self.assertEqual(len(failures), 1, failures)
            self.assertIn(str(tampered["trade_id"]), failures[0])
            self.assertIn("symbol", failures[0])


if __name__ == "__main__":
    unittest.main()
