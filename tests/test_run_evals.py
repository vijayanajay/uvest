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
6. Gate 1 grades a **live** replay of the seeded option pastes through stage 0's
   real CLI (`python scripts/0_ingest_reco.py --input <paste>`), not a stored exit
   code: a stored verdict could only prove the code that ran when it was recorded,
   and the fatal-token vocabulary is asserted equal to the parser's own
   (evals.md §2.1, §3 #3).
7. Gate 1b grades the orchestrator's half of the same invariant: one named
   probe **per refusal family** is replayed through the **full 0 → 12 chain**
   (`python scripts/run_pipeline.py --input <paste> --workdir <tmp>`), so a
   runner that swallowed stage 0's exit code and walked on cannot ride on
   gates 1/1c's green rejection rates. The stop is graded whole per replay —
   propagated exit code, the family's *own* code on stderr (an option stop is
   no evidence for a homoglyph one), no downstream artifact, no report, and a
   run record naming stage 0 `failed` with every later stage `not_reached` —
   and both replays are named on the scorecard.
8. Gate 1c grades the same *kind* of case for the other half of ADR-001: the
   closed instrument domain. Its `refusal_probes` roster holds a homoglyph option
   token (`Type: СЕ` is not the ASCII token `CE`, so the option gate cannot name
   it) and an invented or un-aliased label, each replayed live through stage 0
   against its own code (`ERR_INSTRUMENT_UNRECOGNIZED`). The families are counted
   beside gate 1, never inside it: a paste the gate never classified as an option
   must not inflate the option rejection rate, and the roster refuses a probe
   that could only ever reproduce that other verdict.
9. The probe rosters are sized like the rule census is: `baseline_scorecard.json`
   freezes their counts (`probe_rosters`, banked by
   `scaffold_catalog.re_freeze_baseline`), the runner recomputes them each run
   (`check_probe_roster_floor`), and a roster that shrinks — or vanishes entirely
   — fails the run by roster and by delta until a deliberate re-freeze accepts
   the new size. Growing past the floor is free.
"""

from __future__ import annotations

import json
import re
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
        # Stage 4 emits calibrated direction probabilities and a cone envelope,
        # and the promoted records declare the recorded artifact behind them, so
        # both engine gates grade real bytes — a [SKIP] here would mean those
        # bytes went missing, not that the engine is absent.
        self.assertNotIn("[SKIP] Directional Brier Score", scorecard)
        self.assertNotIn("[SKIP] Price Cone Range Coverage", scorecard)
        self.assertIn("Directional Brier Score", scorecard)
        self.assertIn("calibrated predictions", scorecard)
        self.assertIn("cone-labeled trades", scorecard)
        # Missing stage-12 artifacts are replayed from the proof root before
        # grading (run_evals.ensure_reports), so both artifact-graded gates
        # grade real bytes even on a fresh clone — never a silent skip. The
        # artifact, not a curator's claim, is what both gates read (ADR-007).
        self.assertIn("[PASS] Zero Hallucination Audit", scorecard)
        self.assertIn("[PASS] HTML Visualizer Validation", scorecard)
        self.assertIn("[PASS] Hard Circuit Breaker Accuracy", scorecard)
        # Both halves of ADR-001 are graded: the parser's refusal and the
        # orchestrator's stop (gate 1b — a swallowed exit code must be visible).
        self.assertIn("[PASS] Option Rejection Guardrail", scorecard)
        self.assertIn("[PASS] Ingestion Chain Abort", scorecard)
        self.assertIn("0 → 12 chain", scorecard)
        # Gate 1b replays both refusal families and prints the code each replay
        # was graded against — a homoglyph stop is not an option stop.
        self.assertIn("2/2 chain replays", scorecard)
        self.assertIn("with ERR_INSTRUMENT_UNRECOGNIZED", scorecard)
        # ...and both rosters are sized against the frozen baseline floor, so a
        # quietly deleted probe cannot keep the same green percentage.
        self.assertIn("Probe roster floor: OK", scorecard)
        self.assertIn("10 seeded stage-0 probe(s)", scorecard)
        # Stage 0's *other* refusal family is graded live too, on its own line:
        # the closed instrument domain (gate 1c) counts homoglyph option tokens
        # and unrecognized labels beside the option rate, never inside it — a
        # paste the option gate cannot name must not touch that percentage.
        self.assertIn("[PASS] Unrecognized Instrument Guardrail", scorecard)
        self.assertIn("ERR_INSTRUMENT_UNRECOGNIZED", scorecard)
        # ...and it discloses both families by name, so 5/5 refused cannot read
        # the same when the homoglyph half silently stops being seeded.
        self.assertIn("homoglyph option token(s)", scorecard)
        self.assertIn("unrecognized instrument label(s)", scorecard)
        # Both rosters print their integrity beside the dataset's, so a shrunken
        # refusal roster is a named mismatch rather than a smaller graded set.
        self.assertIn("Option probes: OK", scorecard)
        self.assertIn("Refusal probes: OK", scorecard)
        self.assertLess(scorecard.count("[SKIP]"), 7,
                        "runner hardcoded a full-skip beat instead of grading")
        # Gate 6's engine (stage 8) does not exist yet, so it still skips — and a
        # skip names what is missing rather than reading as a pass.
        self.assertIn("[SKIP] Optimal Limit Slippage Reduction", scorecard)
        # The verdict follows the gates rather than being asserted beside them:
        # a graded gate that misses its threshold is published, and the run is
        # red for it — never a green percentage that hides a failed dimension.
        self.assertIn("FINAL EVALUATION VERDICT:", scorecard)
        self.assertIn("SUITE PASSED" if scorecard.count("[FAIL]") == 0
                      else "SUITE NOT PASSED", scorecard)
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
        # The exit code is the run's own verdict: uncovered evidence *or* a graded
        # gate under its threshold. Read off the scorecard the runner printed, so
        # this follows the evidence instead of freezing today's answer.
        self.assertEqual(proc.returncode,
                         1 if "SUITE NOT PASSED" in scorecard else 0, scorecard)
        if uncovered:
            self.assertIn("Evidence completeness: INCOMPLETE", scorecard)

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


import tempfile  # noqa: E402  (kept next to its consumers)


class TestOptionRejectionGate(unittest.TestCase):
    """Gate 1 grades a live stage-0 replay of the seeded option pastes.

    The roster deliberately sits outside `trades`: an option never becomes a
    trade, so the invariant cannot be graded over the 52-record quota (evals.md
    §2.1). What is frozen here is that the roster exists, that every paste is
    actually refused by *this commit's* parser, and that losing the roster or
    letting a paste through fails the run instead of quietly shrinking the gate.
    """

    @classmethod
    def setUpClass(cls):
        cls.dataset = json.loads(DATASET_FILE.read_text(encoding="utf-8"))
        cls.probes = cls.dataset.get("option_probes") or []
        if not cls.probes:
            raise AssertionError(
                "the option-probe roster is empty — gate 1 would [SKIP] and the "
                "rejection invariant would go ungraded")

    def run_with_dataset(self, dataset: dict) -> subprocess.CompletedProcess:
        tmp = self.enterContext(
            tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, encoding="utf-8"))
        json.dump(dataset, tmp, indent=2)
        tmp.close()
        return run_run_evals(Path(tmp.name))

    def test_the_fatal_token_vocabulary_is_stage_0s_own(self):
        """A widened or narrowed gate vocabulary may not drift from the parser's."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "uvest_stage0_vocabulary", REPO_ROOT / "scripts" / "0_ingest_reco.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(set(run_evals.OPTION_TOKENS), set(module.OPTIONS_TYPES))

    def test_every_seeded_probe_is_refused_by_the_live_parser(self):
        """Exit 2 *and* the frozen error code *and* no canonical payload."""
        for probe in self.probes:
            outcome = run_evals.replay_option_probe(probe)
            self.assertTrue(outcome["rejected"],
                            f"{probe['probe_id']}: not rejected — {outcome}")
            self.assertEqual(outcome["exit_code"], run_evals.OPTION_PROBE_EXIT)
            self.assertIn(run_evals.OPTION_PROBE_ERROR, outcome["stderr"])
            self.assertFalse(outcome["emitted_payload"],
                             f"{probe['probe_id']}: rejected but still emitted a trade")

    def test_the_gate_grades_the_roster_and_does_not_skip(self):
        gate = run_evals.gate_option_rejection(self.probes)
        self.assertFalse(gate.skipped)
        self.assertEqual(gate.value, 100.0)
        self.assertIn(f"{len(self.probes)}/{len(self.probes)} option probe",
                      gate.coverage)

    def test_a_paste_the_parser_stops_refusing_fails_the_gate(self):
        """The gate bites: one option paste that reaches stdout is a red run."""
        admitted = dict(self.probes[0], raw_text=(
            "Symbol: RELIANCE\nType: CASH\nEntry Range: 2500.00-2510.00\n"
            "Live Price: 2505.00\nStoploss: 2400.00\nTarget: 2700.00\nRecommended Date: 2026-10-09\n"))
        gate = run_evals.gate_option_rejection([admitted, *self.probes[1:]])
        self.assertLess(gate.value, 100.0)
        self.assertTrue(gate.detail, "a failed probe must name itself")

    def test_the_roster_is_governed_by_its_expected_count(self):
        ok, reason = run_evals.check_option_probe_roster(self.dataset)
        self.assertTrue(ok, reason)
        short = json.loads(json.dumps(self.dataset))
        short["option_probes"] = short["option_probes"][:-1]
        ok, reason = run_evals.check_option_probe_roster(short)
        self.assertFalse(ok, "losing a probe must not pass as a smaller graded set")
        self.assertIn("expected", reason)

    def test_a_probe_without_the_frozen_contract_is_not_evidence(self):
        broken = json.loads(json.dumps(self.dataset))
        broken["option_probes"][0]["expected"] = {"exit_code": 0}
        ok, reason = run_evals.check_option_probe_roster(broken)
        self.assertFalse(ok)
        self.assertIn(run_evals.OPTION_PROBE_ERROR, reason)

    def test_a_probe_may_not_pretend_to_be_sourced(self):
        """A probe is authored input; the roster has to say so itself."""
        undeclared = json.loads(json.dumps(self.dataset))
        undeclared["option_probes"][0]["origin"] = "sourced_recommendation"
        ok, reason = run_evals.check_option_probe_roster(undeclared)
        self.assertFalse(ok)
        self.assertIn(run_evals.OPTION_PROBE_ORIGIN, reason)

    def test_an_emptied_roster_fails_the_run_not_the_gate(self):
        """Dropping the roster is exit 1 with a named mismatch, never a [SKIP]."""
        dataset = json.loads(DATASET_FILE.read_text(encoding="utf-8"))
        dataset["option_probes"] = []
        proc = self.run_with_dataset(dataset)
        out = "\n".join(stdout_lines(proc))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Option probes: MISMATCH", out)
        self.assertIn("0 probe(s) present, expected 5", out)


