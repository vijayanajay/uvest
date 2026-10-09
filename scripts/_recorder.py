#!/usr/bin/env python3
"""Shared pipeline recording helper (used by every stage script).

`--record <dir>` turns a pipeline run into a **replay bundle**: each stage
writes its canonical artifact into the given directory exactly as it emits it
to stdout, byte-for-byte. Later, `--replay <dir>` mode (stages 2+) or a plain
re-run against the recorded inputs reproduces the same artifacts — the golden
dataset becomes *recorded evidence*, not hand-authored JSON.

Bundle layout (one directory per trade):

    tests/fixtures/RELIANCE_2026-10-09/
        manifest.json        ← stages that ran, timestamps, git-uncommitted-safe
        current_trade.json   ← stage 0 artifact
        out_1_math.json      ← stage 1 artifact
        out_2_market.json    ← stage 2 artifact (when implemented)
        ...

Rules the helper enforces for ALL stages, so the contract stays honest:

* The recorded artifact is the SAME bytes that go to stdout — one write, not
  two divergent serialisations (zero-hallucination guarantee).
* The manifest is append-only across a chained run; stage N appends its row,
  it never overwrites stage N−1's rows.
* The bundle directory name is derived from the symbol + recommendation date,
  so re-recording the same trade overwrites its own bundle, never a neighbour's.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


# Frozen artifact names, copied from the BRD §9.1 canonical contract table
# (the test contract-consistency suite fails if either drifts). These are NOT
# stem-derived: slugs like `out_1_math.json` (from `1_math_and_rr.py`) do not
# follow the script name, so a derivation here would silently fork the contract.
FROZEN_ARTIFACTS = {
    0: "current_trade.json",
    1: "out_1_math.json",
    2: "out_2_market.json",
}


def artifact_filename(stage: int) -> str:
    """Canonical artifact name for a stage, straight from BRD §9.1.

    Raises for any stage not frozen here — a stage must add its contract row
    to this map when it is implemented, not guess its own filename.
    """
    try:
        return FROZEN_ARTIFACTS[stage]
    except KeyError:
        raise AssertionError(
            f"stage {stage} has no frozen artifact name in _recorder.FROZEN_ARTIFACTS; "
            "the contract table (BRD §9.1) is the only source — add it there first, "
            "then here, keyed identically"
        )


def record_artifact(bundle_dir: Path, stage: int, exact_stdout: str) -> None:
    """Write the stage's artifact into the bundle with byte-exact stdout.

    `exact_stdout` MUST be the literal string the stage wrote to stdout (the
    caller passes it once, post-serialisation) — recording a re-serialised
    copy would violate the byte-exact guarantee this helper exists for.
    """
    bundle_dir.mkdir(parents=True, exist_ok=True)

    # The artifact goes in byte-identical to stdout.
    artifact_name = artifact_filename(stage)
    path = bundle_dir / artifact_name
    path.write_text(exact_stdout, encoding="utf-8")

    _append_manifest(bundle_dir, {
        "stage": stage,
        "artifact": artifact_name,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })


def _append_manifest(bundle_dir: Path, row: dict) -> None:
    """Append-only manifest. Chained runs accumulate rows; nothing is deleted."""
    manifest_path = bundle_dir / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest = {"schema": "uvest-replay-manifest/1", "stages": []}
    else:
        manifest = {"schema": "uvest-replay-manifest/1", "stages": []}
    manifest["stages"].append(row)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def bundle_name(symbol: str, recommended_date: str | None) -> str:
    """Deterministic per-trade bundle name: SYMBOL_DATE (date or `undated`)."""
    date_part = (recommended_date or "undated").replace("-", "")
    return f"{symbol}_{date_part}"


def add_record_arg(parser) -> None:
    """Attach the shared --record flag to any stage's argument parser."""
    parser.add_argument(
        "--record", metavar="DIR", default=None,
        help="write this stage's artifact into a per-trade replay bundle at DIR "
             "(optionally pass 'auto' to derive tests/fixtures/SYMBOL_DATE/)",
    )


def resolve_bundle_dir(record_arg: str | None, symbol: str,
                       recommended_date: str | None) -> Path | None:
    """Turn the --record value into a bundle directory, or None if recording off.

    `auto` derives `tests/fixtures/{SYMBOL}_{DATE}` from the repo root so a
    single flag can be reused across every chained stage without repeating paths.
    """
    if record_arg is None:
        return None
    if record_arg == "auto":
        root = Path(__file__).resolve().parent.parent
        date_part = (recommended_date or "undated").replace("-", "")
        return root / "tests" / "fixtures" / bundle_name(symbol, date_part)
    return Path(record_arg)
