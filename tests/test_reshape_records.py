"""Seam — the shape-ladder campaign (`tests/evals/reshape_records.py`).

The campaign re-authors a class's geometry, so its safety properties are frozen
here instead of living in an operator's memory:

1. **The declared families respect the class specs.** Every rung is a shape the
   class's own documentation allows (`docs/agents/evals.md` §2: clean R:R ≥ 1.8,
   `inverted_rr` under 1.0, `tight_stop` under 0.75xATR), the families are
   distinct, and rung 0 is the frozen curator shape so the campaign cannot drift
   from what authored the fleet.
2. **A placement is what the class says it is.** Every row the campaign moved
   resolves to its class's documented outcome on its own pinned tape — the
   admissibility rule — so the campaign raises the rule-exercise census without
   manufacturing FP/FN rows.
3. **The disclosure is the bytes.** A placed row's levels reproduce its recorded
   rung shape from its own ≤T_rec tape, and the re-recorded payloads round to that
   rung at the granule the census speaks in — the projection the plan was built
   on is what stage 1/2 actually emitted.
4. **Rungs buy rule exercises.** A rung is used only if it adds at least one
   exercise the class does not already stand for; re-planning the live dataset
   with the campaign's own rows settled proposes no further placement.
5. **A dry run writes nothing, and an applied campaign is idempotent.** A
   plan is free, and re-running `--apply` cannot churn the proof root, the
   dataset or the baseline.
6. **Rows whose geometry is their own claim are refused** — the legacy
   `GB-2024-0004` and `GB-2025-0005` keep the levels their own tapes corroborate.

No network is touched: every tape read here is committed with the repo.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
PROOF_ROOT = TESTS_DIR / "proof"
GOLDEN = TESTS_DIR / "evals" / "golden_dataset.json"
TOOL = TESTS_DIR / "evals" / "reshape_records.py"

sys.path.insert(0, str(TESTS_DIR / "evals"))
sys.path.insert(0, str(TESTS_DIR))

import dataset_refs as dr  # noqa: E402
import reshape_records as rr  # noqa: E402  (the tool under test)
import run_evals  # noqa: E402  (the census unit)

OWN_CLAIM = ("GB-2024-0004", "GB-2025-0005")


def run_tool(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL), *args],
                          capture_output=True, check=False, cwd=REPO_ROOT)


def fingerprint() -> str:
    """One digest over the proof root and the dataset (what a dry run may not move)."""
    digest = hashlib.sha256()
    for path in sorted(p for p in PROOF_ROOT.rglob("*") if p.is_file()):
        digest.update(path.relative_to(PROOF_ROOT).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    digest.update(GOLDEN.read_bytes())
    return digest.hexdigest()


class TestShapeLadderContract(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not TOOL.exists():
            raise AssertionError(f"campaign tool missing: {TOOL}")
        cls.raw = json.loads(GOLDEN.read_text(encoding="utf-8"))
        cls.materialized, _refs, _derives = dr.materialize(cls.raw)
        cls.trades = {t["trade_id"]: t for t in cls.materialized["trades"]}
        manifest = json.loads(
            (PROOF_ROOT / "scenario_manifest.json").read_text(encoding="utf-8"))
        cls.rows = {r["scenario_id"]: r for r in manifest["scenarios"]}
        cls.reshaped = sorted(
            sid for sid, row in cls.rows.items()
            if (row.get("authoring") or {}).get("reshape", {}).get("campaign") == rr.CAMPAIGN)
        cls.settled = set(cls.reshaped)

    # ------------------------------------------------------------ the families
    def test_declared_families_respect_the_frozen_class_specs(self):
        """Every rung is a shape the class's own documentation allows."""
        self.assertEqual(rr.ladder_problems(), [])
        for cls, rungs in rr.LADDERS.items():
            self.assertEqual(rungs[0], rr.CLASS_SHAPE[cls],
                             f"{cls}'s first rung must be the frozen curator shape")
            self.assertEqual(len(set(rungs)), len(rungs), f"{cls} repeats a rung")
            for risk, band in rungs:
                if cls == "clean_bullish":
                    self.assertGreaterEqual(band, 1.8, "clean R:R is documented at >= 1.8")
                    self.assertGreater(band, 1.0, "a clean rung must not trip the R:R breaker")
                    self.assertGreaterEqual(risk, rr.TIGHT_LIMIT_ATR,
                                            "a clean rung must not read as a tight stop")
                elif cls == "inverted_rr":
                    self.assertLess(band, 1.0)
                    self.assertGreaterEqual(risk, 0.9, "inverted traps must not read tight")
                else:
                    self.assertIsNone(band, "the tight class's shape is its 2.5R target")
                    self.assertLess(risk, rr.TIGHT_LIMIT_ATR)
        # And the check is not decorative: a family that breaks a spec is caught.
        broken = dict(rr.LADDERS)
        broken["clean_bullish"] = ((2.0, 2.2), (1.0, 1.2))
        original, rr.LADDERS = rr.LADDERS, broken
        try:
            self.assertTrue(rr.ladder_problems(), "an out-of-spec rung must be a bug")
        finally:
            rr.LADDERS = original

    def test_every_rung_is_its_own_shape(self):
        """The campaign's families span the parameter space, not one axis of it."""
        for cls, rungs in rr.LADDERS.items():
            risks = {risk for risk, _ in rungs}
            self.assertGreaterEqual(len(risks), 4, f"{cls} varies too few risk multiples")
            if cls != "tight_stop":
                bands = {band for _, band in rungs}
                self.assertGreaterEqual(len(bands), 4, f"{cls} varies too few R:R bands")

    # ------------------------------------------------------------ the placements
    def test_a_placement_realizes_its_class_outcome(self):
        """Admissibility: no row was moved off its class's documented outcome.

        This is the seam that keeps the campaign from decorating a census with
        FP/FN rows: every moved row still resolves the way its class says it does.
        """
        self.assertTrue(self.reshaped, "the campaign must have placements to check")
        for sid in self.reshaped:
            row = self.rows[sid]
            cls = self.trades[sid]["trade_class"]
            self.assertEqual(row["scoring"]["resolution_outcome"], rr.CLASS_OUTCOME[cls],
                             f"{sid} no longer resolves as a {cls}")

    def test_a_placement_is_the_rung_its_row_discloses(self):
        """The levels, the disclosure and the recorded bytes are one shape."""
        for sid in self.reshaped:
            row = self.rows[sid]
            reshape = row["authoring"]["reshape"]
            risk, band = reshape["shape"]["risk_atr"], reshape["shape"]["rr"]
            prefix, _forward = rr.pinned_tape(sid)
            geometry = rr.geometry_of(prefix, risk, band)
            self.assertEqual((row["entry_price"], row["stop_loss"], row["target_price"]),
                             (geometry["entry"], geometry["stop"], geometry["target"]), sid)
            self.assertEqual(row["scoring"]["resolution_basis"],
                             dr.resolution_of(_forward, geometry["stop"],
                                              geometry["target"])[1], sid)
            outputs = self.trades[sid]["script_outputs"]
            stage1, stage2 = outputs["out_1_math.json"], outputs["out_2_market.json"]
            if band is not None:
                self.assertEqual(round(stage1["raw_rr"], 2), round(band, 2), sid)
                self.assertEqual(round(stage1["live_rr"], 2), round(band, 2), sid)
            self.assertEqual(round(stage2["sl_atr_ratio"], 2), round(risk, 2), sid)
            self.assertEqual(stage2["is_tight_sl"], risk < rr.TIGHT_LIMIT_ATR, sid)

    def test_the_projection_is_what_the_recording_emitted(self):
        """The plan's projected exercises are the recorded payload's own census."""
        for sid in self.reshaped:
            row = self.rows[sid]
            reshape = row["authoring"]["reshape"]
            cls = self.trades[sid]["trade_class"]
            risk, band = reshape["shape"]["risk_atr"], reshape["shape"]["rr"]
            self.assertEqual(
                run_evals.rule_exercises(cls, self.trades[sid]),
                rr.projected_exercises(cls, band, risk),
                f"{sid}: the census counts a shape the recording did not produce")

    def test_a_rung_is_only_worth_taking_for_new_rule_evidence(self):
        """The plan's own gate: a repeat adds nothing, a new band adds exercises."""
        first = rr.projected_exercises("clean_bullish", 2.2, 2.0)
        self.assertTrue(first, "a clean shape stands for its arms' clear checks")
        covered = set(first)
        self.assertFalse(rr.projected_exercises("clean_bullish", 2.2, 2.0) - covered,
                         "the same shape adds no new rule exercise")
        self.assertTrue(rr.projected_exercises("clean_bullish", 2.5, 2.0) - covered,
                        "a new R:R band must add one")
        self.assertTrue(rr.projected_exercises("clean_bullish", 2.2, 2.5) - covered,
                        "a new ATR multiple must add one")

    def test_the_campaign_moved_no_row_whose_geometry_is_its_own_claim(self):
        """`GB-2024-0004` / `GB-2025-0005` keep the levels their tapes corroborate."""
        plan = rr.build_plan(self.materialized["trades"], None, self.settled)
        refused = {item["trade_id"] for item in plan["refused"]}
        for sid in OWN_CLAIM:
            self.assertIn(sid, refused, f"{sid} must be refused, not re-authored")
            self.assertNotIn(sid, self.settled)
            row = self.rows[sid]
            self.assertNotIn("reshape", row["authoring"])
            prefix, _forward = rr.pinned_tape(sid)
            cls = self.trades[sid]["trade_class"]
            self.assertFalse(rr.at_class_shape(self.trades[sid], cls, prefix),
                             f"{sid} stands at the class shape after all")

    def test_replanning_the_applied_campaign_proposes_no_further_placement(self):
        """The floor may only grow: every rung the classes still lacked is taken.

        Re-planning with the campaign's own rows settled must find nothing left to
        add — if it did, the applied campaign stopped short of its own rungs.
        """
        plan = rr.build_plan(self.materialized["trades"], None, self.settled)
        writable = [p for p in plan["placed"] if not p["unchanged"]]
        self.assertEqual(writable, [], f"unplaced rungs remain: "
                                       f"{[(p['trade_id'], p['to_shape']) for p in writable]}")
        self.assertEqual(rr.predicted_census(plan, self.materialized["trades"]),
                         run_evals.dataset_rule_census())

    def test_the_frozen_floor_is_the_live_census(self):
        """The baseline's floor was re-frozen by this campaign, and cannot be stale."""
        baseline = run_evals.load_baseline()
        census = run_evals.dataset_rule_census()
        for cls, floor in baseline["rule_exercises"].items():
            self.assertEqual(census[cls], floor,
                             f"{cls}: the floor is not what the campaign measured")

    # ------------------------------------------------------------ the workflow
    def test_a_dry_run_writes_nothing(self):
        before = fingerprint()
        proc = run_tool()
        stdout = proc.stdout.decode("utf-8", "replace")
        self.assertEqual(proc.returncode, 0, stdout + proc.stderr.decode("utf-8", "replace"))
        self.assertIn("DRY RUN", stdout)
        self.assertIn("rule-exercise census", stdout)
        self.assertEqual(fingerprint(), before, "a dry run wrote to the evidence tree")

    def test_an_applied_campaign_is_idempotent(self):
        """Re-applying cannot re-record, churn the proof root or re-freeze."""
        before = fingerprint()
        proc = run_tool("--apply")
        stdout = proc.stdout.decode("utf-8", "replace")
        self.assertEqual(proc.returncode, 0, stdout + proc.stderr.decode("utf-8", "replace"))
        self.assertIn("nothing to do", stdout)
        self.assertEqual(fingerprint(), before)

    def test_the_cli_refuses_an_out_of_spec_family(self):
        """`main` exits 2 on a broken family instead of planning anything."""
        broken = dict(rr.LADDERS)
        broken["tight_stop"] = ((0.6, None), (0.90, None))
        original, rr.LADDERS = rr.LADDERS, broken
        try:
            self.assertEqual(rr.main([]), 2, "an out-of-spec family must not be planned")
        finally:
            rr.LADDERS = original


if __name__ == "__main__":
    unittest.main()
