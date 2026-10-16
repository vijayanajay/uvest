"""Seam — the distribution seed (`tests/evals/seed_distribution_records.py`).

`institutional_distribution` was the last unseeded class of
`meta.class_counts_expected` (10 of 10 unauthored) precisely because its
signature does not live in any stage 0–2 artifact: *price rising on falling
Futures OI and low Delivery % (<25%), resulting in subsequent sharp sell-offs*
(docs/agents/evals.md §2). This seam freezes how the class was closed, because
every part of it is a place a curator could quietly fake a green board:

1. **The signature is recomputable from committed data.** Each row's
   `fixtures/<id>/bhavcopy.json` must be internally consistent: the 20-session
   delivery window must reproduce the mean/stdev the z-score is computed from,
   the equity block must reproduce T_rec's own delivery % and price change,
   and the derivatives block must reproduce the front-month OI change. A
   hand-edited fixture (a convenient delivery number, an invented OI drop)
   fails here — as does a fixture whose `sha256`-named sources disagree with
   its own numbers.
2. **The graded bytes are a real stage stdout carrying the invariant.** Every
   distribution record's `out_5_flow.json` must be a `$ref` to a recorded
   artifact whose row declares it `recorded` and pins the bundle as its
   `replay_inputs.bhavcopy_fixture`, and the bytes must carry the class
   invariant: `SHORT_COVERING_TRAP` matrix, delivery < 25%, OI <= -5%, the
   same numbers the fixture measured (V10 additionally byte-compares these
   bytes to a fresh offline stage-5 run).
3. **The tape, not the tool, owns the outcome.** Each record's forward tape
   must resolve `LOSS` first under the frozen order-aware rule, matching the
   row's stored scoring block — the class is seeded from realized sell-offs
   (AVOID expected, LOSS realized, TN quadrant), never from a tuned geometry.
4. **The roster is complete and honestly disclosed.** 10/10 against the frozen
   quota with `distribution_verified=true`, and gate 2's coverage line names
   the class as `no registered breaker (10 record(s), quota 10)` — the class
   is disclosed, not averaged into breaker accuracy, and it contributes no
   rule exercises (the frozen floor may not be inflated by records that grade
   no rule).
"""

from __future__ import annotations

import json
import math
import statistics
import sys
import unittest
from datetime import date
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
PROOF_ROOT = TESTS_DIR / "proof"
DATASET_FILE = TESTS_DIR / "evals" / "golden_dataset.json"
sys.path.insert(0, str(TESTS_DIR / "evals"))
sys.path.insert(0, str(TESTS_DIR))

