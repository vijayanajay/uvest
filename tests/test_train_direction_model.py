"""Seam for `tests/evals/train_direction_model.py` (the sourced stage-4 booster).

What this pins, and why each half matters:

* **Point-in-time rows.** A row's cross-asset features may not depend on a
  session after that row's date. Asserted by equivalence: the same corpus with
  the market and sector series cut short of a regime change must produce
  identical feature vectors for every row that survives — and no row at all past
  the cut. A trainer that had let the full series in would differ there, so the
  assertion fails if the truncation is ever dropped.
* **The purged group time-series split (BRD §5.2).** Two rows of one symbol
  share a sector series and neighbouring dates, so an unpurged fold would carry
  one row's label inside another's features. The scheme is asserted on a
  hand-built calendar (exact membership) *and* as a property over real rows
  (no training row's label window reaches its fold on its own symbol's
  calendar, nothing inside the embargo tail, every row tested exactly once).
* **Sourced provenance and replaceability.** The committed artifact must
  declare `origin: sourced`, the corpus it was built from, and the out-of-fold
  metrics — and training twice must produce the same bytes, so replacing the
  artifact is a re-run rather than a hand-edited file.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EVALS = REPO_ROOT / "tests" / "evals"
GOLDEN_DATASET = EVALS / "golden_dataset.json"

sys.path.insert(0, str(EVALS))
import dataset_refs  # noqa: E402  (the tape reader gate 5's scope grades through)
CORPUS_DIR = REPO_ROOT / "tests" / "fixtures" / "history"
SHIPPED_MODEL = REPO_ROOT / "models" / "lgbm_direction_v1.txt"
SHIPPED_CALIBRATION = REPO_ROOT / "models" / "lgbm_direction_v1.calibration.json"


def load_trainer():
    spec = importlib.util.spec_from_file_location(
        "uvest_train_direction_model", EVALS / "train_direction_model.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_stage4():
    spec = importlib.util.spec_from_file_location(
        "uvest_stage4_ml_direction", REPO_ROOT / "scripts" / "4_ml_direction.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------
# A tiny sourced-shaped corpus (in memory, deterministic, no network)
# --------------------------------------------------------------------------
START = date(2021, 1, 4)
SESSIONS = 300
# Where the sector series flips regime. Rows exist from session 199 (stage 4's
# 200-session feature floor) to session 277 (22 sessions of forward label), so
# this index leaves rows on both sides of the change.
REGIME_INDEX = 210


def _dates(count: int) -> list[str]:
    return [(START + timedelta(days=i)).isoformat() for i in range(count)]


def _series(count: int, *, factor: float, seed: float,
            invert_after: int | None = None) -> list[tuple[str, float]]:
    """A deterministic walk whose returns load on a shared factor."""
    value = 100.0
    out: list[tuple[str, float]] = []
    for i, day in enumerate(_dates(count)):
        step = 0.004 * math.sin(i / 3.0 + seed) + 0.002 * math.sin(i / 11.0)
        if invert_after is not None and i >= invert_after:
            step = -3.0 * step
        value *= 1.0 + factor * step
        out.append((day, round(value, 4)))
    return out


def bars(series: list[tuple[str, float]], spread: float = 0.01) -> list[dict]:
    """A close walk as sessions with wicks — the shape the corpus carries.

    The trained cone's excursions are the bars' extremes, so a seam whose corpus
    is close-only is a seam that no longer resembles the committed vintages.
    """
    return [{"date": day, "high": round(close * (1.0 + spread), 4),
             "low": round(close * (1.0 - spread), 4), "close": close}
            for day, close in series]


def synthetic_corpus(*, truncate_at: int | None = None,
                     invert: bool = True) -> tuple[dict, dict]:
    """`(stocks, indices)` — real shapes, two symbols, one served sector index.

    Stocks are the corpus's bar shape (`date/high/low/close`), the indices the
    close shape the tier-1 source serves them in: the features read one
    realization, the label's excursions the other.
    """
    market = _series(SESSIONS, factor=1.0, seed=0.0)
    sector = _series(SESSIONS, factor=1.0, seed=1.7,
                     invert_after=REGIME_INDEX if invert else None)
    stocks = {
        "HDFCBANK": bars(_series(SESSIONS, factor=1.4, seed=0.5)),
        "SBIN": bars(_series(SESSIONS, factor=0.9, seed=2.3)),
    }
    if truncate_at is not None:
        market = market[:truncate_at]
        sector = sector[:truncate_at]
    indices = {
        "^NSEI": market,
        "^INDIAVIX": [(d, 14.0 + i % 5) for i, (d, _) in enumerate(market)],
        "^NSEBANK": sector,
    }
    return stocks, indices


def write_corpus(directory: Path, stocks: dict, indices: dict) -> Path:
    history = directory / "history"
    history.mkdir(parents=True, exist_ok=True)
    for symbol, series in stocks.items():
        (history / f"{symbol}_202204.json").write_text(
            json.dumps({"symbol": symbol, "exchange": "NSE", "interval": "1d",
                        "source": "yfinance", "rec_date": series[-1]["date"],
                        "sessions": series}),
            encoding="utf-8")
    (history / "indices_daily.json").write_text(
        json.dumps({"schema": "uvest-index-corpus/1",
                    "source": "yahoo:yf.download (yfinance daily closes, source=yfinance)",
                    "fetched_on": "2026-10-14", "window": None,
                    "tickers": {ticker: {
                        "unit": "index points", "sessions": len(values),
                        "first_date": values[0][0], "last_date": values[-1][0],
                        "values": [{"date": d, "close": c} for d, c in values]}
                        for ticker, values in indices.items()},
                    "absent": {}, "provenance": {"origin": "sourced"}}),
        encoding="utf-8")
    return history


class TestGroundTruthIsPointInTime(unittest.TestCase):
    """A row sees its own session and nothing after it."""

    @classmethod
    def setUpClass(cls):
        cls.trainer = load_trainer()
        cls.stage4 = load_stage4()
        cls.sector_map = cls.trainer.load_sector_map()

    def _rows(self, *, truncate_at: int | None = None, invert: bool = True):
        stocks, indices = synthetic_corpus(truncate_at=truncate_at, invert=invert)
        rows, disclosure = self.trainer.build_rows(
            self.stage4, self.sector_map, stocks, indices)
        return rows, disclosure

    def test_the_label_excursions_are_the_bars_not_the_closes(self):
        """`mae_pct`/`mfe_pct` are the window's extremes — the gate's object.

        Recomputed independently here from the same series: the worst low of the
        22 sessions after the row's own date, the best high, both against that
        session's close. A close-based label is strictly milder, which is what
        made the shipped envelope narrower than the quantity it was graded on.
        """
        rows, _ = self._rows()
        stocks, _ = synthetic_corpus()
        self.assertTrue(rows)
        for row in rows[:20]:
            series = stocks[row["symbol"]]
            index = [s["date"] for s in series].index(row["date"])
            entry = series[index]["close"]
            window = series[index + 1:index + 1 + self.stage4.HORIZON]
            self.assertAlmostEqual(
                row["mae_pct"],
                min(0.0, (min(s["low"] for s in window) / entry - 1.0) * 100.0),
                places=6)
            self.assertAlmostEqual(
                row["mfe_pct"],
                max(0.0, (max(s["high"] for s in window) / entry - 1.0) * 100.0),
                places=6)
            self.assertLessEqual(
                row["mae_pct"],
                (min(s["close"] for s in window) / entry - 1.0) * 100.0,
                "the bar path is never milder than the close path")
            self.assertLessEqual(row["mae_pct"], row["forward_return_pct"])
            self.assertGreaterEqual(row["mfe_pct"], row["forward_return_pct"])

    def test_the_label_is_the_reference_band_race_on_the_rows_own_window(self):
        """Decision D: `class` is which ±3% level printed *first*.

        Recomputed here from the row's own forward bars with the frozen
        convention (a bar carrying both levels resolves to the stop), so a
        label that quietly became a terminal-return class again — or one that
        scanned in a different order than the benchmark's recorded
        `resolution_outcome` — fails against the tape it was built from.
        """
        rows, _ = self._rows()
        stocks, _ = synthetic_corpus()
        self.assertTrue(rows)
        seen: set[str] = set()
        for row in rows:
            series = stocks[row["symbol"]]
            index = [s["date"] for s in series].index(row["date"])
            entry = series[index]["close"]
            window = series[index + 1:index + 1 + self.stage4.HORIZON]
            target, stop = entry * 1.03, entry * 0.97
            expected = "NEITHER"
            for bar in window:
                if bar["low"] <= stop:
                    expected = "STOP_FIRST"
                    break
                if bar["high"] >= target:
                    expected = "TARGET_FIRST"
                    break
            self.assertEqual(row["class"], expected,
                             f"{row['symbol']} {row['date']}")
            seen.add(row["class"])
        self.assertTrue(seen & {"TARGET_FIRST", "STOP_FIRST"},
                        "this corpus was expected to exercise both race arms")

    def test_a_row_never_reads_a_session_after_its_date(self):
        full, _ = self._rows()
        cut, _ = self._rows(truncate_at=REGIME_INDEX)
        self.assertTrue(full and cut)
        cutoff_day = _dates(SESSIONS)[REGIME_INDEX - 1]
        self.assertLessEqual(max(row["date"] for row in cut), cutoff_day,
                             "a row with no market session of its own must not exist")
        by_key = {(row["symbol"], row["date"]): row["features"] for row in full}
        self.assertTrue(cut, "the truncated corpus produced no rows to compare")
        for row in cut:
            self.assertEqual(
                row["features"], by_key[(row["symbol"], row["date"])],
                f"{row['symbol']} {row['date']}: the regime change after the row's "
                "own date leaked into its cross-asset features")
        # ...and the regime change is real: rows after it differ from a corpus
        # where it never happened. Without this the equivalence above could pass
        # on a corpus that never varied.
        flat, _ = self._rows(invert=False)
        flat_by_key = {(row["symbol"], row["date"]): row["features"]
                       for row in flat}
        late = [row for row in full if row["date"] > cutoff_day]
        self.assertTrue(late)
        changed = [row for row in late
                   if row["features"] != flat_by_key[(row["symbol"], row["date"])]]
        self.assertTrue(changed, "the synthetic regime change altered no row")

    def test_rows_stop_before_the_forward_label_runs_out(self):
        rows, _ = self._rows(truncate_at=None)
        last = _dates(SESSIONS)[-1]
        self.assertLess(max(row["date"] for row in rows), last)
        self.assertTrue(all(row["mfe_pct"] >= 0.0 >= row["mae_pct"] for row in rows),
                        "MAE is adverse and MFE favourable, on every row")

    def test_an_unserved_sector_index_excludes_its_symbols_with_a_reason(self):
        stocks, indices = synthetic_corpus()
        stocks["RELIANCE"] = bars(_series(SESSIONS, factor=1.1, seed=3.1))
        rows, disclosure = self.trainer.build_rows(
            self.stage4, self.sector_map, stocks, indices)
        self.assertIn("RELIANCE", disclosure["skipped_symbols"])
        self.assertIn("^CNXENERGY", disclosure["skipped_symbols"]["RELIANCE"])
        self.assertNotIn("RELIANCE", {row["symbol"] for row in rows})

    def test_a_short_series_is_excluded_with_its_length(self):
        stocks, indices = synthetic_corpus()
        stocks["SBIN"] = stocks["SBIN"][:150]
        _, disclosure = self.trainer.build_rows(
            self.stage4, self.sector_map, stocks, indices)
        self.assertIn("SBIN", disclosure["skipped_symbols"])
        self.assertIn("150 session", disclosure["skipped_symbols"]["SBIN"])

    def _vintage(self, history: Path, path: Path, symbol: str,
                 sessions: list[dict]) -> None:
        path.write_text(
            json.dumps({"symbol": symbol, "exchange": "NSE", "interval": "1d",
                        "source": "yfinance", "rec_date": sessions[-1]["date"],
                        "sessions": sessions}),
            encoding="utf-8")

    def test_vintages_are_merged_once_per_date(self):
        stocks = {"SBIN": bars(_series(SESSIONS, factor=0.9, seed=2.3))}
        indices = synthetic_corpus()[1]
        with tempfile.TemporaryDirectory() as tmp:
            history = write_corpus(Path(tmp), stocks, indices)
            # A second vintage of the same symbol: identical bars for the shared
            # window (the normal case — these are two fetches of one tape), plus
            # one extra session at the end.
            series = stocks["SBIN"] + [{"date": _dates(SESSIONS + 1)[-1],
                                        "high": 1000.0, "low": 990.0,
                                        "close": 999.0}]
            self._vintage(history, history / "SBIN_202301.json", "SBIN", series)
            merged, meta = self.trainer.load_stock_corpus(history)
        days = [session["date"] for session in merged["SBIN"]]
        self.assertEqual(len(days), len(set(days)), "a date was loaded twice")
        self.assertEqual(meta["files"]["SBIN"], 2)
        self.assertEqual(meta["clashes"], [], "identical bars are not a clash")

    def test_a_divergent_vintage_is_disclosed_not_averaged(self):
        stocks = {"SBIN": bars(_series(SESSIONS, factor=0.9, seed=2.3))}
        indices = synthetic_corpus()[1]
        with tempfile.TemporaryDirectory() as tmp:
            history = write_corpus(Path(tmp), stocks, indices)
            series = bars([(s["date"], s["close"] * 2.0) for s in stocks["SBIN"]])
            self._vintage(history, history / "SBIN_202301.json", "SBIN", series)
            merged, meta = self.trainer.load_stock_corpus(history)
        self.assertEqual(len(meta["clashes"]), SESSIONS)
        self.assertEqual([s["close"] for s in merged["SBIN"]],
                         [s["close"] for s in stocks["SBIN"]],
                         "the first vintage in sorted filename order wins")

    def test_a_close_only_vintage_is_disclosed_not_trained_on(self):
        """The excursions need bars: a close-only row is dropped, never coerced.

        This is the regression the realization correction exists to prevent — a
        loader that keeps only closes builds the envelope from a quantity inside
        the one the coverage gate grades, and nothing else would notice.
        """
        stocks = {"SBIN": bars(_series(SESSIONS, factor=0.9, seed=2.3))}
        indices = synthetic_corpus()[1]
        with tempfile.TemporaryDirectory() as tmp:
            history = write_corpus(Path(tmp), stocks, indices)
            closes_only = [{"date": s["date"], "close": s["close"]}
                           for s in stocks["SBIN"]]
            self._vintage(history, history / "SBIN_202301.json", "SBIN",
                          closes_only)
            merged, meta = self.trainer.load_stock_corpus(history)
        self.assertEqual(meta["incomplete_sessions"], SESSIONS,
                         "a session without high/low is counted, not used")
        self.assertEqual(len(merged["SBIN"]), SESSIONS,
                         "the complete vintage still supplies every session")
        self.assertTrue(all("high" in s and "low" in s for s in merged["SBIN"]))


class TestLabelWindowsStayInsideAVintage(unittest.TestCase):
    """No training row's 22-session label may cross a gap between vintages.

    The corpus is per-symbol-month *vintages* (one fetch each), and they are not
    consecutive: SBIN's vintages run `202003` then `202204`, so the merged series
    jumps from 2020-03-31 to 2021-05-03. A row on the earlier side of that jump
    used to take its 22 forward sessions from the far side — a label window
    covering fourteen months of real market time, whose excursions then entered
    the cone's pools as if they were a one-month move. The rule pinned here is
    per row and per vintage: **one committed vintage must contain the row's own
    session and all `HORIZON` sessions after it**. A window the vintages cannot
    vouch for is dropped and counted, never labeled across the gap.
    """

    # The second vintage starts this many calendar days after the first: a year
    # of missing sessions, the shape the committed SBIN/RELIANCE vintages have.
    GAP_DAYS = 400

    @classmethod
    def setUpClass(cls):
        cls.trainer = load_trainer()
        cls.stage4 = load_stage4()
        cls.sector_map = cls.trainer.load_sector_map()
        cls.horizon = cls.stage4.HORIZON

    def two_vintage_corpus(self):
        """`(stocks, indices, vintages)` — one symbol, two vintages, one gap."""
        first = bars(_series(SESSIONS, factor=0.9, seed=2.3))
        second = bars([((START + timedelta(days=self.GAP_DAYS + i)).isoformat(),
                        value)
                       for i, (_day, value) in enumerate(
                           _series(SESSIONS, factor=1.2, seed=4.4))])
        stocks = {"SBIN": first + second}
        market = _series(self.GAP_DAYS + SESSIONS, factor=1.0, seed=0.0)
        indices = {
            "^NSEI": market,
            "^INDIAVIX": [(d, 14.0 + i % 5) for i, (d, _) in enumerate(market)],
            "^NSEBANK": _series(self.GAP_DAYS + SESSIONS, factor=1.0, seed=1.7),
        }
        vintages = {"SBIN": [[s["date"] for s in first],
                              [s["date"] for s in second]]}
        return stocks, indices, vintages

    def _rows(self, *, with_vintages: bool):
        stocks, indices, split = self.two_vintage_corpus()
        rows, disclosure = self.trainer.build_rows(
            self.stage4, self.sector_map, stocks, indices,
            vintages=split if with_vintages else None)
        return stocks, rows, disclosure

    def test_a_label_window_spanning_the_gap_is_dropped(self):
        stocks, rows, disclosure = self._rows(with_vintages=True)
        dates = [s["date"] for s in stocks["SBIN"]]
        first_vintage = set(dates[:SESSIONS])
        for row in rows:
            window = dates[dates.index(row["date"]):
                           dates.index(row["date"]) + 1 + self.horizon]
            with self.subTest(row=row["date"]):
                self.assertEqual(
                    len(window), 1 + self.horizon,
                    "gating rows exist: a full label window was built")
                self.assertTrue(
                    all(day in first_vintage for day in window)
                    or not (set(window) & first_vintage),
                    f"{row['date']}: the label window straddles the vintage gap")
        dropped = disclosure["label_windows_dropped"]
        self.assertEqual(dropped["SBIN"], self.horizon,
                         "exactly the last HORIZON sessions of the first "
                         "vintage lost their label window to the gap")

    def test_a_window_the_gap_cannot_reach_is_kept(self):
        """The rule drops the straddling tail, not the corpus (vacuity guard)."""
        stocks, rows, _ = self._rows(with_vintages=True)
        dates = [s["date"] for s in stocks["SBIN"]]
        built = {row["date"] for row in rows}
        self.assertTrue(built, "the gapped corpus built no rows at all")
        self.assertIn(dates[-self.horizon - 1], built,
                      "a window with HORIZON sessions left inside its own "
                      "vintage must survive")
        self.assertNotIn(dates[SESSIONS - 1], built,
                         "the last session of the first vintage has no window "
                         "its own vintage can cover")
        late = [row for row in rows if row["date"] > dates[SESSIONS - 1]]
        early = [row for row in rows if row["date"] < dates[SESSIONS - 1]]
        self.assertTrue(late and early,
                        "rows must survive on both sides of the gap")

    def test_the_rule_is_not_vacuous_on_this_corpus(self):
        """Without vintage provenance the invalid windows *are* built."""
        stocks, gated, _ = self._rows(with_vintages=True)
        _stocks, ungated, _ = self._rows(with_vintages=False)
        dates = [s["date"] for s in stocks["SBIN"]]
        lookback = self.stage4.MIN_SESSIONS - 1
        self.assertGreaterEqual(len(ungated), len(gated))
        self.assertEqual(len(ungated) - len(gated), self.horizon + lookback,
                         "the hole must cost exactly the windows the label rule "
                         f"invalidates ({self.horizon}) and the histories the "
                         f"feature rule does ({lookback}) — a rule that drops "
                         "nothing proves nothing, and one that drops more than "
                         "the hole is over-reach")
        self.assertTrue({row["date"] for row in ungated}
                        - {row["date"] for row in gated}
                        == set(dates[SESSIONS - self.horizon:SESSIONS])
                        | set(dates[SESSIONS:SESSIONS + lookback]),
                        "the ruled-out rows are the first vintage's last "
                        "HORIZON sessions (label) and the second run's first "
                        "LOOKBACK sessions (feature history) and nothing else")

    def test_every_committed_row_labels_inside_one_vintage(self):
        """The real corpus, read the way the trainer reads it."""
        stocks, meta = self.trainer.load_stock_corpus(CORPUS_DIR)
        indices = self.trainer.load_index_corpus(CORPUS_DIR)
        rows, disclosure = self.trainer.build_rows(
            self.stage4, self.sector_map, stocks, indices,
            vintages=meta["vintages"])
        self.assertTrue(rows, f"the committed corpus builds no rows: {disclosure}")
        gapped = 0
        for symbol, series in (meta["vintages"] or {}).items():
            for left, right in zip(series, series[1:]):
                if date.fromisoformat(right[0]) - date.fromisoformat(left[-1]) \
                        > timedelta(days=30):
                    gapped += 1
        self.assertGreater(gapped, 0,
                           "the committed corpus has a real vintage gap (SBIN "
                           "202003 -> 202204); a corpus without one would make "
                           "this seam vacuous")
        by_symbol = {symbol: series for symbol, series in meta["vintages"].items()}
        for row in rows:
            series = stocks[row["symbol"]]
            dates = [s["date"] for s in series]
            start = dates.index(row["date"])
            window = dates[start:start + 1 + self.horizon]
            with self.subTest(row=(row["symbol"], row["date"])):
                self.assertEqual(len(window), 1 + self.horizon)
                self.assertTrue(
                    any(set(window) <= set(vintage)
                        for vintage in by_symbol.get(row["symbol"], [])),
                    f"{row['symbol']} {row['date']}: no single committed vintage "
                    "contains this row's label window")
                span = (date.fromisoformat(window[-1])
                        - date.fromisoformat(window[0])).days
                self.assertLessEqual(
                    span, 45,
                    f"{row['symbol']} {row['date']}: the label window spans "
                    f"{span} calendar days — a gap is inside it")
        dropped = disclosure.get("label_windows_dropped") or {}
        self.assertTrue(dropped,
                        "the committed corpus has gaps, so the builder must "
                        "disclose the windows it dropped for them")


class TestFeatureHistoryNeverCrossesARun(unittest.TestCase):
    """No training row's feature history may cross a hole between vintages.

    The label rule guards the *forward* window; the features read the other
    way. `feature_vector` takes the closes before the row — the 200-session
    SMA, the 61-session volatility window, and `ema20_slope_5`, which recurses
    over the whole prefix — and `common_return_windows` takes the last 21
    common cross-asset sessions off the same prefix. A row just after a fetch
    hole (SBIN's and RELIANCE's vintages jump 385 days) therefore fed a
    14-month price jump to its volatility and beta as if it were one session.

    The rule pinned here is **gapless runs**: consecutive vintages that
    overlap chain into one run — their union is continuous market time, and a
    history may read across a fetch boundary inside a run — but a hole no
    fetch covers ends a run, and a row whose `MIN_SESSIONS - 1`-session
    history reaches across it is dropped and counted
    (`feature_windows_dropped`). The row's prefix is cut at its run's first
    session, so no feature — windowed or recursive — can see the far side of
    a hole.
    """

    # The second vintage starts this many calendar days after the first: the
    # shape of SBIN's and RELIANCE's real 202003 -> 202204 hole.
    GAP_DAYS = 400

    @classmethod
    def setUpClass(cls):
        cls.trainer = load_trainer()
        cls.stage4 = load_stage4()
        cls.sector_map = cls.trainer.load_sector_map()
        cls.lookback = cls.stage4.MIN_SESSIONS - 1

    def two_vintage_gap_corpus(self):
        """`(stocks, indices, vintages)` — one symbol, two vintages, one hole."""
        first = bars(_series(SESSIONS, factor=0.9, seed=2.3))
        second = bars([((START + timedelta(days=self.GAP_DAYS + i)).isoformat(),
                        value)
                       for i, (_day, value) in enumerate(
                           _series(SESSIONS, factor=1.2, seed=4.4))])
        stocks = {"SBIN": first + second}
        market = _series(self.GAP_DAYS + SESSIONS, factor=1.0, seed=0.0)
        indices = {
            "^NSEI": market,
            "^INDIAVIX": [(d, 14.0 + i % 5) for i, (d, _) in enumerate(market)],
            "^NSEBANK": _series(self.GAP_DAYS + SESSIONS, factor=1.0, seed=1.7),
        }
        vintages = {"SBIN": [[s["date"] for s in first],
                              [s["date"] for s in second]]}
        return stocks, indices, vintages

    def _rows(self, stocks, indices, vintages):
        return self.trainer.build_rows(self.stage4, self.sector_map, stocks,
                                       indices, vintages=vintages)

    def test_a_history_across_a_hole_is_dropped_and_counted(self):
        stocks, indices, vintages = self.two_vintage_gap_corpus()
        rows, disclosure = self._rows(stocks, indices, vintages)
        dates = [s["date"] for s in stocks["SBIN"]]
        hole = dates[SESSIONS - 1]  # the last session before the 400-day hole
        for row in rows:
            index = dates.index(row["date"])
            history = dates[index - self.lookback:index + 1]
            with self.subTest(row=row["date"]):
                self.assertEqual(len(history), self.lookback + 1,
                                 "the row was built, so it had a full history")
                self.assertTrue(
                    all(day <= hole for day in history)
                    or all(day > hole for day in history),
                    f"{row['date']}: the feature history spans the vintage hole")
        self.assertEqual(disclosure["feature_windows_dropped"],
                         {"SBIN": self.lookback},
                         "the second run's first LOOKBACK sessions are exactly "
                         "the rows whose history the hole invalidates")
        early = [row for row in rows if row["date"] <= hole]
        late = [row for row in rows if row["date"] > hole]
        self.assertTrue(early and late,
                        "rows must survive on both sides of the hole")

    def test_overlapping_fetches_stitch_inside_a_run(self):
        """A history straddling a fetch *boundary* but no hole is kept.

        The rule is gapless runs, not fetches: two overlapping vintages share
        sessions, so their union is continuous market time. Dropping the rows
        in the overlap would cost ~70% of the committed corpus to fix nothing
        — the rule only exists to keep holes out of the features.
        """
        series = bars(_series(SESSIONS + 20, factor=1.1, seed=7.7))
        stocks = {"SBIN": series}
        market = _series(SESSIONS + 20, factor=1.0, seed=0.0)
        indices = {
            "^NSEI": market,
            "^INDIAVIX": [(d, 14.0 + i % 5) for i, (d, _) in enumerate(market)],
            "^NSEBANK": _series(SESSIONS + 20, factor=1.0, seed=1.7),
        }
        # Two fetches of one continuous series: the second starts 68 sessions
        # in, the first ends at session 251 — they share calendar ground, so
        # the merged series has no hole and chains into a single run.
        vintages = {"SBIN": [[s["date"] for s in series[:252]],
                              [s["date"] for s in series[68:]]]}
        rows, disclosure = self._rows(stocks, indices, vintages)
        dates = [s["date"] for s in series]
        built = {row["date"] for row in rows}
        # Position 250's history starts at 51 — sessions only the first fetch
        # has — and its label ends at 272 — sessions only the second has: no
        # single vintage can vouch for it, but the run can.
        self.assertIn(dates[250], built,
                      "a history straddling an overlapping fetch boundary is "
                      "continuous market time — dropping it would be over-reach")
        self.assertEqual(disclosure["feature_windows_dropped"], {},
                         "a gapless corpus drops no history")
        self.assertEqual(len(rows), len(series) - self.stage4.HORIZON
                         - self.lookback,
                         "every candidate session inside the run is built")

    def test_the_prefix_is_cut_at_the_run(self):
        """`feature_window_dates` maps each allowed row to its run's first index."""
        stocks, indices, vintages = self.two_vintage_gap_corpus()
        dates = [s["date"] for s in stocks["SBIN"]]
        allowed = self.trainer.feature_window_dates(
            vintages["SBIN"], dates, self.lookback)
        self.assertNotIn(dates[SESSIONS], allowed,
                         "the hole's far side has no LOOKBACK sessions yet")
        self.assertEqual(allowed[dates[SESSIONS + self.lookback]], SESSIONS,
                         "a second-run row's prefix starts at its run's first "
                         "session, not at the merged series' start")
        self.assertEqual(allowed[dates[self.lookback]], 0,
                         "a first-run row's prefix starts at the corpus's first "
                         "session — the hole cannot reach it")

    def test_the_cross_asset_window_never_reaches_across_the_hole(self):
        """The beta/correlation sessions are run-bound too.

        `common_return_windows` intersects the three series, and the stock
        half of that intersection is the run-restricted prefix — so a row
        on the far side of the hole can never pick up its sector's or the
        market's pre-hole sessions into its 21 cross-asset returns. The
        intersection recomputed here is exactly the one the stage performs.
        """
        stocks, indices, vintages = self.two_vintage_gap_corpus()
        rows, _ = self._rows(stocks, indices, vintages)
        dates = [s["date"] for s in stocks["SBIN"]]
        hole = dates[SESSIONS - 1]  # the last session before the 400-day hole
        market = dict(indices["^NSEI"])
        sector = dict(indices["^NSEBANK"])
        self.assertTrue(any(row["date"] <= hole for row in rows) and
                        any(row["date"] > hole for row in rows),
                        "rows must survive on both sides of the hole")
        width = self.stage4.CROSS_ASSET_WINDOW + 1
        for row in rows:
            day = row["date"]
            index = dates.index(day)
            run = dates[index - self.lookback: index + 1]
            window = sorted(set(run) & set(market) & set(sector))[-width:]
            with self.subTest(row=day):
                self.assertEqual(len(window), width,
                                 "the row was built, so its window is full — "
                                 "a short one would make this vacuous")
                self.assertEqual(len({day_ <= hole for day_ in window}), 1,
                                 "the cross-asset window spans the vintage hole")

    def test_the_committed_corpus_never_reads_history_across_a_hole(self):
        """The real corpus, run by run."""
        stocks, meta = self.trainer.load_stock_corpus(CORPUS_DIR)
        indices = self.trainer.load_index_corpus(CORPUS_DIR)
        rows, disclosure = self.trainer.build_rows(
            self.stage4, self.sector_map, stocks, indices,
            vintages=meta["vintages"])
        self.assertTrue(rows, f"the committed corpus builds no rows: {disclosure}")
        for row in rows:
            series = stocks[row["symbol"]]
            dates = [s["date"] for s in series]
            index = dates.index(row["date"])
            runs = self.trainer.vintage_runs(meta["vintages"][row["symbol"]],
                                             dates)
            first = next(first for first, last in runs
                         if first <= row["date"] <= last)
            with self.subTest(row=(row["symbol"], row["date"])):
                self.assertGreaterEqual(
                    index - dates.index(first), self.lookback,
                    "the run cannot vouch for this row's full feature history")
        # SBIN's and RELIANCE's vintages jump 385 days; every mapped symbol's
        # other fetches overlap, so those two holes are the only drops.
        self.assertEqual(disclosure["feature_windows_dropped"],
                         {"RELIANCE": self.lookback, "SBIN": self.lookback},
                         "the committed corpus's real holes are exactly the "
                         "histories it may not build")




