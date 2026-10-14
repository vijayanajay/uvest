"""Seam — the regime-class seed (`tests/evals/seed_regime_records.py`).

`regime_shock` is the class whose breaker only stage 7 can produce (BRD §10.3
#6: VIX > 24 **and** breadth < 30), so it was the last unseeded class of
`meta.class_counts_expected`. This seam freezes how its roster was closed,
because every part of it is a place a curator could quietly fake a green board:

1. **Every market-context input is authored and disclosed, never dressed as a
   source.** Each row's `fixtures/<id>/regime_context.json` must equal what the
   seeder's authoring rule produces from the frozen catalog — a hand-edited
   bundle (a convenient VIX, an invented breadth) fails here. A value the
   disclosure calls *documented* must actually appear in that catalog row's
   trap (asserted by the seeder's `trap_claims` and re-asserted here); a value
   it calls *authored* sits inside the breaker's arm with `origin: authored`,
   so a reader never mistakes it for a Yahoo snapshot (E4 discipline).
2. **The graded bytes are a real stage stdout.** Every regime record's
   `out_7_regime.json` must be a `$ref` to a recorded artifact whose manifest
   row declares it `recorded`, whose `gate_focus` carries `panic_regime` (V11
   pairs the two), and whose pending stage-7 intent is gone — the breaker is
   graded from evidence, not from a promise. V10 byte-compares each artifact
   against a fresh offline run through `replay_inputs.regime_fixture`.
3. **Gate 2 actually grades the class.** The recorded bytes must encode BOTH
   arms of the conjunction at the roster's own values, and gate 2 — given the
   same proof the runner gives it — must grade every record rather than
   abstaining, with the class at its frozen quota and a census equal to the
   distinct `(arm, value)` pairs the roster stands for.
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
import seed_regime_records as seed  # noqa: E402

# The seeded class; scenario ids and per-entry authoring live with the seeder.
TRADE_CLASS = "regime_shock"
BREAKER_ARTIFACT = "out_7_regime.json"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class TestSeedRegimeRecords(unittest.TestCase):
    """The seeded regime roster is evidence, not decoration."""

    @classmethod
    def setUpClass(cls):
        cls.raw = read_json(DATASET_FILE)
        cls.dataset, _refs, _derives = dr.materialize(cls.raw)
        cls.by_id = {t["trade_id"]: t for t in cls.dataset["trades"]}
        rows = read_json(PROOF_ROOT / "scenario_manifest.json")["scenarios"]
        cls.rows = {row["scenario_id"]: row for row in rows}
        cls.roster = [e["scenario_id"] for e in seed.ROSTER]
        cls.catalog = seed.sc.parse_catalog()

    # ----------------------------------------------------------------- fixtures
    def test_every_seeded_fixture_is_reproducible_from_the_authoring_rule(self):
        """The committed bundle is exactly the tool's rule — not hand-edited."""
        for entry in seed.ROSTER:
            with self.subTest(scenario=entry["scenario_id"]):
                path = (PROOF_ROOT / "fixtures" / entry["scenario_id"]
                        / seed.REGIME_FILE)
                self.assertTrue(path.exists(), f"missing bundle: {path}")
                self.assertEqual(read_json(path), seed.fixture_doc(entry),
                                 "the committed bundle is not what the "
                                 "authoring rule produces — hand-edited?")

    def test_every_bundle_discloses_authored_and_matches_the_catalog_trap(self):
        """No invented source: the catalog's own text, claims asserted in it."""
        for entry in seed.ROSTER:
            sid = entry["scenario_id"]
            with self.subTest(scenario=sid):
                spec = self.catalog[sid]
                # Documented claims must exist in the frozen catalog text.
                for claim in entry["trap_claims"]:
                    self.assertIn(claim, spec["trap"],
                                  "a documented value no longer stated by the trap")
                fixture = read_json(PROOF_ROOT / "fixtures" / sid
                                    / seed.REGIME_FILE)
                self.assertEqual(fixture["schema"], "uvest-market-context/1")
                self.assertEqual(fixture["provenance"]["origin"], "authored")
                authored = fixture["authoring"]
                self.assertEqual(authored["breaker"], "panic_regime")
                self.assertEqual(authored["trap"], spec["trap"])
                self.assertEqual(authored["vix_value"], entry["vix"])
                self.assertEqual(authored["market_breadth_pct"], entry["breadth"])
                self.assertIn(spec["trap"], authored["disclosure"])
                series = fixture["series"]
                for key in ("^NSEI", authored["sector_index"], "^INDIAVIX",
                            "NSE500_BREADTH", "FII_DII_NET"):
                    with self.subTest(series=key):
                        self.assertIn(key, series,
                                      "every series the stage reads must exist")
                        self.assertTrue(series[key]["values"], f"{key}: empty")
                # Every date-keyed point sits at or before T_rec (V7's
                # look-ahead rule: this bundle IS scanned bar-by-bar).
                cutoff = self.rows[sid]["replay_inputs"]["truncation"]["cutoff"]
                late = [p["date"] for key in series for p in series[key]["values"]
                        if p["date"] > cutoff]
                self.assertEqual(late, [],
                                 "regime context must carry no post-T_rec point")
                # Aligned index axis (NSEI / sector / VIX), as the stage requires.
                dates = [p["date"] for p in series["^NSEI"]["values"]]
                self.assertEqual(len(dates), len(set(dates)))
                for key in (authored["sector_index"], "^INDIAVIX"):
                    self.assertEqual([p["date"] for p in series[key]["values"]],
                                     dates, f"{key}: axis off the NIFTY axis")
                # The graded tails sit exactly at the roster's values, and
                # inside the conjunction.
                self.assertEqual(series["^INDIAVIX"]["values"][-1]["value"],
                                 entry["vix"])
                self.assertEqual(series["NSE500_BREADTH"]["values"][-1]["value"],
                                 entry["breadth"])
                self.assertGreater(entry["vix"], 24.0)
                self.assertLess(entry["breadth"], 30.0)

    # -------------------------------------------------------------------- rows
    def test_each_row_declares_recorded_stage_7_evidence(self):
        """Declaration + gate_focus move together (V11); pending intent is gone."""
        for sid in self.roster:
            with self.subTest(scenario=sid):
                row = self.rows[sid]
                declared = [a for a in row["recorded_artifacts"]
                            if a.get("artifact") == BREAKER_ARTIFACT]
                self.assertEqual(len(declared), 1,
                                 "exactly one stage-7 declaration")
                self.assertEqual(declared[0]["evidence"], "recorded")
                self.assertEqual(declared[0]["stage"], 7)
                self.assertTrue((PROOF_ROOT / declared[0]["path"]).exists(),
                                "declared stage-7 artifact missing on disk")
                self.assertIn("panic_regime", row["gate_focus"])
                self.assertNotIn("breaker_pending_stage_7", row["authoring"],
                                 "graded evidence replaces the pending promise")
                self.assertEqual(row["replay_inputs"]["regime_fixture"],
                                 f"fixtures/{sid}/{seed.REGIME_FILE}")
                self.assertTrue(row["authoring"]["regime_evidence"]["disclosure"])

    def test_each_golden_record_refs_derives_and_nulls(self):
        """A view of the row: `$ref`s for bytes, `$derive`s for tape claims."""
        for sid in self.roster:
            with self.subTest(scenario=sid):
                self.assertIn(sid, self.by_id, "the regime record must be seeded")
                raw_record = next(t for t in self.raw["trades"]
                                  if t["trade_id"] == sid)
                self.assertEqual(raw_record["trade_class"], TRADE_CLASS)
                for artifact in ("out_1_math.json", "out_2_market.json",
                                 BREAKER_ARTIFACT):
                    with self.subTest(artifact=artifact):
                        self.assertTrue(dr.is_ref(
                            raw_record["script_outputs"][artifact]),
                            f"{artifact} must be a $ref, never inline bytes (E1)")
                        target = next(r[2] for r in dr.refs(self.raw)
                                      if r[0] == sid and r[1] == artifact)
                        self.assertTrue((REPO_ROOT / target).exists(),
                                        f"{sid}: missing {target}")
                for field in ("forward_prices_22d", "resolution_date",
                              "resolution_outcome", "market_mae_pct",
                              "actual_direction"):
                    self.assertTrue(dr.is_derive(raw_record[field]),
                                    f"{sid}.{field} must derive from the tape")
                self.assertIsNone(raw_record["cone_p10"])
                self.assertIsNone(raw_record["predicted_direction"])
                self.assertIsNone(raw_record["limit_mae_pct"])

    # ----------------------------------------------------------------- grading
    def test_gate_2_grades_every_regime_record_from_recorded_bytes(self):
        """The point of the seed: both arms at roster values, no abstention."""
        targets = run_evals.dataset_payload_targets(self.raw)
        unbacked = run_evals.unbacked_payloads(self.dataset["trades"], targets)
        for entry in seed.ROSTER:
            sid = entry["scenario_id"]
            with self.subTest(scenario=sid):
                record = self.by_id.get(sid)
                self.assertIsNotNone(record, "the regime record must be seeded")
                observed = run_evals.class_breaker_observed(record,
                                                            unbacked=unbacked)
                self.assertIs(observed, True,
                              "the panic breaker must grade true on recorded bytes")
                payload = record["script_outputs"][BREAKER_ARTIFACT]
                self.assertFalse(payload["waived"])
                self.assertEqual(payload["vix_value"], entry["vix"])
                self.assertEqual(payload["market_breadth_pct"], entry["breadth"])
                self.assertTrue(payload["panic_regime_breaker"])
        gate = run_evals.gate_hard_breakers(self.dataset["trades"], unbacked,
                                            self.raw["meta"]
                                            ["class_counts_expected"])
        self.assertFalse(gate.skipped, gate.detail)
        self.assertEqual(gate.value, 100.0, gate.detail)
        for sid in self.roster:
            self.assertNotIn(sid, gate.detail, gate.detail)
        self.assertNotIn("not graded", gate.detail, gate.detail)
        self.assertIn(f"{TRADE_CLASS} {len(self.roster)}/{len(self.roster)} "
                      "graded", gate.coverage)
        # The census is exactly the roster's distinct (arm, value) pairs: each
        # documented VIX is its own exercise, the authored arm collapses, and
        # breadth contributes its two values.
        expected = ({("panic_vix_above_24", e["vix"]) for e in seed.ROSTER}
                    | {("panic_breadth_below_30", e["breadth"])
                       for e in seed.ROSTER})
        self.assertEqual(gate.census.get(TRADE_CLASS), len(expected))
        self.assertEqual(run_evals.graded_trade_classes(
            self.dataset["trades"], unbacked).get(TRADE_CLASS), expected)

    def test_the_class_meets_its_frozen_quota_and_the_roster_stays_complete(self):
        counts: dict[str, int] = {}
        for trade in self.dataset["trades"]:
            counts[trade["trade_class"]] = counts.get(trade["trade_class"], 0) + 1
        meta = self.raw["meta"]
        self.assertEqual(counts.get(TRADE_CLASS), len(seed.ROSTER))
        self.assertEqual(meta["class_counts_expected"][TRADE_CLASS],
                         len(seed.ROSTER))
        self.assertEqual(len(self.dataset["trades"]),
                         meta["total_trades_expected"],
                         "distribution_verified requires a complete roster")
        self.assertTrue(meta.get("distribution_verified"))
        ok, reason = run_evals.check_class_distribution(self.raw)
        self.assertTrue(ok, reason)


if __name__ == "__main__":
    unittest.main()
