#!/usr/bin/env python3
"""Catalog scaffolder — author frozen-catalog scenarios as proof-root rows.

    python tests/evals/scaffold_catalog.py --scenarios SCEN-002,SCEN-003
    python tests/evals/scaffold_catalog.py --scenarios SCEN-002 --dry-run

Per scenario, in the given order:

1. **Expectations come from the immutable catalog.** Anchor date, symbol,
   instrument, target verdict and realized outcome/quadrant are read straight
   out of `business_test_cases.md` §3 — never re-typed, so a row cannot drift
   from the frozen spec.
2. **Tape is real and point-in-time.** Daily bars are fetched once per
   symbol-month through the curator's cached path (`tests/fixtures/history/`),
   truncated at `T_rec`, with the next 22 sessions kept as the forward tape.
3. **Geometry is authored from ≤T_rec tape under a frozen policy** (POLICY
   below), then stages 1–2 are re-run through the repo's own `--record`
   mechanism — so every graded artifact is a real stage stdout, hashed at
   authoring time.
4. **A row whose tape-derived outcome disagrees with the catalog is BLOCKED,
   never tuned.** The geometry policy is chosen per catalog class and never per
   scenario; if it cannot reproduce the frozen outcome, the slot stays
   unauthored and the evidence is printed for the owner. The only resolution is
   an explicit owner-commanded catalog re-freeze (as SCEN-001/005 rev. R1) —
   never a geometry tuned to fit the claim (E8: expectations are point-in-time
   artifacts, and the tape is not negotiable).
5. Fixtures + row are written, `manifest.sha256` regenerated, and the baseline
   digests deliberately re-frozen (V12) in the same change.

What this tool knowingly does NOT author, and why it is disclosed instead:

* **Futures contract specifics.** `expiry` follows the real NSE monthly
  convention (last Thursday, rolled when the anchor is within 7 calendar days);
  `lot_size` cannot be sourced offline, so it is derived from a frozen ₹5L
  notional band and the derivation is recorded in `authoring.lot_size_rule`.
* **Fundamental inputs.** Stage 6's statement/governance/earnings bundle is not
  in the catalog: a row that has a committed `fixtures/<id>/financials.json`
  (authored by `seed_hazard_records.py` from the catalog's documented trap) gets
  its breaker **graded**, and a row without one keeps the intended breaker under
  `authoring.breaker_pending_stage_6` rather than claiming gate evidence it
  cannot have — a `gate_focus` breaker id whose stage artifact is undeclared is
  a V11 error.

**Stage-6 evidence is a row property, not a curator's afterthought.** When a
scenario has a financials bundle, this tool (a) declares
`out_6_fundamental.json` as recorded evidence, (b) moves the pending stage-6
breaker into `gate_focus`, and (c) re-runs `6_fundamental_audit.py --record` so
the declared bytes are a real stage stdout. So re-authoring a promoted row
reproduces its hazard evidence instead of silently dropping it, and the
breaker's gate evidence can never be a hand-typed payload (E1).
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import re
import shutil
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "tests" / "evals"))

import curate  # noqa: E402  (the curator's fetch cache + frozen ATR convention)
import dataset_refs as dr  # noqa: E402  (the frozen resolution rule, shared with $derive)

CATALOG_FILE = REPO_ROOT / "business_test_cases.md"
PROOF_ROOT = REPO_ROOT / "tests" / "proof"
SCRIPTS = REPO_ROOT / "scripts"

MANIFEST_FILE = "scenario_manifest.json"
HASH_FILE = "manifest.sha256"
BASELINE_FILE = "baseline_scorecard.json"
REGISTER_FILE = "quarantine_register.json"
SCHEMA_NAME = "uvest-scenario-manifest/1"
ALLOWLIST = (MANIFEST_FILE, HASH_FILE, BASELINE_FILE, REGISTER_FILE)
ALLOWED_PREFIXES = ("_replay/",)

FORWARD = curate.FORWARD              # 22 sessions (BRD §5.1)
MIN_SESSIONS = curate.MIN_SESSIONS    # 200 (TICK-013 floor)
FINANCIALS_FILE = "financials.json"   # authored stage-6 input bundle (per scenario)
USER_CAPITAL_INR = 500000.0
LOT_NOTIONAL_INR = 500000.0           # frozen lot-size derivation band
CRLF = "\r\n"

VERDICTS = ("GO", "GO_WEAK", "WAIT", "AVOID", "HOLD", "PRESSURE", "EXIT")

# ---------------------------------------------------------------------------
# Frozen geometry policy (per catalog class — never per scenario)
# ---------------------------------------------------------------------------
POLICY = {
    "crisis": {
        "entry_basis": "open",
        "risk_atr": 0.50,
        "rr": 2.0,
        "why": (
            "an AVOID-class advisory is issued intraday, so the authored entry is the "
            "T_rec session OPEN — the price visible before the collapse — with the tight "
            "stop the catalog itself documents for advisories (SCEN-028 0.32x ATR, "
            "SCEN-029 0.55x ATR); a row whose tape then rallies records the honest "
            "false negative (§4.1), never a tuned loss"
        ),
    },
    "trend": {
        "entry_basis": "close",
        "risk_atr": 2.00,
        "rr": 2.2,
        "why": (
            "a GO-class trend entry at the T_rec close with a 2x ATR noise buffer and a "
            "2.2R target — the golden dataset's clean shape"
        ),
    },
}

# Catalog breaker intent, recorded as pending where the owning engine is absent.
PENDING_BREAKERS = {
    "SCEN-001": ("stage_7", "panic_regime"),
    "SCEN-005": ("stage_7", "panic_regime"),
    "SCEN-007": ("stage_6", "promoter_pledging_gt_25pct"),
    "SCEN-011": ("stage_6", "promoter_pledging_gt_25pct"),
    "SCEN-009": ("stage_6", "promoter_pledging_gt_25pct"),
    "SCEN-010": ("stage_6", "promoter_pledging_gt_25pct"),
    "SCEN-012": ("stage_6", "promoter_pledging_gt_25pct"),
    "SCEN-013": ("stage_6", "earnings_within_7d"),
    "SCEN-002": ("stage_7", "panic_regime"),
    "SCEN-003": ("stage_7", "panic_regime"),
    "SCEN-004": ("stage_7", "panic_regime"),
    "SCEN-006": ("stage_7", "panic_regime"),
    "SCEN-008": ("stage_7", "panic_regime"),
}

REGIME_TAGS = {"SCEN-002": ["vix_panic"]}

# Issuer renames (owner-accepted for SCEN-011): the cache and the row keep the
# point-in-time symbol; only the network fetch uses the surviving ticker.
TAPE_SOURCE_TICKER = {
    "IBULHSGFIN": (
        "SAMMAANCAP",
        "issuer renamed (Indiabulls Housing Finance -> Sammaan Capital, 2024); the "
        "catalog symbol is kept as the point-in-time identity",
    ),
}


def load_tape(symbol: str, anchor: str):
    """The symbol's cached tape, fetched via its surviving ticker if renamed."""
    override = TAPE_SOURCE_TICKER.get(symbol)
    return curate.load_history(symbol, anchor,
                               fetch_symbol=override[0] if override else None)


