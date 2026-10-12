"""Seam — the committed proof manifest (`tests/proof/`) stays valid under V1–V14.

This is the Layer-1 enforcement of the evidence rule on the real, committed
dataset: if anyone hand-edits a recorded artifact, smuggles in unlisted
evidence, moves a cutoff, or drifts the baseline, this seam fails the
invariant suite — and therefore pre-commit (ADR-007; scenario-manifest-schema.md).

The bootstrap contract asserted here: the manifest carries its first
provenance-tagged rows, every graded artifact is recorded (never hand-typed),
and the quarantine register stays in step with the manifest — so the first time a
legacy hand-typed payload appears, the register (and this seam) must be updated in
the same change.

The golden dataset may *reference* these artifacts (`$ref`) instead of copying
an extracted subset inline. That coupling is verified here, not in the dataset:
every reference must land on the very file the row declares, inside the proof
root, with bytes matching its frozen `manifest.sha256` entry — and any artifact
a row declares must be referenced, never re-inlined. One copy, one provenance.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PROOF_ROOT = TESTS_DIR / "proof"
REPO_ROOT = TESTS_DIR.parent
GOLDEN_DATASET = TESTS_DIR / "evals" / "golden_dataset.json"
sys.path.insert(0, str(TESTS_DIR / "evals"))

import dataset_refs as dr  # noqa: E402  (sibling eval modules)
import scaffold_catalog as sc  # noqa: E402
import validate_manifest as vm  # noqa: E402

# The offline-runnable stages, and how to re-run one against a row's fixtures
# (stage 6 reads the row's authored financials bundle, so its recorded breaker
# evidence is byte-compared too — a hazard row cannot pass on a stale artifact).
FORWARD_CLAIM_FIELDS = (
    "forward_prices_22d",
    "resolution_date",
    "resolution_outcome",
    "market_mae_pct",
    "actual_direction",
    "limit_mae_pct",
)

# Ex-ante claims only an engine can produce, and the stage that would produce
# them (BRD §9's artifact inventory: the cone envelope and the direction come
# from stage 4, the limit-entry MAE from stage 8). A record with a proof row may
# carry one ONLY when that row declares the stage's recorded artifact — otherwise
# the value is a hand-typed number dressed as engine output.
ENGINE_CLAIM_STAGES = {
    "cone_p10": 4,
    "cone_p90": 4,
    "predicted_direction": 4,
    "predicted_probability": 4,
    "limit_mae_pct": 8,
}

RERUNNABLE = {
    1: "1_math_and_rr.py",
    2: "2_market_data.py",
    # Stage 6 is offline-runnable through the row's authored financials bundle
    # (`replay_inputs.financials_fixture`), so a recorded `out_6_fundamental.json`
    # is byte-compared to a fresh run exactly like stages 1–2 — the hazard rows'
    # breaker evidence is verified, not taken on trust.
    6: "6_fundamental_audit.py",
}


def fresh_stage_stdout(row: dict, artifact: dict) -> bytes | None:
    """V10's replay callback: the artifact a fresh offline run emits, or None.

    `None` means "not offline-runnable yet" (the validator reports it as an
    unchecked warning, never a pass). Stages 1–2 read only the row's
    point-in-time fixtures, so this is deterministic and network-free.
    """
    stage = artifact.get("stage")
    script = RERUNNABLE.get(stage)
    if script is None:
        return None
    inputs = row.get("replay_inputs") or {}
    cmd = [sys.executable, str(REPO_ROOT / "scripts" / script),
           "--input", str(PROOF_ROOT / inputs["canonical_trade"])]
    if stage == 2:
        cmd += ["--ohlcv-fixture", str(PROOF_ROOT / inputs["truncated_fixtures"][0])]
    if stage == 6:
        cmd += ["--financials-fixture", str(PROOF_ROOT / inputs["financials_fixture"])]
    proc = subprocess.run(cmd, capture_output=True, check=False, cwd=REPO_ROOT)
    return proc.stdout if proc.returncode == 0 else None


class TestCommittedProofManifest(unittest.TestCase):
    """The committed proof dataset is evidence, not documentation."""

    @classmethod
    def setUpClass(cls):
        manifest_path = PROOF_ROOT / vm.MANIFEST_FILE
        if not manifest_path.exists():
            raise AssertionError(
                f"committed proof manifest missing: {manifest_path} — the proof root "
                "is a spec artifact and must survive a fresh clone"
            )
        cls.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        cls.rows = {row["scenario_id"]: row for row in cls.manifest["scenarios"]}
        if not GOLDEN_DATASET.exists():
            raise unittest.SkipTest(f"golden dataset not present: {GOLDEN_DATASET}")
        cls.golden = json.loads(GOLDEN_DATASET.read_text(encoding="utf-8"))

    @classmethod
    def frozen_hashes(cls) -> dict[str, str]:
        """`manifest.sha256` via the validator's own reader (one parsing rule)."""
        report = vm.ValidationReport()
        entries = vm._read_hash_file(PROOF_ROOT, report) or {}
        if report.errors:
            raise AssertionError([(f.code, f.message) for f in report.errors])
        return entries

    def test_manifest_passes_all_frozen_checks(self):
        """Every frozen check, with V10 now actually re-running the stages.

        Supplying the replay callback turns V10 from a warning into a
        byte-comparison of each recorded artifact against a fresh offline run
        of its stage — the strongest available evidence that the committed
        artifacts are real stdout and not hand-copied values.
        """
        report = vm.validate_manifest(self.manifest, PROOF_ROOT, require_baseline=True,
                                      rerun=fresh_stage_stdout)
        self.assertTrue(report.ok, [(f.code, f.message) for f in report.errors])
        self.assertEqual([(f.code, f.message) for f in report.warnings], [],
                         "a fresh-run byte comparison must leave no warning behind")

    def test_every_row_carries_recorded_provenance(self):
        rows = self.manifest["scenarios"]
        self.assertGreaterEqual(len(rows), 2, "expected the first seeded rows to be committed")
        for row in rows:
            self.assertTrue(row.get("classification_tags"),
                            f"{row['scenario_id']}: rows must carry a provenance tag")
            recorded = [a for a in row.get("recorded_artifacts", [])
                        if a.get("evidence") == "recorded"]
            self.assertTrue(recorded, f"{row['scenario_id']}: no recorded evidence")
            for artifact in recorded:
                self.assertTrue((PROOF_ROOT / artifact["path"]).exists(),
                                f"{row['scenario_id']}: recorded artifact missing")

    def test_quarantine_register_matches_manifest_and_baseline(self):
        """The first quarantined row (legacy stage-6 pledge payload) and its frozen meter."""
        quarantined = [(row["scenario_id"], a["stage"], a["artifact"])
                       for row in self.manifest["scenarios"]
                       for a in row.get("recorded_artifacts", [])
                       if a.get("evidence") == "quarantined"]
        self.assertEqual(quarantined, [("GB-2023-0003", 6, "out_6_fundamental.json")])
        register = json.loads((PROOF_ROOT / vm.REGISTER_FILE).read_text(encoding="utf-8"))
        self.assertEqual(register.get("schema"), vm.REGISTER_SCHEMA)
        self.assertEqual(
            [(r["scenario_id"], r["stage"], r["artifact"]) for r in register.get("rows", [])],
            quarantined,
        )
        baseline = json.loads((PROOF_ROOT / vm.BASELINE_FILE).read_text(encoding="utf-8"))
        self.assertEqual(baseline.get("quarantined_count"), len(quarantined),
                         "baseline must freeze the non-increasing quarantine meter")
        self.assertEqual(
            baseline.get("quarantine_register_sha256"),
            hashlib.sha256((PROOF_ROOT / vm.REGISTER_FILE).read_bytes()).hexdigest(),
        )
        artifact = next(a for row in self.manifest["scenarios"]
                        for a in row.get("recorded_artifacts", [])
                        if a.get("evidence") == "quarantined")
        self.assertTrue((PROOF_ROOT / artifact["path"]).exists(),
                        "quarantined payload must stay on disk as the tracked debt")


    # ------------------------------------------------ golden dataset references

    def test_golden_dataset_refs_point_at_frozen_proof_artifacts(self):
        """A reference must land on the row's declared, hash-frozen artifact.

        The dataset names *where* a payload lives; the row names which artifact
        that is; the hash file freezes its bytes. This ties the three together,
        so a ref can never point at an unregistered file, at another scenario's
        evidence, or at bytes that differ from the frozen ones — and quarantine
        provenance is read from the register, never restated in the dataset.
        """
        refs = dr.refs(self.golden)
        self.assertTrue(refs, "expected the migrated legacy record to reference proof artifacts")
        hashes = self.frozen_hashes()
        register = dr.quarantined_artifacts()
        for trade_id, artifact, ref in refs:
            row = self.rows.get(trade_id)
            self.assertIsNotNone(
                row, f"{trade_id}: a reference must belong to a proof-root row")
            declared = next((a for a in row.get("recorded_artifacts", [])
                             if a.get("artifact") == artifact), None)
            self.assertIsNotNone(
                declared,
                f"{trade_id}: {artifact} is referenced but the row does not declare it")
            target = REPO_ROOT / ref
            self.assertTrue(target.exists(), f"{trade_id}: referenced artifact missing: {ref}")
            rel = target.relative_to(PROOF_ROOT).as_posix()
            self.assertEqual(
                rel, vm._safe_relpath(declared.get("path")),
                f"{trade_id}: ref points at a different file than the row declares")
            self.assertIn(rel, hashes, f"{trade_id}: {rel} has no frozen sha256")
            self.assertEqual(
                hashlib.sha256(target.read_bytes()).hexdigest(), hashes[rel],
                f"{trade_id}: referenced bytes are not the frozen ones")
            self.assertEqual(
                (trade_id, artifact) in register,
                declared.get("evidence") == "quarantined",
                f"{trade_id}: the quarantine register disagrees with the row's evidence")

    def test_catalog_rows_match_the_immutable_catalog(self):
        """A SCEN row's expectations must equal the catalog's, never a re-typed copy.

        The scaffolder reads anchor/symbol/instrument/verdict/quadrant straight
        out of `business_test_cases.md`; this guards the authored rows against a
        later hand-edit drifting away from the immutable source.
        """
        catalog = sc.parse_catalog()
        self.assertGreaterEqual(len(catalog), 60, "the frozen catalog is 60 scenarios")
        rows = [r for r in self.manifest["scenarios"]
                if str(r["scenario_id"]).startswith("SCEN-0")]
        self.assertTrue(rows, "expected the first frozen-catalog rows to be authored")
        for row in rows:
            sid = row["scenario_id"]
            spec = catalog.get(sid)
            self.assertIsNotNone(spec, f"{sid} is not in the frozen catalog")
            for row_field, spec_field in (
                ("symbol", "symbol"),
                ("instrument", "instrument"),
                ("recommendation_date", "anchor"),
                ("expected_verdict", "expected_verdict"),
            ):
                self.assertEqual(row.get(row_field), spec[spec_field],
                                 f"{sid}: {row_field} drifted from the catalog")
            self.assertEqual(row["scoring"].get("quadrant_target"), spec["quadrant_target"],
                             f"{sid}: quadrant drifted from the catalog")
            self.assertEqual(row["scoring"].get("resolution_outcome"),
                             spec["realized_outcome"],
                             f"{sid}: realized outcome drifted from the catalog")

    def test_no_proof_artifact_is_also_copied_inline(self):
        """One copy, one provenance: a declared artifact is referenced, never inlined.

        This is the drift the reference mechanism exists to prevent: the golden
        record and the proof root holding two copies of the same scenario's
        payload, one of them silently stale.
        """
        for trade in self.golden["trades"]:
            row = self.rows.get(trade["trade_id"])
            if row is None:
                continue
            declared = {a.get("artifact") for a in row.get("recorded_artifacts", [])}
            for artifact, payload in (trade.get("script_outputs") or {}).items():
                if artifact in declared:
                    self.assertTrue(
                        dr.is_ref(payload),
                        f"{trade['trade_id']}: {artifact} is copied inline while the proof "
                        "root declares it — use $ref so the two cannot drift")


    # ------------------------------------------- pinned-tape outcome derivations

    def test_rows_leave_engine_less_claims_absent(self):
        """A record with a proof row may not hand-type what no engine produced.

        The cone envelope, the predicted direction/probability and the
        limit-entry MAE are stage 4 / stage 8 outputs. Until those engines exist
        their absence is the only honest value (E4); a number here is a claim
        wearing engine clothes, and the migration retires it to `None` rather
        than parking it. A future row may carry one the moment it declares the
        stage's *recorded* artifact — evidence first, number second.
        """
        rows_checked = 0
        for trade in self.golden.get("trades", []):
            tid = trade.get("trade_id")
            row = self.rows.get(tid)
            if row is None:
                continue
            rows_checked += 1
            recorded_stages = {
                a.get("stage") for a in row.get("recorded_artifacts", [])
                if a.get("evidence") == "recorded"
            }
            for field, stage in ENGINE_CLAIM_STAGES.items():
                value = trade.get(field)
                if value is None:
                    continue
                self.assertIn(
                    stage, recorded_stages,
                    f"{tid}.{field} carries {value!r} while the row declares no "
                    f"recorded stage-{stage} artifact — no engine produced it, so "
                    "it must be absent, not hand-typed")
        self.assertGreaterEqual(rows_checked, 1,
                                "expected records with proof rows to check")

    def test_pinned_records_derive_their_forward_claims(self):
        """A record whose proof row pins a forward tape may not hand-type outcomes.

        `forward_prices_22d`, the resolution pair, direction and MAE describe what
        the tape did. The tape is committed and hash-frozen, so the record must
        reference it with `$derive` (or carry nothing) — an inline number is an
        unverifiable claim that can silently drift from the evidence. Derived
        values are recomputed here and must equal the row's own scoring block, so
        the dataset and the proof root cannot disagree.
        """
        hashes = self.frozen_hashes()
        pinned = 0
        for trade in self.golden.get("trades", []):
            tid = trade.get("trade_id")
            row = self.rows.get(tid)
            if row is None:
                continue
            tape_rel = (row.get("scoring") or {}).get("forward_tape")
            if not tape_rel:
                continue
            pinned += 1
            tape = f"tests/proof/{tape_rel}"
            self.assertIn(tape_rel, hashes,
                          f"{tid}: pinned forward tape is not hash-frozen")
            self.assertEqual(
                hashlib.sha256((PROOF_ROOT / tape_rel).read_bytes()).hexdigest(),
                hashes[tape_rel],
                f"{tid}: pinned forward tape bytes are not the frozen ones")
            for field in FORWARD_CLAIM_FIELDS:
                value = trade.get(field)
                if value is None:
                    continue
                self.assertTrue(
                    dr.is_derive(value),
                    f"{tid}.{field} is a hand-typed outcome claim ({value!r}) while "
                    "the proof row pins a forward tape — use $derive or leave it absent")
                spec_tape, _rule = dr.derive_spec(value)
                self.assertEqual(spec_tape, tape,
                                 f"{tid}.{field} derives from {spec_tape}, not the "
                                 f"row's declared tape {tape}")
            scoring = row.get("scoring") or {}
            if dr.is_derive(trade.get("forward_prices_22d")):
                derived = dr.derive_value(trade["forward_prices_22d"], trade)
                cache = scoring.get("forward_derived_cache") or {}
                self.assertTrue({"min", "max"} <= set(cache),
                                f"{tid}: row's forward_derived_cache is incomplete")
                for key in ("min", "max"):
                    self.assertAlmostEqual(
                        derived[key], cache[key], delta=1e-6,
                        msg=f"{tid}: derived forward_prices_22d.{key} != the row's cache")
            if dr.is_derive(trade.get("resolution_date")):
                self.assertEqual(dr.derive_value(trade["resolution_date"], trade),
                                 scoring.get("resolution_date"),
                                 f"{tid}: derived resolution_date != the row's")
            if dr.is_derive(trade.get("resolution_outcome")):
                self.assertEqual(dr.derive_value(trade["resolution_outcome"], trade),
                                 scoring.get("resolution_outcome"),
                                 f"{tid}: derived resolution_outcome != the row's")
        self.assertGreaterEqual(pinned, 1, "expected at least one pinned record")

    def test_stored_scoring_matches_the_frozen_rule(self):
        """Every row's stored resolution equals the frozen rule on its own tape.

        This is the reconciliation seam: the curator's seed scan, the catalog
        scaffolder, the migration tool and the `$derive` rule all delegate to
        `dataset_refs.resolution_of`, so what a row stored at authoring time
        must be what the rule recomputes from the pinned forward tape —
        outcome, basis and horizon end alike. A row seeded under the retired
        window-level convention (any stop touch inside the window) fails here
        instead of silently grading a different history than the tape tells.
        The tape's bytes are frozen by V4 and the dataset's derived claims are
        checked against the row elsewhere; this seam pins the row itself.
        """
        for sid, row in self.rows.items():
            scoring = row.get("scoring") or {}
            tape_rel = scoring.get("forward_tape")
            self.assertTrue(tape_rel,
                            f"{sid}: a seeded row must pin a forward tape")
            bars = json.loads(
                (PROOF_ROOT / tape_rel).read_text(encoding="utf-8"))["bars"]
            outcome, basis = dr.resolution_of(
                bars, float(row["stop_loss"]), float(row["target_price"]))
            self.assertEqual(scoring.get("resolution_outcome"), outcome,
                             f"{sid}: stored resolution_outcome disagrees with "
                             "the frozen rule on its own tape")
            self.assertEqual(scoring.get("resolution_basis"), basis,
                             f"{sid}: stored resolution_basis disagrees with "
                             "the frozen rule")
            self.assertEqual(scoring.get("resolution_date"), bars[-1]["date"],
                             f"{sid}: stored resolution_date is not the "
                             "tape's horizon end")
            authored = (row.get("authoring") or {}).get("realized_outcome_basis")
            if authored is not None:
                self.assertEqual(
                    authored, basis,
                    f"{sid}: authoring.realized_outcome_basis disagrees with "
                    "the frozen rule")



if __name__ == "__main__":
    unittest.main()
