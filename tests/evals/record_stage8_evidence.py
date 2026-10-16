#!/usr/bin/env python3
"""Record stage-8 evidence on every row that can run it, and promote the claim.

    python tests/evals/record_stage8_evidence.py            # dry run
    python tests/evals/record_stage8_evidence.py --apply    # record, declare, write

Why this tool exists. Eval dimension 6 (evals.md §3 #6, gate 6) grades the
engine's **limit entry** against the tape's realized adverse excursion — but a
record may carry that claim only when its proof row declares the *recorded*
stage-8 artifact that produced the limit price (`test_proof_manifest`'s engine
claim map: `limit_mae_pct → stage 8`), so before this the gate had nothing to
grade and emitted `[SKIP]`: an honest absence, but an absence.

Per eligible row (a row with a canonical trade and its point-in-time OHLCV
prefix — every fixture dir carries both):

1. **Run the real stage** — `8_dhan_precision.py --record` over that row's own
   fixtures. Stage 8's daily tier computes the execution zone offline, and its
   Dhan half is operator-gated (`--live`, never passed here), so the artifact
   is deterministic and joins `test_proof_manifest.RERUNNABLE`: a later edit
   that changed the zone is caught by a byte-compare, not by a reader's memory.
2. **Declare it** on the row (`recorded_artifacts`, `evidence: recorded`).
3. **Promote the claim the engine actually produced** — `limit_entry_price`
   is read *out of the recorded bytes* (`optimal_limit_buy`), and
   `limit_mae_pct` is written as a `$derive` on the row's own pinned tape: the
   number itself can never be typed, and an **unfilled** limit derives `null`
   (no position, no MAE), which gate 6 excludes from its denominator rather
   than counting as a free win.
4. **Republish** `manifest.sha256` and re-freeze the baseline in the same
   change (V12), exactly as the other seeding tools do.

Rows with no dataset record are listed and left alone.
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

ARTIFACT = "out_8_dhan.json"
STAGE = 8
PROMOTED_FIELDS = ("limit_entry_price", "limit_mae_pct")
PROMOTION_NOTE = (
    "stage 8 (BRD §4 Core Prediction 3) promotion: {count} record(s) now carry "
    "a recorded `out_8_dhan.json` — the daily-tier precision execution zone "
    "(AVWAP from the swing-low pivot, demand-order-block support, the limit "
    "band between them; the Dhan intraday/depth half is operator-gated and "
    "waived offline) — with `limit_entry_price` read out of the stage's own "
    "stdout and `limit_mae_pct` a `$derive` on the row's own pinned tape "
    "(unfilled ⇒ null, excluded from gate 6's denominator). Eval dimension 6 "
    "grades those bytes instead of skipping. Authored by "
    "tests/evals/record_stage8_evidence.py."
)


def load_dataset() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def run_stage8(row: dict) -> tuple[dict | None, str]:
    """Record stage 8 over the row's own fixtures → (payload, note)."""
    sid = row["scenario_id"]
    inputs = row.get("replay_inputs") or {}
    canonical_rel = inputs.get("canonical_trade")
    prefixes = inputs.get("truncated_fixtures") or []
    if not canonical_rel or not prefixes:
        return None, "no canonical trade / OHLCV prefix"
    canonical = PROOF_ROOT / canonical_rel
    prefix = PROOF_ROOT / prefixes[0]
    if not canonical.exists() or not prefix.exists():
        return None, "fixture missing on disk"
    recorded = PROOF_ROOT / "fixtures" / sid / "recorded"
    recorded.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "8_dhan_precision.py"),
         "--input", str(canonical),
         "--ohlcv-fixture", str(prefix),
         "--record", str(recorded)],
        capture_output=True, check=False, cwd=REPO_ROOT)
    if proc.returncode != 0:
        return None, f"stage 8 exited {proc.returncode}: {proc.stderr[:200]}"
    artifact = recorded / ARTIFACT
    if not artifact.exists():
        return None, "stage 8 recorded no artifact"
    return json.loads(artifact.read_text(encoding="utf-8")), "recorded"


def declare(row: dict, sid: str) -> None:
    """Declare the recorded artifact (replacing any earlier stage-8 entry)."""
    rel = f"fixtures/{sid}/recorded/{ARTIFACT}"
    entries = [a for a in row.get("recorded_artifacts") or []
               if not (isinstance(a, dict) and a.get("stage") == STAGE)]
    entries.append({"stage": STAGE, "artifact": ARTIFACT, "path": rel,
                    "evidence": "recorded"})
    entries.sort(key=lambda a: (a.get("stage") if isinstance(a.get("stage"), int)
                                else 99))
    row["recorded_artifacts"] = entries


def _tape_of(trade: dict, sid: str) -> str:
    """The row's own pinned tape — the one `market_mae_pct` already derives on."""
    spec = (trade.get("market_mae_pct") or {})
    if isinstance(spec, dict) and isinstance(spec.get("$derive"), dict):
        return spec["$derive"]["tape"]
    return f"tests/proof/fixtures/{sid}/forward_tape.json"


