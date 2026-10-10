#!/usr/bin/env python3
"""Scenario-manifest validator — checks V1–V14 (ADR-007 / scenario-manifest-schema.md).

Pure stdlib, offline, deterministic. Usable as a library (the Layer-1 test seam
imports `validate_manifest`) or as a CLI:

    python tests/evals/validate_manifest.py --proof-root tests/proof

Exit codes: 0 = no errors (warnings allowed), 1 = at least one error,
2 = usage / unreadable manifest.

Path namespace: every path in the manifest AND in `manifest.sha256` is
root-relative to the proof root (so `sha256sum -c manifest.sha256` works when
run from the proof root). Absolute paths, drive letters, and `..` are V3 errors.

Frozen breaker -> contract-stage map used by V11 (the only `gate_focus` entries
with stage-precise evidence):

    inverted_risk_reward        -> stage 1
    tighter_than_0_75x_atr      -> stage 2
    promoter_pledging_gt_25pct  -> stage 6
    earnings_within_7d          -> stage 6
    fno_ban_active              -> stage 0
    panic_regime                -> stage 7

Runtime sandboxes materialized under the proof root must live under `_replay/`
(the only allowlisted directory prefix); `_replay/` is gitignored and never
evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

MANIFEST_FILE = "scenario_manifest.json"
HASH_FILE = "manifest.sha256"
BASELINE_FILE = "baseline_scorecard.json"
REGISTER_FILE = "quarantine_register.json"
ALLOWED_PREFIXES = ("_replay/",)

SCHEMA_NAME = "uvest-scenario-manifest/1"
REGISTER_SCHEMA = "uvest-quarantine-register/1"

FROZEN_CHECKS = tuple(f"V{i}" for i in range(1, 15))

ID_RE = re.compile(r"^(GB-\d{4}-\d{4}|SCEN-\d{3})$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")

ORIGINS = ("curated_behavioral", "scan_generated")
INSTRUMENTS = ("CASH", "FUT")
OPTION_TYPES = ("CE", "PE", "OPT", "OPTION", "CALL", "PUT")
ACTIONS = ("BUY",)
EVIDENCE = ("recorded", "waived", "quarantined")
OUTCOMES = ("WIN", "LOSS", "TIME")
RESOLUTION_BASES = ("target_first", "stop_first", "both_hit_conservative_loss", "horizon_end")
QUADRANTS = ("TP", "FP", "TN", "FN")
VERDICTS = ("GO", "GO_WEAK", "WAIT", "AVOID", "HOLD", "PRESSURE", "EXIT")
EXCLUDED_QUADRANT_TAGS = ("WAIT_TRACKED_SEPARATELY", "STEWARDSHIP")
EXCLUDED_QUADRANT_VERDICTS = ("WAIT", "HOLD", "PRESSURE", "EXIT")

BREAKER_STAGE_DEPS = {
    "inverted_risk_reward": 1,
    "tighter_than_0_75x_atr": 2,
    "promoter_pledging_gt_25pct": 6,
    "earnings_within_7d": 6,
    "fno_ban_active": 0,
    "panic_regime": 7,
}

_UNSET = object()


@dataclass
class Finding:
    code: str
    message: str
    scenario_id: str | None = None


@dataclass
class ValidationReport:
    errors: list[Finding] = field(default_factory=list)
    warnings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, code: str, message: str, scenario_id: str | None = None) -> None:
        self.errors.append(Finding(code, message, scenario_id))

    def warn(self, code: str, message: str, scenario_id: str | None = None) -> None:
        self.warnings.append(Finding(code, message, scenario_id))

    def codes(self) -> set[str]:
        """Every check-prefix emitted anywhere (errors and warnings)."""
        found: set[str] = set()
        for finding in (*self.errors, *self.warnings):
            found.add(finding.code.split("_", 1)[0])
        return found


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _rows(manifest: Any, report: ValidationReport) -> list[dict]:
    if not isinstance(manifest, dict):
        report.error("V1_MANIFEST_NOT_OBJECT", "manifest must be a JSON object")
        return []
    raw = manifest.get("scenarios")
    if not isinstance(raw, list):
        report.error("V1_SCENARIOS_MISSING", "top-level `scenarios` must be a list")
        return []
    rows: list[dict] = []
    for idx, row in enumerate(raw):
        if not isinstance(row, dict):
            report.error("V2_ROW_NOT_OBJECT", f"scenarios[{idx}] is not an object")
            continue
        rows.append(row)
    return rows


def _read_json(path: Path) -> tuple[Any, bool]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), True
    except (OSError, json.JSONDecodeError):
        return None, False


def _safe_relpath(value: Any) -> str | None:
    """Return the normalized proof-root-relative path, or None if unsafe."""
    if not isinstance(value, str) or not value.strip():
        return None
    if re.match(r"^[A-Za-z]:", value) or value.startswith(("/", "\\")):
        return None
    parts = value.replace("\\", "/").split("/")
    if any(part in ("", "..") for part in parts):
        return None
    return "/".join(parts)


def _replay_paths(row: dict) -> list[str]:
    inputs = row.get("replay_inputs") or {}
    paths: list[str] = []
    if isinstance(inputs, dict):
        paths.append(inputs.get("canonical_trade"))
        fixtures = inputs.get("truncated_fixtures")
        if isinstance(fixtures, list):
            paths.extend(fixtures)
    for artifact in row.get("recorded_artifacts") or []:
        if isinstance(artifact, dict) and artifact.get("path") is not None:
            paths.append(artifact.get("path"))
    return [p for p in paths if isinstance(p, str)]


def _scoring_paths(row: dict) -> list[str]:
    scoring = row.get("scoring") or {}
    out = scoring.get("forward_tape") if isinstance(scoring, dict) else None
    return [out] if isinstance(out, str) else []


def _is_date(value: Any) -> bool:
    return isinstance(value, str) and bool(DATE_RE.match(value))


def _normalize_newlines(raw: bytes) -> bytes:
    """Text-mode equivalence: CRLF and LF recordings compare equal (Windows)."""
    return raw.replace(b"\r\n", b"\n")


def _collect_bars(obj: Any) -> list[dict]:
    """Every dict carrying a `date` key, anywhere in the document."""
    bars: list[dict] = []
    if isinstance(obj, dict):
        if isinstance(obj.get("date"), str):
            bars.append(obj)
        for value in obj.values():
            bars.extend(_collect_bars(value))
    elif isinstance(obj, list):
        for item in obj:
            bars.extend(_collect_bars(item))
    return bars


def _read_hash_file(proof_root: Path, report: ValidationReport) -> dict[str, str] | None:
    path = proof_root / HASH_FILE
    if not path.exists():
        report.error("V4_HASH_FILE_MISSING", f"{HASH_FILE} not found at proof root")
        return None
    entries: dict[str, str] = {}
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split(None, 1)
        if len(parts) != 2 or not HASH_RE.match(parts[0]):
            report.error("V4_HASH_FILE_MALFORMED", f"{HASH_FILE}:{lineno}: bad line {line!r}")
            continue
        entry_path = _safe_relpath(parts[1].strip())
        if entry_path is None:
            report.error("V4_HASH_FILE_MALFORMED",
                         f"{HASH_FILE}:{lineno}: unsafe or empty path {parts[1]!r}")
            continue
        if entry_path in entries:
            report.error("V4_DUPLICATE_HASH_ENTRY",
                         f"{HASH_FILE}:{lineno}: duplicate entry for {entry_path}")
            continue
        entries[entry_path] = parts[0]
    return entries


# --------------------------------------------------------------------------
# V1–V14
# --------------------------------------------------------------------------
def _v1_ids(rows: list[dict], report: ValidationReport) -> None:
    seen: set[str] = set()
    for row in rows:
        sid = row.get("scenario_id")
        if not isinstance(sid, str) or not sid.strip():
            report.error("V1_MISSING_ID", "scenario_id must be a non-empty string")
            continue
        if not ID_RE.match(sid):
            report.error("V1_BAD_ID_FAMILY",
                         f"scenario_id {sid!r} matches no frozen ID family (GB-/SCEN-)", sid)
        if sid in seen:
            report.error("V1_DUPLICATE_ID", f"duplicate scenario_id {sid!r}", sid)
        seen.add(sid)


def _v2_enums(manifest: dict, rows: list[dict], report: ValidationReport) -> None:
    if manifest.get("schema") != SCHEMA_NAME:
        report.error("V2_MANIFEST_SCHEMA",
                     f"top-level schema must be {SCHEMA_NAME!r}")
    for row in rows:
        sid = row.get("scenario_id")
        checks = (
            ("schema_version", (SCHEMA_NAME,), row.get("schema_version")),
            ("origin", ORIGINS, row.get("origin")),
            ("instrument", INSTRUMENTS, row.get("instrument")),
            ("action", ACTIONS, row.get("action")),
            ("expected_verdict", VERDICTS, row.get("expected_verdict")),
        )
        for field_name, allowed, value in checks:
            if value is None and field_name == "expected_verdict":
                continue
            if value not in allowed:
                report.error("V2_ENUM", f"{field_name}={value!r} not in {allowed}", sid)
        instrument = row.get("instrument")
        if isinstance(instrument, str) and instrument in OPTION_TYPES:
            report.error("V2_OPTIONS_PROHIBITED",
                         "options can never be recorded (ADR-001)", sid)
        if instrument == "FUT" and not _is_date(row.get("expiry")):
            report.error("V2_FUT_EXPIRY_MISSING",
                         "FUT rows require a YYYY-MM-DD expiry", sid)
        scoring = row.get("scoring") or {}
        if not isinstance(scoring, dict):
            report.error("V2_SCORING_NOT_OBJECT", "scoring must be an object", sid)
            continue
        if scoring.get("resolution_outcome") not in OUTCOMES:
            report.error("V2_ENUM",
                         f"resolution_outcome={scoring.get('resolution_outcome')!r} "
                         f"not in {OUTCOMES}", sid)
        if scoring.get("resolution_basis") not in RESOLUTION_BASES:
            report.error("V2_ENUM",
                         f"resolution_basis={scoring.get('resolution_basis')!r} "
                         f"not in {RESOLUTION_BASES}", sid)
        if scoring.get("quadrant_target") is not None and scoring.get("quadrant_target") not in QUADRANTS:
            report.error("V2_ENUM",
                         f"quadrant_target={scoring.get('quadrant_target')!r} "
                         f"not in {QUADRANTS}", sid)
        for artifact in row.get("recorded_artifacts") or []:
            if not isinstance(artifact, dict):
                report.error("V2_ARTIFACT_NOT_OBJECT", "recorded_artifacts entries must be objects", sid)
                continue
            if artifact.get("evidence") not in EVIDENCE:
                report.error("V2_ENUM",
                             f"evidence={artifact.get('evidence')!r} not in {EVIDENCE}", sid)


def _v3_paths(rows: list[dict], hash_entries: dict[str, str] | None,
              proof_root: Path, report: ValidationReport) -> None:
    references = [(row.get("scenario_id"), p)
                  for row in rows
                  for p in (*_replay_paths(row), *_scoring_paths(row))]
    if hash_entries:
        references += [(None, p) for p in hash_entries]
    for sid, raw in references:
        norm = _safe_relpath(raw)
        if norm is None:
            report.error("V3_UNSAFE_PATH",
                         f"path {raw!r} is absolute, empty, or contains '..'", sid)
            continue
        resolved = (proof_root / norm)
        try:
            resolved.resolve().relative_to(proof_root.resolve())
        except ValueError:
            report.error("V3_UNSAFE_PATH", f"path {norm!r} escapes the proof root", sid)
            continue
        if not resolved.exists():
            report.error("V3_MISSING_PATH", f"path {norm!r} does not exist", sid)


def _v4_hashes(rows: list[dict], hash_entries: dict[str, str] | None,
               proof_root: Path, report: ValidationReport) -> None:
    if hash_entries is None:
        return
    referenced = {p for row in rows for p in (*_replay_paths(row), *_scoring_paths(row))
                  if _safe_relpath(p) is not None}
    referenced = {_safe_relpath(p) for p in referenced}  # type: ignore[arg-type]
    for path in sorted(referenced):
        if path not in hash_entries:
            report.error("V4_UNHASHED_EVIDENCE", f"referenced file {path!r} has no hash entry")
    for path, expected in hash_entries.items():
        resolved = proof_root / path
        if not resolved.exists():
            report.error("V4_HASH_ENTRY_MISSING_FILE", f"hashed file {path!r} does not exist")
            continue
        actual = hashlib.sha256(resolved.read_bytes()).hexdigest()
        if actual != expected:
            report.error("V4_HASH_MISMATCH",
                         f"{path!r} does not match its frozen sha256")


def _v5_unlisted(rows: list[dict], hash_entries: dict[str, str] | None,
                 proof_root: Path, report: ValidationReport) -> None:
    allowlisted = {MANIFEST_FILE, HASH_FILE, BASELINE_FILE, REGISTER_FILE}
    for row in rows:
        for path in (*_replay_paths(row), *_scoring_paths(row)):
            norm = _safe_relpath(path)
            if norm:
                allowlisted.add(norm)
    if hash_entries:
        allowlisted.update(hash_entries)
    if not proof_root.exists():
        report.error("V5_PROOF_ROOT_MISSING", f"proof root {proof_root} does not exist")
        return
    for path in sorted(p for p in proof_root.rglob("*") if p.is_file()):
        rel = path.relative_to(proof_root).as_posix()
        if rel in allowlisted or any(rel.startswith(prefix) for prefix in ALLOWED_PREFIXES):
            continue
        report.error("V5_UNLISTED_EVIDENCE", f"unlisted evidence file {rel!r}")


def _v6_blindness(rows: list[dict], report: ValidationReport) -> None:
    for row in rows:
        sid = row.get("scenario_id")
        replay = {_safe_relpath(p) for p in _replay_paths(row)}
        scoring = {_safe_relpath(p) for p in _scoring_paths(row)}
        overlap = {p for p in replay & scoring if p}
        if overlap:
            report.error("V6_BLINDNESS_OVERLAP",
                         f"outcome path(s) also used as replay input: {sorted(overlap)}", sid)


def _v7_prefix(rows: list[dict], proof_root: Path, report: ValidationReport) -> None:
    for row in rows:
        sid = row.get("scenario_id")
        inputs = row.get("replay_inputs") or {}
        if not isinstance(inputs, dict):
            report.error("V7_REPLAY_INPUTS_NOT_OBJECT", "replay_inputs must be an object", sid)
            continue
        cutoff = (inputs.get("truncation") or {}).get("cutoff") if isinstance(inputs.get("truncation"), dict) else None
        min_bars = (inputs.get("truncation") or {}).get("min_bars") if isinstance(inputs.get("truncation"), dict) else None
        if not _is_date(cutoff):
            report.error("V7_CUTOFF_MISSING", "truncation.cutoff must be YYYY-MM-DD", sid)
            continue
        if not isinstance(min_bars, int) or min_bars < 0:
            report.error("V7_MIN_BARS_MISSING", "truncation.min_bars must be a non-negative int", sid)
            continue
        bars: list[dict] = []
        for path in _replay_paths(row):
            norm = _safe_relpath(path)
            if norm is None:
                continue
            doc, loaded = _read_json(proof_root / norm)
            if not loaded:
                report.error("V7_FIXTURE_UNREADABLE", f"{norm!r} is not readable JSON", sid)
                continue
            bars.extend(_collect_bars(doc))
        if not bars:
            report.error("V7_NO_BARS", "replay inputs carry no bar-like records", sid)
            continue
        late = [b.get("date") for b in bars if isinstance(b.get("date"), str) and b["date"] > cutoff]
        if late:
            report.error("V7_POST_CUTOFF_BAR",
                         f"replay input contains bar(s) after T_rec {cutoff}: {late[:3]}", sid)
        if len(bars) < min_bars:
            report.error("V7_TOO_FEW_BARS", f"{len(bars)} prefix bars < declared min_bars {min_bars}", sid)


def _v8_forward(rows: list[dict], proof_root: Path, report: ValidationReport) -> None:
    for row in rows:
        sid = row.get("scenario_id")
        scoring = row.get("scoring") or {}
        if not isinstance(scoring, dict):
            continue
        tape_rel = _safe_relpath(scoring.get("forward_tape"))
        if tape_rel is None:
            report.error("V8_TAPE_MISSING", "scoring.forward_tape must be a safe path", sid)
            continue
        doc, loaded = _read_json(proof_root / tape_rel)
        if not loaded:
            report.error("V8_TAPE_UNREADABLE", f"{tape_rel!r} is not readable JSON", sid)
            continue
        bars = _collect_bars(doc)
        declared = scoring.get("bars")
        if not isinstance(declared, int) or len(bars) != declared:
            report.error("V8_BAR_COUNT",
                         f"forward tape has {len(bars)} bar(s); scoring.bars declares {declared}", sid)
        cutoff = ((row.get("replay_inputs") or {}).get("truncation") or {}).get("cutoff")
        early = [b.get("date") for b in bars
                 if _is_date(cutoff) and isinstance(b.get("date"), str) and b["date"] <= cutoff]
        if early:
            report.error("V8_PRE_CUTOFF_BAR",
                         f"forward tape contains bar(s) at or before T_rec {cutoff}: {early[:3]}", sid)
        for bar in bars:
            if not isinstance(bar.get("high"), (int, float)) or not isinstance(bar.get("low"), (int, float)):
                report.error("V8_TAPE_BAR_INCOMPLETE",
                             f"forward bar {bar.get('date')!r} lacks numeric high/low", sid)
                break


def _v9_caches(rows: list[dict], proof_root: Path, report: ValidationReport) -> None:
    for row in rows:
        sid = row.get("scenario_id")
        scoring = row.get("scoring") or {}
        cache = scoring.get("forward_derived_cache") if isinstance(scoring, dict) else None
        if cache is None:
            continue
        tape_rel = _safe_relpath((scoring or {}).get("forward_tape"))
        doc = None
        if tape_rel is not None:
            doc, loaded = _read_json(proof_root / tape_rel)
            if not loaded:
                doc = None
        if not isinstance(cache, dict) or doc is None:
            report.error("V9_CACHE_UNVERIFIABLE",
                         "derived cache exists but the forward tape cannot be read", sid)
            continue
        bars = [b for b in _collect_bars(doc)
                if isinstance(b.get("high"), (int, float)) and isinstance(b.get("low"), (int, float))]
        if not bars:
            report.error("V9_CACHE_UNVERIFIABLE", "no usable bars to recompute the cache", sid)
            continue
        derived = {"min": min(b["low"] for b in bars), "max": max(b["high"] for b in bars)}
        for key, value in derived.items():
            cached = cache.get(key)
            if not isinstance(cached, (int, float)) or not math.isclose(
                    float(cached), float(value), rel_tol=0.0, abs_tol=1e-6):
                report.error("V9_CACHE_MISMATCH",
                             f"forward_derived_cache.{key}={cached!r} != recomputed {value!r}", sid)


def _v10_replay(rows: list[dict], proof_root: Path,
                rerun: Callable[[dict, dict], bytes | None] | None,
                report: ValidationReport) -> None:
    if rerun is None:
        if any(a.get("evidence") == "recorded"
               for row in rows for a in row.get("recorded_artifacts") or []
               if isinstance(a, dict)):
            report.warn("V10_REPLAY_NOT_PROVIDED",
                        "no replay runner supplied; byte-comparison of recorded artifacts skipped")
        return
    for row in rows:
        sid = row.get("scenario_id")
        for artifact in row.get("recorded_artifacts") or []:
            if not isinstance(artifact, dict) or artifact.get("evidence") != "recorded":
                continue
            norm = _safe_relpath(artifact.get("path"))
            if norm is None:
                continue
            fresh = rerun(row, artifact)
            if fresh is None:
                report.warn("V10_REPLAY_UNCHECKED",
                            f"stage {artifact.get('stage')} not offline-runnable; not compared", sid)
                continue
            recorded = (proof_root / norm).read_bytes()
            # The recorder writes through text mode, so on Windows the on-disk
            # artifact legitimately carries CRLF while a fresh stdout capture is
            # LF-normalized — the same equivalence tests/test_recorder.py asserts.
            if _normalize_newlines(fresh) != _normalize_newlines(recorded):
                report.error("V10_REPLAY_MISMATCH",
                             f"stage {artifact.get('stage')} fresh run differs from recorded bytes", sid)


def _v11_quarantine(rows: list[dict], proof_root: Path, report: ValidationReport) -> None:
    quarantined: list[tuple[str, int, str]] = []
    for row in rows:
        sid = row.get("scenario_id")
        declared_stages = {
            a.get("stage") for a in row.get("recorded_artifacts") or []
            if isinstance(a, dict) and isinstance(a.get("stage"), int)
        }
        for artifact in row.get("recorded_artifacts") or []:
            if not isinstance(artifact, dict) or artifact.get("evidence") != "quarantined":
                continue
            if not artifact.get("reason"):
                report.error("V11_QUARANTINE_REASON_MISSING",
                             f"stage {artifact.get('stage')} quarantined without a reason", sid)
            if isinstance(artifact.get("stage"), int):
                quarantined.append((sid or "<?>", artifact["stage"], str(artifact.get("artifact"))))
        quarantined_stages = {
            a["stage"] for a in (row.get("recorded_artifacts") or [])
            if isinstance(a, dict) and a.get("evidence") == "quarantined"
            and isinstance(a.get("stage"), int)
        }
        for gate in row.get("gate_focus") or []:
            dep = BREAKER_STAGE_DEPS.get(gate)
            if dep is None:
                continue
            if dep in quarantined_stages:
                report.error("V11_QUARANTINED_GATE_EVIDENCE",
                             f"gate {gate!r} depends on stage {dep}, whose artifact is quarantined", sid)
            elif dep not in declared_stages:
                report.error("V11_GATE_ARTIFACT_UNDECLARED",
                             f"gate {gate!r} depends on stage {dep}, which no artifact entry declares", sid)
    register_path = proof_root / REGISTER_FILE
    if not quarantined:
        return
    if not register_path.exists():
        report.error("V11_REGISTER_MISSING",
                     "quarantined artifacts exist but no quarantine register is committed")
        return
    register, loaded = _read_json(register_path)
    if not loaded or not isinstance(register, dict) or register.get("schema") != REGISTER_SCHEMA:
        report.error("V11_REGISTER_MALFORMED", f"{REGISTER_FILE} is not a readable register")
        return
    declared: list[tuple[str, int, str]] = []
    for entry in register.get("rows") or []:
        if not isinstance(entry, dict):
            report.error("V11_REGISTER_MALFORMED", "register rows must be objects")
            return
        declared.append((entry.get("scenario_id"), entry.get("stage"), str(entry.get("artifact"))))
    if sorted(declared) != sorted(quarantined):
        report.error("V11_REGISTER_MISMATCH",
                     "register rows do not exactly match the manifest's quarantined artifacts")


def _v12_baseline(proof_root: Path, baseline: Any, require_baseline: bool,
                  allow_re_freeze: bool, report: ValidationReport) -> None:
    if baseline is None:
        if require_baseline:
            report.error("V12_BASELINE_MISSING", f"{BASELINE_FILE} is required but absent")
        else:
            report.warn("V12_BASELINE_MISSING", f"{BASELINE_FILE} absent; regression check skipped")
        return
    if not isinstance(baseline, dict):
        report.error("V12_BASELINE_MALFORMED", f"{BASELINE_FILE} must be a JSON object")
        return
    digest_manifest = hashlib.sha256((proof_root / MANIFEST_FILE).read_bytes()).hexdigest()
    digest_hashes = hashlib.sha256((proof_root / HASH_FILE).read_bytes()).hexdigest()
    pairs = (
        ("scenario_manifest_sha256", digest_manifest),
        ("manifest_sha256", digest_hashes),
    )
    for key, actual in pairs:
        frozen = baseline.get(key)
        if frozen == actual:
            continue
        if allow_re_freeze:
            report.warn("V12_RE_FREEZE_DECLARED",
                        f"{key} changed ({frozen} -> {actual}); re-freeze declared")
        else:
            report.error("V12_DIGEST_MISMATCH",
                         f"{key} does not match the frozen baseline ({frozen} -> {actual})")


def _v13_quadrant_exclusion(rows: list[dict], report: ValidationReport) -> None:
    for row in rows:
        sid = row.get("scenario_id")
        tags = set(row.get("classification_tags") or [])
        excluded = bool(tags & set(EXCLUDED_QUADRANT_TAGS)) or row.get("expected_verdict") in EXCLUDED_QUADRANT_VERDICTS
        if excluded and (row.get("scoring") or {}).get("quadrant_target") is not None:
            report.error("V13_TARGET_ON_EXCLUDED_ROW",
                         "WAIT/stewardship rows must omit quadrant_target", sid)


def _v14_truncation_cutoff(rows: list[dict], report: ValidationReport) -> None:
    for row in rows:
        sid = row.get("scenario_id")
        cutoff = ((row.get("replay_inputs") or {}).get("truncation") or {}).get("cutoff")
        if cutoff != row.get("recommendation_date"):
            report.error("V14_CUTOFF_MISMATCH",
                         f"truncation.cutoff={cutoff!r} != recommendation_date="
                         f"{row.get('recommendation_date')!r}", sid)


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------
def validate_manifest(
    manifest: Any,
    proof_root: Path | str,
    *,
    baseline: Any = _UNSET,
    rerun: Callable[[dict, dict], bytes | None] | None = None,
    require_baseline: bool = False,
    allow_re_freeze: bool = False,
) -> ValidationReport:
    """Run checks V1–V14. `baseline=None` explicitly skips V12."""
    report = ValidationReport()
    proof_root = Path(proof_root)
    rows = _rows(manifest, report)
    hash_entries = _read_hash_file(proof_root, report)

    _v1_ids(rows, report)
    _v2_enums(manifest if isinstance(manifest, dict) else {}, rows, report)
    _v3_paths(rows, hash_entries, proof_root, report)
    _v4_hashes(rows, hash_entries, proof_root, report)
    _v5_unlisted(rows, hash_entries, proof_root, report)
    _v6_blindness(rows, report)
    _v7_prefix(rows, proof_root, report)
    _v8_forward(rows, proof_root, report)
    _v9_caches(rows, proof_root, report)
    _v10_replay(rows, proof_root, rerun, report)
    _v11_quarantine(rows, proof_root, report)

    baseline_obj = baseline
    if baseline is _UNSET:
        loaded, ok = _read_json(proof_root / BASELINE_FILE)
        baseline_obj = loaded if ok else None
    _v12_baseline(proof_root, baseline_obj, require_baseline, allow_re_freeze, report)

    _v13_quadrant_exclusion(rows, report)
    _v14_truncation_cutoff(rows, report)
    return report


def render_report(report: ValidationReport) -> str:
    lines: list[str] = []
    for finding in report.errors:
        loc = f" [{finding.scenario_id}]" if finding.scenario_id else ""
        lines.append(f"[FAIL] {finding.code}{loc}: {finding.message}")
    for finding in report.warnings:
        loc = f" [{finding.scenario_id}]" if finding.scenario_id else ""
        lines.append(f"[WARN] {finding.code}{loc}: {finding.message}")
    verdict = "PASSED" if report.ok else "FAILED"
    lines.append(f"SCENARIO MANIFEST VALIDATION: {verdict} "
                 f"({len(report.errors)} error(s), {len(report.warnings)} warning(s))")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a UVEST scenario manifest (V1–V14).")
    parser.add_argument("--proof-root", default="tests/proof",
                        help="Directory holding scenario_manifest.json and manifest.sha256.")
    parser.add_argument("--require-baseline", action="store_true",
                        help="Fail when baseline_scorecard.json is absent.")
    parser.add_argument("--allow-re-freeze", action="store_true",
                        help="Downgrade baseline digest drift to a warning (deliberate re-freeze).")
    args = parser.parse_args(argv)

    proof_root = Path(args.proof_root)
    manifest_path = proof_root / MANIFEST_FILE
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"❌ Manifest not found: {manifest_path}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"❌ Manifest is not valid JSON: {manifest_path}\n   {exc}", file=sys.stderr)
        return 2

    report = validate_manifest(
        manifest, proof_root,
        require_baseline=args.require_baseline,
        allow_re_freeze=args.allow_re_freeze,
    )
    print(render_report(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
