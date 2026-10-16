"""TICK-011 seam — scripts/10_daily_triage.py

Stage 10 owns the trade store (`uvest_history.db`) and the macro-premise half
of the daily stewardship loop (BRD §11.1):

* **`--action track`** freezes the day-1 thesis once and audits it against the
  regime evidence the same run produced (`--payload out_7_regime.json`).
* **`--action triage`** (alias `status`) reruns stage 7 against live market
  context **for every open tracked position** — one bundle per position, since
  the position's own symbol decides which sectoral index the bundle carries.

What this seam pins, beyond the plumbing:

* The rerun is a *real* stage-7 run through its frozen CLI: the regime in the
  triage row is byte-for-byte what `7_sector_macro_regime.py` emits for that
  symbol, not a re-implementation. Faking the stage would be invisible
  otherwise.
* A frozen thesis stays frozen: a second `track` for the same trade cannot
  rewrite the entry the original verdict was judged on (BRD §11).
* The half this mode cannot compute (drawdown in ATR, close vs 20 EMA,
  delivery Z-score) is *named* on every row and emitted `null` — never a
  silently reassuring default.
* `LIQUIDATE` requires broken evidence (panic breaker, PANIC VIX, LAGGING
  sector) and the aggregate is the most severe row; a waived regime changes
  nothing and says so.

Runtime strategy (support/pipeline.py): in-process import for the store and
mapping contracts, exactly one subprocess for the CLI contract, and no network
— the live path is proven against an injected fetcher, the same way
`tests/test_fetch_market_context.py` proves its sources.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import types
import unittest
from datetime import date, timedelta
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from support.market_context import build_context  # noqa: E402
from support.pipeline import (  # noqa: E402
    call_imported_main,
    import_mode,
    subprocess_mode,
    temp_input,
)

SCRIPT = REPO_ROOT / "scripts" / "10_daily_triage.py"
BUNDLE_FIXTURE = TESTS_DIR / "fixtures" / "market_context_20261009.json"
# The audit day every seam in this file pins. `audited_on` is `--as-of` or, by
# the CLI's own default, `date.today()` — so a seam that tracks under the clock
# and then audits on a fixed day is measuring the calendar: the two days key
# separate `triage_history` rows (one per position per day), and the same
# assertions pass or fail depending on when they run. Pinned here, and the
# day-key itself is graded by a seam that moves the day on purpose.
AUDIT_DAY = "2026-10-14"

CONTRACT_KEYS = {
    "open_trades_status", "action_required", "trailing_stop_update",
    "premise_invalidated", "adverse_level",
}
ROW_KEYS = {
    "trade_id", "symbol", "instrument_type", "entry_price", "stoploss",
    "target", "tracked_on", "audited_on", "action_required",
    "premise_invalidated", "adverse_level", "regime", "pending_inputs",
}
ACTIONS = {"HOLD", "TIGHTEN_STOP", "LIQUIDATE"}
PENDING_PRICE_HALF = {
    "position_price:drawdown_vs_entry", "atr_14:drawdown_in_atr",
    "close_vs_ema20:premise_evidence", "delivery_zscore:premise_evidence",
}

CANONICAL = {
    "symbol": "RELIANCE",
    "exchange": "NSE",
    "instrument_type": "FUT",
    "action": "BUY",
    "live_price": 2988.20,
    "target": 3120.00,
    "stoploss": 2910.00,
    "lot_size": 250,
    "recommended_date": "2026-10-09",
    "user_capital_inr": 500000.00,
}


def make_payload(**overrides):
    payload = dict(CANONICAL)
    payload.update(overrides)
    return payload


def calm_regime(**overrides):
    regime = {
        "sector_index": "^CNXENERGY", "sector_rrg_state": "LEADING",
        "nifty_trend": "ABOVE_50_200_DMA", "market_breadth_pct": 57.0,
        "breadth_regime": "NEUTRAL", "vix_value": 15.6, "vix_regime": "NORMAL",
        "fii_dii_flow_bias": "NEUTRAL", "panic_regime_breaker": False,
        "waived": False, "as_of": "2026-10-09", "missing_inputs": [],
    }
    regime.update(overrides)
    return regime


def track(db_dir: Path, payload: dict, *, payloads=(), argv=()):
    """Run `--action track` in-process inside an isolated workdir."""
    module, _ = import_mode(10)
    argv = list(argv)
    for document in payloads:
        argv += ["--payload", str(document)]
    with temp_input(payload, ".json") as path:
        return call_imported_main(module, [
            "--action", "track", "--input", path,
            "--db", str(db_dir / "uvest_history.db"), *argv])


def triage(db_dir: Path, *, argv=()):
    module, _ = import_mode(10)
    return call_imported_main(module, [
        "--action", "triage", "--db", str(db_dir / "uvest_history.db"), *argv])


def store_rows(db_dir: Path, table: str, columns: str = "*") -> list[dict]:
    """Rows of one table, keyed by column name."""
    connection = sqlite3.connect(str(db_dir / "uvest_history.db"))
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in
                connection.execute(f"SELECT {columns} FROM {table}")]
    finally:
        connection.close()


class TestTrackFreezesTheThesis(unittest.TestCase):
    """BRD §11: the day-1 thesis is frozen once, and audited on the way in."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_dir = Path(self._tmp.name)

    def test_track_records_the_position_and_audits_this_runs_own_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            regime_path = Path(tmp) / "out_7_regime.json"
            regime_path.write_text(json.dumps(calm_regime()), encoding="utf-8")
            payload, code, err = track(self.db_dir, CANONICAL,
                                       payloads=[regime_path])
        self.assertEqual(code, 0, err)
        self.assertEqual(set(payload), CONTRACT_KEYS)
        row = payload["open_trades_status"][0]
        self.assertEqual(set(row), ROW_KEYS)
        self.assertEqual(row["trade_id"], "RELIANCE_20261009")
        self.assertEqual(row["regime"], calm_regime(),
                         "the row carries the stage-7 payload verbatim")
        self.assertEqual(row["action_required"], "HOLD")
        self.assertFalse(row["premise_invalidated"])

        trades = store_rows(self.db_dir, "trades")
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["symbol"], "RELIANCE")
        self.assertEqual(trades[0]["status"], "OPEN")
        self.assertEqual(trades[0]["entry_price"], 2988.20)
        self.assertIsNone(trades[0]["prior_p_win"])

    def test_the_entry_prior_comes_from_the_decision_engine_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            decision = Path(tmp) / "out_9_decision.json"
            decision.write_text(json.dumps({"p_win": 0.57}), encoding="utf-8")
            _, code, err = track(self.db_dir, CANONICAL, payloads=[decision])
        self.assertEqual(code, 0, err)
        self.assertAlmostEqual(store_rows(self.db_dir, "trades")[0]["prior_p_win"],
                               0.57)

    def test_a_second_track_never_rewrites_the_frozen_geometry(self):
        _, code, err = track(self.db_dir, CANONICAL)
        self.assertEqual(code, 0, err)
        _, code, err = track(self.db_dir, make_payload(live_price=2700.00,
                                                       stoploss=2600.00))
        self.assertEqual(code, 0, err)
        trades = store_rows(self.db_dir, "trades")
        self.assertEqual(len(trades), 1, "one recommendation, one row")
        self.assertEqual(trades[0]["entry_price"], 2988.20,
                         "the entry the thesis was judged on stays frozen")
        self.assertEqual(trades[0]["stoploss"], 2910.00)

    def test_a_waived_regime_audits_to_hold_and_names_the_gap(self):
        payload, code, err = track(self.db_dir, CANONICAL)
        self.assertEqual(code, 0, err)
        row = payload["open_trades_status"][0]
        self.assertIsNone(row["regime"])
        self.assertEqual(row["action_required"], "HOLD")
        self.assertFalse(row["premise_invalidated"],
                         "absence never fabricates a rejection (BRD §10.4)")
        self.assertIn("regime:waived", row["pending_inputs"])

    def test_every_row_names_the_half_this_stage_cannot_compute(self):
        payload, code, err = track(self.db_dir, CANONICAL)
        self.assertEqual(code, 0, err)
        row = payload["open_trades_status"][0]
        self.assertTrue(PENDING_PRICE_HALF.issubset(set(row["pending_inputs"])))
        self.assertIsNone(row["adverse_level"])
        self.assertIsNone(payload["trailing_stop_update"],
                          "the price half is unimplemented, not zero")
        self.assertIsNone(payload["adverse_level"])


