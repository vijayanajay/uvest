#!/usr/bin/env python3
"""Record stage-3 DTW evidence on every row that can run it.

    python tests/evals/record_stage3_evidence.py            # dry run
    python tests/evals/record_stage3_evidence.py --apply    # derive, record, write

Why this tool exists. Stage 3's `out_3_dtw.json` was waived on **every** golden
record — the one retained scoring source (BRD §10.2 Quant & ML Edge, 25 pts)
no row carried — and BRD §10.4's waiver rule caps any verdict with at least
one waived stage at GO_WEAK (50% size). So `GO` was unreachable across the
whole corpus *on a waiver*, not on a measured score. The tapes each row's
geometry was authored from are already committed
(`tests/fixtures/history/<SYMBOL>_<YYYYMM>.json`, one real yfinance fetch per
symbol-month), so stage 3's evidence is recordable offline and point-in-time
exactly like stages 5/6/7's bundles.

Per eligible row (a dataset record, a canonical trade, and a tape whose
T_rec prefix reaches the stage's own MIN_HISTORY sessions):

1. **Derive the row's history bundle** — `fixtures/<id>/history.json`: the
   sessions of the row's own symbol-month tape truncated to `date <= T_rec`
   (the harness, not the stage, performs truncation — the same rule the OHLCV
   prefix obeys), with the derivation (`derived_from`, cutoff) disclosed
   inside the bundle. Nothing is fetched and nothing is retyped.
2. **Run the real stage** — `3_dtw_pattern.py --record --history-fixture`, so
   the artifact is stage stdout (hashed in `manifest.sha256`). Stage 3 is
   offline and byte-deterministic, so it joins
   `test_proof_manifest.RERUNNABLE`: V10 byte-compares every recorded
   artifact against a fresh run.
3. **Declare it** on the row (`recorded_artifacts`, `evidence: recorded`) and
   `$ref` it from `script_outputs`, exactly like the other recorders. Stage 3
   contributes **no dataset claim field**: its numbers reach stage 9 (the
   §10.2 Quant & ML block and the §10.4 waiver check) and the report through
   the recorded payload, never a copy typed beside it.
4. **Republish** `manifest.sha256` and re-freeze the baseline in the same
   change (V12), exactly as the other recording tools do.

Rows with no dataset record, no tape, or a tape too short to warp are listed
and left alone — a waiver path over a truncated bundle would record the
absence this tool exists to end, so it is skipped and named instead.
"""

from __future__ import annotations

import argparse
import importlib.util
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
TAPE_DIR = TESTS_DIR / "fixtures" / "history"
for splice in (str(EVALS_DIR), str(TESTS_DIR)):
    if splice not in sys.path:
        sys.path.insert(0, splice)

import scaffold_catalog as sc  # noqa: E402  (proof-root bookkeeping + JSON helpers)

# The stage's own frozen minimum, read from the stage — never retyped here.
_spec = importlib.util.spec_from_file_location(
    "uvest_stage3", SCRIPTS / "3_dtw_pattern.py")
_stage3 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_stage3)
MIN_HISTORY = _stage3.MIN_HISTORY  # WINDOW + FORWARD + K_MAX + 5 = 72 sessions

ARTIFACT = "out_3_dtw.json"
STAGE = 3
EVIDENCE_NOTE = (
    "stage 3 (BRD §5.1 DTW analog engine) evidence: {count} record(s) now carry "
    "a recorded `out_3_dtw.json` — the 30-session Z-scored window warped against "
    "the row's own T_rec-truncated symbol-month tape (fastdtw, K=15 analogs, "
    "forward 22d win rate at the strict > +4% boundary) — declared on the proof "
    "row and `$ref`d from `script_outputs`. Stage 3 was the one retained scoring "
    "source waived on every record, so §10.4's waiver rule capped every verdict "
    "at GO_WEAK; the cap now fires only where a source is genuinely absent. "
    "Authored by tests/evals/record_stage3_evidence.py."
)


def load_dataset() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def tape_path(row: dict) -> Path:
    """The committed point-in-time tape the row's geometry was authored from."""
    ym = str(row["recommendation_date"]).replace("-", "")[:6]
    return TAPE_DIR / f"{row['symbol']}_{ym}.json"


def bundle_rel(sid: str) -> str:
    return f"fixtures/{sid}/history.json"


def derive_bundle(row: dict) -> tuple[dict | None, str]:
    """The row's history bundle: its own tape truncated to `date <= T_rec`.

    Returns `(bundle, note)`; `(None, reason)` when the row cannot get one —
    a missing tape, or a prefix shorter than the stage's own minimum, which
    would leave stage 3 on its waiver path and record the absence this tool
    exists to end.
    """
    cutoff = ((row.get("replay_inputs") or {}).get("truncation") or {}).get("cutoff")
    if not cutoff:
        return None, "no truncation cutoff declared"
    tape_file = tape_path(row)
    if not tape_file.exists():
        return None, f"no committed tape at {tape_file.relative_to(REPO_ROOT)}"
    tape = json.loads(tape_file.read_text(encoding="utf-8"))
    sessions = [s for s in tape.get("sessions") or []
                if isinstance(s, dict) and str(s.get("date")) <= cutoff]
    if len(sessions) < MIN_HISTORY:
        return None, (f"tape prefix has {len(sessions)} session(s) < "
                      f"MIN_HISTORY {MIN_HISTORY} — stage 3 would waive")
    return {
        "symbol": tape.get("symbol", row["symbol"]),
        "exchange": tape.get("exchange", row.get("exchange", "NSE")),
        "interval": tape.get("interval", "1d"),
        "source": tape.get("source", "yfinance"),
        "rec_date": cutoff,
        "derived_from": tape_file.relative_to(REPO_ROOT).as_posix(),
        "truncation": {"cutoff": cutoff, "rule": "date <= T_rec",
                       "sessions_retained": len(sessions)},
        "sessions": sessions,
    }, f"{len(sessions)} session(s) <= {cutoff}"


