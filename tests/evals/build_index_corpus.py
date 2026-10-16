#!/usr/bin/env python3
"""Build the committed NSE **index** history corpus the stage-4 trainer reads.

    python tests/evals/build_index_corpus.py --fetch   # tier-1 pull, then write
    python tests/evals/build_index_corpus.py --check   # offline: validate the committed file

The stock side of the corpus already lives in `tests/fixtures/history/`
(`SYMBOL_YYYYMM.json`, real yfinance daily bars, one fetch per symbol-month).
Stage 4's two cross-asset features — the 20-session beta to NIFTY 50 and the
20-session correlation to the trade's sectoral index — need the *market* side
of those same sessions, and until this file existed there was none: the only
`uvest-market-context/1` bundle in the repo is a single authored 2026 window.

So the panel is pulled once from the same tier-1 source the live fetcher uses
(`yfinance` daily closes, through `scripts/fetch_market_context.fetch_yahoo_closes`
— one fetch implementation, not two) and committed. Training then runs offline
from committed bytes; the fetch is the only networked step and it is not part
of the test suite.

**Sourced, and disclosed where the source is silent.** Yahoo serves long
history for `^NSEI`, `^INDIAVIX`, `^NSEBANK`, `^CNXIT` and `^CNXPHARMA`, but
only a single session for `^CNXAUTO`, `^CNXFMCG`, `^CNXMETAL`, `^CNXINFRA` and
`^CNXENERGY`, and no `^CNXBANK` at all. A series below `MIN_CORPUS_SESSIONS`
cannot carry a training row (200 sessions of features + a 22-session forward
label), so it is recorded under `absent` with the reason the source gave rather
than committed as a stub. Nothing here is synthesized, interpolated or carried
forward: an absent series is absent.

**The second tier-1 source** (2026-10-15). Yahoo's gaps are not a data gap: NSE
publishes its own daily index snapshot (`ind_close_all_DDMMYYYY.csv`, one file
per trading day, Open/High/Low/Close for every Nifty sector index) and the
archive carries it back through the corpus. That file is the *same* free-first
tier this repository already prefers over any aggregator (AGENTS.md §5.2), so
the sectors Yahoo will not serve are filled from it — one series at a time, and
never for a ticker Yahoo already served, so the fallback can never quietly
become the primary. Every served series records which source served it
(`tickers.<ticker>.source`), the archive's own gaps are counted
(`provenance.archive.no_file`), and a day the archive could not be *reached* is
a failed build rather than a missing session: "we did not reach it" and "the
source does not publish it" are different facts and only one of them may be
recorded as an absent session.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import io
import json
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from math import ceil
from pathlib import Path
from time import sleep


# Windows consoles default to a legacy codepage; both streams must be UTF-8
# (repo convention — the same reconfigure every stage script performs).
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", line_buffering=True)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
CORPUS_FILE = REPO_ROOT / "tests" / "fixtures" / "history" / "indices_daily.json"

SCHEMA = "uvest-index-corpus/1"
SCHEMA_YAHOO_SOURCE = "yahoo:yf.download (yfinance daily closes, source=yfinance)"
SOURCE = (SCHEMA_YAHOO_SOURCE + " + nse:ind_close_all archive (official NSE "
          "daily index snapshot, used for the sectors Yahoo does not serve)")
# Which source each series came from, recorded per ticker.
YAHOO_SERIES_SOURCE = "yahoo:yf.download (yfinance daily closes)"
NSE_SERIES_SOURCE = ("nse:ind_close_all archive (official NSE daily index "
                     "snapshot, Open/High/Low/Close)")
# The archive is one CSV per trading day; the hosts are the same archive under
# two names NSE uses interchangeably.
NSE_ARCHIVE_HOSTS = ("nsearchives.nseindia.com", "archives.nseindia.com")
NSE_ARCHIVE_URL = "https://{host}/content/indices/ind_close_all_{day}.csv"
NSE_ARCHIVE_CACHE = REPO_ROOT / "data_cache" / "nse_index"

# The official index name each panel ticker carries in the archive's own
# `Index Name` column. The panel addresses Yahoo-style tickers, so the mapping
# is the one piece of knowledge the fallback needs, and it covers the *whole*
# panel: a ticker missing here could never be served by the fallback, which is a
# hole nobody would see.
#
# `^CNXBANK` is the shared symbol→sector map's PSU-bank slot (BANKBARODA, PNB,
# CANBK); NSE's own name for that index is `Nifty PSU Bank`, which is also the
# index Yahoo lists under `^CNXPSUBANK`. The map's label is not ours to rename —
# it is pinned domain data with its own tests — so the archive name resolves it
# to the official series rather than to a guessed one.
NSE_ARCHIVE_NAMES = {
    "^NSEI": "Nifty 50",
    "^INDIAVIX": "India VIX",
    "^NSEBANK": "Nifty Bank",
    "^CNXIT": "Nifty IT",
    "^CNXPHARMA": "Nifty Pharma",
    "^CNXREALTY": "Nifty Realty",
    "^CNXAUTO": "Nifty Auto",
    "^CNXFMCG": "Nifty FMCG",
    "^CNXMETAL": "Nifty Metal",
    "^CNXENERGY": "Nifty Energy",
    "^CNXINFRA": "Nifty Infrastructure",
    "^CNXBANK": "Nifty PSU Bank",
}
ARCHIVE_NAME_COLUMN = "Index Name"
ARCHIVE_DATE_COLUMN = "Index Date"
ARCHIVE_CLOSE_COLUMN = "Closing Index Value"
ARCHIVE_ATTEMPTS = 3
# The archive needs a browser-like User-Agent; cookies are not required.
ARCHIVE_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


class ArchiveUnreachable(RuntimeError):
    """The archive could not be reached for a day — never "no such file"."""
# Fallback fetch floor, used only when no readable stock history is present.
# Normally the window is *derived* from the committed vintages (`fetch_sessions`):
# a fixed count went stale at "2021-05 onward" while the corpus reached 2018-03,
# and the panel's shortfall held the first buildable row — hence
# `provenance.corpus.row_window` — open past the benchmark's own Covid tapes
# (ADR-011 decision 10). `fetch_yahoo_closes` widens sessions→calendar days
# by ~1.7x plus a 30-day buffer.
SESSIONS = 1400
# 200 sessions of features + a 22-session forward label, with headroom: below
# this a series cannot produce a single training row.
MIN_CORPUS_SESSIONS = 250
# The panel is the union of what consumers address: NIFTY 50 and India VIX, plus
# every ticker the shared symbol→sector map names. The full panel is attempted
# so a source that stops serving a series is *disclosed*, not silently dropped.
PANEL_EXTRA = ["^NSEI", "^INDIAVIX"]


def panel() -> list[str]:
    spec = importlib.util.spec_from_file_location(
        "uvest_sector_map", SCRIPTS_DIR / "_sector_map.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    sectors = sorted(set(module.SECTOR_INDEX_MAP.values()))
    return PANEL_EXTRA + [t for t in sectors if t not in PANEL_EXTRA]


def fetch_market_context():
    spec = importlib.util.spec_from_file_location(
        "uvest_fetch_market_context", SCRIPTS_DIR / "fetch_market_context.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def stock_history_span() -> tuple[date, date] | None:
    """`(first, last)` session across the committed per-symbol vintages.

    The panel exists to give those vintages their market and sector halves: a
    training row needs `CROSS_ASSET_WINDOW + 1` index sessions at or before
    its own date (`4_ml_direction.common_return_windows`), so a fetch window
    shorter than the stock corpus holds row dates back — which is how the
    panel's 2020-03-09 start opened the trained window on 2020-04-09 while
    the stock vintages for the benchmark's Covid symbols reached 2019-04-30.
    Returns None when no readable stock history is present (the `SESSIONS`
    floor applies then).
    """
    first = last = None
    for path in sorted(CORPUS_FILE.parent.glob("*.json")):
        if path.name == CORPUS_FILE.name:
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        sessions = document.get("sessions")
        if not isinstance(sessions, list):
            continue
        dates = [str(row["date"]) for row in sessions
                 if isinstance(row, dict) and row.get("date")]
        if not dates:
            continue
        span_first, span_last = min(dates), max(dates)
        first = span_first if first is None or span_first < first else first
        last = span_last if last is None or span_last > last else last
    if first is None or last is None:
        return None
    return date.fromisoformat(first), date.fromisoformat(last)


def fetch_sessions(as_of: date) -> int:
    """The session count whose fetch window spans the whole stock corpus.

    `fetch_yahoo_closes` requests `start = as_of - (sessions * 1.7 + 30)`
    days, so this solves for the count whose start lands a quarter (90 days)
    before the corpus's earliest committed session — derived from the corpus
    it serves, not a constant that goes stale when a vintage lands earlier
    (the failure ADR-011 decision 10 records). Falls back to the `SESSIONS`
    floor when no stock history is readable.
    """
    span = stock_history_span()
    if span is None:
        return SESSIONS
    first, _last = span
    days_back = (as_of - first).days + 90
    return max(SESSIONS, ceil((days_back - 30) / 1.7))


# --------------------------------------------------------------------------
# The second tier-1 source: NSE's own daily index snapshot archive
# --------------------------------------------------------------------------
def _archive_day(day: date, *, attempts: int = ARCHIVE_ATTEMPTS,
                 sleep_between=0.4) -> tuple[str, dict[str, float]]:
    """One trading day's snapshot → `(status, {index name: close})`.

    `status` is `published` (the archive carries that day) or `no_file` (HTTP
    404 — the source's own statement that it published nothing for the day, a
    holiday or a hole in the archive). Any other outcome is *not* a status: it is
    retried, and if the retries run out it raises, because a day we could not
    reach must never be recorded as a day the source does not have.
    """
    last_error: Exception | None = None
    for attempt in range(attempts):
        not_found = 0
        for host in NSE_ARCHIVE_HOSTS:
            url = NSE_ARCHIVE_URL.format(host=host, day=day.strftime("%d%m%Y"))
            request = urllib.request.Request(url, headers=ARCHIVE_HEADERS)
            try:
                with urllib.request.urlopen(request, timeout=25) as response:
                    body = response.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code == 404:
                    not_found += 1
                    continue  # the other host may still carry the day
                break
            except Exception as exc:  # noqa: BLE001 — any transport failure
                last_error = exc
                break
            closes: dict[str, float] = {}
            for row in csv.DictReader(io.StringIO(body)):
                name = (row.get(ARCHIVE_NAME_COLUMN) or "").strip()
                value = (row.get(ARCHIVE_CLOSE_COLUMN) or "").strip()
                if not name or not value:
                    continue
                try:
                    closes[name] = round(float(value), 4)
                except ValueError:
                    continue
            if closes:
                return "published", closes
            last_error = ValueError(f"{url}: no parseable index rows")
        if not_found == len(NSE_ARCHIVE_HOSTS):
            # Both hosts answered 404: the source's own statement that it
            # published nothing for this day. A definitive answer, not a
            # transient failure — retrying it would only be noise.
            return "no_file", {}
        if attempt + 1 < attempts:
            sleep(sleep_between * (attempt + 1))
    raise ArchiveUnreachable(
        f"{day.isoformat()}: the archive could not be reached "
        f"({type(last_error).__name__ if last_error else 'unknown error'}: "
        f"{last_error})")


def _cache_path(cache_dir: Path, day: date) -> Path:
    return cache_dir / f"{day.strftime('%Y%m%d')}.json"


def fetch_nse_index_closes(tickers: list[str], *, window_start: date, as_of: date,
                          cache_dir: Path | None = None,
                          day_fetcher=None, workers: int = 8, log=print,
                          ) -> tuple[dict[str, list[tuple[str, float]]], dict]:
    """`{ticker: [(date, close)]}` from NSE's daily index-snapshot archive.

    One file per *weekday* in `window_start .. as_of`; the days the archive does
    not carry are counted under `no_file`, and a day that could not be reached
    raises `ArchiveUnreachable` (naming it) instead of becoming a missing
    session. Fetched days are cached one JSON file per day, so a run that dies
    halfway resumes instead of re-fetching, and the cache is never written for a
    day that failed.

    Returns `(series, disclosure)`; the disclosure is what the committed corpus
    publishes about the archive's own coverage.
    """
    wanted = {NSE_ARCHIVE_NAMES[t]: t for t in tickers if t in NSE_ARCHIVE_NAMES}
    fetch_day = day_fetcher or _archive_day
    cache = Path(cache_dir) if cache_dir is not None else NSE_ARCHIVE_CACHE
    cache.mkdir(parents=True, exist_ok=True)

    days: list[date] = []
    day = window_start
    while day <= as_of:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)

    per_day: dict[date, tuple[str, dict[str, float]]] = {}
    pending: list[date] = []
    for day in days:
        path = _cache_path(cache, day)
        if path.exists():
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                per_day[day] = (str(cached["status"]),
                                {str(k): float(v)
                                 for k, v in (cached.get("closes") or {}).items()})
                continue
            except (OSError, json.JSONDecodeError, KeyError, TypeError,
                    ValueError):
                pass  # an unreadable cache entry is simply re-fetched
        pending.append(day)

    failures: list[str] = []
    if pending:
        log(f"nse archive: fetching {len(pending)} weekday(s) "
            f"{pending[0]}..{pending[-1]} ({len(days) - len(pending)} cached)")

        def work(day: date):
            try:
                return day, fetch_day(day), None
            except Exception as exc:  # noqa: BLE001 — reported, never swallowed
                return day, None, exc

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for day, outcome, error in pool.map(work, pending):
                if error is not None:
                    failures.append(f"{day.isoformat()}: "
                                    f"{type(error).__name__}: {error}")
                    continue
                status, closes = outcome
                per_day[day] = (status, closes)
                _cache_path(cache, day).write_text(
                    json.dumps({"status": status,
                                "date": day.isoformat(),
                                "closes": closes}), encoding="utf-8")
    if failures:
        raise ArchiveUnreachable(
            "the archive could not be reached for "
            f"{len(failures)} day(s): " + "; ".join(sorted(failures)[:5])
            + (" ..." if len(failures) > 5 else ""))

    series: dict[str, list[tuple[str, float]]] = {}
    no_file: list[str] = []
    for day in days:
        status, closes = per_day[day]
        if status != "published":
            no_file.append(day.isoformat())
            continue
        for name, ticker in wanted.items():
            if name in closes:
                series.setdefault(ticker, []).append((day.isoformat(), closes[name]))
    for ticker in series:
        series[ticker].sort()
    published = len(days) - len(no_file)
    return series, {
        "source": NSE_SERIES_SOURCE,
        "url": NSE_ARCHIVE_URL.format(host=NSE_ARCHIVE_HOSTS[0], day="DDMMYYYY"),
        "window": [window_start.isoformat(), as_of.isoformat()],
        "weekdays_attempted": len(days),
        "published": published,
        "no_file": no_file,
        "cache": str(cache),
    }


def archive_window_start(as_of: date) -> date:
    """Where the archive fetch starts: a quarter before the stock corpus.

