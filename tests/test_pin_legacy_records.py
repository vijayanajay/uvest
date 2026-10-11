"""Seam — the legacy-record migration tool (`tests/evals/pin_legacy_records.py`).

The tool is the only writer allowed to turn a pre-proof golden record into a
proof-root row, so its safety properties are frozen here instead of living in an
operator's memory:

1. **A dry run writes nothing.** The planning path (tape → geometry decision →
   derivations) runs to completion and the proof root plus dataset bytes are
   untouched.
2. **Re-running is idempotent.** A record the tool already migrated is skipped:
   its geometry decision (published levels kept vs re-derived at the class shape)
   is not recoverable from its own migrated levels, so a silent recomputation
   would rewrite the disclosure.
3. **An unknown record is rejected** (exit 2), never silently ignored.
4. **The planning path obeys the frozen policy** — the corroboration rule, the
   class shape, the frozen derivation rules in `dataset_refs.py`, and the
   quadrant mapping — verified against a real record's cached tape.

No network is touched: the tape the plan reads (`tests/fixtures/history/`) is
committed with the repo, like every other curation input.
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
TOOL = TESTS_DIR / "evals" / "pin_legacy_records.py"
GOLDEN = TESTS_DIR / "evals" / "golden_dataset.json"

sys.path.insert(0, str(TESTS_DIR / "evals"))

import curate  # noqa: E402  (frozen ATR / outcome conventions)
import dataset_refs as dr  # noqa: E402  (the frozen derivation rules)
import pin_legacy_records as plr  # noqa: E402  (the tool under test)

# A real golden record with a committed tape and no proof row yet: the tool's
# planning path stays exercised by a record the migration has not consumed.
PLANNED_RECORD = "GB-2022-0006"


def run_tool(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True, check=False, cwd=REPO_ROOT,
    )


def fingerprint() -> str:
    """One digest over the proof root and the dataset (what a dry run may not move)."""
    digest = hashlib.sha256()
    for path in sorted(p for p in PROOF_ROOT.rglob("*") if p.is_file()):
        digest.update(path.relative_to(PROOF_ROOT).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    digest.update(GOLDEN.read_bytes())
    return digest.hexdigest()


class TestMigrationToolContract(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not TOOL.exists():
            raise AssertionError(f"migration tool missing: {TOOL}")
        cls.dataset = json.loads(GOLDEN.read_text(encoding="utf-8"))
        cls.trades = {t["trade_id"]: t for t in cls.dataset["trades"]}

    def test_actionable_rule_is_structural_and_lookahead_free(self):
        """A long advisory is live at T_rec iff its stop is under the market and
        its target above it; anything else means the levels belong to a price
        scale the tape cannot corroborate (never a reason to tune them)."""
        levels = {"entry_price": 100.0, "stop_loss": 90.0, "target_price": 120.0}
        self.assertTrue(plr.actionable(levels, 101.0))
        self.assertTrue(plr.actionable(levels, 100.0))
        self.assertFalse(plr.actionable(levels, 89.0), "stop already breached")
        self.assertFalse(plr.actionable(levels, 121.0), "target already passed")
        self.assertFalse(
            plr.actionable({"entry_price": 100.0, "stop_loss": 110.0,
                            "target_price": 120.0}, 105.0),
            "a degenerate long geometry is never actionable")

    def test_incoherent_legacy_levels_are_rebased_at_the_class_shape(self):
        """The `GB-2022-0001` case, replayed from its committed tape.

        Levels two-and-a-bit times the tape's scale (a stop above every bar the
        tape prints) are re-derived at the record's frozen class shape — the
        published numbers are superseded and disclosed, never kept and graded.
        """
        legacy = {
            "trade_id": "GB-2022-0001", "trade_class": "clean_bullish",
            "symbol": "RELIANCE", "instrument": "FUT",
            "recommendation_date": "2022-04-11",
            "entry_price": 2774.0, "stop_loss": 2704.0, "target_price": 2915.0,
        }
        bundle = plr.migrate(legacy, "2026-10-10")
        row = bundle["row"]
        self.assertEqual(bundle["geometry"]["basis"], "rebased_from_tape")
        prefix, _forward = curate.split_history(
            curate.load_history("RELIANCE", "2022-04-11"), "2022-04-11")
        shape = plr.CLASS_PLAN["clean_bullish"][0]
        entry = round(prefix[-1]["close"], 2)
        stop = round(entry - shape["risk_atr"] * curate.atr14_simple_mean(prefix), 2)
        target = round(entry + shape["rr"] * (entry - stop), 2)
        self.assertEqual((row["entry_price"], row["stop_loss"], row["target_price"]),
                         (entry, stop, target))
        self.assertEqual(row["authoring"]["geometry_basis"], "rebased_from_tape")
        self.assertEqual(row["authoring"]["class_shape"],
                         {"risk_atr": shape["risk_atr"], "rr": shape["rr"]})
        self.assertIn("cannot corroborate", row["authoring"]["note"])
        self.assertEqual(row["scoring"]["quadrant_target"],
                         plr.OUTCOME_QUADRANT[("GO", row["scoring"]["resolution_outcome"])])

    def test_dry_run_writes_nothing(self):
        """Planning is free: without `--apply` the evidence tree does not move.

        The batch selector resolves `unpinned` against the live dataset, so this
        asserts write-safety in whichever state the repo is in: a printed plan
        while records remain, and a clean no-op once every record is pinned.
        """
        manifest = json.loads(
            (PROOF_ROOT / "scenario_manifest.json").read_text(encoding="utf-8"))
        pinned = {r["scenario_id"] for r in manifest["scenarios"]}
        unpinned = [tid for tid in self.trades if tid not in pinned]
        before = fingerprint()
        proc = run_tool("--records", "unpinned")
        stdout = proc.stdout.decode("utf-8", "replace")
        self.assertEqual(proc.returncode, 0, stdout + proc.stderr.decode("utf-8", "replace"))
        if unpinned:
            self.assertIn("DRY RUN", stdout)
            self.assertIn(unpinned[0], stdout, "the plan must name the record it would migrate")
        else:
            self.assertIn("every record is pinned", stdout)
        self.assertEqual(fingerprint(), before, "a dry run wrote to the evidence tree")

    def test_already_migrated_records_are_skipped(self):
        """Idempotence: re-running cannot rewrite a geometry disclosure."""
        before = fingerprint()
        proc = run_tool("--records", "GB-2022-0001")
        stdout = proc.stdout.decode("utf-8", "replace")
        self.assertEqual(proc.returncode, 0, stdout + proc.stderr.decode("utf-8", "replace"))
        self.assertIn("already migrated", stdout)
        self.assertIn("nothing to do", stdout)
        self.assertEqual(fingerprint(), before)

    def test_unknown_record_is_rejected(self):
        proc = run_tool("--records", "GB-1999-9999")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("not in the golden dataset",
                      proc.stderr.decode("utf-8", "replace"))

    def test_plan_obeys_the_frozen_geometry_and_derivation_policy(self):
        """The planned row is a consequence of the tape, not of the claim.

        Independently of the tool's own bookkeeping: the corroboration rule
        decides whether the published levels survive, the derivation rules in
        `dataset_refs.py` reproduce every forward number, the basis matches an
        independent bar scan, and the quadrant follows the class verdict.
        """
        trade = self.trades[PLANNED_RECORD]
        bundle = plr.migrate(trade, "2026-10-10")
        row, geometry = bundle["row"], bundle["geometry"]

        prefix, forward = curate.split_history(
            curate.load_history(trade["symbol"], trade["recommendation_date"]),
            trade["recommendation_date"])
        close = round(prefix[-1]["close"], 2)
        self.assertEqual(bundle["close"], close)
        self.assertEqual(len(forward), curate.FORWARD)

        published = {"entry_price": float(trade["entry_price"]),
                     "stop_loss": float(trade["stop_loss"]),
                     "target_price": float(trade["target_price"])}
        # The rule is restated here on purpose: calling the tool's own predicate
        # would make the seam tautological (a mutated policy would agree with it).
        corroborated = (published["stop_loss"] < published["entry_price"]
                        < published["target_price"]
                        and published["stop_loss"] < close < published["target_price"])
        self.assertEqual(geometry["basis"],
                         "preserved_as_published" if corroborated else "rebased_from_tape")
        if corroborated:
            self.assertEqual((row["entry_price"], row["stop_loss"], row["target_price"]),
                             (published["entry_price"], published["stop_loss"],
                              published["target_price"]))
        else:
            shape = plr.CLASS_PLAN[trade["trade_class"]][0]
            atr = curate.atr14_simple_mean(prefix)
            entry = round(close, 2)
            stop = round(entry - shape["risk_atr"] * atr, 2)
            risk = entry - stop
            target = round(entry + (shape["rr"] * risk if shape["rr"] else 2.5 * risk), 2)
            self.assertEqual((row["entry_price"], row["stop_loss"], row["target_price"]),
                             (entry, stop, target))
        self.assertEqual(row["lot_size"], 1, "contract specifics must not be asserted")
        self.assertEqual(row["instrument"], "CASH")

        scoring = row["scoring"]
        self.assertEqual(scoring["bars"], curate.FORWARD)
        self.assertEqual(scoring["resolution_date"], forward[-1]["date"])
        self.assertEqual(scoring["forward_derived_cache"],
                         {"min": min(b["low"] for b in forward), "max": max(b["high"] for b in forward)})
        # Every forward claim is a `$derive` referencing this row's own tape.
        tape_rel = f"tests/proof/{scoring['forward_tape']}"
        for field, rule in (("forward_prices_22d", "forward_min_max"),
                            ("resolution_date", "resolution_date"),
                            ("resolution_outcome", "resolution_outcome"),
                            ("market_mae_pct", "market_mae_pct"),
                            ("actual_direction", "realized_direction")):
            spec = bundle["derived"][field][0]["$derive"]
            self.assertEqual((spec["tape"], spec["rule"]), (tape_rel, rule))
            self.assertEqual(
                bundle["derived"][field][1],
                dr.FORWARD_TAPE_RULES[rule](forward, {"entry_price": row["entry_price"],
                                                      "stop_loss": row["stop_loss"],
                                                      "target_price": row["target_price"]}),
                f"{field} does not follow the frozen rule")
        # The basis and quadrant describe the same scan the frozen rule performs.
        outcome, basis = plr.outcome_of(forward, row["stop_loss"], row["target_price"])
        self.assertEqual(scoring["resolution_outcome"], outcome)
        self.assertEqual(scoring["resolution_basis"], basis)
        verdict = plr.CLASS_PLAN[trade["trade_class"]][1]
        self.assertEqual(row["expected_verdict"], verdict)
        self.assertEqual(scoring.get("quadrant_target"),
                         plr.OUTCOME_QUADRANT.get((verdict, outcome)))


if __name__ == "__main__":
    unittest.main()
