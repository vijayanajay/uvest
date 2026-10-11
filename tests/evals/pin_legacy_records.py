#!/usr/bin/env python3
"""Legacy golden records -> proof-root rows (the dataset-migration half of E5).

    python tests/evals/pin_legacy_records.py                      # dry run
    python tests/evals/pin_legacy_records.py --apply

The pre-curation records (`GB-2022-0001` … `GB-2025-0005`) predate the proof
root: their `script_outputs` held hand-typed subsets and their outcome blocks
held typed numbers. `GB-2023-0003` was migrated first (quarantine migration);
this tool migrates the rest to the same shape, so no migrated record carries a
claim the committed evidence cannot support:

1. **Stage 1–2 payloads become recorded evidence.** Each record's canonical
   trade is rebuilt from its own published levels and re-run through the repo's
   `--record` mechanism into `tests/proof/fixtures/<id>/recorded/`; the dataset
   then holds `$ref`s, so the payload has exactly one copy of its bytes.
2. **Outcome claims become `$derive` references.** `forward_prices_22d`, the
   resolution pair, `market_mae_pct` and `actual_direction` are resolved at load
   from the row's pinned forward tape by the frozen rules in `dataset_refs.py`
   — never typed, so a number in the record cannot drift from the tape.
3. **Geometry is preserved when the tape corroborates it, and re-derived at the
   record's frozen class shape when it does not.** Corroboration is structural
   and lookahead-free: a long advisory is *actionable at T_rec* iff its published
   stop sits below the tape's T_rec close and its target above it. When it does
   (e.g. a pending limit entry a few percent under the close), the published
   levels are kept as published. When the stop is already breached at T_rec the
   levels belong to a price scale the point-in-time tape does not corroborate,
   so the geometry is re-derived from ≤T_rec tape at the class shape the curator
   itself uses (`curate.CLEAN/INVERTED/TIGHT`: 2.0xATR & 2.2R, 1.2xATR & 0.9R,
   0.6xATR) and the superseded levels are recorded in `authoring.note`. The
   shape is chosen per class, never per record, and the tape decides the outcome
   — a row whose tape flip-flops its class claim is disclosed, never tuned.
4. **Engine-less claims are retired, not invented.** Stage 6 payloads (pledge /
   days-to-earnings) and the ex-ante `predicted_direction` have no engine, so
   they are dropped from the record and declared `evidence: waived` with a
   reason (E4: absence is a first-class value). They are deliberately NOT
   quarantined: quarantining is for hand-typed payloads that must stay on disk
   for migration continuity, and the E5 meter may only ever decrease — growing
   it to park retired legacy bytes would corrupt the migration's completion
   meter, so the tool asserts the register is untouched.
5. **Contract specifics.** Rows migrate as `CASH` with `lot_size 1`, exactly as
   the `GB-2023-0003` migration did: the legacy FUT expiry/lot are not
   verifiable from committed point-in-time sources, so they are not asserted.

Disclosed limitations (printed, never hidden):

* **Successor tapes.** A delisted issuer (corporate action) has no live ticker;
  the tape then comes from the successor entity's series and the override is
  recorded in `authoring.tape_source`, with the corporate-action caveat spelled
  out — the row's evidence is only as point-in-time as that series is.
* **Superseded numbers.** Every legacy claim that the tape contradicts is
  printed side by side with the derived value, so the migration reads as a set
  of findings rather than a silent rewrite.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "tests" / "evals"))

# Match every other entrypoint in this repo (run_evals.py, curate.py,
# validate_manifest.py): force UTF-8 output so CLI diagnostics survive the
# Windows console codepage.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

import curate  # noqa: E402  (cached fetch, frozen ATR/outcome conventions)
import dataset_refs as dr  # noqa: E402  (the frozen derivation rules)
import scaffold_catalog as sc  # noqa: E402  (fixture/hash/baseline bookkeeping)

PROOF_ROOT = REPO_ROOT / "tests" / "proof"
SCRIPTS = REPO_ROOT / "scripts"
GOLDEN = REPO_ROOT / "tests" / "evals" / "golden_dataset.json"
BASELINE = PROOF_ROOT / sc.BASELINE_FILE

MIGRATION_SOURCE = "dataset-migration/1"
DEFAULT_RECORDS = ("GB-2022-0001", "GB-2022-0002", "GB-2024-0004", "GB-2025-0005")

# The legacy claims a pinned tape can replace. `market_mae_pct` and
# `actual_direction` are *newly* derived (they were never claimed), so they are
# reported as derivations, not supersessions.
CLAIM_FIELDS = ("forward_prices_22d", "resolution_date", "resolution_outcome")
NOTE_MARKER = "migrated onto proof-root rows"

# Class -> (frozen geometry shape, expected verdict, breaker the evidence supports).
CLASS_PLAN = {
    "clean_bullish": (curate.CLEAN, "GO", None),
    "inverted_rr": (curate.INVERTED, "AVOID", "inverted_risk_reward"),
    "tight_stop": (curate.TIGHT, "AVOID", "tighter_than_0_75x_atr"),
}

# Settled claims: no engine exists for these, so the record must not carry them.
RETIRED_CLAIM_FIELDS = ("cone_p10", "cone_p90", "limit_mae_pct", "predicted_direction")
WAIVED_STAGE6_REASON = (
    "stage 6 unimplemented: the legacy hand-typed pledge/days-to-earnings payload "
    "is retired (absence, not quarantined, so the E5 migration meter cannot grow)"
)

# Delisted issuers: the point-in-time symbol is kept as the record's identity and
# only the network fetch uses the surviving series (the SCEN-011 rename pattern).
TAPE_SOURCE_TICKER = {
    "TATAMOTORS": (
        "TMPV",
        "issuer demerged (Tata Motors -> TMPV/TMCV, 2025): TATAMOTORS.NS is "
        "delisted and the successor entity's series is the only surviving tape, "
        "so it may carry corporate-action adjustment back to 2022",
    ),
}

LOT_SIZE = 1                  # CASH migration (no verifiable contract specifics)
USER_CAPITAL_INR = 500000.0   # the frozen capital convention
OUTCOME_QUADRANT = {
    ("GO", "WIN"): "TP", ("GO", "LOSS"): "FP",
    ("AVOID", "LOSS"): "TN", ("AVOID", "WIN"): "FN",
}


class Blocked(Exception):
    """The tape cannot support the record — report, never invent."""


# ---------------------------------------------------------------------------
# Tape
# ---------------------------------------------------------------------------
def load_tape(symbol: str, rec_date: str):
    """The symbol's cached point-in-time tape, or a Blocked reason."""
    override = TAPE_SOURCE_TICKER.get(symbol)
    sessions = curate.load_history(symbol, rec_date,
                                  fetch_symbol=override[0] if override else None)
    if sessions is None:
        why = (f"no usable tape: {symbol}.NS is delisted"
               + (f" and the successor ticker {override[0]}.NS returned nothing"
                  if override else ""))
        raise Blocked(why)
    tape_source = None
    if override:
        ticker, reason = override
        tape_source = {"requested": f"{symbol}.NS", "fetched": f"{ticker}.NS",
                       "reason": reason}
    return sessions, tape_source


