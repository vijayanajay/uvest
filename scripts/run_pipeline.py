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
* The run leaves `run_record.json` in the workdir: one row per stage in ORDER —
  order, artifact path, skip reason, exit code, duration and the SHA-256 of the
  bytes the stage produced — so a run's provenance is auditable from the record
  alone, without parsing this log, and can be *checked* rather than believed: a
  `ran` row's hash is what a grader compares against the artifact on disk. Rows
  are written on the failure paths too (aborted stage, invalid artifact, missing
  report, interrupt), because a run under question is exactly where the console
  log has already scrolled away. Only a `ran` row claims bytes; every other row
  carries at most the artifact *name* its stage was about.

Usage:
    python scripts/run_pipeline.py --input input.txt [--workdir DIR]
        [--ohlcv-fixture PATH] [--history-fixture PATH]
        [--bhavcopy-fixture PATH] [--financials-fixture PATH]
        [--regime-fixture PATH | --regime-live] [--record DIR]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
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

sys.path.insert(0, str(SCRIPTS_DIR))

import _recorder  # noqa: E402  (scripts dir spliced above)

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
# Implemented stages take their name from the recorder's frozen table — one
# source, so a rename can never leave the runner and the recorder disagreeing.
# Stages that do not exist yet keep the BRD §9.1 name here as a skip-row audit
# label only, and must move into `_recorder.FROZEN_ARTIFACTS` when they land
# (`artifact_filename()` refuses to guess for them until then).
PENDING_ARTIFACTS: dict[int, str] = {
    8: "out_8_dhan.json",
}
ARTIFACTS: dict[int, str | None] = {
    **{stage: _recorder.FROZEN_ARTIFACTS[stage]
       for stage in sorted(_recorder.FROZEN_ARTIFACTS)},
    **PENDING_ARTIFACTS,
    12: None,
}

# Optional per-stage data fixtures (the tier-1 fetchers are unimplemented, so
# an omitted fixture is the tri-state waiver path, never an error). Each entry
# is the flag a stage takes paired with the slot the value is resolved into, so
# a stage may draw on more than one — and two stages may share one artifact.
# Stage 4 is the case ADR-010 decision 4 anticipates: it reads the stock OHLCV
# stage 2 reads *and* the single `uvest-market-context/1` bundle stage 7
# classifies, instead of growing a second fetcher and a second cache.
FIXTURE_FLAG: dict[int, tuple[tuple[str, int], ...]] = {
    2: (("--ohlcv-fixture", 2),),
    3: (("--history-fixture", 3),),
    4: (("--ohlcv-fixture", 2), ("--context-fixture", 4)),
    5: (("--bhavcopy-fixture", 5),),
    6: (("--financials-fixture", 6),),
    7: (("--regime-fixture", 7),),
}

# Stages whose CLI carries _recorder.add_record_arg (stage 12 has no recorder).
RECORD_STAGES = {0, 1, 2, 3, 4, 5, 6, 7, 9, 10}

# BRD §9.1 pipeline block: stage 10 logs the new trade before the report.
STAGE10_ARGS = ["--action", "track"]

# The run record is a workdir audit artifact of the RUNNER, not a BRD §9.1
# stage artifact: it never enters ARTIFACTS and is never fed to a stage.
RUN_RECORD_NAME = "run_record.json"
RUN_RECORD_SCHEMA = "uvest-run-record/1"
RUNNER_PATH = "scripts/run_pipeline.py"


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
    for flag, source_stage in FIXTURE_FLAG.get(stage, ()):
        fixture = fixtures.get(source_stage)
        if fixture:
            argv += [flag, fixture]
    if record is not None and stage in RECORD_STAGES:
        argv += ["--record", record]
    if stage in (9, 10, 12):
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


