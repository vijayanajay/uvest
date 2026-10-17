"""TICK-004 seam — scripts/4_ml_direction.py

Stage 4 owns the 22-session forward direction and range engine (BRD §4, §5.2,
§9.1 row 4) and emits the frozen contract keys:

    prob_target_hit_22d, price_cone_p10_p50_p90, mae_projected,
    mfe_projected, shap_drivers, direction_label, direction_probability, waived

What this seam guards, beyond the arithmetic:

* **The shared bundle.** Stage 4 resolves its sector ticker through the same
  `_sector_map` stage 7 uses and reads the *same* `uvest-market-context/1`
  artifact — one fetcher (`fetch_market_context.py`), two consumers (ADR-010
  decision 4). Dropping the sector series from the bundle must waive, which is
  only observable if the stage really reads it.
* **Feature/trainer identity.** Training and inference run one
  `feature_vector`; the committed calibration pins the feature order and class
  set, so a reordered `FEATURES` cannot silently scramble the booster's inputs.
* **Provenance is disclosed in the artifact**, not just in prose: the trainer
  writes `origin: sourced`, the corpus and the out-of-fold CV metrics into the
  calibration the stage loads.
* **The graded event is stated, not implied** (decision D, 2026-10-16): evals
  dimension 4 grades the target-versus-stop race, so the payload's two
  probability keys state that race at two different geometries —
  `direction_probability` is the booster's own P(TARGET_FIRST) at the
  reference ±3% band, and `prob_target_hit_22d` is *this trade's* target and
  stop read through the excursion pools. The latter is a conservative product
  of the two marginals (a disclosed lower bound on the first-touch race), and
  the seam pins that estimator against hand-built pools, plus a calibration
  whose reference band is absent or is not the stage's own.
* **Waiver discipline (BRD §10.4).** Absent model, absent/short OHLCV, absent
  or mis-schema'd bundle, unmapped symbol, unsupported action — each waives the
  whole payload (every metric `null`, exit 0) rather than fabricating a number.

Runtime strategy (support/pipeline.py): in-process import for behavior and
logic contracts, exactly one subprocess for the CLI contract, and no network.
"""

from __future__ import annotations

import copy
import json
import subprocess
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
from support.market_context import (  # noqa: E402
    DEFAULT_FIXTURE,
    NSEI,
    SECTOR,
    build_context,
)

SCRIPT = REPO_ROOT / "scripts" / "4_ml_direction.py"
OHLCV_FIXTURE = TESTS_DIR / "fixtures" / "reliance_daily_ohlcv.json"
CONTEXT_FIXTURE = DEFAULT_FIXTURE
MODEL = REPO_ROOT / "models" / "lgbm_direction_v1.txt"
CALIBRATION = REPO_ROOT / "models" / "lgbm_direction_v1.calibration.json"

CONTRACT_KEYS = {
    "prob_target_hit_22d", "price_cone_p10_p50_p90", "mae_projected",
    "mfe_projected", "shap_drivers", "direction_label",
    "direction_probability", "waived",
}
METRIC_KEYS = CONTRACT_KEYS - {"waived"}
CONE_KEYS = ("p10", "p25", "p50", "p75", "p90")
DIRECTION_LABELS = {"STRONG_TARGET_FIRST", "TARGET_FIRST", "STOP_FIRST",
                    "NEITHER"}

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


def run_stage4(payload: dict, *, ohlcv=OHLCV_FIXTURE, context=CONTEXT_FIXTURE,
               model=None, calibration=None):
    """Run stage 4 in-process against the named evidence (or its absence)."""
    module, _ = import_mode(4)
    argv = []
    if ohlcv is not None:
        argv.extend(["--ohlcv-fixture", str(ohlcv)])
    if context is not None:
        argv.extend(["--context-fixture", str(context)])
    if model is not None:
        argv.extend(["--model", str(model)])
    if calibration is not None:
        argv.extend(["--calibration", str(calibration)])
    with temp_input(payload, ".json") as path:
        return call_imported_main(module, ["--input", path, *argv])


def run_on_context(payload: dict, context: dict, **kwargs):
    """Run stage 4 against an in-memory bundle (the waiver lab)."""
    with temp_input(context, ".json") as path:
        return run_stage4(payload, context=Path(path), **kwargs)


class TestPayloadContract(unittest.TestCase):
    """A non-waived payload: exactly the contract keys, internally consistent."""

    @classmethod
    def setUpClass(cls):
        cls.payload, cls.code, cls.err = run_stage4(CANONICAL)
        if cls.code != 0 or not isinstance(cls.payload, dict):
            raise AssertionError(
                f"stage 4 failed on the canonical evidence: {cls.code} {cls.err}")

    def test_non_waived_payload_carries_exactly_the_contract_keys(self):
        self.assertFalse(self.payload["waived"], self.err)
        self.assertEqual(set(self.payload), CONTRACT_KEYS,
                         "the frozen TICK-004 §2 contract is exact")

    def test_cone_is_monotone_and_ordered(self):
        cone = self.payload["price_cone_p10_p50_p90"]
        self.assertEqual(tuple(cone), CONE_KEYS)
        prices = [cone[key] for key in CONE_KEYS]
        self.assertEqual(prices, sorted(prices),
                         f"cone must be monotone: {prices}")
        self.assertGreater(cone["p10"], 0.0)

    def test_excursions_straddle_the_entry(self):
        """MAE is adverse and MFE favourable, in ₹ around the entry."""
        self.assertLessEqual(self.payload["mae_projected"], 0.0)
        self.assertGreaterEqual(self.payload["mfe_projected"], 0.0)

    def test_probability_is_a_probability_and_the_label_is_canonical(self):
        probability = self.payload["prob_target_hit_22d"]
        self.assertGreaterEqual(probability, 0.0)
        self.assertLessEqual(probability, 1.0)
        direction = self.payload["direction_probability"]
        self.assertGreaterEqual(direction, 0.0)
        self.assertLessEqual(direction, 1.0)
        # Since decision D both keys state the *same* race at two geometries —
        # the reference ±3% band (the model's class probability) and this
        # trade's own levels (the pools) — so they may coincide numerically.
        # What they may never do is swap jobs: the trade-geometry estimator is
        # pinned on its own in `TestTheRaceProbabilityIsConservative`, and that
        # seam is what proves the graded number answers *this trade's* stop.
        self.assertIn(self.payload["direction_label"], DIRECTION_LABELS)

    def test_shap_drivers_are_at_most_five_ranked_contributions(self):
        drivers = self.payload["shap_drivers"]
        self.assertTrue(drivers, "a non-waived payload explains itself")
        self.assertLessEqual(len(drivers), 5)
        _, module = None, import_mode(4)[0]
        for driver in drivers:
            self.assertEqual(set(driver), {"feature", "value", "contribution"})
            self.assertIn(driver["feature"], module.FEATURES)
        magnitudes = [abs(d["contribution"]) for d in drivers]
        self.assertEqual(magnitudes, sorted(magnitudes, reverse=True))


