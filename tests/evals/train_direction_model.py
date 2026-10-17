#!/usr/bin/env python3
"""Train the shipped stage-4 booster from the sourced NSE history corpus.

    python tests/evals/train_direction_model.py            # train, report, write
    python tests/evals/train_direction_model.py --cv-only  # report only, write nothing

Corpus (committed and sourced — this tool never touches the network):

* `tests/fixtures/history/<SYMBOL>_<YYYYMM>.json` — real yfinance daily bars,
  one fetch per symbol-month (the same tape the golden dataset's prices come
  from), merged across vintages and deduped by `(symbol, date)`. Each session's
  `high`/`low` are kept, not only its close: the cone's excursion atoms are the
  forward bars' extremes, the realization the coverage gate grades (BRD §5.2).
* `tests/fixtures/history/indices_daily.json` — real yfinance daily closes for
  `^NSEI`, `^INDIAVIX` and the sectoral indices the source actually serves
  (built by `tests/evals/build_index_corpus.py`, which discloses the ones it
  does not). Rows are built only for symbols whose sector index is served, so
  the trained universe is a *disclosed* subset of the F&O universe rather than
  a silently narrower one.

Rows are point-in-time: the market and sector series handed to row `i` are
truncated at that row's session, exactly as stage 4 truncates the live bundle at
`T_rec`, and features are computed by importing `scripts/4_ml_direction.py`'s own
`feature_vector` — one implementation, so a feature cannot mean one thing in
training and another at inference.

**A label window is a month of real market time.** The vintages are not
consecutive (SBIN and RELIANCE jump from `202003` to `202204`), so a 22-session
window read off the merged series can span a gap of fourteen months. Every row
is therefore labeled only when **one** vintage contains its own session and all
`HORIZON` sessions after it (`label_window_dates`); the rest are dropped and
counted in `provenance.corpus.label_window`.

**A feature history is too.** The features read the closes *before* the row —
the 200-session SMA, the 61-session volatility window, and `ema20_slope_5`,
which recurses over the whole prefix — and `common_return_windows` takes the
last 21 common cross-asset sessions off the same prefix. A row just after a
fetch hole would therefore feed a fourteen-month price jump to its volatility
and beta as if it were one session. The vintages overlap (they are trailing
~12-month windows fetched months apart), so consecutive fetches that share
calendar ground chain into one **gapless run** — continuous market time, whose
sessions a history may read across; but a hole no fetch covers ends a run, and
a row whose `MIN_SESSIONS - 1`-session history reaches across one is dropped and
counted in `provenance.corpus.feature_window`, its prefix cut at the run's own
first session (`feature_window_dates`).

**Validation: purged group time-series cross-validation (BRD §5.2).** The unique
row sessions are cut into `FOLDS` contiguous chronological blocks; each block is
the test fold once, and a training row survives only if its 22-session forward
label window cannot reach the test block *on its own symbol's session calendar*
(the group half — every symbol is purged against its own sessions, so a symbol
with a different holiday calendar cannot smuggle a near row in) and it sits at
least `EMBARGO_SESSIONS` sessions after the block (the 5-day embargo BRD §5.2
names, for the autocorrelation of adjacent sessions). Because two rows of one
symbol share a sector series and neighbouring dates, an unpurged scheme would
have the label of one row inside the features of another; `tests/test_train_direction_model.py`
proves the split has no such pair.

The reported metrics are the *out-of-fold* ones — a fold's rows are scored by a
booster that never saw them — so the calibrated Brier score and the cone
coverage in the artifact's `provenance.cv` block are measured, not asserted.
Hyper-parameters are frozen constants and were not selected on these folds.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

# Windows consoles default to a legacy codepage; both streams must be UTF-8
# (repo convention — the same reconfigure every stage script performs).
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", line_buffering=True)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
CORPUS_DIR = REPO_ROOT / "tests" / "fixtures" / "history"
INDEX_CORPUS = CORPUS_DIR / "indices_daily.json"
MODEL_DIR = REPO_ROOT / "models"
MODEL_NAME = "lgbm_direction_v1"

INDEX_CORPUS_SCHEMA = "uvest-index-corpus/1"

FOLDS = 5
EMBARGO_SESSIONS = 5
# Frozen, disclosed and not tuned on the folds: a small forest for ~7k rows.
MODEL_PARAMS = {
    "objective": "multiclass",
    "num_class": 3,
    "learning_rate": 0.05,
    "num_leaves": 15,
    "min_data_in_leaf": 50,
    "feature_fraction": 0.9,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "verbose": -1,
    "deterministic": True,
    "force_row_wise": True,
    "num_threads": 1,
    "seed": 42,
}
NUM_BOOST_ROUND = 200

# What the shipped `prob_target_hit_22d` *is*, carried in the artifact rather
# than only in prose: decision D's graded estimator, disclosed as a lower bound.
RACE_ESTIMATOR_NOTE = (
    "prob_target_hit_22d = P(best high >= target) x P(worst low > -stop), "
    "class-weighted over the excursion pools (stage4.race_probability). The "
    "product of two positively-associated marginals is at most their joint, so "
    "it understates the first-touch race and is never tuned upward — a "
    "disclosed lower bound (decision D, 2026-10-16)."
)


def load_module(name: str, filename: str):
    """Import a script whose filename is not a valid module name."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_stage4():
    return load_module("uvest_stage4_ml_direction", "4_ml_direction.py")


def load_sector_map():
    return load_module("uvest_sector_map", "_sector_map.py").SECTOR_INDEX_MAP


