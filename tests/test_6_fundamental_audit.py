"""TICK-006 seam — scripts/6_fundamental_audit.py

Stage 6 owns the Fundamental Quality & Corporate Governance Auditor (BRD §6,
§9.1 row 6):

* Piotroski F-Score (0–9) and the BRD §6.1 #1 `<= 4` score penalty;
* Altman Z''-score with its distress/grey/safe zones;
* Beneish M-score earnings-manipulation screen (red flag above −1.78);
* CFO/PAT cash-flow quality (accrual warning below 0.6);
* the Indian governance kill switches (promoter pledge > 25% is a hard veto,
  BRD §6.3 #1 / §10.3 #1) and the corporate-calendar hazards (earnings < 7 days
  on Futures → AVOID, < 10 days on Cash → 50% size cap, BRD §6.4 / §10.3 #4);
* the tri-state waiver path (BRD §10.4 / ADR-003): absent statements yield
  `waived: true` with null metrics and exit 0 — never a fabricated zero.

Runtime strategy (tests/support/pipeline.py): in-process import for the behavior
contrats, exactly one real subprocess for the CLI contract. Zero network
dependencies: the statements come from a recorded fixture.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from support.pipeline import (  # noqa: E402
    call_imported_main,
    import_mode,
    subprocess_mode,
    temp_input,
)

SCRIPT = REPO_ROOT / "scripts" / "6_fundamental_audit.py"
FIXTURE_FUNDAMENTALS = TESTS_DIR / "fixtures" / "reliance_fundamentals.json"

CANONICAL = {
    "symbol": "RELIANCE",
    "exchange": "NSE",
    "instrument_type": "FUT",
    "action": "BUY",
    "entry_price": 2980.50,
    "entry_range_min": 2975.00,
    "entry_range_max": 2985.00,
    "live_price": 2988.20,
    "target": 3120.00,
    "stoploss": 2910.00,
    "lot_size": 250,
    "recommended_date": "2026-10-09",
    "user_capital_inr": 500000.00,
}

# The TICK-006 §2 canonical output contract, verbatim.
CONTRACT_KEYS = {
    "piotoski_score", "altman_z_score", "promoter_pledge_pct",
    "pledge_circuit_breaker", "cfo_pat_ratio", "days_to_earnings",
    "earnings_hazard_flag", "waived",
}

STRONG_CURRENT = {
    "revenue": 120000.0, "gross_profit": 33600.0, "ebit": 19500.0,
    "net_income": 12000.0, "cfo": 15600.0, "receivables": 5400.0,
    "current_assets": 33000.0, "current_liabilities": 24000.0, "ppe": 88000.0,
    "depreciation": 6000.0, "sga_expense": 11400.0, "long_term_debt": 18000.0,
    "total_liabilities": 48000.0, "total_assets": 150000.0,
    "book_equity": 102000.0, "retained_earnings": 51000.0,
    "shares_outstanding": 1000.0,
}

STRONG_PRIOR = {
    "revenue": 100000.0, "gross_profit": 25000.0, "ebit": 14000.0,
    "net_income": 9000.0, "cfo": 12000.0, "receivables": 5000.0,
    "current_assets": 30000.0, "current_liabilities": 25000.0, "ppe": 80000.0,
    "depreciation": 5000.0, "sga_expense": 10000.0, "long_term_debt": 20000.0,
    "total_liabilities": 50000.0, "total_assets": 140000.0,
    "book_equity": 90000.0, "retained_earnings": 45000.0,
    "shares_outstanding": 1000.0,
}

# Every Piotroski signal fails: ROA negative and worse, CFO negative and below
# the reported loss, leverage up, current ratio down, shares diluted, margins
# and turnover down — the F=0 shape BRD §6.1 #1's penalty exists for.
WEAK_CURRENT = {
    "revenue": 80000.0, "gross_profit": 10000.0, "ebit": -8000.0,
    "net_income": -500.0, "cfo": -600.0, "receivables": 12000.0,
    "current_assets": 50000.0, "current_liabilities": 70000.0, "ppe": 60000.0,
    "depreciation": 4000.0, "sga_expense": 9000.0, "long_term_debt": 50000.0,
    "total_liabilities": 90000.0, "total_assets": 100000.0,
    "book_equity": 10000.0, "retained_earnings": -5000.0,
    "shares_outstanding": 1100.0,
}

WEAK_PRIOR = {
    "revenue": 90000.0, "gross_profit": 18000.0, "ebit": 4000.0,
    "net_income": 1000.0, "cfo": 1200.0, "receivables": 9000.0,
    "current_assets": 40000.0, "current_liabilities": 50000.0, "ppe": 55000.0,
    "depreciation": 3000.0, "sga_expense": 8000.0, "long_term_debt": 30000.0,
    "total_liabilities": 60000.0, "total_assets": 100000.0,
    "book_equity": 40000.0, "retained_earnings": 5000.0,
    "shares_outstanding": 1000.0,
}

GOVERNANCE_CLEAN = {
    "promoter_pledged_pct": 4.2,
    "promoter_holding_pct": {"q_minus_2": 50.4, "q_latest": 50.1},
    "holding_change_disclosed": False,
    "fii_pct": {"q_minus_3": 24.0, "q_latest": 23.4},
    "dii_pct": {"q_minus_3": 15.0, "q_latest": 15.6},
    "retail_pct": {"q_minus_3": 40.0, "q_latest": 41.5},
    "auditor_resigned_within_12m": False,
    "auditor_resignation_routine_rotation": True,
    "earnings": {"sessions_to_earnings": 25},
}


def make_input(**overrides):
    payload = dict(CANONICAL)
    payload.update(overrides)
    return payload


def statements(current=None, prior=None, governance=None, earnings=None):
    """A statement bundle with the given overrides layered on the clean shapes."""
    bundle = {
        "schema": "uvest-fundamentals/1",
        "symbol": "RELIANCE",
        "as_of": "2026-10-09",
        "statement_fy_current": {**STRONG_CURRENT, **(current or {})},
        "statement_fy_prior": {**STRONG_PRIOR, **(prior or {})},
        "governance": {**GOVERNANCE_CLEAN, **(governance or {})},
    }
    bundle["governance"].pop("earnings", None)
    # An explicit `{}` means "the calendar is unknown", not "use the default".
    bundle["earnings"] = {"sessions_to_earnings": 25} if earnings is None else earnings
    return bundle


def run_stage6(payload: dict, financials: dict | Path | None = FIXTURE_FUNDAMENTALS
               ) -> tuple[dict, int, str]:
    """Import and run stage 6 in-process against a bundle, a path, or nothing.

    A dict is written to a temp file (the CLI contract is a path), `None`
    exercises the waiver path, and a `Path` is passed through untouched.
    """
    module, _ = import_mode(6)
    with temp_input(payload, ".json") as path:
        if not isinstance(financials, dict) and financials is not None:
            return call_imported_main(module, ["--input", path,
                                               "--financials-fixture",
                                               str(financials)])
        with tempfile.TemporaryDirectory() as tmp:
            argv = ["--input", path]
            if financials is not None:
                fixture = Path(tmp) / "fundamentals.json"
                fixture.write_text(json.dumps(financials), encoding="utf-8")
                argv += ["--financials-fixture", str(fixture)]
            return call_imported_main(module, argv)


class TestFinancialHealthScores(unittest.TestCase):
    """BRD §6.1: the quality scores every later module is scored against."""

    def test_strong_statements_earn_a_full_piotroski_score(self):
        out, code, err = run_stage6(make_input(), statements())
        self.assertEqual(code, 0, err)
        self.assertFalse(out["waived"])
        self.assertEqual(out["piotoski_score"], 9)
        self.assertEqual(len(out["piotoski_signals"]), out["piotoski_score"])
        self.assertEqual(out["piotoski_penalty"], 0.0)
        self.assertEqual(out["missing_inputs"], [])

    def test_weak_statements_score_zero_and_trip_the_penalty(self):
        """BRD §6.1 #1: `F <= 4` costs the composite model 15 points."""
        out, code, err = run_stage6(
            make_input(), statements(current=WEAK_CURRENT, prior=WEAK_PRIOR))
        self.assertEqual(code, 0, err)
        self.assertEqual(out["piotoski_score"], 0)
        self.assertLessEqual(out["piotoski_score"], 4)
        self.assertEqual(out["piotoski_penalty"], -15.0)
        self.assertEqual(out["piotoski_signals"], [])

    def test_altman_z_double_prime_and_its_zones(self):
        out, code, err = run_stage6(make_input(), statements())
        self.assertEqual(code, 0, err)
        # X1=0.06, X2=0.34, X3=0.13, X4=2.125 → 6.56·X1+3.26·X2+6.72·X3+1.05·X4
        self.assertAlmostEqual(out["altman_z_score"], 4.6068, places=4)
        self.assertEqual(out["altman_zone"], "SAFE")

        module, _ = import_mode(6)
        self.assertEqual(module.altman_zone(1.05), "DISTRESS")
        self.assertEqual(module.altman_zone(1.1), "GREY")
        self.assertEqual(module.altman_zone(2.6), "GREY")
        self.assertEqual(module.altman_zone(2.61), "SAFE")
        self.assertIsNone(module.altman_zone(None))

    def test_insolvent_statements_land_in_the_distress_zone(self):
        out, code, err = run_stage6(
            make_input(), statements(current=WEAK_CURRENT, prior=WEAK_PRIOR))
        self.assertEqual(code, 0, err)
        self.assertEqual(out["altman_zone"], "DISTRESS")
        self.assertLess(out["altman_z_score"], 1.1)

    def test_beneish_screen_and_cash_flow_quality(self):
        out, code, err = run_stage6(make_input(), statements())
        self.assertEqual(code, 0, err)
        self.assertAlmostEqual(out["beneish_m_score"], -2.5602, places=4)
        self.assertFalse(out["beneish_red_flag"])
        self.assertAlmostEqual(out["cfo_pat_ratio"], 1.3, places=4)
        self.assertFalse(out["accrual_warning"])

    def test_accrual_warning_when_cash_conversion_is_poor(self):
        """BRD §6.2: CFO/PAT < 0.6 is paper profit on uncollected receivables."""
        out, code, err = run_stage6(
            make_input(), statements(current={"cfo": 4800.0}))
        self.assertEqual(code, 0, err)
        self.assertAlmostEqual(out["cfo_pat_ratio"], 0.4, places=4)
        self.assertTrue(out["accrual_warning"])

    def test_beneish_red_flag_on_manipulation_shape(self):
        """Window dressing: sales inflated while receivables outrun them."""
        out, code, err = run_stage6(
            make_input(),
            statements(current={"revenue": 180000.0, "receivables": 18000.0,
                                "gross_profit": 25000.0}))
        self.assertEqual(code, 0, err)
        self.assertGreater(out["beneish_m_score"], -1.78)
        self.assertTrue(out["beneish_red_flag"])