class TestSharedBundleConsumption(unittest.TestCase):
    """Stage 4 reads the same bundle stage 7 classifies (ADR-010 decision 4)."""

    def test_sector_ticker_comes_from_the_shared_map(self):
        import _sector_map
        module, _ = import_mode(4)
        stage7, _ = import_mode(7)
        self.assertIs(module.SECTOR_INDEX_MAP, _sector_map.SECTOR_INDEX_MAP)
        self.assertIs(stage7.SECTOR_INDEX_MAP, _sector_map.SECTOR_INDEX_MAP)
        self.assertIn(SECTOR, module.SECTOR_INDEX_MAP.values())

    def test_the_stage_reads_the_bundles_nifty_and_sector_series(self):
        """Dropping the sector series must waive — proof the read is real."""
        module, _ = import_mode(4)
        payload, code, err = run_on_context(
            CANONICAL, build_context(sector_ticker=SECTOR))
        self.assertEqual(code, 0, err)
        self.assertFalse(payload["waived"], err)

        for dropped, reason in (
            (NSEI, f"series:{NSEI}:absent"),
            (SECTOR, f"series:{SECTOR}:absent"),
        ):
            with self.subTest(dropped=dropped):
                gone, code, err = run_on_context(
                    CANONICAL,
                    build_context(sector_ticker=SECTOR, drop=(dropped,)))
                self.assertEqual(code, 0, err)
                self.assertTrue(gone["waived"])
                self.assertIn(reason, err)
                for key in METRIC_KEYS:
                    self.assertIsNone(gone[key])

    def test_cross_asset_features_are_computed_from_the_bundle(self):
        module, _ = import_mode(4)
        context = build_context(sector_ticker=SECTOR)
        sector = {p["date"]: p["value"] for p in context["series"][SECTOR]["values"]}
        market = {p["date"]: p["value"] for p in context["series"][NSEI]["values"]}
        sessions = json.loads(OHLCV_FIXTURE.read_text(encoding="utf-8"))["sessions"]
        stock = {str(r["date"]): float(r["close"]) for r in sessions}
        windows = module.common_return_windows(stock, market, sector)
        self.assertIsNotNone(windows)
        vector = module.feature_vector(
            [float(r["close"]) for r in sessions], *windows)
        self.assertEqual(set(vector), set(module.FEATURES))
        # A market series with no usable variance carries no beta: the floor is
        # what keeps an authored flat index from reporting a beta of ~10.
        self.assertEqual(vector["beta_20_nifty"], 0.0)

    def test_a_bundle_without_a_common_window_waives_instead_of_guessing(self):
        context = build_context(sector_ticker=SECTOR)
        context["series"][NSEI]["values"] = context["series"][NSEI]["values"][-5:]
        payload, code, err = run_on_context(CANONICAL, context)
        self.assertEqual(code, 0, err)
        self.assertTrue(payload["waived"])
        self.assertIn("align:insufficient", err)

    def test_a_shifted_sector_axis_is_tolerated_on_the_common_window(self):
        """Stage 4 estimates on the *intersection* of the series' dates.

        Stage 7 needs the three index series on one axis (a 50-session ratio),
        so it waives on a misaligned axis. Stage 4's estimator is a joint
        return window: one shifted session is simply not a common session, and
        a window that still spans 20 of them is real evidence. Pinned here so
        the difference between the two consumers is a decision, not an
        oversight.
        """
        payload, code, err = run_on_context(
            CANONICAL, build_context(sector_ticker=SECTOR, misalign_sector=True))
        self.assertEqual(code, 0, err)
        self.assertFalse(payload["waived"], err)


class TestWaiverDiscipline(unittest.TestCase):
    """BRD §10.4: absence is disclosed, never filled with an invented number."""

    def assert_waived(self, payload, code, err):
        self.assertEqual(code, 0, "a waiver is exit 0, never a failure code")
        self.assertTrue(payload["waived"])
        self.assertEqual(set(payload), CONTRACT_KEYS)
        for key in METRIC_KEYS:
            self.assertIsNone(payload[key], key)
        self.assertTrue(err.strip(), "a waiver says why on stderr")

    def test_no_fixtures_at_all_waives(self):
        self.assert_waived(*run_stage4(CANONICAL, ohlcv=None, context=None))

    def test_absent_model_artifact_waives(self):
        payload, code, err = run_stage4(CANONICAL, model=REPO_ROOT / "nope.txt")
        self.assert_waived(payload, code, err)
        self.assertIn("model:booster-absent", err)

    def test_short_ohlcv_waives(self):
        short = {"symbol": "RELIANCE", "sessions": [
            {"date": f"2026-01-{i:02d}", "open": 1.0, "high": 1.0, "low": 1.0,
             "close": 1.0, "volume": 1} for i in range(1, 20)]}
        with temp_input(short, ".json") as path:
            payload, code, err = run_stage4(CANONICAL, ohlcv=Path(path))
        self.assert_waived(payload, code, err)
        self.assertIn("ohlcv:absent", err)

    def test_unmapped_symbol_waives(self):
        payload, code, err = run_stage4(make_input(symbol="NOT-A-LISTED-NAME"))
        self.assert_waived(payload, code, err)
        self.assertIn("symbol:unmapped:NOT-A-LISTED-NAME", err)

    def test_non_long_action_waives_rather_than_inverting_the_cone(self):
        payload, code, err = run_stage4(make_input(action="SELL"))
        self.assert_waived(payload, code, err)
        self.assertIn("action:unsupported:SELL", err)

    def test_a_mis_schemaed_bundle_waives(self):
        broken = build_context(sector_ticker=SECTOR)
        broken["schema"] = "uvest-market-context/0"
        payload, code, err = run_on_context(CANONICAL, broken)
        self.assert_waived(payload, code, err)
        self.assertIn("fixture:schema", err)


