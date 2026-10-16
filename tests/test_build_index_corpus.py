"""Seam for `tests/evals/build_index_corpus.py` (the sourced index corpus).

The corpus is the market half of the stage-4 training evidence: without it the
20-session beta and sectoral correlation have no sourced series to read, and the
trainer can build no rows. It is *sourced* evidence, so the two things that can
go wrong silently are (a) a series too thin to train on being committed anyway,
and (b) a ticker the source stopped serving disappearing from the record instead
of being disclosed. Both are asserted here, against the committed file the
trainer actually reads.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
EVALS = REPO_ROOT / "tests" / "evals"
CORPUS_FILE = REPO_ROOT / "tests" / "fixtures" / "history" / "indices_daily.json"


def load_builder():
    spec = importlib.util.spec_from_file_location(
        "uvest_build_index_corpus", EVALS / "build_index_corpus.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TestCommittedCorpus(unittest.TestCase):
    """The committed file passes the builder's own contract."""

    @classmethod
    def setUpClass(cls):
        cls.builder = load_builder()
        if not CORPUS_FILE.exists():
            raise AssertionError(
                "the index corpus is missing — rebuild it with "
                "`python tests/evals/build_index_corpus.py --fetch`")
        cls.document = json.loads(CORPUS_FILE.read_text(encoding="utf-8"))

    def test_the_committed_corpus_satisfies_its_contract(self):
        self.assertEqual(self.builder.check(self.document), [])

    def test_the_cli_check_path_agrees(self):
        proc = subprocess.run(
            [sys.executable, str(EVALS / "build_index_corpus.py"), "--check"],
            capture_output=True, check=False, encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("series served", proc.stdout)

    def test_every_served_series_can_carry_a_training_row(self):
        served = self.document["tickers"]
        self.assertTrue(served, "no served series — nothing to train on")
        self.assertIn("^NSEI", served, "NIFTY 50 is the beta feature's market side")
        for ticker, entry in served.items():
            with self.subTest(ticker=ticker):
                dates = [v["date"] for v in entry["values"]]
                self.assertGreaterEqual(len(dates), self.builder.MIN_CORPUS_SESSIONS)
                self.assertEqual(dates, sorted(dates))
                self.assertEqual(len(set(dates)), len(dates),
                                 "duplicate sessions would double-count a row")

    def test_the_panel_is_disclosed_whole(self):
        """Served + absent must equal the shared map's panel — no quiet drops."""
        served = set(self.document["tickers"])
        absent = self.document["absent"]
        self.assertEqual(served | set(absent), set(self.builder.panel()))
        self.assertFalse(served & set(absent))
        for ticker, reason in absent.items():
            with self.subTest(ticker=ticker):
                self.assertTrue(reason, "an absent series must carry its reason")
                self.assertIn("source", reason,
                              "the reason must name what the source did")

    def test_provenance_declares_the_source_not_a_proxy(self):
        provenance = self.document["provenance"]
        self.assertEqual(provenance["origin"], "sourced")
        self.assertEqual(provenance["builder"],
                         "tests/evals/build_index_corpus.py")
        self.assertIn("yfinance", self.document["source"])
        self.assertIn("never synthesized", provenance["note"])


class TestThePanelCoversTheStockCorpus(unittest.TestCase):
    """The market half spans the stock half it trains beside (ADR-011 decision 10).

    A training row needs `CROSS_ASSET_WINDOW + 1` index sessions at or before
    its own date (`4_ml_direction.common_return_windows`), so the panel's
    first session — not the stocks' — decides where
    `provenance.corpus.row_window` opens and therefore which tapes gate 5 can
    grade. A fixed fetch window that fell behind the growing stock corpus is
    exactly how the benchmark's two Covid tapes ended up scoped out as
    permanently ungradable: the vintages for those symbols run from
    2019-04-30, but the panel started 2020-03-09. The fetch count is now
    derived from the committed vintages, and both halves are asserted here
    against the committed files — the arithmetic *and* its outcome.
    """

    @classmethod
    def setUpClass(cls):
        cls.builder = load_builder()
        if not CORPUS_FILE.exists():
            raise AssertionError("the index corpus is missing")
        cls.document = json.loads(CORPUS_FILE.read_text(encoding="utf-8"))

    def stock_span(self) -> tuple[str, str]:
        """`(first, last)` session across every committed per-symbol vintage."""
        first = last = None
        for path in sorted(CORPUS_FILE.parent.glob("*.json")):
            if path.name == CORPUS_FILE.name:
                continue
            doc = json.loads(path.read_text(encoding="utf-8"))
            dates = [row["date"] for row in doc.get("sessions") or []]
            if not dates:
                continue
            first = dates[0] if first is None or dates[0] < first else first
            last = dates[-1] if last is None or dates[-1] > last else last
        self.assertIsNotNone(first, "no committed stock sessions to span")
        return str(first), str(last)

    def test_the_fetch_window_is_derived_from_the_committed_stock_corpus(self):
        """`fetch_sessions` must reach back to the corpus's earliest session.

        The widening here mirrors `fetch_market_context.fetch_yahoo_closes`
        (`start = as_of - (sessions * 1.7 + 30) days`); the committed-file
        test below is the outcome check if that formula ever drifts.
        """
        first, _last = self.stock_span()
        as_of = date.today()
        sessions = self.builder.fetch_sessions(as_of)
        start = (as_of - timedelta(days=int(sessions * 1.7) + 30)).isoformat()
        self.assertLessEqual(
            start, first,
            f"fetch window starts {start}, after the corpus's first session "
            f"{first} — the panel would gate row dates")

    def test_the_committed_panel_spans_the_committed_stock_corpus(self):
        first, last = self.stock_span()
        window = self.document["window"]
        self.assertLessEqual(
            window[0], first,
            f"the committed panel opens {window[0]}, after the stocks it serves "
            f"({first}) — the cross-asset window would hold row dates back and "
            "keep graded tapes outside `row_window` (ADR-011 decision 10)")
        self.assertGreaterEqual(window[1], last,
                                "the panel must also reach the corpus's end")


class TestTheFallbackSourceServesWhatYahooDoesNot(unittest.TestCase):
    """The panel is served by two tier-1 sources, and says which one served each.

    Yahoo's free history covers `^NSEI`, `^INDIAVIX`, `^NSEBANK`, `^CNXIT` and
    `^CNXPHARMA` back through the corpus, but returns a *single session* for
    `^CNXAUTO`, `^CNXFMCG`, `^CNXMETAL`, `^CNXINFRA`, `^CNXENERGY` and
    `^CNXBANK` (and no such symbol at all for the last). Those are the sectors
    the benchmark's own graded tapes trade in — RELIANCE is `^CNXENERGY`, LT and
    ADANIPORTS `^CNXINFRA` — so a panel that stops at what Yahoo serves keeps
    those symbols out of the trained universe entirely. The fallback is NSE's
    own daily index snapshot archive (`ind_close_all_DDMMYYYY.csv`), which is the
    same free-first tier the pipeline already prefers, and every series records
    which of the two sources served it.
    """

    @classmethod
    def setUpClass(cls):
        cls.builder = load_builder()

    def test_the_official_name_map_covers_the_whole_panel(self):
        panel = set(self.builder.panel())
        names = self.builder.NSE_ARCHIVE_NAMES
        self.assertEqual(panel - set(names), set(),
                         "a panel ticker with no official index name can never "
                         "be served by the archive fallback")
        for ticker, name in names.items():
            with self.subTest(ticker=ticker):
                self.assertTrue(name.strip())

    def test_the_psu_bank_slot_names_the_official_index(self):
        """`^CNXBANK` is the map's PSU-bank slot; the official name is NSE's."""
        self.assertEqual(self.builder.NSE_ARCHIVE_NAMES["^CNXBANK"],
                         "Nifty PSU Bank")
        self.assertEqual(self.builder.NSE_ARCHIVE_NAMES["^CNXENERGY"],
                         "Nifty Energy")
        self.assertEqual(self.builder.NSE_ARCHIVE_NAMES["^CNXINFRA"],
                         "Nifty Infrastructure")

    @staticmethod
    def _pairs(base: float, count: int) -> list[tuple[str, float]]:
        return [((date(2024, 1, 1) + timedelta(days=i)).isoformat(), base + i)
                for i in range(count)]

    def _fake_sources(self, *, archive_names: dict):
        """`(yahoo, archive, asked)` — recording fakes, no network."""
        asked: dict[str, list] = {}
        count = self.builder.MIN_CORPUS_SESSIONS + 2

        def yahoo(tickers, sessions, as_of):
            return {ticker: self._pairs(100.0, count)
                    for ticker in tickers if ticker in ("^NSEI", "^NSEBANK")}

        def archive(tickers, window_start, as_of, cache_dir):
            asked["tickers"] = list(tickers)
            asked["window_start"] = window_start
            return ({ticker: self._pairs(200.0, count)
                     for ticker in tickers if ticker in archive_names},
                    {"published": 5, "no_file": ["2024-01-06"]})

        return yahoo, archive, asked

    def test_each_series_records_the_source_that_served_it(self):
        yahoo, archive, _asked = self._fake_sources(
            archive_names={"^CNXENERGY": "Nifty Energy"})
        document = self.builder.build(as_of=date(2026, 10, 15),
                                      yahoo=yahoo, archive=archive,
                                      cache_dir=Path(tempfile.mkdtemp()))
        served = document["tickers"]
        self.assertIn("^NSEI", served)
        self.assertIn("^CNXENERGY", served)
        self.assertIn("yahoo", served["^NSEI"]["source"])
        self.assertIn("nse", served["^CNXENERGY"]["source"].lower())
        for ticker, entry in served.items():
            with self.subTest(ticker=ticker):
                self.assertTrue(entry.get("source"),
                                "a served series must name its source")
        self.assertEqual(self.builder.check(document), [],
                         "the two-source document must satisfy the contract")

    def test_the_fallback_is_only_asked_for_what_yahoo_missed(self):
        yahoo, archive, asked = self._fake_sources(
            archive_names={"^CNXENERGY": "Nifty Energy"})
        self.builder.build(as_of=date(2026, 10, 15), yahoo=yahoo,
                           archive=archive, cache_dir=Path(tempfile.mkdtemp()))
        self.assertNotIn("^NSEI", asked["tickers"],
                         "Yahoo served NIFTY 50 — the archive is a fallback, "
                         "not a second fetch of what already arrived")
        self.assertNotIn("^NSEBANK", asked["tickers"])
        self.assertIn("^CNXENERGY", asked["tickers"])
        self.assertEqual(set(asked["tickers"]),
                         set(self.builder.panel()) - {"^NSEI", "^NSEBANK"})

    def test_the_archive_window_starts_before_the_stock_corpus(self):
        """The fallback must reach the corpus it serves, like the Yahoo window."""
        yahoo, archive, asked = self._fake_sources(archive_names={})
        self.builder.build(as_of=date(2026, 10, 15), yahoo=yahoo,
                           archive=archive, cache_dir=Path(tempfile.mkdtemp()))
        first, _last = TestThePanelCoversTheStockCorpus().stock_span()
        self.assertLessEqual(asked["window_start"].isoformat(), first,
                             "the archive window would gate row dates")

    def test_a_ticker_neither_source_serves_stays_absent_naming_both(self):
        yahoo, archive, _asked = self._fake_sources(archive_names={})
        document = self.builder.build(as_of=date(2026, 10, 15), yahoo=yahoo,
                                      archive=archive,
                                      cache_dir=Path(tempfile.mkdtemp()))
        self.assertIn("^CNXENERGY", document["absent"])
        reason = document["absent"]["^CNXENERGY"]
        self.assertIn("yahoo", reason.lower())
        self.assertIn("nse", reason.lower())
        self.assertEqual(self.builder.check(document), [])

    def test_the_committed_corpus_serves_the_sectors_the_graded_tapes_trade(self):
        """The three sectors behind the six cone-labeled records are served.

        RELIANCE (`^CNXENERGY`), LT and ADANIPORTS (`^CNXINFRA`) and the banks
        (`^NSEBANK`) are the benchmark's own engine-graded rows; a panel that
        cannot serve one of those sectors cannot train on the symbol, and the
        cone graded for it is built from other sectors' excursions.
        """
        served = set(json.loads(CORPUS_FILE.read_text(encoding="utf-8"))["tickers"])
        dataset = json.loads((EVALS / "golden_dataset.json").read_text(encoding="utf-8"))
        spec = importlib.util.spec_from_file_location(
            "uvest_sector_map", SCRIPTS / "_sector_map.py")
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        required = set()
        for trade in dataset["trades"]:
            if trade.get("cone_p10") is None:
                continue
            ticker = module.SECTOR_INDEX_MAP.get(str(trade.get("symbol")))
            if ticker:
                required.add(ticker)
        self.assertTrue(required, "no graded record maps to a sector index")
        self.assertEqual(required - served, set(),
                         "the committed panel cannot serve a sector the "
                         "benchmark's graded tapes trade in")


class TestArchiveDayFetchedHonestly(unittest.TestCase):
    """A day the source did not publish is not a day we failed to reach.

    The archive is one file per trading day, so its coverage is a sequence of
    HTTP outcomes. Only the source's own "no such file" (404) may be recorded
    as a missing session; a timeout, a reset or a 5xx is *our* failure, and
    recording it as an absent session would understate a series and blame the
    source for a network blip. The cache is the resumable half: a fetched day is
    kept, an unreachable one is retried next run, never cached as empty.
    """

    @classmethod
    def setUpClass(cls):
        cls.builder = load_builder()

    def test_a_day_the_source_published_is_cached(self):
        calls = []

        def day_fetcher(day):
            calls.append(day)
            return "published", {"Nifty Auto": 20000.0}

        with tempfile.TemporaryDirectory() as tmp:
            series, note = self.builder.fetch_nse_index_closes(
                ["^CNXAUTO"], window_start=date(2024, 1, 1),
                as_of=date(2024, 1, 3),
                cache_dir=Path(tmp), day_fetcher=day_fetcher, workers=1)
            # Sunday 2024-01-07 is not a weekday, so only 1st-3rd are asked.
            self.assertEqual(sorted(calls), [date(2024, 1, 1), date(2024, 1, 2),
                                             date(2024, 1, 3)])
            self.assertEqual(note["published"], 3)
            self.assertEqual(series["^CNXAUTO"][0], ("2024-01-01", 20000.0))
            # A second run over the same window re-reads the cache.
            again, _note = self.builder.fetch_nse_index_closes(
                ["^CNXAUTO"], window_start=date(2024, 1, 1),
                as_of=date(2024, 1, 3),
                cache_dir=Path(tmp), day_fetcher=day_fetcher, workers=1)
            self.assertEqual(len(calls), 3, "a cached day must not be re-fetched")
            self.assertEqual(again, series)

    def test_a_missing_file_is_recorded_as_the_sources_own_gap(self):
        def day_fetcher(day):
            return ("published", {"Nifty Auto": 1.0}) if day.day != 2 \
                else ("no_file", {})

        with tempfile.TemporaryDirectory() as tmp:
            _series, note = self.builder.fetch_nse_index_closes(
                ["^CNXAUTO"], window_start=date(2024, 1, 1),
                as_of=date(2024, 1, 3), cache_dir=Path(tmp),
                day_fetcher=day_fetcher, workers=1)
        self.assertEqual(note["no_file"], ["2024-01-02"])
        self.assertEqual(note["published"], 2)

    def test_an_unreachable_day_fails_the_fetch_and_is_never_cached_empty(self):
        def day_fetcher(day):
            if day.day == 2:
                raise ConnectionError("reset by peer")
            return "published", {"Nifty Auto": 1.0}

        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            with self.assertRaises(Exception) as caught:
                self.builder.fetch_nse_index_closes(
                    ["^CNXAUTO"], window_start=date(2024, 1, 1),
                    as_of=date(2024, 1, 3), cache_dir=cache,
                    day_fetcher=day_fetcher, workers=1)
            self.assertIn("2024-01-02", str(caught.exception),
                          "the failure must name the day it could not reach")
            self.assertFalse(
                any("20240102" in path.name for path in cache.glob("*")),
                "an unreachable day must not be cached as an empty session")
            # ...while the days that did arrive stay cached for the retry.
            self.assertTrue(any("20240101" in path.name
                                for path in cache.glob("*")))
            # And the retry, with the source reachable again, completes.
            series, note = self.builder.fetch_nse_index_closes(
                ["^CNXAUTO"], window_start=date(2024, 1, 1),
                as_of=date(2024, 1, 3), cache_dir=cache,
                day_fetcher=lambda day: ("published", {"Nifty Auto": 2.0}),
                workers=1)
            self.assertEqual(note["published"], 3)
            self.assertEqual(len(series["^CNXAUTO"]), 3)


class TestContractRefusals(unittest.TestCase):
    """Each way the corpus could lie is refused, not graded."""

    @classmethod
    def setUpClass(cls):
        cls.builder = load_builder()
        cls.document = json.loads(CORPUS_FILE.read_text(encoding="utf-8"))

    def test_a_thin_series_is_refused(self):
        broken = copy.deepcopy(self.document)
        ticker = next(iter(broken["tickers"]))
        broken["tickers"][ticker]["values"] = \
            broken["tickers"][ticker]["values"][:5]
        broken["tickers"][ticker]["sessions"] = 5
        problems = self.builder.check(broken)
        self.assertTrue(any("below the" in p for p in problems), problems)

    def test_a_dropped_ticker_is_refused_rather_than_tolerated(self):
        broken = copy.deepcopy(self.document)
        dropped = next(iter(broken["tickers"]))
        del broken["tickers"][dropped]
        problems = self.builder.check(broken)
        self.assertTrue(any("panel drift" in p for p in problems), problems)

    def test_unordered_or_duplicated_dates_are_refused(self):
        broken = copy.deepcopy(self.document)
        ticker = next(iter(broken["tickers"]))
        values = broken["tickers"][ticker]["values"]
        values[0], values[1] = values[1], values[0]
        problems = self.builder.check(broken)
        self.assertTrue(any("not sorted" in p for p in problems), problems)

    def test_a_wrong_schema_is_refused(self):
        broken = copy.deepcopy(self.document)
        broken["schema"] = "uvest-something-else/1"
        problems = self.builder.check(broken)
        self.assertTrue(any("schema is" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
