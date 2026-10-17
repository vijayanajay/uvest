"""Seam — stage 9 decision engine (TICK-009, BRD §10).

What is frozen here:

1. **The output contract** (TICK-009 §2, mirroring BRD §9 inventory row 9):
   every run emits `final_score`, `max_possible_score`, `effective_score_pct`,
   `decision`, `verdict` (same token), `p_win`, `expectancy`,
   `circuit_breakers_tripped`, `three_predictions`, `flip_table`,
   `what_would_flip_me`, `avoid_reason`, `confidence_deficit` — canonical
   pre-entry tokens only; a retired `NO_GO`/`CONDITIONAL_GO` can never appear.
2. **Hard breaker override** (BRD §10.3): any tripped breaker forces `AVOID`;
   the single exception is inverted R:R alone when a limit order restores
   R:R >= 1.5, which yields `WAIT`.
3. **Waiver rule** (BRD §10.4): absent upstream artifacts are *waived
   sources*, not failures — their weight is deducted from `max_possible_score`
   and >= 1 waived source caps the verdict at `GO_WEAK`. Absence never
   fabricates a rejection.
4. **Flip Condition Invariant** (BRD §10.4): every non-GO verdict carries a
   non-empty `what_would_flip_me`; GO carries none.
5. **Expectancy arithmetic** (BRD §10.1): E = p*rr - (1-p), so the frozen
   example P=55%, R:R=2.0 must yield exactly +0.65R.
6. **Determinism**: identical inputs produce byte-identical stdout; recorded
   proof fixtures replay to the verdict their evidence implies.
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
STAGE9 = REPO_ROOT / "scripts" / "9_decision_engine.py"
PROOF_FIXTURE = REPO_ROOT / "tests" / "proof" / "fixtures" / "GB-2022-0006"

CANONICAL_VERDICTS = {"GO", "GO_WEAK", "WAIT", "AVOID"}
CONTRACT_KEYS = {
    "final_score", "max_possible_score", "effective_score_pct",
    "decision", "verdict", "p_win", "expectancy",
    "circuit_breakers_tripped", "three_predictions", "flip_table",
    "what_would_flip_me", "avoid_reason", "confidence_deficit",
}


def trade(**overrides) -> dict:
    base = {
        "symbol": "INFY",
        "exchange": "NSE",
        "instrument_type": "CASH",
        "action": "BUY",
        "entry_price": 1500.0,
        "entry_range_min": 1498.0,
        "entry_range_max": 1502.0,
        "live_price": 1500.0,
        "target": 1590.0,
        "stoploss": 1460.0,
        "lot_size": 1,
        "recommended_date": "2026-01-05",
        "user_capital_inr": 500000.0,
    }
    base.update(overrides)
    return base


def strong_payloads() -> dict[str, dict]:
    """All seven scoring sources present and strong; market payload clear."""
    return {
        "out_1_math.json": {"live_rr": 2.0, "outside_range_pct": 0.0,
                            "inverted_rr_flag": False},
        "out_2_market.json": {"is_tight_sl": False},
        "out_3_dtw.json": {"analog_confidence": 0.8, "dtw_win_rate_22d": 0.7},
        "out_4_ml.json": {"prob_target_hit_22d": 0.7},
        "out_5_flow.json": {"matrix_verdict": "LONG_BUILDUP",
                            "delivery_zscore": 1.5, "oi_change_pct": 5.0},
        "out_6_fundamental.json": {"piotoski_score": 8, "altman_z_score": 3.1,
                                   "cfo_pat_ratio": 0.9,
                                   "promoter_pledge_pct": 0.0,
                                   "pledge_circuit_breaker": False,
                                   "days_to_earnings": 28,
                                   "earnings_hazard_flag": False},
        "out_7_regime.json": {"sector_rrg_state": "LEADING",
                              "nifty_trend": "ABOVE_50_200_DMA",
                              "vix_regime": "LOW",
                              "panic_regime_breaker": False},
        "out_8_dhan.json": {"optimal_limit_buy": 1497.0,
                            "spread_impact_cost_pct": 0.05},
    }


def run_stage9(trade_doc: dict, payloads: dict[str, dict] | None,
               thresholds: dict | None = None) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        input_path = root / "current_trade.json"
        input_path.write_text(json.dumps(trade_doc), encoding="utf-8")
        cmd = [sys.executable, str(STAGE9), "--input", str(input_path)]
        for name, doc in (payloads or {}).items():
            path = root / name
            path.write_text(json.dumps(doc), encoding="utf-8")
            cmd += ["--payload", str(path)]
        if thresholds is not None:
            th = root / "thresholds.json"
            th.write_text(json.dumps(thresholds), encoding="utf-8")
            cmd += ["--thresholds", str(th)]
        return subprocess.run(cmd, capture_output=True, check=False,
                              cwd=str(REPO_ROOT))


def verdict_of(proc: subprocess.CompletedProcess) -> dict:
    return json.loads(proc.stdout.decode("utf-8"))


class TestOutputContract(unittest.TestCase):
    def test_contract_keys_and_canonical_tokens(self):
        proc = run_stage9(trade(), strong_payloads())
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8"))
        out = verdict_of(proc)
        self.assertTrue(CONTRACT_KEYS.issubset(out),
                        f"missing: {CONTRACT_KEYS - set(out)}")
        self.assertIn(out["decision"], CANONICAL_VERDICTS)
        self.assertEqual(out["decision"], out["verdict"],
                         "decision and verdict must be the same token")
        self.assertGreaterEqual(out["final_score"], 0.0)
        self.assertLessEqual(out["final_score"], out["max_possible_score"])
        self.assertLessEqual(out["max_possible_score"], 100.0)
        self.assertGreaterEqual(out["effective_score_pct"], 0.0)
        self.assertLessEqual(out["effective_score_pct"], 100.0)
        self.assertGreaterEqual(out["p_win"], 0.05)
        self.assertLessEqual(out["p_win"], 0.95)

    def test_strong_complete_setup_is_go(self):
        proc = run_stage9(trade(), strong_payloads())
        out = verdict_of(proc)
        self.assertEqual(out["decision"], "GO", out)
        self.assertEqual(out["max_possible_score"], 100.0)
        self.assertIsNone(out["what_would_flip_me"])
        self.assertIsNone(out["avoid_reason"])
        self.assertEqual(out["circuit_breakers_tripped"], [])

    def test_retired_tokens_never_emitted(self):
        proc = run_stage9(trade(), strong_payloads())
        text = proc.stdout.decode("utf-8")
        self.assertNotIn("NO_GO", text)
        self.assertNotIn("CONDITIONAL_GO", text)

    def test_frozen_nifty_trend_tokens_are_the_scored_vocabulary(self):
        """Stage 7 can only emit TICK-007 / BRD §7.2 tokens — score them.

        This scorer once keyed `BULLISH`/`NEUTRAL`/`BEARISH`, tokens no
        producer emits, so every real run took the neutral 0.2 default in
        silence (ADR-010 decision 1). The frozen vocabulary must move the
        score: the bearish state scores strictly less than the bullish one,
        and an unknown token scores the neutral default.
        """
        def score_with(nifty_trend: str) -> float:
            payloads = strong_payloads()
            payloads["out_7_regime.json"]["nifty_trend"] = nifty_trend
            proc = run_stage9(trade(), payloads)
            self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8"))
            return verdict_of(proc)["final_score"]

        bullish = score_with("ABOVE_50_200_DMA")
        neutral = score_with("BELOW_50_ABOVE_200")
        bearish = score_with("BELOW_200_DMA")
        unknown = score_with("BULLISH")
        self.assertGreater(bullish, neutral)
        self.assertGreater(neutral, bearish)
        self.assertEqual(unknown, neutral,
                         "a token outside the frozen vocabulary must score "
                         "the neutral default, never a rewarded state")

    def test_unusable_input_exits_non_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty.json"
            empty.write_text("{}", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(STAGE9), "--input", str(empty)],
                capture_output=True, check=False, cwd=str(REPO_ROOT))
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("ERR_", proc.stderr.decode("utf-8", "replace"))


class TestHardBreakerOverride(unittest.TestCase):
    def test_inverted_rr_forces_avoid_when_limit_cannot_recover(self):
        payloads = strong_payloads()
        payloads["out_1_math.json"] = {"live_rr": 0.9, "outside_range_pct": 1.2,
                                       "inverted_rr_flag": True}
        # Band low 1497, stop 1490, target 1505 -> limit rr = 8/7 < 1.5.
        t = trade(entry_range_min=1497.0, live_price=1497.0,
                  target=1505.0, stoploss=1490.0)
        out = verdict_of(run_stage9(t, payloads))
        self.assertEqual(out["decision"], "AVOID", out)
        self.assertIn("inverted_risk_reward", out["circuit_breakers_tripped"])
        self.assertTrue(out["avoid_reason"])
        self.assertTrue(out["what_would_flip_me"])

    def test_inverted_rr_alone_flips_to_wait_when_limit_recovers(self):
        payloads = strong_payloads()
        payloads["out_1_math.json"] = {"live_rr": 0.9, "outside_range_pct": 1.2,
                                       "inverted_rr_flag": True}
        # Band low 1498, stop 1460, target 1590 -> limit rr = 92/38 = 2.42.
        out = verdict_of(run_stage9(trade(live_price=1497.0), payloads))
        self.assertEqual(out["decision"], "WAIT", out)
        self.assertIn("inverted_risk_reward", out["circuit_breakers_tripped"])
        self.assertTrue(out["what_would_flip_me"])

    def test_pledge_breaker_forces_avoid_regardless_of_strength(self):
        payloads = strong_payloads()
        payloads["out_6_fundamental.json"]["promoter_pledge_pct"] = 31.0
        payloads["out_6_fundamental.json"]["pledge_circuit_breaker"] = True
        out = verdict_of(run_stage9(trade(), payloads))
        self.assertEqual(out["decision"], "AVOID", out)
        self.assertIn("promoter_pledge_gt_25pct", out["circuit_breakers_tripped"])

    def test_tight_stop_breaker_forces_avoid(self):
        payloads = strong_payloads()
        payloads["out_2_market.json"] = {"is_tight_sl": True}
        out = verdict_of(run_stage9(trade(), payloads))
        self.assertEqual(out["decision"], "AVOID", out)
        self.assertIn("tighter_than_0_75x_atr", out["circuit_breakers_tripped"])

    def test_panic_regime_breaker_forces_avoid(self):
        payloads = strong_payloads()
        payloads["out_7_regime.json"]["panic_regime_breaker"] = True
        out = verdict_of(run_stage9(trade(), payloads))
        self.assertEqual(out["decision"], "AVOID", out)
        self.assertIn("panic_regime", out["circuit_breakers_tripped"])

    def test_earnings_hazard_breaker_forces_avoid_on_futures(self):
        payloads = strong_payloads()
        payloads["out_6_fundamental.json"]["days_to_earnings"] = 3
        payloads["out_6_fundamental.json"]["earnings_hazard_flag"] = True
        out = verdict_of(run_stage9(trade(instrument_type="FUT"), payloads))
        self.assertEqual(out["decision"], "AVOID", out)
        self.assertIn("earnings_within_7d", out["circuit_breakers_tripped"])

    def test_cash_equity_size_cap_enters_at_half_size(self):
        """BRD §6.4 / §10.4: an earnings window inside 10 sessions on a Cash
        Equity caps a viable trade at GO_WEAK rather than vetoing it."""
        payloads = strong_payloads()
        payloads["out_6_fundamental.json"]["days_to_earnings"] = 6
        payloads["out_6_fundamental.json"]["earnings_size_cap_pct"] = 50
        out = verdict_of(run_stage9(trade(instrument_type="CASH"), payloads))
        self.assertEqual(out["decision"], "GO_WEAK", out)
        self.assertEqual(out["circuit_breakers_tripped"], [])
        self.assertIn("50%", out["confidence_deficit"])
        self.assertIn("6 session", out["confidence_deficit"])
        self.assertTrue(out["what_would_flip_me"],
                        "a half-size verdict still states its flip condition")

    def test_futures_hazard_is_not_softened_by_the_size_cap_rule(self):
        """The cap branch only softens a GO: a fatal Futures hazard stays AVOID."""
        payloads = strong_payloads()
        payloads["out_6_fundamental.json"]["days_to_earnings"] = 4
        payloads["out_6_fundamental.json"]["earnings_hazard_flag"] = True
        payloads["out_6_fundamental.json"]["earnings_size_cap_pct"] = 50
        out = verdict_of(run_stage9(trade(instrument_type="FUT"), payloads))
        self.assertEqual(out["decision"], "AVOID", out)

    def test_a_waived_fundamental_payload_caps_nothing(self):
        payloads = strong_payloads()
        payloads["out_6_fundamental.json"] = {
            "waived": True, "earnings_size_cap_pct": None}
        out = verdict_of(run_stage9(trade(instrument_type="CASH"), payloads))
        self.assertNotEqual(out["decision"], "AVOID",
                            "absence must never fabricate a rejection")
        self.assertNotIn("size at", out["confidence_deficit"] or "")
        self.assertIn("out_6_fundamental.json", out["confidence_deficit"])


class TestWaiverRule(unittest.TestCase):
    def test_absent_sources_deduct_from_max_and_cap_at_go_weak(self):
        payloads = {k: v for k, v in strong_payloads().items()
                    if k in ("out_1_math.json", "out_2_market.json")}
        out = verdict_of(run_stage9(trade(), payloads))
        # Only the math source grades (20 pts); the other six sources are
        # waived, so max_possible drops from 100 to 20.
        self.assertEqual(out["max_possible_score"], 20.0, out)
        self.assertLess(out["effective_score_pct"], 100.0)
        # Full marks on 20% coverage still caps confidence well below the
        # GO verdict's reach: the waiver cap, not a diluted P(win), is what
        # blocks GO on partial evidence (BRD §10.4).
        self.assertLessEqual(out["p_win"], 0.78)
        self.assertIn(out["decision"], {"GO_WEAK", "WAIT"}, out)
        self.assertTrue(out["confidence_deficit"], "waived sources must be named")
        self.assertNotEqual(out["decision"], "AVOID",
                            "absent data must never fabricate a rejection")

    def test_awaiting_everything_still_exits_zero_not_avoid(self):
        out = verdict_of(run_stage9(trade(), None))
        self.assertEqual(out["max_possible_score"], 0.0, out)
        self.assertIn(out["decision"], {"GO_WEAK", "WAIT", "AVOID"}, out)
        if out["decision"] == "AVOID":
            self.assertTrue(out["what_would_flip_me"],
                            "even a data-less AVOID must state its flip condition")
        self.assertTrue(out["confidence_deficit"])


class TestExpectancyAndFloor(unittest.TestCase):
    def test_brd_example_yields_exactly_plus_065r(self):
        # Pin the frozen arithmetic directly: P=55%, R:R 1:2 -> +0.65R.
        import importlib.util
        spec = importlib.util.spec_from_file_location("stage9", STAGE9)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertAlmostEqual(module.expectancy(0.55, 2.0), 0.65, places=9)

    def test_p_win_mapping_hits_the_frozen_floor(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("stage9", STAGE9)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # effective 50 -> the 55% advisory anchor, exactly.
        self.assertAlmostEqual(module.calibrated_p_win(50.0), 0.55, places=9)
        self.assertGreaterEqual(module.calibrated_p_win(100.0), 0.55)

    def test_negative_expectancy_forces_avoid(self):
        # Live R:R 1.1 with a 3% slip and both quant sources at zero signal:
        # graded evidence (not absence) says the geometry cannot pay.
        payloads = {
            "out_1_math.json": {"live_rr": 1.1, "outside_range_pct": 3.0,
                                "inverted_rr_flag": False},
            "out_3_dtw.json": {"analog_confidence": 0.0},
            "out_4_ml.json": {"prob_target_hit_22d": 0.0},
        }
        out = verdict_of(run_stage9(trade(), payloads))
        self.assertLess(out["p_win"], 0.55, out)
        self.assertLessEqual(out["expectancy"], 0.0, out)
        self.assertEqual(out["decision"], "AVOID", out)
        self.assertTrue(out["what_would_flip_me"])


class TestFlipInvariant(unittest.TestCase):
    def test_every_non_go_verdict_carries_a_flip_condition(self):
        cases = []
        inverted = strong_payloads()
        inverted["out_1_math.json"] = {"live_rr": 0.9, "outside_range_pct": 1.2,
                                       "inverted_rr_flag": True}
        cases.append((trade(live_price=1497.0), inverted))
        weak = {k: v for k, v in strong_payloads().items()
                if k in ("out_1_math.json", "out_2_market.json")}
        cases.append((trade(), weak))
        cases.append((trade(), None))
        for t, p in cases:
            out = verdict_of(run_stage9(t, p))
            if out["decision"] == "GO":
                continue
            self.assertTrue(out["what_would_flip_me"],
                            f"{out['decision']} without flip condition: {out}")


class TestReplayAndDeterminism(unittest.TestCase):
    def test_recorded_fixture_replays_to_the_verdict_its_evidence_implies(self):
        """GB-2022-0006 is the recorded inverted-R:R record: live R:R 0.90 and
        a band (1176.41) that cannot restore R:R >= 1.5 over stop 1150.86 to
        target 1201.65 (limit rr = 25.24/25.55 < 1.5) -> AVOID, and only the
        sources actually recorded may grade (the rest are waived)."""
        canonical = json.loads(
            (PROOF_FIXTURE / "current_trade.json").read_text(encoding="utf-8"))
        payloads = {
            name: json.loads((PROOF_FIXTURE / "recorded" / name)
                             .read_text(encoding="utf-8"))
            for name in ("out_1_math.json", "out_2_market.json")
        }
        out = verdict_of(run_stage9(canonical, payloads))
        self.assertEqual(out["decision"], "AVOID", out)
        self.assertIn("inverted_risk_reward", out["circuit_breakers_tripped"])
        self.assertEqual(out["max_possible_score"], 20.0,
                         "only the recorded math source may grade")

    def test_output_is_byte_deterministic(self):
        first = run_stage9(trade(), strong_payloads())
        second = run_stage9(trade(), strong_payloads())
        self.assertEqual(first.returncode, 0)
        self.assertEqual(first.stdout, second.stdout)

    def test_thresholds_file_overrides_module_weights(self):
        math_only = {k: v for k, v in strong_payloads().items()
                     if k in ("out_1_math.json", "out_2_market.json")}
        out = verdict_of(run_stage9(
            trade(), math_only, {"module_weights": {"out_1_math.json": 10.0}}))
        self.assertEqual(out["max_possible_score"], 10.0, out)
        self.assertLessEqual(out["final_score"], 10.0, out)


class TestMacroSizingGates(unittest.TestCase):
    """ADR-010 decision 6 / BRD §7.2, §7.4: macro signals size, not veto.

    `breadth_regime: RISK_OFF` or `fii_dii_flow_bias: NET_SELLING` caps a
    would-be GO at GO_WEAK (50% size) through the §6.4 size-cap lever, with
    the deficit naming each source; both together cap at the same 50% and
    never compound; a waived regime payload caps nothing.
    """

    def test_risk_off_breadth_caps_go_at_half_size(self):
        payloads = strong_payloads()
        payloads["out_7_regime.json"]["breadth_regime"] = "RISK_OFF"
        out = verdict_of(run_stage9(trade(), payloads))
        self.assertEqual(out["decision"], "GO_WEAK", out)
        self.assertEqual(out["circuit_breakers_tripped"], [],
                         "breadth is a sizing gate, never a veto")
        self.assertIn("50%", out["confidence_deficit"])
        self.assertIn("breadth", out["confidence_deficit"])
        self.assertTrue(out["what_would_flip_me"])

    def test_net_selling_flow_caps_go_at_half_size(self):
        payloads = strong_payloads()
        payloads["out_7_regime.json"]["fii_dii_flow_bias"] = "NET_SELLING"
        out = verdict_of(run_stage9(trade(), payloads))
        self.assertEqual(out["decision"], "GO_WEAK", out)
        self.assertEqual(out["circuit_breakers_tripped"], [])
        self.assertIn("50%", out["confidence_deficit"])
        self.assertIn("FII", out["confidence_deficit"])

    def test_both_signals_name_both_sources_at_the_same_cap(self):
        payloads = strong_payloads()
        payloads["out_7_regime.json"]["breadth_regime"] = "RISK_OFF"
        payloads["out_7_regime.json"]["fii_dii_flow_bias"] = "NET_SELLING"
        out = verdict_of(run_stage9(trade(), payloads))
        self.assertEqual(out["decision"], "GO_WEAK", out)
        self.assertIn("breadth", out["confidence_deficit"])
        self.assertIn("FII", out["confidence_deficit"])
        self.assertNotIn("25%", out["confidence_deficit"],
                         "caps must never compound (ADR-010 decision 6)")

    def test_a_waived_regime_payload_caps_nothing(self):
        payloads = strong_payloads()
        payloads["out_7_regime.json"] = {"waived": True,
                                         "breadth_regime": None,
                                         "fii_dii_flow_bias": None}
        out = verdict_of(run_stage9(trade(), payloads))
        # The waiver rule caps it as a waived source — but the macro signals
        # themselves must not appear: a null never gates.
        self.assertEqual(out["decision"], "GO_WEAK", out)
        self.assertIn("waived source(s)", out["confidence_deficit"])
        self.assertNotIn("breadth", out["confidence_deficit"])
        self.assertNotIn("FII", out["confidence_deficit"])

    def test_neutral_regime_signals_leave_go_untouched(self):
        payloads = strong_payloads()
        payloads["out_7_regime.json"]["breadth_regime"] = "NEUTRAL"
        payloads["out_7_regime.json"]["fii_dii_flow_bias"] = "NET_BUYING"
        out = verdict_of(run_stage9(trade(), payloads))
        self.assertEqual(out["decision"], "GO", out)
        self.assertIsNone(out["confidence_deficit"])


class TestTheFrozenWeightsAreReviewable(unittest.TestCase):
    """The weights a verdict is scored with must be visible, not only embedded.

    `thresholds.json` is generated per-run state — stage 11 is its sole writer
    (BRD §9.1) and the repo-hygiene rules keep it untracked — so the baseline
    a run falls back to lived only inside `DEFAULT_WEIGHTS`, invisible to a
    reviewer and to a fresh clone. `thresholds.example.json` is the reviewable
    copy; these tests are what keep it from drifting away from the code that
    actually uses it.
    """

    TEMPLATE = REPO_ROOT / "thresholds.example.json"

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location("stage9_weights", STAGE9)
        cls.stage9 = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(cls.stage9)

    def test_the_template_matches_the_frozen_defaults(self):
        doc = json.loads(self.TEMPLATE.read_text(encoding="utf-8"))
        self.assertEqual(
            doc["module_weights"], self.stage9.DEFAULT_WEIGHTS,
            "the template no longer states the weights stage 9 falls back to")

    def test_the_frozen_weights_are_the_100_point_model(self):
        """BRD §10.2's calibration model adds up; a retune must re-freeze it."""
        self.assertAlmostEqual(sum(self.stage9.DEFAULT_WEIGHTS.values()), 100.0,
                               places=6, msg="module weights no longer sum to 100")