class TestFrozenConventions(unittest.TestCase):
    """The pure conventions the model and the cone are built on."""

    @staticmethod
    def window(sessions):
        """Forward bars for the label rule: `(high, low)` pairs, one per session."""
        return [{"date": f"2024-01-{i + 1:02d}", "open": 100.0, "close": 100.0,
                 "high": high, "low": low}
                for i, (high, low) in enumerate(sessions)]

    def test_the_race_label_is_first_touch_at_the_reference_band(self):
        """Decision D: the label is the ±3% race, first touch wins.

        The reference levels are today's BULL band read as *both* sides of a
        trade — +3% is the target, −3% the stop — so the label says which
        level the window printed first, exactly as the frozen `$derive` rule
        scores the benchmark's own trades (`resolution_of`).
        """
        module, _ = import_mode(4)
        entry = 100.0
        target, stop = entry * 1.03, entry * 0.97
        classify = module.classify_outcome
        # Target printed first.
        self.assertEqual(classify(entry, self.window([(104.0, 99.0),
                                                      (103.0, 102.0)])),
                         "TARGET_FIRST")
        # Stop printed first.
        self.assertEqual(classify(entry, self.window([(101.0, 96.0),
                                                      (100.0, 99.5)])),
                         "STOP_FIRST")
        # Neither level printed inside the horizon.
        self.assertEqual(classify(entry, self.window([(101.5, 98.5)] * 3)),
                         "NEITHER")
        # One bar carrying both levels resolves to the worse one — the same
        # conservative convention the frozen resolution rule uses.
        self.assertEqual(classify(entry, self.window([(104.0, 96.0)])),
                         "STOP_FIRST")
        # The levels themselves are inclusive touches (>= target, <= stop).
        self.assertEqual(classify(entry, self.window([(target, 99.0)])),
                         "TARGET_FIRST")
        self.assertEqual(classify(entry, self.window([(101.0, stop)])),
                         "STOP_FIRST")
        self.assertEqual(module.RACE_BAND_PCT, 3.0)
        self.assertEqual((target, stop), (103.0, 97.0))

    def test_the_race_scan_is_the_frozen_resolution_rule(self):
        """One implementation behind the label *and* the `$derive` rule.

        A corpus label and a benchmark trade's recorded resolution that scanned
        differently would grade two different events under one name — the exact
        drift `dataset_refs.resolution_of`'s docstring guards against.
        """
        module, _ = import_mode(4)
        bars = self.window([(104.0, 99.0), (103.0, 96.0)])
        self.assertEqual(module.first_touch_outcome(bars, 96.0, 103.0),
                         ("WIN", "target_first"))
        self.assertEqual(
            module.first_touch_outcome(self.window([(101.0, 96.0),
                                                    (104.0, 99.0)]),
                                       97.0, 103.0),
            ("LOSS", "stop_first"))
        self.assertEqual(module.first_touch_outcome(self.window([(101.0, 99.0)]),
                                                    97.0, 103.0),
                         ("TIME", "horizon_end"))
        # …and the benchmark's `$derive` rule really is *this* function's file:
        # two separately-loaded module objects are two instances of one source,
        # so the pin is where the callable is defined, not object identity.
        import importlib.util
        import inspect
        spec = importlib.util.spec_from_file_location(
            "dataset_refs_for_stage4", TESTS_DIR / "evals" / "dataset_refs.py")
        dr = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(dr)
        self.assertEqual(
            Path(inspect.getsourcefile(dr.resolution_of)).resolve(),
            Path(module.__file__).resolve(),
            "dataset_refs must alias stage 4's race scan, never keep a copy")

    def test_direction_label_promotes_only_at_the_frozen_threshold(self):
        module, _ = import_mode(4)
        self.assertEqual(
            module.direction_label({"TARGET_FIRST": 0.7, "STOP_FIRST": 0.2,
                                    "NEITHER": 0.1}),
            "STRONG_TARGET_FIRST")
        self.assertEqual(
            module.direction_label({"TARGET_FIRST": 0.6, "STOP_FIRST": 0.2,
                                    "NEITHER": 0.2}),
            "STRONG_TARGET_FIRST", "0.60 is inclusive")
        self.assertEqual(
            module.direction_label({"TARGET_FIRST": 0.59, "STOP_FIRST": 0.3,
                                    "NEITHER": 0.11}),
            "TARGET_FIRST")
        self.assertEqual(
            module.direction_label({"TARGET_FIRST": 0.3, "STOP_FIRST": 0.1,
                                    "NEITHER": 0.6}),
            "NEITHER")
        self.assertEqual(
            module.direction_label({"TARGET_FIRST": 0.1, "STOP_FIRST": 0.7,
                                    "NEITHER": 0.2}),
            "STOP_FIRST")

    def test_mixture_quantiles_are_monotone_in_the_level(self):
        module, _ = import_mode(4)
        probabilities = {"TARGET_FIRST": 0.3, "STOP_FIRST": 0.4, "NEITHER": 0.3}
        pairs = module._pooled(
            {"TARGET_FIRST": [-12.0, -6.0], "STOP_FIRST": [-1.0, 2.0],
             "NEITHER": [5.0, 11.0]},
            probabilities)
        levels = [i / 20 for i in range(21)]
        values = [module.mixture_quantile(pairs, level) for level in levels]
        self.assertEqual(values, sorted(values))

    def test_probability_at_least_is_the_upper_tail(self):
        module, _ = import_mode(4)
        pairs = [(1.0, 0.25), (2.0, 0.25), (3.0, 0.25), (4.0, 0.25)]
        self.assertAlmostEqual(module.mixture_prob_at_least(pairs, 3.0), 0.5)
        self.assertAlmostEqual(module.mixture_prob_at_least(pairs, 5.0), 0.0)
        self.assertAlmostEqual(module.mixture_prob_at_least(pairs, 0.0), 1.0)

    def test_stop_is_safe_only_when_it_sits_outside_the_projected_mae(self):
        module, _ = import_mode(4)
        # entry 100, projected MAE -8 => a stop at or below 92 is outside it
        self.assertTrue(module.stoploss_safely_below_mae(100.0, 92.0, -8.0))
        self.assertFalse(module.stoploss_safely_below_mae(100.0, 95.0, -8.0))

    @staticmethod
    def _session(index: int) -> dict:
        """A bar whose extremes lead its close, so a window's far end dominates."""
        return {"date": f"d{index:03d}", "open": 100.0,
                "high": 100.0 + index, "low": 100.0 - index,
                "close": 100.0 + index * 0.5}

    def test_forward_outcome_reads_the_forward_bars_the_gate_grades(self):
        """The excursions are the sessions' *extremes*, not their closes.

        `open/high/low/close` is what the corpus vintages carry and what the
        coverage gate reads forward, so a close-based excursion answers a
        strictly narrower question than the one graded (BRD §5.2 excursion
        realization): the worst low of the 22 sessions *after* the entry, the
        best high, and the terminal session's close.
        """
        module, _ = import_mode(4)
        sessions = [self._session(i) for i in range(30)]
        forward_return, mae, mfe = module.forward_outcome(sessions, 0)
        window = sessions[1:23]
        entry = sessions[0]["close"]
        self.assertEqual(len(window), module.HORIZON,
                         "the horizon is the frozen 22 sessions")
        self.assertAlmostEqual(forward_return,
                               (window[-1]["close"] / entry - 1.0) * 100.0)
        self.assertAlmostEqual(
            mae, (min(s["low"] for s in window) / entry - 1.0) * 100.0)
        self.assertAlmostEqual(
            mfe, (max(s["high"] for s in window) / entry - 1.0) * 100.0)
        # Session 23's bar is the widest one built: if the window ran past the
        # horizon both tails would move, so the horizon length is observable.
        beyond = sessions[23]
        self.assertGreater(mae, (beyond["low"] / entry - 1.0) * 100.0,
                           "measured on the window, not past its last session")
        self.assertLess(mfe, (beyond["high"] / entry - 1.0) * 100.0)
        # A bar path brackets its closes, which is the envelope's premise.
        closes = [(s["close"] / entry - 1.0) * 100.0 for s in window]
        self.assertLess(mae, min(closes))
        self.assertGreater(mfe, max(closes))
        self.assertLessEqual(mae, forward_return)
        self.assertGreaterEqual(mfe, forward_return)

    def test_the_entry_sessions_own_wicks_stay_out_of_the_window(self):
        """T_rec's own low is history; the excursion window opens after it."""
        module, _ = import_mode(4)
        entry_bar = {"date": "t", "open": 100.0, "high": 500.0, "low": 20.0,
                     "close": 100.0}
        sessions = [entry_bar] + [
            {"date": f"d{i}", "open": 100.0, "high": 101.0, "low": 99.0,
             "close": 100.0} for i in range(1, 23)]
        forward_return, mae, mfe = module.forward_outcome(sessions, 0)
        self.assertAlmostEqual(forward_return, 0.0)
        self.assertAlmostEqual(mae, -1.0)
        self.assertAlmostEqual(mfe, 1.0)

    def test_the_close_path_reading_is_the_alternative_being_measured(self):
        """The shipped atoms are the bars; the close path is the labelled other.

        `close_path_outcome` exists so the trainer can report what the choice of
        realization was worth on the same windows — it is not a second way to
        build a cone, so it must read the same window and agree on the terminal.
        """
        module, _ = import_mode(4)
        sessions = [self._session(i) for i in range(30)]
        bars = module.forward_outcome(sessions, 0)
        closes = module.close_path_outcome(sessions, 0)
        self.assertEqual(bars[0], closes[0], "one terminal, two realizations")
        self.assertLess(bars[1], closes[1], "the bar low is the wider tail")
        self.assertGreater(bars[2], closes[2])

    def test_a_close_based_path_never_understates_the_bar_path(self):
        """The realization correction widens, never narrows — per row."""
        module, _ = import_mode(4)
        sessions = [self._session(i) for i in range(30)]
        for index in (0, 3):
            with self.subTest(index=index):
                _ret, mae, mfe = module.forward_outcome(sessions, index)
                window = sessions[index + 1:index + 1 + module.HORIZON]
                entry = sessions[index]["close"]
                close_low = (min(s["close"] for s in window) / entry - 1.0) * 100.0
                close_high = (max(s["close"] for s in window) / entry - 1.0) * 100.0
                self.assertLessEqual(mae, min(0.0, close_low))
                self.assertGreaterEqual(mfe, max(0.0, close_high))

    def test_a_stop_inside_the_mae_warns_without_failing_the_stage(self):
        module, _ = import_mode(4)
        payload = make_input(stoploss=2985.0, live_price=2988.20)
        result, code, err = run_stage4(payload)
        self.assertEqual(code, 0)
        self.assertFalse(result["waived"])
        self.assertIn("[warn] stage 4", err)


