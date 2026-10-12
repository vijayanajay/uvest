"""Seam — scripts/run_pipeline.py, the end-to-end 0 → 12 chain (BRD §9.1, prompt.md §2).

Frozen here:

1. **One invocation, canonical order.** The runner executes the ascending
   stage chain, forwarding each stage's stdout into its BRD §9.1 artifact, and
   aborts at the first failing stage while propagating its exit code — stage
   0's options gate (`ERR_OPTIONS_PROHIBITED`, exit 2) must survive the chain.
2. **Absence is disclosed, never fatal.** Stages whose script does not exist
   yet print `[SKIP]` and their evidence stays waived downstream (BRD §10.4);
   stage 11 is disclosed as the conditional stage that never runs in the
   validation chain (BRD §9.1).
3. **The artifacts are real.** `out_9_decision.json` carries the stage-9
   contract keys, and stage 12 writes `reports/report_{SYMBOL}_{DATE}.html`
   under the run's workdir.
4. **Frozen names.** The runner's artifact table agrees with
   `scripts/_recorder.py`'s `FROZEN_ARTIFACTS` for every shared stage, and
   importing the runner never writes to stdout (main-guard discipline).
5. **The run record.** `run_record.json` in the workdir accounts for every
   stage in ORDER — order, status, artifact path, skip reason, exit code — so
   provenance survives the console log. An aborted run is recorded too (that is
   when provenance is questioned), and a row names an artifact only when the
   file was really produced and verified: never a path that was merely tried.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
RUNNER = SCRIPTS_DIR / "run_pipeline.py"
INPUT_FIXTURE = TESTS_DIR / "fixtures" / "_input_reliance_20261009.txt"

sys.path.insert(0, str(SCRIPTS_DIR))

import _recorder  # noqa: E402  (scripts dir spliced above)

OPTION_INPUT = (
    "Symbol: RELIANCE\nType: CE\nEntry Range: 2975.00-2985.00\n"
    "Live Price: 2988.20\nStoploss: 2910.00\nTarget: 3120.00\n"
    "Lot Size: 250\nRecommended Date: 2026-10-09\n"
)

STAGE9_CONTRACT_KEYS = {
    "final_score", "max_possible_score", "effective_score_pct",
    "decision", "verdict", "p_win", "expectancy",
    "circuit_breakers_tripped", "three_predictions", "flip_table",
    "what_would_flip_me",
}


def run_runner(workdir: Path, input_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(RUNNER), "--input", str(input_path),
         "--workdir", str(workdir)],
        capture_output=True, text=True, encoding="utf-8",
        check=False, cwd=str(REPO_ROOT),
    )


def load_runner_module():
    """Import the runner as a module, capturing stdio to prove the main guard."""
    spec = importlib.util.spec_from_file_location("uvest_run_pipeline", RUNNER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    stdout_buf, stderr_buf = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
        spec.loader.exec_module(module)
    return module, stdout_buf.getvalue(), stderr_buf.getvalue()


class TestOneInvocationChain(unittest.TestCase):
    """The acceptance property: one command runs 0 → 12 and leaves the artifacts."""

    def test_one_invocation_runs_the_chain_and_produces_the_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp)
            proc = run_runner(wd, INPUT_FIXTURE)
            out, err = proc.stdout, proc.stderr
            self.assertEqual(proc.returncode, 0, f"stdout:\n{out}\nstderr:\n{err}")

            # 1. Ascending canonical order for every stage that exists.
            runs = [int(s) for s in re.findall(r"\[RUN\] stage (\d+)", out)]
            self.assertEqual(runs, [0, 1, 2, 3, 5, 9, 12],
                             f"chain order wrong: {runs}\n{out}")

            # 2. Every absent stage is disclosed, and stage 11 is named as the
            #    conditional stage — silence would be a hidden gap.
            for stage in (4, 6, 7, 8, 10):
                self.assertIn(f"[SKIP] stage {stage}:", out,
                              f"missing disclosure for stage {stage}")
            self.assertIn("[SKIP] stage 11:", out)
            self.assertIn("conditional", out.split("[SKIP] stage 11:")[1][:120].lower())

            # 3. Artifacts of every executed stage land in the workdir.
            for name in ("current_trade.json", "out_1_math.json",
                         "out_2_market.json", "out_3_dtw.json",
                         "out_5_flow.json", "out_9_decision.json"):
                path = wd / name
                self.assertTrue(path.exists(), f"artifact missing: {name}")
                json.loads(path.read_text(encoding="utf-8"))  # must be real JSON

            reports = sorted((wd / "reports").glob("report_RELIANCE_*.html"))
            self.assertEqual(len(reports), 1,
                             f"expected exactly one stage-12 report: {out}")

            # 4. Stage 9's verdict is the canonical contract, and the waiver
            #    rule (no fixtures → sources waived) is disclosed, not silent.
            verdict = json.loads((wd / "out_9_decision.json").read_text(encoding="utf-8"))
            self.assertTrue(STAGE9_CONTRACT_KEYS.issubset(verdict),
                            f"missing: {STAGE9_CONTRACT_KEYS - set(verdict)}")
            self.assertIn(verdict["decision"], {"GO", "GO_WEAK", "WAIT", "AVOID"})
            self.assertTrue(verdict["confidence_deficit"],
                            "waived sources must be named in the confidence deficit")

            # 5. The summary reads the artifacts it produced (zero hallucination).
            self.assertIn("VERDICT:", out)
            self.assertIn(verdict["decision"], out)
            self.assertIn("reports/report_RELIANCE_", out)

    def test_options_recommendation_aborts_the_chain_with_exit_2(self):
        """The ADR-001 hard gate must hold through the orchestrator: nothing runs
        after stage 0 rejects a CE/PE recommendation."""
        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp)
            bad = wd / "option_reco.txt"
            bad.write_text(OPTION_INPUT, encoding="utf-8")
            proc = run_runner(wd, bad)
            combined = proc.stdout + proc.stderr
            self.assertEqual(proc.returncode, 2, combined)
            self.assertIn("ERR_OPTIONS_PROHIBITED", combined)
            self.assertNotIn("[RUN] stage 1:", proc.stdout,
                             "the chain must stop at the ingestion gate")
            self.assertFalse((wd / "out_1_math.json").exists())
            self.assertFalse((wd / "reports").exists(),
                             "no report may be rendered from a rejected trade")


class TestRunRecord(unittest.TestCase):
    """Provenance: the record must say what happened, not what was attempted."""

    ROW_KEYS = {
        "order", "stage", "status", "artifact", "artifact_path",
        "artifact_sha256", "exit_code", "skip_reason", "seconds",
    }

    def read_record(self, workdir: Path) -> dict:
        path = workdir / "run_record.json"
        self.assertTrue(path.exists(), "the run left no run_record.json in its workdir")
        return json.loads(path.read_text(encoding="utf-8"))

    def test_success_record_accounts_for_every_stage(self):
        """One row per stage 0 → 12, agreeing with the console log it replaces."""
        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp)
            proc = run_runner(wd, INPUT_FIXTURE)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            record = self.read_record(wd)

            self.assertEqual(record["schema"], "uvest-run-record/1")
            self.assertEqual(record["outcome"], "complete")
            self.assertEqual(record["exit_code"], 0)
            self.assertIsNone(record["failed_stage"])
            self.assertEqual(record["input"], str(INPUT_FIXTURE.resolve()))
            self.assertEqual(record["workdir"], str(wd.resolve()))
            self.assertTrue(record["started_at"] and record["finished_at"])

            rows = record["stages"]
            self.assertEqual([row["stage"] for row in rows], list(range(13)),
                             "every stage 0 → 12 must be accounted for")
            self.assertEqual([row["order"] for row in rows], list(range(13)),
                             "rows are numbered in the order they were visited")
            for row in rows:
                self.assertEqual(set(row), self.ROW_KEYS,
                                 f"stage {row['stage']} row shape drifted")

            # The record and the log must agree — the log is what it replaces.
            ran = [int(s) for s in re.findall(r"\[RUN\] stage (\d+)", proc.stdout)]
            skipped = [int(s) for s in re.findall(r"\[SKIP\] stage (\d+)", proc.stdout)]
            self.assertEqual([r["stage"] for r in rows if r["status"] == "ran"], ran)
            self.assertEqual([r["stage"] for r in rows if r["status"] == "skipped"], skipped)
            self.assertFalse([r for r in rows if r["status"] == "not_reached"],
                             "a complete run reaches every stage")

            # Skipped stages name their reason and claim no execution. They may
            # name the artifact the contract says they would have produced (an
            # audit label) but never its path or its bytes.
            for row in rows:
                if row["status"] != "skipped":
                    continue
                self.assertTrue(row["skip_reason"], f"stage {row['stage']} was skipped silently")
                self.assertIsNone(row["exit_code"])
                self.assertIsNone(row["artifact_path"])
                self.assertIsNone(row["artifact_sha256"])
            stage11 = [r for r in rows if r["stage"] == 11][0]
            self.assertIn("conditional", stage11["skip_reason"])

            # Only a `ran` row claims bytes, and the claim is checkable: the
            # recorded SHA-256 must equal the artifact's own bytes (that is what
            # the eval harness re-computes before it grades a report).
            for row in rows:
                if row["status"] != "ran":
                    self.assertIsNone(row["artifact_path"],
                                      f"stage {row['stage']} claims a path it did not verify")
                    self.assertIsNone(row["artifact_sha256"],
                                      f"stage {row['stage']} claims bytes it did not produce")
                    continue
                self.assertIsNotNone(row["artifact"],
                                     f"stage {row['stage']} ran but names no artifact")
                self.assertIsInstance(row["seconds"], float)
                path = Path(row["artifact_path"])
                self.assertEqual(path.name, Path(row["artifact"]).name,
                                 "artifact and artifact_path disagree")
                self.assertTrue(path.exists(), f"record claims a missing artifact: {path}")
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                                 row["artifact_sha256"],
                                 f"stage {row['stage']}: recorded hash is not the artifact's")
                if path.suffix == ".json":
                    json.loads(path.read_text(encoding="utf-8"))

            self.assertTrue(record["report"].startswith("reports/report_RELIANCE_"))
            self.assertTrue((wd / record["report"]).exists())

    def test_aborted_record_names_the_failure_and_what_it_never_reached(self):
        """An aborted chain is exactly the run whose provenance gets questioned."""
        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp)
            bad = wd / "option_reco.txt"
            bad.write_text(OPTION_INPUT, encoding="utf-8")
            proc = run_runner(wd, bad)
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
            record = self.read_record(wd)

            self.assertEqual(record["outcome"], "aborted")
            self.assertEqual(record["exit_code"], 2)
            self.assertEqual(record["failed_stage"], 0)
            self.assertIsNone(record["report"])

            rows = record["stages"]
            self.assertEqual(
                [(row["stage"], row["status"]) for row in rows],
                [(0, "failed")] + [(stage, "not_reached") for stage in range(1, 13)],
                "an aborted chain must still account for the stages it never ran",
            )
            self.assertEqual(rows[0]["exit_code"], 2,
                             "the ingestion gate's own exit code must reach the record")
            self.assertIn("aborted at stage 0", rows[1]["skip_reason"])
            self.assertFalse([r for r in rows if r["artifact_path"]],
                             "a rejected trade produces no artifact to claim")


class TestRunnerArgvAndFrozenNames(unittest.TestCase):
    """Pure-function contracts of the runner, without spawning the chain."""

    @classmethod
    def setUpClass(cls):
        if not RUNNER.exists():
            raise AssertionError(f"runner missing: {RUNNER}")
        cls.module, cls.import_stdout, cls.import_stderr = load_runner_module()

    def test_import_never_writes_to_stdout(self):
        """Main-guard discipline: importing the runner performs no work."""
        self.assertEqual(self.import_stdout, "",
                         "importing the runner must not write to stdout")

    def test_artifact_table_matches_the_frozen_recorder(self):
        table = self.module.ARTIFACTS
        for stage, artifact in _recorder.FROZEN_ARTIFACTS.items():
            self.assertEqual(table.get(stage), artifact,
                             f"stage {stage} artifact drifted from _recorder")

    def test_chain_is_the_frozen_ascending_order_without_stage_11(self):
        self.assertEqual(self.module.CHAIN, [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12])
        self.assertIn(11, self.module.EXCLUDED)

    def test_stage_argv_passes_fixtures_record_payloads_and_thresholds(self):
        wd = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(wd, ignore_errors=True))
        script = SCRIPTS_DIR / "2_market_data.py"
        argv = self.module.stage_argv(
            2, script, input_path="input.txt", fixtures={2: "ohlcv.json"},
            record=None, payloads=[], workdir=wd)
        self.assertEqual(argv, [str(script), "--ohlcv-fixture", "ohlcv.json"])

        argv = self.module.stage_argv(
            9, SCRIPTS_DIR / "9_decision_engine.py", input_path="input.txt",
            fixtures={}, record="bundle", payloads=["out_1_math.json"],
            workdir=wd)
        self.assertIn("--payload", argv)
        self.assertIn("out_1_math.json", argv)
        self.assertIn("--record", argv)
        self.assertNotIn("--thresholds", argv, "thresholds only when the file exists")

        (wd / "thresholds.json").write_text("{}", encoding="utf-8")
        argv = self.module.stage_argv(
            9, SCRIPTS_DIR / "9_decision_engine.py", input_path="input.txt",
            fixtures={}, record=None, payloads=[], workdir=wd)
        self.assertIn("--thresholds", argv)

    def test_close_run_record_fills_the_stages_a_walk_never_reached(self):
        """Pure contract: uniform row keys, and no silent holes in the walk."""
        row = self.module.stage_row(0, 0, "ran", artifact="current_trade.json", exit_code=0)
        self.assertEqual(set(row), TestRunRecord.ROW_KEYS)
        record = {"failed_stage": None, "stages": [row]}
        self.module.close_run_record(record, 0)
        self.assertEqual(record["outcome"], "complete")
        self.assertEqual(record["exit_code"], 0)
        self.assertEqual([r["stage"] for r in record["stages"]], list(range(13)))
        self.assertEqual([r["order"] for r in record["stages"]], list(range(13)))
        self.assertTrue(all(r["status"] == "not_reached" for r in record["stages"][1:]))

    def test_losing_the_run_record_never_masks_the_run(self):
        """A record that cannot be written warns; it does not change the exit code."""
        wd = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(wd, ignore_errors=True))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNone(
                self.module.write_run_record(wd / "missing" / "dir", {"schema": "x"}))
        self.assertIn("[WARN] run record not written", err.getvalue())

    def test_stage_12_invocation_is_the_canonical_shape(self):
        script = SCRIPTS_DIR / "12_generate_html_report.py"
        argv = self.module.stage_argv(
            12, script, input_path="input.txt", fixtures={}, record="bundle",
            payloads=["out_1_math.json", "out_9_decision.json"],
            workdir=Path("."))
        self.assertIn("--input", argv)
        self.assertIn("current_trade.json", argv)
        self.assertIn("--out-dir", argv)
        self.assertEqual(argv.count("--payload"), 2)
        self.assertNotIn("--record", argv, "stage 12 has no recorder support")


if __name__ == "__main__":
    unittest.main()