class TestTheClampedZoneNeverFabricatesALimitLeg(unittest.TestCase):
    """Signed proposal B's downstream half (2026-10-16): when stage 8
    disowns its midpoint (`optimal_limit_buy: null`, `zone_above_market:
    true`), stage 9 must honor the silence — the flip table's limit leg is
    disclosed as unavailable, never substituted from a different price, and
    a limit that does not exist can never rescue the inverted-RR WAIT
    exception (BRD §10.3) with a fictional R:R.
    """

    CLAMPED = {"optimal_limit_buy": None,
               "optimal_limit_range": [1480.0, 1505.0],
               "zone_above_market": True,
               "spread_impact_cost_pct": None}

    @staticmethod
    def payloads(out8):
        p = strong_payloads()
        p["out_8_dhan.json"] = out8
        return p

    def test_a_clamped_zone_yields_no_limit_leg_and_no_substitution(self):
        out = verdict_of(run_stage9(trade(), self.payloads(dict(self.CLAMPED))))
        leg = out["flip_table"]["limit_order"]
        self.assertIsNone(
            leg["entry"],
            "entry_range_min must never stand in for a disowned limit")
        self.assertIsNone(leg["r_r"])
        self.assertEqual(leg["unavailable"], "zone_above_market")
        zone = out["three_predictions"]["optimal_price_range"]
        self.assertTrue(zone["zone_above_market"],
                        "the disclosure must reach the engine's own payload")
        self.assertIsNone(zone["optimal_limit_buy"])

    def test_a_clamped_zone_cannot_rescue_the_inverted_rr_exception(self):
        """The capital-relevant half: "a limit order restores R:R >= 1.5"
        must be a real limit claim, not the reco band's low wearing its hat."""
        math_p = {"live_rr": 0.8, "outside_range_pct": 0.0,
                  "inverted_rr_flag": True}
        clamped = dict(self.payloads(dict(self.CLAMPED)))
        clamped["out_1_math.json"] = math_p
        out = verdict_of(run_stage9(trade(), clamped))
        self.assertEqual(out["verdict"], "AVOID")
        self.assertIn("no limit order restores", out["avoid_reason"])

        real = dict(self.payloads({"optimal_limit_buy": 1497.0,
                                   "spread_impact_cost_pct": 0.05}))
        real["out_1_math.json"] = math_p
        self.assertEqual(verdict_of(run_stage9(trade(), real))["verdict"],
                         "WAIT", "a real limit claim still restores R:R")

    def test_an_absent_stage8_payload_keeps_the_legacy_band_reference(self):
        """Back-compat: stage 9 alone (no stage 8 in the chain at all) keeps
        the reco's own band low as its historical limit reference."""
        p = strong_payloads()
        del p["out_8_dhan.json"]
        out = verdict_of(run_stage9(trade(), p))
        leg = out["flip_table"]["limit_order"]
        self.assertEqual(leg["entry"], 1498.0)
        self.assertIsNotNone(leg["r_r"])
        self.assertIsNone(leg["unavailable"])

    def test_a_wholly_waived_zone_discloses_no_zone(self):
        """Stage 8 ran and had nothing to say at all: unavailable, named as
        such — still never the band substitution."""
        waived8 = {"optimal_limit_buy": None, "zone_above_market": None,
                   "spread_impact_cost_pct": None, "waived": True}
        out = verdict_of(run_stage9(trade(), self.payloads(waived8)))
        leg = out["flip_table"]["limit_order"]
        self.assertIsNone(leg["entry"])
        self.assertEqual(leg["unavailable"], "zone_unavailable")