def run_stage3(row: dict) -> tuple[dict | None, str]:
    """Record stage 3 over the row's own canonical trade + history bundle."""
    sid = row["scenario_id"]
    inputs = row.get("replay_inputs") or {}
    canonical_rel = inputs.get("canonical_trade")
    if not canonical_rel:
        return None, "no canonical trade"
    canonical = PROOF_ROOT / canonical_rel
    if not canonical.exists():
        return None, f"canonical trade missing: {canonical_rel}"
    history = PROOF_ROOT / bundle_rel(sid)
    if not history.exists():
        return None, "history bundle missing on disk"
    recorded = PROOF_ROOT / "fixtures" / sid / "recorded"
    recorded.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "3_dtw_pattern.py"),
         "--input", str(canonical),
         "--history-fixture", str(history),
         "--record", str(recorded)],
        capture_output=True, check=False, cwd=REPO_ROOT)
    if proc.returncode != 0:
        return None, f"stage 3 exited {proc.returncode}: {proc.stderr[:200]}"
    artifact = recorded / ARTIFACT
    if not artifact.exists():
        return None, "stage 3 recorded no artifact"
    return json.loads(artifact.read_text(encoding="utf-8")), "recorded"


def declare(row: dict, sid: str) -> None:
    """Declare the recorded artifact (replacing any earlier stage-3 entry)."""
    rel = f"fixtures/{sid}/recorded/{ARTIFACT}"
    entries = [a for a in row.get("recorded_artifacts") or []
               if not (isinstance(a, dict) and a.get("stage") == STAGE)]
    entries.append({"stage": STAGE, "artifact": ARTIFACT, "path": rel,
                    "evidence": "recorded"})
    entries.sort(key=lambda a: (a.get("stage") if isinstance(a.get("stage"), int)
                                else 99))
    row["recorded_artifacts"] = entries


def summary(payload: dict) -> str:
    if payload.get("waived"):
        return "waived (no usable history)"
    return (f"k={payload.get('k_analogs')} "
            f"win_rate={payload.get('dtw_win_rate_22d')} "
            f"median={payload.get('dtw_median_return')} "
            f"mae={payload.get('dtw_mae_drawdown')} "
            f"confidence={payload.get('analog_confidence')} "
            f"as_of={payload.get('as_of')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Record stage-3 DTW evidence on every eligible row.")
    parser.add_argument("--apply", action="store_true",
                        help="write bundles, artifacts, rows, dataset, hashes, baseline")
    args = parser.parse_args(argv)

    manifest = sc.read_json(PROOF_ROOT / sc.MANIFEST_FILE)
    rows = manifest["scenarios"]
    dataset = load_dataset()
    trades = {t["trade_id"]: t for t in dataset.get("trades", [])}

    print("=" * 72)
    print("STAGE-3 EVIDENCE PLAN — derive the bundle, then one real stage run")
    print("=" * 72)
    recorded: list[str] = []
    skipped: list[tuple[str, str]] = []
    for row in rows:
        sid = row["scenario_id"]
        trade = trades.get(sid)
        no_record = trade is None
        if no_record:
            skipped.append((sid, "no dataset record with that trade_id"))
            if not args.apply:
                print(f"  {sid} {row['symbol']} {row['recommendation_date']}: skipped")
            continue
        bundle, note = derive_bundle(row)
        if bundle is None:
            skipped.append((sid, note))
            if not args.apply:
                print(f"  {sid} {row['symbol']} {row['recommendation_date']}: "
                      f"skipped — {note}")
            continue
        if not args.apply:
            print(f"  {sid} {row['symbol']} {row['recommendation_date']}: "
                  f"would derive {bundle_rel(sid)} ({note}) and record {ARTIFACT}")
            continue
        (PROOF_ROOT / bundle_rel(sid)).write_text(
            json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
        row.setdefault("replay_inputs", {})["history_fixture"] = bundle_rel(sid)
        payload, run_note = run_stage3(row)
        if payload is None:
            skipped.append((sid, run_note))
            print(f"  {sid}: skipped — {run_note}")
            continue
        declare(row, sid)
        trade.setdefault("script_outputs", {})[ARTIFACT] = {
            "$ref": f"tests/proof/fixtures/{sid}/recorded/{ARTIFACT}"}
        recorded.append(sid)
        print(f"  {sid} {trade['symbol']}: {summary(payload)}")

    if not args.apply:
        print(f"\nDRY RUN — nothing written; "
              f"{len(rows) - len(skipped)}/{len(rows)} row(s) would record {ARTIFACT}")
        for sid, reason in skipped:
            print(f"    {sid}: {reason}")
        return 0

    if not recorded:
        print("no row could be recorded; nothing written", file=sys.stderr)
        return 1

    if EVIDENCE_NOTE.format(count=len(recorded)) not in dataset["meta"]["notes"]:
        dataset["meta"]["notes"].append(EVIDENCE_NOTE.format(count=len(recorded)))
    sc.write_json(PROOF_ROOT / sc.MANIFEST_FILE, manifest)
    sc.write_json(GOLDEN, dataset)
    hashes = sc.regenerate_hashes()
    sc.re_freeze_baseline()
    print(f"\nrecorded {len(recorded)} row(s): {', '.join(recorded)}")
    print(f"re-published manifest.sha256 ({hashes} entries) and re-froze the baseline")
    if skipped:
        print(f"{len(skipped)} row(s) could not record stage 3:")
        for sid, reason in skipped:
            print(f"    {sid}: {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
