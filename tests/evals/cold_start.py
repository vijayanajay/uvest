"""Clean-checkout proof: the eval harness establishes its own provenance.

    python tests/evals/cold_start.py

`reports/report_*.html` and `reports/run_record.json` are gitignored generated
artifacts, so a fresh checkout has neither and the artifact-graded gates have
nothing to read. That cold path — reproduce every report from the proof root,
write the ledger, then grade what the ledger proves — is the path CI exercises
and no developer machine ever does: locally the artifacts are always already
there, warm and proven. This script proves it, twice, and fails the run (exit 1)
if any number disagrees:

1. **Cold.** The harness must exit 0, report `11/11`-style completeness over the
   whole golden dataset — *both* halves of it, stage-12 evidence and the gate
   payloads every graded gate reads — and, the load-bearing detail, *write* every
   report itself (`N written, 0 verified by reproduction`). A tree that quietly started warm would
   show reproductions instead of writes, or no replay line at all; a proof root
   that does not back every gate input leaves the roster incomplete and this
   proof fails, which is the point of checking it rather than the exit code.
2. **Ledger.** `reports/run_record.json` must validate, carry exactly one `ran`
   row per golden record, name the artifact it produced, and its SHA-256 must
   equal the bytes now on disk (`provenance.integrity_problems` is the check the
   harness itself trusts).
3. **Warm.** Re-running must reuse that ledger: same completeness, exit 0, and
   *no* replay at all — grading is tied to recorded proof, not re-derived.

It refuses to run on a tree that is not cold (exit 2, with the commands to get
there), so a local invocation can never be mistaken for the CI proof. Every
number printed is read back from the harness output or the ledger — nothing is
asserted from memory.
"""

from __future__ import annotations

import argparse
import json
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

import provenance  # noqa: E402  (sibling eval module, spliced above)

HARNESS = REPO_ROOT / "tests" / "run_evals.py"
DATASET_FILE = REPO_ROOT / "tests" / "evals" / "golden_dataset.json"
REPORTS_DIR = REPO_ROOT / "reports"
COMMITTED_REPORTS = {"sample_report.html"}  # the one report that IS tracked

COLD_REPLAY_RE = re.compile(
    r"↻ stage-12 replay: (\d+) report\(s\) missing or unproven — "
    r"(\d+) written, (\d+) verified by reproduction")
PROVEN_RE = re.compile(r"Stage-12 reports proven: (\d+)/(\d+)")
COVERAGE_RE = re.compile(
    r"Evidence completeness: OK \((\d+)/(\d+) record\(s\) carry proven stage-12 "
    r"evidence; (\d+)/(\d+) record\(s\) carry gate payloads with no "
    r"unregistered gap")
VERDICT_LINE = "FINAL EVALUATION VERDICT: SUITE PASSED"


def _label(path: Path) -> str:
    """Repo-relative when it is inside the repo, else the path as given.

    The proof defaults to `reports/` but accepts any directory (`--reports-dir`),
    including sandboxes and temp dirs outside the checkout.
    """
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def report_artifacts(reports_dir: Path) -> list[Path]:
    """Generated stage-12 reports in a directory (the committed sample excluded)."""
    return sorted(path for path in reports_dir.glob("report_*.html")
                  if path.name not in COMMITTED_REPORTS)


def check_cold(reports_dir: Path) -> list[str]:
    """Why this tree is not a clean checkout; an empty list means it is cold.

    A warm tree cannot prove the cold path, and a "pass" obtained on one would be
    a false green — so the checks are explicit rather than assumed from the
    absence of a directory.
    """
    problems: list[str] = []
    if not reports_dir.exists():
        return problems  # nothing generated yet: colder than cold, which is fine
    generated = report_artifacts(reports_dir)
    if generated:
        problems.append(f"{len(generated)} generated report(s) present, e.g. "
                        f"{generated[0].name}")
    ledger = reports_dir / provenance.RECORD_NAME
    if ledger.exists():
        problems.append(f"{_label(ledger)} present "
                        "(the ledger the cold run must write itself)")
    return problems


def refuse_if_warm(reports_dir: Path) -> int | None:
    """Exit code 2 (after explaining why) when the tree is not pristine, else None.

    Shared by both pristine-checkout proofs: a claim about a fresh clone is
    vacuous on a tree that already holds the generated artifacts, so neither
    script is allowed to run on one.
    """
    problems = check_cold(reports_dir)
    if not problems:
        return None
    print("❌ This tree is not a clean checkout, so the pristine path cannot be "
          "proved here:", file=sys.stderr)
    for problem in problems:
        print(f"   ✗ {problem}", file=sys.stderr)
    print("\n   Get there with:\n"
          "     rm -f reports/run_record.json reports/report_*.html\n"
          "     git clean -Xdf reports   # or drop every ignored generated file\n"
          "   (generated, gitignored artifacts — the harness rebuilds both)",
          file=sys.stderr)
    return 2


def run_harness() -> tuple[int, str]:
    """Run the eval harness in a clean interpreter; return `(exit code, output)`."""
    proc = subprocess.run([sys.executable, str(HARNESS)], capture_output=True,
                          check=False, cwd=REPO_ROOT)
    return proc.returncode, ((proc.stdout + proc.stderr)
                             .decode("utf-8", "replace"))


