"""Seam — stage 12 writes the canonical report the differ grades (TICK-010).

The BRD §9.1 contract names the artifact (`reports/report_{SYMBOL}_{DATE}.html`,
written by the script itself), and the Zero-Hallucination gate grades those
bytes against the record's own JSON (`tests/evals/report_diff.py`). What is
frozen here:

1. The CLI writes the canonical filename and prints a JSON status line.
2. Output is byte-deterministic and genuinely standalone (no external
   resources, charts as inline SVG).
3. **The acceptance property:** every golden record gets a report at its
   canonical path, and every one of them is differ-clean — the gate grades
   them 100.0%. This is the runner-level proof that the visualizer cannot
   hallucinate a number. The same artifacts must satisfy the HTML-validity
   gate (HTML5 doctype, zero external resources, self-contained styling,
   inline SVG): the artifact is the evidence, not a curator's flag (ADR-007).
4. Absent stage payloads render honest placeholders, and an unusable input
   exits non-zero instead of writing a half-truth.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
STAGE12 = REPO_ROOT / "scripts" / "12_generate_html_report.py"
DATASET_FILE = TESTS_DIR / "evals" / "golden_dataset.json"
MANIFEST_FILE = TESTS_DIR / "proof" / "scenario_manifest.json"

sys.path.insert(0, str(TESTS_DIR / "evals"))
sys.path.insert(0, str(TESTS_DIR))

import report_regen  # noqa: E402  (sibling eval modules, spliced above)
import run_evals  # noqa: E402


def synthetic_trade() -> dict:
    return {
        "symbol": "INFY",
        "exchange": "NSE",
        "instrument_type": "CASH",
        "action": "BUY",
        "entry_price": 1500.0,
        "entry_range_min": 1498.5,
        "entry_range_max": 1502.25,
        "live_price": 1500.0,
        "target": 1560.0,
        "stoploss": 1462.5,
        "lot_size": 1,
        "recommended_date": "2026-01-05",
        "user_capital_inr": 500000.0,
    }


def synthetic_payloads(root: Path) -> list[Path]:
    math = root / "out_1_math.json"
    math.write_text(json.dumps({"quantity": 1, "risk_inr": 37.5, "reward_inr": 60.0,
                                "raw_rr": 1.6, "live_rr": 1.6,
                                "inverted_rr_flag": False}), encoding="utf-8")
    market = root / "out_2_market.json"
    market.write_text(json.dumps({"atr_14": 15.25, "sl_atr_ratio": 2.459016,
                                  "is_tight_sl": False, "trend_state": "BULLISH",
                                  "waived": False}), encoding="utf-8")
    return [math, market]


def run_stage12(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(STAGE12), *args],
                          capture_output=True, check=False, cwd=REPO_ROOT)


class TestStage12CLI(unittest.TestCase):
    def test_canonical_path_and_json_status_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trade = root / "current_trade.json"
            trade.write_text(json.dumps(synthetic_trade()), encoding="utf-8")
            out_dir = root / "out"
            args = ["--input", str(trade), "--out-dir", str(out_dir)]
            for payload in synthetic_payloads(root):
                args += ["--payload", str(payload)]
            proc = run_stage12(args)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            status = json.loads(proc.stdout.decode("utf-8"))
            artifact = out_dir / "report_INFY_20260105.html"
            self.assertTrue(artifact.exists(),
                            f"canonical artifact missing: {artifact}")
            self.assertEqual(Path(status["artifact"]).name, artifact.name)
            self.assertEqual(status["bytes"], artifact.stat().st_size)

    def test_output_is_deterministic_and_self_contained(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trade = root / "current_trade.json"
            trade.write_text(json.dumps(synthetic_trade()), encoding="utf-8")
            args = ["--input", str(trade), "--out-dir", str(root / "a")]
            for payload in synthetic_payloads(root):
                args += ["--payload", str(payload)]
            self.assertEqual(run_stage12(args).returncode, 0)
            args_b = [a.replace(str(root / "a"), str(root / "b")) for a in args]
            self.assertEqual(run_stage12(args_b).returncode, 0)
            first = (root / "a" / "report_INFY_20260105.html").read_bytes()
            second = (root / "b" / "report_INFY_20260105.html").read_bytes()
            self.assertEqual(first, second, "same inputs must yield same bytes")
            text = first.decode("utf-8")
            self.assertTrue(text.startswith("<!doctype html>"))
            for external in ("http://", "https://", "<script", "<link"):
                self.assertNotIn(external, text,
                                 f"{external!r} makes the report not standalone")
            self.assertIn("<svg", text, "charts must be inline SVG")
            # The runner's own validity check must agree with these assertions.
            self.assertEqual(run_evals.html_validity_problems(text), [])

    def test_validity_check_catches_broken_reports(self):
        """The gate's checker must name what a broken report violates."""
        problems = run_evals.html_validity_problems(
            "<html><body>no doctype, no style, no chart</body></html>")
        self.assertTrue(any("doctype" in p for p in problems), problems)
        self.assertTrue(any("<style>" in p for p in problems), problems)
        self.assertTrue(any("<svg>" in p for p in problems), problems)
        external = run_evals.html_validity_problems(
            "<!doctype html><style>a{}</style><svg></svg>"
            '<img src="https://cdn.example.com/x.png">')
        self.assertTrue(any("external resource" in p for p in external), external)
        self.assertTrue(any("network URL" in p for p in external), external)
        # Self-contained references are legal.
        self.assertEqual(run_evals.html_validity_problems(
            "<!doctype html><style>a{}</style><svg></svg>"
            '<img src="data:image/png;base64,AA">'), [])

    def test_absent_payloads_render_placeholders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trade = root / "current_trade.json"
            trade.write_text(json.dumps(synthetic_trade()), encoding="utf-8")
            proc = run_stage12(["--input", str(trade), "--out-dir", str(root / "out")])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            text = (root / "out" / "report_INFY_20260105.html").read_text("utf-8")
            self.assertIn("awaiting stage-2 evidence", text)
            self.assertIn("awaiting stage-4 evidence", text)
            self.assertIn("verdict pending stage-9 evidence", text)

    def test_unusable_input_exits_non_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            empty = root / "empty.json"
            empty.write_text("{}", encoding="utf-8")
            proc = run_stage12(["--input", str(empty), "--out-dir", str(root / "out")])
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("ERR_INPUT", proc.stderr.decode("utf-8", "replace"))


