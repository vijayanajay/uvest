"""Regenerate every golden record's stage-12 report (TICK-010 workflow).

    python tests/evals/report_regen.py [--out-dir reports] [--benchmark <dataset>]
    python tests/evals/report_regen.py --prototype   # the tracked sample_report.html

`--prototype` writes only `reports/sample_report.html`, from a single named
record (`PROTOTYPE_TRADE_ID`): the one report that is committed has to be a
*produced* artifact with a source it can be re-derived from, not a mock, and
`tests/test_12_generate_html_report.py` fails if the committed bytes stop being
a byte-exact fresh render of that record.

A report is a *generated* artifact (`reports/report_*.html` is gitignored, so
no stale local bytes can ever be graded as evidence): this driver is how the
canonical path gets (re)populated. Each job pairs a proof row's canonical
trade and recorded payloads with the runner's own report naming
(`run_evals.report_path_for`), invokes the real stage CLI as a subprocess —
exactly the way the pipeline does — and then verifies the produced bytes with
the same differ the Zero-Hallucination gate grades with. A report that would
fail the gate is a *failed regeneration*, not a warning: the file is reported,
the exit code is non-zero, and nothing is left to be silently graded later.

The row's canonical trade must state the same symbol and recommendation date
as the record it belongs to (they are one identity); a mismatch fails the job
rather than writing a report under the wrong name.

Every replay leaves a run record beside the artifacts (`run_record.json`,
`uvest-run-record/1`, see `tests/evals/provenance.py`): one row per report,
naming the exact bytes that run produced (`artifact_sha256`). That is what lets
the eval harness *prove* a report it grades came out of a real stage-12
execution, and what makes a hand-edited or stale report ungradeable instead of
indistinguishable from a produced one. An artifact already on disk is replayed
into a sidecar and byte-compared rather than overwritten — identical bytes are
recorded as this run's output, different bytes are recorded as a `failed` row.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent.parent
sys.path.insert(0, str(TESTS_DIR))  # sibling dataset_refs / report_diff
sys.path.insert(0, str(TESTS_DIR.parent))  # run_evals lives one level up

import dataset_refs  # noqa: E402
import provenance  # noqa: E402
import report_diff  # noqa: E402
import run_evals  # noqa: E402

STAGE12 = REPO_ROOT / "scripts" / "12_generate_html_report.py"
MANIFEST_FILE = REPO_ROOT / "tests" / "proof" / "scenario_manifest.json"
DEFAULT_BENCHMARK = TESTS_DIR / "golden_dataset.json"
PROOF_ROOT = REPO_ROOT / "tests" / "proof"

# The tracked prototype (`reports/sample_report.html`) — the one report that is
# committed (`reports/report_*.html` is gitignored), so it is what a reviewer
# opens and what a fresh clone has as a real-HTML example. It is *produced*, not
# drawn: it is this record's own stage-12 report, and
# `tests/test_12_generate_html_report.py` fails if the tracked bytes stop being
# what stage 12 renders for it. The record is the benchmark's first — a FUTURES
# trade that carries stage-4 evidence, so the prototype shows §13.2's race gauge
# rather than a placeholder.
PROTOTYPE_TRADE_ID = "GB-2022-0001"
PROTOTYPE_NAME = "sample_report.html"


def report_jobs(data: dict, manifest: dict) -> list[dict]:
    """One regeneration job per record whose proof row pins the evidence."""
    rows = {row.get("scenario_id"): row for row in manifest.get("scenarios", [])}
    jobs: list[dict] = []
    for trade in data.get("trades", []):
        row = rows.get(str(trade.get("trade_id")))
        if not row:
            continue
        inputs = row.get("replay_inputs") or {}
        canonical = inputs.get("canonical_trade")
        if not canonical:
            continue
        payloads = [a["path"] for a in row.get("recorded_artifacts", [])
                    if a.get("evidence") == "recorded" and a.get("path")]
        jobs.append({"trade": trade, "canonical": PROOF_ROOT / canonical,
                     "payloads": [PROOF_ROOT / p for p in payloads]})
    return jobs


def regenerate(jobs: list[dict], out_dir: Path,
               python: str = sys.executable) -> tuple[list[Path], list[str]]:
    """Reproduce stage 12 per job; return `(artifacts written, failures)`.

    Identity is checked before the run: the canonical trade's symbol/date must
    equal the record's, or the report would be written under the wrong name.

    A job whose artifact is missing is replayed *into* `out_dir`. A job whose
    artifact is already there is replayed into a sidecar directory and the bytes
    compared: the file on disk is never overwritten, and an identical
    reproduction is what lets this run's record claim those exact bytes as its
    own output (so a gate can prove what it grades). A reproduction that differs
    records a `failed` row — the file on disk is not this run's output — and is
    left exactly as it is.

    The record (`out_dir/run_record.json`, `uvest-run-record/1`) is written as a
    side effect: this run's rows first, then the rows an earlier record still
    stands behind (`provenance.verified_rows`), so a warm artifacts directory
    keeps its proof without replaying anything. `outcome` describes whether the
    *replay* ran to completion; a `failed` row means that artifact's provenance
    could not be established.

    A job that never reaches stage 12 — its canonical trade unreadable, or
    stating a different identity than the record — contributes no row at all:
    the record is a ledger of *executions*, and that failure belongs to the
    caller, which reports it (in `failures`) and fails the run on it.
    """
    artifacts: list[Path] = []
    failures: list[str] = []
    rows: list[dict] = []
    stage_failures = 0
    out_dir.mkdir(parents=True, exist_ok=True)
    record_path = out_dir / provenance.RECORD_NAME
    started_at = provenance.utc_now()

    def _run_job(job: dict, target: Path) -> tuple[Path | None, str | None, float | None, bool]:
        """(artifact, failure, seconds, attempted) — `attempted` gates the ledger row."""
        trade = job["trade"]
        tid = str(trade.get("trade_id"))
        try:
            canonical = json.loads(job["canonical"].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return None, f"{tid}: canonical trade unreadable: {exc}", None, False
        expected = run_evals.report_path_for(trade).name
        day = str(trade.get("recommendation_date") or "").replace("-", "")
        if (str(canonical.get("symbol", "")).upper() != str(trade.get("symbol", "")).upper()
                or str(canonical.get("recommended_date", "")).replace("-", "") != day):
            return None, (f"{tid}: canonical trade states a different symbol/date "
                          f"than the record ({canonical.get('symbol')} / "
                          f"{canonical.get('recommended_date')})"), None, False
        cmd = [python, str(STAGE12), "--input", str(job["canonical"]),
               "--out-dir", str(target)]
        for payload in job["payloads"]:
            cmd += ["--payload", str(payload)]
        started = time.monotonic()
        proc = subprocess.run(cmd, capture_output=True, check=False, cwd=REPO_ROOT)
        seconds = round(time.monotonic() - started, 3)
        if proc.returncode != 0:
            return None, (f"{tid}: stage 12 exited {proc.returncode}: "
                          f"{proc.stderr.decode('utf-8', 'replace').strip()}"), seconds, True
        produced = target / expected
        if not produced.exists():
            return None, f"{tid}: stage 12 wrote no {expected}", seconds, True
        return produced, None, seconds, True

    # An earlier record may outlive this replay: its rows are carried forward
    # only where the bytes they claim are still on disk (`verified_rows`).
    carried_record: dict | None = None
    if record_path.exists():
        try:
            carried_record = provenance.load(record_path)
        except provenance.ProvenanceError as exc:
            print(f"  ! {exc} — rebuilding the run record from this replay",
                  file=sys.stderr)

    # A job whose artifact is present is verified in a sidecar, never re-written.
    with tempfile.TemporaryDirectory() as sidecar_dir:
        sidecar = Path(sidecar_dir)
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(8, max(2, (os.cpu_count() or 4)))) as executor:
            futures = {
                executor.submit(
                    _run_job, job,
                    sidecar if (out_dir / run_evals.report_path_for(job["trade"]).name).exists()
                    else out_dir): job
                for job in jobs
            }
            for future, job in futures.items():
                trade = job["trade"]
                name = run_evals.report_path_for(trade).name
                tid = str(trade.get("trade_id"))
                produced, failure, seconds, attempted = future.result()
                on_disk = out_dir / name
                if failure:
                    failures.append(failure)
                    if attempted:  # a stage that ran and failed is ledger row material
                        stage_failures += 1
                        rows.append(provenance.row(12, "failed", artifact=name,
                                                   seconds=seconds, skip_reason=failure))
                    continue
                if produced.parent == out_dir:
                    artifacts.append(produced)
                    rows.append(provenance.row(
                        12, "ran", artifact=name, artifact_path=on_disk,
                        artifact_sha256=provenance.sha256_of(on_disk), exit_code=0,
                        seconds=seconds))
                    continue
                reproduced = provenance.sha256_of(produced)
                found_on_disk = provenance.sha256_of(on_disk)
                if reproduced == found_on_disk:
                    rows.append(provenance.row(
                        12, "ran", artifact=name, artifact_path=on_disk,
                        artifact_sha256=reproduced, exit_code=0, seconds=seconds))
                else:
                    rows.append(provenance.row(
                        12, "failed", artifact=name, exit_code=0, seconds=seconds,
                        skip_reason=(f"{tid}: reproduced bytes differ from the artifact "
                                     f"on disk ({reproduced[:12]}… != "
                                     f"{found_on_disk[:12]}…) — "
                                     "that file is not this run's output")))

    # This run's rows first, then the rows the earlier record still stands behind.
    # A carried row never outranks this run's finding about the same artifact.
    batch = {row_["artifact"] for row_ in rows if row_.get("artifact")}
    carried = [r for r in provenance.verified_rows(carried_record)
               if Path(r["artifact_path"]).name not in batch]

    record = provenance.build_record(
        rows + carried, runner="tests/evals/report_regen.py", workdir=out_dir,
        outcome="aborted" if stage_failures else "complete",
        exit_code=1 if stage_failures else 0,
        failed_stage=12 if stage_failures else None,
        started_at=started_at, finished_at=provenance.utc_now())
    record_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return artifacts, failures


def prototype_job(data: dict | None = None, manifest: dict | None = None) -> dict:
    """The regeneration job behind the tracked prototype.

    Fails loudly rather than writing something else: a prototype that silently
    became a different record's report would be a hand-drawn artifact again.
    """
    if data is None:
        data = json.loads(DEFAULT_BENCHMARK.read_text(encoding="utf-8"))
    if manifest is None:
        manifest = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    for job in report_jobs(data, manifest):
        if str(job["trade"].get("trade_id")) == PROTOTYPE_TRADE_ID:
            return job
    raise SystemExit(
        f"ERR_INPUT: no record {PROTOTYPE_TRADE_ID} with a proof row — the tracked "
        f"prototype ({PROTOTYPE_NAME}) has no source to be rendered from")


def write_prototype(out_path: Path | None = None, *,
                    python: str = sys.executable) -> Path:
    """Render the prototype from its record; return the path written.

    The bytes are stage 12's own output for one golden record, so the committed
    artifact is regenerable by anyone with the repo (`report_regen.py
    --prototype`) and gradeable by the same gates as any other report.
    """
    job = prototype_job()
    target = Path(out_path) if out_path else REPO_ROOT / "reports" / PROTOTYPE_NAME
    with tempfile.TemporaryDirectory() as tmp:
        artifacts, failures = regenerate([job], Path(tmp), python=python)
        if failures or len(artifacts) != 1:
            raise SystemExit("ERR_STAGE12: "
                             + "; ".join(failures or ["no artifact produced"]))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(artifacts[0].read_bytes())
    return target


def diff_reports(data: dict, out_dir: Path) -> list[str]:
    """Every produced report must be differ-clean against its own record."""
    materialized, _refs, _derives = dataset_refs.materialize(data)
    problems: list[str] = []
    for trade in materialized.get("trades", []):
        path = out_dir / run_evals.report_path_for(trade).name
        if not path.exists():
            continue
        found = report_diff.diff(
            path.read_text(encoding="utf-8", errors="replace"), trade)
        if found:
            problems.append(f"{trade.get('trade_id')}: "
                            + "; ".join(found[:5])
                            + (f" (+{len(found) - 5} more)" if len(found) > 5 else ""))
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate every golden record's stage-12 HTML report.")
    parser.add_argument("--out-dir", default=str(REPO_ROOT / "reports"))
    parser.add_argument("--benchmark", default=str(DEFAULT_BENCHMARK))
    parser.add_argument("--prototype", action="store_true",
                        help=f"write only the tracked prototype ({PROTOTYPE_NAME}) "
                             f"from its record ({PROTOTYPE_TRADE_ID})")
    args = parser.parse_args(argv)

    if args.prototype:
        written = write_prototype(Path(args.out_dir) / PROTOTYPE_NAME)
        print(f"  ↳ {written.relative_to(REPO_ROOT).as_posix()}"
              if written.is_relative_to(REPO_ROOT) else f"  ↳ {written}")
        print(f"\n{PROTOTYPE_NAME} regenerated from {PROTOTYPE_TRADE_ID}")
        return 0

    data = json.loads(Path(args.benchmark).read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    jobs = report_jobs(data, manifest)
    if not jobs:
        print("no records with proof rows — nothing to regenerate", file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir)
    artifacts, failures = regenerate(jobs, out_dir)
    failures += diff_reports(data, out_dir)

    for artifact in artifacts:
        print(f"  ↳ {artifact.relative_to(REPO_ROOT).as_posix()}"
              if artifact.is_relative_to(REPO_ROOT) else f"  ↳ {artifact}")
    record_path = out_dir / provenance.RECORD_NAME
    if record_path.exists():
        record = provenance.load(record_path)
        proven = sum(1 for r in record["stages"] if r["status"] == "ran")
        print(f"  ↳ {record_path.name}: {proven} artifact(s) proven by this run")
    if failures:
        print(f"\nREGENERATION FAILED: {len(failures)} problem(s)", file=sys.stderr)
        for failure in failures:
            print(f"  ✗ {failure}", file=sys.stderr)
        return 1
    print(f"\n{len(artifacts)} report(s) regenerated under {out_dir}, "
          "all differ-clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
