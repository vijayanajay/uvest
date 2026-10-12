"""Seam — the quarantine-retirement tool (`tests/evals/retire_quarantine_records.py`).

E5's meter may only shrink, and retiring a row is a write to four committed
artifacts at once (the manifest row, the dataset `$ref`, the register, the
baseline meter). Its safety properties are frozen here:

1. **The live register is at its floor and the retired row is recorded.** The
   register's single entry (GB-2023-0003's legacy stage-6 pledge payload) is
   gone, its hand-typed bytes are deleted rather than parked, the row declares
   `recorded` evidence, and the tool now reports nothing to do.
2. **The retirement's own evidence.** The row's recorded `out_6_fundamental.json`
   is the byte-exact stdout of a fresh offline stage-6 run over its committed
   bundle — the claim "stage 6 records real evidence" is checked, not asserted.
3. **The inheritance rule.** The retired level is the quarantined payload's own
   value, carried into the bundle verbatim and disclosed with where it came from.
4. **Refusals write nothing.** A registered payload with no usable hazard field
   stops the run with the register and the dataset untouched, so a retirement can
   never be manufactured by guessing a level.

Tests 3-4 run against a synthetic proof root: the live one is already retired, so
the tool's planning and refusal paths are exercised where they can still be
driven — the same way the other eval seams drive synthetic roots.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
PROOF_ROOT = TESTS_DIR / "proof"
GOLDEN = TESTS_DIR / "evals" / "golden_dataset.json"
TOOL = TESTS_DIR / "evals" / "retire_quarantine_records.py"
STAGE6 = REPO_ROOT / "scripts" / "6_fundamental_audit.py"

sys.path.insert(0, str(TESTS_DIR / "evals"))
sys.path.insert(0, str(TESTS_DIR))

import dataset_refs  # noqa: E402  (the frozen resolution rules)
import run_evals  # noqa: E402  (the runner's own class projections)
import scaffold_catalog as sc  # noqa: E402  (the proof-root bookkeeping it shares)
import retire_quarantine_records as retire  # noqa: E402  (the tool under test)


def run_tool(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL), *args],
                          capture_output=True, check=False, cwd=REPO_ROOT)


def canonical_trade(symbol: str, anchor: str) -> dict:
    """The minimum a stage-6 run reads off the canonical trade fixture."""
    return {"symbol": symbol, "exchange": "NSE", "instrument_type": "CASH",
            "action": "BUY", "entry_price": 316.7, "stop_loss": 303.0,
            "target_price": 342.0, "recommended_date": anchor}


class SyntheticRoot:
    """A one-row proof root whose single registered payload can be retired."""

    SID = "GB-TEST-1"

    def __init__(self, root: Path, *, payload: dict):
        self.root = root
        proof = root / "tests" / "proof"
        fixture = proof / "fixtures" / self.SID
        (fixture / "recorded").mkdir(parents=True)
        (fixture / "quarantined").mkdir(parents=True)
        sc.write_json(fixture / "current_trade.json",
                      canonical_trade("VEDL", "2023-02-13"))
        sc.write_json(fixture / "quarantined" / "out_6_fundamental.json", payload)
        sc.write_json(proof / sc.MANIFEST_FILE, {
            "schema": "uvest-scenario-manifest/1",
            "scenarios": [{
                "scenario_id": self.SID,
                "symbol": "VEDL",
                "exchange": "NSE",
                "recommendation_date": "2023-02-13",
                "recorded_artifacts": [{
                    "stage": 6, "artifact": "out_6_fundamental.json",
                    "path": f"fixtures/{self.SID}/quarantined/out_6_fundamental.json",
                    "evidence": "quarantined",
                    "reason": "legacy hand-typed payload; retire when stage 6 "
                              "records real evidence"}]}],
        })
        sc.write_text_utf8(proof / sc.HASH_FILE, "")
        sc.write_json(proof / sc.BASELINE_FILE, {
            "schema": "uvest-baseline-scorecard/1",
            "quarantined_count": 1,
            "quarantine_register_sha256": "0" * 64})
        sc.write_json(proof / sc.REGISTER_FILE, {
            "schema": "uvest-quarantine-register/1",
            "rows": [{"scenario_id": self.SID, "stage": 6,
                      "artifact": "out_6_fundamental.json"}]})
        sc.write_json(self.dataset_path, {
            "meta": {"notes": ["1 of 1 records."],
                     "class_counts_expected": {"pledge_hazard": 1}},
            "trades": [{
                "trade_id": self.SID, "trade_class": "pledge_hazard",
                "symbol": "VEDL", "instrument": "CASH",
                "recommendation_date": "2023-02-13",
                "entry_price": 316.7, "stop_loss": 303.0, "target_price": 342.0,
                "script_outputs": {
                    "out_6_fundamental.json": {
                        "$ref": f"tests/proof/fixtures/{self.SID}/quarantined/"
                                "out_6_fundamental.json"}},
            }],
        })

    @property
    def dataset_path(self) -> Path:
        return self.root / "tests" / "evals" / "golden_dataset.json"

    @property
    def proof(self) -> Path:
        return self.root / "tests" / "proof"

    def bind(self):
        """Bind the tool (and the bookkeeping module it borrows) to this root."""
        return (
            mock.patch.object(retire, "PROOF_ROOT", self.proof),
            mock.patch.object(retire, "GOLDEN", self.dataset_path),
            mock.patch.object(retire, "REGISTER", self.proof / sc.REGISTER_FILE),
            mock.patch.object(sc, "PROOF_ROOT", self.proof),
        )


class TestLiveRetirement(unittest.TestCase):
    """The committed state after the register's single row was retired."""

    def test_the_register_is_at_its_floor_and_the_row_is_recorded(self):
        register = json.loads((PROOF_ROOT / sc.REGISTER_FILE).read_text(encoding="utf-8"))
        self.assertEqual(register.get("schema"), "uvest-quarantine-register/1")
        self.assertEqual(register.get("rows"), [])
        baseline = json.loads((PROOF_ROOT / sc.BASELINE_FILE).read_text(encoding="utf-8"))
        self.assertEqual(baseline.get("quarantined_count"), 0)
        self.assertEqual(baseline.get("quarantine_register_sha256"),
                         hashlib.sha256(
                             (PROOF_ROOT / sc.REGISTER_FILE).read_bytes()).hexdigest())
        self.assertFalse(
            (PROOF_ROOT / "fixtures" / "GB-2023-0003" / "quarantined"
             / "out_6_fundamental.json").exists(),
            "the hand-typed payload is deleted, not parked")

    def test_the_retired_row_declares_recorded_bytes_and_discloses_the_input(self):
        manifest = json.loads((PROOF_ROOT / sc.MANIFEST_FILE).read_text(encoding="utf-8"))
        row = next(r for r in manifest["scenarios"] if r["scenario_id"] == "GB-2023-0003")
        declared = next(a for a in row["recorded_artifacts"]
                        if a["artifact"] == "out_6_fundamental.json")
        self.assertEqual(declared["evidence"], "recorded")
        self.assertNotIn("reason", declared, "a recorded artifact carries no reason")
        self.assertEqual(row["replay_inputs"]["financials_fixture"],
                         "fixtures/GB-2023-0003/financials.json")
        self.assertIn("promoter_pledging_gt_25pct", row["gate_focus"])
        evidence = row["authoring"]["hazard_evidence"]
        self.assertEqual(evidence["hazard_value"], 54.2)
        self.assertIn("quarantined/out_6_fundamental.json", evidence["inherited_from"])
        self.assertIn("inherited verbatim", evidence["disclosure"])
        retirement = row["authoring"]["quarantine_retirement"]
        self.assertEqual(retirement["previous_evidence"], "quarantined")
        self.assertEqual(retirement["tool"], retire.TOOL_SOURCE)

    def test_the_recorded_bytes_are_a_fresh_stage6_run_over_the_bundle(self):
        """The retirement's evidence, re-derived: the file *is* the engine's stdout."""
        fixture = PROOF_ROOT / "fixtures" / "GB-2023-0003"
        proc = subprocess.run(
            [sys.executable, str(STAGE6),
             "--input", str(fixture / "current_trade.json"),
             "--financials-fixture", str(fixture / sc.FINANCIALS_FILE)],
            capture_output=True, check=False, cwd=REPO_ROOT)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # The recorder writes the stage's in-memory stdout through a text-mode
        # file, so the comparison is on the logical bytes (the platform's own
        # newline convention is what differs, nothing else).
        recorded = (fixture / "recorded" / "out_6_fundamental.json").read_bytes()
        self.assertEqual(recorded.replace(b"\r\n", b"\n"),
                         proc.stdout.replace(b"\r\n", b"\n"),
                         "the recorded artifact must be byte-exact stage-6 stdout")
        payload = json.loads(recorded.decode("utf-8"))
        self.assertFalse(payload["waived"])
        self.assertEqual(payload["promoter_pledge_pct"], 54.2)
        self.assertTrue(payload["pledge_circuit_breaker"])
        self.assertTrue(payload["missing_inputs"],
                        "empty statements must name their gaps")

    def test_the_record_grades_five_of_five_after_retirement(self):
        dataset = json.loads(GOLDEN.read_text(encoding="utf-8"))
        materialized, _refs, _derives = dataset_refs.materialize(dataset)
        trades = materialized["trades"]
        unbacked = run_evals.unbacked_payloads(
            trades, run_evals.dataset_payload_targets(dataset))
        pledged = [t for t in trades if t["trade_class"] == "pledge_hazard"]
        self.assertEqual(len(pledged), 5)
        for trade in pledged:
            with self.subTest(trade=trade["trade_id"]):
                self.assertIs(run_evals.class_breaker_observed(trade, unbacked=unbacked),
                              True, f"{trade['trade_id']}: pledge breaker must grade")
        gate = run_evals.gate_hard_breakers(trades, unbacked)
        self.assertNotIn("not graded", gate.detail, gate.detail)
        self.assertEqual(gate.value, 100.0, gate.detail)

    def test_the_tool_now_reports_nothing_to_do(self):
        proc = run_tool()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = proc.stdout.decode("utf-8")
        self.assertIn("nothing registered", out)
        self.assertIn("nothing to do", out)