class TestEveryRecordGetsAGradedReport(unittest.TestCase):
    """The acceptance property: 31 records, 31 clean reports, gate at 100.0%."""

    @classmethod
    def setUpClass(cls):
        if not STAGE12.exists():
            raise AssertionError(f"stage script missing: {STAGE12}")
        cls.data = json.loads(DATASET_FILE.read_text(encoding="utf-8"))
        manifest = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
        cls.jobs = report_regen.report_jobs(cls.data, manifest)

    def test_every_record_regenerates_and_the_gate_grades_it_clean(self):
        self.assertEqual(len(self.jobs), len(self.data["trades"]),
                         "every golden record must have a regeneration job")
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            artifacts, failures = report_regen.regenerate(self.jobs, out_dir)
            self.assertEqual(failures, [], "; ".join(failures))
            self.assertEqual(len(artifacts), len(self.jobs))
            self.assertEqual(report_regen.diff_reports(self.data, out_dir), [])
            materialized, _r, _d = run_evals.dataset_refs.materialize(self.data)
            gate = run_evals.gate_hallucination(materialized["trades"],
                                                reports_dir=out_dir)
            self.assertFalse(gate.skipped, gate.detail)
            self.assertEqual(gate.value, 100.0, gate.detail)
            status, _ = gate.evaluate()
            self.assertEqual(status, "[PASS]")
            # The same artifacts must also satisfy the HTML-validity gate.
            validity = run_evals.gate_html_validity(materialized["trades"],
                                                    reports_dir=out_dir)
            self.assertFalse(validity.skipped, validity.detail)
            self.assertEqual(validity.value, 100.0, validity.detail)
            v_status, _ = validity.evaluate()
            self.assertEqual(v_status, "[PASS]")


if __name__ == "__main__":
    unittest.main()
