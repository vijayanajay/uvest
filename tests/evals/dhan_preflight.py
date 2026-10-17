#!/usr/bin/env python3
"""UVEST — Tier-2 Phase 0 preflight probe (plan §4; **disclosure only**).

    python tests/evals/dhan_preflight.py --security-id <id> [--symbol SYMBOL] \
        [--manifest tests/proof/scenario_manifest.json] > preflight.json

One operator session, one script run, and **nothing but a disclosure**: this tool
writes no fixture, no cache and no ledger — it asks the broker what it actually
serves and prints the answer. That is its entire job, and it is the only place a
*real* response can close the host/path/body rows of `plan-tier2-dhan-collection.md`
§2 ("never by editing a doc to match the code").

What it does, in the plan's own order:

1. **Profile** — `GET /v2/profile` with the `access-token` header, disclosing
   `dataPlan` / `dataValidity` / `tokenValidity`: whether this account is even
   entitled to the Data APIs (`DH-902` is the documented "not subscribed" answer).
2. **One intraday probe per vintage** — `POST /v2/charts/intraday` on the
   *earliest session of each vintage* the proof manifest pins, plus the
   **pre-2021 negative control** (plan §3: the five rows older than the
   documented retention window). The claimed boundary is **five years**; the
   disclosure reports the boundary that was *measured*, because a claim that was
   never probed is exactly what this phase exists to replace.
3. **One depth probe** — `POST /v2/marketfeed/quote` for one roster symbol,
   printing the raw 5-level book and the spread stage 8's own `spread_from_quote`
   reads out of it, so the shape is pinned to the parser that will consume it.
4. **Failures are graded, not smoothed.** A vintage the broker does not serve is
   a *measured result* (HTTP status + code disclosed). A vintage that answers 200
   with a body stage 8's parser cannot read is **`ERR_UNEXPECTED_SHAPE`** and the
   run stops there with a non-zero exit — the one thing this probe must never do
   is disclose "reached" for a shape nobody can parse.

Invariants it obeys (plan §5): it never writes; the access token is never
printed, logged or written into the disclosure; rate limits are enforced by the
probe itself (data APIs ≤ 5 req/s, quote API ≤ 1 req/s); a `securityId` is
supplied by the operator rather than guessed, exactly like the Phase 2
`securityId` map's own discipline.

Exit codes: `0` disclosure produced (including "not served" findings), `2`
`ERR_NO_CREDENTIALS` (or a missing `--security-id`) — nothing was attempted,
`3` `ERR_UNEXPECTED_SHAPE`.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent.parent

# The documented profile endpoint (GET, `access-token` header; dhanhq.co/docs/v2/
# authentication/), beside the stage-8 constants the rest of the campaign uses.
DHAN_PROFILE_URL = "https://api.dhan.co/v2/profile"

# Documented rate limits (plan §2): data APIs 5 req/s, quote APIs 1 req/s. The
# probe enforces them itself rather than trusting the operator's cadence.
DATA_MIN_INTERVAL = 0.25
QUOTE_MIN_INTERVAL = 1.0

# The documented intraday retention claim, to be measured rather than assumed.
DOCUMENTED_RETENTION_YEARS = 5

EXIT_NO_CREDENTIALS = 2
EXIT_UNEXPECTED_SHAPE = 3

SCHEMA = "uvest-dhan-preflight/1"
STAGE8 = REPO_ROOT / "scripts" / "8_dhan_precision.py"
DEFAULT_MANIFEST = REPO_ROOT / "tests" / "proof" / "scenario_manifest.json"

# The shape the depth parser is pinned to (stage 8's own `_quote_dicts` path).
DEPTH_SHAPE = "data.<segment>.<securityId>.depth.buy/sell"


# ---------------------------------------------------------------------------
# Stage 8 is the client under test: this probe imports *its* endpoints and *its*
# parsers, so the disclosure describes the code that will run in Phase 2.
# ---------------------------------------------------------------------------
def load_stage8():
    spec = importlib.util.spec_from_file_location("stage8_for_preflight", STAGE8)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_manifest(path: Path | str = DEFAULT_MANIFEST) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Preconditions — fail closed, naming exactly what is missing
# ---------------------------------------------------------------------------
def credentials_problem(creds: dict) -> str | None:
    missing = [name for name in ("DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN")
               if not creds.get(name)]
    if missing:
        return ("credentials missing: " + ", ".join(missing)
                + " (see .env.example; the token is never printed or recorded)")
    return None


def security_id_problem(security_id: str | None) -> str | None:
    if not security_id:
        return ("no --security-id given: the depth probe addresses one instrument "
                "by its Dhan securityId, and this tool never guesses one "
                "(Phase 2's map discipline)")
    return None


# ---------------------------------------------------------------------------
# What gets probed — the manifest's own vintages, never dates typed here
# ---------------------------------------------------------------------------
def _five_years_before(today: date) -> date:
    try:
        return today.replace(year=today.year - DOCUMENTED_RETENTION_YEARS)
    except ValueError:  # 29 February — the boundary is the 28th, not a crash
        return today.replace(year=today.year - DOCUMENTED_RETENTION_YEARS, day=28)


def _session_date(row: dict) -> str | None:
    raw = str(row.get("recommendation_date") or "").strip()
    return raw[:10] if len(raw) >= 10 and raw[4] == "-" else None


def plan_probes(manifest: dict, today: date | None = None) -> list[dict]:
    """One probe per vintage: the earliest session the roster pins in each year.

    The vintages come from the proof manifest's own `recommendation_date`s, so
    the probe measures the boundary on the sessions the campaign actually cares
    about. A session before the documented five-year boundary is the **control**
    (the plan's pre-2021 negative control), and it is labelled by *which* rule
    excludes it rather than by a year hard-coded here.
    """
    today = today or date.today()
    boundary = _five_years_before(today)
    earliest: dict[str, dict] = {}
    for row in manifest.get("scenarios") or []:
        session = _session_date(row)
        if not session or not row.get("symbol"):
            continue
        year = session[:4]
        if year not in earliest or session < earliest[year]["session_date"]:
            earliest[year] = {
                "scenario_id": row.get("scenario_id"),
                "symbol": row.get("symbol"),
                "session_date": session,
            }
    probes: list[dict] = []
    for year in sorted(earliest):
        probe = dict(earliest[year], year=year)
        session = date.fromisoformat(probe["session_date"])
        if session >= boundary:
            probe["role"] = "vintage"
        elif session.year <= 2020:
            probe["role"] = "pre-2021 control"
        else:
            probe["role"] = "outside-retention control"
        probe["inside_documented_retention"] = session >= boundary
        probes.append(probe)
    return probes


def intraday_body(session_date: str, security_id: str, stage8) -> dict:
    """The documented request body (stage 8's own keys, interval 5 minutes)."""
    return {
        "securityId": str(security_id),
        "exchangeSegment": stage8.DHAN_EXCHANGE_SEGMENT,
        "instrument": stage8.DHAN_INSTRUMENT,
        "interval": "5",
        "oi": False,
        "fromDate": f"{session_date} 09:15:00",
        "toDate": f"{session_date} 15:30:00",
    }


# ---------------------------------------------------------------------------
# Transport — the same POST hook stage 8 uses; a GET for the profile
# ---------------------------------------------------------------------------
def http_get_json(url: str, headers: dict) -> dict:
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def default_fetcher(url: str, headers: dict, body: dict | None) -> dict:
    """`body is None` is the only GET in this tool (the profile)."""
    if body is None:
        return http_get_json(url, headers)
    return load_stage8().http_post_json(url, headers, body)


def _auth_headers(creds: dict) -> dict:
    return {"access-token": creds.get("DHAN_ACCESS_TOKEN") or ""}


def _depth_levels(doc, security_id: str) -> dict | None:
    """Level counts of our own id's book, or None when the shape is not there."""
    if not isinstance(doc, dict):
        return None
    data = doc.get("data")
    if not isinstance(data, dict):
        return None
    for segment in data.values():
        row = segment.get(security_id) if isinstance(segment, dict) else None
        depth = row.get("depth") if isinstance(row, dict) else None
        if isinstance(depth, dict):
            buy, sell = depth.get("buy"), depth.get("sell")
            if isinstance(buy, list) and isinstance(sell, list):
                return {"buy": len(buy), "sell": len(sell)}
    return None


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------
def run_preflight(*, manifest: dict, symbol: str | None, security_id: str | None,
                  creds: dict, fetcher, sleep,
                  today: date | None = None) -> dict:
    """The disclosure, as a committable JSON document. Writes nothing."""
    problem = credentials_problem(creds) or security_id_problem(security_id)
    if problem:
        raise SystemExit(EXIT_NO_CREDENTIALS)
    stage8 = load_stage8()
    headers = _auth_headers(creds)
    today = today or date.today()
    boundary = _five_years_before(today)
    probes = plan_probes(manifest, today)

    # 1. Is the Data API even entitled? A failed profile call is disclosed, never
    #    fatal: it is context for the reachability results below, not one of them.
    sleep(DATA_MIN_INTERVAL)
    try:
        data_plan = fetcher(DHAN_PROFILE_URL, headers, None)
    except urllib.error.HTTPError as exc:
        data_plan = {"error": f"HTTP {exc.code}"}
    except Exception as exc:  # noqa: BLE001 — the run continues, disclosed
        data_plan = {"error": type(exc).__name__}

    # 2. One intraday probe per vintage.
    vintages: list[dict] = []
    outcome = "complete"
    for probe in probes:
        body = intraday_body(probe["session_date"], security_id or "", stage8)
        entry = {
            "year": probe["year"], "role": probe["role"],
            "scenario_id": probe["scenario_id"], "symbol": probe["symbol"],
            "session_date": probe["session_date"],
            "inside_documented_retention": probe["inside_documented_retention"],
            "endpoint": stage8.DHAN_INTRADAY_URL,
            "request": {"body": body,
                        "auth": "access-token header (value not recorded)"},
            "reachable": False, "http_status": None, "bars": 0,
            "first_bar": None, "last_bar": None, "detail": None,
        }
        sleep(DATA_MIN_INTERVAL)
        try:
            doc = fetcher(stage8.DHAN_INTRADAY_URL, headers, body)
        except urllib.error.HTTPError as exc:
            entry["http_status"] = exc.code
            entry["detail"] = (f"HTTP {exc.code} — not served or not entitled "
                               "(documented DH-902/DH-904/805 are not mapped to "
                               "an HTTP status by the docs)")
            vintages.append(entry)
            continue
        except Exception as exc:  # noqa: BLE001 — transport failure, disclosed
            entry["detail"] = f"{type(exc).__name__} on charts/intraday"
            vintages.append(entry)
            continue
        bars = stage8._candles_to_bars(doc if isinstance(doc, dict) else None)
        if not bars:
            # 200 with a body the client cannot read: the one case this probe
            # exists to catch, and it stops the run rather than collecting more.
            keys = ", ".join(sorted(doc)) if isinstance(doc, dict) else type(doc).__name__
            entry["http_status"] = 200
            entry["detail"] = (f"unexpected shape — the client's parser read no "
                               f"candles (documented parallel arrays expected; "
                               f"got: {keys})")
            vintages.append(entry)
            outcome = "unexpected_shape"
            break
        entry.update(reachable=True, http_status=200, bars=len(bars),
                     first_bar=bars[0].get("date"), last_bar=bars[-1].get("date"),
                     detail="documented parallel-array response, parsed by "
                            "stage 8's own parser")
        vintages.append(entry)

    # 3. One depth probe, read through stage 8's own spread parser.
    sleep(QUOTE_MIN_INTERVAL)
    quote_body = {stage8.DHAN_EXCHANGE_SEGMENT: [str(security_id)]}
    depth = {
        "endpoint": stage8.DHAN_MARKETFEED_URL,
        "request": {"body": quote_body,
                    "auth": "access-token header (value not recorded)"},
        "shape": DEPTH_SHAPE, "levels": None, "spread_pct": None,
        "waived_reason": None, "raw": None,
    }
    try:
        doc = fetcher(stage8.DHAN_MARKETFEED_URL, headers, quote_body)
    except urllib.error.HTTPError as exc:
        depth["waived_reason"] = f"HTTP {exc.code} on marketfeed/quote"
    except Exception as exc:  # noqa: BLE001
        depth["waived_reason"] = f"{type(exc).__name__} on marketfeed/quote"
    else:
        depth["raw"] = doc
        depth["levels"] = _depth_levels(doc, str(security_id))
        spread, reason = stage8.spread_from_quote(doc, security_id=str(security_id))
        depth["spread_pct"] = spread
        depth["waived_reason"] = reason
        if spread is not None and depth["levels"] is None:
            depth["detail"] = ("a spread was readable but the 5-level book was "
                               "not — a flat bid/ask quote, not the documented "
                               "depth shape")

    reachable = [v for v in vintages if v["reachable"]]
    in_window = [v for v in reachable if v["inside_documented_retention"]]
    return {
        "schema_version": SCHEMA,
        "ran_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tool": "tests/evals/dhan_preflight.py",
        "outcome": outcome,
        "writes": "none",
        "credentials": {name: ("present" if creds.get(name) else "absent")
                        for name in ("DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN")},
        "symbol": symbol, "security_id": str(security_id),
        "data_plan": data_plan,
        "vintages": vintages,
        "retention": {
            "documented_claim_years": DOCUMENTED_RETENTION_YEARS,
            "documented_boundary": boundary.isoformat(),
            "measured_earliest_reachable": (reachable[0]["session_date"]
                                            if reachable else None),
            "measured_earliest_vintage": (in_window[0]["year"]
                                          if in_window else None),
            "unreachable_vintages": [v["year"] for v in vintages
                                     if not v["reachable"]],
            "note": ("the documented boundary is a claim; only the per-vintage "
                     "results above measure what this account is served"),
        },
        "depth": depth,
        "rate_limits": {"data_min_interval_s": DATA_MIN_INTERVAL,
                        "quote_min_interval_s": QUOTE_MIN_INTERVAL,
                        "note": "enforced by this probe (plan §2: 5 req/s data, "
                                "1 req/s quote)"},
    }


def exit_code_for(report: dict) -> int:
    return EXIT_UNEXPECTED_SHAPE if report.get("outcome") == "unexpected_shape" else 0


def main(argv: list[str] | None = None, *, creds: dict | None = None,
         fetcher=None, sleep=None) -> int:
    parser = argparse.ArgumentParser(
        description="Tier-2 Phase 0 preflight: measure what the broker serves "
                    "and print the disclosure. Writes nothing.")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST),
                        help=f"proof manifest pinning the vintages "
                             f"(default: {DEFAULT_MANIFEST})")
    parser.add_argument("--symbol", default=None,
                        help="roster symbol for the depth probe "
                             "(default: the earliest vintage's own symbol)")
    parser.add_argument("--security-id", default=None,
                        help="Dhan securityId for the depth probe — required; "
                             "this tool never guesses one")
    args = parser.parse_args(argv)

    creds = dict(os.environ) if creds is None else creds
    problem = credentials_problem(creds)
    if problem:
        print(f"ERR_NO_CREDENTIALS: {problem}", file=sys.stderr)
        print("  export DHAN_CLIENT_ID=... DHAN_ACCESS_TOKEN=... "
              "(see .env.example) and re-run; nothing was attempted.",
              file=sys.stderr)
        raise SystemExit(EXIT_NO_CREDENTIALS)
    problem = security_id_problem(args.security_id)
    if problem:
        print(f"ERR_NO_CREDENTIALS: {problem}", file=sys.stderr)
        raise SystemExit(EXIT_NO_CREDENTIALS)

    manifest = load_manifest(args.manifest)
    probes = plan_probes(manifest)
    if not probes:
        print("ERR_INPUT: the manifest pins no vintage to probe", file=sys.stderr)
        raise SystemExit(EXIT_NO_CREDENTIALS)
    symbol = args.symbol or probes[0]["symbol"]

    report = run_preflight(manifest=manifest, symbol=symbol,
                           security_id=args.security_id, creds=creds,
                           fetcher=fetcher or default_fetcher,
                           sleep=sleep or _sleep)
    sys.stdout.write(json.dumps(report, indent=2) + "\n")
    code = exit_code_for(report)
    if code:
        print(f"ERR_UNEXPECTED_SHAPE: a reachable vintage answered a body the "
              f"client cannot read — see `vintages` in the disclosure above.",
              file=sys.stderr)
        raise SystemExit(code)
    return 0


def _sleep(seconds: float) -> None:
    import time
    time.sleep(seconds)


if __name__ == "__main__":
    sys.exit(main())
