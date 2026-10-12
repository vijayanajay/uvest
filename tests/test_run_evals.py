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
4. Grading is tied to **run records** (evals.md §4.1, `tests/evals/provenance.py`):
   the artifact-graded gates read only bytes a record proves, a report the
   harness reproduces is proven byte-for-byte, bytes that change afterwards stop
   being evidence without the file ever being overwritten, and the scorecard
   discloses which stages a record shows ran, skipped or failed.
5. Payload evidence is folded into the same vocabulary
   (`tests/evals/proof_root.py`): the proof root's `recorded` declarations become
   `ran` rows carrying the frozen sha256, and **every payload a graded gate reads**
   — every artifact a record carries, plus its class breaker's — must be proven:
   one the proof root declares `waived` or `quarantined`, or that the dataset
   carries inline, is a hole in the graded set and the run fails on it instead of
   passing with a disclosure (evals.md §4.1).
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

sys.path.insert(0, str(REPO_ROOT / "scripts"))

import dataset_refs  # noqa: E402  (sibling eval module, spliced onto the path above)
import proof_root  # noqa: E402  (the proof root, folded into run-record rows)
import provenance  # noqa: E402  (the run-record reader the gates grade through)
import run_evals  # noqa: E402  (the runner itself, for ensure_reports)

PROOF_DIR = REPO_ROOT / "tests" / "proof"


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
        # ...and the scorecard says what proved the artifacts it graded, including
        # the stages the record discloses as skipped rather than silently absent.
        self.assertIn("Evidence provenance (run records)", scorecard)
        self.assertIn("Stage-12 reports proven:", scorecard)
        self.assertIn("Gate payloads proven:", scorecard)
        # The record that proved the graded reports is named by path, not by a
        # filename every run shares.
        self.assertIn("reports/run_record.json", scorecard)
        # ...and gate 2 states which breaker classes it graded, so a 100.0% PASS
        # cannot stand in for a class that contributed no graded record.
        self.assertIn("class coverage:", scorecard)
        for cls_name in ("clean_bullish", "inverted_rr", "pledge_hazard",
                         "earnings_hazard", "tight_stop"):
            self.assertIn(cls_name, scorecard,
                          f"gate 2's coverage must name {cls_name}")

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
        self.assertTrue(path.exists(),
                        "runner must regenerate the report it cannot grade")
        self.assertIn("[PASS] Zero Hallucination Audit", scorecard)
        self.assertIn("[PASS] HTML Visualizer Validation", scorecard)
        # The roster's size is the dataset's, never a frozen 31: it grows as
        # classes are seeded, and the count this seam reads is the harness's own.
        total = len(json.loads(DATASET_FILE.read_text(encoding="utf-8"))["trades"])
        self.assertIn(f"Stage-12 reports proven: {total}/{total}", scorecard)
        # The exit code is the *evidence* verdict, not a proxy for "the replay
        # worked": the replayed report is graded above, and the run is red only
        # if some gate input no record proves remains (evals.md §4.1). Computed
        # from the same check the runner uses, so this follows the evidence
        # rather than freezing today's answer.
        raw = json.loads(DATASET_FILE.read_text(encoding="utf-8"))
        data, _refs, _derives = dataset_refs.materialize(raw)
        _ok, _reason, uncovered, _excluded = run_evals.check_evidence_coverage(
            data["trades"], REPO_ROOT / "reports",
            run_evals.load_proof(REPO_ROOT / "reports"),
            targets=run_evals.dataset_payload_targets(raw))
        self.assertEqual(proc.returncode, 1 if uncovered else 0, scorecard)

    def test_an_inline_breaker_payload_earns_no_grade_and_fails_the_run(self):
        """End to end: the CLI hands the gates the payload provenance they need.

        The record is clean (`live_rr` > 1.0 and `is_tight_sl` false on recorded
        bytes) but carries a *typed* stage-6 payload naming a 54.2% pledge — a
        breaker trip nobody recorded. Gate 2 must abstain on those bytes rather
        than leak on them, and the run must fail on the uncovered record instead
        of grading bytes the dataset typed.
        """
        dataset = json.loads(DATASET_FILE.read_text(encoding="utf-8"))
        trade = dataset["trades"][0]
        self.assertEqual(trade["trade_class"], "clean_bullish")
        trade["script_outputs"]["out_6_fundamental.json"] = {"promoter_pledge_pct": 54.2}
        proc = self.run_with_dataset(dataset)
        out = (proc.stdout + proc.stderr).decode("utf-8", "replace")
        self.assertIn("[PASS] Hard Circuit Breaker Accuracy", out)
        self.assertIn(f"{trade['trade_id']}: gate payload", out)
        self.assertIn("out_6_fundamental.json is carried inline", out)
        self.assertIn("Evidence completeness: INCOMPLETE", out)
        self.assertEqual(proc.returncode, 1, out)

    def test_dropping_a_distinct_rule_exercise_fails_the_run(self):
        """End to end: the census is a floor, so rule coverage cannot shrink.

        `SCEN-011` is the 32.0% pledge row — the only record carrying that
        exercise. Remove it and the class still grades 4/4, still reports 100.0%,
        and the run still fails: the census drops to 4 against a frozen floor of
        5 and the failure names the class and the delta. This is the mutation
        proof that a record count is not the rule coverage.
        """
        dataset = json.loads(DATASET_FILE.read_text(encoding="utf-8"))
        baseline = run_evals.load_baseline()
        self.assertGreaterEqual(baseline.get("rule_exercises", {}).get("pledge_hazard", 0), 1,
                                "the baseline must freeze a pledge census to regress against")
        dataset["trades"] = [t for t in dataset["trades"]
                             if t["trade_id"] != "SCEN-011"]
        proc = self.run_with_dataset(dataset)
        out = (proc.stdout + proc.stderr).decode("utf-8", "replace")
        self.assertIn("[PASS] Hard Circuit Breaker Accuracy", out)
        self.assertIn("Rule coverage: REGRESSION", out)
        self.assertIn("pledge_hazard: 4 distinct rule exercise(s) < frozen 5", out)
        self.assertEqual(proc.returncode, 1, out)

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
                self.assertIsNotNone(days, f"{trade['trade_id']}: days_to_earnings missing")
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


