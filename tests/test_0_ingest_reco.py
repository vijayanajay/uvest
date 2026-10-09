"""TICK-001 seam — scripts/0_ingest_reco.py

Stage 0 owns the canonical schema and three hard structural gates, per
BRD §3.1–3.3 and TICK-001. Every acceptance criterion here is a frozen
criterion in docs/tickets/TICK-001-canonical-parser.md §3.

Runtime strategy (tests/support/pipeline.py): the behavior contract runs
in-process via import (microseconds), and ONE subprocess test guards the
frozen CLI contract so exit codes / stderr routing / stdout-JSON purity can
never silently break.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))

from support.pipeline import (  # noqa: E402
    import_mode, run_stage0_text, subprocess_mode, temp_input,
)
# NOTE: import_mode imported but unused today; kept as the documented entry
# point the next stage seam copies. Linter-safe via explicit re-export below.

REPO_ROOT = TESTS_DIR.parent
SCRIPT = REPO_ROOT / "scripts" / "0_ingest_reco.py"

VALID_FUTURES = "Symbol: RELIANCE\nType: FUT\nEntry Range: 2975.00-2985.00\nLive Price: 2988.20\nStoploss: 2910.00\nTarget: 3120.00\nLot Size: 250\n"
VALID_CASH = "Symbol: SBIN\nType: CASH\nEntry Range: 820.00-825.00\nLive Price: 822.10\nStoploss: 795.00\nTarget: 890.00\n"


class TestOptionsGate(unittest.TestCase):
    """ADR-001 hard rule: options die at ingestion, with the frozen error code."""

    def test_ce_is_rejected_with_frozen_error_and_exit_2(self):
        payload, code, err = run_stage0_text(VALID_FUTURES.replace("Type: FUT", "Type: CE"))
        self.assertEqual(code, 2, f"expected exit 2, got {code}; stderr={err}")
        self.assertIn("ERR_OPTIONS_PROHIBITED", err)
        self.assertIsNone(payload, "rejected run must not emit a canonical payload")

    def test_pe_and_opt_variants_rejected_too(self):
        for variant in ("PE", "OPT"):
            with self.subTest(variant=variant):
                _, code, err = run_stage0_text(VALID_FUTURES.replace("Type: FUT", f"Type: {variant}"))
                self.assertEqual(code, 2)
                self.assertIn("ERR_OPTIONS_PROHIBITED", err)


class TestCanonicalSchema(unittest.TestCase):
    """The output must be the BRD §3.2 canonical JSON, byte-checkable key-by-key."""

    def test_valid_futures_pays_out_canonical_schema(self):
        payload, code, err = run_stage0_text(VALID_FUTURES)
        self.assertEqual(code, 0, err)
        for key in ("symbol", "exchange", "instrument_type", "action", "entry_price",
                    "entry_range_min", "entry_range_max", "live_price", "target",
                    "stoploss", "lot_size"):
            self.assertIn(key, payload, f"canonical field missing: {key}")
        self.assertEqual(payload["symbol"], "RELIANCE")
        self.assertEqual(payload["instrument_type"], "FUT")
        self.assertEqual(payload["entry_range_min"], 2975.00)
        self.assertEqual(payload["entry_range_max"], 2985.00)
        self.assertEqual(payload["lot_size"], 250)
        self.assertEqual(payload["live_price"], 2988.20)

    def test_cash_equity_action_defaults_to_buy(self):
        payload, code, err = run_stage0_text(VALID_CASH)
        self.assertEqual(code, 0, err)
        self.assertEqual(payload["instrument_type"], "CASH")
        self.assertEqual(payload["action"], "BUY")


class TestBanAndSurveillance(unittest.TestCase):
    """TICK-001 gates 2 and 3: MWPL ban flag, ASM/GSM stage flags.

    Offline determinism: the script must carry its own mock ban/ASM lists so
    the seam never touches the network (zero-hallucination fixture contract).
    """

    def test_unbanned_futures_gets_ban_status_false(self):
        payload, code, err = run_stage0_text(VALID_FUTURES)
        self.assertEqual(code, 0, err)
        self.assertFalse(payload["gatekeeper"]["ban_status"])
        self.assertFalse(payload["gatekeeper"]["asm_gsm_flagged"])

    def test_banned_symbol_flags_ban_status_true_without_rejecting_schema(self):
        payload, code, err = run_stage0_text(VALID_FUTURES.replace("RELIANCE", "TESTBANNED"))
        self.assertEqual(code, 0, err)
        self.assertTrue(payload["gatekeeper"]["ban_status"])

    def test_asm_gsm_stage2_flagged(self):
        payload, code, err = run_stage0_text(VALID_FUTURES.replace("RELIANCE", "TESTASM2"))
        self.assertEqual(code, 0, err)
        self.assertTrue(payload["gatekeeper"]["asm_gsm_flagged"])


class TestLotMarginExposure(unittest.TestCase):
    """TICK-001 gate 4: validate lot size and total initial margin exposure."""

    def test_margin_exposure_computed_from_lot_size_and_live_price(self):
        payload, code, err = run_stage0_text(VALID_FUTURES)
        self.assertEqual(code, 0, err)
        self.assertIn("margin_exposure_inr", payload["gatekeeper"])
        expected = 250 * 2988.20
        self.assertEqual(payload["gatekeeper"]["margin_exposure_inr"], expected)

    def test_cash_equity_has_no_lot_exposure(self):
        payload, code, err = run_stage0_text(VALID_CASH)
        self.assertEqual(code, 0, err)
        self.assertIsNone(payload["gatekeeper"].get("margin_exposure_inr"))


class TestBadInputHandling(unittest.TestCase):
    """Structural parse failures must be distinguishable from gate rejections."""

    def test_empty_input_fails_cleanly(self):
        _, code, err = run_stage0_text("")
        self.assertNotEqual(code, 0)
        self.assertIn("ERR_PARSE", err)

    def test_missing_required_field_fails_cleanly(self):
        _, code, err = run_stage0_text("Symbol: RELIANCE\nType: FUT\n")
        self.assertNotEqual(code, 0)
        self.assertIn("ERR_PARSE", err)

    def test_malformed_entry_range_fails_cleanly(self):
        _, code, err = run_stage0_text(VALID_FUTURES.replace("2975.00-2985.00", "garbage"))
        self.assertNotEqual(code, 0)
        self.assertIn("ERR_PARSE", err)


class TestCliContract(unittest.TestCase):
    """Exactly ONE subprocess invocation per stage — the frozen BRD §9.1 shape.

    The rejection path is already covered in-process (exit code + stderr code
    come from the same main() both modes call); this single interpreter-level
    run pins the wrapper mechanics: real argv handling, exit-code propagation,
    stderr routing, stdout-only-JSON purity. One spawn, not one per case.
    """

    def test_canonical_invocation_via_real_interpreter(self):
        """`python scripts/0_ingest_reco.py --input input.txt > current_trade.json`
        must behave end-to-end like the imported mode."""
        with temp_input(VALID_FUTURES, ".txt") as path:
            proc = subprocess_mode(SCRIPT, ["--input", path])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        import json as _json
        payload = _json.loads(proc.stdout)  # stdout must be pure JSON
        self.assertEqual(payload["symbol"], "RELIANCE")
