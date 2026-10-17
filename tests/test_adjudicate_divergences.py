"""Seam — the GO→GO_WEAK adjudication report (evals gate 8's per-record half).

Gate 8 (`run_evals.gate_verdict_alignment`) grades a *number*: the
expectation-vs-computed agreement percentage, plus the names of the divergent
records. Its contract says each divergence is then "adjudicated by fixing the
engine or re-freezing the expectation, never by tuning the scorecard" — and
that adjudication needs, per record, the exact missing evidence and the
distance between the recorded P(win) and the frozen 55% floor.

`tests/evals/adjudicate_divergences.py` publishes exactly that to
`reports/adjudication_go_divergences.md`, and this seam keeps the publication
honest along four axes:

1. **It cannot go stale.** The committed report must be byte-identical to a
   fresh render from the current payloads — a report that fell behind the
   evidence fails here instead of quietly contradicting it.
2. **It cannot omit or invent a record.** Its sections are exactly the records
   whose proof row says `expected_verdict: GO` while the recorded stage-9
   payload says otherwise (all of them `GO_WEAK` today), and nothing else.
3. **Every figure is arithmetic on recorded bytes.** P(win), its signed
   distance to the floor, and the effective score are read from each row's own
   `out_9_decision.json`; the floor is the engine's constant, not a copy.
4. **Every evidence claim matches the declaration.** Each §10.2 source's cell
   says `scored` / `absent` / `waived` exactly as the row's
   `recorded_artifacts` entry and the payload on disk say it.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
EVALS_DIR = TESTS_DIR / "evals"
REPO_ROOT = TESTS_DIR.parent
PROOF_ROOT = TESTS_DIR / "proof"
DATASET_FILE = EVALS_DIR / "golden_dataset.json"
MANIFEST_FILE = PROOF_ROOT / "scenario_manifest.json"
REPORT_FILE = REPO_ROOT / "reports" / "adjudication_go_divergences.md"

sys.path.insert(0, str(EVALS_DIR))
sys.path.insert(0, str(TESTS_DIR))

import adjudicate_divergences as adj  # noqa: E402


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def expected_slice() -> dict[str, dict]:
    """`trade_id` -> recorded stage-9 payload for every divergent GO row.

    Read straight from the manifest and the recorded artifacts — deliberately
    re-derived here rather than borrowed from the tool under test.
    """
    rows = {r["scenario_id"]: r for r in load_json(MANIFEST_FILE)["scenarios"]}
    trades = {t["trade_id"]: t for t in load_json(DATASET_FILE)["trades"]}
    slice_: dict[str, dict] = {}
    for sid, row in rows.items():
        if row.get("expected_verdict") != "GO" or sid not in trades:
            continue
        path = PROOF_ROOT / "fixtures" / sid / "recorded" / adj.STAGE9_ARTIFACT
        assert path.exists(), f"{sid}: no recorded stage-9 payload to compare against"
        decision = json.loads(path.read_text(encoding="utf-8"))
        if decision["decision"] != "GO":
            slice_[sid] = decision
    return slice_


class TestTheReportIsAFunctionOfTheRecordedBytes(unittest.TestCase):
    """Stale, truncated or hand-edited reports are refused, not read."""

    @classmethod
    def setUpClass(cls):
        if not REPORT_FILE.exists():
            raise AssertionError(
                "the adjudication report is a deliverable — write it with "
                "`python tests/evals/adjudicate_divergences.py --apply`")
        cls.report = REPORT_FILE.read_text(encoding="utf-8")
        cls.slice = expected_slice()

    def test_the_committed_report_is_byte_equal_to_a_fresh_render(self):
        self.assertEqual(
            REPORT_FILE.read_bytes(), adj.render().encode("utf-8"),
            "the report fell behind the recorded payloads — regenerate it with "
            "`python tests/evals/adjudicate_divergences.py --apply`")

    def test_sections_are_exactly_the_divergent_go_records(self):
        sections = re.findall(r"^### (\S+) ", self.report, re.MULTILINE)
        self.assertEqual(sections, sorted(self.slice),
                         "every divergent GO record is adjudicated, and only "
                         "those; aligned GO rows and other expectations never "
                         "appear in this report")
        self.assertNotIn("", sections)
        for sid in sections:
            with self.subTest(sid=sid):
                self.assertEqual(self.slice[sid]["decision"], "GO_WEAK")

    def test_the_summary_counts_the_slice_it_prints(self):
        self.assertIn(f"**{len(self.slice)}** record(s) carry "
                      "`expected_verdict: GO`", self.report)
        complete = sum(
            1 for sid in self.slice
            if "scored" in self._source_cell(sid, "out_3_dtw.json")
            and "scored" in self._source_cell(sid, "out_4_ml.json"))
        self.assertIn(f"**{complete} of {len(self.slice)}** records score both "
                      "from recorded bytes", self.report)

    def _section(self, sid: str) -> str:
        match = re.search(rf"^### {re.escape(sid)} .*?(?=^### |\Z)",
                          self.report, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(match, f"{sid}: no section in the report")
        return match.group(0)

    def _source_cell(self, sid: str, artifact: str) -> str:
        match = re.search(rf"^\| `{re.escape(artifact)}` \| [^|]+ \| (.*) \|$",
                          self._section(sid), re.MULTILINE)
        self.assertIsNotNone(match, f"{sid}: {artifact} row missing from the table")
        return match.group(1)

    def test_every_p_win_and_gap_equals_the_recorded_payload(self):
        floor = float(adj.engine.GO_WIN_FLOOR)
        for sid, decision in sorted(self.slice.items()):
            section = self._section(sid)
            with self.subTest(sid=sid):
                self.assertIn(f"**P(win) {decision['p_win']:.4f}**", section)
                gap = re.search(
                    r"distance to the 55% floor: \*\*([+-]?\d+\.\d{2}) pp\*\*",
                    section)
                self.assertIsNotNone(gap, "each record states its floor distance")
                expected_gap = (decision["p_win"] - floor) * 100.0
                self.assertAlmostEqual(float(gap.group(1)), expected_gap, places=2)
                self.assertIn(
                    f"effective {decision['effective_score_pct']:.2f}%", section)
                self.assertIn(f"{decision['final_score']} / "
                              f"{decision['max_possible_score']}", section)
                self.assertIn(binding_words(decision), section)

    def test_each_source_state_matches_the_declaration_and_payload(self):
        manifest = {r["scenario_id"]: r
                    for r in load_json(MANIFEST_FILE)["scenarios"]}
        for sid in sorted(self.slice):
            row = manifest[sid]
            for artifact, weight in adj.engine.DEFAULT_WEIGHTS.items():
                state, detail = adj.source_state(row, artifact)
                cell = self._source_cell(sid, artifact)
                with self.subTest(sid=sid, source=artifact):
                    if state == "scored":
                        self.assertEqual(cell, "scored")
                    elif state == "waived":
                        self.assertEqual(cell, f"**waived** — {detail}")
                    else:
                        self.assertEqual(cell, f"**absent** — {detail}")

    def test_the_thresholds_are_the_engines_own_constants(self):
        engine = adj.engine
        self.assertIn(f"| `GO_WIN_FLOOR` | {float(engine.GO_WIN_FLOOR):.2f} |",
                      self.report)
        self.assertIn(f"| `P_BASE` | {float(engine.P_BASE):.3f} |", self.report)
        self.assertIn(f"| `P_SLOPE` | {float(engine.P_SLOPE):.4f} |", self.report)
        self.assertIn(
            f"total §10.2 weight (`DEFAULT_WEIGHTS`) | "
            f"{sum(engine.DEFAULT_WEIGHTS.values()):.1f}", self.report)

    def test_the_floor_score_is_what_the_engines_calibration_says(self):
        floor = float(adj.engine.GO_WIN_FLOOR)
        need = adj.floor_efficiency(adj.engine)
        self.assertGreaterEqual(
            adj.engine.calibrated_p_win(need), floor,
            "the stated floor score must reach the floor")
        self.assertLess(
            adj.engine.calibrated_p_win(round(need - 0.01, 2)), floor,
            "…and it must be the smallest score at its precision that does")
        self.assertIn(f"smallest effective score reaching the floor | "
                      f"**{need:.2f}**", self.report)


def binding_words(decision: dict) -> str:
    """The engine's `confidence_deficit` as the report must quote it."""
    deficit = decision.get("confidence_deficit")
    return f"engine's own words: “{deficit or '—'}”"


if __name__ == "__main__":
    unittest.main()