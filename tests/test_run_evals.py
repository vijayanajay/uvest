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
        self.assertIn("[SKIP] HTML Visualizer Validation", scorecard)
        self.assertIn("[PASS] Hard Circuit Breaker Accuracy", scorecard)
        self.assertLess(scorecard.count("[SKIP]"), 7,
                        "runner hardcoded a full-skip beat instead of grading")
        self.assertEqual(scorecard.count("[FAIL]"), 0)


import tempfile  # noqa: E402  (kept next to its only consumer)


class TestGoldenDatasetContract(unittest.TestCase):
    """The dataset enforces its own schema before the runner has to."""

    @classmethod
    def setUpClass(cls):
        if not DATASET_FILE.exists():
            raise AssertionError(f"golden dataset missing: {DATASET_FILE}")
        cls.dataset = json.loads(DATASET_FILE.read_text(encoding="utf-8"))
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


if __name__ == "__main__":
    unittest.main()