def _utc_now() -> str:
    """UTC ISO-8601 stamp at seconds precision (the `_recorder` row convention)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256_of(path: Path) -> str:
    """The hash a grader re-computes: does the artifact still hold these bytes?"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stage_row(order: int, stage: int, status: str, *, artifact: str | None = None,
              artifact_path: str | None = None, artifact_sha256: str | None = None,
              exit_code: int | None = None, skip_reason: str | None = None,
              seconds: float | None = None) -> dict:
    """One audit row of the run record.

    Every row carries the same keys whatever the status, so a reader can diff
    rows without guarding for absent fields: `order` is the row's position in
    the walk, `stage` the BRD §9.1 stage number, and `status` one of `ran`
    (executed and its artifact verified), `failed` (executed, did not land),
    `skipped` (disclosed, deliberately not executed) or `not_reached` (the chain
    stopped before it).

    Only `ran` sets `artifact_path` + `artifact_sha256`: those are a *claim about
    bytes*, and a claim is exactly what no un-executed stage may make. `artifact`
    is the artifact name the row is about, present wherever the contract names
    one — a fine-grained audit trail needs to know what `failed` was attempting.
    """
    return {
        "order": order,
        "stage": stage,
        "status": status,
        "artifact": artifact,
        "artifact_path": artifact_path,
        "artifact_sha256": artifact_sha256,
        "exit_code": exit_code,
        "skip_reason": skip_reason,
        "seconds": seconds,
    }


def close_run_record(record: dict, exit_code: int | None) -> None:
    """Finish the record: outcome, timestamp, and every stage the chain missed.

    Called from a `finally`, so an aborted or interrupted run still leaves a
    complete account — the stages that never ran are named as `not_reached`
    rather than silently absent from the file.
    """
    record["finished_at"] = _utc_now()
    record["exit_code"] = exit_code
    if exit_code is None:
        record["outcome"] = "incomplete"
    else:
        record["outcome"] = "complete" if exit_code == 0 else "aborted"

    reason = (f"chain aborted at stage {record['failed_stage']}"
              if record["failed_stage"] is not None
              else "chain did not reach this stage")
    walked = {row["stage"] for row in record["stages"]}
    for stage in ORDER:
        if stage not in walked:
            record["stages"].append(stage_row(
                len(record["stages"]), stage, "not_reached",
                artifact=ARTIFACTS.get(stage), skip_reason=reason))


def write_run_record(workdir: Path, record: dict) -> Path | None:
    """Persist the run record; a write failure warns but never masks the exit code."""
    path = workdir / RUN_RECORD_NAME
    try:
        path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"[WARN] run record not written ({exc})", file=sys.stderr)
        return None
    return path


def _fetch_live_context(workdir: Path) -> str | None:
    """Fetch a live market-context bundle into the workdir for stages 4 and 7.

    Runs in front of the first bundle consumer (stage 4), because the
    canonical trade (symbol, recommended_date) only exists once the chain has
    ingested the input. Any failure — unmapped symbol, network down, thin
    sources — returns None so the consuming stages take their existing waiver
    paths and say why; a partial bundle is still worth passing, because both
    name exactly which series are missing (`missing_inputs` on stage 7, the
    feature window on stage 4) instead of guessing them.
    """
    try:
        import fetch_market_context as fmc
        trade = json.loads((workdir / "current_trade.json").read_text(
            encoding="utf-8"))
        symbol = str(trade.get("symbol") or "").strip().upper()
        as_of = str(trade.get("recommended_date")
                    or datetime.now().strftime("%Y-%m-%d"))
        output = workdir / "market_context.json"
        print(f"[FETCH] market context: {symbol} as of {as_of} "
              "(tier-1: Yahoo + NSE)")
        code = fmc.main(["--symbol", symbol, "--as-of", as_of,
                         "--output", str(output)])
        if code == 0 and output.exists():
            return str(output)
    except (Exception, SystemExit) as exc:
        # SystemExit: the fetcher's usage gates (unmapped symbol, bad --as-of)
        # exit rather than raise — a usage problem is still just a fetch
        # failure down here, never a chain abort.
        print(f"[WARN] market-context fetch failed: {type(exc).__name__}: "
              f"{exc}", file=sys.stderr)
    print("[WARN] no live bundle — stage 7 takes the waiver path",
          file=sys.stderr)
    return None


