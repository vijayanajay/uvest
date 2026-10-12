"""Pristine-checkout proof: the invariant suite needs no pre-generated evidence.

    python tests/evals/pristine_suite.py

Every developer machine runs `tests/run_all_tests.py` *warm*: `report_*.html` and
`run_record.json` under `reports/` are gitignored generated artifacts that are
simply there, so the suite that gates every commit has never been proven against
the tree a checkout actually produces. This script proves it — and refuses to run
on a tree that is already warm (exit 2, `cold_start.refuse_if_warm`), because on
one the claim is vacuous:

1. **Pristine.** No generated stage-12 report and no ledger anywhere in
   `reports/`, so the suite has to establish its own evidence to pass.
2. **Green.** The suite must exit 0 with zero failures and zero errors, and
   every test it counted must have passed — read from its own footer, not
   assumed from the exit code alone.
3. **Self-sufficient.** Afterwards `reports/` must hold the artifacts the suite
   needed *and* a valid ledger (`uvest-run-record/1`) carrying one `ran` row per
   golden record whose SHA-256 still matches the bytes on disk. A suite that
   could only pass against pre-generated artifacts fails here, with that reason.

Run cold, then hand the pristine state on: `git clean -Xdf reports` removes only
the ignored generated files (the tracked `sample_report.html` survives), which is
how CI chains this prove-with-nothing run into the cold-provenance proof.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent.parent
sys.path.insert(0, str(TESTS_DIR))

import cold_start  # noqa: E402  (sibling eval module: the shared cold checks)
import provenance  # noqa: E402

SUITE = REPO_ROOT / "tests" / "run_all_tests.py"

# The suite's parallel runner footer; the whole proof hangs off these numbers.
SUITE_SUMMARY_RE = re.compile(
    r"Total Tests: (\d+) \| Passed: (\d+) \| Failures: (\d+) \| Errors: (\d+)")


def parse_suite_summary(text: str) -> tuple[int, int, int, int] | None:
    """`(total, passed, failures, errors)` from the suite's own count line."""
    match = SUITE_SUMMARY_RE.search(text)
    return tuple(int(value) for value in match.groups()) if match else None


def run_suite() -> tuple[int, str]:
    """Run the invariant suite in a clean interpreter; return `(code, output)`."""
    proc = subprocess.run([sys.executable, str(SUITE)], capture_output=True,
                          check=False, cwd=REPO_ROOT)
    return proc.returncode, ((proc.stdout + proc.stderr)
                             .decode("utf-8", "replace"))


def evidence_problems(reports_dir: Path, total: int) -> list[str]:
    """Why the suite's leftovers do not stand behind the run that just passed.

    A suite that goes green while producing nothing has not proved the fresh-clone
    claim: its artifact gates must have graded evidence *this* run produced, which
    means both the reports and the ledger naming their hashes. Every missing or
    contradictory piece is reported by name, so the failure says what to fix.
    """
    produced = cold_start.report_artifacts(reports_dir)
    if not produced:
        return ["the suite left no generated stage-12 report — it passed without "
                "producing the evidence its artifact gates grade"]
    ledger_path = reports_dir / provenance.RECORD_NAME
    if not ledger_path.exists():
        return [f"no ledger at {cold_start._label(ledger_path)} — the artifact "
                "gates cannot have graded proven bytes"]
    try:
        record = provenance.load(ledger_path)  # validates schema and row shape
    except provenance.ProvenanceError as exc:
        return [f"unreadable ledger: {exc}"]
    rows = [entry for entry in record["stages"] if entry["status"] == "ran"]
    problems: list[str] = []
    if len(rows) != total or {entry["stage"] for entry in rows} != {12}:
        problems.append(
            f"ledger carries {len(rows)} `ran` row(s) for stage(s) "
            f"{sorted({entry['stage'] for entry in rows})}, expected {total} "
            "stage-12 row(s)")
    if len(produced) != total:
        problems.append(f"{len(produced)} generated report(s) on disk, "
                        f"expected {total}")
    mismatched = [entry["artifact"] for entry in rows
                  if not entry.get("artifact_sha256")
                  or provenance.sha256_of(Path(entry["artifact_path"]))
                  != entry["artifact_sha256"]]
    if mismatched:
        problems.append(f"{len(mismatched)} ledger row(s) do not match the bytes "
                        f"on disk: {mismatched[:3]}")
    integrity = provenance.Proof([ledger_path]).integrity_problems()
    if integrity:
        problems.append(f"ledger contradicts the artifacts it names: {integrity[0]}")
    return problems


def main() -> int:
    # No CLI knobs on purpose: the suite always writes the repo's own reports/
    # (its seam resolves that path itself), so a flag that pointed the checks
    # somewhere else would only ever produce a confusing false failure.
    reports_dir = cold_start.REPORTS_DIR

    refusal = cold_start.refuse_if_warm(reports_dir)
    if refusal is not None:
        return refusal

    total = cold_start.golden_record_count()
    print(f"  Pristine checkout: 0 generated report(s), no ledger — the suite must "
          f"produce its own evidence for {total} golden record(s).")

    # 1. Green on nothing: the fresh-clone claim.
    code, out = run_suite()
    summary = parse_suite_summary(out)
    if summary is None:
        print("❌ The suite printed no summary line — its footer format changed, "
              "so this proof cannot read the result:", file=sys.stderr)
        print(out[-2000:], file=sys.stderr)
        return 1
    ran, passed, failures, errors = summary
    if code != 0 or failures or errors or passed != ran:
        print(f"❌ The invariant suite did not pass on a pristine checkout: "
              f"exit {code}, {passed}/{ran} passed, {failures} failure(s), "
              f"{errors} error(s)", file=sys.stderr)
        print(out[-4000:], file=sys.stderr)
        return 1
    print(f"  ✅ suite on a pristine tree: {passed}/{ran} passed, exit 0 "
          "(no generated artifact existed to lean on)")

    # 2. Self-sufficient: the evidence it graded came out of that run.
    problems = evidence_problems(reports_dir, total)
    if problems:
        print("❌ The suite passed, but the evidence it left behind does not stand "
              "behind the run — a green suite that produced nothing is not this "
              "proof:", file=sys.stderr)
        for problem in problems:
            print(f"   ✗ {problem}", file=sys.stderr)
        return 1
    print(f"  ✅ self-sufficient: the run produced {total} report(s) and a "
          f"{total}-row ledger, every artifact_sha256 matching the disk")
    print(f"\n✅ PRISTINE-SUITE PROVEN — {ran} test(s) green on a fresh checkout, "
          "evidence produced by the run itself")
    return 0


if __name__ == "__main__":
    sys.exit(main())
