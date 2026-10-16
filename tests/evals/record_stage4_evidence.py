#!/usr/bin/env python3
"""Record stage-4 evidence on the proof rows that can run it, and promote the claims.

    python tests/evals/record_stage4_evidence.py            # dry run
    python tests/evals/record_stage4_evidence.py --apply    # record, declare, write

Why this tool exists. Eval dimensions 4 and 5 grade engine output — a calibrated
direction probability against the tape's realized direction (Brier) and a cone
envelope against the tape's realized forward range (coverage). A record may carry
those claims only when its proof row declares the *recorded* stage-4 artifact
that produced them (`tests/test_proof_manifest.py`, E4), so before this the gates
had nothing to grade and emitted `[SKIP]`: an honest absence, but an absence.

Per eligible row (a row with both a point-in-time OHLCV prefix and the same
`uvest-market-context/1` bundle stage 7 classifies):

1. **Run the real stage** — `4_ml_direction.py --record` over that row's own
   fixtures, so the artifact is a stage stdout (hashed in `manifest.sha256`),
   never a hand-typed payload. Stage 4 is offline-runnable, so it also joins
   `test_proof_manifest.RERUNNABLE`: a later edit that changed the cone or the
   probability is caught by a byte-compare, not by a reader's memory.
2. **Declare it** on the row (`recorded_artifacts`, `evidence: recorded`).
3. **Promote the claims the engine actually produced** — `cone_p10`, `cone_p90`,
   `predicted_direction`, `predicted_probability` are read *out of the recorded
   bytes* and the artifact is referenced from `script_outputs` with `$ref`, so
   the dataset and the proof root cannot drift. A row whose artifact is waived
   keeps `null` claims: the engine ran and had nothing to say, which is not the
   same as a number someone supplied.
4. **Republish** `manifest.sha256` and re-freeze the baseline in the same change
   (V12), exactly as the other seeding tools do.

Rows with no market bundle are listed and left alone — the trained universe's
limits are disclosed rather than papered over with a second, synthetic bundle.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent.parent
EVALS_DIR = TESTS_DIR / "evals"
REPO_ROOT = TESTS_DIR.parent
PROOF_ROOT = TESTS_DIR / "proof"
GOLDEN = EVALS_DIR / "golden_dataset.json"
SCRIPTS = REPO_ROOT / "scripts"
for splice in (str(EVALS_DIR), str(TESTS_DIR)):
    if splice not in sys.path:
        sys.path.insert(0, splice)

import dataset_refs as dr      # noqa: E402  (resolves the tape-derived claims)
import scaffold_catalog as sc  # noqa: E402  (proof-root bookkeeping + JSON helpers)

ARTIFACT = "out_4_ml.json"
STAGE = 4
PROMOTED_FIELDS = ("cone_p10", "cone_p90", "predicted_direction",
                   "predicted_probability")
PROMOTION_NOTE = (
    "stage 4 (BRD §5.2) promotion: {count} record(s) now carry a recorded "
    "`out_4_ml.json` — the cone envelope ([P10, P90]), the direction label and "
    "the calibrated direction probability (`direction_probability`, i.e. P(up) = "
    "P(BULL) + φ·P(NEUTRAL)) read out of the stage's own stdout over the row's "
    "pinned ≤T_rec tape and its market-context bundle, with the artifact "
    "`$ref`d from `script_outputs`. Eval dimensions 4 and 5 grade those bytes "
    "instead of skipping; a row whose artifact is waived keeps null claims. "
    "Authored by tests/evals/record_stage4_evidence.py."
)


def load_dataset() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def fixture_path(sid: str, name: str) -> Path:
    return PROOF_ROOT / "fixtures" / sid / name


def run_stage4(row: dict) -> tuple[dict | None, str]:
    """Record stage 4 over the row's own fixtures → (payload, note)."""
    sid = row["scenario_id"]
    inputs = row.get("replay_inputs") or {}
    canonical = PROOF_ROOT / (inputs.get("canonical_trade") or "")
    prefixes = inputs.get("truncated_fixtures") or []
    bundle_rel = inputs.get("regime_fixture")
    if not canonical.exists() or not prefixes:
        return None, "no canonical trade / OHLCV prefix"
    if not bundle_rel:
        return None, "no market-context bundle (stage 4 would waive by construction)"
    bundle = PROOF_ROOT / bundle_rel
    if not bundle.exists():
        return None, f"declared bundle missing on disk: {bundle_rel}"
    recorded = PROOF_ROOT / "fixtures" / sid / "recorded"
    recorded.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "4_ml_direction.py"),
         "--input", str(canonical),
         "--ohlcv-fixture", str(PROOF_ROOT / prefixes[0]),
         "--context-fixture", str(bundle),
         "--record", str(recorded)],
        capture_output=True, check=False, cwd=REPO_ROOT)
    if proc.returncode != 0:
        return None, f"stage 4 exited {proc.returncode}: {proc.stderr[:200]}"
    artifact = recorded / ARTIFACT
    if not artifact.exists():
        return None, "stage 4 recorded no artifact"
    return json.loads(artifact.read_text(encoding="utf-8")), "recorded"