class TestRunRecordProvenance(unittest.TestCase):
    """Grading is tied to run records (evals.md §4.1): `tests/evals/provenance.py`.

    One chain run through the real runner feeds most of these — its record is the
    evidence that has to survive the trip from `scripts/` into the eval harness.
    """

    @classmethod
    def setUpClass(cls):
        # Materialized like the runner grades: `$ref` payloads resolved, so the
        # differ reads the same bytes the scorecard does. The ref *targets* come
        # off the raw dataset, because materializing replaces them.
        cls.raw = json.loads(DATASET_FILE.read_text(encoding="utf-8"))
        cls.targets = run_evals.dataset_payload_targets(cls.raw)
        cls.dataset, _refs, _derives = dataset_refs.materialize(cls.raw)
        cls.trade = cls.dataset["trades"][0]
        cls._tmp = tempfile.TemporaryDirectory()
        cls.workdir = Path(cls._tmp.name)
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "run_pipeline.py"),
             "--input", str(REPO_ROOT / "tests" / "fixtures"
                              / "_input_reliance_20261009.txt"),
             "--workdir", str(cls.workdir)],
            capture_output=True, cwd=REPO_ROOT, check=False)
        if proc.returncode != 0:
            raise AssertionError("pipeline run failed: "
                                 + proc.stderr.decode("utf-8", "replace"))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_the_runners_own_record_is_readable_evidence(self):
        """The runner's ledger, the harness's reader: one schema, one row shape."""
        import run_pipeline  # noqa: PLC0415 — a CLI, imported only where it is tested

        record_path = self.workdir / provenance.RECORD_NAME
        record = provenance.load(record_path)
        # Two writers of one schema: if these drift, one writes records the other
        # cannot read, and the proof silently disappears.
        self.assertEqual(provenance.SCHEMA, run_pipeline.RUN_RECORD_SCHEMA)
        self.assertEqual(provenance.RECORD_NAME, run_pipeline.RUN_RECORD_NAME)
        self.assertEqual(set(run_pipeline.stage_row(0, 0, "ran")), provenance.ROW_KEYS)
        self.assertLessEqual({row["status"] for row in record["stages"]},
                             set(provenance.STATUSES))

        proof = provenance.Proof([record_path])
        report_rows = [row for row in record["stages"] if row["stage"] == 12]
        self.assertEqual(len(report_rows), 1, "stage 12 must be accounted for")
        name = report_rows[0]["artifact"]
        self.assertTrue(name.startswith("reports/report_RELIANCE_"), name)
        proven, reason = proof.proves(name, self.workdir / name, 12)
        self.assertTrue(proven, reason)
        self.assertEqual(proof.integrity_problems(), [])

    def test_the_scorecard_discloses_what_a_record_proved_and_skipped(self):
        """Stage coverage is disclosed from the record, not assumed from the log."""
        proof = run_evals.load_proof(self.workdir)
        block = "\n".join(run_evals.render_provenance(proof, 1, 1, [], []))
        self.assertIn("Evidence provenance (run records)", block)
        self.assertIn(f"{self.workdir.name}/run_record.json", block,
                      "a record outside the repo is named by its directory")
        self.assertIn("stages ran: [0, 1, 2, 3, 5, 6, 9, 12]", block)
        self.assertIn("skipped: [4, 7, 8, 10, 11]", block)
        self.assertIn("Stage-12 reports proven: 1/1", block)

        disclosed = "\n".join(run_evals.render_provenance(
            proof, 0, 1, ["GB-TEST-1: no run record shows stage 12 producing it"],
            ["run_record.json: stage 12 claims report_X.html is abc…, on disk it is def…"]))
        self.assertIn("Reports excluded (unproven — never graded): 1", disclosed)
        self.assertIn("⚠ record contradicts the disk", disclosed)

    def test_a_reproduced_report_is_proven_byte_for_byte(self):
        """The harness replays a report, and the record beside it is the proof."""
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            written, failures = run_evals.ensure_reports(
                [dict(self.trade)], reports_dir=reports)
            self.assertEqual(failures, [], "; ".join(failures))
            produced = reports / run_evals.report_path_for(self.trade).name
            self.assertEqual(written, [produced])

            proof = run_evals.load_proof(reports)
            proven, reason = proof.proves(produced.name, produced, 12)
            self.assertTrue(proven, reason)
            row = [entry for _src, doc in proof.records for entry in doc["stages"]
                   if entry["artifact"] == produced.name][0]
            self.assertEqual(row["status"], "ran")
            self.assertEqual(row["artifact_sha256"], provenance.sha256_of(produced))
            self.assertEqual(proof.integrity_problems(), [])

            gate = run_evals.gate_hallucination([dict(self.trade)], reports_dir=reports)
            self.assertFalse(gate.skipped, gate.detail)
            self.assertEqual(gate.value, 100.0, gate.detail)

            # Warm path: nothing to replay once the record proves what is there.
            self.assertFalse(run_evals.report_needs_replay(
                self.trade, reports, run_evals.load_proof(reports)))

    def test_a_record_without_proven_evidence_fails_the_run(self):
        """The graded set may never silently shrink: an uncovered record fails it.

        Disclosure alone is what this replaces — a run that grades 29 of 31
        records and reports SUITE PASSED claims more than it checked.
        """
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            # This seam is about the *report* half, so keep only the payloads the
            # proof root records.
            trade = dict(self.trade)
            trade["script_outputs"] = {
                artifact: value
                for artifact, value in trade["script_outputs"].items()
                if (str(trade["trade_id"]), artifact) in self.targets}
            self.assertTrue(trade["script_outputs"],
                            "the record's recorded payloads")
            ok, reason, uncovered, _excluded = run_evals.check_evidence_coverage(
                [trade], reports, run_evals.load_proof(reports),
                targets=self.targets)
            self.assertFalse(ok, reason)
            self.assertIn("0/1", reason)
            self.assertEqual(len(uncovered), 1)
            self.assertIn(str(trade["trade_id"]), uncovered[0])
            self.assertIn("no report on disk", uncovered[0])

            # The reproduction closes the hole, and the check follows it.
            run_evals.ensure_reports([trade], reports_dir=reports)
            ok, reason, uncovered, _excluded = run_evals.check_evidence_coverage(
                [trade], reports, run_evals.load_proof(reports),
                targets=self.targets)
            self.assertTrue(ok, reason)
            self.assertEqual(uncovered, [])
            self.assertIn("1/1", reason)

    def test_unproven_bytes_count_as_an_uncovered_record(self):
        """An artifact that is present but unproven is a hole, not a pass."""
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            run_evals.ensure_reports([dict(self.trade)], reports_dir=reports)
            produced = reports / run_evals.report_path_for(self.trade).name
            produced.write_bytes(b"<!doctype html><html><body>edited</body></html>")
            ok, reason, uncovered, _excluded = run_evals.check_evidence_coverage(
                [dict(self.trade)], reports, run_evals.load_proof(reports),
                targets=self.targets)
            self.assertFalse(ok, reason)
            self.assertIn("1 ungraded", reason)
            self.assertIn(str(self.trade["trade_id"]), uncovered[0])
            self.assertIn("different bytes", uncovered[0])

    def test_the_cli_fails_when_a_record_has_no_provable_evidence(self):
        """End to end: one unprovable record turns the whole run red (exit 1)."""
        dataset = json.loads(DATASET_FILE.read_text(encoding="utf-8"))
        dataset["trades"][0]["recommendation_date"] = "1999-01-04"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False,
                                         encoding="utf-8") as handle:
            json.dump(dataset, handle)
            benchmark = Path(handle.name)
        self.addCleanup(benchmark.unlink)
        proc = run_run_evals(benchmark)
        out = (proc.stdout + proc.stderr).decode("utf-8", "replace")
        self.assertEqual(proc.returncode, 1, out)
        self.assertIn("Evidence completeness: INCOMPLETE", out)
        self.assertIn("refuses to pass on a smaller set", out)
        self.assertIn("SUITE NOT PASSED", out)
        # The uncovered record is named, with the artifact the run could not produce.
        self.assertIn(str(dataset["trades"][0]["trade_id"]), out)
        self.assertIn("report_RELIANCE_19990104.html", out)

    def test_changed_bytes_stop_being_evidence_and_are_never_overwritten(self):
        """A leftover or edited file is ungradeable — and is never clobbered."""
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            artifacts, _ = run_evals.ensure_reports([dict(self.trade)],
                                                   reports_dir=reports)
            produced = artifacts[0]
            tampered = b"<!doctype html><html><body>hand edited</body></html>"
            produced.write_bytes(tampered)

            proof = run_evals.load_proof(reports)
            proven, reason = proof.proves(produced.name, produced, 12)
            self.assertFalse(proven)
            self.assertIn("different bytes", reason)
            self.assertTrue(proof.integrity_problems(),
                            "a record contradicted by the disk must be reported")
            self.assertTrue(run_evals.report_needs_replay(self.trade, reports, proof))

            # Reproduced, compared, disclosed — and left exactly as it was found.
            written, failures = run_evals.ensure_reports(
                [dict(self.trade)], reports_dir=reports, proof=proof)
            self.assertEqual(written, [])
            self.assertEqual(failures, [], "a mismatch is disclosed, not a run failure")
            self.assertEqual(produced.read_bytes(), tampered,
                             "the artifact on disk must never be overwritten")

            gate = run_evals.gate_hallucination([dict(self.trade)], reports_dir=reports)
            self.assertTrue(gate.skipped, "unproven bytes must not be graded")
            self.assertIn("unproven", gate.detail)
            # The mismatch is now a declared `failed` row, not a silent claim.
            self.assertFalse(run_evals.load_proof(reports).integrity_problems())

    def test_a_malformed_run_record_is_a_usage_error(self):
        """A ledger that cannot be read is reported, never graded around."""
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "run_record.json"
            bad.write_text(json.dumps({"schema": "uvest-run-record/9", "stages": []}),
                           encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(RUNNER_FILE), "--benchmark", str(DATASET_FILE),
                 "--run-record", str(bad)],
                capture_output=True, cwd=REPO_ROOT, check=False)
        out = (proc.stdout + proc.stderr).decode("utf-8", "replace")
        self.assertEqual(proc.returncode, 2, out)
        self.assertIn("Unreadable run record", out)
        self.assertIn("uvest-run-record/9", out)


