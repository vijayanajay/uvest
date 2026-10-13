#!/usr/bin/env python3
"""Hazard-class seed — the pledge/earnings golden records (EVAL-001 §2).

    python tests/evals/seed_hazard_records.py              # dry run
    python tests/evals/seed_hazard_records.py --apply

`meta.class_counts_expected` asks for 5 pledging traps and 7 impending-earnings
traps (the five catalog windows plus the two ADR-009 boundary probes). Neither class is gradable from stages 0–2 — gate 2 reads their breaker
out of `out_6_fundamental.json` — so while stage 6 had no engine their slots
stayed unseeded (honest [SKIP], never a hand-typed breaker value). Stage 6 now
exists, and the frozen catalog already documents exactly these situations, so
this tool closes the two classes end to end:

1. **Identities come from the immutable catalog.** Symbol, anchor, instrument,
   trap text and expected verdict are read out of `business_test_cases.md` §3
   through the scaffolder's own parser — never re-typed, so a seeded slot cannot
   drift from the frozen spec (E8).
2. **The hazard input is authored, disclosed, and never dressed as a filing.**
   Each row gets one committed `fixtures/<id>/financials.json`: the catalog's
   documented hazard as stage 6's governance / earnings block, and deliberately
   **empty statement blocks**. No offline source exists for these issuers' FY
   statements, so an invented Piotroski/Altman balance sheet is exactly what the
   repo forbids — stage 6's partial-input contract emits null ratio metrics with
   `missing_inputs` naming every gap instead (E4: absence is a first-class
   value). Where the catalog states the number (`pledge 32%`, `pledge 99%`,
   `results in 2 days`) the fixture uses it; where it only states the inequality
   (`pledge > 25%`) the fixture states a disclosed value above that floor.
3. **The evidence is a real stage stdout.** `scaffold_catalog` re-authors each
   row with its bundle in place, so `6_fundamental_audit.py --record` writes
   `out_6_fundamental.json` into the row's bundle, the row declares it
   `recorded`, and the row's pending stage-6 breaker moves into `gate_focus`
   (V11: a breaker id needs the artifact declared, so the two move together).
4. **The golden record is a view of the row.** One dataset record per row:
   `$ref` payloads into the recorded bundle (one copy, one provenance — E1) and
   `$derive` outcome claims resolved from the row's pinned forward tape at load.
   The tape, the row and the record therefore cannot disagree about what
   happened after T_rec.

What this tool does NOT do, and why:

* **It never clears the quarantine register.** Retiring a registered row is a
  separate, owner-visible act with its own tool
  (`tests/evals/retire_quarantine_records.py`, §8.3 of the schema doc) — this
  tool seeds new evidence and leaves the E5 meter exactly where it found it.
* **It never tunes a slot to the quota.** A row the tape blocks (the catalog
  says LOSS, the ≤T_rec-derived geometry says otherwise) stays unauthored and
  the shortfall is printed — the class is seeded from evidence or not at all.
* **It never re-freezes the catalog.** The roster is the catalog's own hazard
  rows; a missing one is reported, never invented.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "tests" / "evals"))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

import dataset_refs as dr  # noqa: E402  (the frozen resolution rules)
import scaffold_catalog as sc  # noqa: E402  (fixture/hash/baseline bookkeeping)

PROOF_ROOT = REPO_ROOT / "tests" / "proof"
GOLDEN = REPO_ROOT / "tests" / "evals" / "golden_dataset.json"
FIXTURE_SCHEMA = "uvest-fundamentals/1"
SEED_SOURCE = "catalog-hazard-seed/1"
# The dataset's roster summary is one note, replaced in place by each curation
# pass (curate.py's `31 of 52 records: …`, then this tool's), never stacked.
# The total is read from the pattern, not baked in, so a quota bump (ADR-009's
# 50→52) does not strand the note outside its own filter.
COUNT_NOTE_RE = re.compile(r"^\d+ of \d+ records")

# The roster: the catalog's own hazard scenarios, split to fill the two frozen
# quotas. Pledge (4 + the already-seeded VEDL legacy record = 5/5) and earnings
# (7/7: the five catalog windows — the §5 roll-up counts SCEN-017's unscheduled
# board meeting as the fifth binary event inside the window — plus the two
# ADR-009 boundary probes, SCEN-062 at 1 session and SCEN-063 at 6, each window
# pinned to the issuer's announced results date; SCEN-061's marginal-pledge cell
# was dropped under ADR-009 §3's refusal rule, never authored).
ROSTER: tuple[dict, ...] = (
    {"scenario_id": "SCEN-007", "trade_class": "pledge_hazard",
     "field": "promoter_pledged_pct", "value": 34.7,
     "documented": "catalog trap: \"Promoter pledge > 25%, offshore debt; "
                   "multi-day lower circuits\"",
     "rule": "the catalog documents the pledge above the 25% kill switch "
             "without its exact level, so the fixture states a disclosed value "
             "above that floor (authored input, not a filing)"},
    {"scenario_id": "SCEN-009", "trade_class": "pledge_hazard",
     "field": "promoter_pledged_pct", "value": 62.5,
     "documented": "catalog trap: \"Promoters pledged > 60%; lenders sold "
                   "collateral; -33% in 1 day\"",
     "rule": "the catalog documents the pledge above 60% without its exact "
             "level, so the fixture states a disclosed value above that floor "
             "(authored input, not a filing)"},
    {"scenario_id": "SCEN-011", "trade_class": "pledge_hazard",
     "field": "promoter_pledged_pct", "value": 32.0,
     "documented": "catalog trap: \"Rating downgrades, promoter pledging 32%, "
                   "cash flow deficit\"",
     "rule": "the catalog states the pledged share (32%), so the fixture uses "
             "it verbatim"},
    {"scenario_id": "SCEN-012", "trade_class": "pledge_hazard",
     "field": "promoter_pledged_pct", "value": 99.0,
     "documented": "catalog trap: \"Parent debt refinancing concerns; promoter "
                   "pledged 99% of stake\"",
     "rule": "the catalog states the pledged stake (99% of the promoter "
             "holding), so the fixture uses it verbatim"},
    {"scenario_id": "SCEN-013", "trade_class": "earnings_hazard",
     "field": "sessions_to_earnings", "value": 2,
     "documented": "catalog trap: \"Results in 2 days; guidance cut caused "
                   "immediate -10% gap-down\"",
     "rule": "the catalog states the window (2 sessions), so the fixture uses "
             "it verbatim"},
    {"scenario_id": "SCEN-014", "trade_class": "earnings_hazard",
     "field": "sessions_to_earnings", "value": 2,
     "documented": "catalog trap: \"IT rally into earnings; margin compression "
                   "triggered instant drop\"",
     "rule": "the catalog documents the pre-earnings window without a session "
             "count, so the fixture states the class's frozen 2-session window "
             "(authored input, not a board-meeting date)"},
    {"scenario_id": "SCEN-015", "trade_class": "earnings_hazard",
     "field": "sessions_to_earnings", "value": 2,
     "documented": "catalog trap: \"Q3 results in 2 days; NIM compression "
                   "caused -8.5% single-day fall\"",
     "rule": "the catalog states the window (2 sessions), so the fixture uses "
             "it verbatim"},
    {"scenario_id": "SCEN-016", "trade_class": "earnings_hazard",
     "field": "sessions_to_earnings", "value": 3,
     "documented": "catalog trap: \"Earnings shock inside 3 days; dividend "
                   "payout couldn't stem drop\"",
     "rule": "the catalog states the window (3 sessions), so the fixture uses "
             "it verbatim"},
    {"scenario_id": "SCEN-017", "trade_class": "earnings_hazard",
     "field": "sessions_to_earnings", "value": 2,
     "documented": "catalog trap: \"Unscheduled board meeting for acquisition "
                   "funding; -10% selloff\" (the catalog's fifth binary-event "
                   "row, per its §5 hazard roll-up: 5/5)",
     "rule": "the catalog documents the unscheduled event without a session "
             "count, so the fixture states the class's frozen 2-session window "
             "(authored input, not a board-meeting date)"},
    {"scenario_id": "SCEN-062", "trade_class": "earnings_hazard",
     "field": "sessions_to_earnings", "value": 1,
     "documented": "catalog trap: \"Results announced 1 trading session after "
                   "T_rec; pre-release drift pierced the crisis stop at that "
                   "session's open\"",
     "rule": "the catalog states the window (1 session), pinned to the issuer's "
             "announced NSE results date (TCS: after market 2024-07-11), so the "
             "fixture uses it verbatim (window pinning per ADR-008, never an "
             "authored default)"},
    {"scenario_id": "SCEN-063", "trade_class": "earnings_hazard",
     "field": "sessions_to_earnings", "value": 6,
     "documented": "catalog trap: \"Results announced 6 trading sessions after "
                   "T_rec (17 Jul Moharram holiday); pre-release drift struck "
                   "the stop first\"",
     "rule": "the catalog states the window (6 sessions), pinned to the issuer's "
             "announced NSE results date (INFY: after market 2024-07-18; "
             "2024-07-17 was the Moharram holiday, so weekday counting would "
             "wrongly say 7), so the fixture uses it verbatim (window pinning "
             "per ADR-008, never an authored default)"},
)

CLASS_BREAKER = {"pledge_hazard": "promoter_pledging_gt_25pct",
                 "earnings_hazard": "earnings_within_7d"}

# The class invariant each seeded row's recorded bytes must satisfy (the same
# projection gate 2 grades).
CLASS_CHECK = {
    "pledge_hazard": lambda payload: (
        payload.get("promoter_pledge_pct") is not None
        and payload["promoter_pledge_pct"] > 25.0
        and payload.get("pledge_circuit_breaker") is True),
    "earnings_hazard": lambda payload: (
        isinstance(payload.get("days_to_earnings"), int)
        and 0 <= payload["days_to_earnings"] < 7
        and payload.get("earnings_hazard_flag") is True),
}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class Blocked(Exception):
    """The evidence cannot support the slot — report it, never invent it."""


def disclosure_of(entry: dict) -> str:
    """The one sentence a manifest reader sees about the authored hazard input."""
    return (f"Authored hazard input ({entry['field']}={entry['value']}): "
            f"{entry['rule']}. {entry['documented']}. The statement blocks are "
            "deliberately empty — no offline source exists for the issuer's FY "
            "statements, so stage 6 emits null ratio metrics with "
            "`missing_inputs` naming every gap (E4: absence, never an invented "
            "filing).")


def fixture_doc(entry: dict, spec: dict) -> dict:
    """The stage-6 input bundle for one row: the hazard block and nothing else."""
    document = {
        "schema": FIXTURE_SCHEMA,
        "symbol": spec["symbol"],
        "exchange": "NSE",
        "source": SEED_SOURCE,
        # Point-in-time: the bundle is stated as of T_rec, so it can never carry
        # a fact the row's tape could not have seen.
        "as_of": spec["anchor"],
        "units": "INR_CRORE",
        "statement_fy_prior": {},
        "statement_fy_current": {},
        "governance": {},
        "earnings": {},
        "authoring": {
            "source": SEED_SOURCE,
            "catalog": "business_test_cases.md §3",
            "scenario_id": entry["scenario_id"],
            "trade_class": entry["trade_class"],
            "catalog_name": spec["name"],
            "trap": spec["trap"],
            # The breaker the row is authored to grade (scaffold_catalog moves it
            # into `gate_focus` when it declares the stage-6 artifact), and the
            # class the gate reads it for.
            "breaker": CLASS_BREAKER[entry["trade_class"]],
            "breaker_stage": 6,
            "hazard_field": entry["field"],
            "hazard_value": entry["value"],
            "documented": entry["documented"],
            "rule": entry["rule"],
            "disclosure": disclosure_of(entry),
        },
    }
    if entry["trade_class"] == "pledge_hazard":
        document["governance"] = {"promoter_pledged_pct": entry["value"]}
    else:
        document["earnings"] = {"sessions_to_earnings": entry["value"]}
    return document


def write_fixture(entry: dict, spec: dict, *, force: bool) -> tuple[Path, str]:
    """Author the bundle unless one is already committed (or --force is given)."""
    path = PROOF_ROOT / "fixtures" / entry["scenario_id"] / sc.FINANCIALS_FILE
    if path.exists() and not force:
        return path, "existing bundle kept"
    sc.write_json(path, fixture_doc(entry, spec))
    return path, "authored"


def check_recorded_evidence(entry: dict, row: dict) -> list[str]:
    """The recorded stage-6 bytes must satisfy the class invariant they grade."""
    sid = entry["scenario_id"]
    cls = entry["trade_class"]
    declared = [a for a in row.get("recorded_artifacts") or []
                if a.get("artifact") == "out_6_fundamental.json"]
    if len(declared) != 1 or declared[0].get("evidence") != "recorded":
        raise Blocked(f"{sid}: the row declares no single recorded stage-6 artifact")
    payload = read_json(PROOF_ROOT / declared[0]["path"])
    if payload.get("waived"):
        raise Blocked(f"{sid}: the recorded stage-6 payload is a waiver — the "
                      "hazard block did not reach the engine")
    if not CLASS_CHECK[cls](payload):
        raise Blocked(f"{sid}: recorded stage-6 bytes do not carry the {cls} "
                      f"breaker ({CLASS_BREAKER[cls]}): {payload}")
    if cls == "pledge_hazard" and payload.get("promoter_pledge_pct") != entry["value"]:
        raise Blocked(f"{sid}: recorded pledge {payload.get('promoter_pledge_pct')!r} "
                      f"is not the authored {entry['value']!r}")
    if cls == "earnings_hazard":
        if row["instrument"] != "FUT":
            raise Blocked(f"{sid}: the earnings hazard is a Futures rule; the row "
                          f"is {row['instrument']}")
        if payload.get("days_to_earnings") != entry["value"]:
            raise Blocked(f"{sid}: recorded days {payload.get('days_to_earnings')!r} "
                          f"is not the authored {entry['value']!r}")
    breaker = CLASS_BREAKER[cls]
    if breaker not in (row.get("gate_focus") or []):
        raise Blocked(f"{sid}: gate_focus does not carry {breaker}")
    if "breaker_pending_stage_6" in (row.get("authoring") or {}):
        raise Blocked(f"{sid}: the row still carries breaker_pending_stage_6")
    # The statements are absent on purpose: the nulls must name their obstacle.
    if not payload.get("missing_inputs"):
        raise Blocked(f"{sid}: empty statements recorded without missing_inputs")
    return [f"{CLASS_BREAKER[cls]} graded from recorded stage-6 bytes "
            f"({entry['field']}={entry['value']})",
            f"statement-derived metrics null, {len(payload['missing_inputs'])} "
            "missing input(s) named"]


def build_record(entry: dict, row: dict) -> dict:
    """The dataset record: a view of the row (refs for bytes, derives for tape)."""
    sid = entry["scenario_id"]
    scoring = row.get("scoring") or {}
    tape_rel = scoring.get("forward_tape")
    if not tape_rel:
        raise Blocked(f"{sid}: the row pins no forward tape")
    bars = read_json(PROOF_ROOT / tape_rel)["bars"]
    outcome, basis = dr.resolution_of(bars, float(row["stop_loss"]),
                                      float(row["target_price"]))
    if outcome != scoring.get("resolution_outcome") or basis != scoring.get("resolution_basis"):
        raise Blocked(f"{sid}: the frozen rule reads {outcome}/{basis} while the "
                      f"row stored {scoring.get('resolution_outcome')}/"
                      f"{scoring.get('resolution_basis')}")
    # Same rule objects the dataset's `$derive` resolves through, so the record
    # cannot claim what the row's own scoring block does not.
    rule = dr.FORWARD_TAPE_RULES
    if rule["forward_min_max"](bars, row) != scoring.get("forward_derived_cache"):
        raise Blocked(f"{sid}: derived forward range disagrees with the row's cache")
    if rule["resolution_date"](bars, row) != scoring.get("resolution_date"):
        raise Blocked(f"{sid}: derived horizon end disagrees with the row's")

    tape = f"tests/proof/{tape_rel}"

    def ref(artifact: str) -> dict:
        return {"$ref": f"tests/proof/fixtures/{sid}/recorded/{artifact}"}

    def derive(rule: str) -> dict:
        return {"$derive": {"tape": tape, "rule": rule}}

    return {
        "trade_id": sid,
        "trade_class": entry["trade_class"],
        "symbol": row["symbol"],
        "instrument": row["instrument"],
        "recommendation_date": row["recommendation_date"],
        "resolution_date": derive("resolution_date"),
        "resolution_outcome": derive("resolution_outcome"),
        "entry_price": row["entry_price"],
        "stop_loss": row["stop_loss"],
        "target_price": row["target_price"],
        "script_outputs": {
            "out_1_math.json": ref("out_1_math.json"),
            "out_2_market.json": ref("out_2_market.json"),
            "out_6_fundamental.json": ref("out_6_fundamental.json"),
        },
        "forward_prices_22d": derive("forward_min_max"),
        "cone_p10": None,
        "cone_p90": None,
        "market_mae_pct": derive("market_mae_pct"),
        "limit_mae_pct": None,
        "predicted_direction": None,
        "actual_direction": derive("realized_direction"),
    }


def dataset_note(records: list[dict], blocked: list[str], seeded: int, expected: int) -> str:
    pledge = sorted(r["trade_id"] for r in records if r["trade_class"] == "pledge_hazard")
    earnings = sorted(r["trade_id"] for r in records if r["trade_class"] == "earnings_hazard")
    return (
        f"{seeded} of {expected} records. The pledge ("
        + ", ".join(pledge)
        + ") and earnings (" + ", ".join(earnings)
        + ") golden records are views of the frozen catalog's own hazard rows "
        "(business_test_cases.md §3) and carry recorded stage-6 evidence: real "
        "`6_fundamental_audit.py --record` stdout over an authored hazard bundle "
        "(tests/proof/fixtures/<id>/financials.json), so gate 2 grades both "
        "breakers (`promoter_pledging_gt_25pct`, `earnings_within_7d`) from proven "
        "bytes instead of abstaining. The hazard inputs are authored and disclosed "
        "per row (`authoring.hazard_evidence`, copied from the bundle, and "
        "`authoring.hazard_inputs` in the fixture): the catalog's documented level "
        "is used verbatim where it states one, and a disclosed value above the "
        "documented floor where it states only the inequality. The statement "
        "blocks are deliberately empty, so stage 6 emits null Piotroski/Altman/"
        "CFO-PAT metrics with `missing_inputs` naming every gap — absence, never an "
        "invented filing (E4). GB-2023-0003's legacy pledge payload was retired "
        "into recorded stage-6 evidence (tests/evals/retire_quarantine_records.py, "
        "E5 meter 0), so the pledge class grades 5/5 and gate 2 has no abstention "
        "left; institutional_distribution 0/10 remains unauthored. Authored by "
        "tests/evals/seed_hazard_records.py."
        + ((" Slot(s) left unauthored because the point-in-time tape contradicts "
            "the catalog's realized outcome under the frozen class geometry: "
            + ", ".join(blocked)
            + " — a finding for the owner, resolvable only by an explicit catalog "
              "re-freeze, never by a geometry tuned to fit the claim (E8).")
           if blocked else "")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seed the pledge/earnings golden records with stage-6 evidence.")
    parser.add_argument("--apply", action="store_true",
                        help="Write fixtures, rows, hashes, baseline and the dataset.")
    parser.add_argument("--force", action="store_true",
                        help="Re-author a financials fixture that already exists.")
    parser.add_argument("--scenarios", default=None,
                        help="Comma-separated subset of the roster (default: all).")
    args = parser.parse_args(argv)

    catalog = sc.parse_catalog()
    unknown = [e["scenario_id"] for e in ROSTER if e["scenario_id"] not in catalog]
    if unknown:
        print(f"❌ not in the frozen catalog: {unknown}", file=sys.stderr)
        return 2
    roster = list(ROSTER)
    if args.scenarios:
        wanted = [s.strip() for s in args.scenarios.split(",") if s.strip()]
        off_roster = [s for s in wanted if s not in {e["scenario_id"] for e in ROSTER}]
        if off_roster:
            print(f"❌ not on the hazard roster: {off_roster}", file=sys.stderr)
            return 2
        roster = [e for e in ROSTER if e["scenario_id"] in wanted]

    print("=" * 72)
    print("HAZARD SEED PLAN — identities from the frozen catalog, hazards disclosed")
    print("=" * 72)
    for entry in roster:
        spec = catalog[entry["scenario_id"]]
        print(f"  {entry['scenario_id']} [{entry['trade_class']}] {spec['symbol']} "
              f"{spec['instrument']} @ {spec['anchor']}")
        print(f"    hazard: {entry['field']} = {entry['value']} — {entry['rule']}")
        print(f"    catalog: {entry['documented']}")

    if not args.apply:
        print(f"\nDRY RUN — {len(roster)} slot(s) would be seeded; nothing written")
        return 0

    today = date.today().isoformat()
    for entry in roster:
        spec = catalog[entry["scenario_id"]]
        _path, state = write_fixture(entry, spec, force=args.force)
        print(f"  {entry['scenario_id']}: {sc.FINANCIALS_FILE} {state}")

    # The scaffolder owns row authoring: it re-derives the geometry from the
    # catalog + tape, records stages 1–2 (and now 6) and republishes hashes and
    # the re-frozen baseline.
    print("\nauthoring rows through the catalog scaffolder…")
    rc = sc.main(["--scenarios", ",".join(e["scenario_id"] for e in roster)])
    if rc != 0:
        print(f"❌ scaffolder exited {rc}; nothing further written", file=sys.stderr)
        return rc

    manifest = read_json(PROOF_ROOT / sc.MANIFEST_FILE)
    rows = {row["scenario_id"]: row for row in manifest["scenarios"]}
    records: list[dict] = []
    checks: list[str] = []
    blocked: list[str] = []
    for entry in roster:
        sid = entry["scenario_id"]
        row = rows.get(sid)
        if row is None:
            # The scaffolder refused the row: its tape disagrees with the
            # catalog's realized outcome under the frozen class geometry. That
            # is a *finding* about the catalog, reported to the owner — the
            # slot stays unauthored and the class shortfall is disclosed,
            # never filled by a geometry tuned to fit the claim (E8).
            blocked.append(sid)
            print(f"⛔ {sid}: no row published — the ≤T_rec tape contradicts the "
                  "catalog's realized outcome under the frozen geometry; the slot "
                  "stays unauthored (the scaffolder printed the evidence)")
            continue
        try:
            checks.extend(f"{sid}: {line}"
                          for line in check_recorded_evidence(entry, row))
            record = build_record(entry, row)
        except Blocked as exc:
            # A row that exists but whose evidence cannot be read is *our*
            # defect, not the tape's: nothing is written.
            print(f"❌ {exc}", file=sys.stderr)
            print("   nothing written; the dataset is unchanged", file=sys.stderr)
            return 1
        records.append(record)
    for line in checks:
        print(f"  ✓ {line}")

    data = read_json(GOLDEN)
    trades = {t["trade_id"]: t for t in data["trades"]}
    for record in records:
        trades[record["trade_id"]] = record
    data["trades"] = [trades[k] for k in sorted(trades)]
    data["meta"]["notes"] = [n for n in data["meta"]["notes"]
                             if not COUNT_NOTE_RE.match(n.lstrip())]
    data["meta"]["notes"].append(dataset_note(
        records, blocked, len(data["trades"]),
        data["meta"].get("total_trades_expected", 50)))
    sc.write_json(GOLDEN, data)

    counts: dict[str, int] = {}
    for trade in data["trades"]:
        counts[trade["trade_class"]] = counts.get(trade["trade_class"], 0) + 1
    print(f"\nseeded {len(records)} record(s); dataset now {len(data['trades'])}/"
          f"{data['meta'].get('total_trades_expected')} across "
          f"{len(counts)} class(es)")
    for cls_name, expected in data["meta"]["class_counts_expected"].items():
        mark = "✓" if counts.get(cls_name, 0) == expected else "·"
        print(f"  {mark} {cls_name}: {counts.get(cls_name, 0)}/{expected}")
    if blocked:
        print(f"  ⚠ {len(blocked)} slot(s) left unauthored (tape vs catalog, "
              f"disclosed): {', '.join(blocked)}")
    print(f"  authored on {today}; quarantine register untouched (E5 meter "
          f"unchanged); hashes and baseline re-frozen by the scaffolder")
    return 0


if __name__ == "__main__":
    sys.exit(main())
