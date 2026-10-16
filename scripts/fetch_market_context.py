#!/usr/bin/env python3
"""Live `uvest-market-context/1` fetcher for stage 7 (ADR-010 decision 4).

Emits the same evidence bundle the authored fixtures carry, from tier-1 free
sources only (BRD's data hierarchy — Yahoo Finance first, NSE's own files
next):

* **`^NSEI`, `^INDIAVIX`, the trade's sector index** — Yahoo Finance via
  `yfinance`, truncated at `--as-of` so the bundle can never carry a
  post-T_rec point (the no-lookahead rule travels with the bundle).
* **`NSE500_BREADTH`** — NSE's own NIFTY 500 constituents CSV plus per-stock
  closes from Yahoo, computed as the % of eligible constituents closing above
  their own 200-DMA. Disclosed: the membership list is today's snapshot
  applied to history (no free point-in-time membership source exists).
* **`FII_DII_NET`** — three layers, official first: NSE's own
  `fiidiiTradeReact` API (latest session), the dated daily cash table on
  5paisa's public FII/DII page (current month; its values match NSE's own to
  the decimal), and the local `data_cache/fii_dii_net.json` continuity cache
  (gitignored, append-only). No free official multi-day history exists — the
  dated table plus the cache is what the 5-session minimum rides on, and the
  bundle's `provenance.sources` says which layer each point came from.

Every series is fetched independently and a source that fails leaves its
series OUT of the bundle with the failure recorded in `provenance.sources` —
stage 7 then waives naming `series:<key>:absent`/`:short` (the tri-state
contract), never an invented value. An index series too thin to join (below
`MIN_JOIN_SESSIONS`) is likewise omitted and disclosed, so a single stub
series cannot cut the others' evidence down to its own dates. Exit 0 whenever a bundle was written;
exit 2 on usage errors (unmapped symbol, bad dates).

The network layer is injectable (`fetch_bundle`'s `sources=` argument), so
the offline seam exercises the whole assembly path from recorded fixtures.

Usage:
    python scripts/fetch_market_context.py --symbol SBIN --output ctx.json
        [--as-of YYYY-MM-DD] [--sessions 260] [--flow-cache PATH]
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import io
import json
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

# Windows consoles default to a legacy codepage; both streams must be UTF-8
# (repo convention — the same reconfigure every stage script performs).
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", line_buffering=True)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
DEFAULT_FLOW_CACHE = REPO_ROOT / "data_cache" / "fii_dii_net.json"

SCHEMA = "uvest-market-context/1"
NSE_CONSTITUENTS_URL = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
NSE_FII_DII_URL = "https://www.nseindia.com/api/fiidiiTradeReact"
FII_DII_TABLE_URL = "https://www.5paisa.com/share-market-today/fii-dii-data"

BREADTH_DMA = 200          # the "% above 200 DMA" definition itself
DEFAULT_SESSIONS = 260     # >= 200 (NIFTY 200-DMA) + buffer for eligible stocks
FLOW_CACHE_MAX = 60
# One trading month of aligned sessions. Below it a series cannot anchor a
# *joint* estimate (the shortest joint window any consumer aligns is stage 4's
# 20-session cross-asset window), so it is omitted and disclosed instead of
# being allowed to cut the series that can.
MIN_JOIN_SESSIONS = 21

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0 Safari/537.36"),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/reports/fii-dii",
}

# Locale-free month table: strptime("%b") follows LC_TIME, and a date that
# only parses under an English locale is not a reproducible parser.
_MONTHS = {m: i + 1 for i, m in enumerate(
    "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}


def _stage7_module():
    """The stage-7 script as a module — one source for keys and the map."""
    spec = importlib.util.spec_from_file_location(
        "uvest_stage7_module", SCRIPTS_DIR / "7_sector_macro_regime.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


S7 = _stage7_module()
NSEI = S7.NSEI
INDIA_VIX = S7.INDIA_VIX
BREADTH = S7.BREADTH
FLOW = S7.FLOW
SECTOR_INDEX_MAP = S7.SECTOR_INDEX_MAP


def die(msg: str, exit_code: int = 2) -> None:
    print(f"ERR_FETCH: {msg}", file=sys.stderr)
    sys.exit(exit_code)


def parse_session_date(text: str) -> date | None:
    """`14-Oct-2026` -> date; None for anything else (locale-free)."""
    parts = text.strip().split("-")
    if len(parts) != 3:
        return None
    day, mon, year = parts
    month = _MONTHS.get(mon[:1].upper() + mon[1:3])
    if month is None or not (day.isdigit() and year.isdigit()):
        return None
    try:
        return date(int(year), month, int(day))
    except ValueError:
        return None


def parse_number(text: str) -> float | None:
    """`-1,857.40` / `+2,670.40` / `₹813.00` -> float; None if unparseable."""
    cleaned = text.replace(",", "").replace("\u20b9", "").replace("\xa0", "").strip()
    if not cleaned or cleaned in {"-", "NA", "N/A"}:
        return None
    if cleaned.startswith("+"):
        cleaned = cleaned[1:]
    try:
        return float(cleaned)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Pure parsers (offline-tested against committed fixtures)
# ---------------------------------------------------------------------------
def parse_constituents_csv(text: str) -> list[str]:
    """NSE's `ind_nifty500list.csv` -> EQ symbols (stable order)."""
    symbols: list[str] = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        symbol = (row.get("Symbol") or "").strip()
        series = (row.get("Series") or "").strip()
        if symbol and series in ("", "EQ"):
            symbols.append(symbol)
    return symbols