def synthetic_proof_root(root: Path, *, evidence: str, registered: bool,
                         extra: dict[str, dict] | None = None) -> Path:
    """A one-scenario proof root: manifest + byte freeze (+ quarantine register).

    `extra` declares further *recorded* artifacts for the same scenario (artifact
    → payload), so a seam can exercise a record carrying more than its class
    breaker's payload.
    """
    relative = "fixtures/GB-TEST-1/recorded/out_6_fundamental.json"
    payload = root / relative
    payload.parent.mkdir(parents=True, exist_ok=True)
    payload.write_bytes(b'{"promoter_pledge_pct": 54.2}')
    declared = {"stage": 6, "artifact": "out_6_fundamental.json",
                "path": relative if evidence == "recorded" else None,
                "evidence": evidence, "reason": "stage 6 unimplemented"}
    frozen = [f"{provenance.sha256_of(payload)}  {relative}"]
    artifacts = [declared]
    for artifact, body in (extra or {}).items():
        extra_rel = f"fixtures/GB-TEST-1/recorded/{artifact}"
        extra_path = root / extra_rel
        extra_path.parent.mkdir(parents=True, exist_ok=True)
        extra_path.write_text(json.dumps(body), encoding="utf-8")
        frozen.append(f"{provenance.sha256_of(extra_path)}  {extra_rel}")
        artifacts.append({"stage": 2, "artifact": artifact, "path": extra_rel,
                          "evidence": "recorded"})
    (root / "manifest.sha256").write_text("\n".join(frozen) + "\n", encoding="utf-8")
    (root / "scenario_manifest.json").write_text(json.dumps({
        "schema": "uvest-scenario-manifest/1",
        "scenarios": [{"scenario_id": "GB-TEST-1",
                       "recorded_artifacts": artifacts}]}),
        encoding="utf-8")
    (root / "quarantine_register.json").write_text(json.dumps({
        "schema": "uvest-quarantine-register/1",
        "rows": ([{"scenario_id": "GB-TEST-1", "stage": 6,
                   "artifact": "out_6_fundamental.json"}] if registered else [])}),
        encoding="utf-8")
    return payload