class TestConeEnvelopeIsThePath(unittest.TestCase):
    """The envelope is the 22-session *path* the coverage gate grades (BRD §5.2).

    The quantity evals dimension 5 grades is forward price *action*: the tape's
    min and max must sit inside `[P10, P90]`. A cone inverted from the terminal
    return mixture answers a different question, and can only contain a path
    whose wicks stay inside the close's interval. These seams pin the definition
    on hand-built pools where the two answers differ, and pin the containment
    property on the shipped calibration's own pools.
    """

    @classmethod
    def setUpClass(cls):
        cls.module, _ = import_mode(4)
        cls.calibration = json.loads(CALIBRATION.read_text(encoding="utf-8"))

    def test_the_spec_maps_each_level_to_its_own_measure(self):
        spec = dict((key, measure) for key, measure, _level in self.module.CONE_SPEC)
        self.assertEqual(spec, {"p10": "mae", "p25": "mae", "p50": "return",
                                "p75": "mfe", "p90": "mfe"})
        levels = [level for _key, _measure, level in self.module.CONE_SPEC]
        self.assertEqual(levels, sorted(levels))
        # Each cone token names the calibration measure it inverts, once.
        self.assertEqual(self.module.CONE_MEASURES,
                         {"return": "forward_return_pct", "mae": "mae_pct",
                          "mfe": "mfe_pct"})

    def test_wicks_outside_the_close_widen_the_envelope(self):
        """Ten atoms, a flat close and symmetric wicks: the path is wider."""
        probs = {"TARGET_FIRST": 0.0, "STOP_FIRST": 0.0, "NEITHER": 1.0}
        samples = {
            "forward_return_pct": {"NEITHER": [0.0] * 10},
            "mae_pct": {"NEITHER": [float(-v) for v in range(10, 0, -1)]},
            "mfe_pct": {"NEITHER": [float(v) for v in range(1, 11)]},
        }
        cone = self.module.cone_envelope(samples, probs, 100.0)
        # ₹ from the mixtures: q10/q25 of the adverse atoms, q75/q90 of the
        # favourable ones, the median from the flat close.
        self.assertEqual(cone, {"p10": 90.0, "p25": 92.0, "p50": 100.0,
                                "p75": 108.0, "p90": 109.0})
        prices = [cone[key] for key, _m, _l in self.module.CONE_SPEC]
        self.assertEqual(prices, sorted(prices), "the envelope must be monotone")
        self.assertLess(cone["p10"], 100.0, "the adverse tail is a path low")
        self.assertGreater(cone["p90"], 100.0, "the favourable tail is a path high")

    def test_a_flat_path_envelope_collapses_to_the_terminal_interval(self):
        """No wicks ⇒ the two definitions agree; the widening is not a fudge."""
        probs = {"TARGET_FIRST": 0.0, "STOP_FIRST": 0.0, "NEITHER": 1.0}
        flat = [float(v) for v in range(-5, 5)]
        samples = {measure: {"NEITHER": list(flat)} for measure in
                   ("forward_return_pct", "mae_pct", "mfe_pct")}
        cone = self.module.cone_envelope(samples, probs, 100.0)
        expected = {key: round(100.0 * (1.0 + self.module.mixture_quantile(
            self.module._pooled(samples["forward_return_pct"], probs), level) / 100.0), 2)
            for key, _m, level in self.module.CONE_SPEC}
        self.assertEqual(cone, expected)

    def test_the_shipped_envelope_always_contains_the_terminal_interval(self):
        """The property that makes the re-definition a widening, on real pools.

        For any mixture of the committed calibration's pools the envelope's P10
        is at or below the terminal P10, and its P90 at or above the terminal
        P90 — the coupling (mae ≤ terminal ≤ mfe per row, one weight vector)
        guarantees it, and no probability mixture can break it.
        """
        samples = self.calibration["samples"]
        mixtures = [{"TARGET_FIRST": 1.0, "STOP_FIRST": 0.0, "NEITHER": 0.0},
                    {"TARGET_FIRST": 0.0, "STOP_FIRST": 1.0, "NEITHER": 0.0},
                    {"TARGET_FIRST": 0.0, "STOP_FIRST": 0.0, "NEITHER": 1.0},
                    {"TARGET_FIRST": 1 / 3, "STOP_FIRST": 1 / 3, "NEITHER": 1 / 3},
                    {"TARGET_FIRST": 0.6, "STOP_FIRST": 0.3, "NEITHER": 0.1}]
        for probs in mixtures:
            with self.subTest(mixture=probs):
                cone = self.module.cone_envelope(samples, probs, 2450.0)
                returns = self.module._pooled(samples["forward_return_pct"], probs)
                prices = [cone[key] for key, _m, _l in self.module.CONE_SPEC]
                self.assertEqual(prices, sorted(prices))
                terminal_low = 2450.0 * (
                    1.0 + self.module.mixture_quantile(returns, 0.10) / 100.0)
                terminal_high = 2450.0 * (
                    1.0 + self.module.mixture_quantile(returns, 0.90) / 100.0)
                self.assertLessEqual(cone["p10"], terminal_low + 0.02)
                self.assertGreaterEqual(cone["p90"], terminal_high - 0.02)

    def test_the_payload_cone_comes_from_that_envelope(self):
        """The shipped payload is the envelope, not a second cone by another rule."""
        payload, code, err = run_stage4(CANONICAL)
        self.assertEqual(code, 0, err)
        self.assertFalse(payload["waived"], err)
        cone = payload["price_cone_p10_p50_p90"]
        prices = [cone[key] for key, _m, _l in self.module.CONE_SPEC]
        self.assertEqual(prices, sorted(prices))
        # A terminal-return cone would sit inside this one; the path envelope is
        # therefore never *narrower* than the entry price on the downside, and
        # both tails are real ₹ levels around the entry.
        live = float(CANONICAL["live_price"])
        self.assertLess(cone["p10"], live)
        self.assertGreater(cone["p90"], live)