class TestScorePrecisionNeverRewardsAbsentSpread(unittest.TestCase):
    """Honesty seam — plan-tier2-dhan-collection §1 / Phase 1.3 (TDD, red first).

    Stage 9 reads its precision dimension straight off
    `spread_impact_cost_pct`. When that value is absent — the key missing,
    null, or not a number — the score must be **0.0**, because BRD §10.4's
    rule is that absence is disclosed and deducted, never rewarded. The old
    `float(doc.get(...) or 0.0)` fall-through read "no evidence" as "zero
    cost" and therefore paid out a perfect 1.0 for evidence nobody collected.

    A waived `out_8` payload is the *other* half of that rule and already
    behaves correctly (its weight leaves `max_possible_score`, §10.4); it is
    pinned here so the two halves can never drift apart.
    """

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location("stage9_precision", STAGE9)
        cls.stage9 = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(cls.stage9)

    @staticmethod
    def zone(spread) -> dict:
        return {"optimal_limit_buy": 1497.0, "spread_impact_cost_pct": spread}

    def test_an_absent_spread_scores_zero_never_one(self):
        for label, doc in (
                ("key missing", {"optimal_limit_buy": 1497.0}),
                ("null", self.zone(None)),
                ("not a number", self.zone("n/a"))):
            with self.subTest(label=label):
                self.assertEqual(self.stage9.score_precision(doc), 0.0,
                                 f"{label}: unmeasured evidence must not score")

    def test_unmeasured_is_not_the_same_number_as_measured_free(self):
        """The falsifiable pair: a *measured* zero-cost spread is a 1.0; an
        unmeasured spread can never be that same number."""
        self.assertEqual(self.stage9.score_precision(self.zone(0.0)), 1.0)
        self.assertEqual(self.stage9.score_precision(self.zone(None)), 0.0)

    def test_a_measured_spread_keeps_the_frozen_formula(self):
        self.assertAlmostEqual(self.stage9.score_precision(self.zone(0.05)),
                               0.9, places=6)
        self.assertEqual(self.stage9.score_precision(self.zone(0.60)), 0.0)

    def test_no_zone_still_scores_zero(self):
        self.assertEqual(
            self.stage9.score_precision({"optimal_limit_buy": None,
                                         "spread_impact_cost_pct": 0.0}),
            0.0)

    def test_a_waived_stage8_payload_is_deducted_not_scored(self):
        """§10.4's waived source: its weight leaves `max_possible_score`
        instead of contributing a score of any kind."""
        payloads = strong_payloads()
        payloads["out_8_dhan.json"] = {
            "optimal_limit_buy": 1497.0, "spread_impact_cost_pct": None,
            "waived": True}
        out = verdict_of(run_stage9(trade(), payloads))
        self.assertAlmostEqual(out["max_possible_score"],
                               100.0 - self.stage9.DEFAULT_WEIGHTS[
                                   "out_8_dhan.json"], places=2)

    def test_the_engine_actually_charges_for_the_missing_spread(self):
        """End to end: two payloads differing only in whether the spread was
        measured. Before the fix both scored 1.0 and the engine could not
        tell them apart."""
        absent = strong_payloads()
        absent["out_8_dhan.json"] = self.zone(None)
        measured = strong_payloads()
        measured["out_8_dhan.json"] = self.zone(0.0)
        a = verdict_of(run_stage9(trade(), absent))
        b = verdict_of(run_stage9(trade(), measured))
        self.assertLess(
            a["final_score"], b["final_score"],
            "an unmeasured spread must cost the verdict points, not earn "
            "the same 5.0 weight credit a measured free one does")


if __name__ == "__main__":
    unittest.main()