class TestRefusalProbeGate(unittest.TestCase):
    """Gate 1c: the closed instrument domain, graded live beside gate 1.

    ADR-001 permits equities and futures only, so a paste stage 0 cannot
    recognize as `CASH`/`FUT` must leave with `ERR_INSTRUMENT_UNRECOGNIZED` and
    no canonical payload. The family exists because the option-token scan
    cannot name a homoglyph (`Type: СЕ`), and it is graded separately from gate
    1 because a paste the gate never classified as an option must not be
    counted as one.
    """

    @classmethod
    def setUpClass(cls):
        cls.dataset = json.loads(DATASET_FILE.read_text(encoding="utf-8"))
        cls.probes = cls.dataset.get("refusal_probes") or []
        if not cls.probes:
            raise AssertionError(
                "the refusal-probe roster is empty — gate 1c would [SKIP] and the "
                "closed-domain invariant would go ungraded")

    def run_with_dataset(self, dataset: dict) -> subprocess.CompletedProcess:
        tmp = self.enterContext(
            tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, encoding="utf-8"))
        json.dump(dataset, tmp, indent=2, ensure_ascii=False)
        tmp.close()
        return run_run_evals(Path(tmp.name))

    def test_every_seeded_refusal_probe_is_refused_by_the_live_parser(self):
        """Each probe is replayed, and the code it declares is the code it gets."""
        for probe in self.probes:
            with self.subTest(probe=probe.get("probe_id")):
                outcome = run_evals.replay_refusal_probe(probe)
                self.assertTrue(outcome["rejected"],
                                f"{probe['probe_id']}: admitted — {outcome}")
                self.assertEqual(outcome["exit_code"], run_evals.REFUSAL_PROBE_EXIT)
                self.assertIn(run_evals.REFUSAL_PROBE_ERROR, outcome["stderr"])
                self.assertNotIn(run_evals.OPTION_PROBE_ERROR, outcome["stderr"],
                                 "an unclassified paste must not borrow the option verdict")
                self.assertFalse(outcome["emitted_payload"])

    def test_both_families_are_seeded_and_graded(self):
        """Homoglyph tokens and invented labels — both, not one twice."""
        families = {str(probe.get("family")) for probe in self.probes}
        self.assertEqual(families, set(run_evals.REFUSAL_PROBE_FAMILIES))

        gate = run_evals.gate_refusal_rejection(self.probes)
        self.assertFalse(gate.skipped)
        self.assertEqual(gate.value, 100.0, gate.detail)
        self.assertIn(f"{len(self.probes)}/{len(self.probes)} refusal probe",
                      gate.coverage)
        for family in run_evals.REFUSAL_PROBE_FAMILIES:
            self.assertIn(run_evals.REFUSAL_FAMILY_LABEL[family], gate.coverage)

    def test_a_paste_the_parser_admits_fails_the_gate(self):
        """The gate bites: one admitted paste is a red run, naming the probe."""
        admitted = dict(self.probes[0], raw_text=(
            "Symbol: RELIANCE\nType: CASH\nEntry Range: 2500.00-2510.00\n"
            "Live Price: 2505.00\nStoploss: 2400.00\nTarget: 2700.00\nRecommended Date: 2026-10-09\n"))
        gate = run_evals.gate_refusal_rejection([admitted])
        self.assertEqual(gate.value, 0.0)
        self.assertIn(self.probes[0]["probe_id"], gate.detail)

    def test_the_roster_is_governed_by_its_expected_count(self):
        ok, reason = run_evals.check_refusal_probe_roster(self.dataset)
        self.assertTrue(ok, reason)
        short = json.loads(json.dumps(self.dataset))
        short["refusal_probes"] = short["refusal_probes"][:-1]
        ok, reason = run_evals.check_refusal_probe_roster(short)
        self.assertFalse(ok, "losing a probe must not pass as a smaller graded set")
        self.assertIn("expected", reason)

    def test_a_probe_may_not_declare_the_option_verdict(self):
        """The two codes are the whole point of the two gates: no borrowing."""
        misfiled = json.loads(json.dumps(self.dataset))
        misfiled["refusal_probes"][0]["expected"] = {
            "exit_code": run_evals.REFUSAL_PROBE_EXIT,
            "error_code": run_evals.OPTION_PROBE_ERROR}
        ok, reason = run_evals.check_refusal_probe_roster(misfiled)
        self.assertFalse(ok)
        self.assertIn(run_evals.REFUSAL_PROBE_ERROR, reason)

    def test_an_ascii_option_token_here_is_a_misfiling(self):
        """A probe the *token* gate names belongs to gate 1's roster: filed here
        it would declare a code stage 0 can never return for it."""
        misfiled = json.loads(json.dumps(self.dataset))
        probe = misfiled["refusal_probes"][0]
        probe["declared_instrument"] = "CE"
        probe["raw_text"] = probe["raw_text"].replace("\u0421\u0415", "CE")
        ok, reason = run_evals.check_refusal_probe_roster(misfiled)
        self.assertFalse(ok)
        self.assertIn("option probe", reason)

    def test_a_probe_whose_paste_never_declares_it_is_not_evidence(self):
        undeclared = json.loads(json.dumps(self.dataset))
        undeclared["refusal_probes"][0]["declared_instrument"] = "\u0416\u0416"
        ok, reason = run_evals.check_refusal_probe_roster(undeclared)
        self.assertFalse(ok)
        self.assertIn("does not appear in the paste", reason)

    def test_a_probe_may_not_pretend_to_be_sourced(self):
        undeclared = json.loads(json.dumps(self.dataset))
        undeclared["refusal_probes"][0]["origin"] = "sourced_recommendation"
        ok, reason = run_evals.check_refusal_probe_roster(undeclared)
        self.assertFalse(ok)
        self.assertIn(run_evals.OPTION_PROBE_ORIGIN, reason)

    def test_an_emptied_roster_fails_the_run_not_the_gate(self):
        """Dropping the roster is exit 1 with a named mismatch, never a [SKIP]."""
        dataset = json.loads(DATASET_FILE.read_text(encoding="utf-8"))
        dataset["refusal_probes"] = []
        proc = self.run_with_dataset(dataset)
        out = "\n".join(stdout_lines(proc))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Refusal probes: MISMATCH", out)
        self.assertIn("0 probe(s) present, expected 5", out)