import dataset_refs as dr  # noqa: E402  (sibling eval modules, spliced above)
import run_evals  # noqa: E402
import seed_distribution_records as seed  # noqa: E402


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class TestSeedDistributionRecords(unittest.TestCase):
    """The seeded distribution class is evidence, not decoration."""

    @classmethod
    def setUpClass(cls):
        cls.raw = read_json(DATASET_FILE)
        cls.dataset, _refs, _derives = dr.materialize(cls.raw)
        cls.trades = [t for t in cls.dataset["trades"]
                      if t["trade_class"] == seed.CLASS]
        cls.raw_by_id = {t["trade_id"]: t for t in cls.raw["trades"]}
        cls.by_id = {t["trade_id"]: t for t in cls.trades}
        rows = read_json(PROOF_ROOT / "scenario_manifest.json")["scenarios"]
        cls.rows = {row["scenario_id"]: row for row in rows
                    if row.get("scenario_id") in cls.by_id}

    # ----------------------------------------------------------------- roster
    def test_the_class_meets_its_frozen_quota_and_is_verified(self):
        quota = self.dataset["meta"]["class_counts_expected"][seed.CLASS]
        self.assertEqual(len(self.trades), quota)
        self.assertEqual(len(self.rows), quota, "every record needs a proof row")
        ok, reason = run_evals.check_class_distribution(self.raw)
        self.assertTrue(ok, reason)
        self.assertIn("distribution verified", reason)
        self.assertIs(self.raw["meta"]["distribution_verified"], True)
        # Identities are unique per report name: one report per (symbol, date).
        pairs = [(t["symbol"].upper(), t["recommendation_date"])
                 for t in self.trades]
        self.assertEqual(len(set(pairs)), len(pairs))

    # --------------------------------------------------------------- fixtures
    def test_every_fixture_is_self_consistent_with_its_own_disclosed_rows(self):
        """Every fixture number recomputes from the rows committed beside it."""
        for sid, row in self.rows.items():
            with self.subTest(scenario=sid):
                path = PROOF_ROOT / row["replay_inputs"]["bhavcopy_fixture"]
                self.assertTrue(path.exists(), f"{sid}: missing {path}")
                fixture = read_json(path)
                self.assertEqual(fixture["schema"], seed.FIXTURE_SCHEMA)
                self.assertEqual(fixture["symbol"], row["symbol"])
                self.assertEqual(fixture["as_of"], row["recommendation_date"])
                authoring = fixture["authoring"]
                self.assertEqual(authoring["source"], seed.SEED_SOURCE)
                self.assertEqual(authoring["trade_class"], seed.CLASS)

                # The 20-session window reproduces the z-score's mean/stdev.
                history = authoring["delivery_history"]
                self.assertEqual(len(history), seed.DELIVERY_WINDOW)
                sessions = [h["session"] for h in history]
                self.assertEqual(sessions, sorted(sessions))
                self.assertTrue(all(s < fixture["as_of"] for s in sessions),
                                f"{sid}: the window must sit strictly before T_rec")
                self.assertEqual(authoring["delivery_window"]["first"], sessions[0])
                self.assertEqual(authoring["delivery_window"]["last"], sessions[-1])
                values = [h["delivery_pct"] for h in history]
                self.assertTrue(math.isclose(
                    statistics.fmean(values), fixture["delivery_mean_20d"],
                    abs_tol=1e-4), f"{sid}: mean does not reproduce")
                self.assertTrue(math.isclose(
                    statistics.pstdev(values), fixture["delivery_std_20d"],
                    abs_tol=1e-4), f"{sid}: stdev does not reproduce")

                # The equity rows reproduce T_rec's delivery % and price move.
                equity = authoring["equity_file"]
                self.assertEqual(equity["date1"],
                                 date.fromisoformat(fixture["as_of"]).strftime("%d-%b-%Y"))
                for key in ("file", "url", "sha256"):
                    self.assertTrue(equity.get(key), f"{sid}: equity source {key} missing")
                self.assertRegex(equity["sha256"], r"^[0-9a-f]{64}$")
                self.assertTrue(math.isclose(
                    equity["delivery_qty"] / equity["traded_qty"] * 100.0,
                    fixture["delivery_pct"], abs_tol=0.01),
                    f"{sid}: delivery_pct does not reproduce from the file rows")
                self.assertTrue(math.isclose(
                    (equity["close"] - equity["prev_close"]) / equity["prev_close"] * 100.0,
                    fixture["price_change_pct"], abs_tol=1e-3),
                    f"{sid}: price_change_pct does not reproduce")

                # The derivatives rows reproduce the front-month OI change.
                derivatives = authoring["derivatives_file"]
                for key in ("file", "url", "sha256", "format"):
                    self.assertTrue(derivatives.get(key),
                                    f"{sid}: derivatives source {key} missing")
                self.assertRegex(derivatives["sha256"], r"^[0-9a-f]{64}$")
                contract = derivatives["contract"]
                prior_oi = contract["open_interest"] - contract["change_in_oi"]
                self.assertGreater(prior_oi, 0)
                self.assertTrue(math.isclose(
                    contract["change_in_oi"] / prior_oi * 100.0,
                    fixture["oi_change_pct"], abs_tol=1e-3),
                    f"{sid}: oi_change_pct does not reproduce")
                self.assertEqual(contract["expiry"], row["expiry"])
                self.assertEqual(fixture["futures_price"], contract["close"])
                self.assertEqual(fixture["spot_price"], equity["close"])
                self.assertEqual(fixture["days_to_expiry"],
                                 (date.fromisoformat(row["expiry"])
                                  - date.fromisoformat(fixture["as_of"])).days)

                # The measured class signature itself.
                self.assertGreater(fixture["price_change_pct"], 0.0)
                self.assertLess(fixture["delivery_pct"], seed.DELIVERY_MAX)
                self.assertLessEqual(fixture["oi_change_pct"], seed.OI_MAX)

    # ------------------------------------------------------------------ rows
    def test_each_row_declares_recorded_stage_5_over_its_bhavcopy_bundle(self):
        for sid, row in self.rows.items():
            with self.subTest(scenario=sid):
                declared = [a for a in row["recorded_artifacts"]
                            if a["artifact"] == "out_5_flow.json"]
                self.assertEqual(len(declared), 1)
                self.assertEqual(declared[0]["evidence"], "recorded")
                self.assertEqual(declared[0]["stage"], 5)
                self.assertEqual(
                    declared[0]["path"],
                    f"fixtures/{sid}/recorded/out_5_flow.json")
                self.assertEqual(row["replay_inputs"]["bhavcopy_fixture"],
                                 f"fixtures/{sid}/{seed.FIXTURE_FILE}")
                self.assertTrue(row["classification_tags"])
                evidence = row["authoring"]["distribution_evidence"]
                self.assertEqual(evidence["fixture"],
                                 f"fixtures/{sid}/{seed.FIXTURE_FILE}")
                self.assertIn("never typed", evidence["disclosure"])
                self.assertLess(evidence["delivery_pct"], seed.DELIVERY_MAX)
                self.assertLessEqual(evidence["oi_change_pct"], seed.OI_MAX)

    def test_recorded_stage_5_bytes_carry_the_class_invariant(self):
        """The graded payload: matrix verdict, thresholds, and the fixture's own
        numbers — what a reader would otherwise have to take on trust."""
        for sid, row in self.rows.items():
            with self.subTest(scenario=sid):
                fixture = read_json(PROOF_ROOT / row["replay_inputs"]["bhavcopy_fixture"])
                payload = read_json(PROOF_ROOT / "fixtures" / sid / "recorded"
                                    / "out_5_flow.json")
                self.assertFalse(payload.get("waived"))
                self.assertIsNone(payload.get("error"))
                self.assertEqual(payload["matrix_verdict"], "SHORT_COVERING_TRAP")
                self.assertLess(payload["delivery_pct"], seed.DELIVERY_MAX)
                self.assertLessEqual(payload["oi_change_pct"], seed.OI_MAX)
                self.assertEqual(payload["as_of"], fixture["as_of"])
                # The payload is the engine's arithmetic over the fixture.
                self.assertEqual(payload["delivery_pct"], fixture["delivery_pct"])
                self.assertEqual(payload["oi_change_pct"],
                                 round(fixture["oi_change_pct"], 2))
                expected_z = (round((fixture["delivery_pct"] - fixture["delivery_mean_20d"])
                                    / fixture["delivery_std_20d"], 2)
                              if fixture["delivery_std_20d"] > 0 else 0.0)
                self.assertEqual(payload["delivery_zscore"], expected_z)
                self.assertTrue(math.isclose(
                    payload["basis_annualized"],
                    round((fixture["futures_price"] - fixture["spot_price"])
                          / fixture["spot_price"] * (365.0 / fixture["days_to_expiry"])
                          * 100.0, 2), abs_tol=0.01))

    # --------------------------------------------------------------- records
    def test_every_record_references_recorded_bytes_and_tapes(self):
        """One copy, one provenance: refs into the bundle, derives from the tape."""
        for sid in self.by_id:
            with self.subTest(scenario=sid):
                raw_record = self.raw_by_id[sid]
                for artifact in ("out_1_math.json", "out_2_market.json",
                                 "out_5_flow.json", "out_8_dhan.json"):
                    self.assertTrue(
                        dr.is_ref(raw_record["script_outputs"][artifact]),
                        f"{sid}: {artifact} must be a $ref, never inline bytes")
                    target = REPO_ROOT / dr.ref_path(
                        raw_record["script_outputs"][artifact])
                    self.assertTrue(target.exists(), f"{sid}: missing {target}")
                for field in ("forward_prices_22d", "resolution_date",
                              "resolution_outcome", "market_mae_pct",
                              "actual_direction", "limit_mae_pct"):
                    self.assertTrue(dr.is_derive(raw_record[field]),
                                    f"{sid}.{field} must derive from the pinned tape")
                self.assertIsNone(raw_record["cone_p10"])
                self.assertIsNone(raw_record["predicted_direction"])
                # The limit price itself is the recorded stage-8 zone read out
                # of `out_8_dhan.json` (the $ref above), never a typed number.
                self.assertIsInstance(raw_record["limit_entry_price"], (int, float))

    def test_the_tape_resolves_loss_under_the_frozen_rule(self):
        """AVOID expected, LOSS realized, TN — decided by the tape, not the tool."""
        for sid, row in self.rows.items():
            with self.subTest(scenario=sid):
                self.assertEqual(row["expected_verdict"], "AVOID")
                self.assertEqual(row["scoring"]["resolution_outcome"], "LOSS")
                self.assertEqual(row["scoring"]["quadrant_target"], "TN")
                record = self.by_id[sid]
                self.assertEqual(record["resolution_outcome"], "LOSS")
                bars = read_json(PROOF_ROOT / row["scoring"]["forward_tape"])["bars"]
                outcome, basis = dr.resolution_of(bars, float(row["stop_loss"]),
                                                  float(row["target_price"]))
                self.assertEqual(outcome, "LOSS",
                                 f"{sid}: the frozen rule does not read LOSS")
                self.assertEqual(basis, row["scoring"]["resolution_basis"])
                self.assertEqual(record["resolution_date"], row["scoring"]["resolution_date"])

    # --------------------------------------------------------------- grading
    def test_gate_2_discloses_the_class_instead_of_averaging_it(self):
        """No registered breaker means disclosure, not silent inclusion."""
        targets = run_evals.dataset_payload_targets(self.raw)
        unbacked = run_evals.unbacked_payloads(self.dataset["trades"], targets)
        gate = run_evals.gate_hard_breakers(
            self.dataset["trades"], unbacked,
            class_quota=self.dataset["meta"]["class_counts_expected"])
        self.assertIn(
            f"{seed.CLASS} no registered breaker ({len(self.trades)} record(s), "
            f"quota {self.dataset['meta']['class_counts_expected'][seed.CLASS]})",
            gate.coverage, gate.coverage)
        for sid in self.by_id:
            self.assertNotIn(sid, gate.detail or "", gate.detail)

    def test_the_class_contributes_no_rule_exercises(self):
        """The frozen floor may not be inflated by records that grade no rule."""
        census = run_evals.dataset_rule_census()
        self.assertNotIn(seed.CLASS, census)
        baseline = read_json(PROOF_ROOT / "baseline_scorecard.json")
        self.assertEqual(census, baseline["rule_exercises"],
                         "the census must still equal the frozen floor exactly")


if __name__ == "__main__":
    unittest.main()
