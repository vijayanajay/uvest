"""EVAL-001 — Behavioral Evals Benchmark Runner (FROZEN, `docs/agents/evals.md` §4).

Run headlessly:

    python tests/run_evals.py --benchmark tests/evals/golden_dataset.json

Emits the evaluation scorecard to stdout, zero network calls, deterministic:
the JSON file drives the score exactly. When a gate's inputs exist for the graded records,
it is graded. When its inputs are absent (the dataset is complete at 52/52, so
the engine behind the gate does not exist yet), the runner prints [SKIP] rather
than inventing a number.

Stage-12 reports are gitignored *generated* artifacts, so the two
artifact-graded gates (Zero Hallucination, HTML Validity) first replay any
report they cannot stand behind from its proof root (`ensure_reports` →
`tests/evals/report_regen.py`): a fresh clone grades the same bytes a dev
machine does, a report absent at grading time is disclosed in the gate's
detail (never a silent [SKIP]), and a replay that cannot run fails the run
(exit 1) rather than quietly shrinking the graded set.

**Gate 1 replays stage 0 (evals.md §3 #3).** The option prohibition is a
property of *this commit's parser*, not of a stored exit code, so the gate
re-runs each seeded option paste (`option_probes`) through the real stage-0 CLI
(`python scripts/0_ingest_reco.py --input <paste>`) and grades the live result:
exit 2, `ERR_OPTIONS_PROHIBITED` on stderr, and no canonical payload on stdout. The
probes live beside the 52-trade roster rather than inside it, because an option can
never *be* one of its trades — stage 0 rejects the paste before a canonical
`current_trade.json` exists, and the proof root's scenarios pin no option
instrument. The roster is governed by `meta.option_probes_expected`, so adding a
probe is a deliberate act and losing one fails the run.

**Gate 1c grades the closed instrument domain (evals.md §2.1/§3 #3).** ADR-001 permits
equities and futures only, so stage 0 refuses anything it cannot recognize as
`CASH`/`FUT` — with `ERR_INSTRUMENT_UNRECOGNIZED`, never `ERR_OPTIONS_PROHIBITED`.
The `refusal_probes` roster holds the two shapes the option-token scan cannot name:
an option written with look-alike letters (`Type: СЕ`, Cyrillic; `ϹΕ`, Greek; mixed
Latin/Cyrillic) and an invented or un-aliased label (`NFO`, `FUTURES`). Each is
replayed live through stage 0 exactly as gate 1 replays the option probes, and the
families are *counted* separately for a reason: a paste the gate never classified as
an option must not be able to inflate the option rejection rate. The roster is
governed by `meta.refusal_probes_expected`, and its integrity check refuses an ASCII
option token filed here (that is an option probe, declaring a code stage 0 can never
return for it) or a `declared_instrument` that does not actually appear in the paste.

**Gate 1b grades the orchestrator's half (evals.md §3 #3).** A user invokes
`scripts/run_pipeline.py`, not stage 0, so one named probe **per refusal family**
is replayed through the whole 0 → 12 chain — an option paste against
`ERR_OPTIONS_PROHIBITED`, and a paste the token scan cannot name against
`ERR_INSTRUMENT_UNRECOGNIZED` — and the *stop* is graded as a whole for each: the
propagated exit 2, the family's own code on stderr, no downstream artifact, no
report rendered from a rejected trade, and a `run_record.json` naming stage 0
`failed` with that code while every later stage is `not_reached`. Two
deterministic replays, both named on the scorecard (the runner's stop does not
branch on *why* stage 0 refused; per-paste coverage is gates 1/1c's, and the
stage-0 seams replay every probe through the orchestrator) — because a runner
that logged the refusal and walked on would leave both rejection rates at
100.0%, and because an option stop is no evidence for a homoglyph one.

**Rosters may not shrink (evals.md §4).** Both probe rosters are graded sets
whose *size* and *members* are part of the claim, so `baseline_scorecard.json`
freezes both — `probe_rosters` (the counts) and `probe_roster_ids` (the
SHA-256 of each probe's paste *and declared metadata* — family, instrument,
origin, contract — keyed by `probe_id`) — exactly as
`rule_exercises` freezes the rule census (`check_probe_roster_floor`):
growing a roster is free, shrinking one, editing a frozen probe's paste or
its declared claims, or swapping a probe for a different one — cases a
count-only floor cannot see — is a
named shortfall and exit 1 until the probes come back or a deliberate
re-freeze (`scaffold_catalog.re_freeze_baseline`, which banks both floors)
accepts the new size.

**Provenance (evals.md §4.1).** Bytes on disk are not evidence by themselves:
a leftover or hand-edited report reads exactly like one a stage produced. So the
artifact-graded gates grade only bytes a **run record** proves — the ledger
written beside the artifacts by the replay (`run_record.json`,
`uvest-run-record/1`) or by a real chain run passed with `--run-record`: a `ran`
row must name the artifact and its SHA-256 must equal the bytes on disk. Anything
unproven is reproduced first (`ensure_reports` replays it into a sidecar and
compares — never overwriting the graded file), and if it still cannot be proven
the record is excluded **and the run fails** (`check_evidence_coverage`): the
artifact-graded gates are defined over the whole benchmark, so grading 29 of 31
records would claim more than was checked. A record that contradicts the disk (a
`ran` row whose bytes are gone or changed) fails the run too: a witness that
misstates its own output is worse than no witness.

Record payloads may be references (`$ref`, see `tests/evals/dataset_refs.py`) into
the proof root, which is where those bytes are frozen. A referenced artifact whose
provenance is `quarantined` is disclosed and excluded from grading — unverified
legacy bytes are never treated as evidence (E5, scenario-manifest-schema.md).

**Payload coverage.** Payloads are gate inputs too, so they are folded into the
same record vocabulary (`tests/evals/proof_root.py`: the manifest's `recorded`
declarations become `ran` rows carrying the frozen sha256, `waived` and
`quarantined` ones become disclosed `skipped` rows). Coverage spans **every
payload a graded gate reads**, not only gate 2's: the Zero-Hallucination differ
backs a report's numbers from the record's whole JSON, so any payload the record
carries can back a graded claim, and gate 2 reads named artifacts (`gate_payloads`
— a record's class breaker even when the record does not carry it).

A payload with no record behind it is either **unregistered** evidence — the
dataset holds the bytes inline (E1: a payload a graded gate reads must be a
recorded artifact, never an extracted subset typed into the dataset), no
declaration covers the artifact at all, or a declared recording no longer hashes
to the bytes on disk — or it is **registered debt**: the proof root declares the
artifact `waived` (E4, its engine does not exist yet) or `quarantined` (E5,
legacy bytes kept on disk for migration continuity). The two are not the same
failure and are not treated the same way. In both cases the gates abstain on the
payload rather than read bytes no run produced, but only the unregistered gap
fails the run (`check_payload_coverage`): typed bytes are a hole nobody declared,
while registered debt is disclosed and excluded exactly as `evals.md` §4.1
requires — the scorecard names it, no gate grades it, and it may never grow.
Emptying the roster of unregistered gaps is the condition for a green run.
Derived outcome claims are not payloads: a `$derive` field is recomputed from a
hash-frozen tape at load, so typed bytes cannot back it either.

For the same reason, gate 2 abstains on a carried breaker payload with no
recording behind it (`unbacked_payloads`) exactly as it abstains on a quarantined
artifact: bytes no run produced are not evidence, so the class breaker cannot be
graded from them.

Outcome claims may instead be `$derive` objects naming a pinned forward tape and a
frozen rule (`tests/evals/dataset_refs.py`); the runner recomputes them from the
tape's own bars at load and prints every derivation on the scorecard.

Exit codes:
    0 → no gate FAILED and dataset integrity OK (SKIP is allowed, per "tests that
        can't run yet stay SKIP" rather than being graded 0 on absent inputs),
    1 → at least one gate FAILED, class distribution ≠ expected counts, the
        option-probe roster is short of `meta.option_probes_expected` (or a probe
        cannot be replayed),        the refusal-probe roster is short of
        `meta.refusal_probes_expected` (or a refusal probe declares a contract
        stage 0 cannot return for it), a seeded stage-0 probe roster shrank
        below its frozen floor or carries a paste that is not the one the
        baseline froze (`probe_rosters` / `probe_roster_ids`), a stage-12 report could not be regenerated
        before grading, a run record
        contradicts the artifacts on disk, or a record's stage-12 evidence or one
        of the gate payloads it carries has an *unregistered* gap — typed inline
        in the dataset, undeclared by the proof root, or recorded bytes that no
        longer match (the graded set may never silently shrink; registered debt
        is disclosed instead, never fatal),
    2 → usage error / dataset missing / dataset unparseable.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BENCHMARK = REPO_ROOT / "tests" / "evals" / "golden_dataset.json"
# Gate 5's scope: the corpus the shipped booster was trained on, read out of the
# artifact's own disclosure (`provenance.corpus.row_window`) rather than a
# constant beside the gate — a retrain moves the scope without a gate edit.
CALIBRATION_FILE = REPO_ROOT / "models" / "lgbm_direction_v1.calibration.json"
# Cached import of scripts/4_ml_direction.py (see `load_stage4`): gate 5's
# miss diagnosis reuses the stage's own pool selection rather than a copy.
_STAGE4 = None

sys.path.insert(0, str(Path(__file__).resolve().parent / "evals"))

import dataset_refs  # noqa: E402  (sibling eval module, spliced onto the path above)
import proof_root  # noqa: E402  (the proof root, folded into run-record rows)
import provenance  # noqa: E402
import report_diff  # noqa: E402

# Class-level hard-disqualifier registry, per BRD §15.1's trade-class roster.
# Each class names exactly one BRD §10.3 breaker; projecting it is the runner's
# job, encoding it is the dataset's.
BREAKER_BY_CLASS: dict[str, str] = {
    "inverted_rr": "inverted_risk_reward",
    "pledge_hazard": "promoter_pledging_gt_25pct",
    "earnings_hazard": "earnings_within_7d",
    "tight_stop": "tighter_than_0_75x_atr",
    "regime_shock": "panic_regime",
}

# Which artifact each breaker reads — needed to know whether that breaker's
# evidence is even gradeable (a quarantined artifact carries no evidence).
BREAKER_ARTIFACT: dict[str, str] = {
    "inverted_risk_reward": "out_1_math.json",
    "promoter_pledging_gt_25pct": "out_6_fundamental.json",
    "earnings_within_7d": "out_6_fundamental.json",
    "tighter_than_0_75x_atr": "out_2_market.json",
    "panic_regime": "out_7_regime.json",
}

# evals.md §3 pass thresholds — frozen before any implementation code exists.
OPTION_REJ_MIN = 100.0
REFUSAL_MIN = 100.0
CHAIN_ABORT_MIN = 100.0
BREAKER_MIN = 100.0
HALLUCINATION_MIN = 100.0
BRIER_MAX = 0.18
COVERAGE_MIN = 80.0
MAE_REDUCTION_MIN = 75.0
HTML_VALIDITY_MIN = 100.0


BASELINE_FILE = REPO_ROOT / "tests" / "proof" / "baseline_scorecard.json"


def load_baseline(path: Path | None = None) -> dict:
    """The frozen regression reference: dataset digests, the E5 meter, and the
    rule-coverage census (`check_rule_coverage`'s floor). Absent or unreadable is
    an empty reference rather than a crash — the validator is what requires it.
    """
    try:
        return json.loads((path or BASELINE_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def load_benchmark(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"❌ Benchmark dataset not found: {path}", file=sys.stderr)
        sys.exit(2)
    except json.JSONDecodeError as exc:
        print(f"❌ Benchmark dataset is not valid JSON: {path}\n   {exc}", file=sys.stderr)
        sys.exit(2)


class Gate:
    """Mutable pass/fail/skip accumulator with frozen-threshold context."""

    def __init__(self, name: str, threshold: float, higher_is_pass: bool,
                 fmt: str = "{:.1f}%"):
        self.name = name
        self.threshold = threshold
        self.higher_is_pass = higher_is_pass
        self.fmt = fmt
        self.value: float | None = None
        self.skipped = False
        self.detail = ""
        # A disclosure that has to survive a PASS. Gate 2 sets it to the breaker
        # classes it actually graded, because a percentage over a shrunken graded
        # set reads exactly like a percentage over the whole benchmark: which
        # classes were graded (and which it had to abstain on) is stated *next to*
        # the number, not left to be inferred from it. Printed for every status.
        self.coverage = ""
        # Gate 2's rule-exercise census: per class, how many distinct rule
        # exercises the graded records stood for, and which exercises those were
        # (`check_rule_coverage` compares the counts with the frozen floor).
        self.census: dict[str, int] = {}
        self.exercises: dict[str, frozenset] = {}

    def evaluate(self) -> tuple[str, str]:
        """Returns ([PASS]|[SKIP]|[FAIL], display_value)."""
        if self.skipped or self.value is None:
            return "[SKIP]", "inputs incomplete — partial dataset"
        ok = (self.value >= self.threshold) if self.higher_is_pass \
            else (self.value <= self.threshold)
        threshold_str = (">= " if self.higher_is_pass else "<= ") + self.fmt.format(self.threshold)
        return (
            "[PASS]" if ok else "[FAIL]",
            f"{self.fmt.format(self.value)}  ({threshold_str})",
        )


def baseline_gate_regressions(gates: list[Gate],
                              baseline: dict) -> tuple[list[str], list[str]]:
    """(regressions, improvements) of this run's gates against the frozen bank.

    The baseline's `gates` block is the *deliberate publication* of the scorecard
    as it stands (`run_evals.py --re-freeze-gates`): verdict and value per gate.
    The pre-commit hook exits through this instead of the absolute verdict,
    because gates 4/5 are red **by policy** (published, not tuned) and an
    absolute exit-1 made every commit need `--no-verify` — the hook then caught
    nothing at all.

    Blocks (a regression): a PASS that stops passing, grading that disappears
    (any verdict → SKIP), a banked red that gets *worse*, and a red nobody ever
    banked — a published red must be an act, never an accident. A SKIP → FAIL is
    also a regression: it is a newly graded red that must be banked explicitly
    (stage 8's arrival is the case). Never a block: a banked red that sits still
    (the policy itself), and any improvement — which returns as a re-bank hint,
    because a stale bank is how a later regression goes unnoticed.
    """
    banked = baseline.get("gates") or {}
    regressions: list[str] = []
    improvements: list[str] = []
    for gate in gates:
        verdict = gate.evaluate()[0].strip("[]")  # PASS | FAIL | SKIP
        frozen = banked.get(gate.name)
        if frozen is None:
            if verdict == "FAIL":
                regressions.append(
                    f"{gate.name}: FAIL at {gate.fmt.format(gate.value)} but "
                    "never banked — a red is published deliberately "
                    "(run_evals.py --re-freeze-gates)")
            continue
        was = frozen.get("verdict")
        was_value = frozen.get("value")
        if verdict == was:
            # Same verdict both sides: PASS→PASS says nothing either way; a
            # banked red moving while staying red is the only signal here.
            if verdict == "FAIL" and was_value is not None \
                    and gate.value is not None:
                worse = (gate.value < was_value) if gate.higher_is_pass \
                    else (gate.value > was_value)
                better = (gate.value > was_value) if gate.higher_is_pass \
                    else (gate.value < was_value)
                if worse:
                    regressions.append(
                        f"{gate.name}: {gate.fmt.format(was_value)} → "
                        f"{gate.fmt.format(gate.value)} — worse than the "
                        "banked red")
                elif better:
                    improvements.append(
                        f"{gate.name}: {gate.fmt.format(was_value)} → "
                        f"{gate.fmt.format(gate.value)} — improved")
            continue
        if verdict == "FAIL":
            if was == "SKIP":
                regressions.append(
                    f"{gate.name}: SKIP → FAIL — a newly graded red must be "
                    "banked (run_evals.py --re-freeze-gates)")
            else:
                regressions.append(f"{gate.name}: {was} → FAIL")
        elif verdict == "SKIP":
            regressions.append(
                f"{gate.name}: {was} → SKIP — grading lost, and a smaller "
                "graded set hides behind the same percentage")
        else:  # PASS
            improvements.append(f"{gate.name}: {was} → PASS — re-bank it")
    return regressions, improvements


def bank_gate_values(gates: list[Gate], path: Path | None = None) -> dict:
    """Write this run's verdict+value per gate into the baseline's `gates` block.

    The deliberate publication act: afterwards `baseline_gate_regressions`
    tolerates exactly these reds and fails on any move for the worse. Every other
    key (hashs, census, rosters — owned by `scaffold_catalog.re_freeze_baseline`
    and the quarantine tools) is read back and preserved, and the bytes follow
    the proof-root write convention (`scaffold_catalog.write_text_utf8`:
    indent-2 dumps, CRLF on disk), so the two writers of one file cannot churn
    each other's lines. The file is required: a clone without a baseline is a
    broken clone, and this command invents nothing.
    """
    target = path or BASELINE_FILE
    baseline = json.loads(target.read_text(encoding="utf-8"))
    baseline["gates"] = {
        gate.name: {"verdict": gate.evaluate()[0].strip("[]"),
                    "value": gate.value}
        for gate in gates
    }
    text = json.dumps(baseline, indent=2, ensure_ascii=False) + "\n"
    # Exactly scaffold_catalog.write_json + write_text_utf8's bytes: LF-normalize,
    # then CRLF (proof-root files are CRLF on disk), UTF-8, trailing newline.
    target.write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n")
                       .encode("utf-8"))
    return baseline


# --------------------------------------------------------------------------
# Rule arms — what a breaker *checks*, one arm per comparison
# --------------------------------------------------------------------------
# A breaker is not one comparison but the set of comparisons BRD §10.3 makes it:
# the R:R breaker trips on the recommended-band midpoint (`raw_rr`) *or* at the
# executable price (`live_rr`), the earnings hazard is a Futures rule, and so on.
# Each arm names one comparison, the artifact it reads, the field whose value
# decides it, the granularity that comparison speaks in, and (when it differs)
# the field carrying the *measurement* the rule was exercised at — `is_tight_sl`
# is a boolean, but the stop multiple behind it is `sl_atr_ratio`.
#
# The granule is the grain the business rule is stated in (percent to 0.1, R:R
# to 2 decimals, ATR multiple to 2 decimals, sessions whole), and it is what makes
# a *rule exercise* comparable: two records at 0.90006 and 0.90011 exercise the
# same "R:R ≈ 0.90, below 1.0" rule, and counting them as two would be counting
# records again.
TIGHT_STOP_ARM = ("is_tight_sl_true", "out_2_market.json", "is_tight_sl", 2,
                  "sl_atr_ratio")
RULE_ARMS: dict[str, tuple[tuple[str, str, object, int, str | None], ...]] = {
    "inverted_risk_reward": (
        ("raw_rr_below_1", "out_1_math.json", "raw_rr", 2, None),
        # live_rr carries the trap-encoding in the golden dataset (the
        # executable reality); older records predate the pair-splitting.
        ("live_rr_below_1", "out_1_math.json", ("live_rr", "raw_rr"), 2, None),
    ),
    "promoter_pledging_gt_25pct": (
        ("promoter_pledge_pct_above_25", "out_6_fundamental.json",
         "promoter_pledge_pct", 1, None),
    ),
    "earnings_within_7d": (
        ("days_to_earnings_below_7_futures", "out_6_fundamental.json",
         "days_to_earnings", 0, None),
    ),
    "tighter_than_0_75x_atr": (TIGHT_STOP_ARM,),
    # BRD §10.3 #6: a *conjunction* (VIX > 24 AND breadth < 30), graded arm by
    # arm so the census sees which side each record exercised — combined by
    # BREAKER_COMBINE, never OR-ed like the R:R arms.
    "panic_regime": (
        ("panic_vix_above_24", "out_7_regime.json", "vix_value", 1, None),
        ("panic_breadth_below_30", "out_7_regime.json",
         "market_breadth_pct", 1, None),
    ),
}
# How a breaker's arms combine into the class verdict: "any" (the R:R breaker
# trips on either price) or "all" (the panic regime requires both arms — a
# breadth print below 30 with a calm VIX is Risk-Off, not the breaker).
BREAKER_COMBINE: dict[str, str] = {
    "panic_regime": "all",
}
# How a clear arm's exercise is named: the same rule as the trap's, on the other
# side of its threshold (`raw_rr_below_1` → `raw_rr_below_1:clear`). A clean
# record's exercise is not one constant "nothing leaked" but the boundary check
# itself, per arm, at the measurement that stayed clear — otherwise a class of
# fifteen rows at one shape reads exactly like fifteen rows spread over fifteen.
CLEAR_ARM_SUFFIX = ":clear"


def _field(trade: dict, artifact: str, field: object):
    """The value an arm reads, tolerating a fallback key (`live_rr` → `raw_rr`)."""
    outputs = (trade.get("script_outputs") or {}).get(artifact) or {}
    keys = field if isinstance(field, tuple) else (field,)
    for key in keys:
        if key in outputs:
            return outputs[key]
    return None


def _trips(breaker: str, field: str, value) -> bool:
    """The arm's own comparison — the single source of the breaker thresholds."""
    if breaker == "inverted_risk_reward":
        return isinstance(value, (int, float)) and not isinstance(value, bool) \
            and value < 1.0
    if breaker == "promoter_pledging_gt_25pct":
        return isinstance(value, (int, float)) and not isinstance(value, bool) \
            and value > 25.0
    if breaker == "earnings_within_7d":
        return isinstance(value, (int, float)) and not isinstance(value, bool) \
            and 0 <= value < 7
    if breaker == "tighter_than_0_75x_atr":
        return value is True
    if breaker == "panic_regime":
        # The conjunction's two arms speak in different thresholds, so this
        # breaker dispatches on which field the arm read (BRD §10.3 #6).
        numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
        if field == "vix_value":
            return numeric and value > 24.0
        if field == "market_breadth_pct":
            return numeric and value < 30.0
        return False
    return False


def breaker_arms(trade: dict, breaker: str,
                 unbacked: frozenset[tuple[str, str]] = frozenset(),
                 ) -> dict[str, bool] | None:
    """Every arm of `breaker`, verdict by verdict — or `None` when ungradeable.

    `None` means *ungradeable*, never *clean*: the artifact is registered
    quarantined, or the record carries the payload inline with no recording
    behind it (`unbacked_payloads` — E1). Either way the breaker has no evidence
    to read, so it contributes nothing to a gate.
    """
    artifact = BREAKER_ARTIFACT[breaker]
    if artifact in dataset_refs.quarantined_in(trade):
        return None
    if (str(trade.get("trade_id")), artifact) in unbacked:
        return None
    return {arm: _trips(breaker, field, _field(trade, source, field))
            for arm, source, field, _granule, _measure in RULE_ARMS[breaker]}


def class_breaker_observed(trade: dict, breaker: str | None = None,
                           unbacked: frozenset[tuple[str, str]] = frozenset(),
                           ) -> bool | None:
    """True iff the breaker corresponding to the trade's *class* is present
    with the threshold-level value — i.e., encoding projected the right value.

    The class verdict combines the breaker's arms per `BREAKER_COMBINE` —
    OR-ed by default (BRD §10.2 #1 "R:R" trips on either price), AND-ed for a
    conjunction breaker like `panic_regime` — and `None` (ungradeable)
    propagates unchanged.
    """
    cls = trade.get("trade_class")
    breaker = breaker or BREAKER_BY_CLASS[cls]
    arms = breaker_arms(trade, breaker, unbacked)
    if arms is None:
        return None
    if BREAKER_COMBINE.get(breaker, "any") == "all":
        return all(arms.values())
    return any(arms.values())


def _granulated(value, granule: int):
    """A measurement at the granule the rule speaks in; booleans are their own value."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    return round(float(value), granule) if granule else int(value)


def rule_exercises(trade_class: str, trade: dict,
                   unbacked: frozenset[tuple[str, str]] = frozenset(),
                   ) -> frozenset[tuple[str, object]]:
    """The *rule exercises* a graded record stands for.

    One exercise per arm, identified as `(arm, value)` with the value rounded to
    the granule that comparison speaks in. Records are not exercises: ten rows
    admitting themselves to the same "R:R ≈ 0.90 below 1.0" rule are one exercise
    of it, ten times over — and the census below is what says so out loud instead
    of letting the record count imply ten rules were probed.

    A trap row exercises the arm that tripped. A clean row exercises the same
    arms from the other side — the boundary check that stayed clear, at the value
    that cleared it (`raw_rr_below_1:clear` at 2.2) — because "nothing leaked" is
    not one rule but one rule per arm, and how far under each threshold the clean
    rows sit is the only thing a class of clean rows can be measured on. Rows at
    one shape probe one point under the thresholds; the same rows spread over
    fourteen shapes probe fourteen, and the census is what shows the difference.
    """
    if trade_class == "clean_bullish":
        found: set[tuple[str, object]] = set()
        for cls in BREAKER_BY_CLASS:
            breaker = BREAKER_BY_CLASS[cls]
            arms = breaker_arms(trade, breaker, unbacked)
            if arms is None:
                continue
            conjunction = BREAKER_COMBINE.get(breaker, "any") == "all"
            for arm, source, field, granule, measure in RULE_ARMS[breaker]:
                value = _field(trade, source, measure or field)
                if value is None:
                    continue
                # A conjunction fires (or clears) as one unit: the pair is the
                # rule, so every arm is keyed by the breaker's own verdict.
                fired = all(arms.values()) if conjunction else arms.get(arm)
                key = arm if fired else f"{arm}{CLEAR_ARM_SUFFIX}"
                found.add((key, _granulated(value, granule)))
        return frozenset(found)
    breaker = BREAKER_BY_CLASS[trade_class]
    arms = breaker_arms(trade, breaker, unbacked)
    if arms is None:
        return frozenset()
    if BREAKER_COMBINE.get(breaker, "any") == "all":
        # Same conjunction rule at the trap side: when the pair fired, every
        # arm is a trap exercise at its own value; when it did not, every arm
        # is the boundary that held (`:clear`) — a breadth print under 30 with
        # a calm VIX exercised the pair, it just did not trip it.
        fired = all(arms.values())
        conjuncted = set()
        for arm, source, field, granule, measure in RULE_ARMS[breaker]:
            value = _field(trade, source, measure or field)
            if value is None:
                continue
            key = arm if fired else f"{arm}{CLEAR_ARM_SUFFIX}"
            conjuncted.add((key, _granulated(value, granule)))
        return frozenset(conjuncted)
    found = set()
    for arm, source, field, granule, measure in RULE_ARMS[breaker]:
        if not arms.get(arm):
            continue
        value = _field(trade, source, measure or field)
        if value is None:
            continue
        found.add((arm, _granulated(value, granule)))
    return frozenset(found)


def dataset_rule_census(path: Path | None = None) -> dict[str, int]:
    """The rule-exercise census of a committed dataset, as the gate computes it.

    The baseline's floor is banked from this (`scaffold_catalog.re_freeze_baseline`),
    so the number a run is measured against and the number a run computes come
    from one walk with one set of grading rules.
    """
    raw = load_benchmark(path or DEFAULT_BENCHMARK)
    data, _refs, _derives = dataset_refs.materialize(raw)
    trades = data.get("trades", [])
    unbacked = unbacked_payloads(trades, dataset_payload_targets(raw))
    census = graded_trade_classes(trades, unbacked)
    return {cls: len(keys) for cls, keys in sorted(census.items())}


def graded_trade_classes(trades: list[dict],
                         unbacked: frozenset[tuple[str, str]] = frozenset(),
                         ) -> dict[str, frozenset[tuple[str, object]]]:
    """Per class, the distinct rule exercises its gradeable records stand for.

    The same walk the gate grades with, so the census and the scorecard cannot
    disagree about which records contributed: a record whose breaker is
    ungradeable is not counted here either.
    """
    census: dict[str, frozenset[tuple[str, object]]] = {}
    for trade in trades:
        cls = str(trade.get("trade_class"))
        if cls == "clean_bullish":
            observed = [class_breaker_observed({**trade, "trade_class": c},
                                              BREAKER_BY_CLASS[c], unbacked)
                        for c in BREAKER_BY_CLASS]
            if all(v is None for v in observed):
                continue
        elif cls not in BREAKER_BY_CLASS:
            continue
        elif class_breaker_observed(trade, unbacked=unbacked) is None:
            continue
        census[cls] = census.get(cls, frozenset()) | rule_exercises(cls, trade, unbacked)
    return census


def gate_hard_breakers(trades: list[dict],
                       unbacked: frozenset[tuple[str, str]] = frozenset(),
                       class_quota: dict[str, int] | None = None,
                       ) -> Gate:
    """Gate 2: circuit-breaker accuracy across the benchmark (evals.md §3 #2).

    Normally the decision engine's outputs (out_9_decision.json) would be replayed;
    until stage 9 exists, the runner grades the *dataset's* breaker discipline:
    traps must carry a threshold-level breaker value and clean trades must not.

    The value is an average over the records it could grade, so a class whose
    records all abstained contributes nothing to it — a smaller graded set that
    reads exactly like the whole benchmark. That is why the gate also reports its
    **class coverage** (`gate.coverage`, printed whatever the verdict): every
    breaker class is named with how many of its records were graded, how many
    abstained, and — from the dataset's frozen roster (`class_quota`, i.e.
    `meta.class_counts_expected`) — when the class itself is short of its quota.
    Coverage discloses; it does not change the arithmetic, because an abstention
    with a recorded reason is registered debt by design (`check_payload_coverage`
    already fails the run on the ones nobody declared).
    """
    g = Gate("Hard Circuit Breaker Accuracy", BREAKER_MIN, higher_is_pass=True)
    graded: list[float] = []
    per_trade: list[str] = []
    ungraded: list[str] = []
    violations = 0
    # Per-class bookkeeping: records seen, records graded, records abstained on,
    # and — the unit that is *not* a record — the rule exercises they stand for.
    present: dict[str, int] = {}
    graded_by_class: dict[str, int] = {}
    abstained_by_class: dict[str, int] = {}
    exercises: dict[str, set[tuple[str, object]]] = {}
    repeats_by_class: dict[str, int] = {}

    def credit(cls: str, trade: dict) -> None:
        """Count one graded record *and* the distinct rule exercise it stands for.

        A record that adds no exercise the class does not already stand for is
        counted as a *repeat* — the literal answer to "which records taught the
        class nothing new", which is not `graded - distinct` once a record can
        stand for several arms at once.
        """
        graded_by_class[cls] = graded_by_class.get(cls, 0) + 1
        keys = rule_exercises(cls, trade, unbacked)
        seen = exercises.setdefault(cls, set())
        if keys and keys <= seen:
            repeats_by_class[cls] = repeats_by_class.get(cls, 0) + 1
        seen.update(keys)

    def count(book: dict[str, int], cls: str) -> None:
        book[cls] = book.get(cls, 0) + 1

    for t in trades:
        cls = t.get("trade_class")
        if cls == "clean_bullish":
            count(present, cls)
            # Correct behaviour here: NO breaker observed. A breaker whose own
            # evidence is quarantined abstains rather than counting either way.
            verdicts = [class_breaker_observed({**t, "trade_class": c}, BREAKER_BY_CLASS[c],
                                               unbacked)
                        for c in BREAKER_BY_CLASS]
            observed = [v for v in verdicts if v is not None]
            if not observed:
                count(abstained_by_class, cls)
                ungraded.append(f"{t['trade_id']}: no breaker payload with "
                                "evidence behind it")
                continue
            leaked = any(observed)
            graded.append(0.0 if leaked else 1.0)
            credit(cls, t)
            per_trade.append(f"{t['trade_id']}: {'LEAK' if leaked else 'ok'}")
            violations += 1 if leaked else 0
            continue
        count(present, cls)
        if cls not in BREAKER_BY_CLASS:
            continue  # classes without a defined breaker are out of scope
        verdict = class_breaker_observed(t, unbacked=unbacked)
        if verdict is None:
            count(abstained_by_class, cls)
            artifact = BREAKER_ARTIFACT[BREAKER_BY_CLASS[cls]]
            ungraded.append(
                f"{t['trade_id']}: {artifact} "
                + ("quarantined" if artifact in dataset_refs.quarantined_in(t)
                   else "carried inline — no recorded artifact backs it"))
            continue
        if not verdict:
            per_trade.append(f"{t['trade_id']}: breaker value not encoded")
            violations += 1
            graded.append(0.0)
            credit(cls, t)
            continue
        graded.append(1.0)
        credit(cls, t)

    g.census = {cls: len(keys) for cls, keys in exercises.items()}
    g.exercises = {cls: frozenset(keys) for cls, keys in exercises.items()}
    g.coverage = class_coverage(present, graded_by_class, abstained_by_class,
                                class_quota, g.census, g.exercises, repeats_by_class)
    g.skipped = not graded
    if g.skipped:
        # Name the obstacle even when every record is ungraded: "no encodable
        # breaker yet" alone would hide whether the payloads are quarantined,
        # typed inline, or simply absent.
        g.detail = ("no trades with an encodable breaker yet"
                    + (f"; not graded (payload is not evidence): "
                       f"{'; '.join(ungraded)}" if ungraded else ""))
    else:
        g.value = 100.0 * (sum(graded) / len(graded))
        g.detail = (f"{len(graded)} graded, {violations} violation(s)"
                    + (f"; {'; '.join(per_trade)}" if per_trade else "")
                    + (f"; not graded (payload is not evidence): "
                       f"{'; '.join(ungraded)}" if ungraded else ""))
    return g


def class_coverage(present: dict[str, int], graded_by_class: dict[str, int],
                   abstained_by_class: dict[str, int],
                   class_quota: dict[str, int] | None = None,
                   census: dict[str, int] | None = None,
                   exercises: dict[str, frozenset] | None = None,
                   repeats_by_class: dict[str, int] | None = None) -> str:
    """Gate 2's roster disclosure: what it graded, per class, record by record.

    One clause per class, in the dataset's own roster order (`class_quota`, i.e.
    `meta.class_counts_expected`) so the line reads like the quota it is measured
    against, then any class the dataset carries that the roster omits. A class
    with records but no graded record reads `0/N graded, N abstained` — that
    clause is the whole point of the line, since `N=0` graded is what a 100.0%
    average over the other classes cannot show. Classes with no breaker in the
    frozen registry are named too, so a class nobody grades is not invisible
    either.

    Every graded class also carries its **rule-exercise census**: `K distinct
    rule exercise(s)`, and how many records repeated an exercise instead of
    probing a new one. A record count says how many rows exist; this says how
    many distinct rules those rows actually probed, at the granule each rule
    speaks in (`rule_exercises`).
    """
    names = list(class_quota or {})
    names += [c for c in ("clean_bullish", *BREAKER_BY_CLASS) if c not in names]
    names += sorted(c for c in present if c not in names)
    parts: list[str] = []
    for cls in names:
        total = present.get(cls, 0)
        quota = (class_quota or {}).get(cls)
        if cls in BREAKER_BY_CLASS or cls == "clean_bullish":
            graded = graded_by_class.get(cls, 0)
            part = f"{cls} {graded}/{total} graded"
            distinct = (census or {}).get(cls)
            if graded:
                part += f", {distinct} distinct rule exercise(s)"
                repeats = _repeat_note(cls, graded, repeats_by_class)
                if repeats:
                    part += f" ({repeats})"
            if quota is not None and total != quota:
                part += f" (quota {quota})"
            abstained = abstained_by_class.get(cls, 0)
            if abstained:
                part += f", {abstained} abstained"
        else:
            part = (f"{cls} no registered breaker ({total} record(s)"
                    + (f", quota {quota})" if quota is not None else ")"))
        parts.append(part)
    return "class coverage: " + "; ".join(parts) if parts else ""


def _repeat_note(cls: str, graded: int,
                 repeats_by_class: dict[str, int] | None) -> str:
    """Say how many records added no rule the class did not already stand for.

    Counted as the runner walks the class (`credit`), not as `graded - distinct`:
    a record can stand for several arms at once, so subtracting the census from
    the record count stops meaning "rows that repeated one" as soon as a class
    carries more arms than rows. `cls` is unused by the arithmetic and kept for
    the caller's clarity.
    """
    repeats = (repeats_by_class or {}).get(cls, 0)
    if repeats <= 0:
        return ""
    return f"{repeats} of {graded} repeat an exercise"


def check_rule_coverage(census: dict[str, int],
                        baseline: dict | None,
                        ) -> tuple[bool, str, list[str]]:
    """Rule coverage may not shrink: the baseline freezes the measured census.

    `baseline_scorecard.json` is the repo's frozen regression reference, and this
    is the coverage half of it: per class, how many distinct rule exercises the
    graded records stood for when it was last deliberately re-frozen. Falling
    below the floor is a hard failure — a class can lose the record that carried
    its only exercise at, say, 54.2% and still look graded — and it is named by
    class and by delta, so the fix is either more distinct evidence or an
    explicit re-freeze (`scaffold_catalog.re_freeze_baseline`, which banks the
    census itself). Growing past the floor is free: that is the point.

    A class absent from the floor (added since the last re-freeze) adds a key;
    a class absent from the dataset while the floor holds one is a shrink to 0,
    which fails. Returns `(ok, reason, shortfalls)`.
    """
    frozen = (baseline or {}).get("rule_exercises")
    if not isinstance(frozen, dict):
        return True, "no frozen rule-coverage census yet; run skipped", []
    shortfalls = [
        f"{cls}: {census.get(cls, 0)} distinct rule exercise(s) < frozen {floor}"
        for cls, floor in sorted(frozen.items())
        if isinstance(floor, int) and census.get(cls, 0) < floor
    ]
    total = sum(census.values())
    floor_total = sum(f for f in frozen.values() if isinstance(f, int))
    reason = (f"{total} distinct rule exercise(s) across the graded classes; "
              f"frozen floor {floor_total}")
    if shortfalls:
        return False, reason + f"; {len(shortfalls)} class(es) below the floor", shortfalls
    return True, reason, []


def probe_identity(probe: dict) -> str:
    """The frozen identity of one probe: the SHA-256 of everything it declares
    except its own id.

    A probe's graded surface is its paste *and* the claims the roster checks
    enforce on it — `raw_text`, `family`, `declared_instrument`, `origin` and
    the `expected` rejection contract — so all of it is hashed, canonically
    (`sort_keys`, tight separators, `ensure_ascii=False`). The key itself is
    excluded: a renamed id is already caught as a frozen member gone missing,
    and hashing it too would only report one change twice. Hashing the whole
    remainder rather than a listed field set also means a *new* declared field
    cannot slip in un-frozen.

    A count is not an identity either: swapping one probe for another, or
    relabelling a family or contract, preserves a count — which is why the
    baseline banks this hash per `probe_id` (`probe_roster_ids`) beside the
    roster size (`probe_rosters`).
    """
    declared = {key: value for key, value in probe.items() if key != "probe_id"}
    canonical = json.dumps(declared, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def probe_roster_ids(rosters: dict[str, list[dict]]) -> dict[str, dict[str, str]]:
    """`{roster: {probe_id: identity}}` for the seeded rosters, one walk."""
    return {name: {str(probe.get("probe_id") or "<no probe_id>"): probe_identity(probe)
                   for probe in probes or []}
            for name, probes in rosters.items()}


def _duplicated_probe_ids(probes: list[dict]) -> list[str]:
    """Probe ids appearing more than once in a roster: two pastes under one id
    cannot both be the member the baseline froze."""
    seen: set[str] = set()
    duplicates: list[str] = []
    for probe in probes:
        probe_id = str(probe.get("probe_id") or "<no probe_id>")
        if probe_id in seen and probe_id not in duplicates:
            duplicates.append(probe_id)
        seen.add(probe_id)
    return sorted(duplicates)


def check_probe_roster_floor(rosters: dict[str, list[dict]],
                             baseline: dict | None,
                             ) -> tuple[bool, str, list[str]]:
    """The probe rosters may not shrink, swap or mutate: the baseline freezes
    their sizes *and* their members.

    The twin of `check_rule_coverage` for the graded sets outside `trades`,
    one step stricter — a count is not an identity. `baseline_scorecard.json`
    freezes both (`probe_rosters`, how many; `probe_roster_ids`, the SHA-256
    of each probe's paste *and declared metadata* — family, instrument,
    origin, contract — keyed by `probe_id`), so:

      * a deleted probe is a size shortfall, named by roster and delta — a
        smaller graded set can never keep the same green percentage;
      * a probe swapped for a *different* member is an identity shortfall even
        when the count holds, which is the case a size-only floor cannot see:
        replacing a homoglyph with a plain `CE` would otherwise sail through;
      * an edited paste, or a relabelled family/origin/contract, is the same
        identity shortfall at the same id — the roster checks validate a
        contract's *shape*, so only the hash notices that a frozen
        `ERR_INSTRUMENT_UNRECOGNIZED` claim quietly became something else;
      * a duplicated `probe_id` is named, since two pastes cannot both be the
        frozen member;
      * and adding a probe — or a whole roster — stays free until a deliberate
        re-freeze banks it (`scaffold_catalog.re_freeze_baseline` writes both
        floors), exactly as growing past the rule census is.

    Returns `(ok, reason, shortfalls)`.
    """
    frozen = (baseline or {}).get("probe_rosters")
    if not isinstance(frozen, dict):
        return True, "no frozen probe-roster floor yet; run skipped", []
    counts = {name: len(probes or []) for name, probes in rosters.items()}
    shortfalls = [
        f"{name}: {counts.get(name, 0)} seeded probe(s) < frozen {floor}"
        for name, floor in sorted(frozen.items())
        if isinstance(floor, int) and counts.get(name, 0) < floor
    ]
    frozen_ids = (baseline or {}).get("probe_roster_ids")
    members_frozen = (sum(len(m) for m in frozen_ids.values()
                          if isinstance(m, dict))
                      if isinstance(frozen_ids, dict) else 0)
    if isinstance(frozen_ids, dict):
        seeded_ids = probe_roster_ids(rosters)
        for name, members in sorted(frozen_ids.items()):
            if not isinstance(members, dict):
                continue
            for probe_id in _duplicated_probe_ids(rosters.get(name) or []):
                shortfalls.append(f"{name}/{probe_id}: duplicated probe_id — "
                                  "two pastes cannot both be the frozen member")
            present = seeded_ids.get(name) or {}
            for probe_id, identity in sorted(members.items()):
                if probe_id not in present:
                    shortfalls.append(f"{name}/{probe_id}: frozen probe is not "
                                      "seeded — deleting one, or replacing it "
                                      "with a different paste, is a deliberate "
                                      "re-freeze")
                elif present[probe_id] != identity:
                    shortfalls.append(f"{name}/{probe_id}: probe edited since "
                                      "the last re-freeze — paste or declared "
                                      "metadata changed, identity "
                                      f"{present[probe_id][:12]}… ≠ frozen "
                                      f"{identity[:12]}…")
    total = sum(counts.values())
    floor_total = sum(f for f in frozen.values() if isinstance(f, int))
    reason = (f"{total} seeded stage-0 probe(s) across {len(frozen)} roster(s); "
              f"frozen floor {floor_total}"
              + (f", {members_frozen} member hash(es) frozen"
                 if members_frozen else ""))
    if shortfalls:
        return False, reason + f"; {len(shortfalls)} floor violation(s)", shortfalls
    return True, reason, []


def dataset_probe_roster_counts(path: Path | None = None) -> dict[str, int]:
    """How many probes each roster holds in a committed dataset, the way the
    floor measures it (`scaffold_catalog.re_freeze_baseline` banks this).

    Counted from the *raw* file: the rosters are authored pastes, never `$ref`
    payloads, so materialization cannot change their size — and reading the
    same file the gates read keeps the banked number and the measured number on
    one walk.
    """
    raw = load_benchmark(path or DEFAULT_BENCHMARK)
    return {name: len(raw.get(name) or []) for name in PROBE_ROSTERS}


def dataset_probe_roster_ids(path: Path | None = None) -> dict[str, dict[str, str]]:
    """Each roster's members the way the identity floor measures them
    (`{roster: {probe_id: identity}}`, banked by `scaffold_catalog.re_freeze_baseline` as `probe_roster_ids`).

    Read from the *raw* file for the same reason `dataset_probe_roster_counts`
    is: the rosters are authored pastes, never `$ref` payloads, so
    materialization cannot change what a probe *is* — and reading the same
    file the gates read keeps the banked identities and the measured ones on
    one walk.
    """
    raw = load_benchmark(path or DEFAULT_BENCHMARK)
    return probe_roster_ids({name: raw.get(name) or [] for name in PROBE_ROSTERS})


# --------------------------------------------------------------------------
# Gate 1: Option Rejection Invariance (evals.md §3 #3)
# --------------------------------------------------------------------------

# BRD ADR-001 / TICK-001 §3 — the option token vocabulary that is fatal at
# ingestion. Deliberately NOT read from stage 0 at import: the gate grades a
# *live* run of that script, and reading its constants here would let a widened
# vocabulary (or a deleted gate) look like a passing replay. Equality with
# `scripts/0_ingest_reco.py:OPTIONS_TYPES` is asserted by the gate's own seam
# (`tests/test_run_evals.TestOptionRejectionGate`), so the two can only drift
# loudly.
OPTION_TOKENS = ("CE", "PE", "OPT", "OPTION", "CALL", "PUT")

# The frozen rejection contract: exit 2 *and* the family's code on stderr *and*
# no canonical payload. Two of the three is not a rejection — argparse itself
# exits 2 on a usage error, and a parser that rejects while still emitting a
# canonical trade has not rejected it. The exit code is shared by every
# ingestion refusal; the *code string* is what names which gate fired.
PROBE_EXIT = 2
OPTION_PROBE_EXIT = PROBE_EXIT
REFUSAL_PROBE_EXIT = PROBE_EXIT
OPTION_PROBE_ERROR = "ERR_OPTIONS_PROHIBITED"
REFUSAL_PROBE_ERROR = "ERR_INSTRUMENT_UNRECOGNIZED"

# The two shapes the option-token scan cannot name, so the closed instrument
# domain (ADR-001: "only equities and futures are permitted") is what refuses
# them: an option written with look-alike letters, and a label that is not
# CASH/FUT at all.
REFUSAL_PROBE_FAMILIES = ("homoglyph_option", "unrecognized_instrument")

# Which rosters the baseline's frozen probe floor (`probe_rosters`) governs.
# Both are the only graded sets outside `trades`, both are authored stage-0
# input, and neither may enter the class census or `distribution_verified` — a
# probe is a parser test case, never a trade.
PROBE_ROSTERS = ("option_probes", "refusal_probes")
REFUSAL_FAMILY_LABEL = {
    "homoglyph_option": "homoglyph option token(s)",
    "unrecognized_instrument": "unrecognized instrument label(s)",
}

# Every probe must declare itself authored. A probe is *input* to stage 0, so it
# cannot be a sourced recommendation or a measured hazard; requiring the field
# keeps that disclosure a property of the roster rather than of a note.
OPTION_PROBE_ORIGIN = "authored_test_input"


def replay_ingestion_probe(probe: dict, error_code: str) -> dict:
    """Replay one probe's raw paste through stage 0's real CLI contract.

    The stage is *invoked*, never imported (`python scripts/0_ingest_reco.py
    --input <paste>`) — the contract stage 0's own docstring states — so the
    gate grades the entry point a human paste would actually hit, and the
    refusal it measures is this commit's parser's rather than a stored verdict's.
    `error_code` is the refusal the family must produce: the option gate and the
    closed-domain gate share the exit code and the no-payload rule, and are
    told apart by the code, which is exactly why the caller names it.
    """
    script = REPO_ROOT / "scripts" / "0_ingest_reco.py"
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                     encoding="utf-8") as handle:
        handle.write(str(probe.get("raw_text") or ""))
        input_path = Path(handle.name)
    try:
        proc = subprocess.run(
            [sys.executable, str(script), "--input", str(input_path)],
            capture_output=True, check=False, cwd=REPO_ROOT,
            text=True, encoding="utf-8", errors="replace",
        )
    finally:
        input_path.unlink(missing_ok=True)
    stdout_text, stderr_text = proc.stdout or "", proc.stderr or ""
    return {
        "exit_code": proc.returncode,
        "stderr": stderr_text.strip(),
        "emitted_payload": bool(stdout_text.strip()),
        "rejected": (proc.returncode == PROBE_EXIT
                     and error_code in stderr_text
                     and not stdout_text.strip()),
    }


def replay_option_probe(probe: dict) -> dict:
    """Gate 1's replay: the paste must die as an option."""
    return replay_ingestion_probe(probe, OPTION_PROBE_ERROR)


def replay_refusal_probe(probe: dict) -> dict:
    """Gate 1c's replay: the paste must die as an unrecognized instrument."""
    return replay_ingestion_probe(probe, REFUSAL_PROBE_ERROR)


def gate_option_rejection(probes: list[dict]) -> Gate:
    """Options (CE/PE/OPT/CALL/PUT) must exit 2 with no canonical payload.

    Graded over the benchmark's option-probe roster (evals.md §2.1), not over
    `trades`: an option can never *be* a trade — stage 0 rejects the paste before
    a canonical `current_trade.json` exists and the proof root pins no option
    scenario — so the roster sits beside the 52-trade quota rather than inside
    it. Each probe is replayed live through the real CLI above; a recorded exit
    code would only ever prove the code that ran when it was recorded.
    """
    g = Gate("Option Rejection Guardrail", OPTION_REJ_MIN, higher_is_pass=True)
    if not probes:
        g.skipped = True
        g.detail = "no option-instrument probes seeded in the benchmark yet"
        return g
    rejected, failures = 0, []
    for probe in probes:
        probe_id = str(probe.get("probe_id") or "<no probe_id>")
        outcome = replay_option_probe(probe)
        if outcome["rejected"]:
            rejected += 1
        else:
            failures.append(
                f"{probe_id}: exit {outcome['exit_code']}, "
                f"stderr={outcome['stderr'][:60]!r}, "
                f"payload emitted={outcome['emitted_payload']}")
    g.value = 100.0 * rejected / len(probes)
    g.coverage = (f"{rejected}/{len(probes)} option probe(s) replayed live through "
                  f"stage 0 (scripts/0_ingest_reco.py) — exit "
                  f"{OPTION_PROBE_EXIT} + {OPTION_PROBE_ERROR} and no canonical "
                  f"payload")
    if failures:
        g.detail = "; ".join(failures[:3]) + (
            f" (+{len(failures) - 3} more)" if len(failures) > 3 else "")
    return g


def check_option_probe_roster(data: dict) -> tuple[bool, str]:
    """The option-probe roster is complete and every probe is replayable.

    The probes are the benchmark's stage-0 test cases (evals.md §2.1): each is an
    authored Univest-style paste that declares itself as such (`origin`; also
    restated in `meta.notes` — parser input, never a sourced recommendation and
    never a hazard measurement), carries an option token stage 0 makes fatal, and
    states the rejection contract it must reproduce. A probe missing any of that is not evidence for the invariant, so
    it fails the run rather than shrinking the graded set silently.
    """
    meta = data.get("meta", {})
    probes = data.get("option_probes") or []
    expected = meta.get("option_probes_expected")
    problems: list[str] = []
    if not isinstance(expected, int):
        problems.append("meta.option_probes_expected missing")
    elif len(probes) != expected:
        problems.append(f"{len(probes)} probe(s) present, expected {expected}")
    for probe in probes:
        label = str(probe.get("probe_id") or "<no probe_id>")
        if not str(probe.get("raw_text") or "").strip():
            problems.append(f"{label}: no raw_text to replay")
        if str(probe.get("instrument_type") or "").upper() not in OPTION_TOKENS:
            problems.append(f"{label}: instrument_type={probe.get('instrument_type')!r} "
                            f"is not one of {', '.join(OPTION_TOKENS)}")
        if probe.get("origin") != OPTION_PROBE_ORIGIN:
            problems.append(f"{label}: origin must be {OPTION_PROBE_ORIGIN!r} — a "
                            "probe is an authored parser input and is disclosed as "
                            "one, never passed off as a sourced recommendation")
        contract = probe.get("expected") or {}
        if (contract.get("exit_code") != OPTION_PROBE_EXIT
                or contract.get("error_code") != OPTION_PROBE_ERROR):
            problems.append(f"{label}: expected must state exit_code "
                            f"{OPTION_PROBE_EXIT} / {OPTION_PROBE_ERROR} "
                            f"(ADR-001, TICK-001 §3)")
    if problems:
        return False, "; ".join(problems[:3]) + (
            f" (+{len(problems) - 3} more)" if len(problems) > 3 else "")
    return True, (f"{len(probes)}/{expected} option probe(s) seeded beside the "
                  f"{meta.get('total_trades_expected')}-trade roster (an option can "
                  f"never be a trade — ADR-001)")


# --------------------------------------------------------------------------
# Gate 1c: Ingestion Refusal Invariance — the closed instrument domain
# (ADR-001: "only equities and futures are permitted"). Gate 1's sibling,
# graded the same way over its own authored roster (evals.md §2.1).
# --------------------------------------------------------------------------
def gate_refusal_rejection(probes: list[dict]) -> Gate:
    """Unrecognized instruments and option homoglyphs must die at ingestion.

    The half of ADR-001 the option-token scan cannot name. A paste whose
    `Type:` is `СЕ` (Cyrillic) is not the ASCII token `CE`, so the option gate
    has nothing to say about it; the closed domain does. Each probe is replayed
    live through the real CLI, exactly as gate 1 replays the option roster, and
    is told apart from it by the code it must produce — a paste the gate never
    classified as an option must not be able to inflate the option rejection
    rate, so these are their own line rather than extra cases in gate 1's.
    """
    g = Gate("Unrecognized Instrument Guardrail", REFUSAL_MIN, higher_is_pass=True)
    if not probes:
        g.skipped = True
        g.detail = "no ingestion-refusal probes seeded in the benchmark yet"
        return g
    refused, failures = 0, []
    families: dict[str, int] = {}
    for probe in probes:
        probe_id = str(probe.get("probe_id") or "<no probe_id>")
        family = str(probe.get("family") or "unstated")
        families[family] = families.get(family, 0) + 1
        outcome = replay_refusal_probe(probe)
        if outcome["rejected"]:
            refused += 1
        else:
            failures.append(
                f"{probe_id}: exit {outcome['exit_code']}, "
                f"stderr={outcome['stderr'][:60]!r}, "
                f"payload emitted={outcome['emitted_payload']}")
    g.value = 100.0 * refused / len(probes)
    breakdown = ", ".join(
        f"{count} {REFUSAL_FAMILY_LABEL.get(family, family)}"
        for family, count in sorted(families.items()))
    g.coverage = (f"{refused}/{len(probes)} refusal probe(s) replayed live through "
                  f"stage 0 (scripts/0_ingest_reco.py) — exit {REFUSAL_PROBE_EXIT} "
                  f"+ {REFUSAL_PROBE_ERROR} and no canonical payload ({breakdown})")
    if failures:
        g.detail = "; ".join(failures[:3]) + (
            f" (+{len(failures) - 3} more)" if len(failures) > 3 else "")
    return g


def check_refusal_probe_roster(data: dict) -> tuple[bool, str]:
    """The refusal roster is complete and every probe is a true refusal case.

    Same governance as the option roster (authored origin, a declared contract,
    `meta.refusal_probes_expected`), plus the checks only this family needs:
    the declared instrument must actually appear in the paste it is supposed to
    refuse, and it must be one the **token** gate cannot name — an ASCII option
    token here would be an option probe filed in the wrong roster, declaring a
    code stage 0 can never return for it.
    """
    meta = data.get("meta", {})
    probes = data.get("refusal_probes") or []
    expected = meta.get("refusal_probes_expected")
    problems: list[str] = []
    if not isinstance(expected, int):
        problems.append("meta.refusal_probes_expected missing")
    elif len(probes) != expected:
        problems.append(f"{len(probes)} probe(s) present, expected {expected}")
    for probe in probes:
        label = str(probe.get("probe_id") or "<no probe_id>")
        declared = str(probe.get("declared_instrument") or "")
        raw = str(probe.get("raw_text") or "")
        if not raw.strip():
            problems.append(f"{label}: no raw_text to replay")
        if not declared.strip():
            problems.append(f"{label}: no declared_instrument to refuse")
        elif declared not in raw:
            problems.append(f"{label}: declared_instrument {declared!r} does not "
                            "appear in the paste it must refuse")
        if probe.get("origin") != OPTION_PROBE_ORIGIN:
            problems.append(f"{label}: origin must be {OPTION_PROBE_ORIGIN!r} — a "
                            "probe is an authored parser input, never passed off "
                            "as a sourced recommendation")
        if probe.get("family") not in REFUSAL_PROBE_FAMILIES:
            problems.append(f"{label}: family={probe.get('family')!r} is not one of "
                            f"{', '.join(REFUSAL_PROBE_FAMILIES)}")
        contract = probe.get("expected") or {}
        if (contract.get("exit_code") != REFUSAL_PROBE_EXIT
                or contract.get("error_code") != REFUSAL_PROBE_ERROR):
            problems.append(f"{label}: expected must state exit_code "
                            f"{REFUSAL_PROBE_EXIT} / {REFUSAL_PROBE_ERROR} "
                            f"(ADR-001 closed instrument domain)")
        if set(re.findall(r"[A-Z0-9]+", declared.upper())) & set(OPTION_TOKENS):
            problems.append(f"{label}: declared_instrument {declared!r} carries an "
                            "ASCII option token — that is an option probe, and its "
                            "declared code would be unreachable")
    if problems:
        return False, "; ".join(problems[:3]) + (
            f" (+{len(problems) - 3} more)" if len(problems) > 3 else "")
    families = {str(probe.get("family")) for probe in probes}
    return True, (f"{len(probes)}/{expected} refusal probe(s) seeded beside the "
                  f"{meta.get('total_trades_expected')}-trade roster "
                  f"({', '.join(sorted(families))}; an unrecognized instrument is "
                  f"never a trade — ADR-001)")


# --------------------------------------------------------------------------
# Gate 1b: Chain Abort Invariance (evals.md §3 #3, the orchestrator's half)
# --------------------------------------------------------------------------
# The artifacts a stage-0 rejection must never leave behind: stage 1's payload
# and the decision engine's are the two the chain would have to fabricate a
# trade to produce, and the report directory is what a user would read.
DOWNSTREAM_ARTIFACTS = ("out_1_math.json", "out_9_decision.json")


def chain_abort_failures(exit_code: int | None, stderr_text: str, workdir: Path,
                         record: dict | None,
                         error_code: str = OPTION_PROBE_ERROR) -> list[str]:
    """Every way a chain run failed to stop at the ingestion gate.

    Empty means it stopped. Split out as a pure function because the negative
    cases are what matter — a swallowed exit code, a payload written anyway, a
    record that claims later stages ran — and they are cheaper and safer to
    forge here than to produce by sabotaging the runner on disk.

    `error_code` is the refusal the family under test must put on stderr: the
    two families share exit 2 and are told apart by the code alone, so an
    option stop can never stand in for a homoglyph one and vice versa.
    """
    problems: list[str] = []
    if exit_code != OPTION_PROBE_EXIT:
        problems.append(f"exit {exit_code}, expected {OPTION_PROBE_EXIT}")
    if error_code not in stderr_text:
        problems.append(f"no {error_code} on stderr")
    for name in DOWNSTREAM_ARTIFACTS:
        if (workdir / name).exists():
            problems.append(f"downstream artifact written: {name}")
    if (workdir / "reports").exists():
        problems.append("report directory rendered from a rejected trade")
    record = record or {}
    if not record:
        problems.append("no run_record.json to account for the run")
        return problems
    if record.get("outcome") != "aborted":
        problems.append(f"outcome={record.get('outcome')!r}, expected 'aborted'")
    if record.get("failed_stage") != 0:
        problems.append(f"failed_stage={record.get('failed_stage')!r}, expected 0")
    if record.get("exit_code") != OPTION_PROBE_EXIT:
        problems.append(f"record exit_code={record.get('exit_code')!r}, expected "
                        f"{OPTION_PROBE_EXIT}")
    if record.get("report") is not None:
        problems.append(f"record claims a report: {record.get('report')!r}")
    rows = {row.get("stage"): row for row in record.get("stages") or []
            if isinstance(row, dict)}
    failed = rows.get(0) or {}
    if failed.get("status") != "failed" or failed.get("exit_code") != OPTION_PROBE_EXIT:
        problems.append(f"stage 0 row is {failed.get('status')!r}/"
                        f"{failed.get('exit_code')!r}, expected 'failed'/{OPTION_PROBE_EXIT}")
    walked_on = [stage for stage in range(1, 13)
                 if (rows.get(stage) or {}).get("status") != "not_reached"]
    if walked_on:
        problems.append(f"later stage(s) not recorded as not_reached: {walked_on}")
    return problems


def replay_probe_through_runner(probe: dict,
                                error_code: str = OPTION_PROBE_ERROR) -> dict:
    """Replay one probe through the real 0 → 12 chain, the way a user runs it.

    Gate 1/1c prove stage 0 refuses the paste; they say nothing about the
    orchestrator, which is what a user actually invokes. `run_pipeline.py`
    forwards each stage's stdout into its artifact and an orchestrator that
    swallowed a non-zero exit code would carry the rejected paste into stages
    1 … 12 while stage 0 printed its refusal. So the whole chain is invoked here
    (`--input <paste> --workdir <tmp>`) and graded on the stop as a whole: the
    propagated code, the family's *own* code on stderr (`error_code`), no
    downstream artifact, no report, and a run record that names stage 0
    `failed` with that code while every later stage is `not_reached`.
    """
    runner = REPO_ROOT / "scripts" / "run_pipeline.py"
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        paste = root / "paste.txt"
        paste.write_text(str(probe.get("raw_text") or ""), encoding="utf-8")
        workdir = root / "work"
        proc = subprocess.run(
            [sys.executable, str(runner), "--input", str(paste),
             "--workdir", str(workdir)],
            capture_output=True, check=False, cwd=REPO_ROOT,
            text=True, encoding="utf-8", errors="replace",
        )
        record: dict | None = None
        record_path = workdir / "run_record.json"
        if record_path.exists():
            try:
                record = json.loads(record_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                record = None
        problems = chain_abort_failures(proc.returncode, proc.stderr or "",
                                       workdir, record, error_code)
    return {"stopped": not problems, "exit_code": proc.returncode,
            "problems": problems}


def gate_chain_abort(probes: list[dict],
                     refusal_probes: list[dict] | None = None) -> Gate:
    """A paste stage 0 refuses must not enter the 0 → 12 chain at all.

    The orchestrator's half of ADR-001 — *both* halves of it. Kept as its own
    gate rather than folded into the rejection rates because the failure it
    catches is a different one: stage 0 refusing correctly while the chain
    walks on regardless — which two 100.0% rejection rates would happily
    report as green.

    **One named replay per refusal family.** The runner's stop does not branch
    on *why* stage 0 refused — it propagates whatever stage 0 returns — so
    replaying a whole roster through the chain would multiply the evidence's
    cost without adding a claim (per-paste coverage is gates 1/1c's, and the
    stage-0 seams replay every probe through the orchestrator
    (`tests/test_run_evals.TestChainAbortGate`)). What each replay does add is
    the *family's* code: the chain has to stop on `ERR_OPTIONS_PROHIBITED` for
    the option paste and on `ERR_INSTRUMENT_UNRECOGNIZED` for a paste the token
    scan cannot name — the same exit 2, but not the same stop, and one is no
    evidence for the other. Both probes are printed next to the number, so two
    replays can read as neither one roster's nor as roster-wide.
    """
    g = Gate("Ingestion Chain Abort", CHAIN_ABORT_MIN, higher_is_pass=True)
    replays: list[tuple[str, dict, str]] = []
    unseeded: list[str] = []
    if probes:
        replays.append((str(probes[0].get("probe_id") or "<no probe_id>"),
                        probes[0], OPTION_PROBE_ERROR))
    else:
        unseeded.append("option_probes")
    if refusal_probes:
        replays.append((str(refusal_probes[0].get("probe_id") or "<no probe_id>"),
                        refusal_probes[0], REFUSAL_PROBE_ERROR))
    else:
        unseeded.append("refusal_probes")
    if not replays:
        g.skipped = True
        g.detail = ("no stage-0 probe rosters seeded in the benchmark yet "
                    f"({', '.join(PROBE_ROSTERS)})")
        return g
    stopped, failures = 0, []
    for probe_id, probe, error_code in replays:
        outcome = replay_probe_through_runner(probe, error_code)
        if outcome["stopped"]:
            stopped += 1
        else:
            failures.append(f"{probe_id}: exit {outcome['exit_code']} — "
                            f"{'; '.join(outcome['problems'][:3])}")
    g.value = 100.0 * stopped / len(replays)
    named = ", ".join(f"{probe_id} with {error_code}"
                      for probe_id, _probe, error_code in replays)
    g.coverage = (f"{len(replays)}/{len(replays)} chain replays through the full "
                  f"0 → 12 chain (scripts/run_pipeline.py) — stage 0 failed with "
                  f"exit {OPTION_PROBE_EXIT} and its own code, no downstream "
                  f"artifact, later stages not_reached ({named})"
                  + (f"; roster(s) not seeded, half ungraded: "
                     f"{', '.join(unseeded)}" if unseeded else ""))
    if failures:
        g.detail = "; ".join(failures[:3]) + (
            f" (+{len(failures) - 3} more)" if len(failures) > 3 else "")
    return g


# --------------------------------------------------------------------------
# Run-record provenance (the evidence ledger behind every graded artifact)
# --------------------------------------------------------------------------
def load_proof(reports_dir: Path | None = None,
               extra: list[Path] | None = None) -> provenance.Proof:
    """The run records that can prove the artifacts in a reports directory.

    The record written *beside* the artifacts is always read — an artifact and
    its provenance travel together, which is what stops a dev machine and a fresh
    clone from grading differently. `extra` folds in externally produced records
    (`--run-record`), e.g. a real `scripts/run_pipeline.py` chain run whose rows
    prove the whole 0 → 12 walk.

    The proof root is folded in as well (`tests/evals/proof_root.py`): its payload
    declarations become record rows, so gate 2's inputs carry the same kind of
    proof the stage-12 artifacts do. That fold is a read of the frozen proof root,
    not a fifth file to keep in step with it.

    A record that cannot be parsed raises `ProvenanceError`; `main()` reports that
    as a usage error (exit 2) rather than grading around a broken ledger.
    """
    proof = provenance.Proof(list(extra or []))
    local = (reports_dir or (REPO_ROOT / "reports")) / provenance.RECORD_NAME
    if local.exists():
        proof.add(local)
    try:
        proof.add(proof_root.MANIFEST_PATH, record=proof_root.record())
    except proof_root.ProofRootError as exc:
        raise provenance.ProvenanceError(str(exc)) from exc
    return proof


def proven_reports(trades: list[dict], reports_dir: Path,
                   proof: provenance.Proof) -> tuple[int, list[str]]:
    """Count the stage-12 artifacts a record proves; name the ones it does not."""
    proven = 0
    unproven: list[str] = []
    for trade in trades:
        path = reports_dir / report_path_for(trade).name
        if not path.exists():
            unproven.append(f"{trade.get('trade_id')}: no report on disk ({path.name})")
            continue
        ok, reason = proof.proves(path.name, path, 12)
        proven += 1 if ok else 0
        if not ok:
            unproven.append(f"{trade.get('trade_id')}: {reason}")
    return proven, unproven


def record_label(path: Path) -> str:
    """How a record is named on the scorecard: repo-relative, else parent/name.

    Two records are usually both called `run_record.json`, so the bare filename
    says nothing about which run is being quoted.
    """
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return f"{path.parent.name}/{path.name}"


def dataset_payload_targets(raw: dict) -> dict[tuple[str, str], Path]:
    """Where the dataset's `$ref` payloads live, keyed by `(trade_id, artifact)`.

    Read off the *raw* dataset: `dataset_refs.materialize` replaces each `$ref`
    with the bytes it stands for, so the file a gate reads has to be captured
    before that happens — and it is the file, not the payload's presence, that
    says whether a recorded artifact backs what the gate grades.
    """
    return {(trade_id, artifact): REPO_ROOT / ref
            for trade_id, artifact, ref in dataset_refs.refs(raw)}


def gate_payloads(trade: dict,
                  targets: dict[tuple[str, str], Path] | None = None,
                  ) -> list[tuple[str, Path | None]]:
    """The payloads the graded gates read for a record: `(artifact, bytes it reads)`.

    Every payload the record carries, plus the artifact its *class* breaker needs
    (that is the claim the class exists to make), carried or not. Both halves are
    gate inputs: the Zero-Hallucination differ backs a report's numbers from the
    record's whole JSON, so any carried payload can back a graded claim, and gate 2
    reads named breaker artifacts — including ones that are not the class's own,
    because absence must never be read as "nothing fired"
    (`class_breaker_observed`).

    The second element is the file the payload resolves to — `targets` for a
    materialized dataset, or the trade's own `$ref` when it is still unresolved —
    and `None` when the payload is carried inline in the dataset: those bytes are
    the dataset's own, and no recorded artifact backs them (E1).
    """
    outputs = trade.get("script_outputs") or {}
    required = set(outputs)
    breaker = BREAKER_BY_CLASS.get(str(trade.get("trade_class")))
    if breaker:
        required.add(BREAKER_ARTIFACT[breaker])
    payloads: list[tuple[str, Path | None]] = []
    for artifact in sorted(required):
        value = outputs.get(artifact)
        target = (targets or {}).get((str(trade.get("trade_id")), artifact))
        if target is None and dataset_refs.is_ref(value):
            target = REPO_ROOT / dataset_refs.ref_path(value)
        payloads.append((artifact, target))
    return payloads


def unbacked_payloads(trades: list[dict],
                      targets: dict[tuple[str, str], Path] | None = None,
                      ) -> frozenset[tuple[str, str]]:
    """`(trade_id, artifact)` of every carried payload with no recording behind it.

    A payload the dataset holds inline — not a `$ref` into the proof root — has no
    file behind it, so no run record can ever prove those bytes, and E1
    (`scenario-manifest-schema.md` §3) says a payload a graded gate reads must be a
    recording rather than an extracted subset typed into the dataset.
    `gate_hard_breakers` abstains on such a payload exactly as it abstains on a
    quarantined artifact; `check_payload_coverage` fails the run on it.

    Only *carried* payloads qualify: a class-breaker artifact the record omits
    altogether is a different failure (`breaker value not encoded`), never an
    unproven payload.
    """
    return frozenset(
        (str(trade.get("trade_id")), artifact)
        for trade in trades
        for artifact, target in gate_payloads(trade, targets)
        if target is None and artifact in (trade.get("script_outputs") or {})
    )


def payload_gap_kind(trade: dict, artifact: str, target: Path | None,
                     proof: provenance.Proof) -> tuple[str, str]:
    """`(kind, reason)` for one gate payload: `proven`, `registered`, `unregistered`.

    The two ways a payload stops being evidence are separated here because the
    repo treats them differently (`scenario-manifest-schema.md` E1/E4/E5):

    * `unregistered` — typed bytes the dataset holds inline, an artifact no
      declaration covers, or a declared recording whose bytes are gone or
      changed. Nobody declared this gap, so it fails the run.
    * `registered` — the proof root declares the artifact `waived` or
      `quarantined`; its folded row is `skipped`, the gates abstain, and the gap
      is disclosed and excluded from grading rather than fatal (legacy debt is
      tracked, never grown).
    """
    label = proof_root.label(str(trade.get("trade_id")), artifact)
    proven, reason = proof.proves_payload(label, target)
    if proven:
        return "proven", reason
    carried = artifact in (trade.get("script_outputs") or {})
    if target is None and carried:
        # Typed bytes: no declaration can prove them, whatever the proof root says.
        return "unregistered", reason
    rows = proof.rows_for(label)
    if rows and not any(entry.get("status") == "ran" for _source, entry in rows):
        return "registered", reason
    if not rows and not carried:
        return "unregistered", (f"no proof-root declaration covers {artifact} — a "
                                "graded gate's artifact must be recorded, waived "
                                "or quarantined (E1)")
    return "unregistered", reason


def check_payload_coverage(trades: list[dict], proof: provenance.Proof,
                           targets: dict[tuple[str, str], Path] | None = None,
                           ) -> tuple[int, list[str], list[str]]:
    """Count the records whose gate payloads carry no *unregistered* gap.

    `(covered, uncovered, excluded)`: `covered` counts records whose every gate
    payload is either proven or registered debt; `uncovered` names the records
    with a gap nobody declared (typed bytes, an undeclared artifact, a
    contradicted recording) and is what fails the run; `excluded` names the
    registered exclusions — disclosed, never graded, and never grown.

    Coverage exists because a gate is defined over the whole benchmark: a record
    the proof root declares `waived` or `quarantined` leaves it quietly, and a
    disclosure inside one gate's detail is not enough to notice that the graded
    set shrank. The two lists are what the provenance block prints, so the line
    and the ledger cannot disagree.
    """
    covered = 0
    uncovered: list[str] = []
    excluded: list[str] = []
    for trade in trades:
        shortfalls: list[str] = []
        registered: list[str] = []
        for artifact, target in gate_payloads(trade, targets):
            kind, reason = payload_gap_kind(trade, artifact, target, proof)
            if kind == "proven":
                continue
            (registered if kind == "registered" else shortfalls).append(
                f"{artifact} ({reason})")
        tid = str(trade.get("trade_id"))
        if registered:
            excluded.append(f"{tid}: gate payload " + "; ".join(registered))
        if shortfalls:
            uncovered.append(f"{tid}: gate payload " + "; ".join(shortfalls))
        else:
            covered += 1
    return covered, uncovered, excluded


def check_evidence_coverage(trades: list[dict], reports_dir: Path,
                            proof: provenance.Proof,
                            payload_coverage: tuple[int, list[str], list[str]] | None = None,
                            targets: dict[tuple[str, str], Path] | None = None,
                            ) -> tuple[bool, str, list[str], list[str]]:
    """Every graded record must carry evidence a run record proves — both halves.

    The gates are defined over the whole benchmark, so a record they cannot read
    is a hole in the graded set, and a scorecard that quietly grades 29 of 31
    claims more than it checked. Two kinds of hole are checked, because two kinds
    of evidence back a graded record: the **stage-12 artifact** the artifact-graded
    gates read, and the **gate payloads** every graded gate reads — every payload a
    record carries (the differ backs report numbers from all of them) plus the
    artifact its class breaker needs. For stage-12 reports the fix is the stated
    one — the reproduction already tried (`ensure_reports`), so whatever remains
    needs the record repaired; for payloads it is the payload's provenance —
    reference a recorded artifact the proof root declares `recorded`, or stop
    carrying the bytes at all.

    Only *unregistered* payload gaps fail the run. A payload the proof root
    declares `waived` or `quarantined` is registered debt: disclosed here, graded
    by nobody, and returned as `excluded` rather than counted against the run —
    the dataset's own mid-curation disclosure (`evals.md` §4.1) would otherwise
    make a green run impossible until every engine exists.

    Returns `(ok, summary, uncovered, excluded)`; `uncovered` is exactly the list
    the provenance block prints, so the line and the ledger cannot disagree.
    """
    proven, ungraded = proven_reports(trades, reports_dir, proof)
    covered, unproven, payload_excluded = (
        payload_coverage if payload_coverage is not None
        else check_payload_coverage(trades, proof, targets))
    total = len(trades)
    reports_reason = f"{proven}/{total} record(s) carry proven stage-12 evidence"
    payload_reason = (f"{covered}/{total} record(s) carry gate payloads "
                      "with no unregistered gap")
    if not ungraded and not unproven:
        reason = f"{reports_reason}; {payload_reason}"
        if payload_excluded:
            reason += (f" — {len(payload_excluded)} registered exclusion(s) "
                       "never graded")
        return True, reason, [], payload_excluded
    reports_summary = (f"{reports_reason} — {len(ungraded)} ungraded" if ungraded
                       else reports_reason)
    payload_summary = (f"{payload_reason} — {len(unproven)} unregistered" if unproven
                       else payload_reason)
    if payload_excluded:
        payload_summary += (f" ({len(payload_excluded)} registered exclusion(s) "
                            "never graded)")
    return (False, f"{reports_summary}; {payload_summary}", [*ungraded, *unproven],
            payload_excluded)


def render_provenance(proof: provenance.Proof, proven: int, total: int,
                      unproven: list[str], integrity: list[str],
                      payload_covered: int | None = None,
                      payload_unproven: list[str] | None = None,
                      payload_excluded: list[str] | None = None) -> list[str]:
    """The evidence ledger block: what proved what, and what stayed disclosed.

    Printed for every run so the scorecard shows *why* a gate could grade at all
    — including the stages a record discloses as skipped instead of pretending
    the chain was complete.
    """
    lines = ["", "  ── Evidence provenance (run records) ──"]
    if not proof.records:
        lines.append("  No run record read — no artifact can be proven: "
                     "report-regeneration writes one beside the artifacts it produces.")
        return lines
    lines.append(f"  Records read: {len(proof.records)}")
    for source, document in proof.records:
        summary = provenance.document_stage_summary(document)
        ran = summary["ran"] or "none"
        skipped = summary["skipped"] or "none"
        failed = summary["failed"] or "none"
        lines.append(f"    ↳ {record_label(source)} ({document.get('outcome')}, "
                     f"exit {document.get('exit_code')}) — stages ran: {ran} · "
                     f"skipped: {skipped} · failed: {failed}")
    lines.append(f"  Stage-12 reports proven: {proven}/{total}")
    if payload_covered is not None:
        lines.append(f"  Gate payloads proven: {payload_covered}/{total}")
    if unproven:
        lines.append(f"  Reports excluded (unproven — never graded): {len(unproven)}")
        for reason in unproven[:3]:
            lines.append(f"    ↳ {reason}")
        if len(unproven) > 3:
            lines.append(f"    ↳ (+{len(unproven) - 3} more)")
    if payload_excluded:
        lines.append("  Gate payloads excluded (registered debt — no gate grades "
                     f"them as evidence): {len(payload_excluded)}")
        for reason in payload_excluded[:3]:
            lines.append(f"    ↳ {reason}")
        if len(payload_excluded) > 3:
            lines.append(f"    ↳ (+{len(payload_excluded) - 3} more)")
    if payload_unproven:
        lines.append("  Gate payloads with no registered evidence (unregistered "
                     f"gap — the run fails on it): {len(payload_unproven)}")
        for reason in payload_unproven[:3]:
            lines.append(f"    ↳ {reason}")
        if len(payload_unproven) > 3:
            lines.append(f"    ↳ (+{len(payload_unproven) - 3} more)")
    for problem in integrity:
        lines.append(f"  ⚠ record contradicts the disk: {problem}")
    return lines


# --------------------------------------------------------------------------
# Gate 3: Zero Hallucination (evals.md §3 #1)
# --------------------------------------------------------------------------
def report_path_for(trade: dict) -> Path:
    """The stage-12 artifact name of a record (BRD §13, TICK-010's template).

    `reports/report_{SYMBOL}_{DATE}.html`, DATE being the recommendation date
    as YYYYMMDD — the one name the contract pins, so the gate, the visualizer
    and the differ all meet at the same file.
    """
    symbol = str(trade.get("symbol") or "").upper()
    day = str(trade.get("recommendation_date") or "").replace("-", "")
    return REPO_ROOT / "reports" / f"report_{symbol}_{day}.html"


def gate_hallucination(trades: list[dict], reports_dir: Path | None = None,
                       proof: provenance.Proof | None = None) -> Gate:
    """Every number a report shows must trace to that record's own JSON — and the
    report must be *proven* to be a real stage-12 artifact.

    The retired `reported_numbers_match_json` boolean was a curator's *claim*
    about a report; `tests/evals/report_diff.py` is the check itself, run here
    against the stage-12 artifact whenever one has been generated. Reading bytes
    that no run produced would be the same species of claim, so the artifact must
    also carry proof: a run record must show stage 12 ran and produced exactly
    these bytes. Anything unproven is reproduced first (`ensure_reports`, which
    never overwrites the graded file), then disclosed and excluded — an absent or
    unproven artifact is ungraded, never a fake green. `main()` does that replay
    before grading, so a record still ungraded after it has no replayable proof.
    """
    g = Gate("Zero Hallucination Audit", HALLUCINATION_MIN, higher_is_pass=True)
    reports = reports_dir or (REPO_ROOT / "reports")
    proof = proof if proof is not None else load_proof(reports)
    graded: list[float] = []
    per_trade: list[str] = []
    ungraded: list[str] = []
    unproven: list[str] = []
    for t in trades:
        path = reports / report_path_for(t).name
        if not path.exists():
            ungraded.append(str(t.get("trade_id")))
            continue
        proven, reason = proof.proves(path.name, path, 12)
        if not proven:
            unproven.append(f"{t.get('trade_id')}: {reason}")
            continue
        problems = report_diff.diff(
            path.read_text(encoding="utf-8", errors="replace"), t)
        graded.append(1.0 if not problems else 0.0)
        if problems:
            shown = "; ".join(problems[:3])
            more = f" (+{len(problems) - 3} more)" if len(problems) > 3 else ""
            per_trade.append(f"{t.get('trade_id')}: {shown}{more}")
    g.skipped = not graded
    if g.skipped:
        if unproven and not ungraded:
            g.detail = (f"no stage-12 report is proven by a run record — "
                        f"{len(unproven)} unproven: {'; '.join(unproven[:3])}")
        else:
            g.detail = (f"no stage-12 reports generated yet — {len(ungraded)} "
                        "record(s) without a report, "
                        f"{len(unproven)} unproven; the runner reproduces both on "
                        "demand before grading (tests/evals/report_regen.py)")
    else:
        g.value = 100.0 * (sum(graded) / len(graded))
        g.detail = (f"{len(graded)} graded, "
                    f"{sum(1 for v in graded if v == 0.0)} with discrepancies"
                    + (f"; {'; '.join(per_trade)}" if per_trade else "")
                    + (f"; not graded (no report generated): {len(ungraded)}"
                       if ungraded else "")
                    + (f"; not graded (unproven): {'; '.join(unproven[:2])}"
                       if unproven else ""))
    return g


# --------------------------------------------------------------------------
# Gate 4: Directional Calibration (Brier), evals.md §3 #4
# --------------------------------------------------------------------------
def gate_brier(trades: list[dict]) -> Gate:
    """Brier = mean((p − y)²) over calibrated 1-month direction probabilities.

    `predicted_direction` is a hard class label (ties to accuracy tracking),
    NOT a probability — grading it here would silently substitute accuracy
    for calibration, so the gate stays [SKIP] until the stage-4 model replay
    supplies `predicted_probability` per trade.
    """
    g = Gate("Directional Brier Score", BRIER_MAX, higher_is_pass=False)
    graded = []
    for t in trades:
        prob = t.get("predicted_probability")
        actual = t.get("actual_direction")
        if prob is None or actual not in (0, 1):
            continue
        graded.append((prob, actual))
    g.fmt = "{:.3f}"
    if not graded:
        g.skipped = True
        g.detail = ("no calibrated direction probabilities yet — hard labels in "
                    "`predicted_direction` cannot stand in for a Brier score")
        return g
    g.value = sum((p - y) ** 2 for p, y in graded) / len(graded)
    g.detail = f"{len(graded)} calibrated predictions"
    return g


# --------------------------------------------------------------------------
# Gate 5: Price Cone Range Coverage, evals.md §3 #5
# --------------------------------------------------------------------------
def trained_corpus_window(path: Path | None = None) -> tuple[str, str] | None:
    """`(first_session, last_session)` of the corpus the cone's pools came from.

    Read out of the shipped booster's own disclosure
    (`models/lgbm_direction_v1.calibration.json` → `provenance.corpus.row_window`),
    so the gate grades against what the artifact says it was trained on: a
    retrain widens or narrows the scope without a gate edit, and a calibration
    that is missing or malformed yields **no** window rather than an assumed one.
    """
    target = path if path is not None else CALIBRATION_FILE
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
        first, last = document["provenance"]["corpus"]["row_window"][:2]
        first, last = str(first), str(last)
    except (OSError, json.JSONDecodeError, KeyError, IndexError, TypeError,
            ValueError):
        return None
    return (first, last) if first and last and first <= last else None


def load_stage4():
    """`scripts/4_ml_direction.py` — the stage that owns the cone's pools.

    Gate 5's miss diagnosis has to name the pool the cone **actually read** for
    each record, so it reuses the stage's own `samples_for` selection, its
    `CONE_SPEC`/`CONE_MEASURES` mapping and its `SECTOR_INDEX_MAP` — one
    implementation, imported, never re-derived here (the same reason the
    trainer imports the stage instead of copying `feature_vector`).
    """
    global _STAGE4
    if _STAGE4 is None:
        spec = importlib.util.spec_from_file_location(
            "uvest_stage4_ml_direction", REPO_ROOT / "scripts" / "4_ml_direction.py")
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        _STAGE4 = module
    return _STAGE4


def load_calibration(path: Path | None = None) -> dict | None:
    """The shipped calibration document, or None when it cannot be read.

    The same bytes `trained_corpus_window` reads the scope from: the pools the
    diagnosis counts are this artifact's, never a recomputed stand-in.
    """
    target = path if path is not None else CALIBRATION_FILE
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return document if isinstance(document, dict) else None


def pool_percentile(stage4, samples: dict, measure: str, value: float) -> float | None:
    """Where `value` sits in the pool's observed atoms (equal atom weight).

    The cone's levels invert a class-probability-weighted mixture of these
    same atoms, but the record's class weights are not in the frozen 8-key
    payload, so the disclosure counts atoms: "the percentile of the pool" is
    the share of observed excursions at or below the realised one. None when
    the pool is empty — a diagnosis is never invented from no bytes.
    """
    atoms = [float(observed)
             for cls in stage4.CLASSES
             for observed in ((samples.get(measure) or {}).get(cls) or [])]
    if not atoms:
        return None
    return 100.0 * sum(1 for observed in atoms if observed <= value) / len(atoms)


def gate_cone_coverage(trades: list[dict], *, window: tuple[str, str] | None,
                       spans: dict[str, tuple[str, str] | None],
                       calibration: dict | None = None) -> Gate:
    """Coverage of the forward *bars* by `[P10, P90]` — over the graded scope.

    The scope is the trained corpus window (evals.md §3 #5): the envelope's
    excursion pools were sourced from those sessions, so a forward tape outside
    them is a regime the pools never saw, and grading it would measure the
    corpus's age rather than the engine. Such a record is **named on the
    coverage line — with its span and the window — and excluded from the
    denominator**; it is ungraded, never a miss. The disclosure prints whatever
    the verdict, because a percentage over a smaller graded set reads exactly
    like one over the whole benchmark (the reason gate 2 carries its class
    coverage for the same purpose).

    **Each miss carries its own diagnosis** (`calibration` supplied): the
    realised low/high as percent of the entry, and the percentile that value
    occupies among the observed excursions of the pool the cone read *for that
    record* — its own sector's block when it is deep enough to answer, the
    pooled block otherwise, named either way (`pool_percentile`). The verdict
    alone says the envelope was too shallow; the per-miss percentiles say by
    how much, and against which population, so the red explains itself.

    `spans` maps `trade_id` → the record's own pinned tape's first/last session
    (`dataset_refs.forward_tape_span`), read before materialize because a
    `$derive` is the only place a tape's dates live; `None` means the record
    pins no tape and therefore cannot be placed in the window at all.
    """
    g = Gate("Price Cone Range Coverage", COVERAGE_MIN, higher_is_pass=True)
    graded: list[float] = []
    ungraded: list[str] = []
    diagnoses: list[str] = []
    candidates = 0
    for t in trades:
        cone_p10 = t.get("cone_p10")
        cone_p90 = t.get("cone_p90")
        fwd = t.get("forward_prices_22d") or {}
        lo, hi = fwd.get("min"), fwd.get("max")
        if cone_p10 is None or cone_p90 is None or lo is None or hi is None:
            continue
        candidates += 1
        tid = str(t.get("trade_id"))
        span = spans.get(tid)
        if window is None:
            ungraded.append(f"{tid} (trained corpus window unreadable)")
            continue
        if span is None:
            ungraded.append(f"{tid} (no pinned tape to place in the window)")
            continue
        first, last = span
        if not (window[0] <= first and last <= window[1]):
            ungraded.append(f"{tid} (tape {first}..{last}, outside "
                            f"{window[0]}..{window[1]})")
            continue
        covered_low = cone_p10 <= lo
        covered_high = hi <= cone_p90
        graded.append(1.0 if covered_low and covered_high else 0.0)
        if (covered_low and covered_high) or calibration is None:
            continue
        entry = t.get("entry_price")
        if not isinstance(entry, (int, float)) or entry <= 0:
            # Counted as a miss above; only the percent excursion needs an
            # entry to be measured against.
            continue
        try:
            stage4 = load_stage4()
            sector = stage4.SECTOR_INDEX_MAP.get(
                str(t.get("symbol") or "").strip().upper())
            samples = stage4.samples_for(calibration, sector)
            pooled = (calibration.get("samples") or {})
            pool = sector if (sector and samples is not pooled) else "pooled"
            levels = {key: measure for key, measure, _lvl in stage4.CONE_SPEC}
            if not covered_low:
                realised = (lo / entry - 1.0) * 100.0
                floor = (cone_p10 / entry - 1.0) * 100.0
                pct = pool_percentile(stage4, samples,
                                      stage4.CONE_MEASURES[levels["p10"]],
                                      realised)
                if pct is not None:
                    diagnoses.append(
                        f"{tid}: low {realised:+.2f}% = {pct:.1f}th pct of the "
                        f"{pool} adverse pool (floor {floor:+.2f}%)")
            if not covered_high:
                realised = (hi / entry - 1.0) * 100.0
                floor = (cone_p90 / entry - 1.0) * 100.0
                pct = pool_percentile(stage4, samples,
                                      stage4.CONE_MEASURES[levels["p90"]],
                                      realised)
                if pct is not None:
                    diagnoses.append(
                        f"{tid}: high {realised:+.2f}% = {pct:.1f}th pct of the "
                        f"{pool} favourable pool (floor {floor:+.2f}%)")
        except (ValueError, KeyError, TypeError, IndexError, ZeroDivisionError):
            # A diagnosis that cannot be computed is disclosed as absent, the
            # miss itself is already counted; the gate never dies on prose.
            diagnoses.append(f"{tid}: pool percentile unavailable "
                             "(calibration unreadable)")
    g.value = 100.0 * (sum(graded) / len(graded)) if graded else None
    g.skipped = not graded
    if window is None:
        g.coverage = (f"scope: trained corpus window unavailable "
                      f"({CALIBRATION_FILE.name}); {candidates} cone-labeled "
                      f"trade(s) ungraded until it reads")
        g.detail = ("cannot scope the cone envelope: the shipped calibration "
                    f"has no usable provenance.corpus.row_window "
                    f"({CALIBRATION_FILE.name})")
        return g
    if graded:
        misses = len(graded) - int(sum(graded))
        g.detail = (f"{len(graded)} of {candidates} cone-labeled trades graded "
                    f"inside the trained corpus window; {misses} of them "
                    f"missed [P10, P90]"
                    + (f"; {'; '.join(diagnoses)}" if diagnoses else ""))
    else:
        g.detail = (f"no cone-labeled trade lies inside the trained corpus "
                    f"window {window[0]}..{window[1]} — all {candidates} "
                    "candidate(s) are disclosed ungraded")
    shown = ungraded[:3]
    if len(ungraded) > 3:
        shown.append(f"+{len(ungraded) - 3} more")
    g.coverage = (f"scope: trained corpus window {window[0]}..{window[1]}; "
                  f"{len(graded)} of {candidates} cone-labeled trades graded"
                  + (f"; ungraded: {'; '.join(shown)}" if ungraded else ""))
    return g


# --------------------------------------------------------------------------
# Gate 6: Optimal Limit Entry MAE Reduction, evals.md §3 #6
# --------------------------------------------------------------------------
def gate_mae_reduction(trades: list[dict]) -> Gate:
    g = Gate("Optimal Limit Slippage Reduction", MAE_REDUCTION_MIN, higher_is_pass=True)
    graded: list[float] = []
    baselines: list[float] = []
    for t in trades:
        mkt, lim = t.get("market_mae_pct"), t.get("limit_mae_pct")
        if mkt is None or lim is None:
            continue
        if mkt > 0:
            baselines.append(100.0 * (mkt - lim) / mkt)
        graded.append(1.0 if lim < mkt else 0.0)
    if not graded:
        g.skipped = True
        g.detail = ("no market/limit MAE pairs with an engine-derived limit entry "
                    "yet — stage 8 unimplemented")
        return g
    # Report pass-fraction as the headline metric (threshold is a fraction),
    g.value = 100.0 * (sum(graded) / len(graded))
    net = sum(baselines) / len(baselines)
    g.detail = f"net expectancy: {net:+.1f}% avg MAE reduction"
    return g


# --------------------------------------------------------------------------
# Gate 7: HTML Report Validity, evals.md §3 #7
# --------------------------------------------------------------------------
# Any element carrying a src/href pulls the report off its own bytes — a
# missing stylesheet, a broken image, a CDN. `data:`, `#fragment` and
# `mailto:` targets are self-contained and legal.
_EXTERNAL_REF_RE = re.compile(
    r"<(img|source|video|audio|iframe|frame|embed|object|script|link|track|applet)\b"
    r"[^>]*\b(?:src|href)\s*=\s*[\"']?([^\"'\s>]+)",
    re.IGNORECASE,
)


def html_validity_problems(text: str) -> list[str]:
    """The headlessly-checkable half of the frozen §3 #7 criterion:

    HTML5 doctype, zero external resources ("0 network dependencies"),
    self-contained styling, and at least one inline SVG chart. The 300 ms
    render budget is a browser property and is deliberately NOT graded here —
    an unchecked criterion is disclosed by this docstring, never claimed pass.
    """
    problems: list[str] = []
    if not text.lstrip()[:15].lower().startswith("<!doctype html"):
        problems.append("missing <!doctype html> HTML5 doctype")
    for match in _EXTERNAL_REF_RE.finditer(text):
        target = match.group(2)
        if not target.lower().startswith(("data:", "#", "mailto:")):
            problems.append(
                f"external resource <{match.group(1)} src={target[:40]}>")
    if re.search(r"https?://", text, re.IGNORECASE):
        problems.append("network URL present — 0 network dependencies required")
    lowered = text.lower()
    if "<style" not in lowered:
        problems.append("no inline <style> block — CSS must be self-contained")
    if "<svg" not in lowered:
        problems.append("no inline <svg> chart")
    return problems


def gate_html_validity(trades: list[dict], reports_dir: Path | None = None,
                       proof: provenance.Proof | None = None) -> Gate:
    """The stage-12 artifact itself must be standalone HTML5 (evals.md §3 #7).

    The retired `html_report_valid` boolean was a curator's *claim* about a
    report (ADR-007: the gate stays graded, the flag is gone). The check reads
    the artifact at the canonical path — the same
    `reports/report_{SYMBOL}_{DATE}.html` the Zero-Hallucination differ grades —
    and validates what can be validated without a browser, on bytes a run record
    proves stage 12 produced. A record with no report, or with an unproven one,
    is ungraded rather than passed: absence is not evidence, and neither is an
    unattributable file.
    """
    g = Gate("HTML Visualizer Validation", HTML_VALIDITY_MIN, higher_is_pass=True)
    reports = reports_dir or (REPO_ROOT / "reports")
    proof = proof if proof is not None else load_proof(reports)
    graded: list[float] = []
    per_trade: list[str] = []
    ungraded: list[str] = []
    unproven: list[str] = []
    for t in trades:
        path = reports / report_path_for(t).name
        if not path.exists():
            ungraded.append(str(t.get("trade_id")))
            continue
        proven, reason = proof.proves(path.name, path, 12)
        if not proven:
            unproven.append(f"{t.get('trade_id')}: {reason}")
            continue
        problems = html_validity_problems(
            path.read_text(encoding="utf-8", errors="replace"))
        graded.append(1.0 if not problems else 0.0)
        if problems:
            shown = "; ".join(problems[:3])
            more = f" (+{len(problems) - 3} more)" if len(problems) > 3 else ""
            per_trade.append(f"{t.get('trade_id')}: {shown}{more}")
    g.skipped = not graded
    if g.skipped:
        if unproven and not ungraded:
            g.detail = (f"no stage-12 report is proven by a run record — "
                        f"{len(unproven)} unproven: {'; '.join(unproven[:3])}")
        else:
            g.detail = (f"no stage-12 reports generated yet — {len(ungraded)} "
                        "record(s) without a report, "
                        f"{len(unproven)} unproven; the runner reproduces both on "
                        "demand before grading")
    else:
        g.value = 100.0 * (sum(graded) / len(graded))
        g.detail = (f"{len(graded)} graded, "
                    f"{sum(1 for v in graded if v == 0.0)} invalid"
                    + (f"; {'; '.join(per_trade)}" if per_trade else "")
                    + (f"; not graded (no report generated): {len(ungraded)}"
                       if ungraded else "")
                    + (f"; not graded (unproven): {'; '.join(unproven[:2])}"
                       if unproven else ""))
    return g


# --------------------------------------------------------------------------
# Class distribution check (integrity, not a graded gate)
# --------------------------------------------------------------------------
def check_class_distribution(data: dict) -> tuple[bool, str]:
    """Structural integrity of the roster, versus `meta.class_counts_expected`.

    A partially seeded dataset is not an integrity failure: the roster is
    explicitly mid-curation (see `meta.notes`), and `distribution_verified`
    flips to `true` ONLY when all 52 records exist AND every class count
    matches. Until then the check verifies the *plausible* invariant — no
    single class exceeds its expected count — so seeding cannot silently
    overwrite a class quota.
    """
    meta = data.get("meta", {})
    expected = meta.get("class_counts_expected", {})
    trades = data.get("trades", [])
    counts: dict[str, int] = {}
    for t in trades:
        cls = t.get("trade_class")
        counts[cls] = counts.get(cls, 0) + 1

    total = len(trades)
    overfull = [
        f"{cls}: dataset={counts.get(cls, 0)} expected={exp}"
        for cls, exp in expected.items()
        if counts.get(cls, 0) > exp
    ]
    verified = meta.get("distribution_verified", False)
    complete = total == meta.get("total_trades_expected", total)

    if verified and not complete:
        return False, (f"distribution_verified=true but {total}/{meta.get('total_trades_expected')} "
                       f"trades present")
    if overfull:
        return False, "class quota exceeded: " + "; ".join(overfull)
    if verified:
        mismatches = [
            f"{cls}: dataset={counts.get(cls, 0)} expected={exp}"
            for cls, exp in expected.items()
            if counts.get(cls, 0) != exp
        ]
        if mismatches:
            return False, "class counts mismatch: " + "; ".join(mismatches)
        return True, f"{total}/{meta.get('total_trades_expected')} seeded, distribution verified"
    return True, (f"{total}/{meta.get('total_trades_expected')} seeded, mid-curation "
                  f"(distribution_verified=false)")


# --------------------------------------------------------------------------
# Scorecard rendering, evals.md §4 frozen template
# --------------------------------------------------------------------------
def render_scorecard(gates: list[Gate], verdict_ok: bool) -> str:
    lines = [
        "=" * 70,
        "              UVEST AGENT & QUANT EVALUATION SCORECARD",
        "=" * 70,
    ]
    for gate in gates:
        status, display = gate.evaluate()
        lines.append(f"  {status} {gate.name:<38} {display}")
        # Coverage is printed for every status: a PASS over a shrunken graded set
        # is exactly what the line exists to make visible.
        if gate.coverage:
            lines.append(f"         ↳ {gate.coverage}")
        if status in ("[FAIL]", "[SKIP]") and gate.detail:
            lines.append(f"         ↳ {gate.detail}")
    lines += [
        "-" * 70,
        f"  FINAL EVALUATION VERDICT: {'SUITE PASSED' if verdict_ok else 'SUITE NOT PASSED'}",
        "=" * 70,
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# On-demand stage-12 report replay (TICK-010 artifacts are gitignored)
# --------------------------------------------------------------------------
def report_needs_replay(trade: dict, reports_dir: Path,
                        proof: provenance.Proof) -> bool:
    """True when the artifact-graded gates have no proven report to read.

    Either there is no report on disk, or there is one that no run record proves
    (a leftover, an edit, or output from a machine whose ledger is gone): both are
    pending work for the replay, and neither is evidence.
    """
    path = reports_dir / report_path_for(trade).name
    return not path.exists() or not proof.proves(path.name, path, 12)[0]


def ensure_reports(trades: list[dict], reports_dir: Path | None = None,
                   proof: provenance.Proof | None = None) -> tuple[list[Path], list[str]]:
    """Reproduce every stage-12 report the artifact-graded gates cannot stand behind.

    `reports/report_{SYMBOL}_{DATE}.html` is a generated, gitignored artifact, so
    "no report on disk" is pending work — not a reason for the gates to silently
    [SKIP] on a fresh clone. The same goes for a report whose bytes **no run
    record proves**: a leftover from an earlier session, a hand-edited file, or
    output from a machine whose ledger is gone is not evidence either, and the
    fix is the same replay. Both are replayed through the real stage-12 CLI
    (`tests/evals/report_regen.py`), which writes the run record that proves what
    it produced. An existing file is never overwritten: it is replayed into a
    sidecar and byte-compared, so identical bytes become proven and different
    bytes stay unproven and are disclosed by the gates.

    Returns `(written, failures)`. A record with no proof row cannot be replayed
    and stays ungraded (disclosed by the gates); a replay that fails — unreadable
    canonical trade, identity mismatch, stage-12 non-zero exit, missing manifest
    — lands in `failures`, and `main()` fails the run on it instead of grading a
    quietly smaller set.
    """
    reports = reports_dir or (REPO_ROOT / "reports")
    proof = proof if proof is not None else load_proof(reports)
    pending = [t for t in trades if report_needs_replay(t, reports, proof)]
    if not pending:
        return [], []
    import report_regen  # lazy: report_regen imports THIS module at its top
    if not report_regen.MANIFEST_FILE.exists():
        return [], [f"scenario manifest missing — {len(pending)} report(s) "
                    f"cannot be replayed: {report_regen.MANIFEST_FILE}"]
    manifest = json.loads(report_regen.MANIFEST_FILE.read_text(encoding="utf-8"))
    jobs = report_regen.report_jobs({"trades": pending}, manifest)
    return report_regen.regenerate(jobs, reports)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run UVEST behavioral evals (EVAL-001).")
    parser.add_argument("--benchmark", default=str(DEFAULT_BENCHMARK),
                        help="Path to the golden dataset JSON.")
    parser.add_argument("--run-record", action="append", default=[], metavar="PATH",
                        help="Additional run record (uvest-run-record/1) whose rows "
                             "prove stage executions, e.g. a scripts/run_pipeline.py "
                             "chain run. Repeatable. The record beside the graded "
                             "reports is always read.")
    parser.add_argument("--regression-exit", action="store_true",
                        help="exit 1 only on regressions against the frozen "
                             "baseline's `gates` block (plus the non-gate "
                             "correctness failures), instead of on the absolute "
                             "verdict: a banked red stays red on the scorecard "
                             "without failing the run — the pre-commit hook's "
                             "policy (githooks/pre-commit).")
    parser.add_argument("--re-freeze-gates", action="store_true",
                        help="bank this run's gate verdicts/values into "
                             "baseline_scorecard.json's `gates` block — the "
                             "deliberate publication of the current reds, after "
                             "which --regression-exit tolerates exactly them.")
    args = parser.parse_args(argv)

    raw = load_benchmark(Path(args.benchmark))
    # Before materializing: the files the payloads resolve to are the bytes the
    # gates read, and coverage has to compare those, not the resolved values.
    payload_targets = dataset_payload_targets(raw)
    # …and gate 5's scope is read here too, for the same reason: a record's tape
    # span lives only in its `$derive`, which materialize resolves away, and the
    # trained window is the shipped booster's own disclosure of the corpus it saw.
    cone_window = trained_corpus_window()
    cone_calibration = load_calibration()
    cone_spans = {str(t.get("trade_id")): dataset_refs.forward_tape_span(t)
                  for t in raw.get("trades", [])}
    try:
        data, ref_notes, derive_notes = dataset_refs.materialize(raw)
    except dataset_refs.RefError as exc:
        print(f"❌ Benchmark dataset has an unresolvable reference/derivation: {exc}",
              file=sys.stderr)
        sys.exit(2)
    trades = data.get("trades", [])
    option_probes = data.get("option_probes") or []
    refusal_probes = data.get("refusal_probes") or []
    if not trades:
        print("❌ Benchmark dataset contains no trades.", file=sys.stderr)
        sys.exit(2)

    reports_dir = REPO_ROOT / "reports"
    extra_records = [Path(path) for path in args.run_record]
    try:
        proof = load_proof(reports_dir, extra_records)
    except provenance.ProvenanceError as exc:
        print(f"❌ Unreadable run record: {exc}", file=sys.stderr)
        sys.exit(2)

    # Reproduce missing *and* unproven stage-12 artifacts BEFORE grading, so the
    # artifact-graded gates grade only bytes a real run produced, and never skip
    # just because nobody has run report_regen.py on this clone yet.
    pending = [t for t in trades if report_needs_replay(t, reports_dir, proof)]
    regenerated, regen_failures = ensure_reports(trades, proof=proof)
    if pending:
        print(f"  ↻ stage-12 replay: {len(pending)} report(s) missing or unproven — "
              f"{len(regenerated)} written, "
              f"{len(pending) - len(regenerated) - len(regen_failures)} verified by "
              "reproduction, before grading")
    if regen_failures:
        print(f"❌ Stage-12 report regeneration failed for "
              f"{len(regen_failures)} record(s) — the run fails rather than "
              "grade a smaller set:", file=sys.stderr)
        for failure in regen_failures:
            print(f"   ✗ {failure}", file=sys.stderr)

    # The replay just extended the record beside the artifacts: read it back.
    try:
        proof = load_proof(reports_dir, extra_records)
    except provenance.ProvenanceError as exc:
        print(f"❌ Unreadable run record after replay: {exc}", file=sys.stderr)
        sys.exit(2)
    integrity = proof.integrity_problems()
    if integrity:
        print(f"❌ Run record(s) contradict the artifacts on disk "
              f"({len(integrity)}) — a witness that misstates its own output "
              "cannot be graded around:", file=sys.stderr)
        for problem in integrity:
            print(f"   ✗ {problem}", file=sys.stderr)

    unbacked = unbacked_payloads(trades, payload_targets)
    gates = [
        # Gate 1 grades a live replay of the seeded option pastes, not the
        # trade roster: an option never becomes a trade (ADR-001).
        gate_option_rejection(option_probes),
        # Gate 1c grades the other authored stage-0 family the same way — the
        # closed instrument domain (homoglyph option tokens, invented or
        # un-aliased labels) — against its own code, counted beside gate 1
        # rather than inside it (evals.md §2.1).
        gate_refusal_rejection(refusal_probes),
        # ...and the orchestrator's half: one named probe replayed through the
        # whole 0 → 12 chain, so a swallowed exit code cannot ride on gate 1's
        # green rejection rate (evals.md §3 #3).
        gate_chain_abort(option_probes, refusal_probes),
        # The frozen class roster is handed in so the gate can disclose a class
        # the dataset is short of, not only one whose records abstained.
        gate_hard_breakers(trades, unbacked,
                           data.get("meta", {}).get("class_counts_expected")),
        gate_hallucination(trades, proof=proof),
        gate_brier(trades),
        # The graded set is the in-window half of the cone-labeled records; the
        # rest are named on the coverage line (evals.md §3 #5), and every miss
        # carries the percentile of the pool it fell out of.
        gate_cone_coverage(trades, window=cone_window, spans=cone_spans,
                           calibration=cone_calibration),
        gate_mae_reduction(trades),
        gate_html_validity(trades, proof=proof),
    ]
    dist_ok, dist_reason = check_class_distribution(data)
    # The option probes are the one roster outside `trades`, so their own
    # expected count is checked the same way the class quotas are.
    probes_ok, probes_reason = check_option_probe_roster(data)
    refusals_ok, refusals_reason = check_refusal_probe_roster(data)
    # Rule coverage is the other half of the frozen regression reference: the
    # class roster says *which* classes were graded, the census says how many
    # distinct rules they probed, and the baseline says how few is too few.
    class_gate = next(g for g in gates if g.name == "Hard Circuit Breaker Accuracy")
    coverage_floor = load_baseline()
    rules_ok, rules_reason, rule_shortfalls = check_rule_coverage(
        class_gate.census, coverage_floor)
    # The probe rosters get the same regression floor the rule census has:
    # growing one is free, but shrinking one — or losing it entirely — is a
    # named shortfall and a red run, so a smaller graded set can never keep
    # the same green percentage without a deliberate re-freeze.
    roster_seeded = {name: data.get(name) or [] for name in PROBE_ROSTERS}
    rosters_ok, rosters_reason, roster_shortfalls = check_probe_roster_floor(
        roster_seeded, coverage_floor)

    failed = [g.name for g in gates if not g.skipped and g.value is not None
              and ((g.value >= g.threshold) if g.higher_is_pass
                   else (g.value <= g.threshold)) is False]
    # Both halves are computed here because the ledger prints what each one
    # proved; the verdict is their combination, so the lines and the verdict
    # cannot disagree.
    payload_coverage = check_payload_coverage(trades, proof, payload_targets)
    report_coverage = proven_reports(trades, reports_dir, proof)
    coverage_ok, coverage_reason, unproven, payload_excluded = check_evidence_coverage(
        trades, reports_dir, proof, payload_coverage=payload_coverage,
        targets=payload_targets)
    # Everything except the gate verdicts: dataset/probe correctness, evidence
    # provenance, and the floors that are already baseline-compared
    # (rule census, roster sizes/ids). These block in *every* exit mode —
    # only the gate verdicts get the published-red treatment below.
    hard_ok = (dist_ok and probes_ok and refusals_ok and not regen_failures
               and not integrity and coverage_ok and rules_ok and rosters_ok)
    verdict_ok = ((not failed) and hard_ok)

    print(render_scorecard(gates, verdict_ok))
    for line in render_provenance(proof, report_coverage[0], len(trades),
                                  report_coverage[1], integrity,
                                  payload_covered=payload_coverage[0],
                                  payload_unproven=payload_coverage[1],
                                  payload_excluded=payload_excluded):
        print(line)
    print(f"  Evidence completeness: {'OK' if coverage_ok else 'INCOMPLETE'} "
          f"({coverage_reason})")
    if not coverage_ok:
        print("    A gate cannot grade what no run record proves, and this run "
              "refuses to pass on a smaller set:")
        for offender in unproven[:5]:
            print(f"    ✗ {offender}")
        if len(unproven) > 5:
            print(f"    ✗ (+{len(unproven) - 5} more)")
    print(f"  Dataset integrity: {'OK' if dist_ok else 'MISMATCH'} ({dist_reason})")
    print(f"  Option probes: {'OK' if probes_ok else 'MISMATCH'} ({probes_reason})")
    print(f"  Refusal probes: {'OK' if refusals_ok else 'MISMATCH'} ({refusals_reason})")
    print(f"  Rule coverage: {'OK' if rules_ok else 'REGRESSION'} ({rules_reason})")
    for shortfall in rule_shortfalls:
        print(f"    ✗ {shortfall} — more distinct evidence or a deliberate re-freeze "
              "(scaffold_catalog.re_freeze_baseline)")
    print(f"  Probe roster floor: {'OK' if rosters_ok else 'REGRESSION'} "
          f"({rosters_reason})")
    for shortfall in roster_shortfalls:
        print(f"    ✗ {shortfall} — restore the probe(s) or a deliberate re-freeze "
              "(scaffold_catalog.re_freeze_baseline)")
    if ref_notes or derive_notes:
        quarantined = sum(1 for note in ref_notes if note.endswith("(quarantined)"))
        suffix = (f" ({quarantined} quarantined — excluded from grading)"
                  if quarantined else "")
        print(f"  Artifact refs resolved: {len(ref_notes)} artifact ref(s), "
              f"{len(derive_notes)} derived field(s) from tests/proof{suffix}")
        for note in [*ref_notes, *derive_notes]:
            print(f"    ↳ {note}")

    baseline_mode = args.regression_exit or args.re_freeze_gates
    if args.re_freeze_gates:
        banked = bank_gate_values(gates)
        print(f"  Baseline gates banked: {len(banked['gates'])} gate(s) written "
              f"to {BASELINE_FILE.name} — the current scorecard is now the "
              "published reference (--regression-exit tolerates exactly it).")
    if baseline_mode:
        # Compared after a bank on purpose: banking *is* acceptance, so a
        # --re-freeze-gates run walks away green (unless a non-gate
        # correctness half failed). Without the bank, every red is unbanked
        # and blocks — the first run says "publish me" instead of lying.
        regressions, improvements = baseline_gate_regressions(
            gates, load_baseline())
        print(f"  Baseline regression check: "
              f"{'OK' if not regressions else 'REGRESSION'} "
              f"({len(regressions)} regression(s), {len(improvements)} "
              f"improvement(s) vs the frozen bank)")
        for item in regressions:
            print(f"    ✗ {item}")
        for item in improvements:
            print(f"    ▲ {item} — re-bank with run_evals.py --re-freeze-gates")
        if not hard_ok:
            print("    ✗ non-gate failure(s) above (integrity, probes, "
                  "evidence, floors) — these block in every mode")
        return 0 if (hard_ok and not regressions) else 1

    return 0 if verdict_ok else 1


if __name__ == "__main__":
    sys.exit(main())
