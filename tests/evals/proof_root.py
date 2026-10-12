"""The proof root as a run record: payload evidence in the record vocabulary.

The artifact-graded gates trace every byte they read to a **run record**
(`tests/evals/provenance.py`). Payload evidence lives somewhere else:
`tests/proof/scenario_manifest.json` declares, per scenario, which artifact each
stage produced and with what provenance, and `manifest.sha256` freezes those
bytes. So a payload could be `waived` or `quarantined` — declared unproven — and
the gate reading it would simply abstain, with nothing in the *verdict* to show
for the smaller graded set that abstention leaves behind.

This module folds that declaration into the record vocabulary: one row per
declared artifact, so payloads are proven (or disclosed as unproven) exactly the
way stage-12 reports are:

| Manifest evidence | Record status | Meaning |
| :--- | :--- | :--- |
| `recorded` | `ran` | the frozen sha256 of the bytes on disk — the only status that may claim bytes |
| `waived` | `skipped` | absent by design (engine/data source unavailable); the reason is carried through |
| `quarantined` | `skipped` | present but **never** evidence (E5); the register is named in the reason |

Rows are labelled `<scenario_id>:<artifact>` (`label`): a bare artifact name
repeats across scenarios — 31 of them each carry an `out_2_market.json` — so the
label, not the file name, is what a payload row is keyed by.

Two deliberate properties:

* **The fold is a read, not a write.** The proof root is already the record of its
  own bytes: the hash file is the claim and the disk is the check. Nothing is
  written back into the frozen proof root, so nothing in it can drift from this
  record — and a payload edited afterwards stops being proven the moment the
  bytes are compared (`Proof.proves_payload`, `Proof.integrity_problems`).
* **A contradictory proof root is an error, not a thinner record.** A manifest
  row whose `evidence` disagrees with the quarantine register, an unknown
  evidence value, a duplicate label or a `recorded` artifact with no proof-root
  path raises `ProofRootError`: folding it into a clean-looking record would be
  the same silent-shrink failure this module exists to prevent.
"""

from __future__ import annotations

import json
from pathlib import Path

import provenance
import validate_manifest as vm

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PROOF_ROOT = REPO_ROOT / "tests" / "proof"
MANIFEST_FILE = vm.MANIFEST_FILE
HASH_FILE = vm.HASH_FILE
REGISTER_FILE = vm.REGISTER_FILE
MANIFEST_PATH = PROOF_ROOT / MANIFEST_FILE

# How the manifest's per-artifact provenance folds into the record statuses.
EVIDENCE_STATUS = {"recorded": "ran", "waived": "skipped", "quarantined": "skipped"}

QUARANTINE_PREFIX = "registered quarantined"


class ProofRootError(Exception):
    """The proof root cannot be folded into evidence: missing, malformed, contradictory."""


def label(scenario_id: str, artifact: str) -> str:
    """The record label of a payload — scenario-qualified, so it is unique."""
    return f"{scenario_id}:{artifact}"