def actionable(geometry: dict, close: float) -> bool:
    """Whether the published levels describe a long advisory live at T_rec.

    Lookahead-free: it uses only the T_rec close from the ≤T_rec tape. A stop
    above the market (or a target below it) means the levels belong to a scale
    this tape does not corroborate — not that the record should be tuned.
    """
    return (geometry["stop_loss"] < geometry["entry_price"] < geometry["target_price"]
            and geometry["stop_loss"] < close < geometry["target_price"])


def geometry_for(trade: dict, prefix: list[dict], close: float) -> dict:
    """Published geometry when the tape corroborates it, else the class shape."""
    published = {"entry_price": float(trade["entry_price"]),
                 "stop_loss": float(trade["stop_loss"]),
                 "target_price": float(trade["target_price"])}
    if actionable(published, close):
        basis = "preserved_as_published"
        geometry = dict(published, live_price=published["entry_price"])
    else:
        shape, _verdict, _breaker = CLASS_PLAN[trade["trade_class"]]
        atr = curate.atr14_simple_mean(prefix)
        entry = round(close, 2)
        stop = round(entry - shape["risk_atr"] * atr, 2)
        risk = entry - stop
        target = round(entry + (shape["rr"] * risk if shape["rr"] else 2.5 * risk), 2)
        if stop <= 0 or target <= entry:
            raise Blocked(f"class shape derives degenerate geometry (stop {stop})")
        basis = "rebased_from_tape"
        geometry = {"entry_price": entry, "stop_loss": stop, "target_price": target,
                    "live_price": entry}
    geometry["basis"] = basis
    geometry["entry_range_min"], geometry["entry_range_max"] = curate.entry_band(
        geometry["entry_price"])
    return geometry


