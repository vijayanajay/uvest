#!/usr/bin/env python3
"""UVEST end-to-end pipeline runner — the canonical 0 → 12 chain in ONE invocation.

BRD §9.1 and prompt.md §2 freeze the stage scripts, their output artifacts and
the ascending execution order; this orchestrator executes exactly that chain:

    python scripts/run_pipeline.py --input input.txt

Each stage runs with the workdir as its cwd, so the frozen default
`--input current_trade.json` resolves exactly as the canonical block spells it,
and every stage's stdout is forwarded byte-for-byte into its BRD §9.1 artifact
(the same shape as the documented `> out_N_*.json` redirects).

Discipline (BRD §10.4, §9.1):

* Stages whose script is not implemented yet are disclosed `[SKIP]`, never
  fatal — their evidence stays waived downstream, because absence never
  fabricates a rejection (the waiver rule), and silence would be a hidden gap.
* Stage 11 is the conditional stage: it runs only when a tracked trade
  resolves and therefore never executes in the validation chain; its absence
  is disclosed, not silent.
* The first failing stage aborts the run and its exit code is propagated, so
  stage 0's hard gates (`ERR_OPTIONS_PROHIBITED`, exit 2) survive the chain:
  an options recommendation can never reach the decision engine or the report.
* Every artifact must parse as JSON (stdout-JSON is the stage contract) and
  stage 12 must have written `reports/report_{SYMBOL}_{DATE}.html`, or the run
  fails — a green summary never claims artifacts that are not on disk.
* The terminal summary reads the artifacts it just produced (Zero Hallucination
  discipline): the verdict line is a transcription of `out_9_decision.json`.

Usage:
    python scripts/run_pipeline.py --input input.txt [--workdir DIR]
        [--ohlcv-fixture PATH] [--history-fixture PATH]
        [--bhavcopy-fixture PATH] [--record DIR]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# Windows consoles default to a legacy codepage; the summary carries Unicode
# glyphs (→, ✅), so both streams must be UTF-8 (repo convention — the same
# reconfigure every stage script performs).
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        # line-buffered so the chain log stays chronological around the
        # subprocesses' own stdout (stage 12 prints a status line).
        _stream.reconfigure(encoding="utf-8", line_buffering=True)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"

# Frozen ascending execution order (BRD §9.1). Stage 11 is conditional and
# therefore excluded from CHAIN; it is still walked in ORDER so the gap is
# disclosed in sequence rather than silently missing.
CHAIN: list[int] = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12]
EXCLUDED: dict[int, str] = {
    11: "conditional stage — runs only when a tracked trade resolves "
        "(BRD §9.1), never in the validation chain",
}
ORDER: list[int] = sorted([*CHAIN, *EXCLUDED])

# BRD §9.1 output artifacts; stage 12 writes its HTML itself (None here).
ARTIFACTS: dict[int, str | None] = {
    0: "current_trade.json",
    1: "out_1_math.json",
    2: "out_2_market.json",
    3: "out_3_dtw.json",
    4: "out_4_ml.json",
    5: "out_5_flow.json",
    6: "out_6_fundamental.json",
    7: "out_7_regime.json",
    8: "out_8_dhan.json",
    9: "out_9_decision.json",
    10: "out_10_triage.json",
    12: None,
}

# Optional per-stage data fixtures (the tier-1 fetchers are unimplemented, so
# an omitted fixture is the tri-state waiver path, never an error).
FIXTURE_FLAG: dict[int, str] = {
    2: "--ohlcv-fixture",
    3: "--history-fixture",
    5: "--bhavcopy-fixture",
}

# Stages whose CLI carries _recorder.add_record_arg (stage 12 has no recorder).
RECORD_STAGES = {0, 1, 2, 3, 5, 9}

# BRD §9.1 pipeline block: stage 10 logs the new trade before the report.
STAGE10_ARGS = ["--action", "track"]


def script_for(stage: int) -> Path | None:
    """The stage script per the frozen naming rule `scripts/{stage}_*.py`."""
    candidates = sorted(SCRIPTS_DIR.glob(f"{stage}_*.py"))
    return candidates[0] if candidates else None


def stage_argv(stage: int, script: Path, *, input_path: str,
               fixtures: dict[int, str | None], record: str | None,
               payloads: list[str], workdir: Path) -> list[str]:
    """The invocation for one stage — canonical shape, nothing invented.

    Paths handed to a stage are relative to `workdir` (its cwd), matching the
    frozen BRD §9.1 block; user-supplied paths arrive pre-resolved absolute.
    """
    argv = [str(script)]
    if stage == 0:
        argv += ["--input", input_path]
    if stage == 10:
        argv += list(STAGE10_ARGS)
    if stage == 12:
        argv += ["--input", "current_trade.json", "--out-dir", "reports"]
    fixture = fixtures.get(stage)
    if fixture:
        argv += [FIXTURE_FLAG[stage], fixture]
    if record is not None and stage in RECORD_STAGES:
        argv += ["--record", record]
    if stage in (9, 12):
        for payload in payloads:
            argv += ["--payload", payload]
    if stage == 9 and (workdir / "thresholds.json").exists():
        argv += ["--thresholds", "thresholds.json"]
    return argv


def _payloads_for(ran: list[int], workdir: Path) -> list[str]:
    """Every `out_*.json` produced by an earlier stage of THIS run.

    Only stages actually executed count: a stale artifact from a previous run
    must never be fed forward as evidence (recorded bytes are the only bytes
    graded). `current_trade.json` is the canonical input, not a payload.
    """
    names = []
    for stage in ran:
        artifact = ARTIFACTS.get(stage)
        if artifact and artifact.startswith("out_") and (workdir / artifact).exists():
            names.append(artifact)
    return names


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="UVEST end-to-end pipeline runner (BRD §9.1 chain 0 → 12).")
    parser.add_argument("--input", required=True,
                        help="raw recommendation text file for stage 0.")
    parser.add_argument("--workdir", default=".",
                        help="directory receiving current_trade.json, out_*.json "
                             "and reports/ (default: current directory).")
    parser.add_argument("--ohlcv-fixture", default=None,
                        help="recorded daily-OHLCV file for stage 2.")
    parser.add_argument("--history-fixture", default=None,
                        help="recorded 10-year history file for stage 3.")
    parser.add_argument("--bhavcopy-fixture", default=None,
                        help="recorded bhavcopy file for stage 5.")
    parser.add_argument("--record", default=None,
                        help="replay-bundle directory passed to every "
                             "recorder-capable stage.")
    args = parser.parse_args(argv)

    # Resolve every path against the invocation cwd BEFORE the chain starts
    # running inside the workdir.
    def resolve(path: str | None) -> str | None:
        return str(Path(path).resolve()) if path else None

    input_path = resolve(args.input)
    workdir = Path(args.workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    fixtures: dict[int, str | None] = {
        2: resolve(args.ohlcv_fixture),
        3: resolve(args.history_fixture),
        5: resolve(args.bhavcopy_fixture),
    }
    record = resolve(args.record)

    ran: list[int] = []
    report: Path | None = None

    for stage in ORDER:
        if stage in EXCLUDED:
            print(f"[SKIP] stage {stage}: {EXCLUDED[stage]}")
            continue
        script = script_for(stage)
        if script is None:
            print(f"[SKIP] stage {stage}: scripts/{stage}_*.py not implemented — "
                  "its evidence stays waived downstream (BRD §10.4)")
            continue

        artifact = ARTIFACTS[stage]
        target = f" → {artifact}" if artifact else " → reports/report_{SYMBOL}_{DATE}.html"
        print(f"[RUN] stage {stage}: {script.name}{target}")

        invoke = stage_argv(stage, script, input_path=input_path,
                            fixtures=fixtures, record=record,
                            payloads=_payloads_for(ran, workdir),
                            workdir=workdir)
        try:
            # Always go through the interpreter: `.py` files are not directly
            # executable on Windows, and sys.executable pins the runtime.
            command = [sys.executable, *invoke]
            if artifact:
                with open(workdir / artifact, "wb") as handle:
                    proc = subprocess.run(command, cwd=str(workdir), stdout=handle)
            else:
                proc = subprocess.run(command, cwd=str(workdir))
        except OSError as exc:
            print(f"[FAIL] stage {stage}: {exc}", file=sys.stderr)
            return 1
        if proc.returncode != 0:
            # The stage already wrote its diagnosis to inherited stderr; the
            # runner only names the abort and hands back the stage's own code
            # (stage 0's options gate exits 2 — that must survive the chain).
            print(f"[FAIL] stage {stage} exited {proc.returncode} — "
                  "pipeline aborted, later stages did not run", file=sys.stderr)
            return proc.returncode or 1

        if artifact:
            try:
                json.loads((workdir / artifact).read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                print(f"[FAIL] stage {stage}: {artifact} is not valid JSON "
                      f"({exc}) — stdout is the stage contract", file=sys.stderr)
                return 1
        ran.append(stage)

        if stage == 12:
            trade = json.loads(
                (workdir / "current_trade.json").read_text(encoding="utf-8"))
            day = str(trade.get("recommended_date") or "")
            expected = workdir / "reports" / (
                f"report_{str(trade.get('symbol') or '').upper()}"
                f"_{day.replace('-', '')}.html")
            if not expected.exists():
                print(f"[FAIL] stage 12: expected report missing: "
                      f"{expected.name}", file=sys.stderr)
                return 1
            report = expected

    # Summary: transcribed from the artifacts, never invented (evals.md §3.1).
    verdict_path = workdir / "out_9_decision.json"
    if verdict_path.exists():
        verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
        print(f"✅ VERDICT: {verdict['decision']} · "
              f"score {verdict['final_score']}/{verdict['max_possible_score']} · "
              f"P(win) {verdict['p_win']} · "
              f"expectancy {float(verdict['expectancy']):+.2f}R")
    if report is not None:
        print(f"✅ report: reports/{report.name}")
    print(f"✅ pipeline complete — {len(ran)} stage(s) ran in {workdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