# --------------------------------------------------------------------------
# Corpus
# --------------------------------------------------------------------------
def load_stock_corpus(corpus_dir: Path) -> tuple[dict[str, list[dict]], dict]:
    """`{symbol: [{date, high, low, close}]}` merged across vintages, deduped.

    The bars, not just the closes: the cone's excursion atoms are the forward
    sessions' lows and highs (BRD §5.2, excursion realization), so a session is
    kept only when it carries a usable high and low — a close-only row would
    silently narrow the pool it feeds. Sessions that are incomplete or inverted
    are counted and disclosed, never coerced into a close-based excursion.

    A date that two vintages disagree on is kept once (first file wins, in
    sorted filename order) and counted, so a corpus with divergent vintages is
    disclosed rather than silently averaged.

    The returned meta also carries `vintages`: for each symbol, the *usable
    session dates of each vintage separately*, ordered by first date. The
    merged series cannot say where one vintage ends and the next begins, and
    that is exactly what a forward label window has to be checked against —
    the committed vintages are not consecutive (SBIN jumps from `202003` to
    `202204`), so a window taken from the merged series can silently cover a
    year of missing sessions (`label_window_dates`).
    """
    merged: dict[str, dict[str, dict]] = {}
    files: dict[str, int] = {}
    vintages: dict[str, list[list[str]]] = {}
    clashes: list[dict] = []
    incomplete = 0
    for path in sorted(corpus_dir.glob("*.json")):
        if path.name == INDEX_CORPUS.name:
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        symbol = doc.get("symbol")
        sessions = doc.get("sessions")
        if not isinstance(symbol, str) or not isinstance(sessions, list):
            continue
        series = merged.setdefault(symbol, {})
        files[symbol] = files.get(symbol, 0) + 1
        vintage_dates: list[str] = []
        for row in sessions:
            try:
                session = {"date": str(row["date"]),
                           "high": float(row["high"]),
                           "low": float(row["low"]),
                           "close": float(row["close"])}
            except (KeyError, TypeError, ValueError):
                incomplete += 1
                continue
            if session["high"] < session["low"] or session["low"] <= 0.0:
                incomplete += 1
                continue
            vintage_dates.append(session["date"])
            previous = series.get(session["date"])
            if previous is not None and abs(previous["close"] - session["close"]) > 1e-9:
                clashes.append({"symbol": symbol, "date": session["date"],
                                "kept": previous["close"],
                                "ignored": session["close"], "file": path.name})
            else:
                series.setdefault(session["date"], session)
        if vintage_dates:
            vintages.setdefault(symbol, []).append(sorted(set(vintage_dates)))
    ordered = {symbol: [series[day] for day in sorted(series)]
               for symbol, series in merged.items()}
    for symbol in vintages:
        vintages[symbol].sort(key=lambda dates: dates[0])
    return ordered, {"files": files, "clashes": clashes,
                     "incomplete_sessions": incomplete,
                     "vintages": vintages}


def load_index_corpus(corpus_dir: Path) -> dict[str, list[tuple[str, float]]]:
    """The served index series, as `{ticker: [(date, close)]}`."""
    path = corpus_dir / INDEX_CORPUS.name
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("schema") != INDEX_CORPUS_SCHEMA:
        raise SystemExit(f"{path.name}: schema is {doc.get('schema')!r}, "
                         f"expected {INDEX_CORPUS_SCHEMA!r}")
    out: dict[str, list[tuple[str, float]]] = {}
    for ticker, entry in (doc.get("tickers") or {}).items():
        out[ticker] = [(str(v["date"]), float(v["close"]))
                       for v in entry.get("values") or []]
    return out


def label_window_dates(vintage_dates: list[list[str]] | None, merged: list[str],
                       horizon: int) -> set[str]:
    """Row dates whose whole `horizon`-session label window one vintage covers.

    The committed corpus is a set of *vintages* — one fetch per symbol-month,
    each a continuous trailing window — and they are not consecutive: the SBIN
    and RELIANCE vintages run `202003` and then `202204`, so the merged series
    steps from 2020-03-31 to 2021-05-03. Taking the forward window off the
    merged series therefore labels a row on the near side of that jump with the
    excursions of sessions fourteen months later, and calls the result a
    22-session move (measured before this rule: 8 such rows out of 6,294, and
    they are exactly the rows whose excursions are the largest).

    A date qualifies only when **one** vintage contains it *and* the `horizon`
    sessions after it, so no window can straddle two fetches however the merged
    series happens to line up. A date at position `p` of a vintage with `n`
    usable sessions has its full window inside that vintage iff
    `p + horizon <= n - 1`.

    `None` means the caller has no vintage provenance: the series is then one
    continuous fetch by construction (the in-memory corpora the seams build),
    so only its own tail can invalidate a window.
    """
    if vintage_dates is None:
        return set(merged[: max(0, len(merged) - horizon)])
    covered: set[str] = set()
    for dates in vintage_dates:
        covered.update(dates[: max(0, len(dates) - horizon)])
    return covered


def vintage_runs(vintage_dates: list[list[str]] | None,
                 merged: list[str]) -> list[tuple[str, str]]:
    """The merged series' gapless runs, as `(first_date, last_date)` spans.

    A run is a maximal chain of overlapping vintages: the fetches share
    calendar ground, so their union on the merged series is continuous market
    time and a window may read across a fetch boundary inside it. A run ends
    where the next vintage begins *after* it — a calendar gap no fetch covers,
    whose sessions the merged corpus simply does not have (SBIN's and
    RELIANCE's vintages jump 385 days). Chaining compares only first/last
    session dates (ISO dates sort chronologically), and a corpus without
    vintage provenance is one run by construction.
    """
    if not merged:
        return []
    if not vintage_dates:
        return [(merged[0], merged[-1])]
    runs: list[list[str]] = []
    for dates in vintage_dates:
        if runs and dates[0] <= runs[-1][1]:
            runs[-1][1] = max(runs[-1][1], dates[-1])
        else:
            runs.append([dates[0], dates[-1]])
    return [(first, last) for first, last in runs]