def outcome_of(forward: list[dict], stop: float, target: float) -> tuple[str, str]:
    """The tape's verdict and its honest basis — via the frozen rule itself.

    The migration tool has no scan of its own: `dataset_refs.resolution_of` is
    the one implementation shared with the `$derive` rule, the curator's seed
    scan (`curate.outcome`) and the catalog scaffolder
    (`scaffold_catalog.resolve`), so a row's stored resolution is the same
    computation the eval runner re-derives at load.
    """
    return dr.resolution_of(forward, stop, target)


# ---------------------------------------------------------------------------
# Record -> row
# ---------------------------------------------------------------------------
def migrate(trade: dict, today: str) -> dict:
    """Everything the record's row (and the report) needs, or a Blocked reason."""
    tid = trade["trade_id"]
    symbol = trade["symbol"]
    rec_date = trade["recommendation_date"]
    if trade["trade_class"] not in CLASS_PLAN:
        raise Blocked(f"no frozen class plan for {trade['trade_class']}")

    sessions, tape_source = load_tape(symbol, rec_date)
    prefix, forward = curate.split_history(sessions, rec_date)
    if len(prefix) < curate.MIN_SESSIONS:
        raise Blocked(f"prefix is {len(prefix)} sessions < the {curate.MIN_SESSIONS} floor")
    if len(forward) < curate.FORWARD:
        raise Blocked(f"tape ends {len(forward)} sessions after T_rec; {curate.FORWARD} needed")

    close = round(prefix[-1]["close"], 2)
    geometry = geometry_for(trade, prefix, close)

    sid = tid
    row = {
        "scenario_id": sid,
        "schema_version": sc.SCHEMA_NAME,
        "origin": "curated_behavioral",
        "created_on": today,
        "classification_tags": ["DATASET_MIGRATION"],
        "gate_focus": ["ZERO_HALLUCINATION"],
        "symbol": symbol,
        "exchange": "NSE",
        "instrument": "CASH",
        "action": "BUY",
        "recommendation_date": rec_date,
        "entry_price": geometry["entry_price"],
        "entry_range_min": geometry["entry_range_min"],
        "entry_range_max": geometry["entry_range_max"],
        "target_price": geometry["target_price"],
        "stop_loss": geometry["stop_loss"],
        "lot_size": LOT_SIZE,
        "user_capital_inr": USER_CAPITAL_INR,
        "replay_inputs": {
            "canonical_trade": f"fixtures/{sid}/current_trade.json",
            "truncated_fixtures": [f"fixtures/{sid}/ohlcv_prefix.json"],
            "truncation": {"cutoff": rec_date, "rule": "date <= T_rec",
                           "min_bars": curate.MIN_SESSIONS},
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
            "resolution_outcome": None,   # filled from the tape below
            "resolution_basis": None,
            "forward_derived_cache": {
                "min": min(b["low"] for b in forward),
                "max": max(b["high"] for b in forward),
            },
        },
    }

    shape, verdict, breaker = CLASS_PLAN[trade["trade_class"]]
    row["expected_verdict"] = verdict
    if breaker:
        row["gate_focus"].append(breaker)
    # Legacy stage-6 payloads (0001/0002/0004) are retired to absence, declared so
    # the breach is auditable and V11 can see stage 6 has no evidence.
    if "out_6_fundamental.json" in (trade.get("script_outputs") or {}):
        row["recorded_artifacts"].append(
            {"stage": 6, "artifact": "out_6_fundamental.json", "path": None,
             "evidence": "waived", "reason": WAIVED_STAGE6_REASON})

    tape_rel = f"tests/proof/fixtures/{sid}/forward_tape.json"

    def derive(rule: str) -> dict:
        return {"$derive": {"tape": tape_rel, "rule": rule}}

    # The tape decides through `dataset_refs`' own frozen rules — the tool never
    # re-implements them, so row and dataset cannot disagree. The rules read the
    # *migrated* levels (what the row pins), not the superseded legacy ones.
    scoring_trade = {"entry_price": row["entry_price"],
                     "stop_loss": row["stop_loss"],
                     "target_price": row["target_price"]}

    def rule_value(rule: str):
        return dr.FORWARD_TAPE_RULES[rule](forward, scoring_trade)

    outcome, basis = outcome_of(forward, geometry["stop_loss"], geometry["target_price"])
    cache = rule_value("forward_min_max")
    if row["scoring"]["forward_derived_cache"] != cache:
        raise Blocked("derivation rule and tape scan disagree on the forward range")
    row["scoring"]["resolution_outcome"] = outcome
    row["scoring"]["resolution_basis"] = basis
    quadrant = OUTCOME_QUADRANT.get((verdict, outcome))
    if quadrant:
        row["scoring"]["quadrant_target"] = quadrant
    if rule_value("resolution_date") != row["scoring"]["resolution_date"]:
        raise Blocked("derivation rule and tape disagree on the horizon end")

    derived = {
        "forward_prices_22d": (derive("forward_min_max"), cache),
        "resolution_date": (derive("resolution_date"), row["scoring"]["resolution_date"]),
        "resolution_outcome": (derive("resolution_outcome"), outcome),
        "market_mae_pct": (derive("market_mae_pct"), rule_value("market_mae_pct")),
        "actual_direction": (derive("realized_direction"), rule_value("realized_direction")),
    }

    note_bits = [
        f"legacy levels {'preserved as published' if geometry['basis'] == 'preserved_as_published' else 're-derived from tape'}"
        f" (stop {geometry['stop_loss']} / entry {geometry['entry_price']} / "
        f"target {geometry['target_price']})",
        f"real point-in-time {symbol} tape re-recorded (T_rec close {close}, "
        f"ATR14 {round(curate.atr14_simple_mean(prefix), 4)}, "
        f"{len(prefix)} prefix / {len(forward)} forward sessions)",
        "migrated as CASH because the legacy FUT contract specifics (expiry/lot) "
        "are not verifiable from committed point-in-time sources",
    ]
    if geometry["basis"] == "rebased_from_tape":
        note_bits.append(
            f"the published entry {trade['entry_price']} is {round(float(trade['entry_price']) / close, 3)}x the "
            f"tape's T_rec close and the published stop {trade['stop_loss']} sits above every "
            "bar the tape prints, so the levels belong to a scale this tape cannot "
            "corroborate; the geometry is re-derived at the record's frozen class shape "
            f"(risk_atr {shape['risk_atr']}, rr {shape['rr']}) — never tuned to the claim")
    row["authoring"] = {
        "source": MIGRATION_SOURCE,
        "legacy_trade_id": tid,
        "legacy_dataset": "tests/evals/golden_dataset.json",
        "legacy_instrument": trade["instrument"],
        "geometry_basis": geometry["basis"],
        "class_shape": ({"risk_atr": shape["risk_atr"], "rr": shape["rr"]}
                        if geometry["basis"] == "rebased_from_tape" else None),
        "corroboration": {
            "rule": "stop_loss < T_rec close < target_price (lookahead-free, ≤T_rec tape)",
            "t_rec_close": close,
            "published": {"entry_price": trade["entry_price"],
                          "stop_loss": trade["stop_loss"],
                          "target_price": trade["target_price"]},
        },
        "note": "; ".join(note_bits),
    }
    if tape_source:
        row["authoring"]["tape_source"] = tape_source
    # Every legacy claim the tape replaces is frozen next to the evidence, so the
    # supersession is auditable from the row alone (the report prints it too).
    row["authoring"]["superseded"] = [
        {"claim": name, "rule": derived[name][0]["$derive"]["rule"],
         "legacy": trade.get(name), "tape": derived[name][1]}
        for name in CLAIM_FIELDS
        if name in trade and not same_value(trade.get(name), derived[name][1])
    ]
    return {"row": row, "trade": trade, "prefix": prefix, "forward": forward,
            "close": close, "geometry": geometry, "derived": derived,
            "outcome": outcome, "basis": basis, "tape_source": tape_source,
            "replay_value": {
                "symbol": symbol, "exchange": "NSE",
                "instrument_type": "CASH", "action": "BUY",
                "entry_price": row["entry_price"],
                "entry_range_min": row["entry_range_min"],
                "entry_range_max": row["entry_range_max"],
                "live_price": row["entry_price"],
                "target": row["target_price"],
                "stoploss": row["stop_loss"],
                "lot_size": LOT_SIZE,
                "recommended_date": rec_date,
                "user_capital_inr": USER_CAPITAL_INR}}