class TestTheConePoolsComeFromTheTradesOwnSector(unittest.TestCase):
    """The envelope's atoms are the trade's own sector's excursions.

    Pooling every sector into one mixture answers "how far does an average NSE
    name travel" while gate 5 asks what happened to *this* trade, and a bank's
    22-session excursion distribution is not an IT stock's: the crate that is
    priced off ^NSEBANK trades a different dispersion from one priced off
    ^CNXIT. The conditioning is a *selection* between two blocks the calibration
    already carries — the trade's sector's when it is deep enough to stand for a
    quantile, the pooled one otherwise — so the fallback is a disclosure rather
    than a waiver, and a calibration that carries no sector blocks still builds
    a cone.
    """

    @classmethod
    def setUpClass(cls):
        cls.module, _ = import_mode(4)

    def block(self, *, mae: float, mfe: float, atoms: int | None = None) -> dict:
        count = atoms if atoms is not None else self.module.MIN_SECTOR_ATOMS_PER_CLASS
        return {
            "forward_return_pct": {cls: [0.0] * count
                                   for cls in self.module.CLASSES},
            "mae_pct": {cls: [mae] * count for cls in self.module.CLASSES},
            "mfe_pct": {cls: [mfe] * count for cls in self.module.CLASSES},
        }

    def test_a_deep_enough_sector_block_is_the_one_the_cone_reads(self):
        pooled = self.block(mae=-5.0, mfe=5.0)
        sector = self.block(mae=-30.0, mfe=30.0)
        chosen = self.module.samples_for(
            {"samples": pooled, "samples_by_sector": {SECTOR: sector}}, SECTOR)
        self.assertIs(chosen, sector)
        self.assertEqual(chosen["mae_pct"]["STOP_FIRST"][0], -30.0)

    def test_a_thin_or_incomplete_sector_block_falls_back_to_the_pooled_pools(self):
        pooled = self.block(mae=-5.0, mfe=5.0)
        thin = self.block(mae=-30.0, mfe=30.0,
                          atoms=self.module.MIN_SECTOR_ATOMS_PER_CLASS - 1)
        missing_class = self.block(mae=-30.0, mfe=30.0)
        del missing_class["mae_pct"]["STOP_FIRST"]
        empty_class = self.block(mae=-30.0, mfe=30.0)
        empty_class["mfe_pct"]["TARGET_FIRST"] = []
        missing_measure = self.block(mae=-30.0, mfe=30.0)
        del missing_measure["forward_return_pct"]
        for label, block in (("thin", thin), ("missing class", missing_class),
                             ("empty class", empty_class),
                             ("missing measure", missing_measure),
                             ("absent", None)):
            with self.subTest(block=label):
                calibration = {"samples": pooled,
                               "samples_by_sector": {SECTOR: block}}
                self.assertIs(self.module.samples_for(calibration, SECTOR),
                              pooled,
                              "a block that cannot carry a quantile must "
                              "fall back, never waive and never guess")

    def test_an_unknown_or_absent_sector_falls_back(self):
        pooled = self.block(mae=-5.0, mfe=5.0)
        for ticker in (None, "^CNXIT"):
            with self.subTest(ticker=ticker):
                calibration = {"samples": pooled,
                               "samples_by_sector": {SECTOR: self.block(mae=-1.0,
                                                                        mfe=1.0)}}
                self.assertIs(self.module.samples_for(calibration, ticker), pooled)

    def test_a_calibration_without_sector_blocks_still_builds_a_cone(self):
        pooled = self.block(mae=-5.0, mfe=5.0)
        self.assertIs(self.module.samples_for({"samples": pooled}, SECTOR), pooled)

    def test_the_payload_cone_reads_the_trades_sector_block(self):
        """End to end: the shipped booster, a widened block for this sector.

        The pooled block is left untouched, so any movement in the payload is
        the *selection* and nothing else — a stage that kept reading
        `samples` would show no difference at all.
        """
        shipped = json.loads(CALIBRATION.read_text(encoding="utf-8"))
        wide = copy.deepcopy(shipped)
        wide["samples_by_sector"] = {SECTOR: self.block(mae=-40.0, mfe=40.0)}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "calibration.json"
            path.write_text(json.dumps(wide), encoding="utf-8")
            payload, code, err = run_stage4(CANONICAL, calibration=path)
        self.assertEqual(code, 0, err)
        self.assertFalse(payload["waived"], err)
        baseline, code, err = run_stage4(CANONICAL)
        self.assertEqual(code, 0, err)
        self.assertLess(payload["price_cone_p10_p50_p90"]["p10"],
                        baseline["price_cone_p10_p50_p90"]["p10"],
                        "the sector block's deeper adverse tail must reach P10")
        self.assertGreater(payload["price_cone_p10_p50_p90"]["p90"],
                           baseline["price_cone_p10_p50_p90"]["p90"])
        self.assertLess(payload["mae_projected"], baseline["mae_projected"])
        self.assertEqual(set(payload), CONTRACT_KEYS,
                         "the payload contract does not grow a pool key")


