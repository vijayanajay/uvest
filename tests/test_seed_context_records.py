"""Seam — the representative-set context seed (`tests/evals/seed_context_records.py`).

Gates 4 and 5 were graded over six crisis tapes and nothing else: an honest
absence for every other record (`no market-context bundle`), and a denominator
curated to trip breakers. The owner signed option C
(`docs/agents/proposal-gate-thresholds.md`, Q3 2026-10-16): keep both
thresholds, re-scope the *graded set* to representative + tail slice — the
crisis rows stay in the denominator as a disclosed tail slice, and the roster's
clean / inverted-rr rows join it behind recorded stage-4 evidence.

This seam freezes how that campaign may be run, because every part of it is a
place a curator could quietly fake a green board:

1. **The bundle is sourced, not authored.** The whole point of the campaign is
   that gate 4's probability is computed over real point-in-time market
   context. Each bundle's series must be byte-equal to the committed tier-1
   index corpus (`tests/fixtures/history/indices_daily.json`) — `close` mapped
   to the schema's `value`, nothing recomputed, nothing smoothed — with
   `origin: recorded` naming that corpus. A hand-authored trajectory would feed
   fabricated beta/correlation features into the very number the gate grades.
2. **Point-in-time by construction.** Every series point must sit inside
   `[first session of the row's own OHLCV prefix, T_rec]`: the bundle may not
   reach past the recommendation, and V7's look-ahead scan reads these points.
3. **An unmapped symbol is never guessed.** `SECTOR_INDEX_MAP` is conservative
   by design (ADR-010 decision 5): it carries a symbol's sector only when a
   pin was read from NSE's own constituent list, and a symbol with no pin gets
   *no* bundle, no stage-4 artifact and null claims — disclosed, never a proxy
   index and never a fabricated sector series. `TITAN` and `BAJFINANCE` were
   unpinned at signing (2026-10-16) and pinned from those official lists on
   2026-10-17, which is what took the roster from 21 to 25; the never-guess
   guard itself is asserted directly below, because no roster row is unmapped
   any more.
4. **What is absent says so.** The committed corpus serves no breadth or
   FII/DII series, so those keys are absent and the disclosure says a stage-7
   run over this bundle would waive *naming* them — never an invented value.
5. **The claims came out of recorded bytes.** Every promoted `cone_p10` /
   `cone_p90` / `predicted_direction` / `predicted_probability` must equal the
   values read out of the row's own recorded `out_4_ml.json`, referenced with
   `$ref` and declared `recorded` (V10 byte-compares those bytes to a fresh
   offline stage-4 run), and a waived payload promotes nulls.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
PROOF_ROOT = TESTS_DIR / "proof"
DATASET_FILE = TESTS_DIR / "evals" / "golden_dataset.json"
CORPUS_FILE = TESTS_DIR / "fixtures" / "history" / "indices_daily.json"
sys.path.insert(0, str(TESTS_DIR / "evals"))
sys.path.insert(0, str(TESTS_DIR))

import dataset_refs as dr  # noqa: E402  (sibling eval modules, spliced above)
import seed_context_records as seed  # noqa: E402

try:  # the map is the same source stages 4 and 7 read (ADR-010 decision 5)
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from _sector_map import SECTOR_INDEX_MAP  # noqa: E402
except ImportError:  # pragma: no cover — scripts dir is committed
    SECTOR_INDEX_MAP = {}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class TestTheRepresentativeRoster(unittest.TestCase):
    """Who the campaign may touch, and who it must leave alone."""

    @classmethod
    def setUpClass(cls):
        cls.raw = read_json(DATASET_FILE)
        cls.dataset, _refs, _derives = dr.materialize(cls.raw)
        cls.corpus = read_json(CORPUS_FILE)
        rows = read_json(PROOF_ROOT / "scenario_manifest.json")["scenarios"]
        cls.rows = {row["scenario_id"]: row for row in rows}
        cls.trades = [t for t in cls.raw["trades"]
                      if t["trade_class"] in seed.REPRESENTATIVE_CLASSES]
        cls.by_id = {t["trade_id"]: t for t in cls.trades}
        cls.mapped = [t for t in cls.trades
                      if SECTOR_INDEX_MAP.get(t.get("symbol") or "") is not None]
        cls.unmapped = [t for t in cls.trades
                        if SECTOR_INDEX_MAP.get(t.get("symbol") or "") is None]
        # The founder seam this file froze on 2026-10-16 read the *forward tape*
        # as the bundle gate (option C signed an in-window graded set); the
        # owner ruled on 2026-10-17 that the tape window scopes gate 5's
        # denominator instead (evals.md §3 #5), so the split below is used for
        # disclosure only — the out-of-window rows bundle point-in-time and are
        # named on the coverage line.
        cal = read_json(REPO_ROOT / "models" / "lgbm_direction_v1.calibration.json")
        first, last = cal["provenance"]["corpus"]["row_window"][:2]
        cls.window = (str(first), str(last))
        cls.in_window, cls.out_of_window = [], []
        for trade in cls.mapped:
            span = dr.forward_tape_span(trade)
            inside = bool(span and cls.window[0] <= span[0]
                          and span[1] <= cls.window[1])
            (cls.in_window if inside else cls.out_of_window).append(trade)

    def test_the_roster_is_the_signed_clean_and_inverted_rows(self):
        """Option C names clean/inverted-rr — no hazard or crisis row joins."""
        self.assertEqual(set(seed.REPRESENTATIVE_CLASSES),
                         {"clean_bullish", "inverted_rr"})
        self.assertTrue(self.trades, "the roster must not be empty")
        self.assertEqual(
            {t["trade_class"] for t in self.trades},
            {"clean_bullish", "inverted_rr"})
        quota = self.raw["meta"]["class_counts_expected"]
        self.assertEqual(len([t for t in self.raw["trades"]
                              if t["trade_class"] == "clean_bullish"]),
                         quota["clean_bullish"])
        self.assertEqual(len([t for t in self.raw["trades"]
                              if t["trade_class"] == "inverted_rr"]),
                         quota["inverted_rr"])
        # The tail slice keeps every one of its rows: the campaign adds to the
        # denominator, it never removes from it.
        for cls_name in ("regime_shock", "pledge_hazard", "earnings_hazard",
                         "institutional_distribution", "tight_stop"):
            self.assertTrue(any(t["trade_class"] == cls_name
                                for t in self.raw["trades"]),
                            f"{cls_name} left the dataset")

    def test_every_authorable_row_carries_a_declared_bundle(self):
        """Every mapped roster row bundles — the tape window no longer gates it
        (owner rule, 2026-10-17: it scopes gate 5's denominator instead)."""
        self.assertTrue(self.mapped, "no authorable row on the roster")
        for trade in self.mapped:
            sid = trade["trade_id"]
            with self.subTest(scenario=sid):
                row = self.rows[sid]
                declared = row["replay_inputs"].get("regime_fixture")
                self.assertEqual(
                    declared, f"fixtures/{sid}/{seed.REGIME_FILE}",
                    f"{sid}: the bundle is declared where stage 4 reads it")
                self.assertTrue((PROOF_ROOT / declared).exists(),
                                f"{sid}: declared bundle missing on disk")

    def test_an_out_of_window_tape_is_bundled_point_in_time_and_named_for_gate5(self):
        """A forward tape past the trained window scopes gate 5, not this bundle.

        Stage 4's own inputs stop at T_rec, so the evidence is point-in-time
        like every other row's and is recorded; what the trained window still
        excludes — by gate 5's frozen scope (evals.md §3 #5) — is *grading* of
        that tape, and the campaign's own note must name those rows so the
        denominator shrinks as a disclosure rather than silently. This is the
        2026-10-17 owner rule that moved the roster from 21 to 25.
        """
        self.assertTrue(self.out_of_window,
                        "expected out-of-window row(s) on the roster")
        note = next((n for n in self.raw["meta"].get("notes", [])
                     if seed.NOTE_MARKER in n), None)
        self.assertIsNotNone(note)
        window = self.window
        for trade in self.out_of_window:
            sid = trade["trade_id"]
            with self.subTest(scenario=sid):
                span = dr.forward_tape_span(trade)
                self.assertIsNotNone(
                    span, f"{sid}: a cone-labeled row must pin its tape")
                first, last = span
                self.assertFalse(window[0] <= first and last <= window[1],
                                 f"{sid}: this row is no longer out of window — "
                                 "update the roster rather than the assertion")
                declared = self.rows[sid]["replay_inputs"].get("regime_fixture")
                self.assertEqual(declared, f"fixtures/{sid}/{seed.REGIME_FILE}")
                bundle = read_json(PROOF_ROOT / declared)
                for series in bundle["series"].values():
                    for point in series["values"]:
                        self.assertLessEqual(
                            point["date"], bundle["as_of"],
                            f"{sid}: a bundled series reaches past T_rec")
                raw = next(t for t in self.raw["trades"]
                           if t["trade_id"] == sid)
                self.assertIsNotNone(raw.get("cone_p10"),
                                     f"{sid}: point-in-time stage-4 evidence missing")
                self.assertIsNotNone(raw.get("predicted_probability"))
                self.assertIn(sid, note, "the excluded row must be named in the note")
                self.assertIn("excluded from gate 5's denominator", note)

    def test_every_roster_symbol_is_pinned_and_the_unmapped_guard_still_holds(self):
        """Pins come from NSE's own constituent lists — never a guessed index.

        At signing TITAN and BAJFINANCE had no pin and therefore no bundle;
        the owner closed that on 2026-10-17 by pinning both from NSE's official
        constituents CSVs (`scripts/_sector_map.py`'s note names each file),
        so the roster's unmapped set is now empty. The never-guess guard is
        still asserted directly — `bundle_doc` must raise for an entry without
        a sector before anything is written — because a tool whose refusal
        branch no longer executes is a tool nobody is testing.
        """
        self.assertEqual(
            self.unmapped, [],
            "every roster symbol now carries a pin — an unmapped row would mean "
            "a pin was dropped (ADR-010 decision 5: pins are read, never guessed)")
        self.assertEqual(SECTOR_INDEX_MAP["TITAN"], "^CNXCD")
        self.assertEqual(SECTOR_INDEX_MAP["BAJFINANCE"], "^CNXFINSERV25")
        for symbol in ("TITAN", "BAJFINANCE"):
            with self.subTest(symbol=symbol):
                self.assertIn(SECTOR_INDEX_MAP[symbol], self.corpus["tickers"],
                              "the pin's series must be served by the committed "
                              "corpus, else the bundle would be authored thin")
        entry = {"scenario_id": "SYN-UNMAPPED", "eligible": True,
                 "symbol": "NOTPINNED", "as_of": "2024-01-01", "sector": None}
        with self.assertRaises(seed.Blocked):
            seed.bundle_doc(entry, self.corpus)

    def test_the_campaign_discloses_its_roster_in_the_dataset(self):
        note = next((n for n in self.raw["meta"].get("notes", [])
                     if seed.NOTE_MARKER in n), None)
        self.assertIsNotNone(note, "the campaign must publish its own note")
        self.assertIn(f"{len(self.mapped)} market-context bundle(s)", note)
        self.assertIn("0 row(s) left unmapped", note)
        # The out-of-window rows are named, not merely counted: a reader has
        # to be able to see *which* advisory's tape gate 5 will not grade.
        for trade in self.out_of_window:
            self.assertIn(str(trade["trade_id"]), note)
        self.assertIn("indices_daily.json", note)
        self.assertIn("tail slice", note)


class TestTheBundlesAreSourcedPointInTime(unittest.TestCase):
    """`origin: recorded` over corpus bytes — never an authored trajectory."""

    @classmethod
    def setUpClass(cls):
        cls.raw = read_json(DATASET_FILE)
        cls.corpus = read_json(CORPUS_FILE)
        rows = read_json(PROOF_ROOT / "scenario_manifest.json")["scenarios"]
        cls.rows = {row["scenario_id"]: row for row in rows}
        cls.trades = {t["trade_id"]: t for t in cls.raw["trades"]}
        cls.bundles: dict[str, dict] = {}
        for sid, row in cls.rows.items():
            rel = (row.get("replay_inputs") or {}).get("regime_fixture")
            trade = cls.trades.get(sid)
            if rel and trade and trade.get("trade_class") in seed.REPRESENTATIVE_CLASSES:
                cls.bundles[sid] = read_json(PROOF_ROOT / rel)

    def test_the_campaign_authored_the_expected_number_of_bundles(self):
        self.assertEqual(len(self.bundles), seed.EXPECTED_BUNDLES)

    def test_each_bundle_is_sourced_recorded_evidence(self):
        for sid, bundle in self.bundles.items():
            with self.subTest(scenario=sid):
                self.assertEqual(bundle["schema"], "uvest-market-context/1")
                provenance = bundle["provenance"]
                self.assertEqual(provenance["origin"], "recorded")
                self.assertIn("indices_daily.json", provenance["source"])
                self.assertEqual(bundle["as_of"],
                                 self.rows[sid]["recommendation_date"])

    def test_the_series_are_byte_equal_to_the_corpus_within_the_prefix(self):
        """`close` → `value`, nothing recomputed, nothing reaching past T_rec."""
        for sid, bundle in self.bundles.items():
            row = self.rows[sid]
            prefix = read_json(PROOF_ROOT /
                               row["replay_inputs"]["truncated_fixtures"][0])
            first = prefix["sessions"][0]["date"]
            as_of = row["recommendation_date"]
            sector = SECTOR_INDEX_MAP[row["symbol"]]
            with self.subTest(scenario=sid):
                self.assertEqual(set(bundle["series"]),
                                 {seed.MARKET_TICKER, sector})
                for ticker, series in bundle["series"].items():
                    corpus = {p["date"]: p["close"] for p
                              in self.corpus["tickers"][ticker]["values"]}
                    points = series["values"]
                    self.assertTrue(points, f"{sid}/{ticker}: empty series")
                    dates = [p["date"] for p in points]
                    self.assertEqual(dates, sorted(dates))
                    self.assertGreaterEqual(dates[0], first,
                                            f"{sid}/{ticker}: reaches before "
                                            "the row's own prefix")
                    self.assertLessEqual(dates[-1], as_of,
                                         f"{sid}/{ticker}: reaches past T_rec")
                    for point in points:
                        self.assertIn(point["date"], corpus,
                                      f"{sid}/{ticker}: {point['date']} is not "
                                      "a corpus session")
                        self.assertEqual(
                            point["value"], corpus[point["date"]],
                            f"{sid}/{ticker}: value drifted from the corpus "
                            "bytes — a bundle may not re-derive its series")
                    # The 20-session beta/correlation window must be answerable.
                    common = (set(dates) & set(corpus) &
                              {s["date"] for s in prefix["sessions"]})
                    self.assertGreaterEqual(len(common), seed.MIN_COMMON_SESSIONS,
                                            f"{sid}/{ticker}: too few common "
                                            "sessions for the cross-asset window")

    def test_what_the_corpus_does_not_serve_is_absent_and_said_so(self):
        for sid, bundle in self.bundles.items():
            with self.subTest(scenario=sid):
                authoring = bundle["authoring"]
                self.assertEqual(authoring["source"], seed.SEED_SOURCE)
                self.assertEqual(authoring["scenario_id"], sid)
                self.assertEqual(authoring["trade_class"],
                                 self.trades[sid]["trade_class"])
                self.assertEqual(authoring["sector_index"],
                                 SECTOR_INDEX_MAP[self.rows[sid]["symbol"]])
                self.assertEqual(sorted(authoring["absent_series"]),
                                 sorted(seed.ABSENT_SERIES))
                for key in seed.ABSENT_SERIES:
                    self.assertNotIn(key, bundle["series"])
                disclosure = authoring["disclosure"]
                self.assertIn("stage 7", disclosure)
                self.assertIn("waive", disclosure)
                self.assertIn("indices_daily.json", disclosure)


class TestTheClaimsCameOutOfRecordedBytes(unittest.TestCase):
    """The promoted numbers are the stage's stdout, read back — never typed."""

    @classmethod
    def setUpClass(cls):
        cls.raw = read_json(DATASET_FILE)
        cls.dataset, _refs, _derives = dr.materialize(cls.raw)
        cls.raw_by_id = {t["trade_id"]: t for t in cls.raw["trades"]}
        cls.by_id = {t["trade_id"]: t for t in cls.dataset["trades"]}
        rows = read_json(PROOF_ROOT / "scenario_manifest.json")["scenarios"]
        cls.rows = {row["scenario_id"]: row for row in rows}
        cls.claimed = [t for t in cls.raw["trades"]
                       if t.get("cone_p10") is not None]

    def test_stage4_is_recorded_on_every_bundled_representative_row(self):
        for sid, row in self.rows.items():
            trade = self.raw_by_id.get(sid)
            if trade is None or trade.get("trade_class") not in seed.REPRESENTATIVE_CLASSES:
                continue
            if "regime_fixture" not in (row.get("replay_inputs") or {}):
                continue
            with self.subTest(scenario=sid):
                declared = [a for a in row.get("recorded_artifacts") or []
                            if a.get("stage") == 4]
                self.assertEqual(len(declared), 1, f"{sid}: declare stage 4 once")
                self.assertEqual(declared[0]["artifact"], "out_4_ml.json")
                self.assertEqual(declared[0]["evidence"], "recorded")
                self.assertEqual(
                    declared[0]["path"],
                    f"fixtures/{sid}/recorded/out_4_ml.json")
                self.assertTrue((PROOF_ROOT / declared[0]["path"]).exists())
                self.assertEqual(
                    trade["script_outputs"]["out_4_ml.json"],
                    {"$ref": f"tests/proof/fixtures/{sid}/recorded/out_4_ml.json"})

    def test_every_promoted_claim_matches_its_recorded_payload(self):
        """Cone and probability equal the bytes; a waived payload promotes null."""
        self.assertTrue(self.claimed, "no record carries a cone after the campaign")
        for trade in self.claimed:
            sid = trade["trade_id"]
            with self.subTest(scenario=sid):
                row = self.rows[sid]
                declared = [a for a in row["recorded_artifacts"]
                            if a.get("stage") == 4][0]
                payload = read_json(PROOF_ROOT / declared["path"])
                if payload.get("waived"):
                    self.assertIsNone(trade["cone_p10"])
                    self.assertIsNone(trade["cone_p90"])
                    self.assertIsNone(trade["predicted_direction"])
                    self.assertIsNone(trade["predicted_probability"])
                    continue
                cone = payload["price_cone_p10_p50_p90"]
                self.assertEqual(trade["cone_p10"], cone["p10"])
                self.assertEqual(trade["cone_p90"], cone["p90"])
                self.assertEqual(trade["predicted_direction"],
                                 payload["direction_label"])
                # Decision D (2026-10-16): the graded probability is the
                # target-versus-stop race at this trade's own geometry, which
                # is the payload key gate 4 is fed from.
                self.assertEqual(trade["predicted_probability"],
                                 payload["prob_target_hit_22d"])
                self.assertIsInstance(trade["predicted_probability"], float)
                self.assertGreaterEqual(trade["predicted_probability"], 0.0)
                self.assertLessEqual(trade["predicted_probability"], 1.0)
                # The stage's own frozen label set (4_ml_direction.direction_label).
                self.assertIn(trade["predicted_direction"],
                              ("STRONG_TARGET_FIRST", "TARGET_FIRST",
                               "STOP_FIRST", "NEITHER"))

    def test_representative_rows_now_carry_race_claims_for_gate4(self):
        graded = [t for t in self.dataset["trades"]
                  if t.get("predicted_probability") is not None
                  and t.get("resolution_outcome") in ("WIN", "LOSS", "TIME")]
        by_class: dict[str, int] = {}
        for t in graded:
            by_class[t["trade_class"]] = by_class.get(t["trade_class"], 0) + 1
        self.assertGreaterEqual(by_class.get("clean_bullish", 0),
                                seed.EXPECTED_BUNDLES // 2,
                                "the representative set is not representative")
        self.assertEqual(by_class.get("regime_shock", 0), 6,
                         "the tail slice keeps all six of its rows")

    def test_a_bundle_reaching_past_t_rec_would_be_rejected_by_the_seeder(self):
        """The tool's own guard, not only this seam's reading of committed bytes."""
        future = {"date": "2099-01-01", "value": 1.0}
        self.assertRaises(seed.Blocked, seed.assert_point_in_time,
                          "T", "2022-04-11", [future])
        self.assertEqual(
            seed.assert_point_in_time("T", "2022-04-11",
                                      [{"date": "2021-05-20", "value": 1.0}]),
            None)


if __name__ == "__main__":
    unittest.main()
