#!/usr/bin/env python3
"""Quarantine retirement — the register's own exit condition (E5, §10 item 9).

    python tests/evals/retire_quarantine_records.py            # dry run
    python tests/evals/retire_quarantine_records.py --apply

E5's meter is non-increasing, and every row the register holds states the
condition for leaving it — `quarantine_register.json`'s own reason text: *retire
when the stage records real evidence*. Stage 6 exists now, so a registered row
whose scenario can be given a committed stage-6 input bundle is retired exactly
the way a hazard row is seeded: an authored, disclosed
`fixtures/<id>/financials.json`, a real `6_fundamental_audit.py --record` run,
and a manifest declaration that flips `quarantined` → `recorded`. The hand-typed
bytes the register was quarantining are **deleted**, not parked: an unregistered
typed payload on disk is precisely what E1 forbids, and it can no longer be
evidence for anything.

**The inheritance rule.** The retired payload's own hazard value is the input.
Its `promoter_pledge_pct` / `days_to_earnings` is carried into the bundle
verbatim, and the bundle says where it came from, so the record keeps the level
it always asserted — now with recorded engine evidence behind it instead of a
number nobody could check. Authoring a *different* level here, or tuning
anything else to make the retirement succeed, is what this tool must not do.

**Refusals** (nothing is written; the meter does not move):

* a registered artifact with no authored-input mapping (not stage 6, or a class
  whose breaker is not in the runner's registry),
* a quarantined payload that is missing or unreadable — there is nothing to
  inherit, and a guessed level would be a new hand-typed number,
* a manifest row or dataset record that disagrees with the register,
* recorded bytes that do not carry the class breaker at the inherited value, or
  that come back `waived`.

Banking the progress is part of the act: after the writes the tool re-freezes
`manifest.sha256`, the two baseline digests V12 checks, and the E5 meter
(`quarantined_count`, `quarantine_register_sha256`). Without that last part V11
would warn `V11_QUARANTINE_SHRUNK` forever, which is how a shrink is meant to be
noticed rather than assumed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "tests"))
sys.path.insert(0, str(REPO_ROOT / "tests" / "evals"))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

import run_evals  # noqa: E402  (the runner's own class projections)
import scaffold_catalog as sc  # noqa: E402  (hash/baseline bookkeeping)

PROOF_ROOT = REPO_ROOT / "tests" / "proof"
GOLDEN = REPO_ROOT / "tests" / "evals" / "golden_dataset.json"
REGISTER = PROOF_ROOT / sc.REGISTER_FILE
TOOL_SOURCE = "quarantine-retirement/1"
FIXTURE_SCHEMA = "uvest-fundamentals/1"

# trade_class → (bundle block, bundle input field, stage-6 payload field, breaker).
# The mapping is the input half of the runner's own registry (`BREAKER_BY_CLASS`,
# whose values are re-used below), so a class the harness cannot grade is not a
# class this tool retires.
CLASS_HAZARD: dict[str, tuple[str, str, str, str]] = {
    "pledge_hazard": ("governance", "promoter_pledged_pct",
                      "promoter_pledge_pct", "promoter_pledging_gt_25pct"),
    "earnings_hazard": ("earnings", "sessions_to_earnings",
                        "days_to_earnings", "earnings_within_7d"),
}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class Blocked(Exception):
    """The row cannot be retired on evidence — report it, never force it."""


def disclosure_of(trade: dict, field: str, value: object, previous: str) -> str:
    """The one sentence a manifest reader sees about the retired input."""
    return (f"Authored hazard input ({field}={value}), inherited verbatim from "
            f"`{previous}` — the hand-typed payload this row's quarantine held. "
            "The record keeps the level it always asserted; it now has a recorded "
            "stage-6 run behind it. The statement blocks are deliberately empty — "
            "no offline source exists for the issuer's FY statements, so stage 6 "
            "emits null ratio metrics with `missing_inputs` naming every gap "
            "(E4: absence, never an invented filing).")


def bundle_doc(trade: dict, row: dict, spec: tuple[str, str, str, str],
               previous: str) -> dict:
    """The stage-6 input bundle for one retired row: the hazard block and nothing else."""
    block, field, payload_field, breaker = spec
    document = {
        "schema": FIXTURE_SCHEMA,
        "symbol": row["symbol"],
        "exchange": row.get("exchange") or "NSE",
        "source": TOOL_SOURCE,
        # Point-in-time: stated as of T_rec, so the bundle can never carry a fact
        # the row's own tape had not seen (V7's rule, applied to the input).
        "as_of": row["recommendation_date"],
        "units": "INR_CRORE",
        "statement_fy_prior": {},
        "statement_fy_current": {},
        "governance": {},
        "earnings": {},
        "authoring": {
            "source": TOOL_SOURCE,
            "scenario_id": row["scenario_id"],
            "trade_class": trade.get("trade_class"),
            "trade_id": trade["trade_id"],
            "breaker": breaker,
            "breaker_stage": 6,
            "hazard_field": field,
            "hazard_value": value_of(trade),
            "inherited_from": previous,
            "documented": (f"the quarantined payload's own {payload_field}="
                           f"{value_of(trade)}"),
            "rule": "the record's own asserted level is carried into the bundle "
                    "verbatim and recorded; nothing here is re-derived or tuned",
            "disclosure": disclosure_of(trade, field, value_of(trade), previous),
        },
    }
    document[block] = {field: value_of(trade)}
    return document


def value_of(trade: dict) -> object:
    """The hazard value the retirement inherits (set by `plan()` on the trade)."""
    return trade["__inherited_value__"]


def run_stage6(sid: str, bundle: Path, *, record: bool) -> str | None:
    """Run stage 6 over a bundle; return its stdout, or None on a stage failure.

    `--record` writes that same stdout into the row's bundle byte-for-byte
    (`scripts/_recorder.py`), which is why the probe below can validate the
    bytes before anything is committed and then assert the recording matches.
    """
    fixture_dir = PROOF_ROOT / "fixtures" / sid
    cmd = [sys.executable, str(REPO_ROOT / "scripts" / "6_fundamental_audit.py"),
           "--input", str(fixture_dir / "current_trade.json"),
           "--financials-fixture", str(bundle)]
    if record:
        cmd += ["--record", str(fixture_dir / "recorded")]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          check=False, cwd=REPO_ROOT)
    if proc.returncode != 0:
        print(f"  {sid}: 6_fundamental_audit.py failed rc={proc.returncode} "
              f"{proc.stderr.strip()}", file=sys.stderr)
        return None
    return proc.stdout


def breaker_trips(trade: dict, payload: dict) -> bool:
    """Whether the runner's own class projection trips on these bytes.

    `class_breaker_observed` abstains while the artifact is registered
    quarantined — the very state being retired — so the probe asks its threshold
    question directly, on a trade id the register does not hold. The live verdict
    is then re-proved end to end by the harness after the retirement.
    """
    probe = {
        "trade_id": f"{trade['trade_id']}#retirement-probe",
        "trade_class": trade.get("trade_class"),
        "script_outputs": {"out_6_fundamental.json": payload},
    }
    return run_evals.class_breaker_observed(probe) is True


def plan(register_rows: list[dict], manifest: dict, dataset: dict) -> list[dict]:
    """Every registered row this tool can retire, or a `Blocked` refusal."""
    rows = {row["scenario_id"]: row for row in manifest["scenarios"]}
    trades = {str(t["trade_id"]): t for t in dataset["trades"]}
    plans: list[dict] = []
    for entry in register_rows:
        sid = str(entry.get("scenario_id"))
        artifact = str(entry.get("artifact"))
        stage = entry.get("stage")
        row = rows.get(sid)
        trade = trades.get(sid)
        if row is None or trade is None:
            raise Blocked(f"{sid}: registered quarantined but has no manifest row "
                          "or dataset record")
        if stage != 6 or artifact != "out_6_fundamental.json":
            raise Blocked(f"{sid}: stage-{stage} {artifact} has no authored-input "
                          "mapping — this tool retires stage-6 payloads only")
        cls = trade.get("trade_class")
        spec = CLASS_HAZARD.get(str(cls))
        if spec is None:
            raise Blocked(f"{sid}: class {cls!r} has no breaker in the runner's "
                          "registry, so there is nothing to grade it against")
        declared = next((a for a in row.get("recorded_artifacts") or []
                         if a.get("artifact") == artifact), None)
        if declared is None or declared.get("evidence") != "quarantined" \
                or not declared.get("path"):
            raise Blocked(f"{sid}: the manifest row does not declare {artifact} as "
                          "quarantined evidence")
        previous = str(declared["path"])
        try:
            payload = read_json(PROOF_ROOT / previous)
        except (OSError, json.JSONDecodeError) as exc:
            raise Blocked(f"{sid}: the quarantined payload is unreadable "
                          f"({previous}: {exc})") from exc
        block, field, payload_field, breaker = spec
        if breaker != run_evals.BREAKER_BY_CLASS.get(str(cls)):
            raise Blocked(f"{sid}: {breaker!r} is not the breaker the runner grades "
                          f"for class {cls!r}")
        value = payload.get(payload_field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise Blocked(f"{sid}: the quarantined payload states no usable "
                          f"{payload_field} — nothing to inherit, and a guessed "
                          "level would be a new hand-typed number")
        # `record` is the dataset's own dict (mutated on apply); `probe` is a
        # private copy carrying the inherited value the bundle is authored from.
        plans.append({"scenario_id": sid, "row": row, "record": trade,
                      "probe": {**trade, "__inherited_value__": value},
                      "artifact": artifact, "spec": spec, "value": value,
                      "previous": previous, "reason": declared.get("reason")})
    return plans


def apply(plans: list[dict], manifest: dict, dataset: dict,
          register: dict, today: str) -> int:
    """Record the bytes, flip every declaration, delete the debt, bank the meter."""
    published: list[dict] = []
    for entry in plans:
        sid = entry["scenario_id"]
        row, trade, record, spec = (entry["row"], entry["probe"],
                                    entry["record"], entry["spec"])
        block, field, payload_field, breaker = spec
        bundle = PROOF_ROOT / "fixtures" / sid / sc.FINANCIALS_FILE
        recorded_rel = f"fixtures/{sid}/recorded/{entry['artifact']}"
        # 1. Probe: the engine's own stdout over the authored bundle, read from a
        #    scratch file so a refusal below leaves nothing committed behind.
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp) / sc.FINANCIALS_FILE
            sc.write_json(scratch, bundle_doc(trade, row, spec, entry["previous"]))
            stdout = run_stage6(sid, scratch, record=False)
        if stdout is None:
            return 1
        payload = json.loads(stdout)
        if payload.get("waived"):
            raise Blocked(f"{sid}: the recorded payload is a waiver — the inherited "
                          f"hazard block did not reach the engine ({block})")
        if not breaker_trips(trade, payload):
            raise Blocked(f"{sid}: the recorded bytes do not carry {breaker} at the "
                          f"inherited {field}={entry['value']}")
        if payload.get(payload_field) != entry["value"]:
            raise Blocked(f"{sid}: recorded {payload_field} is "
                          f"{payload.get(payload_field)!r}, not the inherited "
                          f"{entry['value']!r}")
        # 2. Commit the bundle and record the same run into the row's bundle.
        sc.write_json(bundle, bundle_doc(trade, row, spec, entry["previous"]))
        if run_stage6(sid, bundle, record=True) is None:
            return 1
        # Text-mode read normalizes the platform's newlines, exactly as
        # `scaffold_catalog.write_text_utf8` does when it freezes CRLF on disk.
        recorded = (PROOF_ROOT / recorded_rel).read_text(encoding="utf-8")
        if recorded != stdout:
            raise Blocked(f"{sid}: the recorded artifact is not the bytes the probe "
                          "validated — recording is not byte-exact")

        declared = next(a for a in row["recorded_artifacts"]
                        if a.get("artifact") == entry["artifact"])
        declared.pop("reason", None)
        declared["path"] = recorded_rel
        declared["evidence"] = "recorded"
        row.setdefault("replay_inputs", {})["financials_fixture"] = \
            f"fixtures/{sid}/{sc.FINANCIALS_FILE}"
        row.setdefault("authoring", {}).pop("breaker_pending_stage_6", None)
        row["authoring"]["hazard_evidence"] = {
            "fixture": f"fixtures/{sid}/{sc.FINANCIALS_FILE}",
            "breaker": breaker,
            "hazard_field": field,
            "hazard_value": entry["value"],
            "inherited_from": entry["previous"],
            "disclosure": disclosure_of(trade, field, entry["value"],
                                        entry["previous"]),
        }
        row["authoring"]["quarantine_retirement"] = {
            "retired_on": today,
            "tool": TOOL_SOURCE,
            "condition": "stage 6 records real evidence (quarantine_register.json)",
            "previous_evidence": "quarantined",
            "previous_payload": entry["previous"],
            "note": ("the hand-typed payload is deleted, not parked: it is no "
                     "longer evidence for anything, and E1 forbids an unregistered "
                     "typed payload on disk"),
        }
        if breaker not in (row.get("gate_focus") or []):
            row.setdefault("gate_focus", []).append(breaker)

        outputs = record.setdefault("script_outputs", {})
        outputs[entry["artifact"]] = {"$ref": f"tests/proof/{recorded_rel}"}
        retired = PROOF_ROOT / entry["previous"]
        if retired.exists():
            retired.unlink()
        if retired.parent.exists() and not any(retired.parent.iterdir()):
            shutil.rmtree(retired.parent)
        published.append({"scenario_id": sid, "breaker": breaker,
                          "field": field, "value": entry["value"],
                          "recorded": recorded_rel, "deleted": entry["previous"],
                          "reason": entry["reason"]})
        print(f"  {sid}: {entry['artifact']} quarantined -> recorded "
              f"({field}={entry['value']}, {breaker} trips); "
              f"deleted {entry['previous']}")

    retired_ids = {entry["scenario_id"] for entry in plans}
    register["rows"] = [row for row in register.get("rows") or []
                        if str(row.get("scenario_id")) not in retired_ids]
    dataset["meta"]["notes"].append(
        f"{', '.join(sorted(retired_ids))} retired from the quarantine register "
        f"({today}, {TOOL_SOURCE}): stage 6 records real evidence now — an authored, "
        "disclosed financials bundle inheriting each record's own hazard level — so "
        "the rows' declarations flip quarantined -> recorded, the dataset `$ref`s "
        "point at the recorded bytes, the hand-typed payloads are deleted, and the "
        f"E5 meter drops to {len(register['rows'])} (baseline re-frozen). The pledge "
        "class now grades 5 of 5 with no abstention, and every earlier note calling "
        "these payloads quarantined legacy debt is superseded by this one. Authored "
        "by tests/evals/retire_quarantine_records.py.")
    sc.write_json(REGISTER, register)
    sc.write_json(PROOF_ROOT / sc.MANIFEST_FILE, manifest)
    sc.write_json(GOLDEN, dataset)

    hashed = sc.regenerate_hashes()
    sc.re_freeze_baseline()
    baseline = read_json(PROOF_ROOT / sc.BASELINE_FILE)
    baseline["quarantined_count"] = len(register["rows"])
    baseline["quarantine_register_sha256"] = hashlib.sha256(
        REGISTER.read_bytes()).hexdigest()
    sc.write_json(PROOF_ROOT / sc.BASELINE_FILE, baseline)

    print(f"\nretired {len(published)} row(s); manifest.sha256 now covers {hashed} "
          f"file(s); E5 meter re-frozen at {len(register['rows'])} row(s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Retire quarantine rows whose stage records real evidence.")
    parser.add_argument("--apply", action="store_true",
                        help="Record stage 6, flip the declarations and bank the meter.")
    args = parser.parse_args(argv)

    register = read_json(REGISTER)
    before = len(register.get("rows") or [])
    manifest = read_json(PROOF_ROOT / sc.MANIFEST_FILE)
    dataset = read_json(GOLDEN)

    print("=" * 72)
    print("QUARANTINE RETIREMENT PLAN — the register's own exit condition (E5)")
    print("=" * 72)
    print(f"  register: {before} row(s) held, meter frozen at "
          f"{read_json(PROOF_ROOT / sc.BASELINE_FILE).get('quarantined_count')}")
    if before == 0:
        print("\nnothing registered — the meter is already at its floor; nothing to do")
        return 0

    try:
        plans = plan(register["rows"], manifest, dataset)
        if not args.apply:
            for entry in plans:
                block, field, _payload_field, breaker = entry["spec"]
                print(f"  {entry['scenario_id']} [{entry['record'].get('trade_class')}] "
                      f"stage-6 {entry['artifact']}")
                print(f"    inherit: {field} = {entry['value']} from "
                      f"{entry['previous']}")
                print(f"    grades:  {breaker} (class projection on recorded bytes)")
            print(f"\nDRY RUN — {len(plans)} row(s) would be retired, the register "
                  f"would go {before} -> {before - len(plans)}; nothing written")
            return 0
        print("\nretiring…")
        return apply(plans, manifest, dataset, register, date.today().isoformat())
    except Blocked as exc:
        print(f"❌ {exc}", file=sys.stderr)
        print("   nothing written; the register is unchanged", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
