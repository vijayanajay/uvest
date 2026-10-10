"""TICK-005 seam — scripts/5_institutional_flow.py

Stage 5 owns the Institutional Order Flow & Footprint Engine (BRD §5.3, §5.4, §9.1 row 5):
* NSE Bhavcopy Delivery % and 20-day rolling Z-score
* Futures Open Interest (OI) buildup %
* The Holy Trinity Matrix classification:
    - Aggressive Long Buildup (Institutional Accumulation)
    - Retail Short-Covering Trap (Exhaustion Rally)
    - Short Buildup (Institutional Distribution)
    - Long Unwinding (Capitulation)
* Futures Basis & Annualized Carry Cost
* Waiver path (BRD §10.3): missing Bhavcopy yields `waived: true` with null metrics and exit 0.

Runtime strategy (support/pipeline.py):
* In-process module import for behavior & logic contracts
* Exactly one subprocess execution guarding the CLI contract
* Zero network dependencies: runs against offline fixtures.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from support.pipeline import call_imported_main, import_mode, subprocess_mode, temp_input  # noqa: E402

SCRIPT = REPO_ROOT / "scripts" / "5_institutional_flow.py"
FIXTURE_BHAVCOPY = TESTS_DIR / "fixtures" / "reliance_bhavcopy.json"

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


def make_input(**overrides):
    payload = dict(CANONICAL)
    payload.update(overrides)
    return payload


def run_stage5(payload: dict, fixture_path: Path | None = FIXTURE_BHAVCOPY):
    """Import and run stage 5 in-process with the fixture argument."""
    module, _ = import_mode(5)
    with temp_input(payload, ".json") as path:
        argv = ["--input", path]
        if fixture_path:
            argv.extend(["--bhavcopy-fixture", str(fixture_path)])
        return call_imported_main(module, argv)


class TestHolyTrinityMatrix(unittest.TestCase):
    """Validates the 4 regimes of the Holy Trinity Matrix (BRD §5.3)."""

    def test_long_buildup_classification(self):
        """Price UP + High Delivery (>50% or Z>=1.5) + Rising OI (>+5%) -> LONG_BUILDUP."""
        out, code, err = run_stage5(make_input())
        self.assertEqual(code, 0, err)
        self.assertFalse(out["waived"])
        self.assertEqual(out["matrix_verdict"], "LONG_BUILDUP")
        self.assertAlmostEqual(out["delivery_pct"], 56.4, places=1)
        self.assertGreater(out["delivery_zscore"], 1.5)
        self.assertGreater(out["oi_change_pct"], 5.0)

    def test_short_covering_trap_classification(self):
        """Price UP + Low Delivery (<30% or Z<=-1.0) + Falling OI (<-5%) -> SHORT_COVERING_TRAP."""
        trap_fixture = {
            "symbol": "RELIANCE",
            "as_of": "2026-10-09",
            "delivery_pct": 24.5,
            "delivery_mean_20d": 38.0,
            "delivery_std_20d": 8.0,
            "price_change_pct": 1.8,
            "oi_change_pct": -8.5,
            "spot_price": 2980.0,
            "futures_price": 2985.0,
            "days_to_expiry": 15,
        }
        with temp_input(trap_fixture, ".json") as fix_path:
            out, code, err = run_stage5(make_input(), fixture_path=Path(fix_path))
        self.assertEqual(code, 0, err)
        self.assertEqual(out["matrix_verdict"], "SHORT_COVERING_TRAP")
        self.assertLess(out["delivery_pct"], 30.0)
        self.assertLess(out["oi_change_pct"], -5.0)

    def test_short_buildup_classification(self):
        """Price DOWN + High Delivery + Rising OI -> SHORT_BUILDUP."""
        short_fixture = {
            "symbol": "RELIANCE",
            "as_of": "2026-10-09",
            "delivery_pct": 55.0,
            "delivery_mean_20d": 35.0,
            "delivery_std_20d": 8.0,
            "price_change_pct": -2.1,
            "oi_change_pct": 9.2,
            "spot_price": 2950.0,
            "futures_price": 2952.0,
            "days_to_expiry": 15,
        }
        with temp_input(short_fixture, ".json") as fix_path:
            out, code, err = run_stage5(make_input(), fixture_path=Path(fix_path))
        self.assertEqual(code, 0, err)
        self.assertEqual(out["matrix_verdict"], "SHORT_BUILDUP")

    def test_long_unwinding_classification(self):
        """Price DOWN + Low Delivery + Falling OI -> LONG_UNWINDING."""
        unwind_fixture = {
            "symbol": "RELIANCE",
            "as_of": "2026-10-09",
            "delivery_pct": 22.0,
            "delivery_mean_20d": 35.0,
            "delivery_std_20d": 8.0,
            "price_change_pct": -1.5,
            "oi_change_pct": -7.0,
            "spot_price": 2960.0,
            "futures_price": 2961.0,
            "days_to_expiry": 15,
        }
        with temp_input(unwind_fixture, ".json") as fix_path:
            out, code, err = run_stage5(make_input(), fixture_path=Path(fix_path))
        self.assertEqual(code, 0, err)
        self.assertEqual(out["matrix_verdict"], "LONG_UNWINDING")


class TestFuturesBasisAndCarry(unittest.TestCase):
    """Validates Basis calculation and annualized carry premium (BRD §5.4)."""

    def test_futures_basis_annualized(self):
        """Basis = Futures - Spot. Annualized = (Basis / Spot) * (365 / days_to_expiry) * 100."""
        out, code, err = run_stage5(make_input())
        self.assertEqual(code, 0, err)
        # Spot: 2981.70, Futures: 2988.20 -> Basis = 6.50
        # (6.50 / 2981.70) * (365 / 16.5) * 100 ≈ 4.82%
        self.assertIsNotNone(out["basis_annualized"])
        self.assertAlmostEqual(out["basis_annualized"], 4.82, places=1)

    def test_cash_equity_has_null_futures_basis(self):
        """Cash equity recommendations do not have a futures carrying cost."""
        cash_input = make_input(instrument_type="CASH")
        out, code, err = run_stage5(cash_input)
        self.assertEqual(code, 0, err)
        self.assertIsNone(out["basis_annualized"])


class TestWaiverAndDeterminism(unittest.TestCase):
    """Guarantees BRD §10.3 waiver semantics and determinism."""

    def test_missing_fixture_yields_waived_with_nulls_and_exit_0(self):
        """ADR-003 / BRD §10.3: Missing data yields waived: true with null metrics, never fabricated numbers."""
        out, code, err = run_stage5(make_input(), fixture_path=None)
        self.assertEqual(code, 0, err)
        self.assertTrue(out["waived"])
        self.assertIsNone(out["delivery_pct"])
        self.assertIsNone(out["delivery_zscore"])
        self.assertIsNone(out["oi_change_pct"])
        self.assertIsNone(out["matrix_verdict"])
        self.assertIsNone(out["basis_annualized"])

    def test_cli_contract_subprocess(self):
        """Guarantees canonical invocation via real Python subprocess."""
        with temp_input(CANONICAL, ".json") as path:
            proc = subprocess_mode(
                SCRIPT,
                ["--input", path, "--bhavcopy-fixture", str(FIXTURE_BHAVCOPY)],
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["matrix_verdict"], "LONG_BUILDUP")
        self.assertFalse(payload["waived"])

    def test_byte_identical_payload_on_repeat_runs(self):
        """Re-running twice over the same fixture produces identical stdout."""
        with temp_input(CANONICAL, ".json") as path:
            p1 = subprocess_mode(
                SCRIPT,
                ["--input", path, "--bhavcopy-fixture", str(FIXTURE_BHAVCOPY)],
            )
            p2 = subprocess_mode(
                SCRIPT,
                ["--input", path, "--bhavcopy-fixture", str(FIXTURE_BHAVCOPY)],
            )
        self.assertEqual(p1.stdout, p2.stdout)


if __name__ == "__main__":
    unittest.main()