def parse_nse_fii_dii(payload) -> list[dict]:
    """NSE `fiidiiTradeReact` JSON -> daily combined-net points.

    The API returns one row per category per session; a session counts only
    when both FII/FPI and DII rows are present (one leg is not a flow).
    """
    by_date: dict[str, dict] = {}
    for row in payload or []:
        if not isinstance(row, dict):
            continue
        iso = None
        parsed = parse_session_date(str(row.get("date") or ""))
        if parsed is not None:
            iso = parsed.isoformat()
        net = parse_number(str(row.get("netValue") or ""))
        if iso is None or net is None:
            continue
        slot = by_date.setdefault(iso, {"net": 0.0, "cats": set()})
        slot["net"] += net
        slot["cats"].add(str(row.get("category") or ""))
    points = []
    for iso in sorted(by_date):
        slot = by_date[iso]
        if any(c.startswith("FII") for c in slot["cats"]) and "DII" in slot["cats"]:
            points.append({"date": iso, "net_cr": round(slot["net"], 2),
                           "source": "nse-api"})
    return points


class _CashTableParser(HTMLParser):
    """Collect the dated daily rows of the page's cash FII/DII table.

    The page mixes cash, F&O and FPI-commitment rows in one markup soup;
    only rows whose class says *cash* and whose first cell is a session date
    (never `Month till date`, never a monthly aggregate) are data.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._cells: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._close_row()
            classes = dict(attrs).get("class") or ""
            self._cells = [] if "cash_" in classes else None
        elif tag in ("td", "th") and self._cells is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._cells.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr":
            self._close_row()

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def _close_row(self):
        if self._cells:
            self.rows.append(self._cells)
        self._cells, self._cell = None, None


def parse_fii_dii_table(html: str) -> list[dict]:
    """5paisa's cash table -> daily combined-net points (current month)."""
    parser = _CashTableParser()
    parser.feed(html)
    points: list[dict] = []
    for cells in parser.rows:
        if len(cells) < 7:
            continue
        when = parse_session_date(cells[0])
        if when is None:
            continue  # `Month till date`, monthly aggregates, headers
        fii_net = parse_number(cells[3])
        dii_net = parse_number(cells[6])
        if fii_net is None or dii_net is None:
            continue
        points.append({"date": when.isoformat(),
                       "net_cr": round(fii_net + dii_net, 2),
                       "source": "public-table"})
    return points


def merge_flow_points(*layers: list[dict]) -> list[dict]:
    """Highest-priority layer first: the first point per date wins."""
    chosen: dict[str, dict] = {}
    for layer in layers:
        for point in layer:
            chosen.setdefault(point["date"], point)
    return [chosen[d] for d in sorted(chosen)]