class TestChainAbortGate(unittest.TestCase):
    """Gate 1b: the rejection must survive the 0 → 12 orchestrator.

    Stage 0 refusing (gates 1/1c) is only half of ADR-001 — a user invokes
    `run_pipeline.py`, so what has to hold is that the chain stops: the code is
    propagated, nothing downstream is written, and the run record says which
    stages never ran. A runner that logged the refusal and walked on would keep
    both rejection rates green, which is why this is graded separately — once
    per refusal family, because the two families share exit 2 and are told apart
    by the code alone, so one family's stop is no evidence for the other's.
    """

    @classmethod
    def setUpClass(cls):
        cls.dataset = json.loads(DATASET_FILE.read_text(encoding="utf-8"))
        cls.probes = cls.dataset.get("option_probes") or []
        cls.refusal_probes = cls.dataset.get("refusal_probes") or []
        if not cls.probes or not cls.refusal_probes:
            raise AssertionError(
                "a stage-0 probe roster is empty — gate 1b would grade half the "
                "chain-abort invariant, and the missing family would go ungraded")

    def aborted_record(self, **overrides) -> dict:
        """A run record shaped like a real stage-0 abort (the reference case)."""
        record = {
            "schema": "uvest-run-record/1",
            "outcome": "aborted",
            "exit_code": 2,
            "failed_stage": 0,
            "report": None,
            "stages": [{"stage": 0, "status": "failed", "exit_code": 2},
                       *[{"stage": stage, "status": "not_reached"}
                         for stage in range(1, 13)]],
        }
        record.update(overrides)
        return record

    def test_every_seeded_probe_stops_the_live_chain_for_its_own_family(self):
        """The real orchestrator, invoked the way a user invokes it — and the
        stop measured against the code *that family* is owed, not any exit 2."""
        families = ((self.probes, run_evals.OPTION_PROBE_ERROR),
                    (self.refusal_probes, run_evals.REFUSAL_PROBE_ERROR))
        for roster, error_code in families:
            for probe in roster:
                with self.subTest(probe=probe.get("probe_id"), code=error_code):
                    outcome = run_evals.replay_probe_through_runner(probe, error_code)
                    self.assertTrue(outcome["stopped"],
                                    f"{probe.get('probe_id')}: {outcome['problems']}")
                    self.assertEqual(outcome["exit_code"], run_evals.PROBE_EXIT)

    def test_the_gate_replays_one_named_probe_per_family_and_does_not_skip(self):
        """One replay per family is enough — but the scorecard must say *which*
        two, and the code each was graded against, so neither replay can read
        as roster-wide and an option stop cannot stand in for a homoglyph one."""
        gate = run_evals.gate_chain_abort(self.probes, self.refusal_probes)
        self.assertFalse(gate.skipped)
        self.assertEqual(gate.value, 100.0, gate.detail)
        coverage = gate.coverage
        self.assertIn("2/2 chain replays", coverage)
        self.assertIn(f"{self.probes[0]['probe_id']} with "
                      f"{run_evals.OPTION_PROBE_ERROR}", coverage)
        self.assertIn(f"{self.refusal_probes[0]['probe_id']} with "
                      f"{run_evals.REFUSAL_PROBE_ERROR}", coverage)
        self.assertIn("0 → 12 chain", coverage)
        self.assertNotIn("not seeded", coverage,
                         "both rosters are seeded here — nothing to disclose")

    def test_a_chain_that_carries_the_paste_on_fails_the_gate(self):
        """The gate bites: one walked-on probe is a red run, naming the probe."""
        from unittest import mock
        carried = dict(self.probes[0], probe_id="OPT-CARRIED")
        with mock.patch.object(
                run_evals, "replay_probe_through_runner",
                return_value={"stopped": False, "exit_code": 0,
                              "problems": ["exit 0, expected 2"]}):
            gate = run_evals.gate_chain_abort([carried, *self.probes[1:]],
                                              self.refusal_probes)
        self.assertEqual(gate.value, 0.0)
        self.assertIn("OPT-CARRIED", gate.coverage)
        self.assertIn("OPT-CARRIED", gate.detail)
        self.assertIn("exit 0, expected 2", gate.detail)

    def test_a_chain_that_carries_a_refusal_on_fails_the_gate_alone(self):
        """The half the token scan cannot name is graded on its own legs.

        The option replay stops cleanly while the homoglyph one walks on: a
        half-green gate naming the refusal probe, not a green one that averaged
        the two families together.
        """
        from unittest import mock
        real = run_evals.replay_probe_through_runner
        carried = dict(self.refusal_probes[0], probe_id="REF-CARRIED")

        def fake(probe, error_code=run_evals.OPTION_PROBE_ERROR):
            if error_code == run_evals.REFUSAL_PROBE_ERROR:
                return {"stopped": False, "exit_code": 0,
                        "problems": ["exit 0, expected 2",
                                     f"no {run_evals.REFUSAL_PROBE_ERROR} on stderr"]}
            return real(probe, error_code)

        with mock.patch.object(run_evals, "replay_probe_through_runner",
                               side_effect=fake):
            gate = run_evals.gate_chain_abort(self.probes,
                                              [carried, *self.refusal_probes[1:]])
        self.assertEqual(gate.value, 50.0, gate.detail)
        self.assertIn("REF-CARRIED", gate.detail)
        self.assertIn("exit 0, expected 2", gate.detail)

    def test_an_unseeded_roster_is_disclosed_not_silently_dropped(self):
        """A roster that vanished is a disclosed half-grade here (and a failed
        run at the roster checks and the baseline floor), never a full green
        measured over the one family left."""
        gate = run_evals.gate_chain_abort(self.probes, [])
        self.assertFalse(gate.skipped)
        self.assertEqual(gate.value, 100.0)
        self.assertIn("not seeded", gate.coverage)
        self.assertIn("refusal_probes", gate.coverage)
        self.assertTrue(run_evals.gate_chain_abort([], []).skipped)

    def test_the_stop_verdict_is_negative_sensitive(self):
        """Every way a stopped chain can be faked is a named problem.

        Forged here rather than by sabotaging the runner on disk: the negative
        cases are the whole point of the gate, and they have to be provably
        detected, not merely unobserved.
        """
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            clean = run_evals.chain_abort_failures(
                2, "ERR_OPTIONS_PROHIBITED: instrument_type='CE'", workdir,
                self.aborted_record())
            self.assertEqual(clean, [], "a real stop must not be flagged")

            swallowed = run_evals.chain_abort_failures(
                0, "", workdir, self.aborted_record(exit_code=0, failed_stage=None))
            self.assertTrue(any("exit 0" in problem for problem in swallowed), swallowed)
            self.assertTrue(any("no ERR_OPTIONS_PROHIBITED" in problem
                                for problem in swallowed), swallowed)

            (workdir / "out_1_math.json").write_text("{}", encoding="utf-8")
            carried = run_evals.chain_abort_failures(
                2, run_evals.OPTION_PROBE_ERROR, workdir, self.aborted_record())
            self.assertTrue(any("out_1_math.json" in problem for problem in carried), carried)

            walked_on = self.aborted_record()
            walked_on["stages"][1]["status"] = "ran"
            problems = run_evals.chain_abort_failures(
                2, run_evals.OPTION_PROBE_ERROR, Path(tmp) / "empty", walked_on)
            self.assertTrue(any("not_reached" in problem for problem in problems), problems)

            unrecorded = run_evals.chain_abort_failures(
                2, run_evals.OPTION_PROBE_ERROR, Path(tmp) / "empty", None)
            self.assertIn("no run_record.json to account for the run", unrecorded)

            # The two families share exit 2, so the code alone tells them apart:
            # an option stop must not grade as a homoglyph stop, and vice versa.
            wrong_family = run_evals.chain_abort_failures(
                2, run_evals.OPTION_PROBE_ERROR, Path(tmp) / "empty",
                self.aborted_record(), run_evals.REFUSAL_PROBE_ERROR)
            self.assertTrue(any(
                f"no {run_evals.REFUSAL_PROBE_ERROR} on stderr" in problem
                for problem in wrong_family), wrong_family)

            refusal_stop = run_evals.chain_abort_failures(
                2, run_evals.REFUSAL_PROBE_ERROR, Path(tmp) / "empty",
                self.aborted_record(), run_evals.REFUSAL_PROBE_ERROR)
            self.assertEqual(refusal_stop, [],
                             "a homoglyph stop is a stop of its own")