def declare(row: dict, sid: str) -> None:
    """Declare the recorded artifact (replacing any earlier stage-4 entry)."""
    rel = f"fixtures/{sid}/recorded/{ARTIFACT}"
    entries = [a for a in row.get("recorded_artifacts") or []
               if not (isinstance(a, dict) and a.get("stage") == STAGE)]
    entries.append({"stage": STAGE, "artifact": ARTIFACT, "path": rel,
                    "evidence": "recorded"})
    entries.sort(key=lambda a: (a.get("stage") if isinstance(a.get("stage"), int)
                                else 99))
    row["recorded_artifacts"] = entries


def promote(trade: dict, payload: dict, sid: str) -> dict:
    """The dataset patch, read out of the recorded bytes (never retyped)."""
    if payload.get("waived"):
        return {field: None for field in PROMOTED_FIELDS}
    cone = payload.get("price_cone_p10_p50_p90") or {}
    return {
        "cone_p10": cone.get("p10"),
        "cone_p90": cone.get("p90"),
        "predicted_direction": payload.get("direction_label"),
        "predicted_probability": payload.get("direction_probability"),
    }


def coverage_of(trade: dict, values: dict) -> dict:
    """A preview of what gate 5 will see, resolved the way the runner resolves it.

    The forward range is a `$derive` on most records, so an inline read would
    report nothing: the tape rule is applied here, exactly as
    `dataset_refs.materialize` applies it at load.
    """
    fwd = dr.materialize({"trades": [trade]})[0]["trades"][0].get("forward_prices_22d")
    lo = hi = None
    if isinstance(fwd, dict):
        if isinstance(fwd.get("min"), (int, float)):
            lo = fwd["min"]
        if isinstance(fwd.get("max"), (int, float)):
            hi = fwd["max"]
    p10, p90 = values.get("cone_p10"), values.get("cone_p90")
    if lo is None or hi is None or p10 is None or p90 is None:
        return {"covered": None,
                "reason": "no resolvable forward range / no cone"}
    return {"covered": bool(p10 <= lo and hi <= p90), "min": lo, "max": hi,
            "cone": [p10, p90]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Record stage-4 evidence and promote its claims.")
    parser.add_argument("--apply", action="store_true",
                        help="write artifacts, rows, dataset, hashes and baseline")
    args = parser.parse_args(argv)

    manifest = sc.read_json(PROOF_ROOT / sc.MANIFEST_FILE)
    rows = manifest["scenarios"]
    dataset = load_dataset()
    trades = {t["trade_id"]: t for t in dataset.get("trades", [])}

    print("=" * 72)
    print("STAGE-4 EVIDENCE PLAN — one real stage run per eligible row")
    print("=" * 72)
    promoted: list[str] = []
    skipped: list[tuple[str, str]] = []
    for row in rows:
        sid = row["scenario_id"]
        payload, note = run_stage4(row) if args.apply else (None, "dry run")
        inputs = row.get("replay_inputs") or {}
        eligible = bool(inputs.get("regime_fixture")) and bool(
            inputs.get("truncated_fixtures"))
        if not args.apply:
            if eligible:
                print(f"  {sid} {row['symbol']} {row['recommendation_date']}: "
                      f"would record {ARTIFACT}")
            else:
                skipped.append((sid, "no market-context bundle"))
            continue
        if payload is None:
            skipped.append((sid, note))
            print(f"  {sid}: skipped — {note}")
            continue
        trade = trades.get(sid)
        if trade is None:
            skipped.append((sid, "no dataset record with that trade_id"))
            print(f"  {sid}: skipped — no dataset record")
            continue
        values = promote(trade, payload, sid)
        declare(row, sid)
        trade["script_outputs"]["out_4_ml.json"] = {
            "$ref": f"tests/proof/fixtures/{sid}/recorded/{ARTIFACT}"}
        before = {f: trade.get(f) for f in PROMOTED_FIELDS}
        trade.update(values)
        promoted.append(sid)
        preview = coverage_of(trade, values)
        print(f"  {sid} {trade['symbol']} {trade['recommendation_date']}: "
              f"{'waived' if payload.get('waived') else payload.get('direction_label')} "
              f"P(up)={values['predicted_probability']} "
              f"cone=[{values['cone_p10']}, {values['cone_p90']}] "
              f"tape-covered={preview.get('covered')}")
        for field, old in before.items():
            if old not in (None, values[field]):
                print(f"    note: {field} {old!r} -> {values[field]!r}")

    if not args.apply:
        print(f"\nDRY RUN — nothing written; "
              f"{len(skipped)} row(s) cannot run stage 4")
        for sid, reason in skipped:
            print(f"    {sid}: {reason}")
        return 0

    if not promoted:
        print("no row could be recorded; nothing written", file=sys.stderr)
        return 1

    if PROMOTION_NOTE.format(count=len(promoted)) not in dataset["meta"]["notes"]:
        dataset["meta"]["notes"].append(
            PROMOTION_NOTE.format(count=len(promoted)))
    sc.write_json(PROOF_ROOT / sc.MANIFEST_FILE, manifest)
    sc.write_json(GOLDEN, dataset)
    hashes = sc.regenerate_hashes()
    sc.re_freeze_baseline()
    print(f"\npromoted {len(promoted)} row(s): {', '.join(promoted)}")
    print(f"re-published manifest.sha256 ({hashes} entries) and re-froze the baseline")
    if skipped:
        print(f"{len(skipped)} row(s) could not run stage 4:")
        for sid, reason in skipped:
            print(f"    {sid}: {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