class TestModelArtifactsAreDisclosed(unittest.TestCase):
    """The committed booster's provenance is in the artifact, not in a comment."""

    @classmethod
    def setUpClass(cls):
        if not MODEL.exists() or not CALIBRATION.exists():
            raise AssertionError(
                "stage-4 model artifacts missing — rebuild them with "
                "`python tests/evals/train_direction_model.py`")
        cls.calibration = json.loads(CALIBRATION.read_text(encoding="utf-8"))

    def test_calibration_pins_the_feature_order_and_classes(self):
        module, _ = import_mode(4)
        self.assertEqual(self.calibration["schema"], module.CALIBRATION_SCHEMA)
        self.assertEqual(self.calibration["features"], list(module.FEATURES))
        self.assertEqual(self.calibration["classes"], list(module.CLASSES))
        self.assertEqual(self.calibration["horizon"], module.HORIZON)
        for measure in ("forward_return_pct", "mae_pct", "mfe_pct"):
            self.assertEqual(set(self.calibration["samples"][measure]),
                             set(module.CLASSES))

    def test_provenance_discloses_the_sourced_corpus_and_the_cv(self):
        provenance = self.calibration["provenance"]
        self.assertEqual(provenance["origin"], "sourced")
        self.assertEqual(provenance["builder"],
                         "tests/evals/train_direction_model.py")
        self.assertGreater(provenance["corpus"]["training_rows"], 0)
        self.assertTrue(provenance["corpus"]["symbols"])
        self.assertEqual(provenance["cv"]["scheme"],
                         "purged group time-series")
        self.assertEqual(provenance["cv"]["embargo_sessions"], 5)
        self.assertGreater(provenance["cv"]["oof_rows"], 0)

    def test_the_cv_disclosure_names_the_excursion_realization(self):
        """A coverage figure means nothing without the object it covered.

        The same envelope scores differently against a close path and against
        the sessions' bar path, so the disclosure has to state which one the
        out-of-fold coverage was measured on — the realization the coverage
        gate reads (BRD §5.2 excursion realization, evals.md §3 #5).
        """
        cv = self.calibration["provenance"]["cv"]
        self.assertEqual(cv["excursion_realization"],
                         "session extremes (low/high) against the entry close")
        self.assertIn("session extremes", cv["cone_definition"])
        self.assertIn("oof_cone_coverage_pct", cv)
        self.assertGreater(cv["oof_rows"], 0)

    def test_the_calibration_carries_the_reference_race_band(self):
        """The reference band is required: it is what the label was measured at.

        Decision D retired φ (P(up) is no longer graded) and replaced it with
        the ±3% band the corpus rows' TARGET_FIRST / STOP_FIRST / NEITHER
        labels were decided at — the label rule and the calibration that ships
        it cannot disagree without the artifact lying about its own classes.
        """
        band = self.calibration["race_band_pct"]
        module, _ = import_mode(4)
        self.assertEqual(band, module.RACE_BAND_PCT)
        self.assertEqual(self.calibration["classes"], list(module.CLASSES))
        self.assertNotIn("neutral_up_share", self.calibration,
                         "the retired direction bridge must not survive")

    def test_a_calibration_without_the_race_band_is_unusable(self):
        module, _ = import_mode(4)
        broken = dict(self.calibration)
        broken.pop("race_band_pct", None)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "calibration.json"
            path.write_text(json.dumps(broken), encoding="utf-8")
            booster, calibration, reason = module.load_model(MODEL, path)
        self.assertIsNone(booster)
        self.assertEqual(reason, "model:calibration-fields")
        for bad in (-0.1, 0.0, "three", True, None):
            broken = dict(self.calibration)
            broken["race_band_pct"] = bad
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "calibration.json"
                path.write_text(json.dumps(broken), encoding="utf-8")
                _, _, reason = module.load_model(MODEL, path)
            self.assertEqual(reason, "model:calibration-fields", bad)
        # A band the stage would not label at is the same lie as a missing one.
        broken = dict(self.calibration)
        broken["race_band_pct"] = module.RACE_BAND_PCT + 1.0
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "calibration.json"
            path.write_text(json.dumps(broken), encoding="utf-8")
            _, _, reason = module.load_model(MODEL, path)
        self.assertEqual(reason, "model:calibration-fields")

    def test_calibration_and_booster_load_together_or_not_at_all(self):
        module, _ = import_mode(4)
        booster, calibration, reason = module.load_model(MODEL, CALIBRATION)
        self.assertIsNone(reason)
        self.assertEqual(booster.num_feature(), len(module.FEATURES))
        _, _, reason = module.load_model(MODEL, REPO_ROOT / "nope.json")
        self.assertEqual(reason, "model:calibration-absent")

    def test_the_model_file_is_line_fed_and_git_pins_it_that_way(self):
        """A CRLF rewrite silently corrupts the booster ('expect a tree here')."""
        raw = MODEL.read_bytes()
        self.assertNotIn(b"\r\n", raw)
        attributes = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("models/*.txt -text", attributes)