class TestGovernanceKillSwitches(unittest.TestCase):
    """BRD §6.3 / §10.3 #1 — the promoter-pledge circuit breaker."""

    def test_clean_pledge_is_neither_warning_nor_breaker(self):
        out, code, err = run_stage6(make_input(), statements())
        self.assertEqual(code, 0, err)
        self.assertAlmostEqual(out["promoter_pledge_pct"], 4.2, places=4)
        self.assertFalse(out["promoter_pledge_warning"])
        self.assertFalse(out["pledge_circuit_breaker"])

    def test_pledge_above_fifteen_warns_without_tripping_the_breaker(self):
        out, code, err = run_stage6(
            make_input(), statements(governance={"promoter_pledged_pct": 18.5}))
        self.assertEqual(code, 0, err)
        self.assertTrue(out["promoter_pledge_warning"])
        self.assertFalse(out["pledge_circuit_breaker"])

    def test_pledge_breaker_is_strictly_greater_than_25_percent(self):
        """The frozen boundary: exactly 25.0% does not trip, 25.01% does."""
        for pledge, expected in ((25.0, False), (25.01, True), (60.0, True)):
            with self.subTest(pledge=pledge):
                out, code, err = run_stage6(
                    make_input(),
                    statements(governance={"promoter_pledged_pct": pledge}))
                self.assertEqual(code, 0, err)
                self.assertEqual(out["pledge_circuit_breaker"], expected)
                self.assertTrue(out["promoter_pledge_warning"])

    def test_missing_pledge_disclosure_stays_tri_state(self):
        """Absence is null, never `false` — the choice that forces AVOID reads it."""
        out, code, err = run_stage6(
            make_input(), statements(governance={"promoter_pledged_pct": None}))
        self.assertEqual(code, 0, err)
        self.assertIsNone(out["promoter_pledge_pct"])
        self.assertIsNone(out["promoter_pledge_warning"])
        self.assertIsNone(out["pledge_circuit_breaker"])

    def test_promoter_holding_decline_flags_insider_selling(self):
        sold = {"promoter_holding_pct": {"q_minus_2": 50.4, "q_latest": 47.9},
                "holding_change_disclosed": False}
        out, code, err = run_stage6(make_input(), statements(governance=sold))
        self.assertEqual(code, 0, err)
        self.assertAlmostEqual(out["promoter_holding_decline_pct"], 2.5, places=4)
        self.assertTrue(out["insider_selling_flag"])

        disclosed = {**sold, "holding_change_disclosed": True}
        out, code, err = run_stage6(make_input(),
                                         statements(governance=disclosed))
        self.assertEqual(code, 0, err)
        self.assertFalse(out["insider_selling_flag"],
                         "a disclosed OFS/capex explains the decline away")

        calm = {"promoter_holding_pct": {"q_minus_2": 50.4, "q_latest": 50.1}}
        out, _code, _err = run_stage6(make_input(), statements(governance=calm))
        self.assertFalse(out["insider_selling_flag"])

    def test_retail_absorbing_fii_and_dii_selling_is_a_divergence(self):
        out, code, err = run_stage6(
            make_input(),
            statements(governance={"fii_pct": {"q_minus_3": 24.0, "q_latest": 22.0},
                                   "dii_pct": {"q_minus_3": 15.0, "q_latest": 14.0},
                                   "retail_pct": {"q_minus_3": 40.0, "q_latest": 45.0}}))
        self.assertEqual(code, 0, err)
        self.assertTrue(out["ownership_divergence_flag"])

        out, _code, _err = run_stage6(make_input(), statements())
        self.assertFalse(out["ownership_divergence_flag"])

    def test_non_routine_auditor_resignation_is_flagged(self):
        out, code, err = run_stage6(
            make_input(),
            statements(governance={"auditor_resigned_within_12m": True,
                                   "auditor_resignation_routine_rotation": False}))
        self.assertEqual(code, 0, err)
        self.assertTrue(out["auditor_resignation_flag"])

        routine = statements(governance={"auditor_resigned_within_12m": True,
                                         "auditor_resignation_routine_rotation": True})
        out, _code, _err = run_stage6(make_input(), routine)
        self.assertFalse(out["auditor_resignation_flag"])


