"""Seam — the hazard-class seed (`tests/evals/seed_hazard_records.py`).

The pledge/earnings slots are the two classes whose breaker only stage 6 can
produce, so they were the two unseeded classes of `meta.class_counts_expected`.
This seam freezes how they were closed, because every part of it is a place a
curator could quietly fake a green board:

1. **The hazard input is authored and disclosed, never dressed as a filing.**
   Each row's `fixtures/<id>/financials.json` must equal what the seeder's
   authoring rule produces from the frozen catalog — a hand-edited bundle (a
   convenient pledge value, an invented balance sheet) fails here. The statement
   blocks must stay empty: absence is what makes stage 6 report null ratio
   metrics with `missing_inputs` naming them, and an invented FY statement would
   be a fabricated filing (E4).
2. **The graded bytes are a real stage stdout.** Every hazard record's
   `out_6_fundamental.json` must be a `$ref` to a recorded artifact whose row
   declares it `recorded`, `gate_focus` must carry the class's breaker id (V11
   pairs the two), and the pending intent must be gone — the class breaker is
   graded from evidence, not from a promise.
3. **Gate 2 actually grades both classes.** The recorded bytes must carry the
   threshold-level value, and gate 2 — given the same proof the runner gives it —
   must grade every seeded hazard record rather than abstaining on it. The one
   remaining abstention is `GB-2023-0003`'s registered quarantine, which this
   change deliberately leaves alone (E5's meter may only shrink on command).
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
PROOF_ROOT = TESTS_DIR / "proof"
DATASET_FILE = TESTS_DIR / "evals" / "golden_dataset.json"
sys.path.insert(0, str(TESTS_DIR / "evals"))
sys.path.insert(0, str(TESTS_DIR))

import dataset_refs as dr  # noqa: E402  (sibling eval modules, spliced above)
import run_evals  # noqa: E402
import seed_hazard_records as seed  # noqa: E402

HAZARD_CLASSES = ("pledge_hazard", "earnings_hazard")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class TestSeedHazardRecords(unittest.TestCase):
    """The seeded hazard classes are evidence, not decoration."""

    @classmethod
    def setUpClass(cls):
        cls.raw = read_json(DATASET_FILE)
        cls.dataset, _refs, _derives = dr.materialize(cls.raw)
        cls.by_id = {t["trade_id"]: t for t in cls.dataset["trades"]}
        rows = read_json(PROOF_ROOT / "scenario_manifest.json")["scenarios"]
        cls.rows = {row["scenario_id"]: row for row in rows}
        cls.roster = [e["scenario_id"] for e in seed.ROSTER]

    # ----------------------------------------------------------------- fixtures
    def test_every_seeded_fixture_is_reproducible_from_the_frozen_catalog(self):
        """The committed bundle is exactly the tool's rule over the catalog row."""
        catalog = seed.sc.parse_catalog()
        for entry in seed.ROSTER:
            with self.subTest(scenario=entry["scenario_id"]):
                path = (PROOF_ROOT / "fixtures" / entry["scenario_id"]
                        / seed.sc.FINANCIALS_FILE)
                self.assertTrue(path.exists(), f"missing hazard bundle: {path}")
                self.assertEqual(read_json(path), seed.fixture_doc(
                    entry, catalog[entry["scenario_id"]]),
                    "the committed bundle is not what the authoring rule produces "
                    "from the catalog — it may have been hand-edited")

    def test_the_statement_blocks_stay_empty_and_the_gaps_are_named(self):
        """No invented balance sheet: null ratios that name their missing inputs."""
        for entry in seed.ROSTER:
            with self.subTest(scenario=entry["scenario_id"]):
                fixture = read_json(PROOF_ROOT / "fixtures" / entry["scenario_id"]
                                    / seed.sc.FINANCIALS_FILE)
                self.assertEqual(fixture["statement_fy_current"], {},
                                 "statement inputs must stay absent (E4)")
                self.assertEqual(fixture["statement_fy_prior"], {})
                self.assertEqual(fixture["authoring"]["hazard_field"], entry["field"])
                self.assertEqual(fixture["authoring"]["hazard_value"], entry["value"])
                self.assertEqual(fixture["authoring"]["breaker"],
                                 seed.CLASS_BREAKER[entry["trade_class"]])
                recorded = read_json(
                    PROOF_ROOT / "fixtures" / entry["scenario_id"] / "recorded"
                    / "out_6_fundamental.json")
                self.assertFalse(recorded["waived"])
                self.assertTrue(recorded["missing_inputs"],
                                "a null metric must name its obstacle")
                self.assertIsNone(recorded["piotoski_score"])

    # ----------------------------------------------------------------- rows
    def test_each_hazard_row_declares_recorded_stage_6_evidence(self):
        """Declaration + `gate_focus` + no pending intent: the breaker is gradable."""
        for entry in seed.ROSTER:
            sid = entry["scenario_id"]
            with self.subTest(scenario=sid):
                row = self.rows.get(sid)
                self.assertIsNotNone(row, f"{sid}: no proof row")
                declared = [a for a in row["recorded_artifacts"]
                            if a["artifact"] == "out_6_fundamental.json"]
                self.assertEqual(len(declared), 1)
                self.assertEqual(declared[0]["evidence"], "recorded")
                self.assertEqual(declared[0]["path"],
                                 f"fixtures/{sid}/financials.json".replace(
                                     "financials.json", "recorded/out_6_fundamental.json"))
                self.assertEqual(row["replay_inputs"]["financials_fixture"],
                                 f"fixtures/{sid}/{seed.sc.FINANCIALS_FILE}")
                self.assertIn(seed.CLASS_BREAKER[entry["trade_class"]],
                              row["gate_focus"])
                self.assertNotIn("breaker_pending_stage_6", row.get("authoring", {}))
                evidence = row["authoring"]["hazard_evidence"]
                self.assertEqual(evidence["breaker"],
                                 seed.CLASS_BREAKER[entry["trade_class"]])
                self.assertIn("Authored hazard input", evidence["disclosure"])

    # ----------------------------------------------------------------- records
    def test_every_hazard_record_references_recorded_bytes_and_tapes(self):
        """One copy, one provenance: refs into the bundle, derives from the tape."""
        for sid in self.roster:
            with self.subTest(scenario=sid):
                record = self.by_id.get(sid)
                self.assertIsNotNone(record, f"{sid}: no golden record")
                self.assertIn(record["trade_class"], HAZARD_CLASSES)
                raw_record = next(t for t in self.raw["trades"]
                                  if t["trade_id"] == sid)
                for artifact in ("out_1_math.json", "out_2_market.json",
                                 "out_6_fundamental.json"):
                    self.assertTrue(
                        dr.is_ref(raw_record["script_outputs"][artifact]),
                        f"{sid}: {artifact} must be a $ref, never inline bytes")
                    target = REPO_ROOT / dr.ref_path(
                        raw_record["script_outputs"][artifact])
                    self.assertTrue(target.exists(), f"{sid}: missing {target}")
                for field in ("forward_prices_22d", "resolution_date",
                              "resolution_outcome", "market_mae_pct",
                              "actual_direction"):
                    self.assertTrue(dr.is_derive(raw_record[field]),
                                    f"{sid}.{field} must derive from the pinned tape")
                self.assertEqual(record["cone_p10"], None)
                self.assertEqual(record["predicted_direction"], None)

    # ----------------------------------------------------------------- grading
    def test_gate_2_grades_both_hazard_classes_from_recorded_bytes(self):
        """The point of the seed: no abstention on a seeded hazard record."""
        targets = run_evals.dataset_payload_targets(self.raw)
        unbacked = run_evals.unbacked_payloads(self.dataset["trades"], targets)
        for sid in self.roster:
            record = self.by_id[sid]
            with self.subTest(scenario=sid):
                observed = run_evals.class_breaker_observed(record, unbacked=unbacked)
                self.assertIs(observed, True,
                              f"{sid}: the class breaker must grade true on "
                              "recorded bytes")
                payload = record["script_outputs"]["out_6_fundamental.json"]
                if record["trade_class"] == "pledge_hazard":
                    self.assertGreater(payload["promoter_pledge_pct"], 25.0)
                    self.assertTrue(payload["pledge_circuit_breaker"])
                else:
                    self.assertEqual(record["instrument"], "FUT",
                                     "the earnings hazard is a Futures rule")
                    self.assertLess(payload["days_to_earnings"], 7)
                    self.assertTrue(payload["earnings_hazard_flag"])
        gate = run_evals.gate_hard_breakers(self.dataset["trades"], unbacked)
        self.assertFalse(gate.skipped, gate.detail)
        self.assertEqual(gate.value, 100.0, gate.detail)
        # Every seeded hazard record is graded, and the only abstention is the
        # registered quarantine this change deliberately leaves in place.
        for sid in self.roster:
            self.assertNotIn(sid, gate.detail, gate.detail)
        self.assertIn("GB-2023-0003: out_6_fundamental.json quarantined", gate.detail)

    def test_the_classes_meet_their_frozen_quotas(self):
        counts: dict[str, int] = {}
        for trade in self.dataset["trades"]:
            counts[trade["trade_class"]] = counts.get(trade["trade_class"], 0) + 1
        for cls_name in HAZARD_CLASSES:
            quota = self.dataset["meta"]["class_counts_expected"][cls_name]
            self.assertEqual(counts.get(cls_name, 0), quota,
                             f"{cls_name}: {counts.get(cls_name, 0)}/{quota}")


if __name__ == "__main__":
    unittest.main()