class TestConeCoverageScope(unittest.TestCase):
    """Gate 5 grades only the tapes the trained window could have produced.

    The envelope's excursion pools come from the shipped booster's corpus
    (`models/lgbm_direction_v1.calibration.json` → `provenance.corpus.row_window`),
    so a forward tape outside that window is a regime the pools never saw:
    grading it would measure the corpus's age rather than the engine. Such a
    record is **disclosed — named, with its span and reason — and excluded from
    the denominator**, never counted as a miss (evals.md §3 #5).
    """

    # A *synthetic* scope for the unit seams below: the shipped window is read
    # from the calibration (`_window_from_calibration`), never pinned here, so
    # a retrain cannot stale this constant — and an out-of-window date under
    # the real one (2020-03-24 is inside it since the corpus reach, ADR-011
    # decision 10) keeps exercising the exclusion these seams cover.
    SYNTHETIC_WINDOW = ("2020-04-09", "2025-10-20")

    @staticmethod
    def _trade(tid: str, *, p10: float, p90: float, lo: float, hi: float) -> dict:
        return {"trade_id": tid, "cone_p10": p10, "cone_p90": p90,
                "forward_prices_22d": {"min": lo, "max": hi}}

    @staticmethod
    def _window_from_calibration() -> tuple[str, str]:
        cal = json.loads((REPO_ROOT / "models" / "lgbm_direction_v1.calibration.json")
                         .read_text(encoding="utf-8"))
        first, last = cal["provenance"]["corpus"]["row_window"]
        return str(first), str(last)

    def test_the_scope_comes_from_the_shipped_calibration_s_own_disclosure(self):
        """The window is what the artifact says it was trained on, not a constant."""
        window = run_evals.trained_corpus_window()
        self.assertIsNotNone(window)
        self.assertEqual(window, self._window_from_calibration())

    def test_a_missing_or_malformed_calibration_has_no_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            absent = Path(tmp) / "nope.json"
            self.assertIsNone(run_evals.trained_corpus_window(absent))
            unscoped = Path(tmp) / "no_window.json"
            unscoped.write_text(json.dumps({"provenance": {"corpus": {}}}),
                                encoding="utf-8")
            self.assertIsNone(run_evals.trained_corpus_window(unscoped))

    def test_an_out_of_window_tape_is_disclosed_and_never_counted(self):
        """A miss outside the window is a disclosure; a miss inside is a miss."""
        inside = self._trade("IN-1", p10=90.0, p90=110.0, lo=80.0, hi=100.0)
        outside = self._trade("OUT-1", p10=90.0, p90=110.0, lo=10.0, hi=999.0)
        gate = run_evals.gate_cone_coverage(
            [inside, outside], window=self.SYNTHETIC_WINDOW,
            spans={"IN-1": ("2024-06-05", "2024-07-05"),
                   "OUT-1": ("2020-03-24", "2020-04-28")})
        status, _display = gate.evaluate()
        self.assertEqual(status, "[FAIL]", "the in-window miss must still fail")
        self.assertEqual(gate.value, 0.0, "one graded row, one miss")
        # The disclosure is printed whatever the verdict, and names the record.
        self.assertIn("1 of 2 cone-labeled trades", gate.coverage)
        self.assertIn("trained corpus window", gate.coverage)
        self.assertIn("OUT-1", gate.coverage)
        self.assertIn("2020-03-24..2020-04-28", gate.coverage)

    def test_the_denominator_is_the_in_window_rows_only(self):
        """The excluded row cannot drag the percentage down *or* prop it up."""
        # The graded row's bars (95..105) sit inside its envelope (90..110); the
        # out-of-window row misses outright and must contribute nothing either way.
        inside = self._trade("IN-1", p10=90.0, p90=110.0, lo=95.0, hi=105.0)
        outside = self._trade("OUT-1", p10=90.0, p90=110.0, lo=10.0, hi=999.0)
        gate = run_evals.gate_cone_coverage(
            [inside, outside], window=self.SYNTHETIC_WINDOW,
            spans={"IN-1": ("2024-06-05", "2024-07-05"),
                   "OUT-1": ("2020-03-24", "2020-04-28")})
        status, _display = gate.evaluate()
        self.assertEqual(gate.value, 100.0)
        self.assertEqual(status, "[PASS]")
        self.assertIn("OUT-1", gate.coverage,
                      "the ungraded record is named beside a PASS too")

    def test_a_record_without_a_pinned_tape_cannot_be_placed_in_the_window(self):
        """No tape to read ⇒ the row cannot be scoped, so it is not graded."""
        trade = self._trade("NOTAPED", p10=90.0, p90=110.0, lo=85.0, hi=105.0)
        gate = run_evals.gate_cone_coverage(
            [trade], window=self.SYNTHETIC_WINDOW, spans={"NOTAPED": None})
        status, _display = gate.evaluate()
        self.assertEqual(status, "[SKIP]")
        self.assertIn("NOTAPED", gate.coverage)

    def test_an_unreadable_window_skips_with_a_reason_instead_of_grading_all(self):
        """A scope that cannot be read is disclosed, never silently dropped."""
        trade = self._trade("IN-1", p10=90.0, p90=110.0, lo=85.0, hi=105.0)
        gate = run_evals.gate_cone_coverage(
            [trade], window=None, spans={"IN-1": ("2024-06-05", "2024-07-05")})
        status, _display = gate.evaluate()
        self.assertEqual(status, "[SKIP]")
        self.assertIn("row_window", gate.detail)
        self.assertIsNone(gate.value, "no number over an unscoreable scope")

    def test_the_span_is_read_from_the_same_tape_the_numbers_come_from(self):
        raw = json.loads(DATASET_FILE.read_text(encoding="utf-8"))
        trade = next(t for t in raw["trades"] if t.get("cone_p10") is not None)
        span = dataset_refs.forward_tape_span(trade)
        self.assertIsNotNone(span)
        tape = trade["forward_prices_22d"]["$derive"]["tape"]
        bars = json.loads((REPO_ROOT / tape).read_text(encoding="utf-8"))["bars"]
        self.assertEqual(span, (str(bars[0]["date"]), str(bars[-1]["date"])))

    def test_an_inline_range_has_no_tape_to_place(self):
        self.assertIsNone(dataset_refs.forward_tape_span(
            {"trade_id": "x", "forward_prices_22d": {"min": 1.0, "max": 2.0}}))

    def test_the_covid_tapes_are_inside_the_scope_and_all_six_grade(self):
        """End to end on the real dataset: the corpus reaches its own benchmark.

        ADR-011 decision 10: the shipped window must open before the
        benchmark's earliest graded tape, so the two Covid tapes that decision
        9's disclosure had named ungraded are *graded* here — the reach gap is
        a corpus defect, fixed by re-fetching and retraining, never by leaving
        a tape out of the denominator. The disclosure machinery itself stays
        covered by the synthetic seams above, which do not depend on where the
        real window opens.
        """
        raw = json.loads(DATASET_FILE.read_text(encoding="utf-8"))
        window = self._window_from_calibration()
        candidates = [t for t in raw["trades"]
                      if t.get("cone_p10") is not None and t.get("cone_p90") is not None]
        inside, outside = [], []
        for trade in candidates:
            span = dataset_refs.forward_tape_span(trade)
            hit = bool(span and window[0] <= span[0] and span[1] <= window[1])
            (inside if hit else outside).append(str(trade["trade_id"]))
        self.assertTrue(inside, "the benchmark must have an in-window record")
        self.assertEqual(outside, [],
                         "the shipped corpus must reach every graded tape "
                         f"(out of scope: {outside})")
        self.assertIn("SCEN-001", inside,
                      "the Covid RELIANCE tape (2020-03-24..2020-04-28) must be gradeable")
        self.assertIn("SCEN-002", inside,
                      "the Covid SBIN tape (2020-03-25..2020-04-29) must be gradeable")
        proc = run_run_evals(DATASET_FILE)
        scorecard = "\n".join(stdout_lines(proc))
        self.assertIn("trained corpus window", scorecard)
        self.assertIn(f"{len(candidates)} of {len(candidates)} cone-labeled trades graded",
                      scorecard)
        scope_lines = [ln for ln in stdout_lines(proc)
                       if "scope: trained corpus window" in ln]
        self.assertTrue(scope_lines, scorecard)
        self.assertNotIn("ungraded", scope_lines[0],
                         "no tape may fall outside the scope anymore: the "
                         "coverage line must carry no exclusion")
        self.assertEqual(proc.returncode, 1, scorecard)