class TestMacroPremiseMapping(unittest.TestCase):
    """The documented mapping from regime evidence to the frozen action set."""

    def test_panic_evidence_liquidates_and_invalidates(self):
        module, _ = import_mode(10)
        for broken in (
            calm_regime(panic_regime_breaker=True),
            calm_regime(vix_regime="PANIC"),
            calm_regime(sector_rrg_state="LAGGING"),
        ):
            with self.subTest(broken=broken):
                self.assertEqual(module.audit_regime(broken), ("LIQUIDATE", True))

    def test_weakened_evidence_tightens_without_invalidating(self):
        module, _ = import_mode(10)
        for weakened in (calm_regime(breadth_regime="RISK_OFF"),
                         calm_regime(fii_dii_flow_bias="NET_SELLING"),
                         calm_regime(sector_rrg_state="WEAKENING"),
                         calm_regime(sector_rrg_state="IMPROVING")):
            with self.subTest(weakened=weakened):
                self.assertEqual(module.audit_regime(weakened),
                                 ("TIGHTEN_STOP", False))

    def test_every_frozen_rrg_token_is_accounted_for(self):
        """The day-1 premise is "sector MRS leading": no quadrant is ignored."""
        module, _ = import_mode(10)
        expected = {"LEADING": "HOLD", "WEAKENING": "TIGHTEN_STOP",
                    "IMPROVING": "TIGHTEN_STOP", "LAGGING": "LIQUIDATE"}
        for quadrant, action in expected.items():
            with self.subTest(quadrant=quadrant):
                self.assertEqual(
                    module.audit_regime(calm_regime(sector_rrg_state=quadrant))[0],
                    action)

    def test_calm_and_absent_evidence_hold(self):
        module, _ = import_mode(10)
        self.assertEqual(module.audit_regime(calm_regime()), ("HOLD", False))
        self.assertEqual(module.audit_regime(None), ("HOLD", False))
        self.assertEqual(module.audit_regime({"waived": True}), ("HOLD", False))

    def test_the_aggregate_is_the_most_severe_row(self):
        module, _ = import_mode(10)
        hold = {"action_required": "HOLD"}
        tighten = {"action_required": "TIGHTEN_STOP"}
        liquidate = {"action_required": "LIQUIDATE"}
        self.assertEqual(module.aggregate_action([hold]), "HOLD")
        self.assertEqual(module.aggregate_action([hold, tighten]), "TIGHTEN_STOP")
        self.assertEqual(module.aggregate_action([hold, tighten, liquidate]),
                         "LIQUIDATE")
        self.assertEqual(module.aggregate_action([]), "HOLD")

    def test_trail_stop_belongs_to_the_unimplemented_price_half(self):
        module, _ = import_mode(10)
        self.assertNotIn("TRAIL_STOP", module.SEVERITY)