def feature_window_dates(vintage_dates: list[list[str]] | None,
                         merged: list[str], lookback: int) -> dict[str, int]:
    """`{row_date: index of its run's first session}` for rows whose feature
    history (`lookback` sessions before the row) stays inside one gapless run
    (`vintage_runs`).

    The value is the slice the features must be computed from — the run's own
    sessions — so the prefix handed to `feature_vector` cannot start on the far
    side of a hole even when the merged series does carry earlier sessions
    (`ema20_slope_5` recurses over the whole prefix, so the bound is the
    prefix, not just the windowed features). `None` means the caller has no
    vintage provenance: one continuous fetch by construction, so only its own
    head can invalidate a history.
    """
    positions = {day: index for index, day in enumerate(merged)}
    allowed: dict[str, int] = {}
    for first, last in vintage_runs(vintage_dates, merged):
        start = positions[first]
        for index in range(start, positions[last] + 1):
            if index - start >= lookback:
                allowed[merged[index]] = start
    return allowed


# --------------------------------------------------------------------------
# Rows
# --------------------------------------------------------------------------
def build_rows(stage4, sector_map: dict, stocks: dict,
               indices: dict,
               vintages: dict[str, list[list[str]]] | None = None,
               ) -> tuple[list[dict], dict]:
    """Point-in-time feature rows with a full 22-session forward label.

    Features read the closes (the index panel is close-only, so one feature
    implementation reads one realization at inference and in training); the
    label's excursions read the sessions' lows and highs, which is the path the
    coverage gate grades.

    A row is built only when `vintages` says one committed vintage contains its
    own session and every session of its forward label window
    (`label_window_dates`) — and one gapless run of the merged series contains
    its whole `MIN_SESSIONS - 1`-session feature history
    (`feature_window_dates`): the vintages have gaps, and a window or a history
    taken off the merged series can otherwise span one.

    Returns `(rows, disclosure)` — the disclosure names every corpus symbol that
    could not be built, with the reason, every row dropped because its label
    window left its vintage, and every row dropped because its feature history
    crossed a hole between runs, so the trained universe's limits are printed by
    the tool that trained it.
    """
    rows: list[dict] = []
    skipped: dict[str, str] = {}
    dropped_windows: dict[str, int] = {}
    dropped_histories: dict[str, int] = {}
    market = dict(indices.get("^NSEI") or [])
    for symbol in sorted(stocks):
        sector_ticker = sector_map.get(symbol)
        if sector_ticker is None:
            skipped[symbol] = "no sector index in the shared SECTOR_INDEX_MAP"
            continue
        series = stocks[symbol]
        if not series:
            skipped[symbol] = "no sessions in the corpus"
            continue
        dates = [session["date"] for session in series]
        closes = [session["close"] for session in series]
        if len(closes) < stage4.MIN_SESSIONS + stage4.HORIZON:
            skipped[symbol] = (f"{len(closes)} session(s) — below the "
                               f"{stage4.MIN_SESSIONS}-session feature floor plus "
                               f"a {stage4.HORIZON}-session label")
            continue
        if sector_ticker not in indices:
            served = ", ".join(sorted(indices))
            skipped[symbol] = (f"sector index {sector_ticker} is not served by the "
                               f"tier-1 source (served: {served})")
            continue
        sector = dict(indices[sector_ticker])
        allowed = label_window_dates((vintages or {}).get(symbol), dates,
                                     stage4.HORIZON)
        histories = feature_window_dates((vintages or {}).get(symbol), dates,
                                         stage4.MIN_SESSIONS - 1)
        built = 0
        for index, day in enumerate(dates):
            if index + 1 < stage4.MIN_SESSIONS:
                continue
            if index + stage4.HORIZON >= len(closes):
                break
            if day not in allowed:
                # The window would leave the vintage this row's own session
                # came from — across a gap the merged series cannot show.
                dropped_windows[symbol] = dropped_windows.get(symbol, 0) + 1
                continue
            run_start = histories.get(day)
            if run_start is None:
                # The 200-session feature history reaches across a hole between
                # runs — real market time the corpus does not have. Dropped and
                # counted, never bridged.
                dropped_histories[symbol] = dropped_histories.get(symbol, 0) + 1
                continue
            if day not in market or day not in sector:
                # A stale cross-asset window — the index has no session of its
                # own on this date — would stamp the row's features with another
                # session's market state. Skipped rather than blurred.
                continue
            # The features read the run's own sessions, cut at its first: the
            # prefix `feature_vector` recurses over must not see the far side
            # of a hole either.
            history = series[run_start: index + 1]
            windows = stage4.common_return_windows(
                {session["date"]: session["close"] for session in history},
                {d: v for d, v in market.items() if d <= day},
                {d: v for d, v in sector.items() if d <= day},
            )
            if windows is None:
                continue
            try:
                vector = stage4.feature_vector(
                    [session["close"] for session in history], *windows)
                # The label's excursions are the forward *bars*, not the closes
                # (BRD §5.2): one implementation, shared with inference.
                forward_return, mae, mfe = stage4.forward_outcome(series, index)
                # ...and the close-path reading of the same window is carried
                # beside it, purely so the CV block can report what the
                # realization choice was worth. It never feeds a pool.
                _ret, close_mae, close_mfe = stage4.close_path_outcome(series, index)
            except (ValueError, KeyError, IndexError, ZeroDivisionError):
                continue
            # Decision D: the label is the reference-band race — which ±3% level
            # the forward window printed first — scanned by stage 4's own
            # `first_touch_outcome`, the same rule the benchmark's recorded
            # `resolution_outcome` is derived by.
            label = stage4.classify_outcome(
                float(series[index]["close"]),
                series[index + 1:index + 1 + stage4.HORIZON])
            rows.append({
                "symbol": symbol,
                "date": day,
                "sector_index": sector_ticker,
                "features": [vector[name] for name in stage4.FEATURES],
                "class": label,
                "forward_return_pct": forward_return,
                "mae_pct": mae,
                "mfe_pct": mfe,
                "close_mae_pct": close_mae,
                "close_mfe_pct": close_mfe,
            })
            built += 1
        if built == 0:
            if dropped_windows.get(symbol):
                skipped[symbol] = (
                    f"every candidate session's {stage4.HORIZON}-session label "
                    "window left its own vintage (a gap between vintages "
                    "invalidates the window)")
            elif dropped_histories.get(symbol):
                skipped[symbol] = (
                    f"every candidate session's {stage4.MIN_SESSIONS}-session "
                    "feature history crossed a hole between vintage runs (a gap "
                    "no fetch covers invalidates the history)")
            else:
                skipped[symbol] = (f"no session had {stage4.MIN_SESSIONS} sessions of "
                                   "stock history and 21 common cross-asset sessions")
    return rows, {"skipped_symbols": skipped,
                  "label_windows_dropped": dict(sorted(dropped_windows.items())),
                  "feature_windows_dropped": dict(sorted(dropped_histories.items()))}