def write_fixtures(bundle: dict) -> None:
    row = bundle["row"]
    sid = row["scenario_id"]
    fixture_dir = PROOF_ROOT / "fixtures" / sid
    fixture_dir.mkdir(parents=True, exist_ok=True)
    sc.write_json(fixture_dir / "current_trade.json", bundle["replay_value"])
    sc.write_json(fixture_dir / "ohlcv_prefix.json",
                  {"symbol": row["symbol"], "sessions": bundle["prefix"]}, indent=None)
    sc.write_json(fixture_dir / "forward_tape.json",
                  {"symbol": row["symbol"], "bars": bundle["forward"]}, indent=None)
    bundle_dir = fixture_dir / "recorded"
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)


def record_stages(bundle: dict) -> dict:
    """Run stages 1–2 through `--record`: the row's evidence is real stdout."""
    row = bundle["row"]
    sid = row["scenario_id"]
    fixture_dir = PROOF_ROOT / "fixtures" / sid
    bundle_dir = fixture_dir / "recorded"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    for script, argv in (
        ("1_math_and_rr.py", ["--input", str(fixture_dir / "current_trade.json"),
                              "--record", str(bundle_dir)]),
        ("2_market_data.py", ["--input", str(fixture_dir / "current_trade.json"),
                              "--ohlcv-fixture", str(fixture_dir / "ohlcv_prefix.json"),
                              "--record", str(bundle_dir)]),
    ):
        proc = subprocess.run([sys.executable, str(SCRIPTS / script), *argv],
                              capture_output=True, text=True, encoding="utf-8",
                              check=False, cwd=REPO_ROOT)
        if proc.returncode != 0:
            raise Blocked(f"{script} failed rc={proc.returncode}: {proc.stderr.strip()}")
    return {"stage1": sc.read_json(bundle_dir / "out_1_math.json"),
            "stage2": sc.read_json(bundle_dir / "out_2_market.json")}


