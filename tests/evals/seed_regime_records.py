"""Seed the regime-shock golden roster with recorded stage-7 evidence.

`regime_shock` is the class whose breaker only stage 7 produces (BRD §10.3 #6:
VIX > 24 **AND** breadth < 30 — a conjunction, ADR-010 decision 6). The frozen
catalog carries seven crisis scenarios with a pending stage-7 `panic_regime`
intent (`scaffold_catalog.PENDING_BREAKERS`); this tool closes six of them with
evidence (`SCEN-005` keeps its pending intent — the owner's roster leaves it
out, and a pending claim the tool never promoted is not this tool's to claim):

1. **Author each stage-7 input bundle** (`fixtures/<id>/regime_context.json`)
   from that catalog row's own trap: a level the trap *states* is taken
   verbatim (`India VIX 83.6`, `VIX 32`) or at its stated bound (`> 80`,
   `< 5% bullish`); a level the trap does not state is authored inside the
   breaker's arm and the disclosure says exactly that — `origin: authored`,
   never an invented snapshot (E4 discipline). Each entry's `trap_claims` are
   asserted to appear in the frozen catalog text, so a documented value can
   never drift away from its source.
2. **Record the real stage stdout** by handing the bundles to the scaffolder,
   which runs `7_sector_macro_regime.py --record` for any scenario carrying a
   regime fixture, declares the stage-7 artifact, moves `panic_regime` from
   pending intent into `gate_focus` (V11 pairs them) and republishes manifest
   hashes and the re-frozen baseline.
3. **Publish the golden records** — `$ref`s for bytes, `$derive`s for the
   pinned tape's claims — and update the roster: `class_counts_expected`
   gains `regime_shock: <len(ROSTER)>`, `total_trades_expected` grows with it,
   and the count note states the new census. The rule-coverage floor is
   re-frozen *after* the dataset write, so the census banks the class in the
   same change (V12).

Dry run by default; `--apply` writes, `--force` re-authors existing bundles.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from datetime import date
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent.parent
EVALS_DIR = TESTS_DIR / "evals"
REPO_ROOT = TESTS_DIR.parent
PROOF_ROOT = TESTS_DIR / "proof"
GOLDEN = TESTS_DIR / "evals" / "golden_dataset.json"
SUPPORT_DIR = TESTS_DIR / "support"
SCRIPTS_DIR = REPO_ROOT / "scripts"
for splice in (str(EVALS_DIR), str(TESTS_DIR), str(SUPPORT_DIR)):
    if splice not in sys.path:
        sys.path.insert(0, splice)

import dataset_refs as dr          # noqa: E402  (sibling eval module)
import scaffold_catalog as sc      # noqa: E402  (the scaffolder owns rows/hashes)
import market_context as mc        # noqa: E402  (the uvest-market-context/1 builder)

TRADE_CLASS = "regime_shock"
BREAKER = "panic_regime"
REGIME_FILE = sc.REGIME_FILE  # "regime_context.json" — the stage-7 input bundle
COUNT_NOTE_RE = re.compile(r"^\d+ of \d+ records")

# Disclosed authored values common to every entry (never tuned per scenario):
# flow -1400 Cr/session ⇒ 5-day -7000 Cr < -5000 ⇒ NET_SELLING (BRD §7.4), and
# the trajectories below pin the graded TAIL values only — the histories are
# authored shapes, disclosed as such.
FLOW_UNIT = -1400.0
N_SESSIONS = mc.DEFAULT_SESSIONS  # >= 200 (NIFTY 200-DMA) and >= 55 (MRS + momentum)
VIX_BASELINE = 13.2         # pre-crisis level the panic ramp starts from
BREADTH_RISK_ON = 62.0      # pre-crash breadth the slide starts from
VIX_RAMP = 15               # sessions of the panic ramp at the tail
BREADTH_SLIDE = 20          # sessions of the crash slide at the tail
RATIO_TAIL_DAYS = 25
RATIO_TAIL_END = 0.97       # sector lagging NIFTY ⇒ RRG LAGGING, crisis-consistent

BREADTH_ARM = "authored inside the breaker arm (< 30): the trap states no breadth level"
VIX_ARM = "authored inside the breaker arm (> 24): the trap states no VIX level"

# The frozen roster. `trap_claims` are substrings the disclosure's "documented"
# basis rests on — `catalog_spec()` refuses to seed if the catalog text no
# longer contains them, so a documented value can never outlive its source.
ROSTER: list[dict] = [
    {"scenario_id": "SCEN-001",
     "vix": 83.6, "vix_basis": "documented verbatim in the catalog trap",
     "trap_claims": ["India VIX 83.6"],
     "breadth": 25.0, "breadth_basis": BREADTH_ARM,
     "nifty_start": 11500.0, "nifty_step": -16.0},
    {"scenario_id": "SCEN-002",
     "vix": 80.5, "vix_basis": "authored above the trap's stated floor (VIX > 80)",
     "trap_claims": ["VIX > 80", "breadth < 5% bullish"],
     "breadth": 4.5,
     "breadth_basis": ("authored below the trap's stated bound (breadth < 5% "
                       "bullish, and < 30 for the breaker arm)"),
     "nifty_start": 12000.0, "nifty_step": -15.0},
    {"scenario_id": "SCEN-003",
     "vix": 32.0, "vix_basis": "documented verbatim in the catalog trap (VIX 32)",
     "trap_claims": ["VIX 32"],
     "breadth": 25.0, "breadth_basis": BREADTH_ARM,
     "nifty_start": 24500.0, "nifty_step": -12.0},
    {"scenario_id": "SCEN-004",
     "vix": 25.0, "vix_basis": VIX_ARM, "trap_claims": [],
     "breadth": 25.0, "breadth_basis": BREADTH_ARM,
     "nifty_start": 24500.0, "nifty_step": -12.0},
    {"scenario_id": "SCEN-006",
     "vix": 25.0, "vix_basis": VIX_ARM, "trap_claims": [],
     "breadth": 25.0, "breadth_basis": BREADTH_ARM,
     "nifty_start": 17400.0, "nifty_step": -3.0},
    {"scenario_id": "SCEN-008",
     "vix": 25.0, "vix_basis": VIX_ARM, "trap_claims": [],
     "breadth": 25.0, "breadth_basis": BREADTH_ARM,
     "nifty_start": 18300.0, "nifty_step": -6.0},
]

CLASS_BREAKER = {TRADE_CLASS: BREAKER}
CLASS_CHECK = {
    TRADE_CLASS: lambda p: (not p.get("waived")
                            and p.get("panic_regime_breaker") is True
                            and p.get("vix_value") is not None
                            and p.get("vix_value") > 24.0
                            and p.get("market_breadth_pct") is not None
                            and p.get("market_breadth_pct") < 30.0),
}


class Blocked(Exception):
    """A condition that must stop the seed rather than write a bad row."""


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


_stage7 = None


def sector_map() -> dict[str, str]:
    """The stage-7 `SECTOR_INDEX_MAP` — the single source of symbol → index."""
    global _stage7
    if _stage7 is None:
        spec = importlib.util.spec_from_file_location(
            "uvest_stage7_map", SCRIPTS_DIR / "7_sector_macro_regime.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _stage7 = module
    return _stage7.SECTOR_INDEX_MAP


def catalog_spec(entry: dict) -> dict:
    catalog = sc.parse_catalog()
    sid = entry["scenario_id"]
    if sid not in catalog:
        raise Blocked(f"{sid} is not in the frozen catalog")
    spec = catalog[sid]
    missing = [c for c in entry["trap_claims"] if c not in spec["trap"]]
    if missing:
        raise Blocked(
            f"{sid}: catalog trap no longer states {missing!r} "
            f"(trap is now {spec['trap']!r}); re-freeze the catalog deliberately "
            "(and this roster with it), never silently")
    if spec["expected_verdict"] != "AVOID":
        raise Blocked(f"{sid}: the regime trap must be an AVOID row")
    if spec["symbol"] not in sector_map():
        raise Blocked(f"{sid}: {spec['symbol']} has no pinned sector index — "
                      "stage 7 would waive (ADR-010 decision 5)")
    return spec


def disclosure_of(entry: dict, spec: dict) -> str:
    sid = entry["scenario_id"]
    sector = sector_map()[spec["symbol"]]
    return (
        f"Authored market-context input ({sid}, as of {spec['anchor']}): the "
        f"catalog's own trap — \"{spec['trap']}\" — with the breaker's panic "
        f"arms stated as: VIX {entry['vix']} ({entry['vix_basis']}), market "
        f"breadth {entry['breadth']}% ({entry['breadth_basis']}), plus an "
        f"authored VIX/breadth trajectory, a 5-day combined net flow of "
        f"{FLOW_UNIT * 5:g} Cr (NET_SELLING), the trade's sector index "
        f"{sector}, and a deterministic weekday date axis ending at T_rec. "
        "origin=authored: no offline source exists for point-in-time index "
        "series, so every value is disclosed authorship rather than a market "
        "data snapshot (E4: disclosed authorship, never an invented source).")


def fixture_doc(entry: dict) -> dict:
    """The stage-7 input bundle for one roster entry: its trap, disclosed."""
    spec = catalog_spec(entry)
    sid = entry["scenario_id"]
    anchor = spec["anchor"]
    sector = sector_map()[spec["symbol"]]
    document = mc.build_context(
        n=N_SESSIONS,
        as_of=date.fromisoformat(anchor),
        nifty_start=entry["nifty_start"],
        nifty_step=entry["nifty_step"],   # falling ⇒ BELOW_200_DMA, crisis-consistent
        ratio_tail_days=RATIO_TAIL_DAYS,
        ratio_tail_end=RATIO_TAIL_END,
        vix_tail=entry["vix"],
        breadth_tail=entry["breadth"],
        fii_unit=FLOW_UNIT,
        sector_ticker=sector,
    )
    # build_context's default tails are linear ramps; the graded tails stay
    # exactly as pinned, but the histories are shaped to their disclosed story
    # (VIX quiet then a panic ramp; breadth RISK_ON then the crash slide).
    vix = document["series"][mc.INDIA_VIX]["values"]
    n = len(vix)
    for i, point in enumerate(vix):
        if i < n - VIX_RAMP:
            point["value"] = VIX_BASELINE
        else:
            frac = (i - (n - VIX_RAMP)) / (VIX_RAMP - 1)
            point["value"] = round(
                VIX_BASELINE + (entry["vix"] - VIX_BASELINE) * frac, 6)
    breadth = document["series"][mc.BREADTH]["values"]
    for i, point in enumerate(breadth):
        if i < n - BREADTH_SLIDE:
            point["value"] = BREADTH_RISK_ON
        else:
            frac = (i - (n - BREADTH_SLIDE)) / (BREADTH_SLIDE - 1)
            point["value"] = round(
                BREADTH_RISK_ON + (entry["breadth"] - BREADTH_RISK_ON) * frac, 6)
    document["provenance"] = {
        "origin": "authored",
        "source": "tests/evals/seed_regime_records.py",
        "note": (f"Authored market-context input for {sid}'s stage-7 "
                 "recording: values at the catalog's stated bounds or inside "
                 "the breaker's arms, disclosed in `authoring`. Not a market "
                 "data source."),
    }
    document["authoring"] = {
        "source": "seed_regime_records/1",
        "catalog": "business_test_cases.md §3",
        "scenario_id": sid,
        "trade_class": TRADE_CLASS,
        "catalog_name": spec["name"],
        "trap": spec["trap"],
        "sector_index": sector,
        "breaker": BREAKER,
        "breaker_stage": 7,
        "vix_value": entry["vix"],
        "vix_basis": entry["vix_basis"],
        "market_breadth_pct": entry["breadth"],
        "breadth_basis": entry["breadth_basis"],
        "fii_dii_flow_unit_cr": FLOW_UNIT,
        "disclosure": disclosure_of(entry, spec),
    }
    return document


def write_fixture(entry: dict, *, force: bool) -> tuple[Path, str]:
    """Author the bundle unless one is already committed (or --force)."""
    path = PROOF_ROOT / "fixtures" / entry["scenario_id"] / REGIME_FILE
    if path.exists() and not force:
        return path, "existing bundle kept"
    sc.write_json(path, fixture_doc(entry))
    return path, "authored"


def check_recorded_evidence(entry: dict, row: dict) -> list[str]:
    """The recorded stage-7 bytes must satisfy the class invariant they grade."""
    sid = entry["scenario_id"]
    declared = [a for a in row.get("recorded_artifacts") or []
                if a.get("artifact") == "out_7_regime.json"]
    if len(declared) != 1 or declared[0].get("evidence") != "recorded":
        raise Blocked(f"{sid}: no single recorded stage-7 artifact declared")
    payload = read_json(PROOF_ROOT / declared[0]["path"])
    if not CLASS_CHECK[TRADE_CLASS](payload):
        raise Blocked(f"{sid}: recorded stage-7 bytes do not encode the "
                      f"panic conjunction: {payload}")
    if payload.get("vix_value") != entry["vix"] or \
            payload.get("market_breadth_pct") != entry["breadth"]:
        raise Blocked(f"{sid}: recorded arms {payload.get('vix_value')}/"
                      f"{payload.get('market_breadth_pct')} are not the "
                      f"roster's {entry['vix']}/{entry['breadth']}")
    if BREAKER not in (row.get("gate_focus") or []):
        raise Blocked(f"{sid}: gate_focus does not carry {BREAKER}")
    if "breaker_pending_stage_7" in (row.get("authoring") or {}):
        raise Blocked(f"{sid}: the row still carries breaker_pending_stage_7")
    if row.get("replay_inputs", {}).get("regime_fixture") != \
            f"fixtures/{sid}/{REGIME_FILE}":
        raise Blocked(f"{sid}: replay_inputs.regime_fixture not declared")
    return [f"{BREAKER} graded from recorded stage-7 bytes (VIX "
            f"{payload['vix_value']} > 24, breadth "
            f"{payload['market_breadth_pct']} < 30)"]


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
    rule = dr.FORWARD_TAPE_RULES
    if rule["forward_min_max"](bars, row) != scoring.get("forward_derived_cache"):
        raise Blocked(f"{sid}: derived forward range disagrees with the row's cache")
    if rule["resolution_date"](bars, row) != scoring.get("resolution_date"):
        raise Blocked(f"{sid}: derived horizon end disagrees with the row's")

    tape = f"tests/proof/{tape_rel}"

    def ref(artifact: str) -> dict:
        return {"$ref": f"tests/proof/fixtures/{sid}/recorded/{artifact}"}

    def derive(rule_name: str) -> dict:
        return {"$derive": {"tape": tape, "rule": rule_name}}

    return {
        "trade_id": sid,
        "trade_class": TRADE_CLASS,
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
            "out_7_regime.json": ref("out_7_regime.json"),
        },
        "forward_prices_22d": derive("forward_min_max"),
        "cone_p10": None,
        "cone_p90": None,
        "market_mae_pct": derive("market_mae_pct"),
        "limit_mae_pct": None,
        "predicted_direction": None,
        "actual_direction": derive("realized_direction"),
    }


def regime_note(prior: str | None, total: int) -> str:
    """The count note, extended with the regime census — the rest preserved."""
    ids = ", ".join(e["scenario_id"] for e in ROSTER)
    documented = "; ".join(
        f"{e['scenario_id']}: VIX {e['vix']} documented verbatim"
        for e in ROSTER if e["trap_claims"]
        and "documented" in e["vix_basis"])
    regime = (
        f"regime_shock {len(ROSTER)}/{len(ROSTER)} ({ids}) — the catalog's "
        "panic scenarios with a stage-7 `panic_regime` intent, each recorded "
        "through `7_sector_macro_regime.py --record` over a disclosed "
        "`uvest-market-context/1` bundle (tests/evals/seed_regime_records.py): "
        "the trap's own level where it states one "
        f"({documented}; SCEN-002 at its stated > 80 / < 5% bounds), disclosed "
        "authored values inside the breaker arms where it states none, so gate "
        "2 grades the panic_regime conjunction (VIX > 24 AND breadth < 30, "
        "ADR-010 decision 6) from proven bytes rather than pending promises.")
    if prior and COUNT_NOTE_RE.match(prior.lstrip()):
        text = COUNT_NOTE_RE.sub(f"{total} of {total} records", prior, count=1)
        text = text.replace("all six classes are seeded",
                            "all seven classes are seeded")
        if regime not in text:
            text = text.rstrip() + " " + regime
        return text
    return f"{total} of {total} records. {regime}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seed the regime-shock golden roster with stage-7 evidence.")
    parser.add_argument("--apply", action="store_true",
                        help="Write fixtures, rows, hashes, baseline and the dataset.")
    parser.add_argument("--force", action="store_true",
                        help="Re-author regime fixtures that already exist.")
    args = parser.parse_args(argv)

    print("=" * 72)
    print("REGIME SEED PLAN — identities from the frozen catalog, bounds disclosed")
    print("=" * 72)
    try:
        for entry in ROSTER:
            spec = catalog_spec(entry)
            sector = sector_map()[spec["symbol"]]
            print(f"  {entry['scenario_id']} [{TRADE_CLASS}] {spec['symbol']} "
                  f"{spec['instrument']} @ {spec['anchor']} -> {sector}")
            print(f"    trap: {spec['trap']}")
            print(f"    arms: VIX {entry['vix']} ({entry['vix_basis']}); "
                  f"breadth {entry['breadth']}% ({entry['breadth_basis']})")
    except Blocked as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2

    if not args.apply:
        print(f"\nDRY RUN — {len(ROSTER)} slot(s) would be seeded; nothing written")
        return 0

    for entry in ROSTER:
        path, state = write_fixture(entry, force=args.force)
        print(f"\n  {entry['scenario_id']}: {REGIME_FILE} {state} ({path.name})")

    # The scaffolder owns row authoring: it records stages 1–2 and now 7,
    # declares the stage-7 artifact, promotes the pending breaker into
    # gate_focus, and republishes hashes and the re-frozen baseline.
    print("authoring rows through the catalog scaffolder…")
    rc = sc.main(["--scenarios", ",".join(e["scenario_id"] for e in ROSTER)])
    if rc != 0:
        print(f"❌ scaffolder exited {rc}; nothing further written", file=sys.stderr)
        return rc

    manifest = read_json(PROOF_ROOT / sc.MANIFEST_FILE)
    rows = {r["scenario_id"]: r for r in manifest["scenarios"]}
    records: list[dict] = []
    for entry in ROSTER:
        sid = entry["scenario_id"]
        row = rows.get(sid)
        if row is None:
            print(f"❌ {sid}: no row published; the dataset is unchanged",
                  file=sys.stderr)
            return 1
        try:
            for line in check_recorded_evidence(entry, row):
                print(f"  ✓ {sid}: {line}")
            records.append(build_record(entry, row))
        except Blocked as exc:
            print(f"❌ {exc}", file=sys.stderr)
            print("   nothing written; the dataset is unchanged", file=sys.stderr)
            return 1

    data = read_json(GOLDEN)
    trades = {t["trade_id"]: t for t in data["trades"]}
    for record in records:
        trades[record["trade_id"]] = record
    data["trades"] = [trades[k] for k in sorted(trades)]
    data["meta"]["class_counts_expected"][TRADE_CLASS] = len(ROSTER)
    counts: dict[str, int] = {}
    for trade in data["trades"]:
        counts[trade["trade_class"]] = counts.get(trade["trade_class"], 0) + 1
    data["meta"]["total_trades_expected"] = len(data["trades"])
    notes = data["meta"].get("notes") or []
    kept = [n for n in notes if not COUNT_NOTE_RE.match(n.lstrip())]
    prior = next((n for n in notes if COUNT_NOTE_RE.match(n.lstrip())), None)
    data["meta"]["notes"] = [*kept, regime_note(prior, len(data["trades"]))]
    sc.write_json(GOLDEN, data)

    # The floor must bank the new rows in the same change as the dataset: the
    # scaffolder froze the baseline before this write, so re-freeze now (V12).
    sc.re_freeze_baseline()

    ok = all(counts.get(cls, 0) == exp
             for cls, exp in data["meta"]["class_counts_expected"].items())
    print(f"\nseeded {len(records)} record(s); dataset now "
          f"{len(data['trades'])}/{data['meta']['total_trades_expected']} "
          f"across {len(counts)} class(es)")
    for cls_name, expected in data["meta"]["class_counts_expected"].items():
        mark = "✓" if counts.get(cls_name, 0) == expected else "·"
        print(f"  {mark} {cls_name}: {counts.get(cls_name, 0)}/{expected}")
    print(f"  authored on {date.today().isoformat()}; quarantine register "
          "untouched (E5 meter unchanged); hashes and baseline re-frozen")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