class TestEarningsCalendarHazard(unittest.TestCase):
    """BRD §6.4 / §10.3 #4 — the corporate-calendar gate."""

    def test_futures_inside_seven_days_is_a_hazard(self):
        out, code, err = run_stage6(
            make_input(instrument_type="FUT"),
            statements(earnings={"sessions_to_earnings": 3}))
        self.assertEqual(code, 0, err)
        self.assertEqual(out["days_to_earnings"], 3)
        self.assertTrue(out["earnings_hazard_flag"])
        self.assertIsNone(out["earnings_size_cap_pct"],
                          "AVOID dominates; a size cap on a vetoed trade is noise")

    def test_futures_at_exactly_seven_days_is_clear(self):
        out, code, err = run_stage6(
            make_input(instrument_type="FUT"),
            statements(earnings={"sessions_to_earnings": 7}))
        self.assertEqual(code, 0, err)
        self.assertFalse(out["earnings_hazard_flag"])

    def test_cash_equity_inside_ten_days_is_capped_not_vetoed(self):
        out, code, err = run_stage6(
            make_input(instrument_type="CASH"),
            statements(earnings={"sessions_to_earnings": 5}))
        self.assertEqual(code, 0, err)
        self.assertFalse(out["earnings_hazard_flag"])
        self.assertEqual(out["earnings_size_cap_pct"], 50)

    def test_cash_equity_outside_ten_days_has_no_cap(self):
        out, code, err = run_stage6(
            make_input(instrument_type="CASH"),
            statements(earnings={"sessions_to_earnings": 12}))
        self.assertEqual(code, 0, err)
        self.assertFalse(out["earnings_hazard_flag"])
        self.assertIsNone(out["earnings_size_cap_pct"])

    def test_unknown_calendar_stays_tri_state(self):
        out, code, err = run_stage6(
            make_input(), statements(earnings={}))
        self.assertEqual(code, 0, err)
        self.assertIsNone(out["days_to_earnings"])
        self.assertIsNone(out["earnings_hazard_flag"])
        self.assertIsNone(out["earnings_size_cap_pct"])

    def test_a_dated_calendar_is_counted_in_weekdays_and_says_so(self):
        """No session count of its own: the frozen Mon–Fri convention, disclosed."""
        bundle = statements(earnings={"next_earnings_date": "2026-10-14"})
        out, code, err = run_stage6(make_input(), bundle)
        self.assertEqual(code, 0, err)
        self.assertEqual(out["days_to_earnings"], 3)   # Mon 12th … Wed 14th
        self.assertEqual(out["days_to_earnings_basis"], "weekdays")
        self.assertTrue(out["earnings_hazard_flag"])