def financials_fixture(scenario_id: str) -> Path | None:
    """The scenario's committed stage-6 input bundle, or None when it has none."""
    path = PROOF_ROOT / "fixtures" / scenario_id / FINANCIALS_FILE
    return path if path.exists() else None


def declare_hazard_evidence(row: dict, spec: dict) -> None:
    """Turn a committed financials bundle into declared, graded stage-6 evidence.

    Two things move together, or neither: the artifact declaration (so the
    payload is proven, never typed) and the `gate_focus` breaker id the bundle
    says it was authored to exercise (so the row states *what it exists to
    grade*). V11 pairs them — a breaker id with no declared stage-6 artifact is
    an error — which is exactly why the pending form existed while stage 6 was
    unimplemented, and why a bundle that names no breaker leaves that pending
    intent alone instead of promoting a guessed id. The bundle's own disclosure
    is copied onto the row, so a manifest reader sees the authored inputs
    without opening the fixture.
    """
    sid = row["scenario_id"]
    fixture = financials_fixture(sid)
    if fixture is None:
        return
    try:
        bundle = read_json(fixture)
    except (OSError, json.JSONDecodeError):
        print(f"  {sid}: {FINANCIALS_FILE} is unreadable; hazard evidence not declared")
        return
    row["recorded_artifacts"].append(
        {"stage": 6, "artifact": "out_6_fundamental.json",
         "path": f"fixtures/{sid}/recorded/out_6_fundamental.json",
         "evidence": "recorded"})
    row["replay_inputs"]["financials_fixture"] = f"fixtures/{sid}/{FINANCIALS_FILE}"
    # The bundle names the breaker it was authored to exercise; without that
    # declaration the row keeps its pending intent rather than guessing one.
    authored = (bundle.get("authoring") or {})
    breaker = authored.get("breaker")
    if breaker:
        row["gate_focus"].append(breaker)
        row["authoring"].pop("breaker_pending_stage_6", None)
    row["authoring"]["hazard_evidence"] = {
        "fixture": f"fixtures/{sid}/{FINANCIALS_FILE}",
        "breaker": breaker,
        "hazard_field": authored.get("hazard_field"),
        "hazard_value": authored.get("hazard_value"),
        "documented": authored.get("documented"),
        "disclosure": authored.get("disclosure"),
    }