# ---------------------------------------------------------------------------
# Payload reproduction: the copied payload claims vs the fresh recording
# ---------------------------------------------------------------------------
def payload_mismatches(bundle: dict, recorded: dict) -> list[dict]:
    """The record's committed out_1/out_2 payload values vs the re-recorded bytes.

    The campaign payloads were *copied* out of an earlier recording of the same
    geometry, so re-recording must reproduce them (the comparison mirrors the
    curator's own rounding). Anything that does not match is a claim the
    evidence supersedes: reported and frozen in `authoring.superseded`, never
    silently accepted — while the class invariant check still has to hold.

    `current_trade.json` is deliberately excluded: it echoes the *input*, and the
    CASH migration legitimately re-states the instrument.
    """
    mismatches: list[dict] = []
    fresh = {"out_1_math.json": recorded["stage1"],
             "out_2_market.json": recorded["stage2"]}
    for artifact, committed in ((bundle["trade"].get("script_outputs") or {}).items()):
        stage = fresh.get(artifact)
        if stage is None or not isinstance(committed, dict):
            continue
        for key, value in committed.items():
            if key not in stage:
                mismatches.append({"claim": f"{artifact}.{key}", "legacy": value,
                                   "tape": None, "rule": "absent from recorded stdout"})
            elif isinstance(value, float) or isinstance(stage[key], float):
                if round(float(stage[key]), 6) != round(float(value), 6):
                    mismatches.append({"claim": f"{artifact}.{key}", "legacy": value,
                                       "tape": stage[key], "rule": "recorded stdout"})
            elif stage[key] != value:
                mismatches.append({"claim": f"{artifact}.{key}", "legacy": value,
                                   "tape": stage[key], "rule": "recorded stdout"})
    return mismatches


def supersession_report(bundles: list[dict]) -> tuple[int, int]:
    """Print every claim the tape replaces, and return (superseded, identical)."""
    print("=" * 72)
    print("SUPERSESSION REPORT — every legacy claim the tape replaces")
    print("=" * 72)
    superseded = identical = 0
    for bundle in bundles:
        sid = bundle["row"]["scenario_id"]
        trade = bundle["trade"]
        for item in bundle["row"]["authoring"].get("superseded") or []:
            superseded += 1
            basis = (f" [{bundle['basis']}]" if item["claim"] == "resolution_outcome" else "")
            print(f"  SUPERSEDED {sid}.{item['claim']}: {item['legacy']!r} -> "
                  f"{item['tape']!r} ({item['rule']}){basis}")
        for name in CLAIM_FIELDS:
            if name in trade and same_value(trade.get(name), bundle["derived"][name][1]):
                identical += 1
        for item in bundle.get("payload_superseded") or []:
            superseded += 1
            print(f"  SUPERSEDED {sid}.{item['claim']}: {item['legacy']!r} -> "
                  f"{item['tape']!r} ({item['rule']})")
        for field in RETIRED_CLAIM_FIELDS:
            if trade.get(field) is not None:
                print(f"  RETIRED    {sid}.{field}: {trade[field]!r} -> absent "
                      "(no engine; absence, not a claim)")
        if "out_6_fundamental.json" in (trade.get("script_outputs") or {}):
            print(f"  RETIRED    {sid}.out_6_fundamental.json -> waived-absent "
                  "(stage 6 unimplemented; never quarantined, E5 meter unchanged)")
    print(f"  --- {superseded} claim value(s) superseded, {identical} confirmed "
          f"identical to the tape, {len(bundles)} record(s) planned")
    return superseded, identical


