"""Run-record provenance for graded evidence (`uvest-run-record/1`).

The scorecard grades *bytes*: the stage-12 HTML reports, and the recorded
payloads pinned in the proof root. Bytes on disk prove nothing on their own —
a report left behind by an old run, or edited by hand, reads exactly like one a
real stage produced. So every artifact the harness grades is traced to a **run
record**: the ledger `scripts/run_pipeline.py` writes into its workdir
(`run_record.json`, one row per stage in ORDER, naming the artifact it produced
and the SHA-256 of those exact bytes), and the record
`tests/evals/report_regen.py` writes for the stage-12 replay it performs.

`Proof.proves(name, path, stage)` is the grade-time question — *does a record
show this stage ran and produce **these** bytes?* — and it answers with a reason
either way, so an unproven artifact is disclosed and excluded rather than
silently graded (the harness reproduces anything unproven first, so a clean
clone still grades a full set: see `run_evals.ensure_reports`).

A record is a claim, so claims are checked. `integrity_problems()` names any
`ran` row whose bytes are now missing or different — a record that contradicts
the disk is a broken witness, and the suite fails rather than grading around it.
A `failed` row is how a replay declares "I reproduced these bytes and they are
not the ones on disk": proof that could not be established, never a quiet pass.

Schema drift is a defect, so `SCHEMA`/`ROW_KEYS`/`STATUSES` mirror
`scripts/run_pipeline.py`'s `RUN_RECORD_SCHEMA` and `stage_row()`, and
`tests/test_run_evals.py` asserts the two agree — the runner stays a standalone
CLI (scripts/ never imports tests/), so the agreement is enforced by a seam
rather than by a shared import.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Mirrors scripts/run_pipeline.py (RUN_RECORD_SCHEMA / RUN_RECORD_NAME).
SCHEMA = "uvest-run-record/1"
RECORD_NAME = "run_record.json"

# The closed status set: executed and verified / executed but did not land /
# disclosed and deliberately not executed / the run stopped before it.
STATUSES = ("ran", "failed", "skipped", "not_reached")

# Mirrors the runner's `stage_row()` keys exactly — every row carries all of
# them, so a reader never guards for absent fields.
ROW_KEYS = {
    "order", "stage", "status", "artifact", "artifact_path", "artifact_sha256",
    "exit_code", "skip_reason", "seconds",
}


class ProvenanceError(Exception):
    """A run record that cannot be trusted as evidence (missing/bad/future schema)."""


def utc_now() -> str:
    """UTC ISO-8601 stamp at seconds precision (the record writers' convention)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_of(path: Path) -> str:
    """The hash both writers record and this module re-computes before grading."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def row(stage: int, status: str, *, artifact: str | None = None,
        artifact_path: Path | str | None = None, artifact_sha256: str | None = None,
        exit_code: int | None = None, skip_reason: str | None = None,
        seconds: float | None = None) -> dict:
    """One run-record row, in the runner's shape (row order is assigned on build).

    Only `ran` rows may set `artifact_path`/`artifact_sha256`: those are claims
    about bytes, and no un-executed stage is allowed to make one.
    """
    if status not in STATUSES:
        raise ProvenanceError(f"{status!r} is not one of the frozen statuses {STATUSES}")
    if status != "ran" and (artifact_path or artifact_sha256):
        raise ProvenanceError(
            f"a {status!r} row cannot claim artifact bytes (stage {stage})")
    return {
        "order": None,
        "stage": stage,
        "status": status,
        "artifact": artifact,
        "artifact_path": str(artifact_path) if artifact_path is not None else None,
        "artifact_sha256": artifact_sha256,
        "exit_code": exit_code,
        "skip_reason": skip_reason,
        "seconds": seconds,
    }


def build_record(rows: list[dict], *, runner: str, workdir: Path | str,
                 input_path: str | None = None, report: str | None = None,
                 outcome: str = "complete", exit_code: int | None = 0,
                 failed_stage: int | None = None, started_at: str | None = None,
                 finished_at: str | None = None) -> dict:
    """The record envelope, with row order assigned by position in the walk.

    `input`/`report` are `None` for a replay that has no chain input and writes
    no single canonical report (a stage-12 batch covers many records); the fields
    are kept so both writers emit one shape.
    """
    return {
        "schema": SCHEMA,
        "runner": runner,
        "input": input_path,
        "workdir": str(workdir),
        "started_at": started_at,
        "finished_at": finished_at,
        "outcome": outcome,
        "exit_code": exit_code,
        "failed_stage": failed_stage,
        "report": report,
        "stages": [dict(r, order=index) for index, r in enumerate(rows)],
    }


def load(path: Path) -> dict:
    """Read and validate one record; anything off raises `ProvenanceError`.

    Validation is strict on purpose — a record is evidence, so a future schema,
    a missing row key or an unknown status must be an error the caller reports,
    never a silently thinner proof.
    """
    path = Path(path)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProvenanceError(f"{path}: run record not found") from exc
    except json.JSONDecodeError as exc:
        raise ProvenanceError(f"{path}: not valid JSON ({exc})") from exc
    return validate_document(document, path)


def validate_document(document: Any, path: Path | str) -> dict:
    """Check an already-parsed record against the frozen schema and row shape.

    Split out of `load` so a record that is *built* rather than read — the proof
    root's payload fold (`tests/evals/proof_root.py`) — is validated by the same
    rules the harness applies to a ledger it reads off disk: one validator, so a
    constructed record cannot be a weaker witness than a stored one.
    """
    path = Path(path)
    if not isinstance(document, dict) or document.get("schema") != SCHEMA:
        got = document.get("schema") if isinstance(document, dict) else type(document).__name__
        raise ProvenanceError(f"{path}: schema {got!r} is not {SCHEMA!r}")
    stages = document.get("stages")
    if not isinstance(stages, list) or not stages:
        raise ProvenanceError(f"{path}: no stage rows to read")
    for index, entry in enumerate(stages):
        if not isinstance(entry, dict):
            raise ProvenanceError(f"{path}: row {index} is not an object")
        if set(entry) != ROW_KEYS:
            raise ProvenanceError(
                f"{path}: row {index} keys {sorted(entry)} are not the frozen row shape")
        if entry["status"] not in STATUSES:
            raise ProvenanceError(
                f"{path}: row {index} status {entry['status']!r} is not one of {STATUSES}")
        if not isinstance(entry["stage"], int) or isinstance(entry["stage"], bool):
            raise ProvenanceError(f"{path}: row {index} stage {entry['stage']!r} is not a stage number")
    return document


def proven_bytes_label(rows: list[tuple[Path, dict]], label: str,
                        path: Path | None) -> tuple[bool, str]:
    """Shared body of `Proof.proves` / `Proof.proves_payload`.

    Takes the rows a record carries for a label plus the file the caller grades
    from, and answers whether those *exact* bytes are proven. `path=None` means
    the caller has no recorded file to compare — the payload is carried inline in
    the dataset — which no record can prove.
    """
    if path is None:
        # Inline bytes: the dataset holds the only copy, so no declaration can
        # prove them — whatever the proof root says about the label, there is no
        # file whose hash could be checked against a recording (E1).
        return False, (f"{label} is carried inline in the dataset — no recorded "
                       "artifact backs those bytes")
    if not rows:
        return False, f"no run record shows {label} among its recorded artifacts"
    source_name = rows[0][0].name
    for source, entry in rows:
        if entry["status"] != "ran":
            continue
        if not path.exists():
            return False, f"{label} is not on disk ({path})"
        if entry.get("artifact_sha256") and sha256_of(path) == entry["artifact_sha256"]:
            return True, f"{source.name} recorded these bytes for {label}"
        return False, (f"{source.name} recorded different bytes for {label} — "
                       "the file on disk is not that recording's output")
    entry = rows[0][1]
    return False, (f"{label} was {entry['status']} in {source_name}: "
                   f"{entry.get('skip_reason') or 'no reason recorded'}")


def document_stage_summary(document: dict) -> dict[str, list[int]]:
    """Distinct stages per status in ONE record (for disclosure).

    Exists so a caller that already holds a parsed record — including one that was
    built rather than read (`tests/evals/proof_root.py`) — can render it without
    going back to disk for a file that was never there.
    """
    summary: dict[str, list[int]] = {status: [] for status in STATUSES}
    for entry in document["stages"]:
        if entry["stage"] not in summary[entry["status"]]:
            summary[entry["status"]].append(entry["stage"])
    for stages in summary.values():
        stages.sort()
    return summary


def verified_rows(record: dict | None) -> list[dict]:
    """Rows of an earlier record whose claimed bytes are still exactly on disk.

    The record is a ledger that outlives one invocation: a replay that later
    re-writes the same artifacts should carry forward the rows it can still
    stand behind, and drop the ones whose files changed or vanished (those rows
    are no longer evidence of anything).
    """
    if not record:
        return []
    carried: list[dict] = []
    for entry in record.get("stages", []):
        if entry.get("status") != "ran" or not entry.get("artifact_path"):
            continue
        path = Path(entry["artifact_path"])
        if path.exists() and entry.get("artifact_sha256") == sha256_of(path):
            carried.append(entry)
    return carried


class Proof:
    """The folded evidence index: which artifact came from which recorded run."""

    def __init__(self, paths: list[Path] | None = None):
        self.records: list[tuple[Path, dict]] = []
        for path in paths or []:
            self.add(path)

    def add(self, path: Path, record: dict | None = None) -> None:
        """Fold in one record (loaded and validated unless already parsed)."""
        path = Path(path)
        self.records.append((path, record if record is not None else load(path)))

    @property
    def sources(self) -> list[Path]:
        return [path for path, _ in self.records]

    def rows_for(self, artifact: str) -> list[tuple[Path, dict]]:
        """Every row any record carries about `artifact`, source-tagged."""
        return [(path, entry)
                for path, document in self.records
                for entry in document["stages"]
                if entry.get("artifact") == artifact]

    def proves(self, artifact: str, path: Path, stage: int) -> tuple[bool, str]:
        """Does a record show `stage` ran and produce *these* bytes?

        The returned reason is what a gate prints when the answer is no, so it
        names the actual obstacle: no record mentions the artifact, the stage
        was skipped/failed there, or the bytes on disk are not the ones that run
        recorded.
        """
        path = Path(path)
        rows = [(source, entry) for source, entry in self.rows_for(artifact)
                if entry["stage"] == stage]
        if not rows:
            where = (", ".join(source.name for source in self.sources)
                     if self.sources else "no run record was read")
            return False, f"no run record shows stage {stage} producing {artifact} ({where})"
        return proven_bytes_label(rows, artifact, path)

    def proves_payload(self, label: str, path: Path | None) -> tuple[bool, str]:
        """Does a record prove these bytes for a scenario-qualified payload label?

        Payload labels are `<scenario_id>:<artifact>`
        (`tests/evals/proof_root.py`): a bare artifact name repeats across
        scenarios — 31 of them each carry an `out_2_market.json` — so the label,
        not the file name, is what a payload row is keyed by, and no stage filter
        is needed to disambiguate.

        This is the payload half of the same question `proves` asks about stage 12
        artifacts: a payload a gate reads is only evidence when a record shows the
        bytes the proof root froze for it.
        """
        return proven_bytes_label(self.rows_for(label), label, path)

    def stage_summary(self) -> dict[str, list[int]]:
        """Distinct stages per status across every record read (for disclosure)."""
        summary: dict[str, list[int]] = {status: [] for status in STATUSES}
        for _path, document in self.records:
            for status, stages in document_stage_summary(document).items():
                summary[status] = sorted(set(summary[status]) | set(stages))
        return summary

    def integrity_problems(self) -> list[str]:
        """`ran` rows whose bytes the disk contradicts — a record lying about itself.

        A row whose artifact no longer exists is *not* a problem: records outlive
        the workdirs they describe (a temp run directory is deleted, the ledger
        stays), and a vanished file simply stops being proof.
        """
        problems: list[str] = []
        for source, document in self.records:
            for entry in document["stages"]:
                if entry["status"] != "ran" or not entry.get("artifact_path"):
                    continue
                artifact = Path(entry["artifact_path"])
                if not artifact.exists():
                    continue
                actual = sha256_of(artifact)
                if actual != entry.get("artifact_sha256"):
                    problems.append(
                        f"{source.name}: stage {entry['stage']} claims {artifact.name} is "
                        f"{str(entry.get('artifact_sha256'))[:12]}…, on disk it is {actual[:12]}…")
        return problems
