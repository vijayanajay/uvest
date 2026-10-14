"""Seam — scripts/run_pipeline.py, the end-to-end 0 → 12 chain (BRD §9.1, prompt.md §2).

Frozen here:

1. **One invocation, canonical order.** The runner executes the ascending
   stage chain, forwarding each stage's stdout into its BRD §9.1 artifact, and
   aborts at the first failing stage while propagating its exit code. That
   contract is proved **for every chain stage**, not only stage 0: real
   end-to-end failures for the stages that have one (0's options gate at exit 2,
   1's contradictory stoploss, 2's and 3's malformed fixtures), and an injected
   exit code per stage for the rest, so "propagates *that stage's own* code" is
   never satisfied by a runner that hardcodes 1 (see `TestChainAbortByStage`).
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

# An option written with look-alike letters: a human reads `CE`, the parser
# reads a two-letter string that is not the ASCII token. The closed instrument
# domain (ADR-001) is what stops it, and it must stop *here* — downstream
# stages have no notion of an unknown instrument and would score it as cash.
HOMOGLYPH_OPTION_INPUT = (
    "Symbol: RELIANCE\nType: \u0421\u0415\nEntry Range: 2975.00-2985.00\n"
    "Live Price: 2988.20\nStoploss: 2910.00\nTarget: 3120.00\n"
    "Recommended Date: 2026-10-09\n"
)

# Stage 0 accepts this paste (CASH, well-formed numbers) and stage 1 then
# refuses it: the stoploss sits above the entry band's midpoint, so the trade
# has negative raw risk. The cheapest *real* mid-chain failure the pipeline has,
# and the one the eval scorecard replays live.
CONTRADICTORY_SL_INPUT = (
    "Symbol: SBIN\nType: CASH\nEntry Range: 100.00-102.00\n"
    "Live Price: 101.00\nStoploss: 103.00\nTarget: 115.00\n"
    "Recommended Date: 2026-10-09\n"
)

# The same card with one line missing. Stage 12 names its artifact
# `report_{SYMBOL}_{DATE}.html` from `Recommended Date:` (TICK-010), so a paste
# without it used to run stages 0 → 9, write every artifact, and then die at
# the last stage with `ERR_INPUT: trade input is missing recommended_date`.
# Ingestion has to be where that refusal happens.
UNDATED_INPUT = (
    "Symbol: SBIN\nType: CASH\nEntry Range: 820.00-825.00\n"
    "Live Price: 822.10\nStoploss: 795.00\nTarget: 890.00\n"
)

# A fixture file that is not JSON: stages 2 and 3 refuse it (ERR_PARSE, exit 1)
# rather than waiving it, while 5 and 6 treat a malformed fixture as absent.
BAD_FIXTURE_BODY = "{this is not json"

# Injected-error harness: one fake stage script per chain stage, all of them
# recording the stage they stand for and exiting with the injected code when
# they are the target. Written into the test's temp dir, never into scripts/.
FAKE_STAGE_SCRIPT = '''\
import json
import sys
from pathlib import Path

Path({log!r}).open("a", encoding="utf-8").write("{stage}\\n")
if {stage} == {target}:
    sys.exit({code})
sys.stdout.write(json.dumps({{"stage": {stage}, "ran": True}}) + "\\n")
'''

STAGE9_CONTRACT_KEYS = {
    "final_score", "max_possible_score", "effective_score_pct",
    "decision", "verdict", "p_win", "expectancy",
    "circuit_breakers_tripped", "three_predictions", "flip_table",
    "what_would_flip_me",
}


def run_runner(workdir: Path, input_path: Path,
               *extra_args: str) -> subprocess.CompletedProcess:
    """Spawn the real runner, optionally with extra flags (e.g. bad fixtures)."""
    return subprocess.run(
        [sys.executable, str(RUNNER), "--input", str(input_path),
         "--workdir", str(workdir), *extra_args],
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
            self.assertEqual(runs, [0, 1, 2, 3, 5, 6, 7, 9, 12],
                             f"chain order wrong: {runs}\n{out}")

            # 2. Every absent stage is disclosed, and stage 11 is named as the
            #    conditional stage — silence would be a hidden gap.
            for stage in (4, 8, 10):
                self.assertIn(f"[SKIP] stage {stage}:", out,
                              f"missing disclosure for stage {stage}")
            self.assertIn("[SKIP] stage 11:", out)
            self.assertIn("conditional", out.split("[SKIP] stage 11:")[1][:120].lower())

            # 3. Artifacts of every executed stage land in the workdir.
            for name in ("current_trade.json", "out_1_math.json",
                         "out_2_market.json", "out_3_dtw.json",
                         "out_5_flow.json", "out_6_fundamental.json",
                         "out_7_regime.json", "out_9_decision.json"):
                path = wd / name
                self.assertTrue(path.exists(), f"artifact missing: {name}")
                json.loads(path.read_text(encoding="utf-8"))  # must be real JSON

            # Stage 6 ran without a financial-statement fixture, so its payload
            # takes the waiver path: absence is disclosed, never a zero score
            # (the decision engine redistributes the module weight).
            fundamental = json.loads(
                (wd / "out_6_fundamental.json").read_text(encoding="utf-8"))
            self.assertTrue(fundamental["waived"],
                            "no fixture means the waiver path, not fabricated metrics")
            self.assertIsNone(fundamental["piotoski_score"])
            self.assertIsNone(fundamental["pledge_circuit_breaker"])

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

    def test_a_dateless_paste_aborts_at_ingestion_not_at_stage_12(self):
        """The stage-12 integration gap: accepted early, fatal at the end.

        Everything stage 12 needs from the trade is available at stage 0 except
        this one field, so the chain is only safe if stage 0 refuses a paste it
        cannot render — a refusal here costs nothing, the same refusal at stage
        12 costs eleven written artifacts and a half-graded run.
        """
        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp)
            undated = wd / "undated_reco.txt"
            undated.write_text(UNDATED_INPUT, encoding="utf-8")
            proc = run_runner(wd, undated)
            combined = proc.stdout + proc.stderr
            self.assertEqual(proc.returncode, 1,
                             f"expected stage 0's own exit code: {combined}")
            self.assertIn("ERR_PARSE", combined)
            self.assertIn("recommended_date", combined)
            self.assertNotIn("[RUN] stage 1:", proc.stdout,
                             "a dateless paste must never reach the chain")
            self.assertFalse((wd / "out_1_math.json").exists())
            self.assertFalse((wd / "reports").exists(),
                             "no report may be rendered from an unrenderable trade")
            record = json.loads((wd / "run_record.json").read_text(encoding="utf-8"))
            self.assertEqual(record["outcome"], "aborted")
            self.assertEqual(record["failed_stage"], 0)
            self.assertEqual(record["exit_code"], 1)

    def test_a_failed_stage_leaves_no_empty_artifact_and_no_partial(self):
        """A refusal must not leave a file that looks like a payload.

        The runner used to open the artifact *before* spawning the stage, so
        every refusal left a 0-byte file behind — `current_trade.json` when
        stage 0 refused, `out_1_math.json` when stage 1 did. The run record
        already refused to claim those bytes; the filesystem now agrees with
        the record instead of contradicting it.
        """
        for label, body, own_artifact in (
                ("stage 0", UNDATED_INPUT, "current_trade.json"),
                ("stage 1", CONTRADICTORY_SL_INPUT, "out_1_math.json")):
            with self.subTest(failed_stage=label), tempfile.TemporaryDirectory() as tmp:
                wd = Path(tmp)
                reco = wd / "reco.txt"
                reco.write_text(body, encoding="utf-8")
                proc = run_runner(wd, reco)
                self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
                self.assertFalse(
                    (wd / own_artifact).exists(),
                    f"{label} failed but left a {own_artifact} that looks "
                    "like a payload")
                self.assertEqual(list(wd.glob(".*.partial")), [],
                                 "a partial artifact leaked from an aborted stage")
                # The stage before it still published its own artifact.
                if label == "stage 1":
                    self.assertTrue((wd / "current_trade.json").exists())
                    self.assertNotEqual(
                        (wd / "current_trade.json").stat().st_size, 0,
                        "stage 0 succeeded; its artifact must carry its bytes")


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
                if row["stage"] != 11:
                    # A chain stage with no script today is disclosed as exactly
                    # that, never as a silent no-op: it is the same set the
                    # injected abort harness stands in for. Stage 11 is the
                    # conditional stage and is skipped for its own reason.
                    self.assertIn("not implemented", row["skip_reason"])
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


class TestChainAbortByStage(unittest.TestCase):
    """A non-zero exit from **any** chain stage aborts the 0 → 12 walk.

    Two layers, because they prove different halves of one contract:

    * **Real failures** (stages 0 and 1): a spawned `run_pipeline.py` with the
      real stage scripts, so the stop is observed end to end — exit code,
      artifacts, run record. Those are the two stages the pipeline can be made
      to reject a *well-formed* input at today: 0's options gate and 1's
      contradictory stoploss. The fixture-fed stages have no such failure by
      design — a missing, malformed or too-short fixture is *absent evidence*
      (disclosed `waived: true`), never a rejection — and
      `test_absent_evidence_is_disclosed_not_an_abort` pins that boundary, so a
      future stage cannot start treating a corrupt fixture as fatal or, worse,
      as a crash.
    * **Injected codes** (every chain stage): fake stage scripts let each stage
      fail with a *distinct* code, so "propagates that stage's own code" cannot
      be satisfied by a runner that always returns 1 — and a stage with no
      natural failure (4, 7, 8, 9, 10, 12 today) is covered all the same.

    Stage 11 is outside both, by contract: it is the conditional stage, always
    disclosed as skipped in this chain, so it can never contribute a non-zero
    exit here. The harness asserts it was never invoked rather than injecting
    into it.
    """

    REAL_CASES = (
        # label, paste (None → the standard fixture), extra runner flag,
        # failing stage, its own exit code, the stage's own diagnosis.
        ("stage 0 options gate", OPTION_INPUT, None, 0, 2, "ERR_OPTIONS_PROHIBITED"),
        ("stage 0 unrecognized instrument (homoglyph CE)", HOMOGLYPH_OPTION_INPUT,
         None, 0, 2, "ERR_INSTRUMENT_UNRECOGNIZED"),
        ("stage 1 contradictory stoploss", CONTRADICTORY_SL_INPUT, None, 1, 1, "ERR_PARSE"),
    )

    @classmethod
    def setUpClass(cls):
        if not RUNNER.exists():
            raise AssertionError(f"runner missing: {RUNNER}")
        cls.module, cls.import_stdout, cls.import_stderr = load_runner_module()

    def assert_chain_stopped(self, stdout_text: str, stderr_text: str,
                             workdir: Path, stage: int, code: int,
                             marker: str | None, implemented: set[int]) -> dict:
        """The shared assertion: the walk stopped at `stage` and said so.

        Grades the four claims separately — the propagated code, the stages that
        never ran, the record's account of both, and the absence of artifacts a
        stopped chain must not leave — so a failure names the claim that broke.
        """
        combined = stdout_text + stderr_text
        if marker is not None:
            self.assertIn(marker, combined,
                          f"stage {stage}: the stage's own diagnosis is missing")
        # The runner prints its abort diagnosis on stderr (the stage's own
        # message reaches the same place), so the disclosure is looked for in
        # both streams; the walk log below is stdout-only.
        self.assertIn(f"[FAIL] stage {stage} exited {code}", combined,
                      f"stage {stage}: the runner did not disclose the abort")
        # `[RUN]` is printed for every stage the walk *attempts*, including the
        # one that fails: the attempted set must be every chain stage up to and
        # including the target — skipped stages print `[SKIP]`, never `[RUN]`.
        ran = [int(s) for s in re.findall(r"\[RUN\] stage (\d+)", stdout_text)]
        self.assertEqual(ran, [s for s in self.module.CHAIN if s <= stage],
                         f"stage {stage}: chain order/stop is wrong — {ran}")

        record = json.loads((workdir / "run_record.json").read_text(encoding="utf-8"))
        rows = {row["stage"]: row for row in record["stages"]}
        self.assertEqual(record["outcome"], "aborted")
        self.assertEqual(record["failed_stage"], stage)
        self.assertEqual(record["exit_code"], code,
                         "the stage's own code must reach the record")
        self.assertIsNone(record["report"])

        failed = rows[stage]
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["exit_code"], code)
        self.assertIsNone(failed["artifact_path"],
                          "a failed stage produced no bytes to claim")
        self.assertIsNone(failed["artifact_sha256"])

        for later in range(stage + 1, 13):
            row = rows[later]
            self.assertEqual(row["status"], "not_reached",
                             f"stage {later} ran after stage {stage} failed")
            self.assertIn(f"stage {stage}", row["skip_reason"])
            artifact = self.module.ARTIFACTS.get(later)
            if artifact:
                self.assertFalse((workdir / artifact).exists(),
                                 f"aborted chain left {artifact} behind")
        self.assertFalse((workdir / "reports").exists(),
                         "no report may be rendered by an aborted chain")

        for earlier in [s for s in self.module.CHAIN if s < stage]:
            row = rows[earlier]
            if earlier not in implemented:
                self.assertEqual(row["status"], "skipped")
                continue
            self.assertEqual(row["status"], "ran",
                             f"stage {earlier} should have run before stage {stage}")
            if row["artifact"] is None:
                continue
            path = Path(row["artifact_path"])
            self.assertTrue(path.exists(), f"stage {earlier} claims a missing artifact")
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                             row["artifact_sha256"])
        return record

    def test_a_real_stage_failure_aborts_and_propagates_its_own_code(self):
        """Real scripts, real subprocess, real artifacts — stages 0 through 3."""
        implemented = {stage for stage in self.module.CHAIN
                       if self.module.script_for(stage) is not None}
        for label, paste, flag, stage, code, marker in self.REAL_CASES:
            with self.subTest(case=label):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    workdir = root / "work"
                    input_path = root / "input.txt"
                    input_path.write_text(
                        paste if paste is not None
                        else INPUT_FIXTURE.read_text(encoding="utf-8"),
                        encoding="utf-8")
                    extra: list[str] = []
                    if flag is not None:
                        bad = root / "bad_fixture.json"
                        bad.write_text(BAD_FIXTURE_BODY, encoding="utf-8")
                        extra = [flag, str(bad)]
                    proc = run_runner(workdir, input_path, *extra)
                    record = self.assert_chain_stopped(
                        proc.stdout, proc.stderr, workdir, stage, code, marker,
                        implemented)
            # The failed stage's own row is the one that names the cause.
            self.assertTrue(record["stages"][stage]["skip_reason"])

    def test_absent_evidence_is_disclosed_not_an_abort(self):
        """The boundary of the abort contract: a data gap is not a failure.

        A corrupt fixture must not abort the chain (that is what the malformed
        fixture regression did before stages 2/3 honoured their own waiver) and
        must not crash it either — the stage emits `waived: true`, the run
        completes, and the waiver is named in the payload the decision engine
        reads.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workdir = root / "work"
            bad = root / "bad_fixture.json"
            bad.write_text(BAD_FIXTURE_BODY, encoding="utf-8")
            proc = run_runner(workdir, INPUT_FIXTURE, "--ohlcv-fixture", str(bad),
                              "--history-fixture", str(bad))
            combined = proc.stdout + proc.stderr
            self.assertEqual(proc.returncode, 0, combined)
            self.assertNotIn("Traceback", combined,
                             "a corrupt fixture must waive, not crash the stage")
            for artifact in ("out_2_market.json", "out_3_dtw.json"):
                payload = json.loads((workdir / artifact).read_text(encoding="utf-8"))
                self.assertTrue(payload["waived"], f"{artifact} did not disclose the gap")
            record = json.loads((workdir / "run_record.json").read_text(encoding="utf-8"))
            self.assertEqual(record["outcome"], "complete")
            self.assertIsNone(record["failed_stage"])
            self.assertFalse([row for row in record["stages"]
                              if row["status"] == "failed"])

    def test_a_non_zero_exit_from_any_chain_stage_aborts_with_that_code(self):
        """Injected per-stage codes: the runner must propagate each one.

        Distinct codes are the point. A runner that returned a constant 1 — or
        that mapped every failure to the first stage's code — would pass a
        single-code test and fail this one, stage by stage.
        """
        module = self.module
        injected = set(module.CHAIN)
        for target in module.CHAIN:
            code = 40 + target
            with self.subTest(stage=target, injected_code=code):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    workdir = root / "work"
                    log = root / "stages.log"
                    fakes = {}
                    for stage in module.ORDER:
                        path = root / f"{stage}_fake.py"
                        path.write_text(FAKE_STAGE_SCRIPT.format(
                            log=str(log), stage=stage, target=target, code=code),
                            encoding="utf-8")
                        fakes[stage] = path
                    original = module.script_for
                    module.script_for = lambda stage, _fakes=fakes: _fakes[stage]
                    # Both streams are captured: the runner prints the abort
                    # diagnosis on stderr, the walk log on stdout.
                    captured_out, captured_err = io.StringIO(), io.StringIO()
                    try:
                        with contextlib.redirect_stdout(captured_out), \
                                contextlib.redirect_stderr(captured_err):
                            rc = module.main(["--input", str(INPUT_FIXTURE),
                                              "--workdir", str(workdir)])
                    finally:
                        module.script_for = original

                    self.assertEqual(rc, code,
                                     f"stage {target}: returned {rc}, expected {code}")
                    invoked = [int(line) for line in
                               log.read_text(encoding="utf-8").split()]
                    self.assertEqual(invoked, [s for s in module.CHAIN if s <= target],
                                     f"stage {target}: walk order wrong — {invoked}")
                    # The conditional stage is skipped by contract, never injected
                    # into and never logged as executed.
                    self.assertNotIn(11, invoked)
                    self.assert_chain_stopped(captured_out.getvalue(),
                                              captured_err.getvalue(), workdir,
                                              target, code, None, injected)


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
        """The table is *derived* from the recorder for implemented stages.

        It used to be a hand-copied table policed by this very assertion; now
        the merge takes the name from `_recorder.FROZEN_ARTIFACTS`, so the
        copy that could drift is gone rather than guarded.
        """
        table = self.module.ARTIFACTS
        for stage, artifact in _recorder.FROZEN_ARTIFACTS.items():
            self.assertEqual(table.get(stage), artifact,
                             f"stage {stage} artifact drifted from _recorder")
        self.assertIsNone(table[12], "stage 12 writes its own HTML artifact")

    def test_every_chain_script_on_disk_has_an_artifact_row(self):
        """A stage script without a row would have its artifact dropped.

        The runner needs the table for two things: where to stream a stage's
        stdout, and what a skipped stage would have produced. A script that
        appears on disk without a row would run with nowhere to put its bytes.
        """
        table = self.module.ARTIFACTS
        for script in sorted(SCRIPTS_DIR.glob("[0-9]*_*.py")):
            stage = int(script.name.split("_", 1)[0])
            if stage in self.module.EXCLUDED:
                continue
            with self.subTest(script=script.name):
                self.assertIn(stage, table, f"{script.name} has no artifact row")
                if stage == 12:
                    continue  # writes reports/report_{SYMBOL}_{DATE}.html itself
                self.assertIn(
                    stage, _recorder.FROZEN_ARTIFACTS,
                    f"{script.name} exists but its name lives only in the "
                    "runner — freeze it in _recorder.FROZEN_ARTIFACTS (BRD §9.1)")

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

    # ------------------------------------------------- live regime fetch mode
    def test_regime_live_is_documented_and_conflicts_with_a_fixture(self):
        """One way to give stage 7 a bundle: pass it, or ask for a live fetch."""
        help_out = subprocess.run(
            [sys.executable, str(RUNNER), "--help"],
            capture_output=True, text=True, encoding="utf-8", check=False,
            cwd=str(REPO_ROOT))
        self.assertEqual(help_out.returncode, 0, help_out.stderr)
        self.assertIn("--regime-live", help_out.stdout)
        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp)
            proc = run_runner(wd, INPUT_FIXTURE,
                              "--regime-fixture", "ctx.json", "--regime-live")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("mutually exclusive", proc.stderr)

    def test_a_failed_live_fetch_degrades_to_the_waiver_path(self):
        """No canonical trade, unmapped symbol — None and a warning, never a
        crash: the fetcher's usage exits are a fetch failure down here."""
        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp)
            err, out = io.StringIO(), io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
                self.assertIsNone(self.module._fetch_live_context(wd),
                                  "no current_trade.json ⇒ waiver path")
            self.assertIn("[WARN]", err.getvalue())
            (wd / "current_trade.json").write_text(
                json.dumps({"symbol": "MYSTERYCO",
                            "recommended_date": "2026-10-14"}),
                encoding="utf-8")
            err, out = io.StringIO(), io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
                self.assertIsNone(self.module._fetch_live_context(wd),
                                  "unmapped symbol ⇒ waiver path, not exit 2 "
                                  "aborting the chain")
            self.assertIn("symbol:unmapped:MYSTERYCO", err.getvalue())
            self.assertFalse((wd / "market_context.json").exists(),
                             "no bundle is invented for an unmapped symbol")


if __name__ == "__main__":
    unittest.main()