def run_chain(run_record: dict, *, input_path: str, workdir: Path,
              fixtures: dict[int, str | None], record_dir: str | None,
              regime_live: bool = False) -> int:
    """Walk ORDER, forwarding each stage's stdout into its artifact.

    Returns the chain's exit code (0 on success, the first failing stage's own
    code otherwise) and appends one audit row per stage visited.
    """
    def note(stage: int, status: str, **fields) -> None:
        """Append a row, numbering it in walk order."""
        run_record["stages"].append(
            stage_row(len(run_record["stages"]), stage, status, **fields))

    ran: list[int] = []
    context_fetched = False
    for stage in ORDER:
        if stage in EXCLUDED:
            reason = EXCLUDED[stage]
            print(f"[SKIP] stage {stage}: {reason}")
            note(stage, "skipped", artifact=ARTIFACTS.get(stage), skip_reason=reason)
            continue
        script = script_for(stage)
        if script is None:
            reason = (f"scripts/{stage}_*.py not implemented — its evidence stays "
                      "waived downstream (BRD §10.4)")
            print(f"[SKIP] stage {stage}: {reason}")
            note(stage, "skipped", artifact=ARTIFACTS.get(stage), skip_reason=reason)
            continue

        # The live bundle is fetched once, in front of the FIRST stage that
        # reads it (stage 4 in the frozen order), and handed to both consumers:
        # one fetch, one cache, one no-lookahead truncation — never two
        # fetchers racing the same market (ADR-010 decision 4).
        if regime_live and not context_fetched and stage in (4, 7):
            context_fetched = True
            fixtures[7] = _fetch_live_context(workdir)
            fixtures[4] = fixtures[7]

        artifact = ARTIFACTS[stage]
        target = f" → {artifact}" if artifact else " → reports/report_{SYMBOL}_{DATE}.html"
        print(f"[RUN] stage {stage}: {script.name}{target}")

        invoke = stage_argv(stage, script, input_path=input_path,
                            fixtures=fixtures, record=record_dir,
                            payloads=_payloads_for(ran, workdir),
                            workdir=workdir)
        started = time.monotonic()
        # A stage's stdout streams into a *partial* file that is published
        # only on success. The runner used to open the artifact itself, which
        # truncated it before the stage ran, so a refusal left a 0-byte
        # `current_trade.json` (or `out_*.json`) behind — a file that looks
        # like a payload and is not. The run record already refuses to claim
        # bytes a failed stage did not produce; the filesystem now agrees.
        partial = (workdir / f".{artifact}.partial") if artifact else None
        try:
            # Always go through the interpreter: `.py` files are not directly
            # executable on Windows, and sys.executable pins the runtime.
            command = [sys.executable, *invoke]
            if artifact:
                with open(partial, "wb") as handle:
                    proc = subprocess.run(command, cwd=str(workdir), stdout=handle)
            else:
                proc = subprocess.run(command, cwd=str(workdir))
        except OSError as exc:
            if partial is not None:
                partial.unlink(missing_ok=True)
            print(f"[FAIL] stage {stage}: {exc}", file=sys.stderr)
            run_record["failed_stage"] = stage
            note(stage, "failed", artifact=artifact, exit_code=None,
                 seconds=round(time.monotonic() - started, 3),
                 skip_reason=f"stage did not run: {type(exc).__name__}: {exc}")
            return 1
        elapsed = round(time.monotonic() - started, 3)
        if partial is not None:
            if proc.returncode == 0:
                os.replace(partial, workdir / artifact)
            else:
                partial.unlink(missing_ok=True)
        if proc.returncode != 0:
            # The stage already wrote its diagnosis to inherited stderr; the
            # runner only names the abort and hands back the stage's own code
            # (stage 0's options gate exits 2 — that must survive the chain).
            print(f"[FAIL] stage {stage} exited {proc.returncode} — "
                  "pipeline aborted, later stages did not run", file=sys.stderr)
            run_record["failed_stage"] = stage
            note(stage, "failed", artifact=artifact, exit_code=proc.returncode,
                 seconds=elapsed, skip_reason=f"stage exited {proc.returncode}")
            return proc.returncode or 1

        if artifact:
            try:
                json.loads((workdir / artifact).read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                print(f"[FAIL] stage {stage}: {artifact} is not valid JSON "
                      f"({exc}) — stdout is the stage contract", file=sys.stderr)
                run_record["failed_stage"] = stage
                note(stage, "failed", exit_code=proc.returncode, seconds=elapsed,
                     skip_reason=f"{artifact} is not valid JSON ({exc})")
                return 1
        ran.append(stage)

        produced = artifact
        produced_path = (workdir / artifact) if artifact else None
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
                run_record["failed_stage"] = stage
                note(stage, "failed", artifact=expected.name, exit_code=proc.returncode,
                     seconds=elapsed, skip_reason=f"report not written: {expected.name}")
                return 1
            produced = f"reports/{expected.name}"
            produced_path = expected
            run_record["report"] = produced

        note(stage, "ran", artifact=produced, exit_code=proc.returncode,
             seconds=elapsed, artifact_path=str(produced_path) if produced_path else None,
             artifact_sha256=_sha256_of(produced_path) if produced_path else None)

    # Summary: transcribed from the artifacts, never invented (evals.md §3.1).
    verdict_path = workdir / "out_9_decision.json"
    if verdict_path.exists():
        verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
        print(f"✅ VERDICT: {verdict['decision']} · "
              f"score {verdict['final_score']}/{verdict['max_possible_score']} · "
              f"P(win) {verdict['p_win']} · "
              f"expectancy {float(verdict['expectancy']):+.2f}R")
    if run_record["report"] is not None:
        print(f"✅ report: {run_record['report']}")
    print(f"✅ pipeline complete — {len(ran)} stage(s) ran in {workdir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="UVEST end-to-end pipeline runner (BRD §9.1 chain 0 → 12).")
    parser.add_argument("--input", required=True,
                        help="raw recommendation text file for stage 0.")
    parser.add_argument("--workdir", default=".",
                        help="directory receiving current_trade.json, out_*.json, "
                             "run_record.json and reports/ (default: current "
                             "directory).")
    parser.add_argument("--ohlcv-fixture", default=None,
                        help="recorded daily-OHLCV file for stage 2 (and the "
                             "stock series stage 4's features need).")
    parser.add_argument("--history-fixture", default=None,
                        help="recorded 10-year history file for stage 3.")
    parser.add_argument("--bhavcopy-fixture", default=None,
                        help="recorded bhavcopy file for stage 5.")
    parser.add_argument("--financials-fixture", default=None,
                        help="recorded financial-statement bundle for stage 6 "
                             "(omit for the waiver path).")
    parser.add_argument("--regime-fixture", default=None,
                        help="recorded uvest-market-context/1 bundle, handed to "
                             "BOTH of its consumers — stage 7 as "
                             "--regime-fixture and stage 4 as --context-fixture "
                             "(omit for the waiver path).")
    parser.add_argument("--regime-live", action="store_true",
                        help="fetch a live uvest-market-context/1 bundle for "
                             "stage 7 from tier-1 sources (Yahoo + NSE; "
                             "fetch_market_context.py) instead of passing a "
                             "fixture — a fetch that fails leaves stage 7's "
                             "waiver path, never an invented value. Conflicts "
                             "with --regime-fixture.")
    parser.add_argument("--record", default=None,
                        help="replay-bundle directory passed to every "
                             "recorder-capable stage.")
    args = parser.parse_args(argv)
    if args.regime_live and args.regime_fixture:
        parser.error("--regime-live and --regime-fixture are mutually "
                     "exclusive — pass the bundle or ask the runner to fetch "
                     "one, not both")

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
        # One bundle, two consumers (`--regime-fixture` and stage 4's
        # `--context-fixture` are the same artifact by construction).
        4: resolve(args.regime_fixture),
        5: resolve(args.bhavcopy_fixture),
        6: resolve(args.financials_fixture),
        7: resolve(args.regime_fixture),
    }
    record_dir = resolve(args.record)

    # The run record is seeded before the first stage so an interrupted run
    # still writes it: `close_run_record` fills the outcome, and stages append
    # their own rows as the walk proceeds.
    run_record: dict = {
        "schema": RUN_RECORD_SCHEMA,
        "runner": RUNNER_PATH,
        "input": input_path,
        "workdir": str(workdir),
        "started_at": _utc_now(),
        "finished_at": None,
        "outcome": "incomplete",
        "exit_code": None,
        "failed_stage": None,
        "report": None,
        "stages": [],
    }

    exit_code: int | None = None
    try:
        exit_code = run_chain(run_record, input_path=input_path, workdir=workdir,
                              fixtures=fixtures, record_dir=record_dir,
                              regime_live=args.regime_live)
    finally:
        # Every exit path leaves the record behind — including a Ctrl-C, which is
        # exactly when the console log is least likely to have been kept.
        close_run_record(run_record, exit_code)
        write_run_record(workdir, run_record)
    return exit_code or 0


if __name__ == "__main__":
    sys.exit(main())