# --------------------------------------------------------------------------
# Purged group time-series CV
# --------------------------------------------------------------------------
def symbol_ranks(rows: list[dict]) -> dict[str, dict[str, int]]:
    """Each symbol's own session calendar → `{date: rank}` (the group axis)."""
    per_symbol: dict[str, list[str]] = {}
    for row in rows:
        per_symbol.setdefault(row["symbol"], []).append(row["date"])
    return {symbol: {day: rank for rank, day in enumerate(sorted(set(days)))}
            for symbol, days in per_symbol.items()}


def fold_spans(rows: list[dict], folds: int) -> list[tuple[str, str]]:
    """`folds` contiguous chronological blocks of the unique row sessions."""
    dates = sorted({row["date"] for row in rows})
    spans: list[tuple[str, str]] = []
    for fold in range(folds):
        start = fold * len(dates) // folds
        stop = (fold + 1) * len(dates) // folds
        if start == stop:
            continue
        spans.append((dates[start], dates[stop - 1]))
    return spans


def split_fold(rows: list[dict], ranks: dict, span: tuple[str, str], *,
               horizon: int, embargo: int) -> dict:
    """One fold's test/train rows under the purged group scheme.

    The fold is a **date span** `[low, high]`, and each symbol is purged against
    that span *on its own session calendar* — which is what makes the group axis
    matter. A row of symbol `s` at rank `r` is:

    * **test** — its date lies inside the span;
    * **purged** — it is before the span and its `horizon`-session label window
      would reach rank `before(s)` or beyond (the first of `s`'s sessions that is
      not earlier than `low`), or it is after the span and within `embargo`
      sessions of it;
    * **train** — everything else.

    Purging against the span rather than against the sessions `s` happens to
    have *inside* it is not a detail: a symbol that did not trade on the fold's
    dates has no session there, and a rule keyed on those sessions would keep
    the row directly before the span even though its label window covers the
    whole fold.
    """
    low, high = span
    before = {symbol: sum(1 for day in calendar if day < low)
              for symbol, calendar in ranks.items()}
    through = {symbol: sum(1 for day in calendar if day <= high)
               for symbol, calendar in ranks.items()}

    test: list[dict] = []
    train: list[dict] = []
    purged: list[dict] = []
    for row in rows:
        if low <= row["date"] <= high:
            test.append(row)
            continue
        symbol = row["symbol"]
        rank = ranks[symbol][row["date"]]
        if rank < before[symbol]:
            keep = rank + horizon < before[symbol]
        else:
            keep = rank >= through[symbol] + embargo
        (train if keep else purged).append(row)
    return {"span": (low, high), "test": test, "train": train, "purged": purged}


def calibration_samples(rows: list[dict],
                        classes: tuple[str, ...] | None = None) -> dict[str, dict[str, list[float]]]:
    """The artifact's `samples` block: measure → class → observed values.

    This is the shape stage 4's inference reads (`samples["forward_return_pct"]`
    for the cone, `mae_pct`/`mfe_pct` for the projected excursions and for the
    race probability), so the trainer writes exactly what the engine consumes.
    The class set is stage 4's own unless the caller already holds it.
    """
    classes = tuple(classes or load_stage4().CLASSES)
    pools = {
        measure: {cls: [] for cls in classes}
        for measure in ("forward_return_pct", "mae_pct", "mfe_pct")
    }
    for row in rows:
        for measure in pools:
            pools[measure][row["class"]].append(round(row[measure], 6))
    return pools


def _nearest_rank(ordered: list[float], level: float) -> float | None:
    """The `level`-quantile of a sorted pool, nearest-rank (no interpolation)."""
    if not ordered:
        return None
    index = max(0, min(len(ordered) - 1, int(level * len(ordered)) - 1))
    return round(ordered[index], 4)


