#!/usr/bin/env python3
"""Record stage-9 evidence on every row that can run it.

    python tests/evals/record_stage9_evidence.py            # dry run
    python tests/evals/record_stage9_evidence.py --apply    # record, declare, write

Why this tool exists. Stage 9 is the sole owner of the pre-entry verdict (BRD
§10, §9.1 row 9), and the report's header transcribes it — but a record may
carry a verdict only when its proof row declares the *recorded* stage-9 artifact
that produced it. Before this, every one of the 58 reports printed
`verdict pending stage-9 evidence`: an honest absence, but an absence — and the
badge's composite score, calibrated P(win) and expectancy were therefore never
graded by the Zero-Hallucination gate either.

Per eligible row (a canonical trade with at least one already-recorded upstream
artifact — the evidence stage 9 actually scores):

1. **Run the real stage** — `9_decision_engine.py --record` over that row's own
   recorded payloads in stage order (`--payload`), so the artifact is stage
   stdout (hashed in `manifest.sha256`) and the verdict is a function of
   declared evidence only, never of a payload someone typed. Stage 9 is offline
   and byte-deterministic — payloads are keyed by basename, and the weights are
   the frozen §10.2 set because `--thresholds` is never passed here — so it
   joins `test_proof_manifest.RERUNNABLE`: a later edit that changed a score or
   a verdict is caught by a byte-compare, not by a reader's memory.
2. **Declare it** on the row (`recorded_artifacts`, `evidence: recorded`).
3. **Reference it** — `script_outputs.out_9_decision.json` `$ref`s the
   artifact, so the badge's figures are backed by the record's own JSON
   (`report_diff`) and the dataset and the proof root cannot drift. Stage 9
   contributes **no dataset claim field**: the engine-claim map (cone, race
   probability, limit MAE) is unchanged, and the verdict is read out of the
   referenced payload — never copied into the record beside it.
4. **Republish** `manifest.sha256` and re-freeze the baseline in the same change
   (V12), exactly as the other seeding tools do.

Rows with no recorded upstream artifact, or no dataset record, are listed and
left alone — a verdict computed over evidence nobody recorded would be a
verdict over nothing.
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

import scaffold_catalog as sc  # noqa: E402  (proof-root bookkeeping + JSON helpers)

ARTIFACT = "out_9_decision.json"
STAGE = 9
EVIDENCE_NOTE = (
    "stage 9 (BRD §10 decision engine) evidence: {count} record(s) now carry a "
    "recorded `out_9_decision.json` — the canonical pre-entry verdict, the "
    "composite score over the frozen §10.2 weights (its maximum reduced by the "
    "§10.4 waiver rule whenever a source is absent or waived), the calibrated "
    "P(win) and the expectancy — run over each row's own already-recorded "
    "payloads in stage order and `$ref`d from `script_outputs`. The report's "
    "header badge transcribes those bytes, so every figure under it is graded "
    "by the Zero-Hallucination gate instead of printing an absence. Authored by "
    "tests/evals/record_stage9_evidence.py."
)


def load_dataset() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def upstream_payloads(row: dict) -> list[Path]:
    """The row's own recorded evidence, in stage order — stage 9's inputs.

    Stage 9's own entry is excluded so a re-record scores the *original*
    evidence rather than its previous verdict (and `declare()` replaces it).
    """
    entries = [a for a in row.get("recorded_artifacts") or []
               if isinstance(a, dict) and a.get("evidence") == "recorded"
               and a.get("stage") != STAGE and a.get("path")]
    entries.sort(key=lambda a: a["stage"] if isinstance(a.get("stage"), int) else 99)
    return [PROOF_ROOT / e["path"] for e in entries]


def run_stage9(row: dict) -> tuple[dict | None, str]:
    """Record stage 9 over the row's own payloads → (payload, note)."""
    sid = row["scenario_id"]
    inputs = row.get("replay_inputs") or {}
    canonical_rel = inputs.get("canonical_trade")
    if not canonical_rel:
        return None, "no canonical trade"
    canonical = PROOF_ROOT / canonical_rel
    if not canonical.exists():
        return None, f"canonical trade missing: {canonical_rel}"
    payloads = upstream_payloads(row)
    if not payloads:
        return None, "no recorded upstream artifact to score"
    missing = next((p for p in payloads if not p.exists()), None)
    if missing is not None:
        return None, f"declared payload missing on disk: {missing.name}"
    recorded = PROOF_ROOT / "fixtures" / sid / "recorded"
    recorded.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(SCRIPTS / "9_decision_engine.py"),
           "--input", str(canonical)]
    for payload in payloads:
        cmd += ["--payload", str(payload)]
    cmd += ["--record", str(recorded)]
    proc = subprocess.run(cmd, capture_output=True, check=False, cwd=REPO_ROOT)
    if proc.returncode != 0:
        return None, f"stage 9 exited {proc.returncode}: {proc.stderr[:200]}"
    artifact = recorded / ARTIFACT
    if not artifact.exists():
        return None, "stage 9 recorded no artifact"
    return json.loads(artifact.read_text(encoding="utf-8")), "recorded"