class TestWaiverAndTriState(unittest.TestCase):
    """BRD §10.4 / ADR-003: absence is disclosed and null, never a default."""

    def test_no_statements_waives_with_nulls_and_exit_zero(self):
        out, code, err = run_stage6(make_input(), None)
        self.assertEqual(code, 0, err)
        self.assertTrue(out["waived"])
        self.assertTrue(CONTRACT_KEYS.issubset(out))
        for key, value in out.items():
            if key in ("symbol", "waived"):
                continue
            self.assertIsNone(value, f"{key} must stay null on the waiver path")

    def test_unreadable_statements_are_absence_not_a_crash(self):
        """A missing/corrupt bundle is absence: the waiver path, not a traceback."""
        missing = TESTS_DIR / "fixtures" / "_no_such_bundle.json"
        out, code, err = run_stage6(make_input(), missing)
        self.assertEqual(code, 0, err)
        self.assertTrue(out["waived"])

    def test_partial_statements_are_null_metrics_with_named_gaps(self):
        """Never 0.0: a metric whose inputs are missing is null and auditable."""
        bundle = statements(current={"cfo": None, "receivables": None})
        out, code, err = run_stage6(make_input(), bundle)
        self.assertEqual(code, 0, err)
        self.assertFalse(out["waived"])
        self.assertIsNone(out["cfo_pat_ratio"])
        self.assertIsNone(out["accrual_warning"])
        self.assertIsNone(out["piotoski_score"])
        self.assertIn("statement_fy_current.cfo", out["missing_inputs"])
        self.assertIn("piotroski_signal.positive_cfo", out["missing_inputs"])
        # The signals that could be decided are still reported as earned.
        self.assertIn("positive_roa", out["piotoski_signals"])

    def test_contract_payload_carries_every_frozen_key(self):
        out, code, err = run_stage6(make_input())
        self.assertEqual(code, 0, err)
        self.assertTrue(CONTRACT_KEYS.issubset(out),
                        f"missing: {CONTRACT_KEYS - set(out)}")
        self.assertEqual(out["symbol"], "RELIANCE")