def golden_record_count() -> int:
    """The roster size the run must cover, read from the dataset itself."""
    data = json.loads(DATASET_FILE.read_text(encoding="utf-8"))
    return len(data.get("trades", []))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prove the eval harness's cold-checkout provenance path.")
    parser.add_argument("--reports-dir", default=str(REPORTS_DIR),
                        help="generated-artifact directory (default: reports/).")
    args = parser.parse_args(argv)
    reports_dir = Path(args.reports_dir)

    refusal = refuse_if_warm(reports_dir)
    if refusal is not None:
        return refusal

    total = golden_record_count()
    print(f"  Cold checkout: {total} golden record(s), no ledger, no generated "
          "report — proving the path CI runs and no dev machine does.")

    # 1. Cold: the harness reproduces its own evidence from the proof root.
    code, out = run_harness()
    if code != 0:
        print(f"❌ Cold harness run exited {code} (expected 0):", file=sys.stderr)
        print(out, file=sys.stderr)
        return 1
    if VERDICT_LINE not in out:
        print("❌ Cold run did not reach the passed verdict:", file=sys.stderr)
        print(out, file=sys.stderr)
        return 1
    replay = COLD_REPLAY_RE.search(out)
    if not replay:
        print("❌ Cold run reported no stage-12 replay — it graded a tree it did "
              "not have to reproduce, so this was not the cold path:", file=sys.stderr)
        print(out, file=sys.stderr)
        return 1
    pending, written, verified = (int(value) for value in replay.groups())
    if (pending, written, verified) != (total, total, 0):
        print(f"❌ Cold replay must write all {total} report(s): got "
              f"{pending} pending, {written} written, {verified} verified",
              file=sys.stderr)
        return 1
    proven, proven_total = (int(value) for value in
                            PROVEN_RE.search(out).groups())
    coverage = COVERAGE_RE.search(out)
    if not coverage:
        # Both halves of the evidence roster have to be complete for this proof;
        # without the line there is nothing to read, and a partial roster is what
        # the completeness check exists to catch (never a silent pass).
        print("❌ Cold run did not report a complete evidence roster — the "
              "proof root does not back every gate input:", file=sys.stderr)
        print(out, file=sys.stderr)
        return 1
    report_covered, report_total, payload_covered, payload_total = (
        int(value) for value in coverage.groups())
    expected = (total, total, total, total)
    if ((proven, proven_total, report_covered, report_total)
            != expected or (payload_covered, payload_total) != (total, total)):
        print(f"❌ Cold run did not prove the whole roster: reports "
              f"{proven}/{proven_total}, stage-12 evidence {report_covered}/"
              f"{report_total}, gate payloads {payload_covered}/{payload_total}, "
              f"expected {total} each", file=sys.stderr)
        print(out, file=sys.stderr)
        return 1
    print(f"  ✅ cold run: {written} report(s) reproduced from the proof root, "
          f"{total}/{total} proven, completeness OK (reports and gate payloads)")

    # 2. Ledger: the proof is on disk, validates, and matches the bytes.
    ledger_path = reports_dir / provenance.RECORD_NAME
    if not ledger_path.exists():
        print(f"❌ Cold run left no ledger at {_label(ledger_path)}", file=sys.stderr)
        return 1
    record = provenance.load(ledger_path)  # validates schema and row shape
    rows = [entry for entry in record["stages"] if entry["status"] == "ran"]
    if len(rows) != total:
        print(f"❌ Ledger carries {len(rows)} `ran` row(s), expected {total}",
              file=sys.stderr)
        return 1
    if {entry["stage"] for entry in rows} != {12}:
        print("❌ Ledger rows must all describe stage 12 for a report replay: "
              f"{sorted({entry['stage'] for entry in rows})}", file=sys.stderr)
        return 1
    mismatched = [entry["artifact"] for entry in rows
                  if not entry.get("artifact_sha256")
                  or provenance.sha256_of(Path(entry["artifact_path"]))
                  != entry["artifact_sha256"]]
    if mismatched:
        print(f"❌ {len(mismatched)} ledger row(s) do not match the bytes on disk: "
              f"{mismatched[:3]}", file=sys.stderr)
        return 1
    integrity = provenance.Proof([ledger_path]).integrity_problems()
    if integrity:
        print(f"❌ Ledger contradicts the artifacts it names: {integrity[0]}",
              file=sys.stderr)
        return 1
    print(f"  ✅ ledger: {len(rows)} stage-12 row(s), every artifact_sha256 equals "
          "the file on disk")

    # 3. Warm: the same artifacts and the ledger reuse themselves untouched.
    code, out = run_harness()
    if code != 0 or VERDICT_LINE not in out:
        print(f"❌ Warm harness run exited {code} (expected 0 and the passed "
              "verdict):", file=sys.stderr)
        print(out, file=sys.stderr)
        return 1
    if COLD_REPLAY_RE.search(out):
        print("❌ Warm run replayed reports it had already proven — the ledger is "
              "not what graded this run:", file=sys.stderr)
        print(out, file=sys.stderr)
        return 1
    warm_proven, warm_total = (int(value) for value in
                               PROVEN_RE.search(out).groups())
    if (warm_proven, warm_total) != (total, total):
        print(f"❌ Warm run proved {warm_proven}/{warm_total}, expected "
              f"{total}/{total}", file=sys.stderr)
        return 1
    print(f"  ✅ warm re-run: {total}/{total} proven from the ledger, no replay, "
          "verdict passed")
    print(f"\n✅ COLD-START PROVEN — {total} record(s): reproduced, ledgered, "
          "graded, reused")
    return 0


if __name__ == "__main__":
    sys.exit(main())