def declare(row: dict, sid: str) -> None:
    """Declare the recorded artifact (replacing any earlier stage-9 entry)."""
    rel = f"fixtures/{sid}/recorded/{ARTIFACT}"
    entries = [a for a in row.get("recorded_artifacts") or []
               if not (isinstance(a, dict) and a.get("stage") == STAGE)]
    entries.append({"stage": STAGE, "artifact": ARTIFACT, "path": rel,
                    "evidence": "recorded"})
    entries.sort(key=lambda a: (a.get("stage") if isinstance(a.get("stage"), int)
                                else 99))
    row["recorded_artifacts"] = entries


def summary(payload: dict) -> str:
    return (f"{payload.get('verdict')} "
            f"{payload.get('final_score')}/{payload.get('max_possible_score')} "
            f"({payload.get('effective_score_pct')}%) "
            f"P(win)={payload.get('p_win')} E={payload.get('expectancy')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Record stage-9 decision evidence on every eligible row.")
    parser.add_argument("--apply", action="store_true",
                        help="write artifacts, rows, dataset, hashes and baseline")
    args = parser.parse_args(argv)

    manifest = sc.read_json(PROOF_ROOT / sc.MANIFEST_FILE)
    rows = manifest["scenarios"]
    dataset = load_dataset()
    trades = {t["trade_id"]: t for t in dataset.get("trades", [])}

    print("=" * 72)
    print("STAGE-9 EVIDENCE PLAN — one real stage run per eligible row")
    print("=" * 72)
    recorded: list[str] = []
    skipped: list[tuple[str, str]] = []
    for row in rows:
        sid = row["scenario_id"]
        inputs = row.get("replay_inputs") or {}
        trade = trades.get(sid)
        # Eligibility is decided *before* the stage runs: a row with no golden
        # record would leave an artifact nothing declares, references or grades.
        no_record = trade is None
        eligible = (not no_record and bool(inputs.get("canonical_trade"))
                    and bool(upstream_payloads(row)))
        if not args.apply:
            if eligible:
                print(f"  {sid} {row['symbol']} {row['recommendation_date']}: "
                      f"would record {ARTIFACT}")
            elif no_record:
                skipped.append((sid, "no dataset record with that trade_id"))
            else:
                skipped.append((sid, "no canonical trade / recorded upstream payload"))
            continue
        if no_record:
            skipped.append((sid, "no dataset record with that trade_id"))
            print(f"  {sid}: skipped — no dataset record")
            continue
        payload, note = run_stage9(row)
        if payload is None:
            skipped.append((sid, note))
            print(f"  {sid}: skipped — {note}")
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
        print(f"{len(skipped)} row(s) could not record stage 9:")
        for sid, reason in skipped:
            print(f"    {sid}: {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
