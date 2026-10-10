"""Seam — the committed proof manifest (`tests/proof/`) stays valid under V1–V14.

This is the Layer-1 enforcement of the evidence rule on the real, committed
dataset: if anyone hand-edits a recorded artifact, smuggles in unlisted
evidence, moves a cutoff, or drifts the baseline, this seam fails the
invariant suite — and therefore pre-commit (ADR-007; scenario-manifest-schema.md).

The bootstrap contract asserted here: the manifest carries its first
provenance-tagged rows, every graded artifact is recorded (never hand-typed),
and the quarantine register is explicitly empty — so the first time a legacy
hand-typed payload appears, the register (and this seam) must be updated in the
same change.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PROOF_ROOT = TESTS_DIR / "proof"
sys.path.insert(0, str(TESTS_DIR / "evals"))

import validate_manifest as vm  # noqa: E402


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

    def test_manifest_passes_all_frozen_checks(self):
        report = vm.validate_manifest(self.manifest, PROOF_ROOT, require_baseline=True)
        self.assertTrue(report.ok, [(f.code, f.message) for f in report.errors])
        # Honest bootstrap state: V10 (fresh-run byte comparison) is the only
        # warning until the replay runner is wired to the validator.
        self.assertEqual([f.code for f in report.warnings], ["V10_REPLAY_NOT_PROVIDED"])

    def test_every_row_carries_recorded_provenance(self):
        rows = self.manifest["scenarios"]
        self.assertGreaterEqual(len(rows), 2, "expected the first seeded rows to be committed")
        for row in rows:
            self.assertIn("SCAFFOLD", row.get("classification_tags", []),
                          f"{row['scenario_id']}: scaffold rows must be tagged")
            recorded = [a for a in row.get("recorded_artifacts", [])
                        if a.get("evidence") == "recorded"]
            self.assertTrue(recorded, f"{row['scenario_id']}: no recorded evidence")
            for artifact in recorded:
                self.assertTrue((PROOF_ROOT / artifact["path"]).exists(),
                                f"{row['scenario_id']}: recorded artifact missing")

    def test_quarantine_is_empty_and_consistent(self):
        quarantined = [a for row in self.manifest["scenarios"]
                       for a in row.get("recorded_artifacts", [])
                       if a.get("evidence") == "quarantined"]
        self.assertEqual(quarantined, [], "scaffold rows must not start in quarantine")
        register = json.loads((PROOF_ROOT / vm.REGISTER_FILE).read_text(encoding="utf-8"))
        self.assertEqual(register.get("schema"), vm.REGISTER_SCHEMA)
        self.assertEqual(register.get("rows"), [])


if __name__ == "__main__":
    unittest.main()