class TestRetirementRules(unittest.TestCase):
    """The inheritance rule and the refusals, on a synthetic proof root."""

    def test_the_inherited_level_is_the_quarantined_payloads_own_value(self):
        """No re-derivation and no guessing: the payload's own level is the input."""
        with tempfile.TemporaryDirectory() as tmp:
            root = SyntheticRoot(Path(tmp),
                                 payload={"promoter_pledge_pct": 54.2,
                                          "days_to_earnings": 40})
            with mock.patch.object(retire, "PROOF_ROOT", root.proof), \
                 mock.patch.object(retire, "GOLDEN", root.dataset_path), \
                 mock.patch.object(retire, "REGISTER", root.proof / sc.REGISTER_FILE):
                manifest = json.loads((root.proof / sc.MANIFEST_FILE)
                                      .read_text(encoding="utf-8"))
                dataset = json.loads(root.dataset_path.read_text(encoding="utf-8"))
                rows = json.loads((root.proof / sc.REGISTER_FILE)
                                  .read_text(encoding="utf-8"))["rows"]
                plans = retire.plan(rows, manifest, dataset)
        self.assertEqual(len(plans), 1)
        entry = plans[0]
        self.assertEqual(entry["value"], 54.2)
        self.assertEqual(entry["spec"][0], "governance")
        self.assertEqual(entry["spec"][1], "promoter_pledged_pct")
        self.assertEqual(entry["previous"],
                         f"fixtures/{SyntheticRoot.SID}/quarantined/"
                         "out_6_fundamental.json")

    def test_a_payload_with_no_usable_level_refuses_and_writes_nothing(self):
        """A level nobody can inherit stops the retirement instead of inventing one."""
        with tempfile.TemporaryDirectory() as tmp:
            root = SyntheticRoot(Path(tmp), payload={"days_to_earnings": 40})
            before = {path: path.read_bytes()
                      for path in sorted(root.proof.rglob("*")) if path.is_file()}
            dataset_before = root.dataset_path.read_bytes()
            with mock.patch.object(retire, "PROOF_ROOT", root.proof), \
                 mock.patch.object(retire, "GOLDEN", root.dataset_path), \
                 mock.patch.object(retire, "REGISTER", root.proof / sc.REGISTER_FILE), \
                 mock.patch.object(sc, "PROOF_ROOT", root.proof):
                manifest = json.loads((root.proof / sc.MANIFEST_FILE)
                                      .read_text(encoding="utf-8"))
                dataset = json.loads(root.dataset_path.read_text(encoding="utf-8"))
                rows = json.loads((root.proof / sc.REGISTER_FILE)
                                  .read_text(encoding="utf-8"))["rows"]
                with self.assertRaises(retire.Blocked) as ctx:
                    retire.plan(rows, manifest, dataset)
            self.assertIn("nothing to inherit", str(ctx.exception))
            after = {path: path.read_bytes()
                     for path in sorted(root.proof.rglob("*")) if path.is_file()}
            self.assertEqual(before, after, "a refusal must not touch the proof root")
            self.assertEqual(root.dataset_path.read_bytes(), dataset_before)

    def test_apply_flips_the_declaration_and_banks_the_meter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = SyntheticRoot(Path(tmp), payload={"promoter_pledge_pct": 54.2})
            proof = root.proof
            with mock.patch.object(retire, "PROOF_ROOT", proof), \
                 mock.patch.object(retire, "GOLDEN", root.dataset_path), \
                 mock.patch.object(retire, "REGISTER", proof / sc.REGISTER_FILE), \
                 mock.patch.object(sc, "PROOF_ROOT", proof):
                manifest = json.loads((proof / sc.MANIFEST_FILE).read_text(encoding="utf-8"))
                dataset = json.loads(root.dataset_path.read_text(encoding="utf-8"))
                rows = json.loads((proof / sc.REGISTER_FILE).read_text(encoding="utf-8"))["rows"]
                plans = retire.plan(rows, manifest, dataset)
                self.assertEqual(retire.apply(plans, manifest, dataset,
                                              {"schema": "uvest-quarantine-register/1",
                                               "rows": rows},
                                              date.today().isoformat()), 0)

            register = json.loads((proof / sc.REGISTER_FILE).read_text(encoding="utf-8"))
            self.assertEqual(register["rows"], [])
            self.assertFalse((proof / "fixtures" / root.SID / "quarantined").exists())
            written = json.loads((proof / sc.MANIFEST_FILE).read_text(encoding="utf-8"))
            row = written["scenarios"][0]
            declared = row["recorded_artifacts"][0]
            self.assertEqual(declared["evidence"], "recorded")
            self.assertEqual(declared["path"],
                             f"fixtures/{root.SID}/recorded/out_6_fundamental.json")
            self.assertIn("promoter_pledging_gt_25pct", row["gate_focus"])
            baseline = json.loads((proof / sc.BASELINE_FILE).read_text(encoding="utf-8"))
            self.assertEqual(baseline["quarantined_count"], 0,
                             "the meter must be banked, not left to warn forever")
            self.assertEqual(baseline["quarantine_register_sha256"],
                             hashlib.sha256((proof / sc.REGISTER_FILE).read_bytes())
                             .hexdigest())
            hashes = (proof / sc.HASH_FILE).read_text(encoding="utf-8")
            self.assertIn(f"fixtures/{root.SID}/recorded/out_6_fundamental.json", hashes)
            updated = json.loads(root.dataset_path.read_text(encoding="utf-8"))
            ref = updated["trades"][0]["script_outputs"]["out_6_fundamental.json"]["$ref"]
            self.assertIn("recorded/out_6_fundamental.json", ref)
            self.assertIn("retired from the quarantine register",
                          updated["meta"]["notes"][-1])
            # The retired bytes are gradeable evidence now, not decoration.
            payload = json.loads((proof / "fixtures" / root.SID / "recorded"
                                  / "out_6_fundamental.json").read_text(encoding="utf-8"))
            probe = {"trade_id": root.SID, "trade_class": "pledge_hazard",
                     "script_outputs": {"out_6_fundamental.json": payload}}
            self.assertIs(run_evals.class_breaker_observed(probe), True)


if __name__ == "__main__":
    unittest.main()