def block_disclosure(block: dict) -> dict:
    """Atoms and tails of one pools block — the numbers that say how fat it is.

    Published per sector in `provenance.corpus.sector_blocks` so the question
    the coverage gate keeps raising — would a wider envelope contain the graded
    tapes? — is answerable against the artifact instead of argued: a realised
    excursion is inside this block's tails or outside them, and how far outside
    is a percentile read off exactly these atoms. A block whose `P10` is
    `mae_pct.p10` cannot contain a low deeper than that, however the mixture is
    weighted.
    """
    adverse = sorted(v for values in block["mae_pct"].values() for v in values)
    favourable = sorted(v for values in block["mfe_pct"].values() for v in values)
    return {
        "atoms": {measure: sum(len(values) for values in classes.values())
                  for measure, classes in block.items()},
        "mae_pct": {"p01": _nearest_rank(adverse, 0.01),
                    "p10": _nearest_rank(adverse, 0.10)},
        "mfe_pct": {"p90": _nearest_rank(favourable, 0.90),
                    "p99": _nearest_rank(favourable, 0.99)},
    }


def calibration_samples_by_sector(rows: list[dict],
                                  classes: tuple[str, ...] | None = None) -> dict[str, dict]:
    """The same pools, split by the sector index each row trades under.

    The cone's levels are quantiles of observed excursions, so the block they
    come from decides the envelope; pooling every sector answers "how far does
    an average NSE name travel" while the coverage gate grades one trade. Stage
    4 selects a block with its own `samples_for` (`MIN_SECTOR_ATOMS_PER_CLASS`),
    so this reader and that selector cannot drift apart, and the pooled block
    stays in the artifact as the fallback for a sector too thin to answer.
    """
    classes = tuple(classes or load_stage4().CLASSES)
    pools: dict[str, dict[str, dict[str, list[float]]]] = {}
    for row in rows:
        sector = row["sector_index"]
        block = pools.setdefault(sector, {
            measure: {cls: [] for cls in classes}
            for measure in ("forward_return_pct", "mae_pct", "mfe_pct")})
        for measure in block:
            block[measure][row["class"]].append(round(row[measure], 6))
    return {sector: pools[sector] for sector in sorted(pools)}


def _race_probability(stage4, weights: dict, sample_sets: dict) -> float:
    """The reference-band race probability the out-of-fold Brier grades.

    Decision D (2026-10-16): the graded event is the target-versus-stop race,
    and the corpus's only common geometry is the reference band the labels were
    decided at — so the OOF number is the *same estimator* the shipped payload
    uses (`stage4.race_probability`) evaluated at ±`RACE_BAND_PCT` against each
    row's own `TARGET_FIRST` label, read off the fold's training pools through
    `samples_for` (the shipped selector, exactly as the cone's CV does).
    """
    band = stage4.RACE_BAND_PCT
    return stage4.race_probability(weights, sample_sets, band, band)