class TestConeMissDiagnosis(unittest.TestCase):
    """A coverage miss says where the realised excursion sat in its own pool.

    Gate 5's red (0.0% against >= 80%) is only actionable if each miss carries
    its own diagnosis: the realised low/high as percent of the entry, and the
    percentile that value occupies among the *observed* excursions of the pool
    the cone actually read for that record — the trade's own sector's block
    when it is deep enough to answer, the pooled block otherwise (stage 4's
    `samples_for`, imported, never re-derived). Percentiles are equal-weight
    over the pool's atoms: the class-probability mixture the levels invert is
    not recoverable from the frozen 8-key payload, so the disclosure counts
    atoms — "where it sits in the pool" is exactly that share.
    """

    WINDOW = ("2020-01-01", "2025-12-31")

    @staticmethod
    def _trade(tid: str, *, lo: float, hi: float, p10: float, p90: float,
               entry: float = 100.0, symbol: str = "SBIN") -> dict:
        return {"trade_id": tid, "symbol": symbol, "entry_price": entry,
                "cone_p10": p10, "cone_p90": p90,
                "forward_prices_22d": {"min": lo, "max": hi}}

    @staticmethod
    def _block(depth: int) -> dict:
        """A calibration-shaped block: `depth` atoms per class per measure."""
        stage4 = run_evals.load_stage4()
        signed = {"mae_pct": lambda i: -(i + 1.0),
                  "mfe_pct": lambda i: i + 1.0,
                  "forward_return_pct": lambda i: i - depth / 2.0}
        return {measure: {cls: [make(i) for i in range(depth)]
                          for cls in stage4.CLASSES}
                for measure, make in signed.items()}

    def _calibration(self, *, sector: str = "^NSEBANK", depth: int = 30,
                     pooled_depth: int = 30) -> dict:
        return {"samples": self._block(pooled_depth),
                "samples_by_sector": {sector: self._block(depth)},
                "provenance": {"corpus": {"row_window": list(self.WINDOW)}}}

    def _gate(self, trades, calibration):
        return run_evals.gate_cone_coverage(
            trades, window=self.WINDOW,
            spans={str(t["trade_id"]): ("2024-06-05", "2024-07-05")
                   for t in trades},
            calibration=calibration)

    def test_a_miss_names_the_pool_the_cone_read_and_its_percentile(self):
        """The low side breached, so the adverse pool carries the diagnosis."""
        # Entry 100, bars bottom at 80 = -20.00%, below the -5.00% floor.
        # The ^NSEBANK block holds 3 x 30 adverse atoms in -1..-30; 33 of 90
        # are <= -20, so the realised low sits at the 36.7th percentile.
        gate = self._gate([self._trade("MISS-1", lo=80.0, hi=102.0,
                                       p10=95.0, p90=110.0)],
                          self._calibration())
        status, _display = gate.evaluate()
        self.assertEqual(status, "[FAIL]")
        self.assertIn(
            "MISS-1: low -20.00% = 36.7th pct of the ^NSEBANK adverse pool "
            "(floor -5.00%)", gate.detail)

    def test_a_high_side_miss_diagnoses_the_favourable_pool(self):
        gate = self._gate([self._trade("MISS-2", lo=98.0, hi=163.0,
                                       p10=90.0, p90=114.0)],
                          self._calibration())
        self.assertIn(
            "MISS-2: high +63.00% = 100.0th pct of the ^NSEBANK favourable "
            "pool (floor +14.00%)", gate.detail)

    def test_a_thin_sector_block_falls_back_to_the_pooled_pool_and_says_so(self):
        """The fallback the stage answered with is named, never hidden."""
        gate = self._gate([self._trade("MISS-3", lo=80.0, hi=102.0,
                                       p10=95.0, p90=110.0)],
                          self._calibration(depth=5, pooled_depth=30))
        self.assertIn(
            "MISS-3: low -20.00% = 36.7th pct of the pooled adverse pool "
            "(floor -5.00%)", gate.detail)

    def test_a_covered_row_carries_no_bogus_diagnosis(self):
        gate = self._gate([self._trade("HIT-1", lo=95.0, hi=105.0,
                                       p10=90.0, p90=110.0)],
                          self._calibration())
        status, _display = gate.evaluate()
        self.assertEqual(status, "[PASS]")
        self.assertNotIn("pct of the", gate.detail)

    def test_without_a_calibration_the_old_sentence_stands(self):
        """No pool bytes, no percentile: the diagnosis is never invented."""
        gate = run_evals.gate_cone_coverage(
            [self._trade("MISS-4", lo=80.0, hi=102.0,
                         p10=95.0, p90=110.0)],
            window=self.WINDOW,
            spans={"MISS-4": ("2024-06-05", "2024-07-05")})
        self.assertIn("missed [P10, P90]", gate.detail)
        self.assertNotIn("pct of the", gate.detail)

    def test_an_entry_less_miss_is_counted_without_a_percentile(self):
        """A record with no entry price cannot ground a percent excursion."""
        trade = self._trade("MISS-5", lo=80.0, hi=102.0,
                            p10=95.0, p90=110.0)
        trade["entry_price"] = None
        gate = self._gate([trade], self._calibration())
        self.assertIn("missed [P10, P90]", gate.detail)
        self.assertNotIn("pct of the", gate.detail)

    def test_the_shipped_calibration_diagnoses_a_real_record(self):
        """End to end on the real artifact: numbers read out of committed bytes.

        SCEN-008 (ADANIPORTS, entry 699.00) fell to 395.10 — -43.48% against a
        -8.98% floor — so the detail must place that low inside the block the
        stage read for it, at a percentile below the floor's own 10th.
        """
        calibration = run_evals.load_calibration()
        self.assertIsNotNone(calibration)
        gate = run_evals.gate_cone_coverage(
            [self._trade("SCEN-008", lo=395.1, hi=656.6,
                         p10=636.22, p90=822.02, entry=699.0,
                         symbol="ADANIPORTS")],
            window=self.WINDOW,
            spans={"SCEN-008": ("2023-01-30", "2023-03-03")},
            calibration=calibration)
        match = re.search(
            r"SCEN-008: low -43\.48% = (\d+\.\d)th pct of the (\S+) adverse "
            r"pool \(floor -8\.98%\)", gate.detail)
        self.assertIsNotNone(match, gate.detail)
        self.assertLess(float(match.group(1)), 10.0,
                        "a value below P10 must sit in the pool's lower tail")