class TestCliContractAndRecording(unittest.TestCase):
    """One real subprocess: exit codes, stdout-only JSON, and byte-exactness."""

    def test_cli_contract_subprocess(self):
        """The frozen invocation: `python scripts/6_fundamental_audit.py --input …`."""
        with temp_input(CANONICAL, ".json") as path:
            proc = subprocess_mode(SCRIPT, [
                "--input", path,
                "--financials-fixture", str(FIXTURE_FUNDAMENTALS)])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["piotoski_score"], 9)
        self.assertFalse(payload["waived"])
        self.assertEqual(payload["symbol"], "RELIANCE")

    def test_byte_identical_payload_on_repeat_runs(self):
        with temp_input(CANONICAL, ".json") as path:
            first = subprocess_mode(SCRIPT, ["--input", path, "--financials-fixture",
                                             str(FIXTURE_FUNDAMENTALS)])
            second = subprocess_mode(SCRIPT, ["--input", path, "--financials-fixture",
                                              str(FIXTURE_FUNDAMENTALS)])
        self.assertNotEqual(first.returncode, 1)
        self.assertEqual(first.stdout, second.stdout)

    def test_record_flag_writes_the_exact_artifact(self):
        """--record <dir> writes out_6_fundamental.json byte-identical to stdout."""
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "bundle"
            with temp_input(CANONICAL, ".json") as path:
                proc = subprocess_mode(SCRIPT, [
                    "--input", path,
                    "--financials-fixture", str(FIXTURE_FUNDAMENTALS),
                    "--record", str(bundle)])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            artifact = bundle / "out_6_fundamental.json"
            self.assertTrue(artifact.exists(), "recorded stage 6 artifact missing")
            self.assertEqual(artifact.read_text(encoding="utf-8"), proc.stdout)

    def test_missing_symbol_is_a_parse_error(self):
        with temp_input({"instrument_type": "FUT"}, ".json") as path:
            proc = subprocess_mode(SCRIPT, ["--input", path])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("ERR_PARSE", proc.stderr)


if __name__ == "__main__":
    unittest.main()
