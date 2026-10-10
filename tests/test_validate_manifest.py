"""Seam — the scenario-manifest validator (checks V1–V14, `docs/agents/scenario-manifest-schema.md`).

Seam contract:

1. A valid synthetic proof passes with zero errors — the validator does not
   cry wolf on well-formed evidence.
2. Every one of the frozen checks V1–V14 has a synthetic violation it must
   catch: the completeness assertion fails if a check is added to the
   validator without a fixture that exercises it (or silently dropped).
3. The CLI fails loudly (exit 1, named code on stdout) on unlisted evidence,
   and exits 2 when the manifest itself is unreadable — the "failing loudly,
   not derivatively" contract this repo uses for every frozen map.

Everything runs offline against temp directories; no real proof root is needed.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
sys.path.insert(0, str(TESTS_DIR / "evals"))

import validate_manifest as vm  # noqa: E402

T_REC = "2023-03-15"
PREFIX_BARS = [
    {"date": "2023-03-13", "high": 101.0, "low": 99.0, "close": 100.0},
    {"date": "2023-03-14", "high": 102.0, "low": 100.0, "close": 101.0},
    {"date": "2023-03-15", "high": 103.0, "low": 101.0, "close": 102.0},
]
FORWARD_BARS = [
    {"date": "2023-03-16", "high": 106.0, "low": 101.0, "close": 105.0},
    {"date": "2023-03-17", "high": 108.0, "low": 104.0, "close": 107.0},
    {"date": "2023-03-20", "high": 110.0, "low": 106.0, "close": 109.0},
]
CACHE = {"min": min(b["low"] for b in FORWARD_BARS),
         "max": max(b["high"] for b in FORWARD_BARS)}

ROW_1_FILES = {
    "current_trade.json": {"symbol": "TATASTEEL", "instrument_type": "FUT"},
    "ohlcv_prefix.json": {"sessions": PREFIX_BARS},
    "recorded/out_1_math.json": {"raw_rr": 2.1, "live_rr": 2.1, "inverted_rr_flag": False},
    "recorded/out_2_market.json": {"atr_14": 3.5, "sl_atr_ratio": 1.8, "is_tight_sl": False},
    "forward_tape.json": {"bars": FORWARD_BARS},
}
ROW_2_FILES = {
    "current_trade.json": {"symbol": "SBIN", "instrument_type": "FUT"},
    "ohlcv_prefix.json": {"sessions": PREFIX_BARS},
    "recorded/out_1_math.json": {"raw_rr": 0.9, "live_rr": 0.9, "inverted_rr_flag": True},
    "forward_tape.json": {"bars": FORWARD_BARS},
}


def _row_1() -> dict:
    return {
        "scenario_id": "SCEN-001",
        "schema_version": vm.SCHEMA_NAME,
        "origin": "scan_generated",
        "created_on": "2026-10-10",
        "authoring": {"scan_rule": "breakout_20d_high", "anchor_date": T_REC,
                      "universe_version": "fno_liquid_v1"},
        "classification_tags": [],
        "gate_focus": ["S1"],
        "symbol": "TATASTEEL", "exchange": "NSE", "instrument": "FUT",
        "expiry": "2023-03-30", "action": "BUY",
        "recommendation_date": T_REC,
        "entry_price": 102.0, "entry_range_min": 101.0, "entry_range_max": 103.0,
        "target_price": 110.0, "stop_loss": 96.0, "lot_size": 550,
        "user_capital_inr": 500000.0,
        "replay_inputs": {
            "canonical_trade": "fixtures/SCEN-001/current_trade.json",
            "truncated_fixtures": ["fixtures/SCEN-001/ohlcv_prefix.json"],
            "truncation": {"cutoff": T_REC, "rule": "date <= T_rec", "min_bars": 3},
        },
        "recorded_artifacts": [
            {"stage": 1, "artifact": "out_1_math.json",
             "path": "fixtures/SCEN-001/recorded/out_1_math.json", "evidence": "recorded"},
            {"stage": 2, "artifact": "out_2_market.json",
             "path": "fixtures/SCEN-001/recorded/out_2_market.json", "evidence": "recorded"},
        ],
        "scoring": {
            "forward_tape": "fixtures/SCEN-001/forward_tape.json",
            "bars": len(FORWARD_BARS),
            "resolution_date": "2023-03-20",
            "resolution_outcome": "WIN",
            "resolution_basis": "target_first",
            "forward_derived_cache": dict(CACHE),
            "quadrant_target": "TP",
            "regime_tags": ["commodity_supercycle"],
        },
        "expected_verdict": "GO",
    }


def _row_2() -> dict:
    return {
        "scenario_id": "GB-2023-0012",
        "schema_version": vm.SCHEMA_NAME,
        "origin": "curated_behavioral",
        "created_on": "2026-10-10",
        "authoring": {"shape": {"risk_atr": 1.2, "rr": 0.9}, "source": "curate"},
        "classification_tags": [],
        "gate_focus": ["promoter_pledging_gt_25pct"],
        "symbol": "SBIN", "exchange": "NSE", "instrument": "FUT",
        "expiry": "2023-03-30", "action": "BUY",
        "recommendation_date": T_REC,
        "entry_price": 552.0, "entry_range_min": 551.0, "entry_range_max": 553.0,
        "target_price": 530.0, "stop_loss": 565.0, "lot_size": 750,
        "user_capital_inr": 500000.0,
        "replay_inputs": {
            "canonical_trade": "fixtures/GB-2023-0012/current_trade.json",
            "truncated_fixtures": ["fixtures/GB-2023-0012/ohlcv_prefix.json"],
            "truncation": {"cutoff": T_REC, "rule": "date <= T_rec", "min_bars": 3},
        },
        "recorded_artifacts": [
            {"stage": 1, "artifact": "out_1_math.json",
             "path": "fixtures/GB-2023-0012/recorded/out_1_math.json", "evidence": "recorded"},
            {"stage": 6, "artifact": "out_6_fundamental.json", "path": None,
             "evidence": "waived", "reason": "stage 6 unimplemented"},
        ],
        "scoring": {
            "forward_tape": "fixtures/GB-2023-0012/forward_tape.json",
            "bars": len(FORWARD_BARS),
            "resolution_date": "2023-03-20",
            "resolution_outcome": "LOSS",
            "resolution_basis": "both_hit_conservative_loss",
            "quadrant_target": None,
            "regime_tags": ["sector_bleed"],
        },
        "expected_verdict": "AVOID",
    }


def _base_manifest() -> dict:
    return {"schema": vm.SCHEMA_NAME, "generated_on": "2026-10-10",
            "scenarios": [_row_1(), _row_2()]}


# --------------------------------------------------------------------------
# fixture writer
# --------------------------------------------------------------------------
def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_evidence(root: Path) -> None:
    for rel, payload in ROW_1_FILES.items():
        _write_json(root / "fixtures" / "SCEN-001" / rel, payload)
    for rel, payload in ROW_2_FILES.items():
        _write_json(root / "fixtures" / "GB-2023-0012" / rel, payload)


def _write_manifest(root: Path, manifest: dict) -> None:
    _write_json(root / vm.MANIFEST_FILE, manifest)


def _write_hashes(root: Path) -> None:
    lines = []
    for path in sorted(p for p in (root / "fixtures").rglob("*") if p.is_file()):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.relative_to(root).as_posix()}")
    (root / vm.HASH_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_baseline(root: Path) -> None:
    _write_json(root / vm.BASELINE_FILE, {
        "schema": "uvest-baseline-scorecard/1",
        "scenario_manifest_sha256": hashlib.sha256(
            (root / vm.MANIFEST_FILE).read_bytes()).hexdigest(),
        "manifest_sha256": hashlib.sha256(
            (root / vm.HASH_FILE).read_bytes()).hexdigest(),
    })


def build(root: Path, manifest: dict | None = None) -> dict:
    """Materialize a fully consistent synthetic proof; returns its manifest."""
    manifest = manifest if manifest is not None else _base_manifest()
    _write_evidence(root)
    _write_manifest(root, manifest)
    _write_hashes(root)
    _write_baseline(root)
    return manifest


def refresh(root: Path, manifest: dict) -> None:
    """Re-commit every digest after a deliberate mutation (keeps V4/V12 quiet)."""
    _write_manifest(root, manifest)
    _write_hashes(root)
    _write_baseline(root)


def load(root: Path) -> dict:
    return json.loads((root / vm.MANIFEST_FILE).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# violations — one exerciser per frozen check
# --------------------------------------------------------------------------
def _violate_v1(root: Path, manifest: dict) -> dict:
    manifest["scenarios"][1]["scenario_id"] = manifest["scenarios"][0]["scenario_id"]
    refresh(root, manifest)
    return {"baseline": None}


def _violate_v2(root: Path, manifest: dict) -> dict:
    manifest["scenarios"][0]["instrument"] = "CE"
    refresh(root, manifest)
    return {"baseline": None}


def _violate_v3(root: Path, manifest: dict) -> dict:
    manifest["scenarios"][0]["replay_inputs"]["canonical_trade"] = "../escape.json"
    refresh(root, manifest)
    return {"baseline": None}


def _violate_v4(root: Path, manifest: dict) -> dict:
    (root / "fixtures" / "SCEN-001" / "recorded" / "out_1_math.json").write_text(
        '{"tampered": true}\n', encoding="utf-8")
    return {"baseline": None}  # deliberately no re-hash: the tamper must be caught


def _violate_v5(root: Path, manifest: dict) -> dict:
    (root / "stray_evidence.json").write_text("{}\n", encoding="utf-8")
    return {"baseline": None}


def _violate_v6(root: Path, manifest: dict) -> dict:
    manifest["scenarios"][0]["scoring"]["forward_tape"] = (
        manifest["scenarios"][0]["replay_inputs"]["canonical_trade"])
    refresh(root, manifest)
    return {"baseline": None}


def _violate_v7(root: Path, manifest: dict) -> dict:
    doc = json.loads((root / "fixtures" / "SCEN-001" / "ohlcv_prefix.json").read_text(encoding="utf-8"))
    doc["sessions"].append({"date": "2023-03-16", "high": 104.0, "low": 102.0, "close": 103.0})
    _write_json(root / "fixtures" / "SCEN-001" / "ohlcv_prefix.json", doc)
    refresh(root, manifest)
    return {"baseline": None}


def _violate_v8(root: Path, manifest: dict) -> dict:
    bars = [{"date": "2023-03-10", "high": 100.0, "low": 98.0, "close": 99.0}, *FORWARD_BARS]
    _write_json(root / "fixtures" / "SCEN-001" / "forward_tape.json", {"bars": bars})
    manifest["scenarios"][0]["scoring"]["forward_derived_cache"] = {
        "min": min(b["low"] for b in bars), "max": max(b["high"] for b in bars)}
    refresh(root, manifest)
    return {"baseline": None}


def _violate_v9(root: Path, manifest: dict) -> dict:
    manifest["scenarios"][0]["scoring"]["forward_derived_cache"]["min"] += 1.0
    refresh(root, manifest)
    return {"baseline": None}


def _violate_v10(root: Path, manifest: dict) -> dict:
    return {"baseline": None, "rerun": lambda row, artifact: b"fresh bytes that differ"}


def _violate_v11(root: Path, manifest: dict) -> dict:
    manifest["scenarios"][1]["recorded_artifacts"][1] = {
        "stage": 6, "artifact": "out_6_fundamental.json", "path": None,
        "evidence": "quarantined", "reason": "legacy hand-typed pledge value",
    }
    refresh(root, manifest)
    return {"baseline": None}


def _violate_v12(root: Path, manifest: dict) -> dict:
    manifest["scenarios"][0]["authoring"]["source"] = "tampered-after-freeze"
    _write_manifest(root, manifest)  # disk manifest changes; baseline deliberately not rewritten
    return {}  # baseline loads from disk -> digest mismatch


def _violate_v13(root: Path, manifest: dict) -> dict:
    manifest["scenarios"][0]["classification_tags"] = ["WAIT_TRACKED_SEPARATELY"]
    refresh(root, manifest)
    return {"baseline": None}


def _violate_v14(root: Path, manifest: dict) -> dict:
    manifest["scenarios"][0]["replay_inputs"]["truncation"]["cutoff"] = "2023-03-14"
    refresh(root, manifest)
    return {"baseline": None}


VIOLATIONS = {
    "V1": _violate_v1, "V2": _violate_v2, "V3": _violate_v3, "V4": _violate_v4,
    "V5": _violate_v5, "V6": _violate_v6, "V7": _violate_v7, "V8": _violate_v8,
    "V9": _violate_v9, "V10": _violate_v10, "V11": _violate_v11,
    "V12": _violate_v12, "V13": _violate_v13, "V14": _violate_v14,
}


class ManifestFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)


class TestValidManifest(ManifestFixture):
    def test_valid_synthetic_proof_passes_with_zero_errors(self):
        manifest = build(self.root)
        report = vm.validate_manifest(manifest, self.root)
        self.assertTrue(report.ok, [f.code for f in report.errors])
        self.assertEqual(report.errors, [])
        # Honesty: without a replay runner, V10 reports its skip as a warning.
        self.assertIn("V10", report.codes())

    def test_v10_byte_matching_rerun_passes_cleanly(self):
        manifest = build(self.root)
        recorded = {
            "fixtures/SCEN-001/recorded/out_1_math.json",
            "fixtures/SCEN-001/recorded/out_2_market.json",
            "fixtures/GB-2023-0012/recorded/out_1_math.json",
        }
        report = vm.validate_manifest(
            manifest, self.root,
            rerun=lambda row, artifact: (self.root / artifact["path"]).read_bytes()
            if artifact["path"] in recorded else None,
            baseline=None,
        )
        self.assertTrue(report.ok, [(f.code, f.message) for f in report.errors])

    def test_v10_recording_matches_crlf_normalized_fresh_run(self):
        """Windows writes CRLF on disk; a fresh LF-normalized stdout must still match."""
        manifest = build(self.root)
        report = vm.validate_manifest(
            manifest, self.root, baseline=None,
            rerun=lambda row, artifact: (self.root / artifact["path"]).read_bytes()
            .replace(b"\r\n", b"\n"),
        )
        self.assertTrue(report.ok, [(f.code, f.message) for f in report.errors])

    def test_v12_deliberate_refreeze_downgrades_to_warning(self):
        manifest = build(self.root)
        manifest["scenarios"][0]["authoring"]["source"] = "re-freeze"
        _write_manifest(self.root, manifest)

        frozen = vm.validate_manifest(manifest, self.root)
        self.assertIn("V12", frozen.codes())
        self.assertFalse(frozen.ok)

        declared = vm.validate_manifest(manifest, self.root, allow_re_freeze=True)
        self.assertTrue(declared.ok, [(f.code, f.message) for f in declared.errors])
        self.assertIn("V12_RE_FREEZE_DECLARED", [f.code for f in declared.warnings])


class TestEveryFrozenCheckHasAViolation(ManifestFixture):
    def test_registry_covers_every_check(self):
        self.assertEqual(set(VIOLATIONS), set(vm.FROZEN_CHECKS),
                         "a frozen check has no synthetic violator (or a stray one exists)")

    def test_each_violation_is_caught(self):
        for code, violate in VIOLATIONS.items():
            with self.subTest(check=code), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                manifest = build(root)
                kwargs = violate(root, manifest) or {}
                report = vm.validate_manifest(manifest, root, **kwargs)
                self.assertFalse(report.ok, f"{code}: violation was not caught")
                self.assertIn(code, report.codes(),
                              f"{code}: validator emitted {sorted(report.codes())} instead")

    def test_options_are_rejected_by_name(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest = build(root)
            manifest["scenarios"][0]["instrument"] = "PE"
            refresh(root, manifest)
            report = vm.validate_manifest(manifest, root, baseline=None)
            self.assertIn("V2_OPTIONS_PROHIBITED", [f.code for f in report.errors])


class TestCliContract(ManifestFixture):
    def test_cli_passes_valid_proof(self):
        build(self.root)
        proc = subprocess.run(
            [sys.executable, str(TESTS_DIR / "evals" / "validate_manifest.py"),
             "--proof-root", str(self.root)],
            capture_output=True, text=True, encoding="utf-8", check=False, cwd=REPO_ROOT,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PASSED", proc.stdout)

    def test_cli_fails_loudly_on_unlisted_evidence(self):
        build(self.root)
        (self.root / "smuggled.json").write_text("{}\n", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(TESTS_DIR / "evals" / "validate_manifest.py"),
             "--proof-root", str(self.root)],
            capture_output=True, text=True, encoding="utf-8", check=False, cwd=REPO_ROOT,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("V5_UNLISTED_EVIDENCE", proc.stdout)
        self.assertIn("FAILED", proc.stdout)

    def test_cli_missing_manifest_exits_two(self):
        proc = subprocess.run(
            [sys.executable, str(TESTS_DIR / "evals" / "validate_manifest.py"),
             "--proof-root", str(self.root)],
            capture_output=True, text=True, encoding="utf-8", check=False, cwd=REPO_ROOT,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("not found", proc.stderr)


if __name__ == "__main__":
    unittest.main()