def cross_validate(stage4, rows: list[dict], *, folds: int, embargo: int,
                   lightgbm, np) -> tuple[dict, list[dict]]:
    """Out-of-fold metrics + the per-fold split record (the seam's raw material)."""
    ranks = symbol_ranks(rows)
    classes = tuple(stage4.CLASSES)
    report = {
        "scheme": "purged group time-series",
        "folds": folds,
        "embargo_sessions": embargo,
        "horizon_sessions": stage4.HORIZON,
        # The event decision D moved evals dimension 4 onto, and the geometry
        # the corpus can state it at (the rows carry no target and no stop).
        "graded_event": (
            "the target-versus-stop race at the reference band: 1 iff the "
            f"{stage4.HORIZON}-session forward window prints +"
            f"{stage4.RACE_BAND_PCT}% before -{stage4.RACE_BAND_PCT}% from the "
            "entry (first touch wins, one bar carrying both resolves to the "
            "stop — stage4.first_touch_outcome)"),
        "race_band_pct": stage4.RACE_BAND_PCT,
        "brier_note": (
            "oof_brier_race is stage4.race_probability — the conservative "
            "product of the two excursion marginals — evaluated at the "
            "reference band off this fold's training pools through "
            "stage4.samples_for, against the row's own race label; "
            "oof_brier_race_base_rate is the constant-predictor floor at the "
            "corpus's own race-win share. The shipped payload's "
            "prob_target_hit_22d is the same estimator at each trade's own "
            "target and stop"),
        "cone_definition": ("path envelope on the session extremes: p10/p25 from "
                            "the adverse-excursion mixture (worst low), p50 from "
                            "terminal returns, p75/p90 from the favourable-"
                            "excursion mixture (best high) (stage4.CONE_SPEC)"),
        # Which object the coverage figures below covered. A close path and a bar
        # path give different answers for one envelope, so the number is not
        # reportable without it (BRD §5.2, excursion realization).
        "excursion_realization": ("session extremes (low/high) against the entry "
                                  "close"),
        "coverage_note": ("oof_cone_coverage_pct grades the shipped envelope "
                          "against the bar path (the realization the coverage "
                          "gate reads), oof_cone_pooled_coverage_pct grades the "
                          "pooled block's envelope over the same rows (the cost "
                          "of the sector conditioning, published not tuned), "
                          "oof_cone_close_path_coverage_pct against "
                          "the close path of the same windows, and "
                          "oof_cone_terminal_coverage_pct against the closing "
                          "session alone"),
        "purge_rule": ("per symbol, on its own session calendar: a training row is "
                       "dropped when its forward label window would reach the "
                       "fold's date span, or when it sits inside the embargo tail"),
        # Which observations the measured envelope's levels were read from. A
        # pooled block and a sector block are different cones for one row, so
        # the coverage figure is not reportable without it.
        "pool_conditioning": ("the trade's own sector's block when every class of "
                              "every measure holds >= stage4."
                              "MIN_SECTOR_ATOMS_PER_CLASS atoms, else the pooled "
                              "block (stage4.samples_for — the shipped selector)"),
        "per_fold": [],
    }
    oof: list[dict] = []
    splits: list[dict] = []
    sector_rows = 0
    for index, span in enumerate(fold_spans(rows, folds)):
        split = split_fold(rows, ranks, span, horizon=stage4.HORIZON,
                           embargo=embargo)
        splits.append({"fold": index, "span": list(span),
                       "test": len(split["test"]), "train": len(split["train"]),
                       "purged": len(split["purged"])})
        train = split["train"]
        if not split["test"] or not train:
            continue
        dataset = lightgbm.Dataset(
            np.asarray([r["features"] for r in train], dtype=float),
            label=np.asarray([classes.index(r["class"]) for r in train], dtype=int),
            feature_name=list(stage4.FEATURES), free_raw_data=False)
        booster = lightgbm.train(MODEL_PARAMS, dataset,
                                 num_boost_round=NUM_BOOST_ROUND)
        probabilities = booster.predict(
            np.asarray([r["features"] for r in split["test"]], dtype=float))
        pools = calibration_samples(train, classes)
        pools_by_sector = calibration_samples_by_sector(train, classes)
        majority = Counter(r["class"] for r in train).most_common(1)[0][0]
        brier_race = multiclass = 0.0
        covered = close_covered = terminal_covered = pooled_covered = correct = 0
        for row, probs in zip(split["test"], probabilities):
            probs = [float(p) for p in probs]
            multiclass += sum((probs[classes.index(cls)] - (1.0 if row["class"] == cls else 0.0)) ** 2
                              for cls in classes)
            correct += 1 if classes[int(max(range(len(probs)), key=probs.__getitem__))] == row["class"] else 0
            # The measured envelope is the *shipped* one — both the mixture
            # (`cone_levels_pct`) and the pools it reads (`samples_for`) are the
            # stage's own functions, so a change to either moves this metric
            # instead of leaving a stale one behind.
            sample_sets = stage4.samples_for(
                {"samples": pools, "samples_by_sector": pools_by_sector},
                row["sector_index"])
            if sample_sets is not pools:
                sector_rows += 1
            weights = dict(zip(classes, probs))
            # Decision D's graded event, measured out of fold: the same
            # conservative estimator the payload ships (`stage4.race_probability`)
            # at the reference band the labels were decided at, against the
            # row's own first-touch label — never a direction proxy.
            race = _race_probability(stage4, weights, sample_sets)
            truth = 1 if row["class"] == "TARGET_FIRST" else 0
            brier_race += (race - truth) ** 2
            envelope = stage4.cone_levels_pct(sample_sets, weights)
            # The pooled block's cone over the same row, measured beside the
            # shipped one: the two blocks are different envelopes for one row
            # with the same weights, so the *cost* of the sector conditioning
            # (ADR-011 decision 13) is a published number, not an assertion.
            pooled_envelope = stage4.cone_levels_pct(pools, weights)
            low, high = envelope["p10"], envelope["p90"]
            # The cone is the bar-path envelope (CONE_SPEC on the excursions
            # `forward_outcome` measured from the sessions' lows and highs),
            # which is what gate 5 grades: the realised forward min/max must sit
            # inside it. Terminal containment is reported beside it as the
            # containment the envelope is *built* to have — the guard against
            # reporting a wide envelope that no longer contains the close.
            path_ok = low <= row["mae_pct"] and row["mfe_pct"] <= high
            close_ok = low <= row["close_mae_pct"] and row["close_mfe_pct"] <= high
            terminal_ok = low <= row["forward_return_pct"] <= high
            pooled_ok = (pooled_envelope["p10"] <= row["mae_pct"]
                         and row["mfe_pct"] <= pooled_envelope["p90"])
            covered += 1 if path_ok else 0
            close_covered += 1 if close_ok else 0
            terminal_covered += 1 if terminal_ok else 0
            pooled_covered += 1 if pooled_ok else 0
            oof.append({"brier_race": (race - truth) ** 2,
                        "race_won": truth == 1,
                        "covered": path_ok, "close_covered": close_ok,
                        "terminal_covered": terminal_ok,
                        "pooled_covered": pooled_ok,
                        "correct": classes.index(row["class"]) == int(max(range(len(probs)), key=probs.__getitem__))})
        n = len(split["test"])
        report["per_fold"].append({
            "fold": index,
            "span": list(span),
            "test_rows": n,
            "train_rows": len(train),
            "purged_rows": len(split["purged"]),
            "oof_brier_race": round(brier_race / n, 6),
            "oof_brier_multiclass": round(multiclass / n, 6),
            "oof_cone_coverage_pct": round(100.0 * covered / n, 2),
            "oof_cone_close_path_coverage_pct": round(100.0 * close_covered / n, 2),
            "oof_cone_terminal_coverage_pct": round(100.0 * terminal_covered / n, 2),
            "oof_cone_pooled_coverage_pct": round(100.0 * pooled_covered / n, 2),
            "oof_accuracy_pct": round(100.0 * correct / n, 2),
            "majority_class_share_pct": round(100.0 * sum(
                1 for r in train if r["class"] == majority) / len(train), 2),
            "race_band_pct": stage4.RACE_BAND_PCT,
        })
    if oof:
        report["oof_rows"] = len(oof)
        report["oof_sector_pool_share_pct"] = round(
            100.0 * sector_rows / len(oof), 2)
        report["oof_brier_race"] = round(
            sum(entry["brier_race"] for entry in oof) / len(oof), 6)
        # A constant predictor at the corpus's own race-win share is the honest
        # floor for the Brier score: with no skill, `p(1-p)` is what it scores.
        race_share = sum(1 for entry in oof if entry["race_won"]) / len(oof)
        report["oof_race_win_share"] = round(race_share, 6)
        report["oof_brier_race_base_rate"] = round(
            race_share * (1.0 - race_share), 6)
        report["oof_cone_coverage_pct"] = round(
            100.0 * sum(1 for entry in oof if entry["covered"]) / len(oof), 2)
        report["oof_cone_close_path_coverage_pct"] = round(
            100.0 * sum(1 for entry in oof if entry["close_covered"]) / len(oof), 2)
        report["oof_cone_terminal_coverage_pct"] = round(
            100.0 * sum(1 for entry in oof if entry["terminal_covered"]) / len(oof), 2)
        report["oof_cone_pooled_coverage_pct"] = round(
            100.0 * sum(1 for entry in oof if entry["pooled_covered"]) / len(oof), 2)
        report["oof_accuracy_pct"] = round(
            100.0 * sum(1 for entry in oof if entry["correct"]) / len(oof), 2)
    report["total_purged_rows"] = sum(entry["purged"] for entry in splits)
    return report, splits


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Train the shipped stage-4 booster from the sourced corpus.")
    parser.add_argument("--model-dir", default=str(MODEL_DIR),
                        help="where to write the artifacts (default: models/)")
    parser.add_argument("--corpus-dir", default=str(CORPUS_DIR),
                        help="corpus directory holding the per-symbol vintages "
                             "and indices_daily.json (default: the committed "
                             "tests/fixtures/history/)")
    parser.add_argument("--folds", type=int, default=FOLDS)
    parser.add_argument("--embargo", type=int, default=EMBARGO_SESSIONS)
    parser.add_argument("--cv-only", action="store_true",
                        help="report the out-of-fold metrics and write nothing")
    args = parser.parse_args(argv)

    stage4 = load_stage4()
    sector_map = load_sector_map()
    corpus_dir = Path(args.corpus_dir)
    stocks, corpus_meta = load_stock_corpus(corpus_dir)
    indices = load_index_corpus(corpus_dir)
    rows, disclosure = build_rows(stage4, sector_map, stocks, indices,
                                  vintages=corpus_meta.get("vintages"))
    if not rows:
        raise SystemExit("no training rows: the corpus has no symbol whose sector "
                         "index the source serves")

    import lightgbm
    import numpy as np

    report, splits = cross_validate(stage4, rows, folds=args.folds,
                                    embargo=args.embargo,
                                    lightgbm=lightgbm, np=np)

    classes = tuple(stage4.CLASSES)
    counts = Counter(row["class"] for row in rows)
    missing = [cls for cls in classes if not counts.get(cls)]
    if missing:
        raise SystemExit(f"frozen class set has no observations: {missing}")

    dataset = lightgbm.Dataset(
        np.asarray([r["features"] for r in rows], dtype=float),
        label=np.asarray([classes.index(r["class"]) for r in rows], dtype=int),
        feature_name=list(stage4.FEATURES), free_raw_data=False)
    booster = lightgbm.train(MODEL_PARAMS, dataset, num_boost_round=NUM_BOOST_ROUND)

    pooled_blocks = calibration_samples(rows, classes)
    sector_blocks = calibration_samples_by_sector(rows, classes)
    dates = sorted({row["date"] for row in rows})
    try:
        shown_corpus = corpus_dir.relative_to(REPO_ROOT).as_posix()
    except ValueError:  # a caller-supplied corpus outside the repo
        shown_corpus = corpus_dir.as_posix()
    provenance = {
        "origin": "sourced",
        "builder": "tests/evals/train_direction_model.py",
        "corpus": {
            "stock": f"{shown_corpus}/<SYMBOL>_<YYYYMM>.json",
            "index": f"{shown_corpus}/{INDEX_CORPUS.name}",
            "symbols": sorted({row["symbol"] for row in rows}),
            "symbols_excluded": disclosure["skipped_symbols"],
            "sector_indices": sorted({row["sector_index"] for row in rows}),
            "vintages": sum(corpus_meta["files"].values()),
            "duplicate_date_clashes": corpus_meta["clashes"],
            "incomplete_sessions": corpus_meta["incomplete_sessions"],
            "training_rows": len(rows),
            "class_counts": {cls: counts.get(cls, 0) for cls in classes},
            "row_window": [dates[0], dates[-1]],
            # Which sessions the forward labels may read. The vintages are not
            # consecutive, so a window taken off the merged series can span a
            # gap; every row here was labeled inside one vintage.
            "label_window": {
                "rule": (f"the {stage4.HORIZON}-session forward label window, "
                         "including the row's own session, must lie inside a "
                         "single committed vintage"),
                "dropped_rows": sum(disclosure["label_windows_dropped"].values()),
                "dropped_by_symbol": disclosure["label_windows_dropped"],
            },
            # Which sessions the features may read backwards. Overlapping
            # fetches chain into one gapless run (continuous market time), but
            # a hole no fetch covers ends a run: a history may not reach
            # across one, and the row's prefix is cut at its run's start.
            "feature_window": {
                "rule": (f"the {stage4.MIN_SESSIONS}-session feature history, "
                         "ending at the row's own session, must stay inside a "
                         "single gapless run of the merged series (overlapping "
                         "vintages chain; a hole no fetch covers ends a run)"),
                "dropped_rows": sum(disclosure["feature_windows_dropped"].values()),
                "dropped_by_symbol": disclosure["feature_windows_dropped"],
            },
            # Which sectors the cone can answer for, and how deep each block is.
            # A block below stage 4's floor never answers, so the counts are what
            # says whether the shipped cone is really sector-conditioned.
            "sector_blocks": {
                ticker: block_disclosure(block)
                for ticker, block in sorted(sector_blocks.items())
            },
            "pooled_block": block_disclosure(pooled_blocks),
        },
        "cv": report,
        "hyperparameters": {"params": MODEL_PARAMS, "num_boost_round": NUM_BOOST_ROUND,
                           "note": "frozen constants, not selected on the CV folds"},
        "lightgbm_version": lightgbm.__version__,
        "fetched_on": date.today().isoformat(),
        "note": (
            "Sourced booster for BRD §5.2's target-versus-stop race engine "
            "(decision D — the graded event is the reference-band race, not the "
            "1-month up/down): real yfinance "
            "daily bars for the symbols whose sectoral index the tier-1 source "
            "serves, point-in-time rows only with forward label windows kept "
            "inside one committed vintage and feature histories kept inside one "
            "gapless run of overlapping vintages, validated by purged group "
            "time-series CV (BRD §5.2). The trained universe is disclosed in "
            "corpus.symbols / corpus.symbols_excluded."
        ),
    }

    if args.cv_only:
        print(json.dumps(report, indent=2))
        print(f"rows: {len(rows)} · classes: {dict(counts)}")
        dropped = sum(disclosure["label_windows_dropped"].values())
        print(f"label windows dropped for a gap between vintages: {dropped}")
        for symbol, count in disclosure["label_windows_dropped"].items():
            print(f"  [dropped] {symbol}: {count} row(s) whose "
                  f"{stage4.HORIZON}-session label window left its vintage")
        history_drops = sum(disclosure["feature_windows_dropped"].values())
        print(f"feature histories dropped for a hole between runs: "
              f"{history_drops}")
        for symbol, count in disclosure["feature_windows_dropped"].items():
            print(f"  [dropped] {symbol}: {count} row(s) whose "
                  f"{stage4.MIN_SESSIONS}-session feature history crossed a "
                  "hole between vintage runs")
        return 0

    model_dir = Path(args.model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / f"{MODEL_NAME}.txt"
    # Bytes, never text mode: LightGBM's format parses line by line and a
    # Windows text-mode write turns every \n into \r\n ("Model format error,
    # expect a tree here"). .gitattributes pins the file to LF for the same
    # reason on checkout.
    model_path.write_bytes(booster.model_to_string().encode("utf-8"))

    calibration = {
        "schema": stage4.CALIBRATION_SCHEMA,
        "horizon": stage4.HORIZON,
        "classes": list(classes),
        "features": list(stage4.FEATURES),
        "race_band_pct": stage4.RACE_BAND_PCT,
        "race_estimator": RACE_ESTIMATOR_NOTE,
        "samples": pooled_blocks,
        # One block per sectoral index, plus the pooled block above as the
        # fallback; stage 4's `samples_for` picks between them.
        "samples_by_sector": sector_blocks,
        "provenance": provenance,
    }
    calibration_path = model_dir / f"{MODEL_NAME}.calibration.json"
    calibration_path.write_text(json.dumps(calibration, indent=2) + "\n",
                                encoding="utf-8")

    try:
        shown_model = model_path.relative_to(REPO_ROOT)
        shown_calibration = calibration_path.relative_to(REPO_ROOT)
    except ValueError:  # a caller-supplied directory outside the repo
        shown_model, shown_calibration = model_path, calibration_path
    print(f"wrote {shown_model} ({model_path.stat().st_size} bytes)")
    print(f"wrote {shown_calibration}")
    print(f"rows: {len(rows)} · classes: {dict(counts)} · "
          f"race band ±{stage4.RACE_BAND_PCT}%")
    dropped = sum(disclosure["label_windows_dropped"].values())
    print(f"label windows dropped for a gap between vintages: {dropped}")
    for symbol, count in disclosure["label_windows_dropped"].items():
        print(f"  [dropped] {symbol}: {count} row(s) whose "
              f"{stage4.HORIZON}-session label window left its vintage")
    history_drops = sum(disclosure["feature_windows_dropped"].values())
    print(f"feature histories dropped for a hole between runs: "
          f"{history_drops}")
    for symbol, count in disclosure["feature_windows_dropped"].items():
        print(f"  [dropped] {symbol}: {count} row(s) whose "
              f"{stage4.MIN_SESSIONS}-session feature history crossed a "
              "hole between vintage runs")
    print(f"out-of-fold Brier (race): {report.get('oof_brier_race')} over "
          f"{report.get('oof_rows')} row(s) (base rate "
          f"{report.get('oof_brier_race_base_rate')}) · cone coverage "
          f"{report.get('oof_cone_coverage_pct')}% bar path / "
          f"{report.get('oof_cone_close_path_coverage_pct')}% close path / "
          f"{report.get('oof_cone_terminal_coverage_pct')}% terminal · "
          f"accuracy {report.get('oof_accuracy_pct')}%")
    print(f"cone pools: {report.get('oof_sector_pool_share_pct')}% of out-of-fold "
          f"rows read their own sector's block "
          f"({len(sector_blocks)} sector block(s) written)")
    for symbol, reason in sorted(disclosure["skipped_symbols"].items()):
        print(f"  [excluded] {symbol}: {reason}", file=sys.stderr)
    for fold in splits:
        print(f"  fold {fold['fold']} {fold['span'][0]}..{fold['span'][1]}: "
              f"{fold['test']} test / {fold['train']} train / "
              f"{fold['purged']} purged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