class TestProofRootPayloadEvidence(unittest.TestCase):
    """Payload provenance in the record vocabulary: `tests/evals/proof_root.py`.

    Payloads are gate inputs — the differ backs report numbers from every payload
    a record carries, and gate 2 grades a record's class breaker from its artifact
    — so every one of them has to be proven. Folding the proof root's declarations
    into run-record rows is what lets evidence coverage ask the same question about
    a payload that it asks about a stage-12 report, and fail the run when the
    answer is no instead of passing on a smaller graded set (evals.md §4.1).
    """

    @classmethod
    def setUpClass(cls):
        cls.raw = json.loads(DATASET_FILE.read_text(encoding="utf-8"))
        cls.dataset, _refs, _derives = dataset_refs.materialize(cls.raw)
        cls.trades = cls.dataset["trades"]
        cls.by_id = {str(t["trade_id"]): t for t in cls.trades}
        cls.targets = run_evals.dataset_payload_targets(cls.raw)
        cls.proof = run_evals.load_proof(REPO_ROOT / "reports")
        cls.rows = proof_root.rows()
        cls.by_label = {row["artifact"]: row for row in cls.rows}

    # ------------------------------------------------------------ the fold
    def test_the_fold_is_a_valid_run_record(self):
        """Built, not read — and validated by the rules a ledger is read under."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / provenance.RECORD_NAME
            path.write_text(json.dumps(proof_root.record()), encoding="utf-8")
            loaded = provenance.load(path)
        labels = [row["artifact"] for row in loaded["stages"]]
        self.assertEqual(len(labels), len(set(labels)),
                         "a payload label must name exactly one artifact")

    def test_the_fold_covers_every_artifact_the_manifest_declares(self):
        manifest = json.loads((PROOF_DIR / "scenario_manifest.json")
                              .read_text(encoding="utf-8"))
        declared = [proof_root.label(row["scenario_id"], artifact["artifact"])
                    for row in manifest["scenarios"]
                    for artifact in row["recorded_artifacts"]]
        self.assertTrue(declared)
        self.assertEqual(sorted(declared), sorted(self.by_label))

    def test_recorded_payloads_carry_the_frozen_hash(self):
        ran = [row for row in self.rows if row["status"] == "ran"]
        self.assertTrue(ran, "the proof root declares recorded payloads")
        for row in ran:
            self.assertEqual(provenance.sha256_of(Path(row["artifact_path"])),
                             row["artifact_sha256"], row["artifact"])

    def test_only_a_ran_row_may_claim_bytes(self):
        for row in self.rows:
            if row["status"] != "ran":
                self.assertIsNone(row["artifact_path"], row["artifact"])
                self.assertIsNone(row["artifact_sha256"], row["artifact"])

    def test_the_retired_row_folds_to_ran_and_the_rest_to_disclosed_skips(self):
        """One declaration was retired; the remaining debt is the waivers.

        GB-2023-0003's stage-6 payload was the register's single quarantined row
        (now retired into recorded bytes), so the folded record must show it as
        `ran` with a matching hash, and no row may carry a quarantine skip reason.
        The stage-6 payloads whose engine has no input remain disclosed skips.
        """
        retired = self.by_label["GB-2023-0003:out_6_fundamental.json"]
        self.assertEqual(retired["status"], "ran")
        self.assertIsNotNone(retired["artifact_sha256"])
        self.assertFalse([row for row in self.rows
                          if proof_root.REGISTER_FILE in (row["skip_reason"] or "")],
                         "nothing is registered quarantined any more")
        waived = self.by_label["GB-2022-0001:out_6_fundamental.json"]
        self.assertEqual(waived["status"], "skipped")
        self.assertTrue(waived["skip_reason"].startswith("waived:"))

    # -------------------------------------------------- synthetic proof roots
    def _proof_for(self, root: Path) -> provenance.Proof:
        proof = provenance.Proof()
        proof.add(root / "scenario_manifest.json", record=proof_root.record(root))
        return proof

    def test_a_recorded_payload_is_proven_until_its_bytes_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proof"
            payload = synthetic_proof_root(root, evidence="recorded", registered=False)
            proof = self._proof_for(root)
            ok, reason = proof.proves_payload("GB-TEST-1:out_6_fundamental.json", payload)
            self.assertTrue(ok, reason)
            self.assertEqual(proof.integrity_problems(), [])

            payload.write_bytes(b'{"promoter_pledge_pct": 0.0}')
            ok, reason = proof.proves_payload("GB-TEST-1:out_6_fundamental.json", payload)
            self.assertFalse(ok, reason)
            self.assertIn("different bytes", reason)
            self.assertTrue(proof.integrity_problems(),
                            "a payload edited after the freeze must be reported")

    def test_an_inline_payload_is_never_proven(self):
        """Bytes that live only in the dataset are not a recorded artifact."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proof"
            synthetic_proof_root(root, evidence="recorded", registered=False)
            proof = self._proof_for(root)
            ok, reason = proof.proves_payload("GB-TEST-1:out_6_fundamental.json", None)
        self.assertFalse(ok)
        self.assertIn("carried inline", reason)

    def test_a_waived_or_quarantined_payload_is_registered_debt_not_a_failure(self):
        """Registered debt is disclosed and excluded; it never fails the run.

        E4/E5: a `waived` declaration (the engine does not exist yet) and a
        `quarantined` registration (legacy bytes kept for migration continuity)
        are both *declared* — the gates abstain, the ledger names them, and the
        record still counts as covered. Treating them as fatal would make a green
        run impossible until every engine exists, which is not what the dataset's
        own mid-curation disclosure claims.
        """
        trade = {"trade_id": "GB-TEST-1", "trade_class": "pledge_hazard"}
        for evidence, registered in (("waived", False), ("quarantined", True)):
            with self.subTest(evidence=evidence), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "proof"
                payload = synthetic_proof_root(root, evidence=evidence,
                                               registered=registered)
                proof = self._proof_for(root)
                covered, uncovered, excluded = run_evals.check_payload_coverage(
                    [trade], proof,
                    {("GB-TEST-1", "out_6_fundamental.json"): payload})
                self.assertEqual(covered, 1, excluded)
                self.assertEqual(uncovered, [])
                self.assertEqual(len(excluded), 1, excluded)
                self.assertIn("GB-TEST-1: gate payload out_6_fundamental.json",
                              excluded[0])
                self.assertIn(evidence if evidence == "waived" else "quarantined",
                              excluded[0])
                # The ordered run keeps the same distinction: the exclusion is
                # disclosed, never counted as a hole in the graded set.
                _covered, _uncovered, _excluded = run_evals.check_payload_coverage(
                    [trade], proof, {})
                self.assertEqual(_uncovered, [])
                self.assertEqual(len(_excluded), 1, _excluded)

    def test_an_inline_payload_fails_payload_coverage(self):
        """Bytes the dataset typed are not evidence, whatever the proof root says.

        E1: a payload a graded gate reads must be a recording. A record holding an
        inline copy has no file behind it, so *no* declaration can prove those
        bytes — not even one for the same label, which is why declaring a payload
        in the proof root while the dataset keeps its own copy stays uncovered.
        Both the class's artifact and one the record merely carries count: the
        differ backs report numbers from the whole record.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proof"
            synthetic_proof_root(root, evidence="recorded", registered=False)
            trade = {"trade_id": "GB-TEST-1", "trade_class": "pledge_hazard",
                     "script_outputs": {
                         "out_6_fundamental.json": {"promoter_pledge_pct": 54.2},
                         "out_5_flow.json": {"delivery_pct": 61.3},
                         "current_trade.json": {"type": "FUT"}}}
            covered, uncovered, excluded = run_evals.check_payload_coverage(
                [trade], self._proof_for(root), {})
        self.assertEqual(covered, 0)
        self.assertEqual(excluded, [])
        self.assertEqual(len(uncovered), 1, uncovered)
        self.assertIn("gate payload", uncovered[0])
        for artifact in ("out_6_fundamental.json", "out_5_flow.json",
                         "current_trade.json"):
            self.assertIn(f"{artifact} is carried inline", uncovered[0])

    def test_gate_2_abstains_on_a_payload_with_no_recording_behind_it(self):
        """No recording, no grade — for a typed payload exactly as for a quarantined
        one: `None` is *ungradeable*, never *clean*."""
        trade = {"trade_id": "GB-TEST-1", "trade_class": "pledge_hazard",
                 "script_outputs": {"out_6_fundamental.json":
                                    {"promoter_pledge_pct": 54.2}}}
        unbacked = run_evals.unbacked_payloads([trade], {})
        self.assertEqual(unbacked, {("GB-TEST-1", "out_6_fundamental.json")})
        gate = run_evals.gate_hard_breakers([trade], unbacked)
        self.assertTrue(gate.skipped, gate.detail)
        self.assertIn("carried inline", gate.detail)
        # Not a blanket skip: a file behind the payload is not unbacked, so the
        # same record grades its breaker normally.
        recorded = {("GB-TEST-1", "out_6_fundamental.json"): Path("recorded.json")}
        self.assertEqual(run_evals.unbacked_payloads([trade], recorded), frozenset())
        self.assertEqual(run_evals.gate_hard_breakers([trade], set()).value, 100.0)

        # A trip that exists only in typed bytes: the clean record would leak on it
        # (0.0% = a failed gate) if the payload's provenance were unknown. Its two
        # recorded payloads are not unbacked, so the abstention is the typed one's.
        clean = {"trade_id": "GB-TEST-2", "trade_class": "clean_bullish",
                 "script_outputs": {"out_1_math.json": {"live_rr": 1.8},
                                    "out_2_market.json": {"is_tight_sl": False},
                                    "out_6_fundamental.json": {"promoter_pledge_pct": 54.2}}}
        recorded_clean = {("GB-TEST-2", "out_1_math.json"): Path("out_1_math.json"),
                          ("GB-TEST-2", "out_2_market.json"): Path("out_2_market.json")}
        unbacked_clean = run_evals.unbacked_payloads([clean], recorded_clean)
        self.assertEqual(unbacked_clean, {("GB-TEST-2", "out_6_fundamental.json")})
        self.assertEqual(run_evals.gate_hard_breakers([clean], set()).value, 0.0)
        self.assertEqual(run_evals.gate_hard_breakers([clean], unbacked_clean).value, 100.0)

    def test_gate_2_discloses_the_classes_it_graded(self):
        """The disclosure is the gate's own roster, per class, with counts.

        Gate 2's value is an average over the records it could grade, so a class
        with no graded record cannot move it. The coverage line is what makes that
        visible: every breaker class is named, and the counts asserted here are
        recomputed from the same projection the gate uses, so the line cannot
        drift into a decorative summary.
        """
        unbacked = run_evals.unbacked_payloads(self.trades, self.targets)
        gate = run_evals.gate_hard_breakers(
            self.trades, unbacked, self.raw["meta"]["class_counts_expected"])
        coverage = gate.coverage
        self.assertTrue(coverage.startswith("class coverage: "), coverage)
        for cls in run_evals.BREAKER_BY_CLASS:
            graded = sum(1 for t in self.trades if t["trade_class"] == cls
                         and run_evals.class_breaker_observed(t, unbacked=unbacked)
                         is not None)
            total = sum(1 for t in self.trades if t["trade_class"] == cls)
            self.assertIn(f"{cls} {graded}/{total} graded", coverage)
        clean = sum(1 for t in self.trades if t["trade_class"] == "clean_bullish")
        self.assertIn(f"clean_bullish {clean}/{clean} graded", coverage)
        # A class nobody grades is named too, so it is not invisible either.
        self.assertIn("no registered breaker", coverage)
        self.assertEqual(gate.value, 100.0, gate.detail)

    def test_a_class_with_no_graded_record_is_named_not_hidden(self):
        """`0/N graded, N abstained` — what a 100.0% PASS would otherwise hide.

        Two pledge records whose payload no recording backs and one fully
        recorded clean record: the gate still averages 100.0 over what it graded,
        and the coverage line states that the pledge class was not part of it —
        including the quota it is short of. The line is printed on the scorecard
        for a PASS, not only for a failure.
        """
        abstained = [
            {"trade_id": f"GB-TEST-{i}", "trade_class": "pledge_hazard",
             "script_outputs": {"out_6_fundamental.json":
                                {"promoter_pledge_pct": 54.2}}}
            for i in (1, 2)]
        clean = {"trade_id": "GB-TEST-3", "trade_class": "clean_bullish",
                 "script_outputs": {"out_1_math.json": {"raw_rr": 1.8, "live_rr": 1.8},
                                    "out_2_market.json": {"is_tight_sl": False}}}
        trades = [*abstained, clean]
        # The clean record is *recorded*, as the docstring says: its two payloads
        # have a target behind them. Its exercises are then the two R:R arms it
        # carries a measurement for (`:clear`, at 1.8) — the tight arm carries no
        # multiple here, so it counts for nothing, which is the point of counting
        # measurements rather than records.
        recorded = {("GB-TEST-3", "out_1_math.json"): Path("out_1_math.json"),
                    ("GB-TEST-3", "out_2_market.json"): Path("out_2_market.json")}
        unbacked = run_evals.unbacked_payloads(trades, recorded)
        self.assertEqual(unbacked, frozenset(
            {("GB-TEST-1", "out_6_fundamental.json"),
             ("GB-TEST-2", "out_6_fundamental.json")}))
        gate = run_evals.gate_hard_breakers(trades, unbacked,
                                           {"clean_bullish": 15, "pledge_hazard": 5})
        self.assertFalse(gate.skipped, gate.detail)
        self.assertEqual(gate.value, 100.0, gate.detail)
        self.assertIn("pledge_hazard 0/2 graded", gate.coverage)
        self.assertIn("(quota 5), 2 abstained", gate.coverage)
        self.assertIn("clean_bullish 1/1 graded", gate.coverage)
        self.assertIn("2 distinct rule exercise(s) (quota 15)", gate.coverage)
        rendered = run_evals.render_scorecard([gate], True)
        self.assertIn("[PASS] Hard Circuit Breaker Accuracy", rendered)
        self.assertIn("pledge_hazard 0/2 graded", rendered,
                      "a PASS must carry its coverage line onto the scorecard")

    # ------------------------------------------- rule exercises, not records
    def test_gate_2_counts_rule_exercises_not_records(self):
        """The census is per class, and it is the runner's own walk.

        Counting records would say 40 rules were probed; the census reads the
        measured coverage instead — the three geometry classes are spread across
        their shape families by the ladder campaign, the two hazard classes are
        catalog-fixed, and the line says which rows added nothing new.
        """
        unbacked = run_evals.unbacked_payloads(self.trades, self.targets)
        gate = run_evals.gate_hard_breakers(
            self.trades, unbacked, self.raw["meta"]["class_counts_expected"])
        self.assertEqual(gate.census, run_evals.dataset_rule_census())
        # The census is not a record count, in either direction: a record can stand
        # for several arms, so the invariant is the *collapse* of repeated
        # measurements (pinned by the sibling seam) and the floor, not an ordering
        # against `len(trades)`.
        self.assertNotEqual(sum(gate.census.values()), len(self.trades))
        baseline = run_evals.load_baseline()
        ok, reason, shortfalls = run_evals.check_rule_coverage(gate.census, baseline)
        self.assertTrue(ok, f"{reason} {shortfalls}")
        for cls, keys in run_evals.graded_trade_classes(self.trades, unbacked).items():
            self.assertEqual(gate.census.get(cls), len(keys), cls)
            self.assertIn(f"{cls} ", gate.coverage)
            self.assertIn(f", {len(keys)} distinct rule exercise(s)", gate.coverage)
        self.assertIn("pledge_hazard 5/5 graded, 5 distinct rule exercise(s)",
                      gate.coverage)
        # The curated shape rows repeat an exercise, and the line says by how much.
        self.assertIn("repeat an exercise", gate.coverage)

    def test_a_repeated_measurement_is_one_exercise_not_two_records(self):
        """Same arm, same measurement at the rule's granule ⇒ the same exercise."""
        unbacked: frozenset = frozenset()
        first = {"trade_id": "GB-TEST-1", "trade_class": "pledge_hazard",
                 "script_outputs": {"out_6_fundamental.json":
                                    {"promoter_pledge_pct": 34.7}}}
        rounded = {"trade_id": "GB-TEST-2", "trade_class": "pledge_hazard",
                   "script_outputs": {"out_6_fundamental.json":
                                      {"promoter_pledge_pct": 34.7000000001}}}
        other = {"trade_id": "GB-TEST-3", "trade_class": "pledge_hazard",
                 "script_outputs": {"out_6_fundamental.json":
                                    {"promoter_pledge_pct": 62.5}}}
        self.assertEqual(run_evals.rule_exercises("pledge_hazard", first, unbacked),
                         run_evals.rule_exercises("pledge_hazard", rounded, unbacked),
                         "the granule is what makes two measurements one exercise")
        census = run_evals.graded_trade_classes([first, rounded, other], unbacked)
        self.assertEqual(len(census["pledge_hazard"]), 2)
        gate = run_evals.gate_hard_breakers([first, rounded, other], unbacked,
                                           {"pledge_hazard": 5})
        self.assertEqual(gate.census["pledge_hazard"], 2)
        self.assertIn("pledge_hazard 3/3 graded, 2 distinct rule exercise(s)",
                      gate.coverage)
        self.assertIn("(1 of 3 repeat an exercise)", gate.coverage)

    def test_a_breaker_arm_is_a_comparison_and_the_class_is_their_or(self):
        """`breaker_arms` is the single source of the arm semantics.

        The R:R breaker is two arms (recommended-band midpoint, executable
        price); the class verdict ORs them, and only the arm that actually
        tripped counts as an exercise.
        """
        trade = {"trade_id": "GB-TEST-1", "trade_class": "inverted_rr",
                 "script_outputs": {"out_1_math.json":
                                    {"raw_rr": 0.9, "live_rr": 1.5}}}
        arms = run_evals.breaker_arms(trade, "inverted_risk_reward")
        self.assertEqual(arms, {"raw_rr_below_1": True, "live_rr_below_1": False})
        self.assertIs(run_evals.class_breaker_observed(trade), True)
        self.assertEqual(run_evals.rule_exercises("inverted_rr", trade),
                         frozenset({("raw_rr_below_1", 0.9)}),
                         "an arm that did not trip is not an exercise")
        # A record without live_rr falls back to raw_rr, exactly as before the
        # arms were split out.
        legacy = {"trade_id": "GB-TEST-2", "trade_class": "inverted_rr",
                  "script_outputs": {"out_1_math.json": {"raw_rr": 0.8}}}
        self.assertEqual(run_evals.breaker_arms(legacy, "inverted_risk_reward"),
                         {"raw_rr_below_1": True, "live_rr_below_1": True})

    def test_rule_coverage_cannot_shrink_below_the_frozen_floor(self):
        """The floor is the baseline's, and losing a distinct rule is a failure.

        Growth is free; a class that drops below its frozen census fails with the
        class and the delta named, and a class the floor does not know yet (added
        since the last re-freeze) is not a regression.
        """
        baseline = {"rule_exercises": {"pledge_hazard": 5, "earnings_hazard": 2}}
        ok, reason, shortfalls = run_evals.check_rule_coverage(
            {"pledge_hazard": 5, "earnings_hazard": 3}, baseline)
        self.assertTrue(ok, reason)
        self.assertEqual(shortfalls, [])
        self.assertIn("8 distinct rule exercise(s)", reason)
        ok, reason, shortfalls = run_evals.check_rule_coverage(
            {"pledge_hazard": 4, "earnings_hazard": 2, "tight_stop": 1}, baseline)
        self.assertFalse(ok)
        self.assertEqual(len(shortfalls), 1, shortfalls)
        self.assertIn("pledge_hazard: 4 distinct rule exercise(s) < frozen 5",
                      shortfalls[0])
        # A class the floor has never seen is an addition, not a regression.
        ok, _reason, _shortfalls = run_evals.check_rule_coverage(
            {"pledge_hazard": 5, "earnings_hazard": 2, "tight_stop": 1}, baseline)
        self.assertTrue(ok)
        # No floor yet: the check discloses rather than inventing one.
        ok, reason, _shortfalls = run_evals.check_rule_coverage(
            {"pledge_hazard": 5}, {})
        self.assertTrue(ok)
        self.assertIn("no frozen rule-coverage census", reason)

    def test_the_live_census_meets_the_committed_floor(self):
        """End to end: today's dataset is at or above the frozen census."""
        baseline = run_evals.load_baseline()
        self.assertIn("rule_exercises", baseline,
                      "the baseline must freeze the rule-coverage census")
        census = run_evals.dataset_rule_census()
        ok, reason, shortfalls = run_evals.check_rule_coverage(census, baseline)
        self.assertTrue(ok, f"{reason} {shortfalls}")

    def test_coverage_is_a_read_of_the_gate_not_a_second_opinion(self):
        """A fully graded benchmark has no abstention clause to show."""
        unbacked = run_evals.unbacked_payloads(self.trades, self.targets)
        gate = run_evals.gate_hard_breakers(
            self.trades, unbacked, self.raw["meta"]["class_counts_expected"])
        self.assertNotIn("abstained", gate.coverage, gate.coverage)
        self.assertEqual(gate.detail.split(";")[0],
                         f"{len(self.trades)} graded, 0 violation(s)")

    def test_a_recorded_payload_covers_the_record(self):
        """The negative case: coverage is a proof, not a permanent complaint.

        Every payload the record carries resolves to a recorded bundle — the
        class's breaker artifact, and one the record only carries incidentally.
        """
        trade = {"trade_id": "GB-TEST-1", "trade_class": "pledge_hazard",
                 "script_outputs": {"out_6_fundamental.json":
                                    {"promoter_pledge_pct": 54.2},
                                    "out_2_market.json": {"is_tight_sl": False}}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proof"
            payload = synthetic_proof_root(
                root, evidence="recorded", registered=False,
                extra={"out_2_market.json": {"is_tight_sl": False}})
            recorded = root / "fixtures" / "GB-TEST-1" / "recorded" / "out_2_market.json"
            covered, uncovered, excluded = run_evals.check_payload_coverage(
                [trade], self._proof_for(root),
                {("GB-TEST-1", "out_6_fundamental.json"): payload,
                 ("GB-TEST-1", "out_2_market.json"): recorded})
        self.assertEqual((covered, uncovered, excluded), (1, [], []))

    def test_the_fold_refuses_a_self_contradicting_proof_root(self):
        """A manifest that disagrees with the register is an error, not a thinnner
        record: folding it would hide exactly the contradiction that matters."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proof"
            synthetic_proof_root(root, evidence="recorded", registered=True)
            with self.assertRaises(proof_root.ProofRootError) as ctx:
                proof_root.rows(root)
        self.assertIn("contradicts itself", str(ctx.exception))

    # ------------------------------------------------------ the real proof root
    def test_declared_debt_is_disclosed_and_no_record_types_bytes_inline(self):
        """The roster splits into covered records and *declared* exclusions.

        A gap is fatal only when nobody declared it (E1: bytes the dataset typed
        inline, an artifact no declaration covers). Signature debt — the proof
        root's `waived`/`quarantined` declarations — is disclosed and excluded
        (E4/E5) exactly as the dataset's own mid-curation note says, and the
        exclusions must equal the live register exactly (which is now empty, so
        the pledge class grades every one of its records). The inline half of that
        split must stay empty: a curator reintroducing a typed payload fails this
        seam before it reaches a commit.
        """
        covered, uncovered, excluded = run_evals.check_payload_coverage(
            self.trades, self.proof, self.targets)
        self.assertEqual(covered + len(uncovered), len(self.trades))
        referenced = {(trade_id, artifact)
                      for trade_id, artifact, _ref in dataset_refs.refs(self.raw)}
        inline = {str(trade["trade_id"]) for trade in self.raw["trades"]
                  for artifact in (trade.get("script_outputs") or {})
                  if (str(trade["trade_id"]), artifact) not in referenced}
        registered = {scenario for scenario, _artifact
                      in dataset_refs.quarantined_artifacts() if scenario in self.by_id}
        self.assertEqual(inline, set(),
                         "the golden dataset must carry no payload bytes inline (E1)")
        self.assertEqual(uncovered, [],
                         "no record may hold an undeclared evidence gap")
        offenders = {entry.split(":", 1)[0] for entry in excluded}
        self.assertEqual(offenders, registered,
                         "the disclosed exclusions are exactly the registered debt")

    def test_the_scorecard_discloses_declared_debt_and_stays_green(self):
        """End to end: red only for an undeclared gap, and never silently so."""
        covered, uncovered, excluded = run_evals.check_payload_coverage(
            self.trades, self.proof, self.targets)
        proc = run_run_evals(DATASET_FILE)
        out = (proc.stdout + proc.stderr).decode("utf-8", "replace")
        self.assertIn(f"Gate payloads proven: {covered}/{len(self.trades)}", out)
        # The scorecard prints the first few of each kind and counts the rest, so
        # the seam reads it the way a human does (the calls above return the rest).
        for entry in excluded[:3]:
            self.assertIn(entry.split(" (", 1)[0], out)
        for entry in uncovered[:3]:
            self.assertIn(entry.split(" (", 1)[0], out)
        # Only an *undeclared* gap turns the run red; registered debt is disclosed
        # on the ledger and leaves the verdict alone.
        self.assertEqual(proc.returncode, 1 if uncovered else 0, out)
        if excluded:
            self.assertIn("registered debt — no gate grades them as evidence", out)
            self.assertIn(f"{len(excluded)} registered exclusion(s)", out)
        if uncovered:
            self.assertIn("Evidence completeness: INCOMPLETE", out)
            self.assertIn("SUITE NOT PASSED", out)
        else:
            self.assertIn("Evidence completeness: OK", out)
            self.assertIn("SUITE PASSED", out)


if __name__ == "__main__":
    unittest.main()
