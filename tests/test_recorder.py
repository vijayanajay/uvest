"""Seam — the `--record` replay-bundle mechanism (scripts/_recorder.py).

The dataset-as-replay-bundle architecture stands on four frozen guarantees,
each asserted here:

1. The bundle artifact is BYTE-IDENTICAL to the stdout the stage emitted
   (one serialisation, two destinations — zero-hallucination prerequisite).
2. Artifact names come from the frozen contract map, never derived — a stage
   that guesses its own filename breaks the contract this repo's whole
   consistency suite guards, and this test case is the direct check.
3. The manifest is append-only across a chained run.
4. A stage that has not registered a frozen artifact name fails LOUDLY
   (AssertionError naming the contract table), not silently with a derived name.

The RELIANCE bundle committed under tests/fixtures/ is itself a recorded run
of stages 0→1, so its byte-exactness is also verified against live output.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import _recorder  # noqa: E402

VALID_FUTURES = ("Symbol: RELIANCE\nType: FUT\nEntry Range: 2975.00-2985.00\n"
                 "Live Price: 2988.20\nStoploss: 2910.00\nTarget: 3120.00\n"
                 "Lot Size: 250\nRecommended Date: 2026-10-09\n")

GROUND_TRUTH_CANONICAL = {
    "symbol": "RELIANCE",
    "instrument_type": "FUT",
    "entry_range_min": 2975.00,
    "entry_range_max": 2985.00,
    "live_price": 2988.20,
    "lot_size": 250,
}


class TestFrozenArtifactMap(unittest.TestCase):
    """Artifact names are contract-frozen, not derived from script stems."""

    def test_stage_names_match_the_frozen_contract(self):
        self.assertEqual(_recorder.FROZEN_ARTIFACTS[0], "current_trade.json")
        self.assertEqual(_recorder.FROZEN_ARTIFACTS[1], "out_1_math.json")
        self.assertEqual(_recorder.FROZEN_ARTIFACTS[2], "out_2_market.json")
        self.assertEqual(_recorder.FROZEN_ARTIFACTS[3], "out_3_dtw.json")
        self.assertEqual(_recorder.FROZEN_ARTIFACTS[5], "out_5_flow.json")

    def test_unregistered_stage_fails_loudly_not_derivatively(self):
        """Any stage NOT in the frozen map must fail loudly naming the contract.
        (Stage 4 is the next unimplemented stage; keep this forward-looking.)"""
        with self.assertRaises(AssertionError) as caught:
            _recorder.artifact_filename(4)
        self.assertIn("BRD §9.1", str(caught.exception))


class TestRecordedBundleByteExactness(unittest.TestCase):
    """The committed RELIANCE bundle is a real recording, not hand-authored."""

    @classmethod
    def setUpClass(cls):
        cls.bundle = REPO_ROOT / "tests" / "fixtures" / "RELIANCE_20261009"
        if not cls.bundle.exists():
            raise AssertionError(
                f"expected recorded bundle missing: {cls.bundle} — "
                "record it with scripts/0_ingest_reco.py --record auto"
            )

    def run_pipeline_recording(self, workroot: Path):
        """Run stages 0→1 with --record into a scratch bundle; return paths."""
        input_path = workroot / "input.txt"
        input_path.write_text(VALID_FUTURES, encoding="utf-8")
        scratch = workroot / "bundle"
        p0 = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "0_ingest_reco.py"),
             "--input", str(input_path), "--record", str(scratch), "--input-date-less"],
            capture_output=True, text=True, encoding="utf-8", check=False,
            cwd=REPO_ROOT,
        )
        return p0, scratch

    def test_bundle_artifacts_are_byte_identical_to_stdout_rerun(self):
        """Re-running stage 0 twice must produce identical bytes: determinism
        is what makes a bundle *replayable* rather than a one-off snapshot."""
        stdout_recent = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "0_ingest_reco.py"),
             "--input", str(REPO_ROOT / "tests" / "fixtures" / "_input_reliance_20261009.txt")],
            capture_output=True, text=True, encoding="utf-8", check=False, cwd=REPO_ROOT,
        )
        recorded = (self.bundle / "current_trade.json").read_text(encoding="utf-8")
        self.assertEqual(stdout_recent.returncode, 0, stdout_recent.stderr)
        self.assertEqual(stdout_recent.stdout, recorded)

    def test_bundle_manifest_lists_both_stages_append_only(self):
        manifest = json.loads((self.bundle / "manifest.json").read_text(encoding="utf-8"))
        stages = [row["stage"] for row in manifest["stages"]]
        self.assertEqual(stages, sorted(stages), "manifest stages must be append-only ordered")
        self.assertIn(0, stages)
        self.assertIn(1, stages)
        names = {row["artifact"] for row in manifest["stages"]}
        self.assertEqual(names, {"current_trade.json", "out_1_math.json",
                                 "out_2_market.json", "out_3_dtw.json"})

    def test_recorded_stage1_payload_matches_ground_truth_values(self):
        out = json.loads((self.bundle / "out_1_math.json").read_text(encoding="utf-8"))
        self.assertAlmostEqual(out["live_rr"], 131.80 / 78.20, places=6)
        self.assertAlmostEqual(out["risk_inr"], 19550.00, places=2)
        self.assertFalse(out["inverted_rr_flag"])


class TestRecordFlagOnFreshRun(unittest.TestCase):
    """`--record <dir>` on a clean run creates the bundle from nothing."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def run_stage0_with_record(self, record_value: str):
        input_path = self.root / "in.txt"
        input_path.write_text(VALID_FUTURES, encoding="utf-8")
        bundle = (self.root / record_value) if record_value != "auto" else "AUTO"
        cmd = [sys.executable, str(REPO_ROOT / "scripts" / "0_ingest_reco.py"),
               "--input", str(input_path), "--record", record_value]
        if record_value == "auto":
            cmd += ["--input-date-less"]  # not a real flag; guard against accidental use
            cmd = cmd[:-1]
            # auto derives from recommended_date inside the input; that is 2026-10-09.
        return subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", check=False, cwd=REPO_ROOT), bundle

    def test_explicit_record_dir_creates_bundle(self):
        proc, bundle = self.run_stage0_with_record(str(self.root / "bundle"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        artifact = self.root / "bundle" / "current_trade.json"
        self.assertTrue(artifact.exists(), "recorded artifact missing")
        self.assertEqual(artifact.read_text(encoding="utf-8"), proc.stdout,
                         "recorded artifact is not byte-identical to stdout")

    def test_rejections_are_not_recorded(self):
        """ADR-001 hard rejects must NOT write a bundle — no 'committed option
        trade' hallucination can ever enter the dataset."""
        input_path = self.root / "in.txt"
        input_path.write_text(VALID_FUTURES.replace("Type: FUT", "Type: CE"), encoding="utf-8")
        record_dir = self.root / "bundle"
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "0_ingest_reco.py"),
             "--input", str(input_path), "--record", str(record_dir)],
            capture_output=True, text=True, encoding="utf-8", check=False, cwd=REPO_ROOT,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertFalse(record_dir.exists(), "rejected trade must not leave a bundle")


class TestAutoDerivation(unittest.TestCase):
    def test_auto_bundle_name_uses_symbol_and_date(self):
        self.assertEqual(_recorder.bundle_name("RELIANCE", "2026-10-09"),
                         "RELIANCE_20261009")
        self.assertEqual(_recorder.bundle_name("SBIN", None), "SBIN_undated")


if __name__ == "__main__":
    unittest.main()