class TestTriageRerunsStageSeven(unittest.TestCase):
    """The core ask: stage 7, rerun against market context, once per position."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_dir = Path(self._tmp.name)
        for symbol, price in (("RELIANCE", 2988.20), ("SBIN", 812.40)):
            _, code, err = track(self.db_dir, make_payload(
                symbol=symbol, live_price=price, stoploss=price * 0.95,
                target=price * 1.08), argv=["--as-of", AUDIT_DAY])
            self.assertEqual(code, 0, err)

    def test_every_open_position_is_audited_through_the_real_stage_7(self):
        payload, code, err = triage(
            self.db_dir, argv=["--context-fixture", str(BUNDLE_FIXTURE),
                               "--as-of", AUDIT_DAY])
        self.assertEqual(code, 0, err)
        self.assertEqual(set(payload), CONTRACT_KEYS)
        rows = {row["trade_id"]: row for row in payload["open_trades_status"]}
        self.assertEqual(set(rows), {"RELIANCE_20261009", "SBIN_20261009"})

        # The mapped symbol's row is exactly what stage 7 emits for it — the
        # rerun is the stage, not a lookalike re-implementation.
        regime = rows["RELIANCE_20261009"]["regime"]
        self.assertFalse(regime["waived"], err)
        self.assertEqual(regime["sector_index"], "^CNXENERGY")
        self.assertEqual(regime["sector_rrg_state"], "LEADING")
        self.assertEqual(regime["as_of"], "2026-10-09",
                         "the bundle's own as_of, transcribed")

        # SBIN's sectoral index is not in this bundle, so that position waives —
        # per position, disclosed in its row, never silently dropped.
        sbin = rows["SBIN_20261009"]
        self.assertTrue(sbin["regime"]["waived"])
        self.assertIn("series:^NSEBANK:absent", sbin["regime"]["missing_inputs"])
        self.assertIn("regime:waived", sbin["pending_inputs"])

        history = store_rows(
            self.db_dir, "triage_history",
            "trade_id, audited_on, action_required, regime_waived, "
            "sector_rrg_state")
        self.assertEqual(len(history), 2, "the tracked day's own row per position")
        self.assertTrue(all(row["audited_on"] == AUDIT_DAY for row in history))
        self.assertTrue(all(row["action_required"] in ACTIONS for row in history))

    def test_a_panic_bundle_escalates_the_whole_report(self):
        panic = Path(self.db_dir) / "panic.json"
        panic.write_text(json.dumps(build_context(vix_tail=31.0,
                                                  breadth_tail=22.0)),
                         encoding="utf-8")
        payload, code, err = triage(
            self.db_dir, argv=["--context-fixture", str(panic),
                               "--as-of", AUDIT_DAY])
        self.assertEqual(code, 0, err)
        self.assertEqual(payload["action_required"], "LIQUIDATE")
        self.assertTrue(payload["premise_invalidated"])
        row = payload["open_trades_status"][0]["regime"]
        self.assertTrue(row["panic_regime_breaker"])
        self.assertEqual(row["vix_regime"], "PANIC")

    def test_the_same_day_rerun_replaces_its_own_history_row(self):
        argv = ["--context-fixture", str(BUNDLE_FIXTURE), "--as-of", AUDIT_DAY]
        triage(self.db_dir, argv=argv)
        triage(self.db_dir, argv=argv)
        history = store_rows(self.db_dir, "triage_history", "trade_id, audited_on")
        self.assertEqual(len(history), 2, "one row per position per day")

    def test_a_later_audit_day_appends_instead_of_replacing(self):
        """The row key is the audit day — not the machine's, and not yesterday's.

        The same-day seam above proves the replace; this one proves the key it
        replaces on, by moving the day deliberately instead of leaving it to
        `date.today()`. Two positions audited on two days are four rows, and
        each position owns one row per day.
        """
        next_day = (date.fromisoformat(AUDIT_DAY) + timedelta(days=1)).isoformat()
        _, code, err = triage(self.db_dir, argv=[
            "--context-fixture", str(BUNDLE_FIXTURE), "--as-of", next_day])
        self.assertEqual(code, 0, err)
        history = store_rows(self.db_dir, "triage_history", "trade_id, audited_on")
        self.assertEqual(len(history), 4, "one row per position per audit day")
        self.assertEqual({row["audited_on"] for row in history},
                         {AUDIT_DAY, next_day})
        trade_ids = {row["trade_id"] for row in history}
        self.assertEqual(len(trade_ids), 2)
        for trade_id in trade_ids:
            self.assertEqual(
                sorted(row["audited_on"] for row in history
                       if row["trade_id"] == trade_id),
                [AUDIT_DAY, next_day],
                f"{trade_id} keeps one row per audit day")

    def test_triage_never_touches_a_closed_position(self):
        connection = sqlite3.connect(str(self.db_dir / "uvest_history.db"))
        connection.execute("UPDATE trades SET status = 'CLOSED' WHERE symbol = 'SBIN'")
        connection.commit()
        connection.close()
        payload, code, err = triage(
            self.db_dir, argv=["--context-fixture", str(BUNDLE_FIXTURE),
                               "--as-of", AUDIT_DAY])
        self.assertEqual(code, 0, err)
        audited = {row["symbol"] for row in payload["open_trades_status"]}
        self.assertEqual(audited, {"RELIANCE"})

    def test_status_is_a_documented_alias_of_triage(self):
        module, _ = import_mode(10)
        self.assertEqual(module.TRIAGE_ALIASES["status"], "triage")
        with temp_input({}, ".json") as path:
            _, code, err = call_imported_main(module, [
                "--action", "status",
                "--db", str(self.db_dir / "uvest_history.db")])
        self.assertEqual(code, 0, err)


class TestLiveContextPath(unittest.TestCase):
    """`--context-fixture` omitted: one live bundle per position, shared fetcher."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_dir = Path(self._tmp.name)
        for symbol in ("RELIANCE", "SBIN"):
            _, code, err = track(self.db_dir, make_payload(symbol=symbol))
            self.assertEqual(code, 0, err)

    def _stub_fetcher(self, calls: list[list[str]], exit_code: int = 0):
        """A fake `fetch_market_context` module, injected for the live path."""
        source = str(BUNDLE_FIXTURE)
        module = types.ModuleType("fetch_market_context")

        def fake_main(argv):
            calls.append(list(argv))
            # The real fetcher reports per-source progress on stdout; stage 10
            # runs it in-process, so that chatter must never reach the artifact.
            print(f"  [ok  ] mock fetch for {argv[argv.index('--symbol') + 1]}")
            if exit_code != 0:
                return exit_code
            output = Path(argv[argv.index("--output") + 1])
            output.write_text(Path(source).read_text(encoding="utf-8"),
                              encoding="utf-8")
            return 0

        module.main = fake_main
        return module

    def test_the_shared_fetcher_supplies_one_bundle_per_position(self):
        calls: list[list[str]] = []
        sys.modules["fetch_market_context"] = self._stub_fetcher(calls)
        self.addCleanup(sys.modules.pop, "fetch_market_context", None)
        payload, code, err = triage(self.db_dir, argv=["--as-of", "2026-10-14"])
        self.assertEqual(code, 0, err)
        self.assertEqual([call[call.index("--symbol") + 1] for call in calls],
                         ["RELIANCE", "SBIN"],
                         "one fetch per position, addressing that position")
        for call in calls:
            self.assertIn("--as-of", call)
            self.assertEqual(call[call.index("--as-of") + 1], "2026-10-14")
        self.assertFalse(payload["open_trades_status"][0]["regime"]["waived"])

    def test_fetcher_chatter_never_reaches_the_artifact(self):
        """Stage 10's stdout is the artifact: a fetch log line must not land in it.

        The live fetcher prints per-source progress lines. Run in-process (its
        own stdout is not redirected by a shell), those lines would prefix the
        payload and the artifact would stop being JSON at all.
        """
        calls: list[list[str]] = []
        sys.modules["fetch_market_context"] = self._stub_fetcher(calls)
        self.addCleanup(sys.modules.pop, "fetch_market_context", None)
        payload, code, err = triage(self.db_dir, argv=["--as-of", "2026-10-14"])
        self.assertEqual(code, 0, err)
        self.assertIsInstance(payload, dict, f"stdout was not JSON: {payload!r}")
        self.assertEqual(set(payload), CONTRACT_KEYS)
        for call in calls:
            self.assertNotIn("[ok  ] mock fetch", json.dumps(payload))

    def test_a_failed_fetch_waives_that_position_and_never_aborts_the_run(self):
        calls: list[list[str]] = []
        sys.modules["fetch_market_context"] = self._stub_fetcher(calls, exit_code=1)
        self.addCleanup(sys.modules.pop, "fetch_market_context", None)
        payload, code, err = triage(self.db_dir, argv=["--as-of", "2026-10-14"])
        self.assertEqual(code, 0, "a fetch failure is a waiver, not a crash")
        rows = payload["open_trades_status"]
        self.assertEqual(len(rows), 2, "one failed fetch does not drop a position")
        for row in rows:
            self.assertIsNone(row["regime"])
            self.assertEqual(row["action_required"], "HOLD")
            self.assertIn("regime:waived", row["pending_inputs"])