CATALOG_ROW_RE = re.compile(r"^\|\s*\*\*(SCEN-\d{3})\*\*\s*\|")


def policy_for(expected_verdict: str, realized_outcome: str) -> dict:
    if expected_verdict == "AVOID" and realized_outcome in ("LOSS", "WIN"):
        return POLICY["crisis"]
    if expected_verdict in ("GO", "GO_WEAK") and realized_outcome == "WIN":
        return POLICY["trend"]
    raise SystemExit(
        f"no frozen geometry policy covers {expected_verdict}/{realized_outcome}: add one "
        "deliberately (with its rationale) rather than tuning a single scenario"
    )


# ---------------------------------------------------------------------------
# Catalog + fixture I/O
# ---------------------------------------------------------------------------
def cells_of(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def bare(cell: str) -> str:
    """Strip markdown decorations and emoji from a catalog cell."""
    return re.sub(r"[^A-Z_]", "", cell.replace("**", "").replace("`", "")).strip("_")


def parse_catalog() -> dict[str, dict]:
    """The frozen catalog table: SCEN id -> its spec."""
    rows: dict[str, dict] = {}
    for line in CATALOG_FILE.read_text(encoding="utf-8").splitlines():
        if not CATALOG_ROW_RE.match(line):
            continue
        cells = cells_of(line)
        sid = cells[0].strip("*` ")
        rows[sid] = {
            "scenario_id": sid,
            "anchor": cells[1],
            "symbol": cells[2],
            "instrument": cells[3],
            "name": cells[4],
            "trap": cells[5],
            "expected_verdict": bare(cells[6]),
            "realized_outcome": bare(cells[7]),
            "quadrant_target": bare(cells[8]),
        }
    return rows


def write_text_utf8(path: Path, text: str) -> None:
    """Proof-root files are CRLF on disk (recorder text mode on Windows)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.replace("\r\n", "\n").replace("\n", CRLF).encode("utf-8"))


def write_json(path: Path, obj, *, indent: int | None = 2) -> None:
    text = json.dumps(obj, indent=indent, ensure_ascii=False)
    write_text_utf8(path, text + "\n")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Authoring derivations
# ---------------------------------------------------------------------------
def atr14(sessions: list[dict]) -> float:
    return round(curate.atr14_simple_mean(sessions), 6)


def nse_monthly_expiry(anchor: str) -> str:
    """Last-Thursday monthly expiry, rolled when the anchor is within 7 days.

    (Validated against the frozen schema doc's worked example: SCEN-032's
    2021-04-15 anchor yields 2021-04-29.)
    """
    day = date.fromisoformat(anchor)

    def last_thursday(year: int, month: int) -> date:
        found = date(year, month, calendar.monthrange(year, month)[1])
        while found.weekday() != 3:  # Thursday
            found -= timedelta(days=1)
        return found

    expiry = last_thursday(day.year, day.month)
    if (expiry - day).days < 7:
        nxt = date(day.year + (day.month == 12), (day.month % 12) + 1, 1)
        expiry = last_thursday(nxt.year, nxt.month)
    return expiry.isoformat()


def derive_lot_size(entry: float) -> int:
    """Frozen ₹5L notional band, rounded to a 25-share grid (disclosed)."""
    lots = int(LOT_NOTIONAL_INR // entry)
    return max(25, int(round(lots / 25.0)) * 25)


def resolve(forward: list[dict], target: float, stop: float) -> tuple[str, str]:
    """Tape-derived outcome + the honest basis — the shared frozen rule.

    `dataset_refs.resolution_of` decides (first touch wins; a bar carrying both
    levels is LOSS), so the scaffolder's catalog comparison cannot disagree with
    what a row's outcome will later derive to.
    """
    return dr.resolution_of(forward, stop, target)


# ---------------------------------------------------------------------------
# Row build
# ---------------------------------------------------------------------------
def build_row(spec: dict, *, today: str) -> dict | None:
    symbol = spec["symbol"]
    anchor = spec["anchor"]
    sessions = load_tape(symbol, anchor)
    if sessions is None:
        print(f"  {spec['scenario_id']}: no usable point-in-time tape for {symbol}")
        return None
    prefix, forward = curate.split_history(sessions, anchor)
    if len(prefix) < MIN_SESSIONS or len(forward) < FORWARD:
        print(f"  {spec['scenario_id']}: history too short "
              f"({len(prefix)} prefix / {len(forward)} forward)")
        return None

    policy = policy_for(spec["expected_verdict"], spec["realized_outcome"])
    entry = round(float(prefix[-1][policy["entry_basis"]]), 2)
    atr = atr14(prefix)
    stop = round(entry - policy["risk_atr"] * atr, 2)
    target = round(entry + policy["rr"] * (entry - stop), 2)
    if stop <= 0 or target <= entry:
        print(f"  {spec['scenario_id']}: non-positive geometry derived (stop {stop})")
        return None

    outcome, basis = resolve(forward, target, stop)
    if outcome != spec["realized_outcome"]:
        low = min(b["low"] for b in forward)
        high = max(b["high"] for b in forward)
        print(f"  {spec['scenario_id']}: BLOCKED — catalog says "
              f"{spec['realized_outcome']}, {policy['entry_basis']}-anchored "
              f"{policy['risk_atr']}x ATR geometry on real tape says {outcome}")
        print(f"      entry {entry} stop {stop} target {target} | forward "
              f"[{low}, {high}] | a {spec['realized_outcome']} would need the "
              f"opposite level touched")
        return None

    band_min, band_max = curate.entry_band(entry)
    sid = spec["scenario_id"]
    row: dict = {
        "scenario_id": sid,
        "schema_version": SCHEMA_NAME,
        "origin": "curated_behavioral",
        "created_on": today,
        "authoring": {
            "source": "catalog-scaffold/1",
            "catalog": "business_test_cases.md §3",
            "catalog_name": spec["name"],
            "policy": policy["why"],
            "entry_basis": policy["entry_basis"],
            "shape": {"risk_atr": policy["risk_atr"], "rr": policy["rr"]},
            "atr_14": atr,
            "expiry_rule": ("NSE monthly last-Thursday, rolled when within 7 days of "
                            "T_rec" if spec["instrument"] == "FUT" else None),
            "lot_size_rule": (f"₹{int(LOT_NOTIONAL_INR)} notional band on the authored "
                              "entry, rounded to a 25-share grid; historical NSE lot "
                              "revisions are not archived offline"),
            "realized_outcome_basis": basis,
        },
        "classification_tags": ["CATALOG"],
        "gate_focus": ["S3", "S4", "S6"],
        "symbol": symbol,
        "exchange": "NSE",
        "instrument": spec["instrument"],
        "action": "BUY",
        "recommendation_date": anchor,
        "entry_price": entry,
        "entry_range_min": band_min,
        "entry_range_max": band_max,
        "target_price": target,
        "stop_loss": stop,
        "lot_size": derive_lot_size(entry),
        "user_capital_inr": USER_CAPITAL_INR,
        "replay_inputs": {
            "canonical_trade": f"fixtures/{sid}/current_trade.json",
            "truncated_fixtures": [f"fixtures/{sid}/ohlcv_prefix.json"],
            "truncation": {"cutoff": anchor, "rule": "date <= T_rec", "min_bars": MIN_SESSIONS},
        },
        "recorded_artifacts": [
            {"stage": 1, "artifact": "out_1_math.json",
             "path": f"fixtures/{sid}/recorded/out_1_math.json", "evidence": "recorded"},
            {"stage": 2, "artifact": "out_2_market.json",
             "path": f"fixtures/{sid}/recorded/out_2_market.json", "evidence": "recorded"},
        ],
        "scoring": {
            "forward_tape": f"fixtures/{sid}/forward_tape.json",
            "bars": len(forward),
            "resolution_date": forward[-1]["date"],
            "resolution_outcome": outcome,
            "resolution_basis": basis,
            "forward_derived_cache": {
                "min": min(b["low"] for b in forward),
                "max": max(b["high"] for b in forward),
            },
            "quadrant_target": spec["quadrant_target"],
        },
        "expected_verdict": spec["expected_verdict"],
    }
    if spec["instrument"] == "FUT":
        row["expiry"] = nse_monthly_expiry(anchor)
    if sid in REGIME_TAGS:
        row["scoring"]["regime_tags"] = REGIME_TAGS[sid]
    if sid in PENDING_BREAKERS:
        stage, breaker = PENDING_BREAKERS[sid]
        row["authoring"][f"breaker_pending_{stage}"] = [breaker]
    row["authoring"]["catalog_trap"] = spec["trap"]
    # A committed financials bundle promotes the pending stage-6 breaker into
    # graded evidence (declaration + gate_focus + the stage-6 recording below).
    declare_hazard_evidence(row, spec)
    if symbol in TAPE_SOURCE_TICKER:
        fetched, reason = TAPE_SOURCE_TICKER[symbol]
        row["authoring"]["tape_source"] = {
            "requested": f"{symbol}.NS",
            "fetched": f"{fetched}.NS",
            "reason": reason,
        }
    return row


def canonical_trade(row: dict) -> dict:
    return {
        "symbol": row["symbol"],
        "exchange": row["exchange"],
        "instrument_type": row["instrument"],
        **({"expiry": row["expiry"]} if "expiry" in row else {}),
        "action": row["action"],
        "entry_price": row["entry_price"],
        "entry_range_min": row["entry_range_min"],
        "entry_range_max": row["entry_range_max"],
        "live_price": row["entry_price"],
        "target": row["target_price"],
        "stoploss": row["stop_loss"],
        "lot_size": row["lot_size"],
        "margin": row["lot_size"] * row["entry_price"],
        "recommended_date": row["recommendation_date"],
        "app_warning": "catalog scaffold",
        "user_capital_inr": row["user_capital_inr"],
    }


def record_stages(row: dict) -> bool:
    """Run stages 1–2 (and 6, when the scenario has a bundle) through --record.

    The row's evidence is real stdout: every declared artifact is a stage's own
    output over the row's point-in-time fixtures, recorded in one bundle.
    """
    sid = row["scenario_id"]
    fixture_dir = PROOF_ROOT / "fixtures" / sid
    bundle = fixture_dir / "recorded"
    if bundle.exists():
        shutil.rmtree(bundle)
    canonical = fixture_dir / "current_trade.json"
    prefix = fixture_dir / "ohlcv_prefix.json"
    stages = [
        ("1_math_and_rr.py", ["--input", str(canonical), "--record", str(bundle)]),
        ("2_market_data.py", ["--input", str(canonical), "--ohlcv-fixture", str(prefix),
                              "--record", str(bundle)]),
    ]
    fundamentals = financials_fixture(sid)
    if fundamentals is not None:
        stages.append(
            ("6_fundamental_audit.py", ["--input", str(canonical),
                                        "--financials-fixture", str(fundamentals),
                                        "--record", str(bundle)]))
    for script, argv in stages:
        proc = subprocess.run([sys.executable, str(SCRIPTS / script), *argv],
                              capture_output=True, text=True, encoding="utf-8",
                              check=False, cwd=REPO_ROOT)
        if proc.returncode != 0:
            print(f"  {sid}: {script} failed rc={proc.returncode} {proc.stderr.strip()}")
            return False
    return True


# ---------------------------------------------------------------------------
# Proof-root bookkeeping
# ---------------------------------------------------------------------------
def regenerate_hashes() -> int:
    """`manifest.sha256` is the single source of integrity (V4/V5)."""
    entries: list[tuple[str, str]] = []
    for path in sorted(p for p in PROOF_ROOT.rglob("*") if p.is_file()):
        rel = path.relative_to(PROOF_ROOT).as_posix()
        if rel in ALLOWLIST or any(rel.startswith(p) for p in ALLOWED_PREFIXES):
            continue
        entries.append((rel, hashlib.sha256(path.read_bytes()).hexdigest()))
    text = "".join(f"{digest}  {rel}\n" for rel, digest in entries)
    write_text_utf8(PROOF_ROOT / HASH_FILE, text)
    return len(entries)


def re_freeze_baseline() -> None:
    """V12: a deliberate re-freeze, in the same change as the new rows.

    Banks the rule-coverage census as well (`run_evals.check_rule_coverage`'s
    floor): re-freezing is the moment the current distinct-rule count is
    accepted, so it is recorded here rather than left to drift stale against a
    runner that measures it every run. The import is deferred because the runner
    is a sibling that imports the modules this tool shares.
    """
    baseline = read_json(PROOF_ROOT / BASELINE_FILE)
    baseline["scenario_manifest_sha256"] = hashlib.sha256(
        (PROOF_ROOT / MANIFEST_FILE).read_bytes()).hexdigest()
    baseline["manifest_sha256"] = hashlib.sha256(
        (PROOF_ROOT / HASH_FILE).read_bytes()).hexdigest()
    import run_evals  # noqa: PLC0415 (deferred: sibling runner, see docstring)
    baseline["rule_exercises"] = run_evals.dataset_rule_census()
    write_json(PROOF_ROOT / BASELINE_FILE, baseline)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Author frozen-catalog scenarios into tests/proof.")
    parser.add_argument("--scenarios", required=True,
                        help="Comma-separated catalog ids, e.g. SCEN-002,SCEN-003")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be authored; write nothing.")
    args = parser.parse_args(argv)

    catalog = parse_catalog()
    wanted = [s.strip() for s in args.scenarios.split(",") if s.strip()]
    unknown = [s for s in wanted if s not in catalog]
    if unknown:
        print(f"❌ not in the frozen catalog: {unknown}", file=sys.stderr)
        return 2

    today = date.today().isoformat()
    authored: list[dict] = []
    for sid in wanted:
        print(f"{sid}: authoring")
        row = build_row(catalog[sid], today=today)
        if row is None:
            continue
        if not args.dry_run:
            fixture_dir = PROOF_ROOT / "fixtures" / sid
            fixture_dir.mkdir(parents=True, exist_ok=True)
            write_json(fixture_dir / "current_trade.json", canonical_trade(row))
            # The truncated prefix is the ONLY tape the engine may see (V7).
            prefix, forward = curate.split_history(
                load_tape(row["symbol"], row["recommendation_date"]),
                row["recommendation_date"])
            write_json(fixture_dir / "ohlcv_prefix.json",
                       {"symbol": row["symbol"], "sessions": prefix}, indent=None)
            write_json(fixture_dir / "forward_tape.json",
                       {"symbol": row["symbol"], "bars": forward}, indent=None)
            if not record_stages(row):
                print(f"  {sid}: recording failed; row not published")
                continue
        authored.append(row)
        print(f"  {sid}: {row['instrument']} {row['symbol']}@{row['recommendation_date']} "
              f"entry {row['entry_price']} stop {row['stop_loss']} target "
              f"{row['target_price']} -> {row['scoring']['resolution_outcome']} "
              f"({row['scoring']['resolution_basis']}, {row['scoring']['quadrant_target']})")

    if not authored:
        print("\nno rows published")
        return 0
    if args.dry_run:
        print(f"\nDRY RUN — {len(authored)} row(s) would be published; nothing written")
        return 0

    manifest = read_json(PROOF_ROOT / MANIFEST_FILE)
    rows = {r["scenario_id"]: r for r in manifest["scenarios"]}
    for row in authored:
        rows[row["scenario_id"]] = row
    manifest["scenarios"] = [rows[k] for k in sorted(rows)]
    manifest["generated_on"] = today
    write_json(PROOF_ROOT / MANIFEST_FILE, manifest)

    hashed = regenerate_hashes()
    re_freeze_baseline()
    print(f"\npublished {len(authored)} row(s); manifest.sha256 now covers {hashed} file(s); "
          "baseline re-frozen")
    return 0


if __name__ == "__main__":
    sys.exit(main())
