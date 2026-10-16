"""Seam — the live market-context fetcher (`scripts/fetch_market_context.py`).

The fetcher is the only code allowed to turn a network response into stage-7
evidence, so every seam here pins the property a broken fetch could otherwise
quietly violate:

1. **Parsers are fixture-proven, locale-free.** The committed NSE API JSON,
   NSE constituents CSV and public cash-table excerpt parse to the same
   numbers — including the cross-source agreement (NSE's own API and the
   public table both say the 2026-10-14 combined net was ₹813.0 cr), which is
   the whole reason the second source is allowed to exist.
2. **Absence stays absence.** A failed source omits its series from the
   bundle and records the failure in `provenance.sources`; a date past
   `--as-of` never enters a series (no lookahead); a breadth date with no
   eligible 200-DMA window is not fabricated; the axes are inner-joined, never
   padded. The bundle may be thin — it may never be invented.
3. **The assembly path the live run takes is the one tested.** `fetch_bundle`
   with injected sources (no network) must produce a `uvest-market-context/1`
   bundle that the REAL stage-7 CLI consumes non-waived, and an unmapped
   symbol must refuse at the CLI with exit 2 rather than emit a bundle stage
   7 would only waive.

The full network path is a separate live smoke, opt-in via `UVEST_LIVE=1`
(the invariant suite itself must run offline).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
SCRIPTS = REPO_ROOT / "scripts"
FIXTURES = TESTS_DIR / "fixtures"
sys.path.insert(0, str(SCRIPTS))

import fetch_market_context as fmc  # noqa: E402

AS_OF = date(2026, 10, 14)  # the fixtures' session; matches their newest point
NSE_FIXTURE = FIXTURES / "fii_dii_nse_api.json"
TABLE_FIXTURE = FIXTURES / "fii_dii_5paisa_cash_table.html"
CONSTITUENTS_FIXTURE = FIXTURES / "ind_nifty500list_sample.csv"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def rising(days: int, start: float, step: float, end: str) -> list:
    """Synthetic closes ending on `end`, one per weekday going back."""
    from datetime import timedelta
    last = date.fromisoformat(end)
    dates = []
    cursor = last
    while len(dates) < days:
        if cursor.weekday() < 5:
            dates.append(cursor)
        cursor -= timedelta(days=1)
    dates.reverse()
    return [(d.isoformat(), start + step * i) for i, d in enumerate(dates)]


class TestFixtureParsers(unittest.TestCase):
    """The committed fixtures parse to frozen numbers, locale-free."""

    def test_nse_api_point_and_public_table_agree_to_the_decimal(self):
        nse = fmc.parse_nse_fii_dii(json.loads(read_text(NSE_FIXTURE)))
        table = fmc.parse_fii_dii_table(read_text(TABLE_FIXTURE))
        self.assertEqual(nse, [{"date": "2026-10-14", "net_cr": 813.0,
                                "source": "nse-api"}])
        newest = next(p for p in table if p["date"] == "2026-10-14")
        self.assertEqual(newest["net_cr"], 813.0,
                         "the public table must agree with NSE's own API on "
                         "the session both report — that agreement is what "
                         "licenses the second source")
        self.assertEqual(len(table), 9, "every dated cash row parses")
        self.assertTrue(all(p["source"] == "public-table" for p in table))
        self.assertTrue(all(p["date"] <= "2026-10-14" for p in table))

    def test_month_till_date_and_monthly_aggregates_are_never_points(self):
        rows = ("<table><tr class=\"cash_current_data\"><td>Month till date</td>"
                "<td>1</td><td>2</td><td>-3</td><td>4</td><td>5</td>"
                "<td>6</td></tr>"
                "<tr class=\"cash_prev_data\"><td>Aug 2026</td><td>1</td>"
                "<td>2</td><td>-3</td><td>4</td><td>5</td><td>6</td></tr>"
                "</table>")
        self.assertEqual(fmc.parse_fii_dii_table(rows), [])

    def test_constituents_csv_is_eq_only_and_ordered(self):
        symbols = fmc.parse_constituents_csv(read_text(CONSTITUENTS_FIXTURE))
        self.assertIn("RELIANCE", symbols)
        self.assertIn("SBIN", symbols)
        self.assertGreater(len(symbols), 400)
        self.assertEqual(len(symbols), len(set(symbols)), "no duplicates")

    def test_date_and_number_parsing_needs_no_english_locale(self):
        self.assertEqual(fmc.parse_session_date("14-Oct-2026"),
                         date(2026, 10, 14))
        self.assertIsNone(fmc.parse_session_date("Month till date"))
        self.assertIsNone(fmc.parse_session_date("Aug 2026"))
        self.assertEqual(fmc.parse_number("-1,857.40"), -1857.4)
        self.assertEqual(fmc.parse_number("+2,670.40\u00a0"), 2670.4)
        self.assertIsNone(fmc.parse_number("NA"))


class TestSeriesBuilders(unittest.TestCase):
    """Truncation, alignment and the breadth math are pure and exact."""

    def test_truncate_drops_every_point_after_the_horizon(self):
        pairs = [("2026-10-13", 1.0), ("2026-10-14", 2.0), ("2026-10-15", 3.0)]
        self.assertEqual(fmc.truncate(pairs, AS_OF),
                         [("2026-10-13", 1.0), ("2026-10-14", 2.0)])

    def test_align_axes_inner_joins_without_inventing_dates(self):
        series = {
            "^NSEI": [("2026-10-10", 0.5), ("2026-10-13", 1.0),
                      ("2026-10-14", 2.0)],
            "^INDIAVIX": [("2026-10-13", 15.0), ("2026-10-14", 16.0),
                          ("2026-10-15", 17.0)],
        }
        aligned, omitted = fmc.align_axes(series, min_sessions=2)
        self.assertEqual(omitted, [])
        self.assertEqual(aligned["^NSEI"],
                         [("2026-10-13", 1.0), ("2026-10-14", 2.0)])
        self.assertEqual(aligned["^INDIAVIX"],
                         [("2026-10-13", 15.0), ("2026-10-14", 16.0)])
        # Order follows the first series; no date appears that any series lacked.
        self.assertEqual([d for d, _ in aligned["^INDIAVIX"]],
                         ["2026-10-13", "2026-10-14"])

    def test_align_axes_omits_a_thin_series_instead_of_cutting_the_others(self):
        """One stub series must not decide the whole bundle's axis.

        The live failure this pins: for an energy-sector trade Yahoo served
        `^CNXENERGY` a single session while `^NSEI` had ~320 and `^INDIAVIX`
        ~319, and the plain inner join cut *every* series to that one date —
        the sector stub took the trend and panic-VIX evidence down with it.
        A member below the floor is omitted (and reported so the caller can
        disclose it); the series that can carry a joint estimate keep their
        own sessions.
        """
        series = {
            "^NSEI": [("2026-10-13", 1.0), ("2026-10-14", 2.0)],
            "^INDIAVIX": [("2026-10-13", 15.0), ("2026-10-14", 16.0)],
            "^NSEBANK": [("2026-10-14", 9.0)],
        }
        aligned, omitted = fmc.align_axes(series, min_sessions=2)
        self.assertEqual(omitted, [("^NSEBANK", 1)])
        self.assertNotIn("^NSEBANK", aligned)
        self.assertEqual([d for d, _ in aligned["^NSEI"]],
                         ["2026-10-13", "2026-10-14"])
        self.assertEqual([d for d, _ in aligned["^INDIAVIX"]],
                         ["2026-10-13", "2026-10-14"])

    def test_the_default_join_floor_is_one_trading_month(self):
        """Below a month of aligned sessions no joint estimate is possible.

        The floor is the fetcher's, not a consumer's stage-7 threshold: it is
        the shortest *joint* window any reader aligns (stage 4's 20-session
        cross-asset window), so a series under it is dropped rather than
        allowed to cut the others. Every-member-thin yields no axis at all —
        never a one-date bundle that reads as evidence.
        """
        self.assertEqual(fmc.MIN_JOIN_SESSIONS, 21)
        short = {name: [(f"2026-09-{i:02d}", 1.0) for i in range(1, 21)]
                 for name in ("^NSEI", "^INDIAVIX")}  # 20 sessions each
        aligned, omitted = fmc.align_axes(short)
        self.assertEqual(aligned, {})
        self.assertEqual(omitted, [("^INDIAVIX", 20), ("^NSEI", 20)],
                         "name-sorted so the disclosed order is stable")
        long_ = {name: [(f"2026-09-{i:02d}", 1.0) for i in range(1, 22)]
                 for name in ("^NSEI", "^INDIAVIX")}  # 21 sessions each
        aligned, omitted = fmc.align_axes({**long_, "^NSEBANK": [("2026-09-21", 1.0)]})
        self.assertEqual(omitted, [("^NSEBANK", 1)])
        self.assertEqual(len(aligned["^NSEI"]), 21)

    def test_breadth_counts_only_eligible_stocks_and_skips_thin_dates(self):
        # A: always rising (above its own 200-DMA once eligible).
        # B: always falling (below it).
        # C: only 100 sessions — never eligible, so never counted.
        closes = {
            "A": rising(210, 100.0, 1.0, "2026-10-14"),
            "B": rising(210, 10000.0, -50.0, "2026-10-14"),
            "C": rising(100, 50.0, 1.0, "2026-10-14"),
        }
        breadth = fmc.breadth_from_closes(closes, AS_OF)
        self.assertTrue(breadth)
        first_day, first_value = breadth[0]
        # Before any stock reaches a full window there is no date at all.
        self.assertGreater(first_day, "2026-01-01")
        self.assertEqual(breadth[-1], (breadth[-1][0], 50.0),
                         "1 of 2 eligible stocks above its 200-DMA")
        # A stock with no window contributes nothing: dates only exist once
        # A and B are both eligible, and C never enters the denominator.
        self.assertTrue(all(v in (50.0,) for _, v in breadth[-5:]))
        # Future dates are out (no lookahead).
        future = fmc.breadth_from_closes(
            {"A": rising(210, 100.0, 1.0, "2026-11-30")}, AS_OF)
        self.assertTrue(all(d <= "2026-10-14" for d, _ in future))

    def test_flow_merge_official_wins_then_table_then_cache(self):
        official = [{"date": "2026-10-14", "net_cr": 813.0, "source": "nse-api"}]
        table = [{"date": "2026-10-14", "net_cr": 813.1, "source": "public-table"},
                 {"date": "2026-10-13", "net_cr": 1134.1, "source": "public-table"}]
        cache = [{"date": "2026-10-13", "net_cr": 999.9, "source": "cache"},
                 {"date": "2026-10-09", "net_cr": -400.0, "source": "cache"}]
        merged = fmc.merge_flow_points(official, table, cache)
        by_date = {p["date"]: p for p in merged}
        self.assertEqual(by_date["2026-10-14"]["source"], "nse-api")
        self.assertEqual(by_date["2026-10-13"]["source"], "public-table")
        self.assertEqual(by_date["2026-10-09"]["source"], "cache")
        self.assertEqual([p["date"] for p in merged],
                         sorted(by_date), "sorted by date")

    def test_flow_cache_round_trips_and_rejects_junk(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "flow.json"
            points = [{"date": "2026-10-14", "net_cr": 813.0,
                       "source": "nse-api"}]
            fmc.save_flow_cache(path, points)
            self.assertEqual(fmc.load_flow_cache(path), points)
            path.write_text('{"points": [{"date": "junk", "net_cr": 1},'
                            ' {"net_cr": true}, "not-a-point",'
                            ' {"date": "2026-10-13", "net_cr": 5}]}',
                            encoding="utf-8")
            self.assertEqual(fmc.load_flow_cache(path),
                             [{"date": "2026-10-13", "net_cr": 5.0,
                               "source": "cache"}])
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(fmc.load_flow_cache(path), [])


def _fake_sources(*, drop: tuple[str, ...] = (),
                  as_of: date = AS_OF,
                  sector_sessions: int | None = None) -> dict:
    """Injected sources: real shapes, zero network."""
    index = {
        "^NSEI": rising(210, 24000.0, 5.0, as_of.isoformat()),
        "^INDIAVIX": ([(d, 14.0) for d, _ in rising(210, 0, 1, as_of.isoformat())]
                      [:-1] + [(as_of.isoformat(), 26.5)]),
        "^NSEBANK": rising(210, 52000.0, -3.0, as_of.isoformat()),
    }
    if sector_sessions is not None:
        # Yahoo serving a stub series for one ticker — the live `^CNXENERGY`.
        index["^NSEBANK"] = index["^NSEBANK"][-sector_sessions:] or []
    # 1 of 4 eligible names above its 200-DMA on the last session ⇒ a 25%
    # breadth print, which satisfies the panic breaker's breadth arm (< 30).
    panel = {
        "RELIANCE": rising(210, 100.0, 1.0, as_of.isoformat()),
        "SBIN": rising(210, 600.0, -1.0, as_of.isoformat()),
        "TCS": rising(210, 3500.0, -10.0, as_of.isoformat()),
        "INFY": rising(210, 1400.0, -5.0, as_of.isoformat()),
    }
    official = [{"date": d, "net_cr": n, "source": "nse-api"}
                for (d, _v), n in zip(
                    rising(6, 0, 1, as_of.isoformat())[-6:],
                    [-500.0, -600.0, -700.0, -800.0, -900.0, -7000.0])]
    table = [{"date": (as_of.replace(day=1)).isoformat(),
              "net_cr": -6500.0, "source": "public-table"}]
    cached = [{"date": "2026-01-05", "net_cr": -5200.0, "source": "cache"}]
    if "indices" in drop:
        index = {k: v for k, v in index.items() if k in ("^NSEBANK",)}
    if "sector" in drop:
        index.pop("^NSEBANK", None)
    return {
        "indices": lambda: {k: v for k, v in index.items()
                            if k in ("^NSEI", "^INDIAVIX")},
        "sector": lambda tick: index.get(tick, []),
        "constituents": lambda: list(panel),
        "breadth_panel": lambda symbols, sessions, as_of: panel,
        "flow_layers": lambda cache: (official, table, cached),
    }


class TestAssembly(unittest.TestCase):
    """The injected-source path builds the bundle stage 7 actually consumes."""

    def _bundle(self, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            return fmc.fetch_bundle(
                "SBIN", AS_OF, flow_cache=Path(tmp) / "flow.json",
                sources=_fake_sources(**kwargs))

    def test_full_bundle_carries_all_five_series_and_disclosed_sources(self):
        bundle, statuses = self._bundle()
        self.assertEqual(bundle["schema"], "uvest-market-context/1")
        self.assertEqual(bundle["as_of"], "2026-10-14")
        self.assertEqual(bundle["provenance"]["origin"], "recorded")
        self.assertEqual(
            set(bundle["series"]),
            {"^NSEI", "^NSEBANK", "^INDIAVIX", "NSE500_BREADTH", "FII_DII_NET"})
        for entry in statuses:
            self.assertIn(entry["status"], ("ok", "failed"))
            self.assertTrue(entry["source"])
        # No lookahead anywhere, in any series.
        for key, doc in bundle["series"].items():
            for point in doc["values"]:
                self.assertLessEqual(point["date"], "2026-10-14",
                                     f"{key}: post-as_of point")
        # The axes agree, as the stage requires (values differ per index;
        # the DATE axis is what must be identical).
        axes = [[p["date"] for p in bundle["series"][k]["values"]] for k in
                ("^NSEI", "^NSEBANK", "^INDIAVIX")]
        self.assertEqual(axes[0], axes[1])
        self.assertEqual(axes[0], axes[2])
        # Flow carries the official worst-case day the fixture pinned.
        flow = bundle["series"]["FII_DII_NET"]["values"]
        self.assertEqual(flow[-1]["value"], -7000.0)

    def test_the_real_stage7_cli_consumes_the_bundle_without_waiving(self):
        bundle, _ = self._bundle()
        with tempfile.TemporaryDirectory() as tmp:
            ctx = Path(tmp) / "ctx.json"
            ctx.write_text(json.dumps(bundle), encoding="utf-8")
            trade = Path(tmp) / "current_trade.json"
            trade.write_text(json.dumps({
                "symbol": "SBIN", "exchange": "NSE",
                "instrument_type": "FUT", "action": "BUY",
                "entry_price": 190.95, "target": 216.93, "stoploss": 177.96,
                "lot_size": 2625, "margin": 500000.0,
                "recommended_date": "2026-10-14",
            }), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(SCRIPTS / "7_sector_macro_regime.py"),
                 "--input", str(trade), "--regime-fixture", str(ctx)],
                capture_output=True, check=False, encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertFalse(payload["waived"], payload)
        self.assertEqual(payload["missing_inputs"], [])
        self.assertTrue(payload["panic_regime_breaker"])
        self.assertEqual(payload["vix_value"], 26.5)
        self.assertEqual(payload["market_breadth_pct"], 25.0)
        self.assertEqual(payload["breadth_regime"], "RISK_OFF")
        self.assertEqual(payload["sector_index"], "^NSEBANK")
        self.assertEqual(payload["nifty_trend"], "ABOVE_50_200_DMA")

    def test_a_failed_source_omits_its_series_and_records_the_failure(self):
        bundle, statuses = self._bundle(drop=("sector",))
        self.assertNotIn("^NSEBANK", bundle["series"])
        failed = [s for s in statuses if s["status"] == "failed"]
        self.assertTrue(any(s["series"] == "^NSEBANK" for s in failed),
                        statuses)
        # The surviving series still carry real points — one failure never
        # fabricates or blanks the rest.
        self.assertIn("^NSEI", bundle["series"])

    def test_a_thin_sector_series_loses_only_itself(self):
        """The live bug at the assembly level, not just in the pure join.

        With the sector index served as a single session, the bundle used to
        arrive with all three index series cut to that one date. It must now
        arrive with the sector absent, its thinness disclosed, and the NIFTY
        trend plus panic-VIX evidence intact.
        """
        bundle, statuses = self._bundle(sector_sessions=1)
        self.assertNotIn("^NSEBANK", bundle["series"])
        self.assertEqual(len(bundle["series"]["^NSEI"]["values"]), 210)
        self.assertEqual(len(bundle["series"]["^INDIAVIX"]["values"]), 210)
        self.assertEqual(bundle["series"]["^INDIAVIX"]["values"][-1]["value"],
                         26.5, "the panic-VIX print survived the sector stub")
        thin = [s for s in statuses if s["series"] == "^NSEBANK"]
        self.assertTrue(any("too thin to join" in s["detail"] for s in thin),
                        statuses)
        self.assertTrue(all(s["status"] == "failed" for s in thin[-1:]),
                        statuses)

    def test_stage7_waives_only_the_thin_series_the_bundle_lost(self):
        """End to end: the waiver names one series, not the whole axis.

        Because the other series kept their sessions, stage 7 sees an
        adjacent-series bundle and its tri-state contract can say exactly
        which index is missing — instead of `index-axis:misaligned` wiping
        out a verdict it could have formed from the trend and VIX it has.
        """
        bundle, _ = self._bundle(sector_sessions=1)
        with tempfile.TemporaryDirectory() as tmp:
            ctx = Path(tmp) / "ctx.json"
            ctx.write_text(json.dumps(bundle), encoding="utf-8")
            trade = Path(tmp) / "current_trade.json"
            trade.write_text(json.dumps({
                "symbol": "SBIN", "exchange": "NSE",
                "instrument_type": "FUT", "action": "BUY",
                "entry_price": 190.95, "target": 216.93, "stoploss": 177.96,
                "lot_size": 2625, "margin": 500000.0,
                "recommended_date": "2026-10-14",
            }), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(SCRIPTS / "7_sector_macro_regime.py"),
                 "--input", str(trade), "--regime-fixture", str(ctx)],
                capture_output=True, check=False, encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["waived"])
        named = " ".join(payload.get("missing_inputs") or [])
        self.assertIn("^NSEBANK", named)
        for key in ("^NSEI", "^INDIAVIX", "NSE500_BREADTH", "FII_DII_NET"):
            self.assertNotIn(
                key, named,
                f"the bundle lost {key} to the sector stub's join: "
                f"{payload.get('missing_inputs')}")

    def test_unmapped_symbols_are_refused_at_the_cli(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "fetch_market_context.py"),
             "--symbol", "MYSTERYCO", "--output", "nope.json"],
            capture_output=True, check=False, encoding="utf-8")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("symbol:unmapped:MYSTERYCO", proc.stderr)
        self.assertFalse(Path("nope.json").exists())

    def test_bad_as_of_is_a_usage_error_not_a_bundle(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "fetch_market_context.py"),
             "--symbol", "SBIN", "--output", "nope.json",
             "--as-of", "14/10/2026"],
            capture_output=True, check=False, encoding="utf-8")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("YYYY-MM-DD", proc.stderr)


@unittest.skipUnless(os.environ.get("UVEST_LIVE") == "1",
                     "network smoke — opt in with UVEST_LIVE=1")
class TestLiveFetch(unittest.TestCase):
    """The real network path, end to end (never part of the offline suite)."""

    def test_live_bundle_reaches_stage7_without_waiver(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "ctx.json"
            proc = subprocess.run(
                [sys.executable, str(SCRIPTS / "fetch_market_context.py"),
                 "--symbol", "SBIN", "--output", str(out),
                 "--flow-cache", str(Path(tmp) / "flow.json")],
                capture_output=True, check=False, encoding="utf-8")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            bundle = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(bundle["schema"], "uvest-market-context/1")
            self.assertGreaterEqual(len(bundle["series"]), 5,
                                    proc.stdout + proc.stderr)
            trade = Path(tmp) / "current_trade.json"
            trade.write_text(json.dumps({
                "symbol": "SBIN", "exchange": "NSE",
                "instrument_type": "FUT", "action": "BUY",
                "entry_price": 190.95, "target": 216.93, "stoploss": 177.96,
                "lot_size": 2625, "margin": 500000.0,
                "recommended_date": date.today().isoformat(),
            }), encoding="utf-8")
            stage = subprocess.run(
                [sys.executable, str(SCRIPTS / "7_sector_macro_regime.py"),
                 "--input", str(trade), "--regime-fixture", str(out)],
                capture_output=True, check=False, encoding="utf-8")
            self.assertEqual(stage.returncode, 0, stage.stderr)
            payload = json.loads(stage.stdout)
            self.assertFalse(payload["waived"], payload)
            self.assertFalse(payload["panic_regime_breaker"] is None)
            for key in ("vix_value", "market_breadth_pct", "fii_dii_flow_bias",
                        "breadth_regime", "nifty_trend"):
                self.assertIsNotNone(payload[key], payload)


if __name__ == "__main__":
    unittest.main()