class TestStoreDiscipline(unittest.TestCase):
    def test_triage_without_a_store_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            module, _ = import_mode(10)
            _, code, err = call_imported_main(module, [
                "--action", "triage", "--db", str(Path(tmp) / "missing.db")])
        self.assertEqual(code, 1)
        self.assertIn("ERR_STORE", err)
        self.assertIn("--action track", err)

    def test_track_creates_the_frozen_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_dir = Path(tmp)
            _, code, err = track(db_dir, CANONICAL)
            self.assertEqual(code, 0, err)
            connection = sqlite3.connect(str(db_dir / "uvest_history.db"))
            try:
                tables = {row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'")}
            finally:
                connection.close()
        self.assertEqual({"trades", "triage_history"}, tables)

    def test_the_stage_declares_what_it_cannot_run_without(self):
        module, _ = import_mode(10)
        self.assertEqual(module.REQUIRED_FIELDS,
                         ["symbol", "live_price", "stoploss", "target"])


class TestCliAndRecording(unittest.TestCase):
    def test_track_cli_is_the_canonical_shape_and_records_its_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            bundle = workdir / "bundle"
            input_path = workdir / "current_trade.json"
            input_path.write_text(json.dumps(CANONICAL), encoding="utf-8")
            proc = subprocess_mode(SCRIPT, [
                "--action", "track", "--input", str(input_path),
                "--db", str(workdir / "uvest_history.db"),
                "--record", str(bundle)])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertEqual(set(payload), CONTRACT_KEYS)
            self.assertTrue((workdir / "uvest_history.db").exists())
            artifact = bundle / "out_10_triage.json"
            self.assertTrue(artifact.exists(), "recorded stage-10 artifact missing")
            self.assertEqual(artifact.read_text(encoding="utf-8"), proc.stdout)

    def test_import_writes_nothing_to_stdout(self):
        import subprocess

        proc = subprocess.run(
            [sys.executable, "-c",
             "import importlib.util;"
             "spec=importlib.util.spec_from_file_location('s10',"
             f"{str(SCRIPT)!r});"
             "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)"],
            capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")


if __name__ == "__main__":
    unittest.main()
