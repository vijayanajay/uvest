"""Regenerate every golden record's stage-12 report (TICK-010 workflow).

    python tests/evals/report_regen.py [--out-dir reports] [--benchmark <dataset>]

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
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent.parent
sys.path.insert(0, str(TESTS_DIR))  # sibling dataset_refs / report_diff
sys.path.insert(0, str(TESTS_DIR.parent))  # run_evals lives one level up

import dataset_refs  # noqa: E402
import report_diff  # noqa: E402
import run_evals  # noqa: E402

STAGE12 = REPO_ROOT / "scripts" / "12_generate_html_report.py"
MANIFEST_FILE = REPO_ROOT / "tests" / "proof" / "scenario_manifest.json"
DEFAULT_BENCHMARK = TESTS_DIR / "golden_dataset.json"
PROOF_ROOT = REPO_ROOT / "tests" / "proof"


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
    """Run the stage CLI per job in parallel; return `(artifacts, failures)`.

    Identity is checked before the run: the canonical trade's symbol/date must
    equal the record's, or the report would be written under the wrong name.
    """
    artifacts: list[Path] = []
    failures: list[str] = []
    out_dir.mkdir(parents=True, exist_ok=True)

    def _run_job(job: dict) -> tuple[Path | None, str | None]:
        trade = job["trade"]
        tid = str(trade.get("trade_id"))
        try:
            canonical = json.loads(job["canonical"].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return None, f"{tid}: canonical trade unreadable: {exc}"
        expected = run_evals.report_path_for(trade).name
        day = str(trade.get("recommendation_date") or "").replace("-", "")
        if (str(canonical.get("symbol", "")).upper() != str(trade.get("symbol", "")).upper()
                or str(canonical.get("recommended_date", "")).replace("-", "") != day):
            return None, (f"{tid}: canonical trade states a different symbol/date "
                          f"than the record ({canonical.get('symbol')} / "
                          f"{canonical.get('recommended_date')})")
        cmd = [python, str(STAGE12), "--input", str(job["canonical"]),
               "--out-dir", str(out_dir)]
        for payload in job["payloads"]:
            cmd += ["--payload", str(payload)]
        proc = subprocess.run(cmd, capture_output=True, check=False, cwd=REPO_ROOT)
        if proc.returncode != 0:
            return None, (f"{tid}: stage 12 exited {proc.returncode}: "
                          f"{proc.stderr.decode('utf-8', 'replace').strip()}")
        produced = out_dir / expected
        if not produced.exists():
            return None, f"{tid}: stage 12 wrote no {expected}"
        return produced, None

    workers = min(8, max(2, (os.cpu_count() or 4)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        for produced, failure in executor.map(_run_job, jobs):
            if failure:
                failures.append(failure)
            if produced:
                artifacts.append(produced)
    return artifacts, failures


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
    args = parser.parse_args(argv)

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