class TestSectorConditionedPools(unittest.TestCase):
    """The calibration carries each sector's own excursions beside the pooled ones.

    The cone's levels are quantiles of a mixture of *observed* excursions, so the
    block they come from decides the envelope. One pooled block mixes a bank's
    22-session dispersion with an IT stock's and an energy major's; the gate then
    grades that average against one specific trade. The trainer writes the
    per-sector blocks (and keeps the pooled block as the fallback), and the CV
    measures the shipped *selection* — stage 4's own `samples_for` — so the
    number in `provenance.cv` is the cone the stage actually ships.
    """

    @classmethod
    def setUpClass(cls):
        cls.trainer = load_trainer()
        cls.stage4 = load_stage4()
        cls.sector_map = cls.trainer.load_sector_map()

    def two_sector_corpus(self):
        stocks, indices = synthetic_corpus()
        energy = _series(SESSIONS, factor=1.6, seed=5.5)
        stocks["RELIANCE"] = bars(_series(SESSIONS, factor=1.1, seed=3.1))
        # A second bank-sector name. Race labels split every block by class, so
        # the bank block needs enough rows for *every* class to clear
        # MIN_SECTOR_ATOMS_PER_CLASS — the deep half of the contrast below —
        # while the energy block stays deliberately thin.
        stocks["ICICIBANK"] = bars(_series(SESSIONS + 40, factor=1.25, seed=8.8))
        indices["^CNXENERGY"] = energy
        return stocks, indices

    def test_each_block_holds_only_its_own_sectors_rows(self):
        stocks, indices = self.two_sector_corpus()
        rows, _ = self.trainer.build_rows(self.stage4, self.sector_map,
                                          stocks, indices)
        self.assertTrue(rows)
        blocks = self.trainer.calibration_samples_by_sector(rows)
        self.assertEqual(sorted(blocks), ["^CNXENERGY", "^NSEBANK"])
        for ticker, block in blocks.items():
            expected = sum(1 for row in rows if row["sector_index"] == ticker)
            with self.subTest(sector=ticker):
                for measure in ("forward_return_pct", "mae_pct", "mfe_pct"):
                    self.assertEqual(
                        sum(len(values) for values in block[measure].values()),
                        expected,
                        "a sector block may hold its own sector's excursions "
                        "and nothing else")
        self.assertNotIn("RELIANCE", {row["symbol"] for row in rows
                                      if row["sector_index"] == "^NSEBANK"})

    def test_the_pooled_block_is_unchanged_and_covers_every_row(self):
        stocks, indices = self.two_sector_corpus()
        rows, _ = self.trainer.build_rows(self.stage4, self.sector_map,
                                          stocks, indices)
        pooled = self.trainer.calibration_samples(rows)
        blocks = self.trainer.calibration_samples_by_sector(rows)
        for measure in ("forward_return_pct", "mae_pct", "mfe_pct"):
            with self.subTest(measure=measure):
                self.assertEqual(
                    sum(len(values) for values in pooled[measure].values()),
                    len(rows))
                for cls in self.stage4.CLASSES:
                    by_sector = sorted(value for block in blocks.values()
                                       for value in block[measure][cls])
                    self.assertEqual(by_sector,
                                     sorted(pooled[measure][cls]),
                                     "the pooled block must be exactly the "
                                     "union of the sector blocks")

    def test_a_deep_block_is_selected_and_a_thin_one_falls_back(self):
        """The trainer's blocks are shaped for stage 4's own selector and floor.

        On this tiny two-sector corpus the bank block is deep enough to answer
        and the energy block is not, so both halves of `samples_for` are visible
        without a synthetic floor: the block that can carry a quantile is read,
        the one that cannot defers to the pooled pools.
        """
        stocks, indices = self.two_sector_corpus()
        rows, _ = self.trainer.build_rows(self.stage4, self.sector_map,
                                          stocks, indices)
        calibration = {"samples": self.trainer.calibration_samples(rows),
                       "samples_by_sector":
                           self.trainer.calibration_samples_by_sector(rows)}
        self.assertIs(self.stage4.samples_for(calibration, "^NSEBANK"),
                      calibration["samples_by_sector"]["^NSEBANK"])
        deep = calibration["samples_by_sector"]["^NSEBANK"]
        self.assertGreaterEqual(
            min(len(values) for block in deep.values()
                for values in block.values()),
            self.stage4.MIN_SECTOR_ATOMS_PER_CLASS,
            "this corpus's bank block is meant to be deep enough in every "
            "race class — the contrast needs both halves to be real")
        thin = calibration["samples_by_sector"]["^CNXENERGY"]
        self.assertLess(
            min(len(values) for block in thin.values()
                for values in block.values()),
            self.stage4.MIN_SECTOR_ATOMS_PER_CLASS,
            "this corpus's energy block is meant to be below the floor")
        self.assertIs(self.stage4.samples_for(calibration, "^CNXENERGY"),
                      calibration["samples"],
                      "a block the stage cannot stand a quantile on defers to "
                      "the pooled pools")