The archive serves the sectors whose symbols the corpus carries, so it has to
    reach back at least as far as the Yahoo window does; a row needs
    `CROSS_ASSET_WINDOW + 1` index sessions at or before its own date, so the
    90-day margin is the same derivation `fetch_sessions` uses. Falls back to the
    `SESSIONS` floor when no stock history is readable.
    """
    span = stock_history_span()
    if span is None:
        return as_of - timedelta(days=int(SESSIONS * 1.7) + 30)
    return span[0] - timedelta(days=90)


def build(*, as_of: date, yahoo=None, archive=None,
          cache_dir: Path | None = None) -> dict:
    """The committed panel: Yahoo first, NSE's archive for what Yahoo misses.

    The fallback is asked only about the tickers Yahoo could not serve with
    enough sessions to carry a training row, so it can never quietly become the
    primary source; each served series records which of the two served it.
    """
    tickers = panel()
    if yahoo is None:
        fmc = fetch_market_context()
        yahoo = (lambda names, sessions, as_of: fmc.fetch_yahoo_closes(
            names, sessions=sessions, as_of=as_of))
    got = yahoo(tickers, fetch_sessions(as_of), as_of)

    served: dict[str, dict] = {}
    short: dict[str, str] = {}
    for ticker in tickers:
        pairs = [(str(d), float(c)) for d, c in (got.get(ticker) or [])]
        if len(pairs) >= MIN_CORPUS_SESSIONS:
            served[ticker] = _series_entry(pairs, YAHOO_SERIES_SOURCE)
        else:
            short[ticker] = ("the source returned no sessions" if not pairs else
                             f"the source served {len(pairs)} session(s), below "
                             f"the {MIN_CORPUS_SESSIONS}-session floor a "
                             "training row needs")

    archive_note: dict | None = None
    absent: dict[str, str] = {}
    if short:
        if archive is None:
            archive = (lambda names, window_start, as_of, cache_dir:
                       fetch_nse_index_closes(
                           names, window_start=window_start, as_of=as_of,
                           cache_dir=cache_dir))
        series, archive_note = archive(list(short), archive_window_start(as_of),
                                       as_of, cache_dir)
        for ticker, yahoo_reason in short.items():
            pairs = series.get(ticker) or []
            if len(pairs) >= MIN_CORPUS_SESSIONS:
                served[ticker] = _series_entry(pairs, NSE_SERIES_SOURCE)
                continue
            archive_reason = (
                "nse archive: no session of this index in the fetched window"
                if not pairs else
                f"nse archive: {len(pairs)} session(s), below the "
                f"{MIN_CORPUS_SESSIONS}-session floor")
            absent[ticker] = f"yahoo: {yahoo_reason}; {archive_reason}"

    first = min((s["first_date"] for s in served.values()), default=None)
    last = max((s["last_date"] for s in served.values()), default=None)
    return {
        "schema": SCHEMA,
        "source": SOURCE,
        "fetched_on": as_of.isoformat(),
        "window": [first, last],
        "tickers": served,
        "absent": dict(sorted(absent.items())),
        "provenance": {
            "origin": "sourced",
            "builder": "tests/evals/build_index_corpus.py",
            "archive": archive_note,
            "note": (
                "Real Yahoo Finance daily closes (one fetch of the full panel) "
                "plus NSE's own daily index snapshot for the sectors Yahoo does "
                "not serve historically — the free-first tier the pipeline "
                "already prefers. Committed so training is offline and "
                "reproducible; every series names its source, and a series "
                "neither source carried is listed under `absent` with both "
                "reasons — never synthesized, and never replaced by a proxy "
                "index."
            ),
        },
    }


def _series_entry(pairs: list[tuple[str, float]], source: str) -> dict:
    """One served series, in date order (the shape `check` requires)."""
    ordered = sorted(((str(d), float(c)) for d, c in pairs), key=lambda p: p[0])
    return {
        "unit": "index points",
        "source": source,
        "sessions": len(ordered),
        "first_date": ordered[0][0],
        "last_date": ordered[-1][0],
        "values": [{"date": d, "close": round(c, 4)} for d, c in ordered],
    }


def check(document: dict) -> list[str]:
    """Contract violations in a committed corpus (empty list = sound)."""
    problems: list[str] = []
    if document.get("schema") != SCHEMA:
        problems.append(f"schema is {document.get('schema')!r}, expected {SCHEMA!r}")
    expected = set(panel())
    served = document.get("tickers") or {}
    absent = document.get("absent") or {}
    if not served:
        problems.append("no served series at all — the corpus cannot train anything")
    if set(served) | set(absent) != expected:
        problems.append(
            "panel drift: served+absent "
            f"({sorted(set(served) | set(absent))}) != the shared map's panel "
            f"({sorted(expected)})")
    for ticker, entry in served.items():
        values = entry.get("values") or []
        if not entry.get("source"):
            problems.append(f"{ticker}: served with no source named — a series "
                            "must say which tier-1 source carries it")
        if len(values) != entry.get("sessions"):
            problems.append(f"{ticker}: sessions {entry.get('sessions')} != "
                            f"{len(values)} value(s)")
        if len(values) < MIN_CORPUS_SESSIONS:
            problems.append(f"{ticker}: {len(values)} session(s) below the "
                            f"{MIN_CORPUS_SESSIONS}-session floor")
        dates = [v.get("date") for v in values]
        if dates != sorted(dates):
            problems.append(f"{ticker}: dates are not sorted")
        if len(set(dates)) != len(dates):
            problems.append(f"{ticker}: duplicate dates")
        if any(not isinstance(v.get("close"), (int, float)) for v in values):
            problems.append(f"{ticker}: a close is not numeric")
    for ticker, reason in absent.items():
        if not reason:
            problems.append(f"{ticker}: absent with no recorded reason")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build/validate the committed NSE index-history corpus.")
    parser.add_argument("--fetch", action="store_true",
                        help="pull the panel from the tier-1 source and write "
                             "the corpus (the only networked path)")
    parser.add_argument("--check", action="store_true",
                        help="validate the committed corpus and exit non-zero "
                             "on any contract violation (the default when "
                             "--fetch is absent)")
    parser.add_argument("--as-of", default=None,
                        help="window end (YYYY-MM-DD; default: today)")
    parser.add_argument("--output", default=str(CORPUS_FILE),
                        help="where to write the corpus (default: the committed "
                             "tests/fixtures/history/indices_daily.json)")
    args = parser.parse_args(argv)
    out = Path(args.output)

    if args.fetch:
        try:
            as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
        except ValueError:
            print("--as-of must be YYYY-MM-DD", file=sys.stderr)
            return 2
        document = build(as_of=as_of)
        problems = check(document)
        if problems:
            for problem in problems:
                print(f"[FAIL] {problem}", file=sys.stderr)
            return 1
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {out.relative_to(REPO_ROOT)}")
    else:
        try:
            document = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[FAIL] cannot read the committed corpus: {exc}", file=sys.stderr)
            return 1
        problems = check(document)
        if problems:
            for problem in problems:
                print(f"[FAIL] {problem}", file=sys.stderr)
            return 1

    tickers = document.get("tickers") or {}
    absent = document.get("absent") or {}
    archive = (document.get("provenance") or {}).get("archive") or {}
    print(f"{len(tickers)} series served · {len(absent)} disclosed absent · "
          f"window {document.get('window')}")
    for ticker, entry in sorted(tickers.items()):
        source = str(entry.get("source") or "?").split(":")[0]
        print(f"  [ok  ] {ticker}: {entry['sessions']} session(s) "
              f"{entry['first_date']} .. {entry['last_date']} ({source})")
    for ticker, reason in sorted(absent.items()):
        print(f"  [absent] {ticker}: {reason}")
    if archive:
        print(f"  nse archive: {archive.get('published')} of "
              f"{archive.get('weekdays_attempted')} weekday(s) published "
              f"{archive.get('window')}"
              + (f" · {len(archive.get('no_file') or [])} day(s) the archive "
                 "does not carry" if archive.get("no_file") else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
