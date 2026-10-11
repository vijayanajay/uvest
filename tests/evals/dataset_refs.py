"""Cross-dataset artifact references: golden dataset → proof root.

A golden-dataset record may *point at* an artifact instead of embedding an
extracted subset of its values:

    "script_outputs": {
      "out_6_fundamental.json": {
        "$ref": "tests/proof/fixtures/GB-2023-0003/quarantined/out_6_fundamental.json"
      }
    }

The referenced file is then the single source of truth for those bytes: the
proof root's `manifest.sha256` freezes them and `quarantine_register.json`
records their provenance, so the same payload can never also be copied inline
and drift (`tests/test_proof_manifest.py` enforces that).

Outcome claims can also be *derived* from a pinned tape instead of typed:

    "forward_prices_22d": {
      "$derive": {
        "tape": "tests/proof/fixtures/GB-2023-0003/forward_tape.json",
        "rule": "forward_min_max"
      }
    }

The value is recomputed at load by the frozen rule below from the tape's own
bars, so a record whose proof row pins a forward tape never hand-types what the
tape did — it references the tape or carries nothing. `$derive` is exclusive
exactly like `$ref`: a half-migrated value (`{"$derive": …, "min": 268.0}`)
fails loudly instead of quietly grading the stale copy.

Three deliberate properties:

* **Repo-relative refs.** The dataset and the proof root sit at different
  depths, so a ref is anchored at the repo root (the module's own location, not
  the caller's cwd) — refs survive temp-dataset runs and any working directory.
* **Provenance is derived, never restated.** The dataset says only *where* the
  bytes are; whether they are `recorded` or `quarantined` is read from the
  register at load time. A label copied into the dataset would be one more
  thing to drift.
* **`$ref` is exclusive.** A reference object may not also carry payload keys,
  so a half-migrated record (`{"$ref": …, "promoter_pledge_pct": 54.2}`) fails
  loudly instead of quietly grading the stale copy.

Quarantined payloads are resolved for inspection but are **never evidence**:
callers must keep them out of graded gates (`tests/run_evals.py` discloses and
excludes them — E5 of `docs/agents/scenario-manifest-schema.md`).
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

REF_KEY = "$ref"
DERIVE_KEY = "$derive"

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PROOF_ROOT = REPO_ROOT / "tests" / "proof"
REGISTER_FILE = "quarantine_register.json"

# Repo-relative, forward-slash, no drive letters, no `..`, no absolute paths.
_SAFE_REF_RE = re.compile(r"^(?![A-Za-z]:)(?!/)[\w./-]+$")

PROVENANCE_RECORDED = "recorded"
PROVENANCE_QUARANTINED = "quarantined"


class RefError(Exception):
    """A reference is malformed, unsafe, missing, or unreadable."""


def is_ref(value: Any) -> bool:
    """True iff `value` is a reference object."""
    return isinstance(value, dict) and REF_KEY in value


def ref_path(value: Any) -> str:
    """The repo-relative path a reference names, validated for safety."""
    if not is_ref(value):
        raise RefError(f"not a reference object: {value!r}")
    extra = sorted(set(value) - {REF_KEY})
    if extra:
        raise RefError(
            f"a reference may not carry payload keys ({extra}) — the referenced "
            "file is the only copy of those bytes"
        )
    raw = value[REF_KEY]
    if not isinstance(raw, str) or not _SAFE_REF_RE.match(raw) or ".." in raw.split("/"):
        raise RefError(f"unsafe or malformed $ref path: {raw!r}")
    return raw


def _load_artifact(repo_rel: str, root: Path) -> dict:
    """Read a repo-relative JSON object, with one error vocabulary for callers."""
    target = root / repo_rel
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RefError(f"referenced artifact is missing: {repo_rel}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RefError(f"referenced artifact is unreadable: {repo_rel} ({exc})") from exc
    if not isinstance(payload, dict) or not payload:
        raise RefError(f"referenced artifact is not a non-empty JSON object: {repo_rel}")
    return payload


def resolve(value: Any, root: Path | None = None) -> dict:
    """Load the payload a reference names."""
    return _load_artifact(ref_path(value), root or REPO_ROOT)


# ---------------------------------------------------------------------------
# Derived outcome claims (`$derive`) — computed from a pinned forward tape
# ---------------------------------------------------------------------------
def is_derive(value: Any) -> bool:
    """True iff `value` is a derivation object."""
    return isinstance(value, dict) and DERIVE_KEY in value


def _number(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RefError(f"{what} must be numeric, got {value!r}")
    return float(value)


def _bars_of(doc: dict, tape: str) -> list[dict]:
    bars = doc.get("bars")
    if not isinstance(bars, list) or not bars:
        raise RefError(f"derivation tape carries no bars: {tape}")
    for bar in bars:
        if not isinstance(bar, dict) or not isinstance(bar.get("date"), str):
            raise RefError(f"derivation tape bar is not a dated record: {tape}")
        for key in ("high", "low", "close"):
            if isinstance(bar.get(key), bool) or not isinstance(bar.get(key), (int, float)):
                raise RefError(f"derivation tape bar has no numeric {key}: {tape}")
    return bars


def _rule_forward_min_max(bars: list[dict], trade: dict) -> dict:
    """The frozen range cache: min of lows / max of highs (V9's own formula)."""
    return {"min": round(min(b["low"] for b in bars), 4),
            "max": round(max(b["high"] for b in bars), 4)}


def _rule_market_mae_pct(bars: list[dict], trade: dict) -> float:
    """Max adverse excursion from the record's authored market entry, in percent."""
    entry = _number(trade.get("entry_price"), "entry_price (market_mae_pct)")
    return round((entry - min(b["low"] for b in bars)) / entry * 100.0, 4)


def _rule_resolution_date(bars: list[dict], trade: dict) -> str:
    """The frozen convention: the last forward bar's date (the horizon end)."""
    return bars[-1]["date"]


def resolution_of(bars: list[dict], stop: float, target: float) -> tuple[str, str]:
    """The frozen resolution rule: `(outcome, basis)`, first touch wins.

    Bars are scanned in order and the first level touched decides — the trade is
    flat the moment either level prints. Daily bars cannot order touches *within*
    a bar, so    a bar carrying both levels resolves to the worse one (LOSS). This
    is the single implementation behind the `$derive` rule below, the curator's
    seed scan (`curate.outcome`), the catalog scaffolder's tape comparison
    (`scaffold_catalog.resolve`) and the migration tool's row scan
    (`pin_legacy_records.outcome_of`): admission, authoring, stored resolution
    and migrated records can never disagree about what the tape did.
    """
    for bar in bars:
        hit_stop, hit_target = bar["low"] <= stop, bar["high"] >= target
        if hit_stop and hit_target:
            return "LOSS", "both_hit_conservative_loss"
        if hit_stop:
            return "LOSS", "stop_first"
        if hit_target:
            return "WIN", "target_first"
    return "TIME", "horizon_end"


def _rule_resolution_outcome(bars: list[dict], trade: dict) -> str:
    """WIN/LOSS/TIME by the frozen scan — `resolution_of` decides."""
    stop = _number(trade.get("stop_loss"), "stop_loss (resolution_outcome)")
    target = _number(trade.get("target_price"), "target_price (resolution_outcome)")
    return resolution_of(bars, stop, target)[0]


def _rule_realized_direction(bars: list[dict], trade: dict) -> int:
    """1 iff the window's final close finished above the executed entry."""
    entry = _number(trade.get("entry_price"), "entry_price (realized_direction)")
    return 1 if bars[-1]["close"] > entry else 0


FORWARD_TAPE_RULES = {
    "forward_min_max": _rule_forward_min_max,
    "market_mae_pct": _rule_market_mae_pct,
    "resolution_date": _rule_resolution_date,
    "resolution_outcome": _rule_resolution_outcome,
    "realized_direction": _rule_realized_direction,
}


def derive_spec(value: Any) -> tuple[str, str]:
    """The `(tape, rule)` a derivation names, validated for safety and support."""
    if not is_derive(value):
        raise RefError(f"not a derivation object: {value!r}")
    extra = sorted(set(value) - {DERIVE_KEY})
    if extra:
        raise RefError(
            f"a derivation may not carry payload keys ({extra}) — the tape is the "
            "only source of those numbers"
        )
    spec = value[DERIVE_KEY]
    if not isinstance(spec, dict) or set(spec) != {"tape", "rule"}:
        raise RefError(f"a derivation must name exactly a tape and a rule: {value!r}")
    tape, rule = spec["tape"], spec["rule"]
    if not isinstance(tape, str) or not _SAFE_REF_RE.match(tape) or ".." in tape.split("/"):
        raise RefError(f"unsafe or malformed derivation tape: {tape!r}")
    if rule not in FORWARD_TAPE_RULES:
        raise RefError(f"unknown derivation rule {rule!r} (supported: "
                       f"{sorted(FORWARD_TAPE_RULES)})")
    return tape, rule


def derive_value(value: Any, trade: dict, root: Path | None = None) -> Any:
    """Recompute a derivation from the pinned tape's bars."""
    tape, rule = derive_spec(value)
    doc = _load_artifact(tape, root or REPO_ROOT)
    return FORWARD_TAPE_RULES[rule](_bars_of(doc, tape), trade)


def derivations(data: dict) -> list[tuple[str, str, str, str]]:
    """Every `(trade_id, field, tape, rule)` derivation in a dataset."""
    found: list[tuple[str, str, str, str]] = []
    for trade in data.get("trades", []):
        for field, value in trade.items():
            if is_derive(value):
                tape, rule = derive_spec(value)
                found.append((str(trade.get("trade_id")), field, tape, rule))
    return found


def refs(data: dict) -> list[tuple[str, str, str]]:
    """Every `(trade_id, artifact, ref_path)` reference in a dataset."""
    found: list[tuple[str, str, str]] = []
    for trade in data.get("trades", []):
        for artifact, payload in (trade.get("script_outputs") or {}).items():
            if is_ref(payload):
                found.append((str(trade.get("trade_id")), artifact, ref_path(payload)))
    return found


def quarantined_artifacts(root: Path | None = None) -> set[tuple[str, str]]:
    """`(scenario_id, artifact)` pairs the quarantine register holds."""
    root = root or REPO_ROOT
    path = (root / "tests" / "proof" / REGISTER_FILE)
    try:
        register = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return {
        (str(row.get("scenario_id")), str(row.get("artifact")))
        for row in register.get("rows", [])
        if isinstance(row, dict)
    }


def provenance(scenario_id: str, artifact: str, root: Path | None = None) -> str:
    """Whether an artifact is registered quarantined, else `recorded`."""
    if (scenario_id, artifact) in quarantined_artifacts(root):
        return PROVENANCE_QUARANTINED
    return PROVENANCE_RECORDED


def quarantined_in(trade: dict, root: Path | None = None) -> list[str]:
    """Artifacts of `trade` that the register holds quarantined."""
    tid = str(trade.get("trade_id"))
    return sorted(
        artifact
        for artifact in (trade.get("script_outputs") or {})
        if (tid, artifact) in quarantined_artifacts(root)
    )


def materialize(data: dict, root: Path | None = None) -> tuple[dict, list[str], list[str]]:
    """Replace every reference and derivation with the value it stands for.

    Returns `(dataset, ref_notes, derive_notes)`: a deep copy safe to mutate,
    plus one disclosure line per resolved reference
    (`"<trade>:<artifact> <- <path> (<provenance>)"`) and per derived outcome
    field (`"<trade>:<field> <- derived <rule> from <tape>"`), so a caller can
    print what it read, from where, and by which frozen rule.
    """
    materialized = copy.deepcopy(data)
    ref_notes: list[str] = []
    derive_notes: list[str] = []
    for trade in materialized.get("trades", []):
        tid = trade.get("trade_id")
        outputs = trade.get("script_outputs") or {}
        for artifact, payload in list(outputs.items()):
            if not is_ref(payload):
                continue
            target = ref_path(payload)
            outputs[artifact] = resolve(payload, root)
            ref_notes.append(
                f"{tid}:{artifact} <- {target} ({provenance(str(tid), artifact, root)})"
            )
        for field, value in list(trade.items()):
            if not is_derive(value):
                continue
            tape, rule = derive_spec(value)
            trade[field] = derive_value(value, trade, root)
            derive_notes.append(f"{tid}:{field} <- derived {rule} from {tape}")
    return materialized, ref_notes, derive_notes
