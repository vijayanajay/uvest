"""EVAL-001 — Behavioral Evals Benchmark Runner (FROZEN, `docs/agents/evals.md` §4).

Run headlessly:

    python tests/run_evals.py --benchmark tests/evals/golden_dataset.json

Emits the evaluation scorecard to stdout, zero network calls, deterministic:
the JSON file drives the score exactly. When a gate's inputs exist for the graded records,
it is graded. When its inputs are absent (only 5 of 50 trades seeded so far),
the runner prints [SKIP] rather than inventing a number.

Stage-12 reports are gitignored *generated* artifacts, so the two
artifact-graded gates (Zero Hallucination, HTML Validity) first replay any
report they cannot stand behind from its proof root (`ensure_reports` →
`tests/evals/report_regen.py`): a fresh clone grades the same bytes a dev
machine does, a report absent at grading time is disclosed in the gate's
detail (never a silent [SKIP]), and a replay that cannot run fails the run
(exit 1) rather than quietly shrinking the graded set.

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
    1 → at least one gate FAILED, class distribution ≠ expected counts, a
        stage-12 report could not be regenerated before grading, a run record
        contradicts the artifacts on disk, or a record's stage-12 evidence or one
        of the gate payloads it carries has an *unregistered* gap — typed inline
        in the dataset, undeclared by the proof root, or recorded bytes that no
        longer match (the graded set may never silently shrink; registered debt
        is disclosed instead, never fatal),
    2 → usage error / dataset missing / dataset unparseable.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BENCHMARK = REPO_ROOT / "tests" / "evals" / "golden_dataset.json"

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
}

# Which artifact each breaker reads — needed to know whether that breaker's
# evidence is even gradeable (a quarantined artifact carries no evidence).
BREAKER_ARTIFACT: dict[str, str] = {
    "inverted_risk_reward": "out_1_math.json",
    "promoter_pledging_gt_25pct": "out_6_fundamental.json",
    "earnings_within_7d": "out_6_fundamental.json",
    "tighter_than_0_75x_atr": "out_2_market.json",
}

# evals.md §3 pass thresholds — frozen before any implementation code exists.
OPTION_REJ_MIN = 100.0
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

    The class verdict is its arms OR-ed (BRD §10.2 #1 "R:R" trips on either
    price), and `None` (ungradeable) propagates unchanged.
    """
    cls = trade.get("trade_class")
    arms = breaker_arms(trade, breaker or BREAKER_BY_CLASS[cls], unbacked)
    return None if arms is None else any(arms.values())


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
            for arm, source, field, granule, measure in RULE_ARMS[breaker]:
                value = _field(trade, source, measure or field)
                if value is None:
                    continue
                key = arm if arms.get(arm) else f"{arm}{CLEAR_ARM_SUFFIX}"
                found.add((key, _granulated(value, granule)))
        return frozenset(found)
    breaker = BREAKER_BY_CLASS[trade_class]
    arms = breaker_arms(trade, breaker, unbacked)
    if arms is None:
        return frozenset()
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


# --------------------------------------------------------------------------
# Gate 1: Option Rejection Invariance (evals.md §3 #3)
# --------------------------------------------------------------------------
def gate_option_rejection(trades: list[dict]) -> Gate:
    """Options (CE/PE) recommendations must exit 2 (ADR-001 hard prohibition)."""
    g = Gate("Option Rejection Guardrail", OPTION_REJ_MIN, higher_is_pass=True)
    options = [
        t for t in trades
        if (
            t.get("script_outputs", {}).get("current_trade.json", {}).get("instrument_type")
            or t.get("script_outputs", {}).get("current_trade.json", {}).get("type")
            or t.get("instrument")
        ) in ("CE", "PE", "OPT", "OPTION", "CALL", "PUT")
    ]

    if not options:
        g.skipped = True
        g.detail = "no option-instrument trades in the dataset yet"
        return g
    # The dataset has no replay of 0_ingest_reco.py, so no verdict signal exists.
    # Honesty: mark SKIP with an explicit diagnostic rather than invent 100%.
    g.skipped = True
    g.detail = (f"{len(options)} option trade(s) present but no stage-0 replay "
                f"signal to grade rejection against")
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
def gate_cone_coverage(trades: list[dict]) -> Gate:
    g = Gate("Price Cone Range Coverage", COVERAGE_MIN, higher_is_pass=True)
    graded: list[float] = []
    for t in trades:
        cone_p10 = t.get("cone_p10")
        cone_p90 = t.get("cone_p90")
        fwd = t.get("forward_prices_22d") or {}
        lo, hi = fwd.get("min"), fwd.get("max")
        if cone_p10 is None or cone_p90 is None or lo is None or hi is None:
            continue
        graded.append(1.0 if cone_p10 <= lo and hi <= cone_p90 else 0.0)
    g.value = 100.0 * (sum(graded) / len(graded)) if graded else None
    g.skipped = not graded
    g.detail = (f"{len(graded)} cone-labeled trades" if graded
                else "no trades with an engine-produced cone envelope yet — "
                     "stage 4 unimplemented")
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
    flips to `true` ONLY when all 50 records exist AND every class count
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
    args = parser.parse_args(argv)

    raw = load_benchmark(Path(args.benchmark))
    # Before materializing: the files the payloads resolve to are the bytes the
    # gates read, and coverage has to compare those, not the resolved values.
    payload_targets = dataset_payload_targets(raw)
    try:
        data, ref_notes, derive_notes = dataset_refs.materialize(raw)
    except dataset_refs.RefError as exc:
        print(f"❌ Benchmark dataset has an unresolvable reference/derivation: {exc}",
              file=sys.stderr)
        sys.exit(2)
    trades = data.get("trades", [])
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
        gate_option_rejection(trades),
        # The frozen class roster is handed in so the gate can disclose a class
        # the dataset is short of, not only one whose records abstained.
        gate_hard_breakers(trades, unbacked,
                           data.get("meta", {}).get("class_counts_expected")),
        gate_hallucination(trades, proof=proof),
        gate_brier(trades),
        gate_cone_coverage(trades),
        gate_mae_reduction(trades),
        gate_html_validity(trades, proof=proof),
    ]
    dist_ok, dist_reason = check_class_distribution(data)
    # Rule coverage is the other half of the frozen regression reference: the
    # class roster says *which* classes were graded, the census says how many
    # distinct rules they probed, and the baseline says how few is too few.
    class_gate = next(g for g in gates if g.name == "Hard Circuit Breaker Accuracy")
    coverage_floor = load_baseline()
    rules_ok, rules_reason, rule_shortfalls = check_rule_coverage(
        class_gate.census, coverage_floor)

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
    verdict_ok = ((not failed) and dist_ok and not regen_failures and not integrity
                  and coverage_ok and rules_ok)

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
    print(f"  Rule coverage: {'OK' if rules_ok else 'REGRESSION'} ({rules_reason})")
    for shortfall in rule_shortfalls:
        print(f"    ✗ {shortfall} — more distinct evidence or a deliberate re-freeze "
              "(scaffold_catalog.re_freeze_baseline)")
    if ref_notes or derive_notes:
        quarantined = sum(1 for note in ref_notes if note.endswith("(quarantined)"))
        suffix = (f" ({quarantined} quarantined — excluded from grading)"
                  if quarantined else "")
        print(f"  Artifact refs resolved: {len(ref_notes)} artifact ref(s), "
              f"{len(derive_notes)} derived field(s) from tests/proof{suffix}")
        for note in [*ref_notes, *derive_notes]:
            print(f"    ↳ {note}")

    return 0 if verdict_ok else 1


if __name__ == "__main__":
    sys.exit(main())