class TestPurgedGroupTimeSeriesSplit(unittest.TestCase):
    """The fold rule, exactly — and as a property over real rows."""

    @classmethod
    def setUpClass(cls):
        cls.trainer = load_trainer()
        stocks, indices = synthetic_corpus()
        cls.rows, _ = cls.trainer.build_rows(
            load_stage4(), cls.trainer.load_sector_map(), stocks, indices)

    def _calendar_rows(self, count: int, symbol: str = "SBIN") -> list[dict]:
        return [{"symbol": symbol, "date": d, "features": [0.0], "class": "NEITHER",
                 "forward_return_pct": 0.0, "mae_pct": 0.0, "mfe_pct": 0.0}
                for d in _dates(count)]

    def test_a_label_window_reaching_the_fold_is_purged(self):
        rows = self._calendar_rows(12)
        ranks = self.trainer.symbol_ranks(rows)
        split = self.trainer.split_fold(rows, ranks, (rows[6]["date"], rows[7]["date"]),
                                        horizon=2, embargo=1)
        self.assertEqual([r["date"] for r in split["test"]],
                         [rows[6]["date"], rows[7]["date"]])
        kept = {r["date"] for r in split["train"]}
        purged = {r["date"] for r in split["purged"]}
        # A row whose label window (rank+1 .. rank+horizon] reaches rank 6 (the
        # first session of the span) is out: ranks 5 and 4 both do.
        for rank in (5, 4):
            self.assertIn(rows[rank]["date"], purged, f"rank {rank} must be purged")
        self.assertIn(rows[3]["date"], kept, "rank 3's window ends at rank 5")
        self.assertIn(rows[8]["date"], purged, "rank 8 is inside the embargo tail")
        self.assertIn(rows[9]["date"], kept)
        self.assertTrue(purged.isdisjoint(kept))

    def test_the_embargo_tail_is_purged(self):
        rows = self._calendar_rows(12)
        ranks = self.trainer.symbol_ranks(rows)
        split = self.trainer.split_fold(rows, ranks, (rows[6]["date"], rows[7]["date"]),
                                        horizon=1, embargo=2)
        purged = {r["date"] for r in split["purged"]}
        kept = {r["date"] for r in split["train"]}
        self.assertIn(rows[8]["date"], purged, "inside the embargo tail")
        self.assertIn(rows[9]["date"], purged, "inside the embargo tail")
        self.assertIn(rows[10]["date"], kept, "clear of the embargo tail")

    def test_a_symbol_that_did_not_trade_in_the_fold_is_still_purged(self):
        """The group axis: no session inside the span is not a licence to leak.

        A stock halted (or a corpus missing) the fold's two dates has no session
        *inside* the span, so a rule keyed on its in-span sessions would keep the
        row directly before the span — whose two-session label window covers the
        whole fold. Purged against the span on its own calendar instead.
        """
        rows = self._calendar_rows(12, "SBIN")
        other = self._calendar_rows(12, "TCS")
        halted = [r for r in other
                  if r["date"] not in {d for d in _dates(12)[6:8]}]
        ranks = self.trainer.symbol_ranks(rows + halted)
        split = self.trainer.split_fold(rows + halted, ranks,
                                        (rows[6]["date"], rows[7]["date"]),
                                        horizon=2, embargo=1)
        purged_tcs = {r["date"] for r in split["purged"] if r["symbol"] == "TCS"}
        kept_tcs = {r["date"] for r in split["train"] if r["symbol"] == "TCS"}
        # TCS's sessions: ranks 0..5 before the span, 6..9 after it.
        for rank in (4, 5):
            self.assertIn(_dates(12)[rank], purged_tcs,
                          f"TCS rank {rank} reaches the span after a halt")
        self.assertIn(_dates(12)[3], kept_tcs, "two sessions clear of the span")
        self.assertIn(_dates(12)[8], purged_tcs, "the first session after the span")
        self.assertIn(_dates(12)[9], kept_tcs, "clear of the embargo tail")

    def test_the_property_holds_over_real_rows(self):
        spans = self.trainer.fold_spans(self.rows, 5)
        self.assertEqual(len(spans), 5)
        ranks = self.trainer.symbol_ranks(self.rows)
        tested: set[tuple[str, str]] = set()
        for span in spans:
            low, high = span
            split = self.trainer.split_fold(self.rows, ranks, span, horizon=22,
                                            embargo=5)
            self.assertTrue(split["test"])
            test_keys = {(r["symbol"], r["date"]) for r in split["test"]}
            self.assertFalse(test_keys & {(r["symbol"], r["date"])
                                          for r in split["train"]})
            for row in split["train"]:
                calendar = ranks[row["symbol"]]
                rank = calendar[row["date"]]
                before = sum(1 for day in calendar if day < low)
                through = sum(1 for day in calendar if day <= high)
                expected = (rank + 22 < before) if rank < before \
                    else (rank >= through + 5)
                self.assertTrue(
                    expected,
                    f"{row['symbol']} {row['date']} (rank {rank}) is inside its "
                    f"fold's purge or embargo span ({before=}, {through=})")
            tested |= test_keys
        self.assertEqual(tested, {(r["symbol"], r["date"]) for r in self.rows},
                         "every row must be tested exactly once across the folds")

    def test_the_folds_are_contiguous_and_ordered(self):
        spans = self.trainer.fold_spans(self.rows, 5)
        for (low, high), (next_low, _) in zip(spans, spans[1:]):
            self.assertLess(low, high)
            self.assertLess(high, next_low, "fold spans must not overlap")
        dates = sorted({r["date"] for r in self.rows})
        self.assertEqual(spans[0][0], dates[0])
        self.assertEqual(spans[-1][1], dates[-1])