def check_class_invariant(bundle: dict, recorded: dict) -> list[str]:
    """The dataset's own frozen class invariant, graded on the recorded bytes."""
    cls = bundle["trade"]["trade_class"]
    s1, s2 = recorded["stage1"], recorded["stage2"]
    checks: list[str] = []
    if cls == "clean_bullish":
        if s1.get("live_rr") is None or s1["live_rr"] <= 1.0:
            raise Blocked(f"clean_bullish needs live_rr > 1.0, recorded {s1.get('live_rr')!r}")
        if s2.get("is_tight_sl") is not False:
            raise Blocked(f"clean_bullish needs is_tight_sl false, recorded {s2.get('is_tight_sl')!r}")
        checks.append(f"live_rr {round(s1['live_rr'], 6)} > 1.0")
        checks.append("is_tight_sl false")
    elif cls == "inverted_rr":
        for key in ("raw_rr", "live_rr"):
            if s1.get(key) is None or s1[key] >= 1.0:
                raise Blocked(f"inverted_rr needs {key} < 1.0, recorded {s1.get(key)!r}")
        checks.append(f"raw_rr/live_rr {round(s1['live_rr'], 6)} < 1.0")
    elif cls == "tight_stop":
        if s2.get("is_tight_sl") is not True:
            raise Blocked(f"tight_stop needs is_tight_sl true, recorded {s2.get('is_tight_sl')!r}")
        checks.append(f"sl_atr_ratio {round(s2['sl_atr_ratio'], 6)} < 0.75 (is_tight_sl true)")
    return checks


# ---------------------------------------------------------------------------
# Dataset rewrite
# ---------------------------------------------------------------------------
def migrated_record(bundle: dict) -> dict:
    """The record as it must read after migration: refs, derivations, no claims."""
    trade = bundle["trade"]
    row = bundle["row"]
    ref = (lambda name, kind: {"$ref": f"tests/proof/fixtures/{row['scenario_id']}/{kind}/{name}"})
    outputs: dict = {}
    for artifact, payload in (trade.get("script_outputs") or {}).items():
        if artifact in ("out_1_math.json", "out_2_market.json"):
            outputs[artifact] = ref(artifact, "recorded")
        elif artifact == "out_6_fundamental.json":
            continue           # retired: waived-absent per E4 (stage 6 unimplemented)
        else:
            outputs[artifact] = payload   # current_trade.json: the legacy input echo
    record = {
        "trade_id": trade["trade_id"],
        "trade_class": trade["trade_class"],
        "symbol": trade["symbol"],
        "instrument": trade["instrument"],
        "recommendation_date": trade["recommendation_date"],
        "resolution_date": bundle["derived"]["resolution_date"][0],
        "resolution_outcome": bundle["derived"]["resolution_outcome"][0],
        "entry_price": row["entry_price"],
        "stop_loss": row["stop_loss"],
        "target_price": row["target_price"],
        "script_outputs": outputs,
        "forward_prices_22d": bundle["derived"]["forward_prices_22d"][0],
        "cone_p10": None,
        "cone_p90": None,
        "market_mae_pct": bundle["derived"]["market_mae_pct"][0],
        "limit_mae_pct": None,
        "predicted_direction": None,
        "actual_direction": bundle["derived"]["actual_direction"][0],
        "html_report_valid": trade.get("html_report_valid"),
    }
    return record


def first_touch(forward: list[dict], stop: float, target: float) -> tuple[str, str]:
    """The bars that decided the verdict — printed so the scan is auditable."""
    stop_bar = next((b for b in forward if b["low"] <= stop), None)
    target_bar = next((b for b in forward if b["high"] >= target), None)
    if stop_bar is None and target_bar is None:
        return "neither level touched in 22 sessions", "—"
    if target_bar is not None and (stop_bar is None or forward.index(target_bar) < forward.index(stop_bar)):
        return (f"target {target} touched {target_bar['date']} (high {target_bar['high']})",
                f"stop {stop} touched {stop_bar['date']}" if stop_bar else f"stop {stop} never touched")
    return (f"stop {stop} touched {stop_bar['date']} (low {stop_bar['low']})",
            f"target {target} touched {target_bar['date']}" if target_bar else f"target {target} never touched")