def load_flow_cache(path: Path) -> list[dict]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return []
    points = doc.get("points") if isinstance(doc, dict) else None
    if not isinstance(points, list):
        return []
    clean = []
    for point in points:
        if not isinstance(point, dict):
            continue
        when = str(point.get("date") or "")
        net = point.get("net_cr")
        if len(when) == 10 and isinstance(net, (int, float)) \
                and not isinstance(net, bool):
            clean.append({"date": when, "net_cr": round(float(net), 2),
                          "source": str(point.get("source") or "cache")})
    return clean


def save_flow_cache(path: Path, points: list[dict]) -> None:
    """Append-only local continuity (gitignored under data_cache/)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "schema": "uvest-fii-dii-cache/1",
        "note": ("Local continuity cache of daily FII+DII combined net cash "
                 "flow (INR crore). Points are recorded by "
                 "scripts/fetch_market_context.py from its disclosed sources "
                 "on the day they are fetched; no value is ever synthesized."),
        "points": points[-FLOW_CACHE_MAX:],
    }
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Pure series builders (offline-tested)
# ---------------------------------------------------------------------------
def truncate(pairs: list[tuple[str, float]], as_of: date) -> list[tuple[str, float]]:
    """Drop every point after `as_of` — the no-lookahead rule, on the bundle."""
    horizon = as_of.isoformat()
    return [(d, v) for d, v in pairs if d <= horizon]


def align_axes(series: dict[str, list[tuple[str, float]]],
               *, min_sessions: int = MIN_JOIN_SESSIONS,
               ) -> tuple[dict[str, list[tuple[str, float]]],
                          list[tuple[str, int]]]:
    """Inner-join the index series on one date axis, first series' order.

    Stage 7 waives on a misaligned axis; joining real sessions against each
    other (never inventing a date) is the fetcher's side of that contract.

    What the join must not do is let one thin series destroy the others. A
    member with fewer than `min_sessions` sessions cannot anchor a joint
    estimate, and joining it in would intersect *every* series down to its
    own handful of dates — an energy-sector trade whose `^CNXENERGY` came back
    as a single session would take `^NSEI`'s trend and `^INDIAVIX`'s panic
    evidence down with it, silently. So such members are omitted from the
    join and returned as `(name, sessions)` for the caller to disclose in
    `provenance.sources` (stage 7 then waives naming that one series, which is
    the honest tri-state outcome). Members that participate are still
    intersected, so no consumer ever sees a date another one lacked.

    Returns `(aligned, omitted)`.
    """
    omitted = sorted((name, len(pairs)) for name, pairs in series.items()
                     if len(pairs) < min_sessions)
    keep = [name for name in series if len(series[name]) >= min_sessions]
    if not keep:
        return {}, omitted
    common = {d for d, _ in series[keep[0]]}
    for name in keep[1:]:
        common &= {d for d, _ in series[name]}
    return ({name: [(d, v) for d, v in series[name] if d in common]
             for name in keep}, omitted)


def breadth_from_closes(closes: dict[str, list[tuple[str, float]]],
                        as_of: date) -> list[tuple[str, float]]:
    """% of eligible constituents above their own 200-DMA, per session.

    A stock is *eligible* on a date only with its full trailing window
    (`BREADTH_DMA` sessions); a stock without one contributes to no date, and
    a date with no eligible stock is omitted — absence, never a fabricated
    percentage.
    """
    horizon = as_of.isoformat()
    per_date: dict[str, list[int]] = {}
    for rows in closes.values():
        ordered = sorted((d, float(v)) for d, v in rows if d <= horizon)
        if len(ordered) < BREADTH_DMA:
            continue
        window = 0.0
        for i, (day, value) in enumerate(ordered):
            window += value
            if i >= BREADTH_DMA:
                window -= ordered[i - BREADTH_DMA][1]
            if i >= BREADTH_DMA - 1:
                slot = per_date.setdefault(day, [0, 0])
                slot[0] += 1
                if value > window / BREADTH_DMA:
                    slot[1] += 1
    return [(day, round(100.0 * above / eligible, 2))
            for day, (eligible, above) in sorted(per_date.items())
            if eligible > 0]


def series_doc(name: str, unit: str,
               pairs: list[tuple[str, float]]) -> dict:
    return {"name": name, "unit": unit,
            "values": [{"date": d, "value": round(float(v), 6)} for d, v in pairs]}


# ---------------------------------------------------------------------------
# Network layer (thin; injectable through `sources=`)
# ---------------------------------------------------------------------------
def http_get(url: str, *, timeout: int = 30) -> bytes:
    request = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _pairs_from_frame(frame) -> list[tuple[str, float]]:
    """yfinance Close series -> [(ISO date, close)]; empty when unavailable.

    yfinance shapes vary by version and ticker kind (flat columns,
    (Price, Ticker) MultiIndex, ticker-by-field), so the Close slice is
    normalized to a single Series before anything else touches it.
    """
    if frame is None or getattr(frame, "empty", True):
        return []
    close = frame
    if hasattr(close, "columns"):
        import pandas as pd
        if isinstance(close.columns, pd.MultiIndex):
            fields = set(map(str, close.columns.get_level_values(0)))
            if "Close" in fields:
                close = close["Close"]
            else:
                close = close.xs("Close", axis=1, level=1, drop_level=True)
        elif "Close" in close.columns:
            close = close["Close"]
    if hasattr(close, "columns"):            # still a DataFrame (sub-frame)
        if close.shape[1] != 1:
            return []
        close = close.iloc[:, 0]
    pairs: list[tuple[str, float]] = []
    for idx, value in close.items():
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue                          # NaN / non-numeric
        if number != number:
            continue
        day = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
        pairs.append((day, number))
    return pairs


def fetch_yahoo_closes(tickers: list[str], *, sessions: int,
                       as_of: date) -> dict[str, list[tuple[str, float]]]:
    """Yahoo daily closes per ticker, end-exclusive window widened for drift."""
    import yfinance as yf
    end = as_of + timedelta(days=1)          # yfinance `end` is exclusive
    start = as_of - timedelta(days=int(sessions * 1.7) + 30)
    out: dict[str, list[tuple[str, float]]] = {}
    for ticker in tickers:
        try:
            frame = yf.download(ticker, start=start, end=end, progress=False,
                                auto_adjust=False, threads=False)
        except Exception:
            frame = None
        out[ticker] = truncate(_pairs_from_frame(frame), as_of)
    return out


def fetch_breadth_panel(symbols: list[str], *, sessions: int,
                        as_of: date) -> dict[str, list[tuple[str, float]]]:
    """Per-stock closes for the constituents panel (batched Yahoo download)."""
    import yfinance as yf
    end = as_of + timedelta(days=1)
    start = as_of - timedelta(days=int(sessions * 1.7) + 30)
    tickers = [f"{s}.NS" for s in symbols]
    try:
        raw = yf.download(tickers, start=start, end=end, progress=False,
                          auto_adjust=False, group_by="column", threads=True)
    except Exception:
        return {}
    if raw is None or getattr(raw, "empty", True):
        return {}
    closes: dict[str, list[tuple[str, float]]] = {}
    import pandas as pd
    if isinstance(raw.columns, pd.MultiIndex):
        fields = set(map(str, raw.columns.get_level_values(0)))
        if "Close" in fields:                 # (field, ticker) layout
            panel = raw["Close"]
        else:                                 # (ticker, field) layout
            panel = raw.xs("Close", axis=1, level=1, drop_level=True)
    else:
        panel = None
    if panel is None:
        return {}
    for column in panel.columns:
        symbol = str(column).removesuffix(".NS")
        frame = pd.DataFrame({"Close": panel[column]}).dropna()
        closes[symbol] = truncate(_pairs_from_frame(frame), as_of)
    return closes


def fetch_constituents() -> list[str]:
    return parse_constituents_csv(http_get(NSE_CONSTITUENTS_URL).decode("utf-8"))


def fetch_nse_flow() -> list[dict]:
    payload = json.loads(http_get(NSE_FII_DII_URL).decode("utf-8"))
    return parse_nse_fii_dii(payload)


def fetch_table_flow() -> list[dict]:
    return parse_fii_dii_table(http_get(FII_DII_TABLE_URL).decode("utf-8"))


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------
def fetch_bundle(symbol: str, as_of: date, *, sessions: int = DEFAULT_SESSIONS,
                 flow_cache: Path = DEFAULT_FLOW_CACHE,
                 sources: dict | None = None) -> tuple[dict, list[dict]]:
    """Fetch every series independently -> (bundle, source statuses).

    `sources` injects each network layer (`indices`, `constituents`,
    `breadth_panel`, `flow_layers`), so the offline seam drives the exact
    assembly path the live run takes. Failures are recorded, never raised:
    the bundle always comes back with whatever real series were obtainable.
    """
    symbol = str(symbol).strip().upper()
    if symbol not in SECTOR_INDEX_MAP:
        raise ValueError(f"symbol:unmapped:{symbol}")
    sector = SECTOR_INDEX_MAP[symbol]
    src: dict = {
        "indices": lambda: fetch_yahoo_closes(
            [NSEI, INDIA_VIX], sessions=sessions, as_of=as_of),
        "sector": lambda tick: fetch_yahoo_closes(
            [tick], sessions=sessions, as_of=as_of).get(tick) or [],
        "constituents": fetch_constituents,
        "breadth_panel": fetch_breadth_panel,
        "flow_layers": lambda cache: (fetch_nse_flow(), fetch_table_flow(),
                                      load_flow_cache(cache)),
    }
    if sources:
        src.update({k: v for k, v in sources.items() if v is not None})
    statuses: list[dict] = []

    def status(role: str, url: str, ok: bool, detail: str) -> None:
        statuses.append({"series": role, "source": url,
                         "status": "ok" if ok else "failed", "detail": detail})

    # --- index series: one Yahoo call shape per ticker, axis-joined ---
    index_series: dict[str, list[tuple[str, float]]] = {}
    try:
        got = src["indices"]()
        for key in (NSEI, INDIA_VIX):
            pairs = got.get(key) or []
            if pairs:
                index_series[key] = pairs
                status(key, "yahoo:yf.download", True, f"{len(pairs)} session(s)")
            else:
                status(key, "yahoo:yf.download", False, "no data returned")
    except Exception as exc:
        for key in (NSEI, INDIA_VIX):
            status(key, "yahoo:yf.download", False, f"{type(exc).__name__}: {exc}")
    try:
        sector_pairs = src["sector"](sector)
        if sector_pairs:
            index_series[sector] = sector_pairs
            status(sector, "yahoo:yf.download", True,
                   f"{len(sector_pairs)} session(s)")
        else:
            status(sector, "yahoo:yf.download", False, "no data returned")
    except Exception as exc:
        status(sector, "yahoo:yf.download", False, f"{type(exc).__name__}: {exc}")
    index_series, thin = align_axes({k: truncate(v, as_of)
                                     for k, v in index_series.items() if v})
    for key, sessions in thin:
        status(key, "yahoo:yf.download", False,
               f"too thin to join: {sessions} session(s) below the "
               f"{MIN_JOIN_SESSIONS}-session floor — omitted so the other "
               "index series keep their own sessions")
    for key in list(index_series):
        if not index_series[key]:
            del index_series[key]

    # --- breadth: constituents CSV + per-stock panel ---
    breadth_pairs: list[tuple[str, float]] = []
    try:
        symbols = src["constituents"]()
        status(BREADTH, "nse:" + NSE_CONSTITUENTS_URL, bool(symbols),
               f"{len(symbols)} constituent(s)")
        panel = src["breadth_panel"](symbols, sessions=sessions, as_of=as_of)
        breadth_pairs = truncate(breadth_from_closes(panel, as_of), as_of)
        if breadth_pairs:
            status(BREADTH, "yahoo:yf.download panel", True,
                   f"{len(breadth_pairs)} session(s), "
                   f"{len([p for p in panel.values() if p])} of "
                   f"{len(panel)} symbol(s) with data")
        else:
            status(BREADTH, "yahoo:yf.download panel", False,
                   "no session reached a full 200-DMA window")
    except Exception as exc:
        status(BREADTH, "yahoo:yf.download panel", False,
               f"{type(exc).__name__}: {exc}")

    # --- flow: official API > dated table > local cache ---
    flow_pairs: list[tuple[str, float]] = []
    try:
        official, table, cached = src["flow_layers"](flow_cache)
        status(FLOW, "nse:" + NSE_FII_DII_URL, bool(official),
               f"{len(official)} session(s) from NSE's official API")
        status(FLOW, FII_DII_TABLE_URL, bool(table),
               f"{len(table)} dated session(s) from the public cash table")
        status(FLOW, str(flow_cache), True,
               f"{len(cached)} cached session(s)")
        merged = merge_flow_points(official, table, cached)
        flow_pairs = truncate([(p["date"], p["net_cr"]) for p in merged], as_of)
        # The cache is an observation log: every fetched point is appended so
        # next run's window survives the table's month boundary; the bundle
        # view above stays truncated at as_of.
        if merged:
            save_flow_cache(flow_cache, merged)
    except Exception as exc:
        status(FLOW, "flow sources", False, f"{type(exc).__name__}: {exc}")

    # --- assemble: only series with real points exist, failures disclosed ---
    series: dict[str, dict] = {}
    for key, pairs in index_series.items():
        if pairs:
            if key == NSEI:
                series[key] = series_doc("NIFTY 50", "index_close", pairs)
            elif key == INDIA_VIX:
                series[key] = series_doc("INDIA VIX", "index_close", pairs)
            else:
                series[key] = series_doc(key, "index_close", pairs)
    if breadth_pairs:
        series[BREADTH] = series_doc("NSE 500 % above 200 DMA", "percent",
                                     breadth_pairs)
    if flow_pairs:
        series[FLOW] = series_doc("FII + DII combined net cash flow", "inr_cr",
                                  flow_pairs)

    bundle = {
        "schema": SCHEMA,
        "as_of": as_of.isoformat(),
        "provenance": {
            "origin": "recorded",
            "source": "scripts/fetch_market_context.py",
            "fetched_at": datetime.now(timezone.utc).isoformat(
                timespec="seconds"),
            "as_of": as_of.isoformat(),
            "sources": statuses,
            "note": (
                "Live tier-1 fetch (BRD data hierarchy): Yahoo Finance index "
                "closes; breadth computed from NSE's own NIFTY 500 "
                "constituents CSV + Yahoo per-stock closes (today's "
                "membership applied to history — disclosed, no free "
                "point-in-time list); flow from NSE's official FII/DII API, "
                "the dated public cash table and the local continuity cache, "
                "official values winning per date. A series whose source "
                "failed is omitted, never invented — stage 7 waives naming "
                "it (the tri-state contract)."),
        },
        "series": series,
    }
    return bundle, statuses


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch a live uvest-market-context/1 bundle for stage 7 "
                    "(tier-1 free sources; failures waive, never invent).")
    parser.add_argument("--symbol", required=True,
                        help="trade symbol; resolves its sector index via "
                             "SECTOR_INDEX_MAP (unmapped symbols exit 2)")
    parser.add_argument("--output", required=True,
                        help="path to write the bundle JSON")
    parser.add_argument("--as-of", default=None,
                        help="horizon YYYY-MM-DD (default: today) — every "
                             "series point is truncated at or before it")
    parser.add_argument("--sessions", type=int, default=DEFAULT_SESSIONS,
                        help=f"history window in sessions (default "
                             f"{DEFAULT_SESSIONS})")
    parser.add_argument("--flow-cache", default=str(DEFAULT_FLOW_CACHE),
                        help="local FII/DII continuity cache path")
    args = parser.parse_args(argv)

    if args.as_of:
        try:
            as_of = date.fromisoformat(args.as_of)
        except ValueError:
            die(f"--as-of must be YYYY-MM-DD, got {args.as_of!r}")
            return 2
    else:
        as_of = date.today()
    symbol = args.symbol.strip().upper()
    if symbol not in SECTOR_INDEX_MAP:
        die(f"symbol:unmapped:{symbol} — stage 7 would waive; refusing to "
            "fetch a bundle it can never grade (SECTOR_INDEX_MAP)")
        return 2

    try:
        bundle, statuses = fetch_bundle(symbol, as_of,
                                        sessions=args.sessions,
                                        flow_cache=Path(args.flow_cache))
    except ValueError as exc:
        die(str(exc))
        return 2

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
    for entry in statuses:
        mark = "ok  " if entry["status"] == "ok" else "FAIL"
        print(f"  [{mark}] {entry['series']}: {entry['detail']}")
    names = ", ".join(sorted(bundle["series"])) or "none (stage 7 will waive)"
    print(f"wrote {output} — series: {names}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