class TestTrainedArtifacts(unittest.TestCase):
    """One real end-to-end run on a tiny corpus: provenance, determinism."""

    @classmethod
    def setUpClass(cls):
        cls.trainer = load_trainer()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        stocks, indices = synthetic_corpus()
        cls.corpus = write_corpus(cls.root, stocks, indices)
        cls.models = cls.root / "models_a"
        cls.models_b = cls.root / "models_b"
        code = cls.trainer.main(["--corpus-dir", str(cls.corpus),
                                 "--model-dir", str(cls.models)])
        if code != 0:
            raise AssertionError(f"the trainer exited {code} on a tiny corpus")
        cls.calibration = json.loads(
            (cls.models / "lgbm_direction_v1.calibration.json").read_text(
                encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_calibration_carries_the_frozen_inference_contract(self):
        stage4 = load_stage4()
        self.assertEqual(self.calibration["schema"], stage4.CALIBRATION_SCHEMA)
        self.assertEqual(self.calibration["features"], list(stage4.FEATURES))
        self.assertEqual(self.calibration["classes"], list(stage4.CLASSES))
        self.assertEqual(self.calibration["horizon"], stage4.HORIZON)
        self.assertEqual(set(self.calibration["samples"]),
                         {"forward_return_pct", "mae_pct", "mfe_pct"})
        self.assertEqual(self.calibration["race_band_pct"], stage4.RACE_BAND_PCT)
        self.assertNotIn("neutral_up_share", self.calibration,
                         "the retired direction bridge must not survive")
        self.assertIn("stage4.race_probability", self.calibration["race_estimator"],
                      "the graded estimator must be disclosed in the artifact")
        # The trade's own sector's excursions ship beside the pooled block, in
        # the shape the stage's selector reads.
        by_sector = self.calibration["samples_by_sector"]
        self.assertEqual(sorted(by_sector),
                         self.calibration["provenance"]["corpus"]["sector_indices"])
        for ticker, block in by_sector.items():
            with self.subTest(sector=ticker):
                self.assertEqual(set(block),
                                 {"forward_return_pct", "mae_pct", "mfe_pct"})
                for measure, classes in block.items():
                    self.assertEqual(sorted(classes), sorted(stage4.CLASSES))
                    for cls, values in classes.items():
                        self.assertTrue(values, measure)

    def test_the_cv_discloses_which_pools_the_cone_was_measured_on(self):
        cv = self.calibration["provenance"]["cv"]
        self.assertIn("sector", cv["pool_conditioning"])
        self.assertIn("MIN_SECTOR_ATOMS_PER_CLASS", cv["pool_conditioning"])
        self.assertIn("oof_sector_pool_share_pct", cv)
        self.assertTrue(0.0 <= cv["oof_sector_pool_share_pct"] <= 100.0)

    def test_provenance_is_sourced_and_names_the_corpus(self):
        provenance = self.calibration["provenance"]
        self.assertEqual(provenance["origin"], "sourced")
        self.assertEqual(provenance["builder"],
                         "tests/evals/train_direction_model.py")
        corpus = provenance["corpus"]
        self.assertEqual(sorted(corpus["symbols"]), ["HDFCBANK", "SBIN"])
        self.assertEqual(corpus["sector_indices"], ["^NSEBANK"])
        self.assertGreater(corpus["training_rows"], 0)
        self.assertEqual(corpus["duplicate_date_clashes"], [])
        self.assertIn("indices_daily.json", corpus["index"])

    def test_the_cv_block_names_the_cone_definition_it_measured(self):
        """The metric and the shipped envelope must be the same quantity.

        The cone was re-frozen from the terminal interval to the path envelope
        (BRD §5.2); this block is where a stale "coverage" number could sit and
        look like evidence for a cone the stage no longer ships.
        """
        cv = self.calibration["provenance"]["cv"]
        self.assertIn("path envelope", cv["cone_definition"])
        self.assertIn("session extremes", cv["cone_definition"])
        self.assertIn("stage4.CONE_SPEC", cv["cone_definition"])
        # Every observed row has mae ≤ terminal ≤ mfe with one weight vector, so
        # an envelope that contains the path necessarily contains the close.
        self.assertGreaterEqual(cv["oof_cone_terminal_coverage_pct"],
                                cv["oof_cone_coverage_pct"])

    def test_the_cv_block_reports_every_realization_of_one_envelope(self):
        """Each coverage figure says which object it covered.

        One envelope scores differently against the bars the gate reads, the
        closes of the same windows, and the closing session alone — so all three
        readings live in the artifact and the note names them. The pooled
        block's envelope over the same rows is the fourth reading: the cost of
        the sector conditioning (decision 13), published as a number instead of
        asserted as a virtue.
        """
        cv = self.calibration["provenance"]["cv"]
        for key in ("oof_cone_coverage_pct", "oof_cone_close_path_coverage_pct",
                    "oof_cone_terminal_coverage_pct",
                    "oof_cone_pooled_coverage_pct"):
            self.assertIn(key, cv)
            self.assertTrue(0.0 <= cv[key] <= 100.0)
        for fold in cv["per_fold"]:
            self.assertIn("oof_cone_pooled_coverage_pct", fold)
            self.assertTrue(0.0 <= fold["oof_cone_pooled_coverage_pct"] <= 100.0)
        self.assertIn("bar path", cv["coverage_note"])
        self.assertIn("close path", cv["coverage_note"])
        self.assertIn("pooled", cv["coverage_note"])
        # A close path sits inside the bar path containing it, so the close
        # reading can never be the stricter of the two.
        self.assertGreaterEqual(cv["oof_cone_close_path_coverage_pct"],
                                cv["oof_cone_coverage_pct"])
        self.assertGreaterEqual(cv["oof_cone_terminal_coverage_pct"],
                                cv["oof_cone_coverage_pct"])

    def test_the_rows_carry_both_realizations_of_their_window(self):
        """The pools read the bars; the close reading rides along, labelled."""
        rows, _ = self.trainer.build_rows(
            load_stage4(), self.trainer.load_sector_map(),
            *synthetic_corpus())
        self.assertTrue(rows)
        for row in rows[:20]:
            self.assertIn("close_mae_pct", row)
            self.assertIn("close_mfe_pct", row)
            self.assertLessEqual(row["mae_pct"], row["close_mae_pct"],
                                 "the bar low is never milder than the close low")
            self.assertGreaterEqual(row["mfe_pct"], row["close_mfe_pct"])
            self.assertLessEqual(row["close_mae_pct"], row["forward_return_pct"])
            self.assertGreaterEqual(row["close_mfe_pct"], row["forward_return_pct"])

    def test_provenance_reports_the_out_of_fold_metrics_and_the_scheme(self):
        cv = self.calibration["provenance"]["cv"]
        self.assertEqual(cv["scheme"], "purged group time-series")
        self.assertEqual(cv["embargo_sessions"], 5)
        self.assertEqual(cv["horizon_sessions"], 22)
        self.assertGreater(cv["oof_rows"], 0)
        self.assertGreater(cv["total_purged_rows"], 0)
        self.assertEqual(len(cv["per_fold"]), 5)
        for fold in cv["per_fold"]:
            self.assertGreater(fold["train_rows"], 0)
            self.assertGreater(fold["purged_rows"], 0)
            self.assertGreater(fold["oof_brier_race"], 0.0)
            self.assertTrue(0.0 <= fold["oof_cone_coverage_pct"] <= 100.0)

    def test_training_twice_produces_the_same_bytes(self):
        code = self.trainer.main(["--corpus-dir", str(self.corpus),
                                  "--model-dir", str(self.models_b)])
        self.assertEqual(code, 0)
        first = (self.models / "lgbm_direction_v1.txt").read_bytes()
        second = (self.models_b / "lgbm_direction_v1.txt").read_bytes()
        self.assertEqual(first, second, "the booster is not reproducible")

    def test_the_model_file_is_line_fed(self):
        raw = (self.models / "lgbm_direction_v1.txt").read_bytes()
        self.assertNotIn(b"\r\n", raw)
        self.assertNotIn(b"\r", raw)


class TestShippedArtifact(unittest.TestCase):
    """The committed booster is the sourced one, and it is current."""

    @classmethod
    def setUpClass(cls):
        if not SHIPPED_CALIBRATION.exists():
            raise AssertionError(
                "the shipped stage-4 calibration is missing — rebuild it with "
                "`python tests/evals/train_direction_model.py`")
        cls.calibration = json.loads(SHIPPED_CALIBRATION.read_text(encoding="utf-8"))
        cls.trainer = load_trainer()

    def test_the_shipped_model_is_sourced_not_the_authored_stand_in(self):
        provenance = self.calibration["provenance"]
        self.assertEqual(provenance["origin"], "sourced")
        self.assertEqual(provenance["builder"],
                         "tests/evals/train_direction_model.py")
        self.assertGreater(provenance["corpus"]["training_rows"], 1000,
                           "a stand-in-sized corpus cannot be the shipped model")
        self.assertGreater(provenance["cv"]["oof_rows"], 1000)
        self.assertIn("Purged", provenance["cv"]["scheme"].title())
        self.assertIn("Sourced", provenance["note"])

    def test_the_artifact_still_matches_the_corpus_it_names(self):
        """A corpus that grew without a retrain is caught here, not in a gate."""
        provenance = self.calibration["provenance"]
        served = json.loads(
            (CORPUS_DIR / "indices_daily.json").read_text(encoding="utf-8"))["tickers"]
        sector_map = self.trainer.load_sector_map()
        buildable = sorted(
            symbol for symbol in
            {json.loads(path.read_text(encoding="utf-8")).get("symbol")
             for path in CORPUS_DIR.glob("*.json") if path.name != "indices_daily.json"}
            if symbol and sector_map.get(symbol) in served)
        self.assertEqual(provenance["corpus"]["symbols"], buildable,
                         "the shipped artifact was trained on a different symbol "
                         "set than the committed corpus now supports")

    def test_the_sector_blocks_disclose_their_atoms_and_tails(self):
        """The artifact says how deep each block is — atoms and both tails.

        A `P10`/`P90` envelope can never contain an excursion outside the block
        it was read from, so whether a graded tape is a tail event is a question
        about these atoms; the disclosure is checked against the blocks it names
        (`samples_by_sector`), so it cannot drift from the pools it describes.
        """
        corpus = self.calibration["provenance"]["corpus"]
        blocks = self.calibration["samples_by_sector"]
        self.assertEqual(sorted(corpus["sector_blocks"]), sorted(blocks))
        for ticker, block in blocks.items():
            with self.subTest(sector=ticker):
                disclosure = corpus["sector_blocks"][ticker]
                for measure, classes in block.items():
                    self.assertEqual(
                        disclosure["atoms"][measure],
                        sum(len(values) for values in classes.values()),
                        "the disclosed atom count must be the block's own")
                self.assertLessEqual(disclosure["mae_pct"]["p01"],
                                     disclosure["mae_pct"]["p10"])
                self.assertLessEqual(disclosure["mae_pct"]["p10"], 0.0)
                self.assertGreaterEqual(disclosure["mfe_pct"]["p90"], 0.0)
                self.assertLessEqual(disclosure["mfe_pct"]["p90"],
                                     disclosure["mfe_pct"]["p99"])
        pooled = corpus["pooled_block"]
        self.assertEqual(pooled["atoms"]["mae_pct"],
                         corpus["training_rows"],
                         "the pooled block is every row's excursion")

    def test_the_lf_pin_covers_the_model_file(self):
        raw = SHIPPED_MODEL.read_bytes()
        self.assertNotIn(b"\r\n", raw)
        attributes = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("models/*.txt -text", attributes)


class TestTheCorpusReachesTheGradedTapes(unittest.TestCase):
    """The committed corpus builds rows inside every graded tape (ADR-011 decision 10).

    Gate 5 grades only records whose pinned forward tape lies inside
    `provenance.corpus.row_window`, and that window opens at the first
    *buildable* row: 200 stock sessions plus `CROSS_ASSET_WINDOW + 1` index
    sessions at or before its own date. The Covid scenario vintages reach back
    to 2019-04-30, so before the index panel was re-derived from this corpus
    the market half alone pushed the first row to 2020-04-09 and the two Covid
    tapes were scoped out as permanently ungradable. This seam reads the same
    committed corpus the trainer reads and requires the row span it would
    train on to contain every graded tape — a corpus (or panel) that falls
    behind the benchmark fails here, in the trainer's own vocabulary, instead
    of surfacing later as a smaller denominator inside a gate.
    """

    @classmethod
    def setUpClass(cls):
        cls.trainer = load_trainer()
        cls.stage4 = load_stage4()
        raw = json.loads(GOLDEN_DATASET.read_text(encoding="utf-8"))
        cls.tapes: dict[str, tuple[str, str]] = {}
        for trade in raw["trades"]:
            if trade.get("cone_p10") is None or trade.get("cone_p90") is None:
                continue
            span = dataset_refs.forward_tape_span(trade)
            if span is None:
                raise AssertionError(
                    f"{trade.get('trade_id')}: a cone-labeled record must pin "
                    "the tape gate 5 scopes it by")
            cls.tapes[str(trade["trade_id"])] = span

    def test_every_graded_tape_lies_inside_the_rows_the_corpus_builds(self):
        self.assertTrue(self.tapes, "the benchmark must grade at least one tape")
        stocks, _meta = self.trainer.load_stock_corpus(CORPUS_DIR)
        indices = self.trainer.load_index_corpus(CORPUS_DIR)
        rows, disclosure = self.trainer.build_rows(
            self.stage4, self.trainer.load_sector_map(), stocks, indices)
        self.assertTrue(rows, f"the committed corpus builds no rows: {disclosure}")
        first = min(row["date"] for row in rows)
        last = max(row["date"] for row in rows)
        window = tuple(str(v) for v in json.loads(
            SHIPPED_CALIBRATION.read_text(encoding="utf-8")
        )["provenance"]["corpus"]["row_window"][:2])
        out_of_window: list[str] = []
        for tid, (tape_first, tape_last) in sorted(self.tapes.items()):
            # Gate 5 grades a cone-labeled tape only when the trained window
            # contains it (evals.md §3 #5); a tape outside it is that gate's
            # *named* exclusion, not a tape this seam may demand the corpus
            # reach. The owner signed the two below in on 2026-10-17 — stage 4
            # reads ≤ T_rec, so their evidence is recorded while gate 5 keeps
            # its denominator inside the pools' window.
            if not (window[0] <= tape_first and tape_last <= window[1]):
                out_of_window.append(tid)
                continue
            with self.subTest(tape=tid, span=(tape_first, tape_last)):
                self.assertLessEqual(
                    first, tape_first,
                    "the row window would open after this tape — the corpus "
                    f"never reached this regime (first row {first}, tape "
                    f"starts {tape_first})")
                self.assertGreaterEqual(
                    last, tape_last,
                    "the row window would close before this tape ends "
                    f"(last row {last}, tape ends {tape_last})")
        self.assertEqual(
            out_of_window, ["GB-2025-0031", "GB-2025-0032"],
            "a cone claim whose tape the trained window cannot reach is only "
            "legal as gate 5's named exclusion — say which rows, never grow "
            "the set unexamined")


if __name__ == "__main__":
    unittest.main()