def same_value(old, new) -> bool:
    """Compare a legacy claim with a derived value like for like."""
    if isinstance(old, dict) and isinstance(new, dict) and set(old) == set(new):
        return all(old[k] == new[k] for k in old)
    return old == new


def legacy_report(bundle: dict) -> list[str]:
    """Side-by-side legacy claim vs tape truth — the migration's findings."""
    trade, row = bundle["trade"], bundle["row"]
    lines = [f"{row['scenario_id']} {trade['symbol']}@{trade['recommendation_date']} "
             f"[{trade['trade_class']}]"]
    if bundle["tape_source"]:
        lines.append(f"  tape: {bundle['tape_source']['fetched']} (successor series) — "
                     f"{bundle['tape_source']['reason']}")
    lines.append(f"  geometry: {bundle['geometry']['basis']} "
                 f"| published entry {trade['entry_price']} / stop {trade['stop_loss']} / "
                 f"target {trade['target_price']} vs T_rec close {bundle['close']}")
    lines.append(f"  row: entry {row['entry_price']} band "
                 f"[{row['entry_range_min']}, {row['entry_range_max']}] stop {row['stop_loss']} "
                 f"target {row['target_price']} lot {row['lot_size']} ({row['instrument']})")
    for _tid, name, _tape, rule in dr.derivations({"trades": [migrated_record(bundle)]}):
        old, new = trade.get(name), bundle["derived"][name][1]
        if name in CLAIM_FIELDS:
            flag = "same" if same_value(old, new) else "SUPERSEDED"
        elif old is None:
            flag = "newly derived (never claimed before)"
        else:
            flag = "same" if same_value(old, new) else "SUPERSEDED"
        lines.append(f"  {name}: {old!r} -> {new!r} via {rule} [{flag}]")
    touched, other = first_touch(bundle["forward"], row["stop_loss"], row["target_price"])
    lines.append(f"  scan: {touched} | {other}")
    lines.append(f"  resolution basis: {bundle['basis']} | expected verdict "
                 f"{row['expected_verdict']} + {bundle['outcome']} -> quadrant "
                 f"{row['scoring'].get('quadrant_target', '(none: TIME has no frozen quadrant)')}")
    for field in RETIRED_CLAIM_FIELDS:
        if trade.get(field) is not None:
            lines.append(f"  {field}: {trade[field]!r} -> retired (no engine; absent, not a claim)")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pin the legacy golden records onto proof-root rows.")
    parser.add_argument("--records", default=",".join(DEFAULT_RECORDS),
                        help=("Comma-separated trade ids, or `unpinned` for every "
                              "record with no migrated row (default: "
                              + ",".join(DEFAULT_RECORDS) + ")"))
    parser.add_argument("--apply", action="store_true",
                        help="Write fixtures, rows, the dataset, hashes and the re-frozen baseline.")
    parser.add_argument("--force", action="store_true",
                        help="Re-record a record this tool already migrated (default: skip it).")
    args = parser.parse_args(argv)

    wanted = [s.strip() for s in args.records.split(",") if s.strip()]
    data = sc.read_json(GOLDEN)
    trades = {t["trade_id"]: t for t in data["trades"]}
    manifest = sc.read_json(PROOF_ROOT / sc.MANIFEST_FILE)
    existing = {r["scenario_id"]: r for r in manifest["scenarios"]}
    if args.records.strip() == "unpinned":
        wanted = [tid for tid in trades if tid not in existing]
        print(f"unpinned records: {len(wanted)}")
        if not wanted:
            print("every record is pinned")
            return 0
    unknown = [tid for tid in wanted if tid not in trades]
    if unknown:
        print(f"❌ not in the golden dataset: {unknown}", file=sys.stderr)
        return 2

    # Idempotent by design: a migrated record's geometry decision (preserved vs
    # re-derived) cannot be re-derived from its own migrated levels, so re-running
    # must not silently rewrite the disclosure — it skips, unless asked to.
    if not args.force:
        already = [tid for tid in wanted
                   if (existing.get(tid) or {}).get("authoring", {}).get("source")
                   == MIGRATION_SOURCE]
        if already:
            print(f"already migrated (skipped, --force to re-record): {', '.join(already)}")
            wanted = [tid for tid in wanted if tid not in already]
        if not wanted:
            print("nothing to do")
            return 0

    baseline_before = sc.read_json(BASELINE)
    register_before = sc.read_json(PROOF_ROOT / sc.REGISTER_FILE)
    today = date.today().isoformat()

    bundles: list[dict] = []
    for tid in wanted:
        try:
            bundle = migrate(trades[tid], today)
        except Blocked as exc:
            print(f"⛔ {tid}: BLOCKED — {exc}")
            print("   the slot stays unauthored; the tape is never negotiated (E8)")
            return 1
        bundles.append(bundle)

    for bundle in bundles:
        for line in legacy_report(bundle):
            print(line)
        print()
    _superseded, _identical = supersession_report(bundles)

    if not args.apply:
        print(f"\nDRY RUN — {len(bundles)} record(s) would be migrated; nothing written")
        return 0

    records = {b["row"]["scenario_id"]: b for b in bundles}
    for bundle in bundles:
        write_fixtures(bundle)
        recorded = record_stages(bundle)
        # The recorded bytes must satisfy the record's own class invariant —
        # otherwise the migration is wrong, not the record.
        for check in check_class_invariant(bundle, recorded):
            print(f"  {bundle['row']['scenario_id']}: recorded invariant OK — {check}")
        # …and they must reproduce the payload values the record claimed; drift is
        # superseded by the evidence and recorded on the row, never swallowed.
        drift = payload_mismatches(bundle, recorded)
        if drift:
            bundle["payload_superseded"] = drift
            bundle["row"]["authoring"]["superseded"].extend(drift)
            for item in drift:
                print(f"  SUPERSEDED {bundle['row']['scenario_id']}.{item['claim']}: "
                      f"{item['legacy']!r} -> {item['tape']!r} ({item['rule']})")
        else:
            print(f"  {bundle['row']['scenario_id']}: payload claims reproduced by "
                  "the recorded bundle")

    for tid, bundle in records.items():
        trades[tid] = migrated_record(bundle)
    data["trades"] = [trades[k] for k in sorted(trades)]
    superseded_total = sum(1 for b in bundles
                           for _ in b["row"]["authoring"].get("superseded") or [])
    identical_total = sum(1 for b in bundles for name in CLAIM_FIELDS
                          if name in b["trade"]
                          and same_value(b["trade"].get(name), b["derived"][name][1]))
    note = (f"{len(records)} record(s) migrated onto proof-root rows "
            f"({', '.join(sorted(records))}, authoring source `{MIGRATION_SOURCE}`): "
            "stage 1–2 payloads are recorded evidence (`$ref`), forward outcome "
            "claims are `$derive` references to each row's pinned tape, the "
            "engine-less claims (cone envelope, limit-entry MAE, predicted "
            "direction, plus the legacy stage-6 pledge/days-to-earnings payloads "
            "on the records that carried them) are retired to absence and declared "
            "`waived`, and no artifact was added to the quarantine register — the "
            "E5 migration meter is unchanged by design. The tape superseded "
            f"{superseded_total} legacy claim value(s) and confirmed "
            f"{identical_total} identical; every supersession is frozen in its "
            "row's `authoring.superseded`.")
    # The curator's roster note used to claim the legacy stage-6 payloads stay
    # hand-typed; this migration retires them, so the stale claim is dropped here
    # and corrected in `curate.py` for the next curation run. The aggregate note
    # replaces its own previous versions, so repeated runs cannot stack.
    data["meta"]["notes"] = [n for n in data["meta"]["notes"]
                             if "hand-typed in the existing records" not in n
                             and NOTE_MARKER not in n]
    data["meta"]["notes"].append(note)
    sc.write_json(GOLDEN, data)

    rows = {r["scenario_id"]: r for r in manifest["scenarios"]}
    for tid, bundle in records.items():
        rows[tid] = bundle["row"]
    manifest["scenarios"] = [rows[k] for k in sorted(rows)]
    manifest["generated_on"] = today
    sc.write_json(PROOF_ROOT / sc.MANIFEST_FILE, manifest)

    hashed = sc.regenerate_hashes()
    sc.re_freeze_baseline()

    # E5: quarantining retired bytes would corrupt the migration meter.
    baseline_after = sc.read_json(BASELINE)
    register_after = sc.read_json(PROOF_ROOT / sc.REGISTER_FILE)
    if (baseline_before.get("quarantined_count") != baseline_after.get("quarantined_count")
            or register_before != register_after):
        raise Blocked("the quarantine register changed — the E5 meter must not grow")
    print(f"\nmigrated {len(records)} record(s) onto proof rows; manifest now holds "
          f"{len(manifest['scenarios'])} row(s); manifest.sha256 covers {hashed} file(s); "
          f"baseline digests re-frozen; quarantine register untouched "
          f"({baseline_after['quarantined_count']} row(s), the frozen meter)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