def promote(trade: dict, payload: dict, sid: str) -> dict:
    """The dataset patch, read out of the recorded bytes (never retyped).

    The claim is gated on the *zone*, not on `waived`: that flag reports the
    Dhan Tier-2 half (BRD §8.1's offline degradation), which is waived on every
    offline run while the daily Tier-1 zone still ships — nulling the limit
    here would throw away exactly the number gate 6 grades. A whole-payload
    waiver (`_waived_payload`) carries `optimal_limit_buy: None`, so absence
    still propagates as absence on its own.
    """
    limit = payload.get("optimal_limit_buy")
    mae = None
    if isinstance(limit, (int, float)):
        mae = {"$derive": {"tape": _tape_of(trade, sid), "rule": "limit_mae_pct"}}
    return {"limit_entry_price": limit, "limit_mae_pct": mae}


def coverage_of(dataset: dict) -> dict:
    """A preview of what gate 6 will see, resolved the way the runner resolves."""
    trades = dr.materialize(dataset)[0]["trades"]
    pairs = filled = better = 0
    for t in trades:
        mkt, lim = t.get("market_mae_pct"), t.get("limit_mae_pct")
        if mkt is None or lim is None:
            continue
        pairs += 1
        filled += 1
        better += 1 if lim < mkt else 0
    unfilled = sum(1 for t in trades
                   if t.get("limit_entry_price") is not None
                   and t.get("limit_mae_pct") is None)
    return {"graded_pairs": pairs, "unfilled": unfilled,
            "limit_better_pct": round(100.0 * better / pairs, 2) if pairs else None}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Record stage-8 evidence and promote its limit claim.")
    parser.add_argument("--apply", action="store_true",
                        help="write artifacts, rows, dataset, hashes and baseline")
    args = parser.parse_args(argv)

    manifest = sc.read_json(PROOF_ROOT / sc.MANIFEST_FILE)
    rows = manifest["scenarios"]
    dataset = load_dataset()
    trades = {t["trade_id"]: t for t in dataset.get("trades", [])}

    print("=" * 72)
    print("STAGE-8 EVIDENCE PLAN — one real stage run per eligible row")
    print("=" * 72)
    promoted: list[str] = []
    skipped: list[tuple[str, str]] = []
    for row in rows:
        sid = row["scenario_id"]
        inputs = row.get("replay_inputs") or {}
        eligible = bool(inputs.get("canonical_trade")) and bool(
            inputs.get("truncated_fixtures"))
        if not args.apply:
            if eligible:
                print(f"  {sid} {row['symbol']} {row['recommendation_date']}: "
                      f"would record {ARTIFACT}")
            else:
                skipped.append((sid, "no canonical trade / OHLCV prefix"))
            continue
        payload, note = run_stage8(row)
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
        trade.setdefault("script_outputs", {})[ARTIFACT] = {
            "$ref": f"tests/proof/fixtures/{sid}/recorded/{ARTIFACT}"}
        before = {f: trade.get(f) for f in PROMOTED_FIELDS}
        trade.update(values)
        promoted.append(sid)
        print(f"  {sid} {trade['symbol']}: limit={values['limit_entry_price']} "
              f"(zone=[{(payload.get('optimal_limit_range') or [None, None])[0]}, "
              f"{(payload.get('optimal_limit_range') or [None, None])[1]}], "
              f"source={payload.get('precision_source')}, "
              f"dhan={'waived: ' + str(payload.get('waived_reason')) if payload.get('waived') else 'live'})")
        for field, old in before.items():
            if old not in (None, values[field]):
                print(f"    note: {field} {old!r} -> {values[field]!r}")

    if not args.apply:
        print(f"\nDRY RUN — nothing written; "
              f"{len(skipped)} row(s) cannot run stage 8")
        for sid, reason in skipped:
            print(f"    {sid}: {reason}")
        return 0

    if not promoted:
        print("no row could be recorded; nothing written", file=sys.stderr)
        return 1

    preview = coverage_of(dataset)
    if PROMOTION_NOTE.format(count=len(promoted)) not in dataset["meta"]["notes"]:
        dataset["meta"]["notes"].append(PROMOTION_NOTE.format(count=len(promoted)))
    sc.write_json(PROOF_ROOT / sc.MANIFEST_FILE, manifest)
    sc.write_json(GOLDEN, dataset)
    hashes = sc.regenerate_hashes()
    sc.re_freeze_baseline()
    print(f"\npromoted {len(promoted)} row(s): {', '.join(promoted)}")
    print(f"gate-6 preview: {preview['graded_pairs']} filled pair(s), "
          f"{preview['unfilled']} unfilled limit(s) excluded, "
          f"limit-better fraction {preview['limit_better_pct']}%")
    print(f"re-published manifest.sha256 ({hashes} entries) and re-froze the baseline")
    if skipped:
        print(f"{len(skipped)} row(s) could not run stage 8:")
        for sid, reason in skipped:
            print(f"    {sid}: {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