class TestCliAndRecording(unittest.TestCase):
    """One real invocation: the canonical CLI shape, and --record byte-exactness."""

    def test_cli_invocation_is_the_canonical_shape(self):
        with temp_input(CANONICAL, ".json") as path:
            proc = subprocess_mode(SCRIPT, [
                "--input", path,
                "--ohlcv-fixture", str(OHLCV_FIXTURE),
                "--context-fixture", str(CONTEXT_FIXTURE),
            ])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(set(payload), CONTRACT_KEYS)
        self.assertFalse(payload["waived"])

    def test_waived_run_keeps_stdout_pure_json_and_explains_on_stderr(self):
        with temp_input(CANONICAL, ".json") as path:
            proc = subprocess_mode(SCRIPT, ["--input", path])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["waived"])
        self.assertIn("[waived] stage 4", proc.stderr)

    def test_identical_evidence_is_byte_identical(self):
        first, code, err = run_stage4(CANONICAL)
        second, code2, err2 = run_stage4(CANONICAL)
        self.assertEqual((code, code2), (0, 0))
        self.assertEqual(json.dumps(first, indent=2),
                         json.dumps(second, indent=2))

    def test_record_writes_out_4_ml_json_matching_stdout(self):
        """The bundle holds the artifact's own bytes, not a re-serialisation.

        Compared as text, which is how the shared `_recorder` writes every
        stage's bundle (stage 7's seam reads it back the same way) — the
        recorded JSON and the stdout JSON are the same document.
        """
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "bundle"
            with temp_input(CANONICAL, ".json") as path:
                proc = subprocess_mode(SCRIPT, [
                    "--input", path,
                    "--ohlcv-fixture", str(OHLCV_FIXTURE),
                    "--context-fixture", str(CONTEXT_FIXTURE),
                    "--record", str(bundle),
                ])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            artifact = bundle / "out_4_ml.json"
            self.assertTrue(artifact.exists(), "recorded stage-4 artifact missing")
            self.assertEqual(artifact.read_text(encoding="utf-8"), proc.stdout)
            manifest = json.loads(
                (bundle / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["stages"][-1]["stage"], 4)

    def test_import_writes_nothing_to_stdout(self):
        """Main-guard discipline: importing the stage performs no work."""
        proc = subprocess.run(
            [sys.executable, "-c",
             "import importlib.util,sys;"
             "spec=importlib.util.spec_from_file_location('s4',"
             f"{str(SCRIPT)!r});"
             "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)"],
            capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")


class TestTheRaceProbabilityIsConservative(unittest.TestCase):
    """Decision D's prediction side (2026-10-16) — the number gate 4 grades.

    `prob_target_hit_22d` must answer *this trade's* race: the probability
    that the tape reaches the trade's target without first printing its stop.
    The estimator is the class-weighted product of the two excursion
    marginals — P(best high ≥ target) × P(worst low > −stop) — which is
    **at most** the true first-touch probability (the two events are
    positively associated), so it is a disclosed lower bound, never a
    tuned-up one. The calibration ships the disclosure in `race_estimator`.
    """

    @classmethod
    def setUpClass(cls):
        cls.module, _ = import_mode(4)

    @staticmethod
    def pools(mae: list[float], mfe: list[float]) -> dict:
        """One class holding every atom, so the arithmetic is hand-checkable."""
        return {
            "forward_return_pct": {"TARGET_FIRST": [0.0]},
            "mae_pct": {"TARGET_FIRST": list(mae)},
            "mfe_pct": {"TARGET_FIRST": list(mfe)},
        }

    def weights(self, target: float = 1.0) -> dict:
        return {"TARGET_FIRST": target, "STOP_FIRST": 0.0, "NEITHER": 0.0}

    def test_it_is_the_product_of_the_two_marginals(self):
        # mfe atoms: two of them reach 5%, one does not → P(reach) = 2/3.
        # mae atoms: two hold above −4%, one breaches → P(hold) = 2/3.
        value = self.module.race_probability(
            self.weights(), self.pools([-2.0, -3.0, -9.0], [2.0, 5.0, 8.0]),
            target_pct=5.0, stop_pct=4.0)
        self.assertAlmostEqual(value, (2 / 3) * (2 / 3), places=6)

    def test_a_stop_already_beyond_the_market_wins_no_race(self):
        """stop_pct ≤ 0 means the stop sits at/above the entry: lost on print."""
        for stop_pct in (0.0, -1.5):
            with self.subTest(stop_pct=stop_pct):
                self.assertEqual(
                    self.module.race_probability(
                        self.weights(), self.pools([-1.0], [50.0]),
                        target_pct=2.0, stop_pct=stop_pct),
                    0.0)

    def test_a_harder_target_lowers_it_and_a_deeper_stop_raises_it(self):
        pools = self.pools([-2.0, -6.0, -12.0], [3.0, 9.0, 15.0])
        easier = self.module.race_probability(
            self.weights(), pools, target_pct=3.0, stop_pct=7.0)
        harder = self.module.race_probability(
            self.weights(), pools, target_pct=9.0, stop_pct=7.0)
        deeper = self.module.race_probability(
            self.weights(), pools, target_pct=3.0, stop_pct=13.0)
        tighter = self.module.race_probability(
            self.weights(), pools, target_pct=3.0, stop_pct=1.0)
        self.assertGreater(easier, harder, "a target 9% away is likelier to "
                           "lose than one 3% away, all else equal")
        self.assertGreater(deeper, easier, "a stop no atom breaches cannot "
                           "cost the race anything")
        self.assertLess(tighter, easier, "a stop every atom breaches wins "
                        "nothing, however reachable the target is")

    def test_it_never_exceeds_either_marginal(self):
        """A lower bound may not exceed either event it multiplies."""
        pools = self.pools([-2.0, -6.0, -12.0], [3.0, 9.0, 15.0])
        weights = self.weights()
        for target_pct, stop_pct in ((3.0, 7.0), (9.0, 7.0), (15.0, 1.0),
                                     (5.0, 5.0), (14.9, 13.0)):
            with self.subTest(target=target_pct, stop=stop_pct):
                value = self.module.race_probability(
                    weights, pools, target_pct, stop_pct)
                reached = self.module.mixture_prob_at_least(
                    self.module._pooled(pools["mfe_pct"], weights), target_pct)
                held = self.module.mixture_prob_at_least(
                    self.module._pooled(pools["mae_pct"], weights), -stop_pct)
                self.assertGreaterEqual(value, 0.0)
                self.assertLessEqual(value, min(reached, held))

    def test_the_graded_number_answers_this_trades_geometry(self):
        """End to end: move the trade's target and stop, the graded key moves
        with them while the reference-band key does not — the proof that gate
        4 is graded on the trade's own race and not on a label-space echo."""
        base, code, err = run_stage4(CANONICAL)
        self.assertEqual(code, 0, err)
        self.assertFalse(base["waived"], err)
        harder, code, err = run_stage4(make_input(target=4000.0, stoploss=2950.0))
        self.assertEqual(code, 0, err)
        self.assertFalse(harder["waived"], err)
        self.assertLess(harder["prob_target_hit_22d"], base["prob_target_hit_22d"],
                        "a target 29% away cannot be likelier than one 4.4% away")
        self.assertEqual(harder["direction_probability"],
                         base["direction_probability"],
                         "the reference-band key must not follow the trade's levels")


if __name__ == "__main__":
    unittest.main()
