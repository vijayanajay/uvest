#!/usr/bin/env python3
"""Seed the representative set's market-context bundles (signed option C).

Gates 4 and 5 grade engine output — a calibrated direction probability against
the tape's realized direction, a cone envelope against the tape's realized
forward range — and until now they had exactly six records to grade, all of
them crisis tapes chosen to trip a breaker. That denominator is the catalog's
design, not the model's distribution, which is why both gates read a
publication-red number that says nothing about the corpus the pools came from.

The owner signed option C on 2026-10-16
(`docs/agents/proposal-gate-thresholds.md`: Q1 keep the ceiling, Q2 keep the
floor, Q3 re-scope the *graded set* to representative + tail slice). This tool
runs the authoring half of that campaign:

1. **Author one `uvest-market-context/1` bundle per eligible row** — the
   roster's `clean_bullish` / `inverted_rr` rows that carry a proof row, a
   canonical trade and an OHLCV prefix — from the committed tier-1 index corpus
   (`tests/fixtures/history/indices_daily.json`, real yfinance / NSE
   `ind_close_all` closes), truncated to `[first session of the row's own
   prefix, T_rec]`. `origin: recorded`, `close → value` as the only
   transformation: gate 4's beta/correlation features must read real
   point-in-time market context, never an authored trajectory, because the
   number they feed is the number the gate grades (E4).
2. **Declare it** where stage 4 reads it (`replay_inputs.regime_fixture`) and
   what is absent says so: the corpus serves no NSE500 breadth or FII/DII
   net-flow series, so those keys are absent and the disclosure states a
   stage-7 run over this bundle would waive *naming* them — never an invented
   value.
3. **Never guess a sector.** An unmapped symbol (`SECTOR_INDEX_MAP`, ADR-010
   decision 5) gets no bundle and no proxy index; it is printed and left
   ungraded.
4. **Record the evidence** by handing the manifest to
   `record_stage4_evidence.py --apply`, which runs the real stage over each
   bundled row, declares the artifact and reads the promoted claims *out of the
   recorded bytes*.

Dry run by default; `--apply` writes, `--force` re-authors existing bundles.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent.parent
EVALS_DIR = TESTS_DIR / "evals"
REPO_ROOT = TESTS_DIR.parent
PROOF_ROOT = TESTS_DIR / "proof"
GOLDEN = TESTS_DIR / "evals" / "golden_dataset.json"
CALIBRATION = REPO_ROOT / "models" / "lgbm_direction_v1.calibration.json"
SCRIPTS_DIR = REPO_ROOT / "scripts"
for splice in (str(EVALS_DIR), str(TESTS_DIR)):
    if splice not in sys.path:
        sys.path.insert(0, splice)

import scaffold_catalog as sc      # noqa: E402  (proof-root bookkeeping)
import record_stage4_evidence as r4  # noqa: E402  (the evidence recorder)

# The signed roster: option C names the geometry advisories' clean and
# inverted-rr rows. Tight-stop is a geometry class too but is NOT added here —
# the signature authorizes these two, and a wider campaign is a wider signature.
REPRESENTATIVE_CLASSES = ("clean_bullish", "inverted_rr")
SEED_SOURCE = "seed_context_records/1"
CAMPAIGN = "gate-thresholds option C (docs/agents/proposal-gate-thresholds.md, signed 2026-10-16)"
REGIME_FILE = sc.REGIME_FILE          # "regime_context.json" — the frozen bundle name
REGIME_KEY = "regime_fixture"         # where stage 4 (and stage 7) read it
INDEX_CORPUS = REPO_ROOT / "tests" / "fixtures" / "history" / "indices_daily.json"
MARKET_TICKER = "^NSEI"
# The two aggregates stage 7 classifies that no committed source serves.
ABSENT_SERIES = ("NSE500_BREADTH", "FII_DII_NET")
# Stage 4's cross-asset window needs 20 returns ⇒ 21 common sessions.
MIN_COMMON_SESSIONS = 21
# The roster as signed: all 25 clean/inverted rows. It was 21 at signing —
# 2 rows sat behind a symbol with no pinned sector index (never a guessed one;
# ADR-010 decision 5) and 2 behind a forward tape running past the trained
# corpus window. The owner closed both on 2026-10-17: TITAN and BAJFINANCE are
# now pinned from NSE's own constituent lists (`scripts/_sector_map.py` note),
# and a forward tape outside the window is ruled to scope *gate 5's
# denominator* (evals.md §3 #5) rather than this bundle — stage 4 reads only
# ≤ T_rec sessions, so that evidence is point-in-time like every other row's,
# and the two out-of-window tapes stay named and excluded on gate 5's coverage
# line. Growth is fine (the tool prints it); shrinking is a lost row and fails
# the seam, which is the point of freezing the number here.
EXPECTED_BUNDLES = 25
# The promotion note `record_stage4_evidence` appends carries a count, so a
# second campaign run would otherwise stack contradictory totals in `meta.notes`.
# The count notes in this repo are rewritten in place, never accumulated
# (`seed_regime_records.regime_note` does the same for the census note).
PROMOTION_NOTE_PREFIX = "stage 4 (BRD §5.2"
NOTE_MARKER = "Representative-set context campaign"


class Blocked(Exception):
    """A condition that must stop the seed rather than write a bad bundle."""


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sector_map() -> dict[str, str]:
    """The shared symbol → NSE sectoral-index map (ADR-010 decision 5)."""
    spec = importlib.util.spec_from_file_location(
        "uvest_sector_map_context_seed", SCRIPTS_DIR / "_sector_map.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SECTOR_INDEX_MAP


def assert_point_in_time(label: str, as_of: str, points: list[dict]) -> None:
    """Every series point must sit at or before T_rec (V7's look-ahead scan).

    A bundle that reached past the recommendation would leak the very
    forward window the gate grades, so this raises rather than truncating
    silently.
    """
    for point in points:
        date = point.get("date")
        if not isinstance(date, str) or date > as_of:
            raise Blocked(f"{label}: series point {date!r} reaches past T_rec "
                          f"{as_of}")


def dataset_classes() -> dict[str, str]:
    data = read_json(GOLDEN)
    return {t["trade_id"]: t["trade_class"] for t in data.get("trades", [])}


def corpus_window() -> tuple[str, str] | None:
    """`(first, last)` of the trained corpus, as gate 5 scopes its denominator."""
    try:
        first, last = read_json(CALIBRATION)["provenance"]["corpus"]["row_window"][:2]
    except (OSError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return (str(first), str(last)) if first and last else None


def candidates(rows: list[dict]) -> list[dict]:
    """Every roster row that may be bundled, with what it needs to be one.

    A row without a proof row, a canonical trade or an OHLCV prefix cannot run
    stage 4 at all; a symbol outside `SECTOR_INDEX_MAP` would waive by
    construction and is *not* bundled (no guessed index, no proxy series).
    """
    classes = dataset_classes()
    sector = sector_map()
    roster = []
    for row in rows:
        sid = row.get("scenario_id")
        if classes.get(sid) not in REPRESENTATIVE_CLASSES:
            continue
        inputs = row.get("replay_inputs") or {}
        ticker = sector.get(str(row.get("symbol") or "").strip().upper())
        eligible = bool(inputs.get("canonical_trade")) and bool(
            inputs.get("truncated_fixtures"))
        roster.append({
            "scenario_id": sid,
            "row": row,
            "trade_class": classes.get(sid),
            "symbol": row.get("symbol"),
            "as_of": row.get("recommendation_date"),
            "sector": ticker,
            "eligible": eligible,
            "declared": inputs.get("regime_fixture"),
        })
    return roster


def bundle_doc(entry: dict, corpus: dict) -> dict:
    """One sourced `uvest-market-context/1` bundle for one roster row."""
    if not entry["eligible"]:
        raise Blocked(f"{entry['scenario_id']}: no canonical trade / OHLCV prefix")
    if entry["sector"] is None:
        raise Blocked(f"{entry['scenario_id']}: {entry['symbol']} has no pinned "
                      "sector index — never a guessed one (ADR-010 decision 5)")
    row = entry["row"]
    sid = entry["scenario_id"]
    as_of = entry["as_of"]
    prefix = read_json(PROOF_ROOT / row["replay_inputs"]["truncated_fixtures"][0])
    sessions = prefix.get("sessions") or []
    if not sessions:
        raise Blocked(f"{sid}: the row's OHLCV prefix carries no sessions")
    first = sessions[0]["date"]
    series: dict[str, dict] = {}
    sources: dict[str, str] = {}
    for ticker in (MARKET_TICKER, entry["sector"]):
        block = (corpus.get("tickers") or {}).get(ticker)
        if not isinstance(block, dict):
            raise Blocked(f"{sid}: the committed corpus does not serve {ticker}")
        points = [p for p in block.get("values") or []
                  if isinstance(p, dict) and first <= str(p.get("date")) <= as_of]
        if not points:
            raise Blocked(f"{sid}: no corpus session for {ticker} inside "
                          f"[{first}, {as_of}]")
        assert_point_in_time(f"{sid}/{ticker}", as_of, points)
        if len(points) < MIN_COMMON_SESSIONS:
            raise Blocked(f"{sid}: only {len(points)} session(s) for {ticker} — "
                          f"the cross-asset window needs {MIN_COMMON_SESSIONS}")
        series[ticker] = {
            "name": ticker,
            "unit": "index_close",
            # `close` → `value` is the schema's key, not a recomputation: the
            # number in the bundle is the corpus byte.
            "values": [{"date": p["date"], "value": p["close"]} for p in points],
        }
        sources[ticker] = str(block.get("source") or "unstated source")

    disclosure = (
        f"Sourced market-context input ({sid}, {entry['trade_class']}, as of "
        f"{as_of}): real index closes for {MARKET_TICKER} and "
        f"{entry['sector']} read out of the committed tier-1 corpus "
        f"tests/fixtures/history/indices_daily.json ({'; '.join(f'{k} from {v}' for k, v in sources.items())}), "
        f"truncated to the row's own point-in-time window "
        f"[{first} .. {as_of}] — `close` → `value` is the only transformation, "
        "no series is recomputed, smoothed or authored. The corpus serves no "
        "NSE500 breadth and no FII/DII net-flow series, so those keys are "
        "absent: a stage 7 run over this bundle would waive *naming* them "
        "(BRD §7.4), never invent one (E4: recorded or disclosed, never an "
        "invented source). Authored by tests/evals/seed_context_records.py "
        f"for the {CAMPAIGN}."
    )
    return {
        "schema": "uvest-market-context/1",
        "as_of": as_of,
        "provenance": {
            "origin": "recorded",
            "source": "tests/fixtures/history/indices_daily.json",
            "note": ("Real index closes from the committed tier-1 corpus "
                     "(built by tests/evals/build_index_corpus.py from "
                     "yahoo:yf.download and the NSE ind_close_all archive), "
                     "truncated to [first session of the row's own OHLCV "
                     "prefix, T_rec]. close → value is the only "
                     "transformation; breadth and FII/DII are absent from "
                     "that corpus and named as absent, never authored."),
        },
        "series": series,
        "authoring": {
            "source": SEED_SOURCE,
            "campaign": CAMPAIGN,
            "scenario_id": sid,
            "trade_class": entry["trade_class"],
            "sector_index": entry["sector"],
            "window": {"first": first, "last": as_of,
                       "sessions": len(sessions)},
            "corpus": {"path": "tests/fixtures/history/indices_daily.json",
                       "window": read_json(INDEX_CORPUS).get("window"),
                       "origin": "sourced"},
            "series_source": sources,
            "absent_series": list(ABSENT_SERIES),
            "disclosure": disclosure,
        },
    }


def campaign_note(mapped: list[dict], unmapped: list[dict],
                  out_of_window: list[str]) -> str:
    """The dataset's own disclosure of what this campaign added (and did not)."""
    unmapped_clause = (
        f"{len(unmapped)} row(s) left unbundled and ungraded because "
        "SECTOR_INDEX_MAP pins no sector index for their symbol ("
        + ", ".join(sorted(str(t["symbol"]) for t in unmapped))
        + " — never a guessed index, never a proxy series); "
        if unmapped else
        "0 row(s) left unmapped — every roster symbol now carries a pin read "
        "from NSE's own constituent list, never a guessed index; "
    )
    window_clause = (
        f"{len(out_of_window)} of them have a forward tape past the trained "
        "corpus window ("
        + ", ".join(sorted(out_of_window))
        + ") and are excluded from gate 5's denominator by that gate's frozen "
        "scope (evals.md §3 #5) while staying named there — stage 4's inputs "
        "stop at T_rec, so their cone, race probability and calibrated P(win) "
        "are recorded and graded like every other row's, and the window scopes "
        "only what the coverage gate counts. "
        if out_of_window else
        "every bundled row's forward tape also lies inside the trained corpus "
        "window, so gate 5's scope excludes none of them. "
    )
    return (
        f"{NOTE_MARKER} (signed {CAMPAIGN}; roster completed 2026-10-17): "
        f"{len(mapped)} market-context bundle(s) sourced from the committed "
        "tier-1 index corpus "
        "(tests/fixtures/history/indices_daily.json, origin: recorded, "
        "point-in-time ≤ T_rec) over every mapped "
        "clean_bullish/inverted_rr row, so eval dimensions 4 and 5 grade a "
        "corpus-representative slice instead of six crisis tapes while those "
        "six stay in the denominator as the disclosed tail slice; "
        + unmapped_clause + window_clause
        + "Breadth and FII/DII series are absent from the corpus and named "
        "absent in every bundle. Authored by "
        "tests/evals/seed_context_records.py."
    )


def is_ours(path: Path) -> bool:
    """True when a committed bundle was written by *this* tool.

    A stage-7 regime bundle (`seed_regime_records`) shares the file name and
    must never be retired by this campaign, so authorship is read from the
    bundle itself rather than assumed from the path.
    """
    try:
        return read_json(path).get("authoring", {}).get("source") == SEED_SOURCE
    except (OSError, json.JSONDecodeError, AttributeError):
        return False


def retire(entry: dict) -> None:
    """Undo this campaign's evidence for one row: bundle, declaration, claims.

    An out-of-window row is *not* half-graded: the bundle this tool authored is
    deleted, its `regime_fixture` declaration and stage-4 artifact declaration
    are dropped, the recorded `out_4_ml.json` is removed with them, and the
    dataset's four engine claims go back to `null` — the row's exact
    pre-campaign state, so the corpus-reach seam keeps its absolute. The
    per-bundle `recorded/manifest.json` keeps its line for the same reason
    every other recording does: `_recorder._append_manifest` is append-only by
    contract ("nothing is deleted"), so the ledger stays a true log of what
    was recorded while every *claim* drawn from it is withdrawn.
    """
    sid = entry["scenario_id"]
    row = entry["row"]
    path = PROOF_ROOT / "fixtures" / sid / REGIME_FILE
    if path.exists() and not is_ours(path):
        raise Blocked(f"{sid}: {REGIME_FILE} on disk is not this tool's — "
                      "refusing to retire another campaign's bundle")
    path.unlink(missing_ok=True)
    row["replay_inputs"].pop(REGIME_KEY, None)
    kept = [a for a in row.get("recorded_artifacts") or []
            if a.get("stage") != r4.STAGE]
    row["recorded_artifacts"] = kept
    (PROOF_ROOT / "fixtures" / sid / "recorded" / r4.ARTIFACT).unlink(
        missing_ok=True)
    entry["retired"] = True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seed the representative set's market-context bundles "
                    "(signed option C) and record the stage-4 evidence.")
    parser.add_argument("--apply", action="store_true",
                        help="Write bundles, declarations, dataset note and evidence.")
    parser.add_argument("--force", action="store_true",
                        help="Re-author bundles that already exist.")
    args = parser.parse_args(argv)

    manifest = sc.read_json(PROOF_ROOT / sc.MANIFEST_FILE)
    rows = manifest["scenarios"]
    roster = candidates(rows)
    if not roster:
        print("❌ no representative row found — the dataset's classes moved?",
              file=sys.stderr)
        return 2

    corpus = read_json(INDEX_CORPUS)
    sector = sector_map()
    window = corpus_window()
    if window is None:
        print("❌ the trained corpus window is unreadable — option C scopes its "
              "graded set to in-window tapes, so nothing may be authored "
              "against an unknown window", file=sys.stderr)
        return 2

    tapes: dict[str, tuple[str, str]] = {}
    for entry in roster:
        tape = (entry["row"].get("scoring") or {}).get("forward_tape")
        bars = read_json(PROOF_ROOT / tape)["bars"] if tape else []
        if bars:
            tapes[entry["scenario_id"]] = (bars[0]["date"], bars[-1]["date"])
        entry["span"] = tapes.get(entry["scenario_id"])
        entry["in_window"] = bool(
            entry["span"] and window[0] <= entry["span"][0]
            and entry["span"][1] <= window[1])

    def unmapped(entry: dict) -> bool:
        return not (entry["eligible"]
                    and sector.get(str(entry["symbol"]).upper()))

    no_tape = [e for e in roster if not e["span"]]
    unmapped_rows = [e for e in roster if e["span"] and unmapped(e)]
    out_of_window = [e for e in roster
                     if e["span"] and not unmapped(e) and not e["in_window"]]
    # Owner rule, 2026-10-17: an out-of-window forward tape scopes gate 5's
    # denominator (§3 #5), not this bundle — stage 4's own inputs stop at
    # T_rec, so those two rows bundle point-in-time like the rest, and gate 5
    # still names and excludes their tapes. `retire()` therefore runs for the
    # unmapped / tape-less rows only.
    mapped = [e for e in roster if e["span"] and not unmapped(e)]
    excluded = unmapped_rows + no_tape

    print("=" * 72)
    print(f"REPRESENTATIVE-SET CONTEXT PLAN — {CAMPAIGN}")
    print("=" * 72)
    for entry in roster:
        if entry in mapped:
            mark = ("bundle (out-of-window tape — gate 5's scope keeps its own "
                    "denominator)" if entry in out_of_window else "bundle")
        elif entry in unmapped_rows:
            mark = "SKIP (unmapped symbol — never a guessed index)"
        elif entry in out_of_window:
            mark = "SKIP (tape outside the trained corpus window)"
        else:
            mark = "SKIP (no pinned forward tape)"
        span = entry["span"]
        scope = "" if span is None else f" tape {span[0]}..{span[1]}"
        print(f"  {entry['scenario_id']:12} {entry['trade_class']:14} "
              f"{entry['symbol']:11} @ {entry['as_of']} -> "
              f"{entry['sector'] or '-':12} {mark}{scope}")
    print(f"\n  window {window[0]}..{window[1]}: {len(mapped)} bundle(s) to "
          f"author, {len(unmapped_rows)} unmapped, {len(out_of_window)} "
          f"out-of-window, {len(no_tape)} without a tape")

    if len(mapped) != EXPECTED_BUNDLES:
        print(f"  ⚠ expected {EXPECTED_BUNDLES} bundle(s) (the frozen roster), "
              f"found {len(mapped)} — the seam freezes that number; re-freeze "
              "it deliberately if the roster really moved", file=sys.stderr)

    if not args.apply:
        print(f"\nDRY RUN — nothing written; --apply authors {len(mapped)} "
              "bundle(s) and records the stage-4 evidence")
        return 0

    written = []
    for entry in mapped:
        sid = entry["scenario_id"]
        path = PROOF_ROOT / "fixtures" / sid / REGIME_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = bundle_doc(entry, corpus)
        if path.exists() and not args.force:
            state = "existing bundle kept"
        else:
            sc.write_json(path, doc)
            state = "authored"
        entry["row"]["replay_inputs"][REGIME_KEY] = f"fixtures/{sid}/{REGIME_FILE}"
        written.append(sid)
        print(f"  {sid}: {REGIME_FILE} {state} "
              f"({len(doc['series'][MARKET_TICKER]['values'])} market session(s))")

    retired = []
    for entry in excluded:
        sid = entry["scenario_id"]
        declared = (entry["row"].get("replay_inputs") or {}).get(REGIME_KEY)
        path = PROOF_ROOT / "fixtures" / sid / REGIME_FILE
        if not declared and not path.exists():
            continue
        reason = (f"{entry['symbol']} has no pinned sector index"
                  if not sector.get(str(entry["symbol"]).strip().upper())
                  else "no canonical trade / OHLCV prefix or no pinned tape")
        retire(entry)
        retired.append(sid)
        print(f"  {sid}: evidence retired (bundle, declaration, recorded "
              f"{r4.ARTIFACT}) — {reason}")

    data = read_json(GOLDEN)
    meta = data.setdefault("meta", {})
    notes = meta.setdefault("notes", [])
    if retired:
        by_id = {t["trade_id"]: t for t in data.get("trades", [])}
        for sid in retired:
            trade = by_id.get(sid)
            if trade is None:
                continue
            trade["script_outputs"].pop(r4.ARTIFACT, None)
            for field in r4.PROMOTED_FIELDS:
                trade[field] = None
    # One campaign note and one stage-4 promotion note: counts are rewritten in
    # place, never accumulated (the census note is rewritten the same way).
    meta["notes"] = [n for n in notes
                     if NOTE_MARKER not in n
                     and not n.startswith(PROMOTION_NOTE_PREFIX)]
    meta["notes"].append(campaign_note(mapped, unmapped_rows,
                                       [e["scenario_id"] for e in out_of_window]))
    sc.write_json(PROOF_ROOT / sc.MANIFEST_FILE, manifest)
    sc.write_json(GOLDEN, data)

    print("\nrecording stage-4 evidence over the bundled rows…")
    rc = r4.main(["--apply"])
    if rc != 0:
        print(f"❌ record_stage4_evidence exited {rc}", file=sys.stderr)
        return rc
    print(f"\n{len(written)} bundle(s) declared ({len(retired)} retired), "
          "stage-4 evidence recorded, hashes and baseline re-frozen by the "
          "recorder")
    return 0


if __name__ == "__main__":
    sys.exit(main())
