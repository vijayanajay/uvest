"""Seam — proposal-cone-envelope-depth.md's measured tables cannot drift.

The envelope-depth proposal puts ten depth pairs (P0–P9) in front of the owner
with headline readings taken from `tests/evals/envelope_depth_sweep.py`.failures this seam exists to catch:

1. **The sweep losing fidelity to the gate.** The sweep's P0 row must equal the
   committed `baseline_scorecard.json` gate-5 reading over the same graded set
   — 16 of 29 = 55.172…%, slices 14/23 and 2/6 — because the proposal's whole
   authority is that its baseline *is* today's scorecard. The sweep itself
   already aborts mid-run if P0 fails to reproduce a single record's stored
   `cone_p10`/`cone_p90`; this test pins the aggregate next to the banked
   number.
2. **The proposal going stale silently.** `test_the_proposal_quoted_depths_are
   _the_committed_reading` asserts the P4 headline the proposal recommends
   (82.8%) and the named residual miss ids. If corpus work later changes those
   readings, this test fails *inside the ordinary suite* — the same policy as
   `test_adjudicate_divergences`: the artifact must be regenerated and the
   proposal re-measured in the same change, never left to a reader.
3. **The B+D pairing drifting apart** (proposal §6): `test_the_bd_pairing_
   tables_are_the_committed_reading` pins the P4 headline's invariance across
   all five per-class atom rules (only width moves), the P6 regression at
   N≤10 (`GB-2022-0002` flips into the miss set — the measured cost of the
   narrowing), and the gate-4 anchor row; the tool itself aborts mid-run if
   the N=30 race Brier stops equaling the committed 0.409726949… to 1e-12.
4. **Q3's evidence or the side-by-side drifting** (§2's tail ladder and §6.4's
   `--pairwise` bullet): the ladder test pins the tail counts and held-out ids
   at every depth (flip chain SCEN-004@P4, SCEN-003@P6, SCEN-008@P8,
   SCEN-001@P9); the pairwise test keeps the default output free of the
   flag-only section and locks the three N=20 switchers plus
   `GB-2025-0030`'s lone own-block-vs-pooled verdict disagreement.
5. **The staged signature text going stale** (`TestTheStagedSignatureTextIsLive`):
   §8's Signing Q2/Q3/Q4 blocks are paste-ready only while every `find`
   string still matches its live target exactly once — if `BRD.md`,
   TICK-004, `evals.md` or `domain.md` drifts before the owner signs, the
   paste becomes a silent no-op; this pin fails instead, and signing retires
   it in the commit that applies the blocks (the §9 re-cut).

The tool runs once per class (model load + 29 stage-4 runs), so the assertions
read from one shared captured output.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SWEEP = TESTS_DIR / "evals" / "envelope_depth_sweep.py"
BASELINE = TESTS_DIR / "proof" / "baseline_scorecard.json"
PROPOSAL = TESTS_DIR.parent / "docs" / "agents" / \
    "proposal-cone-envelope-depth.md"

# §8's staged signature text (Signing Q2 / Q3 / Q4): every `find` string the
# implementation commit will paste, with the live file it must match. Each is
# pinned two-sided below — exactly once in the target file, verbatim in the
# proposal — so neither side can drift silently before the owner signs.
STAGED_FIND_STRINGS = (
    ("BRD.md",
     r"| **2. Expected Range** | GARCH(1,1) / Volatility Jump-Diffusion "
     r"Cones + Empirical 30-Day Historical Volatility (HV) + ATR Multiples. "
     r"| Quantified 22-trading-day price cone: "
     r"$[P_{10}, P_{25}, P_{50}, P_{75}, P_{90}]$. Projected **Max Adverse "
     r"Excursion (MAE)** and **Max Favorable Excursion (MFE)**. |"),
    ("docs/tickets/TICK-004-lightgbm-cone.md",
     r"| `price_cone_p10_p50_p90` | dict \| null | Percentile price "
     r"envelope of the 22-session **path** — the sessions' lows and highs, "
     r"the realization evals dimension 5 grades: `p10`/`p25` from the "
     r"excursion mixture of adverse excursions (worst low vs the entry), "
     r"`p50` from terminal returns, `p75`/`p90` from favourable excursions "
     r"(best high) |"),
    ("docs/tickets/TICK-004-lightgbm-cone.md",
     r"at least `MIN_SECTOR_ATOMS_PER_CLASS` (30) atoms"),
    ("docs/agents/evals.md",
     r"remained within the calculated $[P_{10}, P_{90}]$ price envelope — "
     r"**graded only over records whose forward tape lies inside the "
     r"trained corpus window**"),
    ("docs/agents/evals.md",
     r"MIN_SECTOR_ATOMS_PER_CLASS = 30"),
    ("docs/agents/evals.md",
     r"| **Coverage $\ge 80.0\%$** (over the in-window records; ungraded "
     r"records named on the coverage line, and the representative/tail "
     r"slice counts and scores printed beside the number — signed Q3)"),
    ("docs/agents/domain.md",
     r"- **Price Cone ([P10, P50, P90]):** Volatility-projected price "
     r"distribution over 22 trading days (1 month). P10 represents the "
     r"10th percentile bearish boundary; P90 represents the 90th "
     r"percentile bullish expansion."),
    ("docs/agents/domain.md",
     r"at least `MIN_SECTOR_ATOMS_PER_CLASS` (30) atoms"),
)


class TestEnvelopeDepthSweep(unittest.TestCase):
    out = ""
    err = ""

    @classmethod
    def setUpClass(cls) -> None:
        proc = subprocess.run([sys.executable, str(SWEEP)],
                              capture_output=True, check=False,
                              cwd=TESTS_DIR, timeout=600)
        if proc.returncode != 0:
            raise AssertionError(
                f"sweep exited {proc.returncode}:\n"
                f"{proc.stderr.decode('utf-8', 'replace')[-2000:]}")
        cls.out = proc.stdout.decode("utf-8")
        cls.err = proc.stderr.decode("utf-8")

    def test_the_sweep_grades_exactly_the_committed_scope(self) -> None:
        """29 graded, the two October tapes ungraded, window from the calibration."""
        self.assertIn("# sweep scope: 29 graded rows "
                      "(window 2020-02-18..2025-10-20)", self.err)
        self.assertIn("GB-2025-0031 (tape 2025-10-16..2025-11-18)", self.err)
        self.assertIn("GB-2025-0032 (tape 2025-10-16..2025-11-18)", self.err)

    def test_the_p0_row_equals_the_committed_gate_reading(self) -> None:
        gate = json.loads(BASELINE.read_text(encoding="utf-8"))
        value = gate["gates"]["Price Cone Range Coverage"]["value"]
        self.assertAlmostEqual(value, 1600.0 / 29.0, places=12,
                               msg="baseline_scorecard gate 5 is no longer "
                                   "16/29 — re-measure before trusting the "
                                   "proposal's P0 anchor")
        self.assertIn("| **P0** | 0.100 | 0.900 | **16 → 55.2%** | "
                      "14 → 60.9% | 2 → 33.3% |", self.out)
        # Fidelity banner: P0 reproduces every stored claim within the stage's
        # own 2-dp rounding (the 29 graded rows plus the two October tapes
        # that carry recorded claims but grade nothing).
        self.assertIn("baseline P0 reproduction over 31 rows", self.err)
        self.assertRegex(self.err, r"worst \|Δp10\| 0\.0000 INR")

    def test_the_proposal_quoted_depths_are_the_committed_reading(self) -> None:
        """The rows the proposal quotes: shallowest passing depth + residuals."""
        self.assertIn("| **P4** | 0.025 | 0.975 | **24 → 82.8%** | "
                      "21 → 91.3% | 3 → 50.0% |", self.out)
        self.assertIn("GB-2022-0002, GB-2025-0030, SCEN-001, SCEN-003, SCEN-008",
                      self.out)
        # The honest dead ends the proposal leans on.
        self.assertIn("60.9% (14/23)", self.out)   # re-scope ceiling
        # SCEN-001 closes at no finite depth below the observed maximum.
        self.assertIn("| SCEN-001 (RELIANCE) | tail | -2.23% .. +63.17% | P9 |",
                      self.out)

    def test_the_bd_pairing_tables_are_the_committed_reading(self) -> None:
        """Proposal §6: the P4 headline is **invariant** across every per-class
        atom rule (only width moves); below N=20 the P6 headline pays for the
        narrowing; the gate-4 N=30 row equals the committed baseline."""
        matrix = self.out.split("## B+D combined", 1)[1]
        matrix = matrix.split("### At the proposal depths", 1)[0]
        p4 = [line for line in matrix.splitlines()
              if line.startswith("| P4 ")]
        self.assertEqual(len(p4), 1, "one P4 line in the depth × rule matrix")
        self.assertEqual(p4[0].count("82.8%"), 5,
                         "P4 headline must read 82.8% under all five rules "
                         f"(N=30/20/10/5/1): {p4[0]}")

        detail = self.out.split("### At the proposal depths", 1)[1]
        detail = detail.split("### Tail slice only", 1)[0]
        n30 = [line for line in detail.splitlines()
               if line.startswith("| P6 | N=30 ")]
        self.assertEqual(len(n30), 1)
        self.assertIn("27 → 93.1%", n30[0])
        self.assertNotIn("GB-2022-0002", n30[0])
        n10 = [line for line in detail.splitlines()
               if line.startswith("| P6 | N=10 ")]
        self.assertEqual(len(n10), 1)
        self.assertIn("26 → 89.7%", n10[0])
        self.assertIn("GB-2022-0002", n10[0],
                      "the N≤10 regression must stay named")

        gate4 = self.out.split("## B+D on gate 4", 1)[1]
        anchor = [line for line in gate4.splitlines()
                  if line.startswith("| N=30 ")]
        self.assertEqual(len(anchor), 1)
        self.assertIn("0.410", anchor[0])
        self.assertIn("equals the committed baseline", anchor[0])
        n5 = [line for line in gate4.splitlines()
              if line.startswith("| N=5 ")]
        self.assertEqual(len(n5), 1)
        self.assertIn("0.406", n5[0])

    def test_the_tail_slice_per_rule_prices_b_and_bp(self) -> None:
        """Proposal §6.5: the B / B′ tail price per rule — P4's tail is fully
        rule-invariant (3/6, same three ids); P6 lifts 66.7% → 83.3% only at
        N=1 (SCEN-008 on the 4-atom block the atom rule refuses)."""
        sec = self.out.split("### Tail slice only", 1)[1]
        sec = sec.split("## B+D on gate 4", 1)[0]
        rows = [line for line in sec.splitlines() if line.startswith("| P")]
        self.assertEqual(len(rows), 10, "P4 and P6 × five rules")
        for line in rows[:5]:     # P4 × N=30/20/10/5/1
            self.assertIn("**3/6 → 50.0%**", line)
            self.assertTrue(line.endswith("SCEN-001, SCEN-003, SCEN-008 |"),
                            f"P4 tail ids moved under a rule: {line}")
        for line in rows[5:9]:    # P6 × N=30/20/10/5
            self.assertIn("**4/6 → 66.7%**", line)
            self.assertTrue(line.endswith("SCEN-001, SCEN-008 |"),
                            f"P6 tail ids moved under a rule: {line}")
        self.assertIn("**5/6 → 83.3%**", rows[9])  # P6 × N=1
        self.assertTrue(rows[9].endswith("SCEN-001 |"))

    def test_the_tail_stress_ladder_prices_q3_at_every_depth(self) -> None:
        """Proposal §2's ladder: the tail slice at its own floor per depth, the
        held-out ids per depth (the flip chain) and a headline column that
        agrees with the envelope table. The tool asserts monotonicity itself
        as the levels nest."""
        ladder = self.out.split("### Tail stress ladder", 1)[1]
        ladder = ladder.split("## The re-scope lever", 1)[0]
        rows = [line for line in ladder.splitlines()
                if line.startswith("| P")]
        self.assertEqual(len(rows), 10, "one ladder row per depth P0..P9")
        self.assertIn("| P0 | **2/6 → 33.3%** | 33.3% | "
                      "SCEN-001, SCEN-003, SCEN-004, SCEN-008 | 55.2% |",
                      ladder)
        self.assertIn("| P4 | **3/6 → 50.0%** | 50.0% | "
                      "SCEN-001, SCEN-003, SCEN-008 | 82.8% |", ladder)
        self.assertIn("| P6 | **4/6 → 66.7%** | 66.7% | "
                      "SCEN-001, SCEN-008 |", ladder)
        self.assertIn("| P8 | **5/6 → 83.3%** | 83.3% | SCEN-001 |", ladder)
        self.assertIn("| P9 | **6/6 → 100.0%** | 100.0% | — | 100.0% |",
                      ladder)

    def test_the_pairwise_side_by_side_stays_flag_only_and_pinned(self) -> None:
        """Proposal §6.4's one-off: absent from the default output (the pinned
        tables are unaffected), and under --pairwise exactly three rows switch
        pools at N=20 — all with unchanged containment — with
        `GB-2025-0030` the sole own-block-vs-pooled verdict disagreement."""
        self.assertNotIn("One-off side-by-side", self.out)
        proc = subprocess.run([sys.executable, str(SWEEP), "--pairwise"],
                              capture_output=True, check=False,
                              cwd=TESTS_DIR, timeout=600)
        if proc.returncode != 0:
            raise AssertionError(
                f"--pairwise exited {proc.returncode}:\n"
                f"{proc.stderr.decode('utf-8', 'replace')[-2000:]}")
        out = proc.stdout.decode("utf-8")
        self.assertIn("## One-off side-by-side", out)
        section = out.split("## One-off side-by-side", 1)[1]
        switched = [line.split("|")[1].split("(")[0].strip()
                    for line in section.splitlines()
                    if line.startswith("|") and "**yes**" in line]
        self.assertEqual(switched,
                         ["GB-2022-0010", "GB-2022-0011", "GB-2022-0014"])
        disagreements = []
        for line in section.splitlines():
            cells = [c.strip() for c in line.split("|")]
            if len(cells) >= 8 and ("✅" in cells[4] or "✗" in cells[4]) \
                    and ("✅" in cells[5] or "✗" in cells[5]):
                if ("✅" in cells[4]) != ("✅" in cells[5]):
                    disagreements.append(cells[1].split(" (")[0])
        self.assertEqual(disagreements, ["GB-2025-0030"])


class TestTheStagedSignatureTextIsLive(unittest.TestCase):
    """§8's staged signature text is paste-ready *today*: every `find`
    string occurs exactly once in the live file it will edit. If any target
    drifts before the owner signs, the paste would be a silent no-op — this
    fails in the ordinary suite instead. Signing retires these pins in the
    commit that applies the blocks (the §9 re-cut)."""

    def test_every_staged_find_string_matches_its_live_file_once(self) -> None:
        for rel, needle in STAGED_FIND_STRINGS:
            text = (TESTS_DIR.parent / rel).read_text(encoding="utf-8")
            self.assertEqual(
                text.count(needle), 1,
                f"{rel}: staged find-string must occur exactly once — "
                f"re-cut the staged block in the proposal: "
                f"<<{needle[:70]}…>>")

    def test_every_staged_find_string_is_carried_verbatim_by_the_proposal(
            self) -> None:
        proposal = PROPOSAL.read_text(encoding="utf-8")
        for heading in ("### Signing Q2 —", "### Signing Q3 —",
                        "### Signing Q4 —"):
            self.assertIn(heading, proposal)
        for rel, needle in STAGED_FIND_STRINGS:
            self.assertIn(
                needle, proposal,
                f"the pin for {rel} no longer appears verbatim in the "
                f"proposal's staged text — update both together")


if __name__ == "__main__":
    unittest.main()