class TestProbeRosterFloor(unittest.TestCase):
    """The roster contents are a frozen regression reference, not a comment.

    `baseline_scorecard.json` freezes both floors banked by
    `scaffold_catalog.re_freeze_baseline`: `probe_rosters` (the sizes) and
    `probe_roster_ids` (each probe's identity — the hash of its paste *and*
    its declared metadata, keyed by `probe_id`). Growing a roster is free;
    shrinking one, editing a frozen paste, relabelling a family or contract,
    or swapping a probe for a different one — the cases a size-only floor
    cannot see — is a named shortfall and a red run.
    """

    FLOOR = {"option_probes": 5, "refusal_probes": 5}

    @staticmethod
    def roster(prefix: str, count: int, start: int = 1) -> list[dict]:
        """Synthetic probes whose paste derives from their id, so identity is
        controlled without copying a real paste."""
        return [{"probe_id": f"{prefix}-{i:03d}",
                 "raw_text": f"paste for {prefix}-{i:03d}"}
                for i in range(start, start + count)]

    @staticmethod
    def baseline_for(rosters: dict[str, list[dict]]) -> dict:
        """A baseline that froze exactly these rosters — sizes *and* hashes."""
        return {"probe_rosters": {name: len(p) for name, p in rosters.items()},
                "probe_roster_ids": run_evals.probe_roster_ids(rosters)}

    def test_no_floor_yet_is_disclosed_not_enforced(self):
        """Before any re-freeze has banked a size, the check says so and passes.
        """
        ok, reason, shortfalls = run_evals.check_probe_roster_floor(
            {"option_probes": self.roster("OPT", 3)}, {})
        self.assertTrue(ok)
        self.assertEqual(shortfalls, [])
        self.assertIn("no frozen probe-roster floor", reason)

    def test_growth_past_the_floor_is_free(self):
        """More probes than frozen is the census's growth case: free."""
        frozen = self.roster("OPT", 5)
        baseline = self.baseline_for({"option_probes": frozen,
                                      "refusal_probes": self.roster("REF", 5)})
        grown = {"option_probes": [*frozen, *self.roster("OPT", 2, start=6)],
                 "refusal_probes": self.roster("REF", 6)}
        ok, reason, shortfalls = run_evals.check_probe_roster_floor(grown, baseline)
        self.assertTrue(ok, shortfalls)
        self.assertIn("13 seeded stage-0 probe(s)", reason)

    def test_a_shrunken_roster_fails_by_name_and_delta(self):
        baseline = self.baseline_for({"option_probes": self.roster("OPT", 5),
                                      "refusal_probes": self.roster("REF", 5)})
        shrunken = {"option_probes": self.roster("OPT", 5),
                    "refusal_probes": self.roster("REF", 4)}
        ok, reason, shortfalls = run_evals.check_probe_roster_floor(shrunken, baseline)
        self.assertFalse(ok)
        self.assertIn("refusal_probes: 4 seeded probe(s) < frozen 5", shortfalls)
        # the missing member is named too: a count says how many went, not which
        self.assertTrue(any("refusal_probes/REF-005: frozen probe is not seeded"
                            in s for s in shortfalls), shortfalls)
        self.assertIn("2 floor violation(s)", reason)

    def test_a_roster_that_vanished_is_a_shrink_to_zero(self):
        """No roster at all is not 'no rosters to check' — it is both floors
        unmet: one size shortfall per roster and one naming shortfall per
        frozen member."""
        baseline = self.baseline_for({"option_probes": self.roster("OPT", 5),
                                      "refusal_probes": self.roster("REF", 5)})
        ok, _reason, shortfalls = run_evals.check_probe_roster_floor({}, baseline)
        self.assertFalse(ok)
        self.assertEqual(len(shortfalls), 2 + 10)

    def test_a_swapped_probe_cannot_preserve_the_count(self):
        """THE case a size-only floor cannot see: same roster size, same shape
        of claim, a different paste under a new id. The frozen member is gone
        and the floor says which one — swapping is only a re-freeze."""
        frozen = {"option_probes": self.roster("OPT", 5),
                  "refusal_probes": self.roster("REF", 5)}
        baseline = self.baseline_for(frozen)
        swapped = {"option_probes": frozen["option_probes"],
                   "refusal_probes": [*self.roster("REF", 4),
                                      {"probe_id": "REF-999",
                                       "raw_text": "paste for REF-999"}]}
        ok, _reason, shortfalls = run_evals.check_probe_roster_floor(swapped, baseline)
        self.assertFalse(ok,
                         "a same-size roster with a swapped member passed the floor")
        self.assertTrue(any("refusal_probes/REF-005: frozen probe is not seeded"
                            in s for s in shortfalls), shortfalls)
        self.assertFalse(any("seeded probe(s) < frozen" in s for s in shortfalls),
                         "the count is intact — only the identity check sees this")

    def test_an_edited_paste_fails_even_when_the_id_holds(self):
        frozen = {"option_probes": self.roster("OPT", 5),
                  "refusal_probes": self.roster("REF", 5)}
        baseline = self.baseline_for(frozen)
        edited = {"option_probes": frozen["option_probes"],
                  "refusal_probes": [*self.roster("REF", 4),
                                     {"probe_id": "REF-005",
                                      "raw_text": "paste for REF-005 tweaked"}]}
        ok, _reason, shortfalls = run_evals.check_probe_roster_floor(edited, baseline)
        self.assertFalse(ok)
        self.assertTrue(any("refusal_probes/REF-005: probe edited since the last"
                            in s for s in shortfalls), shortfalls)

    def test_a_relabelled_family_fails_even_when_the_paste_holds(self):
        """The paste is byte-identical, the graded replay would be identical —
        and the roster check validates a family's *vocabulary*, not that this
        probe still belongs to it. Only the identity floor can see the claim
        the scorecard is now making about REF-005."""
        labelled = [{"probe_id": f"REF-{i:03d}", "raw_text": f"paste for REF-{i:03d}",
                     "family": "unrecognized_instrument"}
                    for i in range(1, 6)]
        baseline = self.baseline_for({"option_probes": self.roster("OPT", 5),
                                      "refusal_probes": labelled})
        relabelled = [*self.roster("REF", 4),
                      {"probe_id": "REF-005", "raw_text": "paste for REF-005",
                       "family": "homoglyph_option"}]
        ok, _reason, shortfalls = run_evals.check_probe_roster_floor(
            {"option_probes": self.roster("OPT", 5),
             "refusal_probes": relabelled}, baseline)
        self.assertFalse(ok,
                         "relabelling a family changed nothing but the claim")
        self.assertTrue(any("refusal_probes/REF-005: probe edited since the last"
                            in s for s in shortfalls), shortfalls)
        self.assertFalse(any("seeded probe(s) < frozen" in s for s in shortfalls),
                         "the roster still holds five — only identity sees this")

    def test_a_relabelled_contract_fails_even_when_the_paste_holds(self):
        """The contract is what the roster check enforces and what the gate
        replays against, so a frozen ERR_INSTRUMENT_UNRECOGNIZED claim that
        quietly becomes the options verdict is a changed claim — caught even
        though both codes are 'exit 2 with no payload' in shape."""
        import copy
        dataset = json.loads(DATASET_FILE.read_text(encoding="utf-8"))
        frozen = {name: copy.deepcopy(dataset.get(name) or [])
                  for name in run_evals.PROBE_ROSTERS}
        baseline = self.baseline_for(frozen)
        relabelled = copy.deepcopy(frozen)
        relabelled["refusal_probes"][-1]["expected"] = {
            **relabelled["refusal_probes"][-1]["expected"],
            "error_code": "ERR_OPTIONS_PROHIBITED"}
        ok, _reason, shortfalls = run_evals.check_probe_roster_floor(relabelled,
                                                                    baseline)
        self.assertFalse(ok, "a relabelled contract kept the run green")
        gone = frozen["refusal_probes"][-1]["probe_id"]
        self.assertTrue(any(f"refusal_probes/{gone}: probe edited since the last"
                            in s for s in shortfalls), shortfalls)

    def test_a_duplicated_probe_id_is_named(self):
        baseline = self.baseline_for({"option_probes": self.roster("OPT", 5),
                                      "refusal_probes": self.roster("REF", 5)})
        duped = {"option_probes": self.roster("OPT", 5),
                 "refusal_probes": [*self.roster("REF", 4),
                                    {"probe_id": "REF-001",
                                     "raw_text": "paste for REF-001"}]}
        ok, _reason, shortfalls = run_evals.check_probe_roster_floor(duped, baseline)
        self.assertFalse(ok)
        self.assertTrue(any("refusal_probes/REF-001: duplicated probe_id" in s
                            for s in shortfalls), shortfalls)

    def test_a_roster_added_since_the_freeze_is_not_checked(self):
        """Growth is free until the new members are banked, exactly as for the
        rule census — a new roster must not fail a run that grew on purpose."""
        baseline = self.baseline_for({"option_probes": self.roster("OPT", 5),
                                      "refusal_probes": self.roster("REF", 5)})
        grown = {"option_probes": self.roster("OPT", 5),
                 "refusal_probes": self.roster("REF", 5),
                 "image_probes": self.roster("IMG", 2)}
        ok, _reason, shortfalls = run_evals.check_probe_roster_floor(grown, baseline)
        self.assertTrue(ok, shortfalls)

    def test_the_committed_baseline_banks_the_dataset_s_own_rosters(self):
        """The floor on disk and the rosters in the dataset are one claim; if
        they ever disagree, the frozen floor is what the run is measured
        against — so this seam makes the disagreement visible first."""
        counts = run_evals.dataset_probe_roster_counts()
        ids = run_evals.dataset_probe_roster_ids()
        frozen = run_evals.load_baseline() or {}
        self.assertEqual(
            frozen.get("probe_rosters"), counts,
            "baseline_scorecard.json's probe_rosters is stale against the "
            "dataset — re-freeze deliberately (scaffold_catalog)")
        self.assertEqual(
            frozen.get("probe_roster_ids"), ids,
            "baseline_scorecard.json's probe_roster_ids is stale — a probe's "
            "paste or declared metadata changed without a re-freeze")
        self.assertEqual(counts, self.FLOOR)
        self.assertEqual({name: len(m) for name, m in ids.items()}, self.FLOOR)

    def test_re_freeze_banks_both_floors(self):
        """`re_freeze_baseline` writes both floors the runner measures against —
        exercised against a scratch proof root, so the real one is never
        touched by a test."""
        import shutil
        from unittest import mock
        import scaffold_catalog as sc
        scratch = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name in (sc.BASELINE_FILE, sc.MANIFEST_FILE, sc.HASH_FILE):
            shutil.copy2(PROOF_DIR / name, scratch / name)
        before = json.loads((scratch / sc.BASELINE_FILE).read_text(encoding="utf-8"))
        with mock.patch.object(sc, "PROOF_ROOT", scratch):
            sc.re_freeze_baseline()
        after = json.loads((scratch / sc.BASELINE_FILE).read_text(encoding="utf-8"))
        self.assertEqual(after["probe_rosters"],
                         run_evals.dataset_probe_roster_counts())
        self.assertEqual(after["probe_roster_ids"],
                         run_evals.dataset_probe_roster_ids(),
                         "a re-freeze must bank the members, not only the count")
        self.assertEqual(after["rule_exercises"], before["rule_exercises"],
                         "a re-freeze must not silently drop the census floor")
        self.assertEqual(after["manifest_sha256"], before["manifest_sha256"],
                         "same bytes in, same hash out — a re-freeze is not a "
                         "rewrite of the proof root")

    def test_the_runner_fails_the_run_when_a_roster_shrinks(self):
        """End to end: one probe deleted is exit 1, naming the roster and the
        frozen size next to the roster check that already refuses to grade it."""
        dataset = json.loads(DATASET_FILE.read_text(encoding="utf-8"))
        dataset["refusal_probes"] = dataset["refusal_probes"][:-1]
        tmp = self.enterContext(tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"))
        json.dump(dataset, tmp, indent=2, ensure_ascii=False)
        tmp.close()
        proc = run_run_evals(Path(tmp.name))
        out = "\n".join(stdout_lines(proc))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Probe roster floor: REGRESSION", out)
        self.assertIn("refusal_probes: 4 seeded probe(s) < frozen 5", out)
        # ...and the member that went is named: a count says how many, not which.
        gone = json.loads(DATASET_FILE.read_text(
            encoding="utf-8"))["refusal_probes"][-1]["probe_id"]
        self.assertIn(f"{gone}: frozen probe is not seeded", out)

    def test_the_runner_catches_a_swapped_probe_a_count_cannot(self):
        """End to end on the *real* roster: replace the last refusal probe with
        a new one — same count, same family, same frozen contract, still
        refused live by stage 0 — so every count-based check passes and only
        the identity floor can go red."""
        dataset = json.loads(DATASET_FILE.read_text(encoding="utf-8"))
        replaced = dataset["refusal_probes"][-1]["probe_id"]
        template = dataset["refusal_probes"][-1]
        dataset["refusal_probes"] = [
            *dataset["refusal_probes"][:-1],
            {**template, "probe_id": "REF-SWAP",
             "declared_instrument": "FUTURES",
             "raw_text": "Symbol: RELIANCE\nType: FUTURES\n"
                         "Entry Range: 2500.00-2510.00\nLive Price: 2505.00\n"
                         "Stoploss: 2400.00\nTarget: 2700.00\nRecommended Date: 2026-10-09\n"}]
        tmp = self.enterContext(tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"))
        json.dump(dataset, tmp, indent=2, ensure_ascii=False)
        tmp.close()
        proc = run_run_evals(Path(tmp.name))
        out = "\n".join(stdout_lines(proc))
        self.assertEqual(proc.returncode, 1,
                         "a swapped probe kept the run green")
        self.assertIn("Probe roster floor: REGRESSION", out)
        self.assertIn(f"refusal_probes/{replaced}: frozen probe is not seeded",
                      out)
        # Everything count-based stayed green — this is precisely the gap the
        # identity floor exists to close.
        self.assertIn("Refusal probes: OK (5/5", out)
        self.assertIn("[PASS] Unrecognized Instrument Guardrail      100.0%", out)

    def test_the_scorecard_states_the_floor_it_is_measured_against(self):
        """The floor is printed whatever the verdict — a reader sees the size
        behind the percentage next to the percentage itself."""
        out = "\n".join(stdout_lines(run_run_evals(DATASET_FILE)))
        self.assertIn("Probe roster floor: OK", out)
        self.assertIn("10 seeded stage-0 probe(s)", out)


class TestBaselineRegressionExit(unittest.TestCase):
    """The pre-commit hook tolerates banked reds, never regressions.

    `githooks/pre-commit` runs the scorecard with `--regression-exit`: gates 4
    and 5 are red *by policy* (published, not tuned), so an absolute exit-1 made
    every commit need `--no-verify` — the hook caught nothing. The frozen
    baseline's `gates` block (banked by `run_evals.py --re-freeze-gates`, the
    deliberate publication) is what the run is measured against instead:

    * a banked red that sits still is not a regression — that is the policy;
    * a PASS that stops passing, or grading that disappears (→ SKIP), blocks;
    * a banked red that gets *worse* blocks; a red that improves is a hint to
      re-bank, never a block;
    * a red nobody ever banked blocks — a published red must be deliberate.

    Non-gate failures (dataset integrity, probe rosters, evidence coverage,
    rule census) are not part of this function: they are absolute correctness
    or already floor-compared, and `main` keeps failing on them in every mode.
    """

    @staticmethod
    def gate(name="Gate", value=50.0, threshold=80.0, higher_is_pass=True,
             skipped=False):
        g = run_evals.Gate(name, threshold, higher_is_pass, fmt="{:.3f}")
        g.value = value
        g.skipped = skipped
        return g

    @staticmethod
    def bank(*entries):
        return {"gates": {name: entry for name, entry in entries}}

    def compare(self, gates, baseline):
        return run_evals.baseline_gate_regressions(gates, baseline)

    def test_an_unbanked_red_blocks(self):
        """No `gates` block (or no entry): a FAIL has never been published."""
        for baseline in ({}, {"gates": {}}):
            with self.subTest(baseline=baseline):
                regressions, improvements = self.compare(
                    [self.gate("Directional Brier Score", value=0.483,
                               threshold=0.18, higher_is_pass=False)],
                    baseline)
                self.assertEqual(improvements, [])
                self.assertEqual(len(regressions), 1)
                self.assertIn("never banked", regressions[0])
                self.assertIn("Directional Brier Score", regressions[0])

    def test_unbanked_pass_and_skip_never_block(self):
        """Nothing red, nothing to publish: green and ungraded both stand."""
        regressions, improvements = self.compare(
            [self.gate("Option", value=100.0, threshold=100.0),
             self.gate("Limit", value=None, skipped=True)], {})
        self.assertEqual((regressions, improvements), ([], []))

    def test_a_banked_red_that_is_unchanged_is_not_a_regression(self):
        """The published-red case itself: same verdict, same value."""
        regressions, improvements = self.compare(
            [self.gate("Cone", value=0.0, threshold=80.0)],
            self.bank(("Cone", {"verdict": "FAIL", "value": 0.0})))
        self.assertEqual((regressions, improvements), ([], []))

    def test_a_banked_red_that_gets_worse_blocks_and_better_hints(self):
        """Both directions, both threshold polarities."""
        worse = self.gate("Brier", value=0.55, threshold=0.18,
                          higher_is_pass=False)
        better = self.gate("Brier", value=0.40, threshold=0.18,
                           higher_is_pass=False)
        baseline = self.bank(("Brier", {"verdict": "FAIL", "value": 0.483}))
        regressions, improvements = self.compare([worse], baseline)
        self.assertEqual(improvements, [])
        self.assertEqual(len(regressions), 1)
        self.assertIn("worse than the banked red", regressions[0])
        regressions, improvements = self.compare([better], baseline)
        self.assertEqual(regressions, [])
        self.assertEqual(len(improvements), 1)
        self.assertIn("improved", improvements[0])
        # higher_is_pass: a coverage red sinking from the banked 76.02 blocks.
        regressions, _ = self.compare(
            [self.gate("Coverage", value=70.0, threshold=80.0)],
            self.bank(("Coverage", {"verdict": "FAIL", "value": 76.02})))
        self.assertEqual(len(regressions), 1)
        self.assertIn("worse than the banked red", regressions[0])

    def test_a_pass_that_stops_passing_blocks(self):
        """The regression the hook exists for: green went red."""
        regressions, improvements = self.compare(
            [self.gate("Breakers", value=80.0, threshold=100.0)],
            self.bank(("Breakers", {"verdict": "PASS", "value": 100.0})))
        self.assertEqual(improvements, [])
        self.assertEqual(len(regressions), 1)
        self.assertIn("PASS → FAIL", regressions[0])

    def test_grading_that_disappears_blocks(self):
        """PASS → SKIP and FAIL → SKIP both mean: the gate stopped showing its
        work, which is exactly how a smaller graded set hides behind a number."""
        for was, frozen_value in (("PASS", 100.0), ("FAIL", 60.0)):
            with self.subTest(was=was):
                regressions, _ = self.compare(
                    [self.gate("Gate", value=None, skipped=True)],
                    self.bank(("Gate", {"verdict": was,
                                        "value": frozen_value})))
                self.assertEqual(len(regressions), 1)
                self.assertIn(f"{was} → SKIP", regressions[0])
                self.assertIn("grading lost", regressions[0])

    def test_a_red_that_turns_green_is_an_improvement_hint(self):
        """Never a block — but it must ask for a re-bank, or the stale bank is
        how a later regression goes unnoticed."""
        regressions, improvements = self.compare(
            [self.gate("Gate", value=100.0, threshold=80.0)],
            self.bank(("Gate", {"verdict": "FAIL", "value": 60.0})))
        self.assertEqual(regressions, [])
        self.assertEqual(len(improvements), 1)
        self.assertIn("FAIL → PASS", improvements[0])

    def test_a_newly_graded_red_must_be_banked(self):
        """A gate that finally grades — and fails — is a *new* published red:
        deliberate banking, not a quiet ship (stage 8's arrival is the case)."""
        regressions, improvements = self.compare(
            [self.gate("Limit", value=50.0, threshold=75.0)],
            self.bank(("Limit", {"verdict": "SKIP", "value": None})))
        self.assertEqual(improvements, [])
        self.assertEqual(len(regressions), 1)
        self.assertIn("SKIP → FAIL", regressions[0])
        self.assertIn("re-freeze-gates", regressions[0])

    def test_banking_writes_verdict_and_value_and_keeps_the_other_keys(self):
        """`bank_gate_values` is a surgical write: the hashes and floors the
        other writers own must survive it byte-for-byte."""
        import tempfile
        scratch = Path(self.enterContext(tempfile.TemporaryDirectory()))
        target = scratch / "baseline_scorecard.json"
        target.write_text(json.dumps({
            "schema": "uvest-baseline-scorecard/1",
            "rule_exercises": {"clean_bullish": 18},
            "manifest_sha256": "f" * 64,
        }, indent=2) + "\n", encoding="utf-8")
        gates = [self.gate("A", value=100.0, threshold=100.0),
                 self.gate("B", value=None, skipped=True),
                 self.gate("C", value=0.0, threshold=80.0)]
        banked = run_evals.bank_gate_values(gates, path=target)
        self.assertEqual(banked["gates"], {
            "A": {"verdict": "PASS", "value": 100.0},
            "B": {"verdict": "SKIP", "value": None},
            "C": {"verdict": "FAIL", "value": 0.0},
        })
        on_disk = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["rule_exercises"], {"clean_bullish": 18})
        self.assertEqual(on_disk["manifest_sha256"], "f" * 64)
        self.assertEqual(on_disk["schema"], "uvest-baseline-scorecard/1")
        # Round-trip: banking once makes every current gate non-regressing.
        regressions, _ = run_evals.baseline_gate_regressions(gates, on_disk)
        self.assertEqual(regressions, [])
        # The proof-root write convention (scaffold_catalog.write_text_utf8:
        # CRLF on disk, LF-normalized dumps): the two writers of one file must
        # produce the same line endings or every re-freeze churns the diff.
        raw = target.read_bytes()
        self.assertTrue(raw.endswith(b"}\r\n"))
        self.assertEqual(raw.count(b"\r\n"), raw.count(b"\n"),
                         "no bare LF — proof-root files are CRLF on disk")


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
        52/52 equality is lockable in by `distribution_verified` at curation's
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
        self.assertIn("stages ran: [0, 1, 2, 3, 4, 5, 6, 7, 9, 10, 12]", block)
        self.assertIn("skipped: [8, 11]", block)
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

    def test_the_panic_breaker_is_a_conjunction_of_both_arms(self):
        """BRD §10.3 #6 trips only when BOTH arms do.

        Breadth below 30 with a calm VIX is a Risk-Off print, not the panic
        breaker (ADR-010 decision 6) — the OR that grades the R:R arms would
        misgrade this conjunction, so `BREAKER_COMBINE` decides per breaker.
        """
        both = {"trade_id": "GB-TEST-P1", "trade_class": "regime_shock",
                "script_outputs": {"out_7_regime.json":
                                   {"vix_value": 26.4,
                                    "market_breadth_pct": 28.5}}}
        arms = run_evals.breaker_arms(both, "panic_regime")
        self.assertEqual(arms, {"panic_vix_above_24": True,
                                "panic_breadth_below_30": True})
        self.assertIs(run_evals.class_breaker_observed(both), True)
        self.assertEqual(
            run_evals.rule_exercises("regime_shock", both),
            frozenset({("panic_vix_above_24", 26.4),
                       ("panic_breadth_below_30", 28.5)}),
            "a trap row exercises both arms at their own granule")
        narrow = {"trade_id": "GB-TEST-P2", "trade_class": "regime_shock",
                  "script_outputs": {"out_7_regime.json":
                                     {"vix_value": 15.0,
                                      "market_breadth_pct": 22.0}}}
        self.assertIs(run_evals.class_breaker_observed(narrow), False,
                      "one arm alone is never this breaker")
        self.assertEqual(
            run_evals.rule_exercises("regime_shock", narrow),
            frozenset({("panic_vix_above_24:clear", 15.0),
                       ("panic_breadth_below_30:clear", 22.0)}),
            "a row the conjunction did not fire exercised the pair from the "
            "holding side — every arm at its own value, `:clear`")

    def test_a_clean_row_never_leaks_the_panic_breaker(self):
        """The clean class sweeps every registered breaker: an out_7 payload
        with a single panic-side number (breadth 25, calm VIX) must not read
        as a leak — the conjunction decides, exactly as at the trap side."""
        trade = {"trade_id": "GB-TEST-C1", "trade_class": "clean_bullish",
                 "script_outputs": {"out_7_regime.json":
                                    {"vix_value": 15.0,
                                     "market_breadth_pct": 25.0}}}
        self.assertIs(run_evals.class_breaker_observed(
            {**trade, "trade_class": "regime_shock"}), False)
        self.assertIn(("panic_breadth_below_30:clear", 25.0),
                      run_evals.rule_exercises("clean_bullish", trade),
                      "the clear half of the conjunction is still an exercise")

    def test_the_panic_breaker_grades_only_over_recorded_out_7_bytes(self):
        """Gate 2 reads `out_7_regime.json`: recording decides graded vs
        abstained, exactly as for every other breaker artifact (E1)."""
        payload = {"vix_value": 26.4, "market_breadth_pct": 28.5}
        row = {"trade_id": "GB-TEST-P3", "trade_class": "regime_shock",
               "script_outputs": {"out_7_regime.json": payload}}
        # A target behind the payload ⇒ the row is graded and its violation
        # counts against the class quota.
        recorded = {("GB-TEST-P3", "out_7_regime.json"): Path("out_7_regime.json")}
        unbacked = run_evals.unbacked_payloads([row], recorded)
        self.assertEqual(unbacked, frozenset())
        gate = run_evals.gate_hard_breakers([row], unbacked, {"regime_shock": 1})
        self.assertFalse(gate.skipped, gate.detail)
        self.assertIn("regime_shock 1/1 graded", gate.coverage)
        self.assertIn("1 graded, 0 violation(s)", gate.detail,
                      "the trap class exists to carry the breaker: both arms "
                      "encoded is a pass, not a violation")
        # The real violation: a recorded trap row whose conjunction never fired.
        calm = {"trade_id": "GB-TEST-P4", "trade_class": "regime_shock",
                "script_outputs": {"out_7_regime.json":
                                   {"vix_value": 15.0, "market_breadth_pct": 22.0}}}
        calm_targets = {("GB-TEST-P4", "out_7_regime.json"): Path("out_7_regime.json")}
        violating = run_evals.gate_hard_breakers(
            [calm], run_evals.unbacked_payloads([calm], calm_targets),
            {"regime_shock": 1})
        self.assertIn("1 graded, 1 violation(s)", violating.detail)
        self.assertIn("breaker value not encoded", violating.detail)
        # The same bytes inline with no file behind them ⇒ E1 abstention.
        unproven = run_evals.unbacked_payloads([row], {})
        self.assertEqual(unproven,
                         frozenset({("GB-TEST-P3", "out_7_regime.json")}))
        abstained = run_evals.gate_hard_breakers([row], unproven,
                                                  {"regime_shock": 1})
        self.assertIn("regime_shock 0/1 graded, 1 abstained", abstained.coverage)

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
        """Every record whose class has a registered breaker is graded; a class
        without one is disclosed in coverage, never averaged in or dropped."""
        unbacked = run_evals.unbacked_payloads(self.trades, self.targets)
        gate = run_evals.gate_hard_breakers(
            self.trades, unbacked, self.raw["meta"]["class_counts_expected"])
        self.assertNotIn("abstained", gate.coverage, gate.coverage)
        out_of_scope = [t for t in self.trades
                        if t.get("trade_class") != "clean_bullish"
                        and t.get("trade_class") not in run_evals.BREAKER_BY_CLASS]
        self.assertEqual(gate.detail.split(";")[0],
                         f"{len(self.trades) - len(out_of_scope)} graded, "
                         "0 violation(s)")
        for cls in sorted({t["trade_class"] for t in out_of_scope}):
            self.assertIn(f"{cls} no registered breaker", gate.coverage)

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

    def test_the_scorecard_discloses_declared_debt_and_reports_the_verdict(self):
        """End to end: red only for an undeclared gap or a missed gate, never
        silently so."""
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
        # An *undeclared* gap turns the run red and says so; registered debt is
        # disclosed on the ledger and leaves the verdict alone. A graded gate
        # under its threshold is the other red path, and the verdict pages it.
        self.assertEqual(
            proc.returncode, 1 if "SUITE NOT PASSED" in out else 0, out)
        if excluded:
            self.assertIn("registered debt — no gate grades them as evidence", out)
            self.assertIn(f"{len(excluded)} registered exclusion(s)", out)
        if uncovered:
            self.assertIn("Evidence completeness: INCOMPLETE", out)
        else:
            self.assertIn("Evidence completeness: OK", out)
        graded = [line.strip() for line in out.splitlines()
                  if line.strip().startswith(("[PASS]", "[FAIL]"))]
        failed = [line for line in graded if line.startswith("[FAIL]")]
        self.assertTrue(graded, "the scorecard must grade at least one gate")
        # ...and the verdict is the gates' own arithmetic, not a second opinion.
        if uncovered or failed:
            self.assertIn("SUITE NOT PASSED", out)
        else:
            self.assertIn("SUITE PASSED", out)


if __name__ == "__main__":
    unittest.main()