def _read_json(path: Path, what: str) -> dict:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProofRootError(f"{what} not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ProofRootError(f"{what} unreadable: {path} ({exc})") from exc
    if not isinstance(document, dict):
        raise ProofRootError(f"{what} is not a JSON object: {path}")
    return document


def _frozen_hashes(root: Path) -> dict[str, str]:
    """`manifest.sha256` through the validator's own reader — one parsing rule."""
    report = vm.ValidationReport()
    entries = vm._read_hash_file(root, report)
    if entries is None or report.errors:
        first = report.errors[0] if report.errors else None
        detail = f": {first.code} {first.message}" if first else ""
        raise ProofRootError(f"{HASH_FILE} cannot be read as the proof root's "
                             f"byte freeze{detail}")
    return entries


def _quarantined(root: Path) -> dict[tuple[str, str], dict]:
    """The quarantine register, keyed by `(scenario_id, artifact)`."""
    document = _read_json(root / REGISTER_FILE, REGISTER_FILE)
    if document.get("schema") != vm.REGISTER_SCHEMA:
        raise ProofRootError(f"{REGISTER_FILE} schema {document.get('schema')!r} is "
                             f"not {vm.REGISTER_SCHEMA!r} — its rows cannot be "
                             "read as provenance")
    held: dict[tuple[str, str], dict] = {}
    for row in document.get("rows") or []:
        if not isinstance(row, dict):
            raise ProofRootError(f"{REGISTER_FILE} carries a row that is not an object")
        held[(str(row.get("scenario_id")), str(row.get("artifact")))] = row
    return held


def rows(proof_root: Path | None = None) -> list[dict]:
    """One record row per artifact the proof root declares, in manifest order."""
    root = Path(proof_root) if proof_root is not None else PROOF_ROOT
    manifest = _read_json(root / MANIFEST_FILE, MANIFEST_FILE)
    scenarios = manifest.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise ProofRootError(f"{MANIFEST_FILE} declares no scenarios: {root / MANIFEST_FILE}")
    hashes = _frozen_hashes(root)
    held = _quarantined(root)

    built: list[dict] = []
    seen: set[str] = set()
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            raise ProofRootError(f"{MANIFEST_FILE} carries a scenario that is not an object")
        scenario_id = str(scenario.get("scenario_id"))
        for declared in scenario.get("recorded_artifacts") or []:
            if not isinstance(declared, dict):
                raise ProofRootError(f"{scenario_id}: recorded_artifacts carries "
                                     "an entry that is not an object")
            artifact = str(declared.get("artifact"))
            name = label(scenario_id, artifact)
            if name in seen:
                raise ProofRootError(f"{name}: declared twice — a payload label must "
                                     "name exactly one artifact")
            seen.add(name)
            evidence = declared.get("evidence")
            if evidence not in EVIDENCE_STATUS:
                raise ProofRootError(f"{name}: evidence {evidence!r} is not one of "
                                     f"{tuple(EVIDENCE_STATUS)}")
            is_quarantined = (scenario_id, artifact) in held
            if is_quarantined != (evidence == "quarantined"):
                disagreement = ("holds it" if is_quarantined
                                else "does not hold it")
                raise ProofRootError(
                    f"{name}: the manifest declares {evidence!r} but "
                    f"{REGISTER_FILE} {disagreement} — the proof root contradicts "
                    "itself")
            reason = str(declared.get("reason") or "no reason recorded")
            stage = declared.get("stage")

            if evidence == "recorded":
                relative = vm._safe_relpath(declared.get("path"))
                if relative is None:
                    raise ProofRootError(f"{name}: a recorded artifact must name a "
                                         "proof-root-relative path")
                frozen = hashes.get(relative)
                if frozen is None:
                    built.append(provenance.row(
                        stage, "failed", artifact=name,
                        skip_reason=f"no frozen sha256 for {relative} in {HASH_FILE}"))
                    continue
                built.append(provenance.row(
                    stage, "ran", artifact=name, artifact_path=root / relative,
                    artifact_sha256=frozen, exit_code=0))
                continue

            if evidence == "quarantined":
                built.append(provenance.row(
                    stage, "skipped", artifact=name,
                    skip_reason=f"{QUARANTINE_PREFIX} in {REGISTER_FILE}: {reason} "
                                "(present, never evidence — E5)"))
                continue

            built.append(provenance.row(stage, "skipped", artifact=name,
                                        skip_reason=f"waived: {reason}"))
    return built


def record(proof_root: Path | None = None) -> dict:
    """The proof root as a validated `uvest-run-record/1` document.

    Validated through the same rules `provenance.load` applies to a ledger read
    off disk, so a folded record is never a weaker witness than a stored one.
    """
    root = Path(proof_root) if proof_root is not None else PROOF_ROOT
    document = provenance.build_record(
        rows(root), runner=f"tests/proof/{MANIFEST_FILE}", workdir=root,
        outcome="complete", exit_code=0)
    try:
        return provenance.validate_document(document, root / MANIFEST_FILE)
    except provenance.ProvenanceError as exc:
        raise ProofRootError(f"the folded proof-root record is not readable "
                             f"evidence: {exc}") from exc
